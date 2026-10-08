#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Apply the reviewed CPU update to the user's verified graphics source and playtest it."""

import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
DEPENDENCY_REVISION = "251278d933106cb139b9b9df61230e274e60e1d9"
DEPENDENCIES = {
    "affinity_game_profile.py": "211ec6d217af6d552f3b2409e65e1579ac2a4aaa930f568c9701ab8ae0414b9c",
    "affinity_game_guard.py": "f56530b095fc4cbfc8424a23225e6d955f984129cf2bf81f2642b926a9759c88",
    "test_affinity_inheritance.py": "0a4e6e3aafaddd519a37a7f2c6092794a8e78a5bf762df105deab91d1acd6464",
    "working_base_cpu_update.patch": "36167e52c2ba1bd6317fb9be02eaa803f058422dcd15c3e4ba74adb9335a8a3a",
    "working_base_cpu_update.json": "3f463c894b6e3d0f652b2593ac143a46c137c0897c75bef8f15431a4d3770091",
}
RUNTIME_COMMIT = "a522a505582076eb7f68363b5d301ddca44399e2"
RUNTIME_PATCH_SHA = "989208dc8e2432af107b948df37e958082342999fb1c77f7a75ebcb6480abf0b"
DRRUN_SHA = "e58dcb8cdefec91d144c1a8fa79a7bd1b8a29969c4319f31df4df01644ef0b97"
FFMPEG_COMMIT = "94dde08c8a9e4271a93a2a7e4159e9fb05d30c0a"
FFMPEG_URL = "https://github.com/shadps4-emu/ext-ffmpeg-core/releases/download/94dde08/ffmpeg-linux-x64.zip"
FFMPEG_SHA = "aacbbfb8e622b684bc5d3b4cd6c9f9f77f5def64ae8d83c0c5b3ebe657aa33dd"
FFMPEG_SIZE = 15543319


def dependencies():
    base_url = "https://raw.githubusercontent.com/Chreece/shadPS4/" + DEPENDENCY_REVISION + "/scripts/"
    for name, expected in DEPENDENCIES.items():
        path = HERE / name
        if path.exists():
            data = path.read_bytes()
        else:
            with urllib.request.urlopen(base_url + name, timeout=30) as response:
                data = response.read(256 * 1024 + 1)
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("Helper checksum mismatch: " + name)
        if not path.exists():
            path.write_bytes(data)
    sys.path.insert(0, str(HERE))
    return (importlib.import_module("affinity_game_profile"),
            importlib.import_module("affinity_game_guard"),
            importlib.import_module("test_affinity_inheritance"))


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args], stderr=subprocess.STDOUT).decode().strip()


