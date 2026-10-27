#!/usr/bin/env python3
"""One isolated GoT native run: identify missing image/sampler SHARP sources.

Read-only shader-compiler logging. No changes to rasterization, GPU guest
resource handling, shader semantics, configs, original saves or ES-DE.
The c925 18:05 native candidate and the older menu+gameplay-positive ELF
are separately hash-verified before compiling this diagnostic build.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback

PROJECT = Path.home() / "projects/shadps4"
PINNED_RUNNER = PROJECT / "scripts/got-c925-integrated-native-ebea63b.py"
PINNED_RUNNER_BLOB = "5ec32ffdccd7e2a4dba29b56f8fe8f53343b348b"
BASE = "c925523b49ef39434024b16b2b014d120471ee1a"
PREVIOUS_CANDIDATE = "c8a745c602f382a92c5ae4d41f5923b5077a09624a7efc9082c39b053cc6fdbd"
PROTECTED_GOOD = "be3e8c12cf91117917d355d41a03e227a45cd338981d672de4f0ca2c57b68f28"
PRE_SOURCE = "5cc480c8746d4ba824ea3d3ed8002a65ccd96f4ed2f51983edf284c01c3d5fd6"
POST_SOURCE = "1ad7b1e8e19035f61ac6935a02b13b9511e065c202094ac6a823f5d85633490e"
PATCH_COMMIT = "561bbdd976b8077ea8394271ed988e87b4c4356f"
PATCH_BLOB = "283a7c9d7657282adfb153b090acb0e1f381b562"
PATCH_SHA256 = "948a687d9cb7c5d9ebd0d6873cf63c48e4840e74c351eef97d0375e7a3c29607"
PATCH_FILE = "scripts/patchsets/c925-sharp-source-provenance-after-gpu-vsharp-20261027.patch"
RESOURCE_FILE = "src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
EMITTER_FILE = "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp"
EMITTER_SHA = "e0a37e85a33ea53b70401002cf52d8cc413bb10e04b882214c84128694a15f47"
SRT_FILE = "src/shader_recompiler/ir/passes/srt_dependency_codegen.inc"
SRT_SHA = "55de6de0f99544fb5a444367eb50a2c99d3e272a835fbb29f93ca4610fd2c8b1"
MEMORY_FILE = "src/core/memory.cpp"
MEMORY_SHA = "0747c7ce28523164a657d155098187eb6c4318e1238718840de7733e08af71aa"


def get_native_helper():
    if PINNED_RUNNER.is_symlink() or not PINNED_RUNNER.is_file():
        raise RuntimeError("Pinned verified c925 native runner missing or symlinked")
    blob = subprocess.check_output(
        ["git", "hash-object", str(PINNED_RUNNER)], text=True, timeout=20).strip()
    if blob != PINNED_RUNNER_BLOB:
        raise RuntimeError("Pinned native helper changed; no build started")
    spec = importlib.util.spec_from_file_location("got_c925_proven_helper", PINNED_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot import verified native helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_source(m, relative: str, digest: str) -> Path:
    path = m.SRC / relative
    if not path.is_file() or path.is_symlink() or m.sha256(path) != digest:
        raise m.Abort("EXACT_SOURCE_PREIMAGE_MISMATCH_" + relative.replace("/", "_"), 2)
    return path


def protect_candidate(m) -> None:
    m.stage("PROTECT_18_05_CANDIDATE_AND_PREVIOUS_GAMEPLAY_ELF")
    current = m.BUILD / "shadps4"
    if not current.is_file() or current.is_symlink() or m.sha256(current) != PREVIOUS_CANDIDATE:
        raise m.Abort("EXPECTED_18_05_EXECUTABLE_NOT_PRESENT", 2)
    previous = m.ROOT / "candidates/c925-20261027-154043-descriptor-typed-proven-progress/shadps4"
    if not previous.is_file() or previous.is_symlink() or m.sha256(previous) != PROTECTED_GOOD:
        raise m.Abort("PREVIOUS_MENU_AND_GAMEPLAY_ELF_SHA_MISMATCH", 2)
    if not m.PROVEN.is_file() or m.sha256(m.PROVEN) != m.MENU_ELF:
        raise m.Abort("ORIGINAL_MENU_CAPABLE_ELF_SHA_MISMATCH", 2)
    target = m.ROOT / "candidates/c925-20261027-180510-gpu-selected-vsharp/shadps4"
    if target.exists():
        if not target.is_file() or target.is_symlink() or m.sha256(target) != PREVIOUS_CANDIDATE:
            raise m.Abort("PREVIOUS_CANDIDATE_BACKUP_WRONG_SHA", 2)
    else:
        if target.parent.is_symlink():
            raise m.Abort("CANDIDATE_BACKUP_DIRECTORY_IS_SYMLINK", 2)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(".shadps4-180510-copying")
        if tmp.exists():
            raise m.Abort("PREVIOUS_CANDIDATE_TEMP_BACKUP_EXISTS", 2)
        shutil.copy2(current, tmp)
        if m.sha256(tmp) != PREVIOUS_CANDIDATE:
            tmp.unlink(missing_ok=True)
            raise m.Abort("PREVIOUS_CANDIDATE_COPY_INTEGRITY_FAILED", 2)
        tmp.replace(target)
        target.chmod(0o555)
    m.STATE["previous_candidate_protected"] = str(target)
    m.STATE["previous_candidate_sha256"] = PREVIOUS_CANDIDATE
    m.STATE["proven_gameplay_sha256"] = PROTECTED_GOOD
    print("PROTECTED_1805_CANDIDATE=" + str(target), flush=True)


def preflight(m) -> Path:
    m.stage("VERIFY_EXACT_CURRENT_C925_SHADER_SOURCE_NO_GRAPHICS_CONFIG_CHANGES")
    m.EXP = m.ROOT / "experiments/c925-integrated-gpu-20261027-122701"
    m.SRC = m.EXP / "source"
    m.BUILD = m.EXP / "build"
    if any(x.is_symlink() for x in (m.ROOT, m.EXP, m.SRC, m.BUILD, m.BUILD / "user")):
        raise m.Abort("SYMLINKED_NATIVE_EXPERIMENT_OR_SAVE_PROFILE", 2)
    if not (m.BUILD / "build.ninja").is_file() or not (m.BUILD / "user").is_dir():
        raise m.Abort("EXPECTED_NATIVE_BUILD_AND_PORTABLE_PROFILE_NOT_FOUND", 2)
    cache = m.BUILD / "CMakeCache.txt"
    if not cache.is_file() or "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(m.SRC) not in cache.read_text():
        raise m.Abort("NATIVE_CMAKE_BUILD_TREE_IS_NOT_EXACT_C925", 2)
    if m.output("commit", ["git", "-C", str(m.SRC), "rev-parse", "HEAD"]) != BASE:
        raise m.Abort("SOURCE_HEAD_NOT_EXACT_C925", 2)
    if Path(m.output("source-root", ["git", "-C", str(m.SRC), "rev-parse", "--show-toplevel"])).resolve() != m.SRC.resolve():
        raise m.Abort("GIT_WORKTREE_ROOT_MISMATCH", 2)
    if not m.GAME.is_file():
        raise m.Abort("ORIGINAL_GOT_EBOOT_NOT_FOUND", 2)
    if subprocess.run(["pgrep", "-x", "shadps4"], capture_output=True).returncode == 0:
        raise m.Abort("OTHER_GAME_EMULATOR_ALREADY_RUNNING", 2)
    original = validate_source(m, RESOURCE_FILE, PRE_SOURCE)
    for name, digest in ((EMITTER_FILE, EMITTER_SHA), (SRT_FILE, SRT_SHA),
                         (MEMORY_FILE, MEMORY_SHA)):
        validate_source(m, name, digest)
    protect_candidate(m)
    m.output("original-source-diff-check", ["git", "-C", str(m.SRC), "diff", "--check"])
    m.STATE.update({
        "record_type": "SHARP_REAL_IMAGE_SAMPLER_SOURCE_PROVENANCE",
        "tested_games": [],
        "source_base": BASE,
        "render_semantics_modified": False,
        "old_source_sha256": PRE_SOURCE,
        "new_source_sha256": POST_SOURCE,
        "patch_git_blob": PATCH_BLOB,
        "no_auto_input": True
    })
    return original


def download_patch(m) -> Path:
    m.stage("FETCH_EXACT_GIT_VERIFIED_SHADER_PROVENANCE_PATCH")
    patch = m.OUT / "sharp-resource-provenance.patch"
    url = ("https://raw.githubusercontent.com/Chreece/shadPS4/" +
           PATCH_COMMIT + "/" + PATCH_FILE)
    m.command("download-sharp-patch", [
        "curl", "-4", "--http1.1", "-fLsS", "--retry", "3", "--connect-timeout", "15",
        "--max-time", "120", url, "-o", str(patch)
    ], timeout=160)
    if m.output("patch-blob", ["git", "hash-object", str(patch)]) != PATCH_BLOB:
        raise m.Abort("RESOURCE_PROVENANCE_GIT_BLOB_MISMATCH", 2)
    if m.sha256(patch) != PATCH_SHA256:
        raise m.Abort("RESOURCE_PROVENANCE_SHA256_MISMATCH", 2)
    return patch


def verify_clean_patch_roundtrip(m, patch: Path, original: Path) -> None:
    m.stage("TEST_PATCH_FORWARD_AND_REVERSE_ON_DISPOSABLE_NATIVE_SOURCE_COPY")
    scratch = m.OUT / "checked-source"
    relative = scratch / RESOURCE_FILE
    if scratch.exists():
        raise m.Abort("DISPOSABLE_PATCH_CHECK_DIRECTORY_ALREADY_EXISTS", 2)
    relative.parent.mkdir(parents=True)
    shutil.copyfile(original, relative)
    m.command("scratch-git-init", ["git", "init", "-q", str(scratch)], timeout=20)
    m.command("scratch-git-apply-check", [
        "git", "-C", str(scratch), "apply", "--check", str(patch)])
    m.command("scratch-git-apply", ["git", "-C", str(scratch), "apply", str(patch)])
    if m.sha256(relative) != POST_SOURCE:
        raise m.Abort("SCRATCH_PATCH_POSTIMAGE_SHA256_MISMATCH", 2)
    m.command("scratch-reverse-check", [
        "git", "-C", str(scratch), "apply", "-R", "--check", str(patch)])
    m.command("scratch-reverse", [
        "git", "-C", str(scratch), "apply", "-R", str(patch)])
    if m.sha256(relative) != PRE_SOURCE:
        raise m.Abort("SCRATCH_SOURCE_NOT_RESTORED_EXACTLY", 2)
    m.STATE["source_patch_roundtrip"] = "PASS"
    print("SHARP_PROVENANCE_SOURCE_ROUNDTRIP=PASS", flush=True)


def record_shader_evidence(m) -> None:
    logfile = m.OUT / "game-console.log"
    if not logfile.is_file():
        return
    raw = logfile.read_text(errors="replace")
    counts = Counter()
    shader_roles = Counter()
    dynamic = Counter()
    interesting = []
    for line in raw.splitlines():
        if "SHARP_RESOURCE_PROVENANCE shader=" in line:
            match = re.search(r"shader=(0x[0-9a-f]+).*?role=(image|sampler).*?indexed=(true|false)", line)
            if match:
                shader_roles[(match.group(1), match.group(2), match.group(3))] += 1
            counts["source_events"] += 1
            if len(interesting) < 450:
                interesting.append(line[:1700])
        if "GPU_DYNAMIC_BUFFER_SHARP shader=" in line:
            match = re.search(r"GPU_DYNAMIC_BUFFER_SHARP shader=(0x[0-9a-f]+)", line)
            if match:
                dynamic[match.group(1)] += 1
            counts["dynamic_buffer_events"] += 1
        if "SRT dependency resolution stalled" in line:
            counts["srt_stalls"] += 1
        if "Sharp source was not flatenned" in line:
            counts["sharp_warnings"] += 1
        if "ErrorDeviceLost" in line or "context is lost" in line:
            counts["device_loss_mentions"] += 1
    formatted_roles = [
        {"shader": h, "role": role, "indexed": indexed, "count": n}
        for (h, role, indexed), n in shader_roles.most_common()
    ]
    summary = {"counts": dict(counts), "dynamic_shaders": dict(dynamic),
               "unresolved_resources": formatted_roles,
               "game_exit": m.STATE.get("game_exit"),
               "max_typed_bindings": m.STATE.get("maximum_typed_bindings", {})}
    m.STATE["sharp_provenance_summary"] = summary
    (m.OUT / "sharp-provenance-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (m.OUT / "sharp-provenance-selected.log").write_text("\n".join(interesting) + "\n")
    print("GPU_DYNAMIC_VSHARP_SHADERS=" + json.dumps(dict(dynamic)), flush=True)
    print("SHARP_IMAGE_SAMPLER_SOURCE_EVENTS=" + str(counts["source_events"]), flush=True)
    print("SRT_STALLS=" + str(counts["srt_stalls"]), flush=True)


def run(m) -> None:
    original = preflight(m)
    patch = download_patch(m)
    verify_clean_patch_roundtrip(m, patch, original)
    m.stage("APPLY_LOGGING_ONLY_TO_EXISTING_ISOLATED_SHADER_SOURCE")
    m.command("actual-patch-check", [
        "git", "-C", str(m.SRC), "apply", "--check", str(patch)])
    m.command("actual-patch-apply", [
        "git", "-C", str(m.SRC), "apply", str(patch)])
    if m.sha256(original) != POST_SOURCE:
        raise m.Abort("PRODUCTION_SOURCE_AFTER_PATCH_SHA256_MISMATCH", 2)
    m.output("patched-diff-check", ["git", "-C", str(m.SRC), "diff", "--check"])
    m.tests()
    m.stage("INCREMENTAL_NATIVE_CPP_SHADER_SOURCE_AND_LINK")
    m.command("native-sharp-provenance-build", [
        "cmake", "--build", str(m.BUILD), "--parallel", "8", "--", "-k", "0"
    ], timeout=3600, show=True)
    elf = m.BUILD / "shadps4"
    if not elf.is_file() or not os.access(elf, os.X_OK):
        raise m.Abort("NATIVE_LOGGING_ONLY_CANDIDATE_LINK_NOT_FOUND", 6)
    m.STATE["candidate_sha256"] = m.sha256(elf)
    current = m.ROOT / "candidates/c925-20261027-180510-gpu-selected-vsharp/shadps4"
    if not current.is_file() or m.sha256(current) != PREVIOUS_CANDIDATE:
        raise m.Abort("PROTECTED_1805_BINARY_CHANGED_AFTER_NATIVE_BUILD", 2)
    m.stage("NATIVE_LINK_PASS_RUN_MANUAL_GOT_MAX_118SEC")
    m.offload_if_needed()
    try:
        m.launch(elf)
    finally:
        record_shader_evidence(m)


def main() -> int:
    mod = None
    result = 0
    try:
        mod = get_native_helper()
        run(mod)
    except BaseException as exc:
        if mod is not None and isinstance(exc, mod.Abort):
            mod.STATE["status"] = exc.status
            result = exc.code
        else:
            result = 9
            if mod is not None:
                mod.STATE["status"] = "SHARP_PROVENANCE_RUNNER_UNEXPECTED_EXCEPTION"
                (mod.OUT / "unexpected-runner-exception.txt").write_text(traceback.format_exc())
        print("TRIAL_STOP=" + str(exc), file=sys.stderr, flush=True)
    finally:
        if mod is not None:
            try:
                diary = PROJECT / "diary/trials.jsonl"
                diary.parent.mkdir(parents=True, exist_ok=True)
                with diary.open("a") as stream:
                    stream.write(json.dumps(mod.STATE, separators=(",", ":")) + "\n")
            except OSError as error:
                mod.STATE["global_diary_error"] = str(error)
            mod.finish(result)
    return result


if __name__ == "__main__":
    sys.exit(main())
