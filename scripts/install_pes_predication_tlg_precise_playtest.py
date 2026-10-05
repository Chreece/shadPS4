#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build the exact proven playtest baseline with TLG precise readbacks and PES predication."""

import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request

SEED_REVISION = "ab46655439abc75d491e403fe2447ec3d58c4176"
EXPECTED_UPSTREAM = "b5ce9690038bb7b47c075816cd2a9e881fe682cb"
CPU_FIX = "94834eb610f1e92c5a6ae7385f48de417ad984e6"
TLG_FIX = "0bbc84e07fe8ab3ef1e7e4a5e923a5089b888e7e"
PES_PRED_DIAG = "46302d507ba016e7569f517143955bed2009208d"
PES_PRED_FIXES = (
    ("31ec080d969d2fa4da88f3363f2cc873d02d35e6", PES_PRED_DIAG),
    ("62e11daff7560bd34dad63469e63c1ce8a75b3ac", "31ec080d969d2fa4da88f3363f2cc873d02d35e6"),
    ("ffa2c73cc8de9f3907185149c3b94b4506881f1c", "62e11daff7560bd34dad63469e63c1ce8a75b3ac"),
    ("21a8419be517c79d7600abd7cf0b3f5527ea58d2", "ffa2c73cc8de9f3907185149c3b94b4506881f1c"),
    ("0c7299d2c32cfac1fd801e76ffd3ff7d0883e749", "21a8419be517c79d7600abd7cf0b3f5527ea58d2"),
)
TLG_SERIAL = "CUSA03745"
TLG_READBACKS_MODE = 2
RAW = "https://raw.githubusercontent.com/Chreece/shadPS4/"
HELPERS = {
    "pes_refresh_baseline.py": (
        "19153e3113228c22b4f233487043d768469532f3",
        "2d0ba312366bbcbd1ea22b60528e0110de69a857218f1735afb87f894998b49f",
    ),
    "install_local_default.py": (
        "19153e3113228c22b4f233487043d768469532f3",
        "8c7a85d2819775ad853d692ba754f905ae2d0306864de31bef7dd91e81a2288c",
    ),
}
MANIFEST_SHA256 = "0b6c90960c80b220105e52f6558e0e597812f6752381184352520529a098721e"
AUTO_CROSS_MARKER = "SHADPS4_DIAG_AUTO_CROSS_MS"
LOG_MARKER = "# SHADPS4_PLAYTEST_LOG_INLINE_V2"
UNSET_LINE = (
    "unset SHADPS4_NGS2_DIAGNOSTICS SHADPS4_GRAPHICS_DIAGNOSTICS "
    "SHADPS4_NGS2_DIAGNOSTICS_TRIGGER\n"
)
GRAPHICS_EXPORT = "export SHADPS4_GRAPHICS_DIAGNOSTICS=1\n"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_write(path, data, mode):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".new-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
            os.fchmod(out.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def atomic_link(path, target):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".new-", dir=path.parent)
    os.close(fd)
    os.unlink(name)
    temp = Path(name)
    try:
        temp.symlink_to(target)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def require_idle():
    busy = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            exe = (proc / "exe").readlink().name.removesuffix(" (deleted)").lower()
            comm = (proc / "comm").read_text().strip().lower()
            if exe in ("shadps4", "shadps4.exe", "es-de") or comm == "es-de":
                busy.append(proc.name + ":" + exe)
        except (OSError, ValueError):
            continue
    if busy:
        raise RuntimeError("Close shadPS4/ES-DE before install: " + ", ".join(busy))


def download(url, expected, target):
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise RuntimeError("Download unexpectedly large: " + target.name)
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError(
            "Checksum mismatch for " + target.name + "\nexpected=" + expected + "\nactual=" + actual
        )
    target.write_bytes(data)