def prepare_source(original, source, metadata, patch, log, command):
    if git(original, "rev-parse", metadata["base_commit"] + "^{tree}") != metadata["base_tree"]:
        raise RuntimeError("Working source differs from the uploaded baseline; preserved")
    if not source.exists():
        command(["git", "clone", "--shared", "--no-checkout", original, source], log)
        command(["git", "checkout", "--detach", metadata["base_commit"]], log, cwd=source)
    current_tree = git(source, "rev-parse", "HEAD^{tree}")
    if git(source, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"):
        raise RuntimeError("Isolated source has unfinished or local changes; retained for inspection")
    if current_tree == metadata["base_tree"]:
        command(["git", "apply", "--check", "--index", patch], log, cwd=source)
        command(["git", "apply", "--index", patch], log, cwd=source)
        if git(source, "write-tree") != metadata["candidate_tree"]:
            raise RuntimeError("Updated source does not match the locally verified tree")
        command(["git", "-c", "user.name=shadPS4 playtest", "-c", "user.email=playtest@localhost",
                 "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-m",
                 "playtest: update affinity and CPU identity on verified graphics source"], log, cwd=source)
    elif current_tree != metadata["candidate_tree"]:
        raise RuntimeError("Cached playtest source has an unexpected tree; preserved")
    changed = git(source, "diff", "--name-only", metadata["base_commit"], "HEAD").splitlines()
    if set(changed) != set(metadata["changed_files"]):
        raise RuntimeError("Unexpected changes outside the reviewed CPU update")


def write_atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def prepare_ffmpeg(source, build_dir, cache, original, evidence, profile):
    dependency = source / "externals/ffmpeg-core"
    if git(dependency, "rev-parse", "HEAD") != FFMPEG_COMMIT:
        raise RuntimeError("FFmpeg source differs from the pinned release")
    short_sha = git(dependency, "rev-parse", "--short", "HEAD")
    if len(short_sha) < 7 or not FFMPEG_COMMIT.startswith(short_sha):
        raise RuntimeError("Unexpected FFmpeg cache name")
    # CMake uses the local Git abbreviation for both its URL and cache name.
    # A reference clone may abbreviate to eight digits, but the release has seven.
    destination = build_dir / "externals" / ("ffmpeg-" + short_sha + ".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    candidates = [destination]
    for root in (cache, original.parent, original):
        candidates.extend(sorted(root.glob("*/externals/ffmpeg-94dde08*.zip")))
    origin = FFMPEG_URL
    data = None
    for candidate in dict.fromkeys(candidates):
        if candidate.is_file() and candidate.stat().st_size == FFMPEG_SIZE:
            cached = candidate.read_bytes()
            if hashlib.sha256(cached).hexdigest() == FFMPEG_SHA:
                data, origin = cached, str(candidate)
                profile.say("FFMPEG=Reusing verified " + origin)
                break
    if data is None:
        profile.say("FFMPEG=Downloading pinned release 94dde08 (15 MB)")
        with urllib.request.urlopen(FFMPEG_URL, timeout=30) as response:
            data = response.read(FFMPEG_SIZE + 1)
        if len(data) != FFMPEG_SIZE or hashlib.sha256(data).hexdigest() != FFMPEG_SHA:
            raise RuntimeError("FFmpeg release checksum mismatch; nothing extracted")
    if origin != str(destination):
        write_atomic(destination, data)
    # Also recover an interrupted extraction: CMake skips extraction whenever
    # the lib directory exists, even if one or more archives are missing.
    libraries = build_dir / "externals" / ("ffmpeg-" + short_sha) / "lib"
    libraries.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination) as archive:
        for name in ("avformat", "avcodec", "swscale", "avutil", "avfilter", "swresample"):
            filename = "lib" + name + ".a"
            contents = archive.read(filename)
            if not contents.startswith(b"!<arch>\n"):
                raise RuntimeError("Invalid FFmpeg static library: " + filename)
            path = libraries / filename
            if not path.is_file() or path.read_bytes() != contents:
                write_atomic(path, contents)
    info = {"commit": FFMPEG_COMMIT, "release": "94dde08", "cache_name": short_sha,
            "sha256": FFMPEG_SHA, "origin": origin, "archive": str(destination)}
    (evidence / "ffmpeg.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


def build(cache, work, evidence, metadata, base, profile):
    original = Path.home() / ".cache/shadps4-esde-latest-pending/source"
    source = cache / ("working-source-" + metadata["candidate_tree"][:12])
    build_dir = cache / ("working-build-" + metadata["candidate_tree"][:12])
    runtime_source = cache / ("runtime-source-" + RUNTIME_COMMIT[:12])
    runtime_build = cache / ("runtime-build-" + RUNTIME_COMMIT[:12])
    drrun = runtime_build / "bin64/drrun"
    runtime_library = runtime_build / "lib64/release/libdynamorio.so"
    if profile.digest(drrun) != DRRUN_SHA or not runtime_library.is_file():
        raise RuntimeError("Verified cached translation runtime is missing or changed; preserved")
    if git(runtime_source, "rev-parse", "HEAD") != RUNTIME_COMMIT:
        raise RuntimeError("Translation source revision changed; preserved")
    runtime_diff = subprocess.check_output(["git", "-C", str(runtime_source), "diff", "HEAD", "--"])
    if hashlib.sha256(runtime_diff).hexdigest() != RUNTIME_PATCH_SHA:
        raise RuntimeError("Translation runtime patch differs from the tested version")
    log = evidence / "build.log"
    cc, cxx = base.compiler(work, log)
    patch = HERE / "working_base_cpu_update.patch"
    prepare_source(original, source, metadata, patch, log, base.command)
    status = git(source, "submodule", "status", "--recursive")
    if any(line[:1] in {"+", "-", "U"} for line in status.splitlines()):
        base.command(["git", "-c", "submodule.alternateErrorStrategy=info", "submodule", "update",
                      "--init", "--recursive", "--jobs", "4", "--reference", original], log, cwd=source)
    else:
        profile.say("SOURCE=Reusing prepared checkout and matching submodules")
    base.command(["git", "diff", "--exit-code", "HEAD", "--"], log, cwd=source)
    status = git(source, "submodule", "status", "--recursive")
    if any(line[:1] in {"+", "-", "U"} for line in status.splitlines()):
        raise RuntimeError("A build dependency differs from the verified graphics source")
    ffmpeg = prepare_ffmpeg(source, build_dir, cache, original, evidence, profile)
    jobs = str(max(1, min(6, len(os.sched_getaffinity(0)))))
    configure = ["cmake", "-S", source, "-B", build_dir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                 "-DENABLE_TESTS=OFF", "-DENABLE_UPDATER=OFF", "-DCMAKE_CXX_SCAN_FOR_MODULES=OFF",
                 "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF", "-DCMAKE_C_COMPILER=" + cc,
                 "-DCMAKE_CXX_COMPILER=" + cxx, "-DENABLE_CPU_ID_TRANSLATION=ON",
                 "-DDynamoRIO_DIR=" + str(runtime_build / "cmake")]
    if shutil.which("ccache"):
        configure += ["-DCMAKE_C_COMPILER_LAUNCHER=ccache", "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache"]
    base.command(configure, log)
    base.command(["cmake", "--build", build_dir, "--target", "shadps4", "shadps4_cpu_id",
                  "--parallel", jobs], log)
    base.command(["git", "diff", "--exit-code", "HEAD", "--"], log, cwd=source)
    binary = build_dir / "shadps4"
    client = build_dir / "src/core/cpu_id_translation/libshadps4_cpu_id.so"
    translated = [str(drrun), "-disable_rseq", "-vm_base", "0x710020000000", "-no_vm_base_near_app",
                  "-c", str(client), "--", str(binary)]
    return binary, translated, {**metadata, "local_commit": git(source, "rev-parse", "HEAD"),
        "source": str(source), "binary": str(binary), "binary_sha256": profile.digest(binary),
        "client_sha256": profile.digest(client), "drrun_sha256": profile.digest(drrun),
        "runtime_library_sha256": profile.digest(runtime_library), "compiler": cxx, "ffmpeg": ffmpeg}


def cancel(signum, frame):
    raise KeyboardInterrupt


def main():
    if len(sys.argv) != 1:
        raise RuntimeError("This pinned runner takes no arguments")
    profile, games, base = dependencies()
    metadata = json.loads((HERE / "working_base_cpu_update.json").read_text())
    home = Path.home()
    cache = home / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="working-cpu-test-", dir=cache))
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-working-cpu-", dir=home))
    summary = {"source_update": metadata, "games": [], "installed_binary_replaced": False,
               "homebrew_repeated": False, "runner_sha256": profile.digest(__file__)}
    lock = (cache / "homebrew.lock").open("a")
    handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if profile.emulators():
            raise RuntimeError("An emulator is running; close it normally before testing")
        installed = home / "Applications/shadps4/shadps4"
        if profile.digest(installed) != metadata["installed_sha256"]:
            raise RuntimeError("Installed baseline changed since your archive; preserved")
        wrapper = home / ".local/bin/shadps4-esde"
        if wrapper.is_symlink():
            raise RuntimeError("ES-DE wrapper is a symlink; left untouched")
        original = wrapper.read_text()
        (evidence / "launcher-before.sh").write_text(original)
        games.hook(original, wrapper, work / "preflight.json", "CUSA18676")
        profile.say("BUILD=Updating CPU code on your verified 89e3c8fa graphics source. Keep games closed until READY.")
        binary, translated, build_info = build(cache, work, evidence, metadata, base, profile)
        summary["build"] = build_info
        profile.say("CANDIDATE_VERIFIED=" + build_info["candidate_tree"])
        profile.say("PLAYTEST=Same match twice: native, then translated. Screenshot each and exit normally.")
        preservation = None
        for mode, prefix in [("native", [str(binary)]), ("translated", translated)]:
            result = games.run_stage("CUSA18676", "PES", mode, binary, prefix, work, evidence,
                                     expected_preservation=preservation)
            summary["games"].append(result)
            games.write_json(evidence / "summary.json", summary)
            if not result["capture_complete"]:
                raise RuntimeError("Capture or preservation check incomplete for " + mode + "; evidence collected")
            preservation = json.loads((evidence / ("CUSA18676-" + mode) / "preservation.json").read_text())
            profile.say("CAPTURED=" + mode + "; emulator return code " + str(result.get("returncode")))
        summary["captures_complete"] = True
        summary["visual_result"] = "Awaiting user observations; process exit does not prove correct rendering"
    except KeyboardInterrupt:
        summary["error"] = "Interrupted; collecting evidence and cleaning up the owned test"
        profile.say("INTERRUPTED=Collecting evidence")
    except Exception as error:
        summary["error"] = str(error)
        profile.say("TEST_ERROR=" + str(error))
        if "build" not in summary and (evidence / "build.log").is_file():
            profile.say("BUILD_LOG_TAIL:\n" + "\n".join(
                (evidence / "build.log").read_text(errors="replace").splitlines()[-25:]))
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            active = False
            cleanup_errors = []
            for path in work.glob("*/job.json"):
                job = json.loads(path.read_text())
                for action in (games.restore, games.stop_owned):
                    try:
                        action(job)
                    except Exception as error:
                        cleanup_errors.append(str(error))
                status_file = path.parent / "status.json"
                status = json.loads(status_file.read_text()) if status_file.exists() else {}
                active |= bool(status.get("bridge") and games.alive(status["bridge"])) or bool(games.owned(job))
            if cleanup_errors:
                summary["cleanup_error"] = cleanup_errors
            summary["stage_results"] = [str(path.relative_to(evidence)) for path in evidence.glob("*/result.json")]
            games.write_json(evidence / "summary.json", summary)
            archive_path = evidence.with_suffix(".tar.gz")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(evidence, arcname=evidence.name)
            if not active and not cleanup_errors:
                shutil.rmtree(work)
            profile.say("UPLOAD_ONLY=" + str(archive_path))
            profile.say("SSH remains open. The installed binary was not replaced; profile checks are in the archive.")
        finally:
            lock.close()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    return 1 if "error" in summary or "cleanup_error" in summary else 0


if __name__ == "__main__":
    raise SystemExit(main())