def prepare_helpers(work):
    helper_dir = work / "helpers"
    helper_dir.mkdir()
    for name, (revision, expected) in HELPERS.items():
        download(RAW + revision + "/scripts/" + name, expected, helper_dir / name)
    manifest_path = helper_dir / "LOCAL_TEST_BASELINE.json"
    download(
        RAW + SEED_REVISION + "/documents/LOCAL_TEST_BASELINE.json",
        MANIFEST_SHA256,
        manifest_path,
    )
    sys.path.insert(0, str(helper_dir))
    return json.loads(manifest_path.read_text())


def git(source, *args):
    env = dict(
        os.environ,
        GIT_TERMINAL_PROMPT="0",
        GIT_EDITOR="true",
        GIT_COMMITTER_NAME="shadPS4 focused playtest",
        GIT_COMMITTER_EMAIL="playtest@localhost",
    )
    command = [
        "git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false",
        "-c", "rerere.enabled=false", *map(str, args)
    ]
    print("SOURCE_STEP=" + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=source, env=env, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise RuntimeError(
            "Source operation failed: " + (result.stderr or result.stdout)[-4000:]
        )
    return result.stdout.strip()


def apply_focused_patches(work, source):
    source_dir = Path(source["source_directory"])
    if source["upstream"] != EXPECTED_UPSTREAM:
        raise RuntimeError(
            "Upstream moved since the captured logs. Refusing to mix revisions: expected "
            + EXPECTED_UPSTREAM + ", got " + source["upstream"]
        )

    for revision, expected_parent in (
        (TLG_FIX, EXPECTED_UPSTREAM),
        (PES_PRED_DIAG, SEED_REVISION),
        *PES_PRED_FIXES,
    ):
        git(source_dir, "fetch", "--depth=2", "--no-tags", "--no-recurse-submodules",
            "origin", revision)
        parent = git(source_dir, "rev-parse", revision + "^")
        if parent != expected_parent:
            raise RuntimeError(
                "Unexpected parent for focused patch " + revision + ": " + parent
            )
        git(
            source_dir,
            "-c", "user.name=shadPS4 focused playtest",
            "-c", "user.email=playtest@localhost",
            "cherry-pick", revision,
        )

    source["revision"] = git(source_dir, "rev-parse", "HEAD")
    source["tree"] = git(source_dir, "rev-parse", "HEAD^{tree}")

    if AUTO_CROSS_MARKER in (source_dir / "src/core/libraries/pad/pad.cpp").read_text(errors="replace"):
        raise RuntimeError("Automatic Cross diagnostic code is present; refusing build")

    translate = (source_dir / "src/shader_recompiler/frontend/translate/translate.cpp").read_text()
    if "ASSERT(base_instance_sgpr == -1);" in translate:
        raise RuntimeError("TLG step-rate assertion still present after focused fix")

    liverpool = (source_dir / "src/video_core/amdgpu/liverpool.cpp").read_text()
    if "EvaluateZpass(" not in liverpool or "predication_execute" not in liverpool:
        raise RuntimeError("PES ZPASS predication implementation missing")

    occlusion = (
        source_dir / "src/video_core/renderer_vulkan/vk_occlusion_query.cpp"
    ).read_text()
    if "OcclusionQuery::EvaluateZpass" not in occlusion:
        raise RuntimeError("PES ZPASS evaluation helper missing")

    pm4 = (source_dir / "src/video_core/amdgpu/pm4_cmds.h").read_text()
    if "struct PM4CmdSetPredication" not in pm4:
        raise RuntimeError("PM4 SET_PREDICATION decoder missing")

    bundle = work / "source-refresh/candidate.bundle"
    bundle.unlink(missing_ok=True)
    git(source_dir, "bundle", "create", bundle, source["source_branch"], "^" + source["upstream"])
    heads = subprocess.check_output(["git", "bundle", "list-heads", str(bundle)], text=True).split()
    if not heads or heads[0] != source["revision"]:
        raise RuntimeError("Candidate bundle does not advertise focused revision")

    patch = git(source_dir, "diff", "--binary", source["upstream"], source["revision"])
    (work / "source-refresh/retained.patch").write_text(patch + "\n")
    (work / "source-refresh/source.json").write_text(json.dumps(source, indent=2) + "\n")

    print("FOCUSED_SOURCE=PASS:" + source["revision"], flush=True)
    print("TLG_FIX=" + TLG_FIX, flush=True)
    print("PES_PRED_DIAG=" + PES_PRED_DIAG, flush=True)
    print("PES_PRED_FIX=" + PES_PRED_FIXES[-1][0], flush=True)
    print("AUTO_CROSS=ABSENT", flush=True)


def select_binary(home, revision, built):
    root = home / "Applications/shadps4"
    releases = root / "releases"
    core = root / "shadps4"
    if root.is_symlink() or releases.is_symlink() or not releases.is_dir() or not core.is_symlink():
        raise RuntimeError("Unexpected shadPS4 installation layout")

    previous = core.resolve(strict=True)
    previous_sha = digest(previous)
    built_sha = digest(built)
    release = releases / ("focused-playtest-" + revision[:12])
    if release.is_symlink() or (release.exists() and not release.is_dir()):
        raise RuntimeError("Unexpected target release path")
    release.mkdir(parents=True, exist_ok=True)
    binary = release / "shadps4"
    if binary.is_symlink():
        raise RuntimeError("Unexpected target binary symlink")
    if binary.exists() and digest(binary) != built_sha:
        raise RuntimeError("Existing target binary differs from verified build")
    if not binary.exists():
        shutil.copy2(built, binary)
        binary.chmod(0o755)
    if digest(binary) != built_sha:
        raise RuntimeError("Copied binary checksum mismatch")

    require_idle()
    atomic_link(core, binary)
    if core.resolve(strict=True) != binary or digest(core) != built_sha:
        atomic_link(core, previous)
        raise RuntimeError("Core switch verification failed; previous core restored")

    return {
        "binary": str(binary),
        "binary_sha256": built_sha,
        "previous_binary": str(previous),
        "previous_sha256": previous_sha,
    }


def configure_tlg_precise_readbacks(home):
    config_dir = home / ".local/share/shadPS4/custom_configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / (TLG_SERIAL + ".json")

    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Unexpected TLG game config path")
        mode = stat.S_IMODE(path.stat().st_mode)
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            raise RuntimeError("Existing TLG game config is invalid JSON") from error
        if not isinstance(data, dict):
            raise RuntimeError("Existing TLG game config root is not an object")
    else:
        mode = 0o644
        data = {}

    gpu = data.setdefault("GPU", {})
    if not isinstance(gpu, dict):
        raise RuntimeError("Existing TLG GPU config is not an object")
    previous_mode = gpu.get("readbacks_mode")
    gpu["readbacks_mode"] = TLG_READBACKS_MODE

    payload = (json.dumps(data, indent=2) + "\n").encode()
    atomic_write(path, payload, mode)

    check = json.loads(path.read_text())
    if check.get("GPU", {}).get("readbacks_mode") != TLG_READBACKS_MODE:
        raise RuntimeError("TLG precise-readbacks override did not verify")

    print("TLG_GAME_CONFIG=" + str(path), flush=True)
    print("TLG_READBACKS_PREVIOUS=" + str(previous_mode), flush=True)
    print("TLG_READBACKS_MODE=PRECISE", flush=True)
    return {
        "path": str(path),
        "previous_mode": previous_mode,
        "readbacks_mode": TLG_READBACKS_MODE,
    }


def repair_graphics_diagnostics_logging(home):
    wrapper = home / ".local/bin/shadps4-esde"
    if not wrapper.is_file() or wrapper.is_symlink():
        raise RuntimeError("Expected regular shadps4-esde wrapper")
    text = wrapper.read_text(errors="strict")
    if LOG_MARKER not in text:
        raise RuntimeError("Expected repaired inline playtest logging wrapper")
    if text.count(UNSET_LINE) != 1:
        raise RuntimeError("Could not identify the launcher diagnostics reset exactly once")

    replacement = UNSET_LINE + GRAPHICS_EXPORT
    if replacement not in text:
        text = text.replace(UNSET_LINE, replacement, 1)
        atomic_write(wrapper, text.encode(), stat.S_IMODE(wrapper.stat().st_mode))

    check = wrapper.read_text(errors="strict")
    if check.count(replacement) != 1 or LOG_MARKER not in check:
        raise RuntimeError("Graphics diagnostics logging repair did not verify")

    print("GRAPHICS_DIAGNOSTICS_AFTER_UNSET=PASS", flush=True)


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError("Run as chreece without sudo or arguments")
    require_idle()
    home = Path.home()
    lock_root = home / ".local/state/shadps4-ngs2"
    lock_root.mkdir(parents=True, exist_ok=True)

    with (lock_root / "deploy.lock").open("a") as deploy_lock:
        fcntl.flock(deploy_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        work = Path(tempfile.mkdtemp(prefix="shadps4-focused-playtest-", dir=home))
        print("WORK=" + str(work), flush=True)
        manifest = prepare_helpers(work)
        refresh = importlib.import_module("pes_refresh_baseline")
        installer = importlib.import_module("install_local_default")

        source = refresh.prepare(work / "source-refresh", SEED_REVISION, manifest)
        apply_focused_patches(work, source)

        built = installer.build(
            home,
            source["revision"],
            source["source_branch"],
            local_source=source["source_directory"],
        )
        require_idle()
        selection = select_binary(home, source["revision"], built)
        repair_graphics_diagnostics_logging(home)
        tlg_config = configure_tlg_precise_readbacks(home)

        state_path = home / ".local/state/shadps4-playtest-logs/build.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "schema": 2,
            "upstream_revision": EXPECTED_UPSTREAM,
            "revision": source["revision"],
            "source_branch": source["source_branch"],
            "binary": selection["binary"],
            "binary_sha256": selection["binary_sha256"],
            "previous_binary": selection["previous_binary"],
            "previous_sha256": selection["previous_sha256"],
            "pending_fixes": source["manifest"]["pending_fixes"],
            "cpu_fix": CPU_FIX,
            "tlg_instance_step_rate_fix": TLG_FIX,
            "pes_predication_diagnostics": PES_PRED_DIAG,
            "pes_zpass_predication_fix": PES_PRED_FIXES[-1][0],
            "tlg_config": tlg_config,
            "auto_cross_absent": True,
            "graphics_diagnostics_after_launcher_unset": True,
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n")

        print("", flush=True)
        print("============================================================", flush=True)
        print(" FOCUSED PES + THE LAST GUARDIAN PLAYTEST READY", flush=True)
        print("============================================================", flush=True)
        print("UPSTREAM=" + EXPECTED_UPSTREAM, flush=True)
        print("PLAYTEST_REVISION=" + source["revision"], flush=True)
        print("BINARY_SHA256=" + selection["binary_sha256"], flush=True)
        print("PENDING_PRS=5228,5229,5230,5232,5234,5235", flush=True)
        print("PES_CPU_FIX=" + CPU_FIX, flush=True)
        print("TLG_STEP_RATE_FIX=" + TLG_FIX, flush=True)
        print("PES_PREDICATION_DIAGNOSTICS=" + PES_PRED_DIAG, flush=True)
        print("PES_ZPASS_PREDICATION_FIX=" + PES_PRED_FIXES[-1][0], flush=True)
        print("TLG_READBACKS_MODE=PRECISE", flush=True)
        print("AUTO_CROSS=ABSENT", flush=True)
        print("GAME_LAUNCHED=NO", flush=True)
        print("GRAPHICS_DIAGNOSTICS=ENABLED_AFTER_LAUNCHER_RESET", flush=True)
        print("RESULT=PASS", flush=True)
        print("", flush=True)
        print("Test the same Last Guardian Continue save first, then one PES match.", flush=True)
        print("Expected TLG log: Game-specific config used: true; GPU readbacksMode: 2", flush=True)
        print("After both tests run: shadps4-pack-playtest-logs", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(
            "RESULT=FAIL: " + type(error).__name__ + ": " + (str(error) or "Interrupted"),
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
    finally:
        print("Returning to your existing SSH prompt.", flush=True)
