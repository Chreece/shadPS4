#!/usr/bin/env python3
"""One bounded GoT native A/B: correct generic VMA range clamping and capture
the exact FOUR stalled SRT read-operand chains, not unrelated unflattened reads.

Only the existing isolated c925 experiment is touched. Protect both earlier
menu and later menu+gameplay positive ELFs, real saves, ES-DE, SSH, GPU config.
No game/hash-specific execution changes, no DynamoRIO, no forced input.
"""
from __future__ import annotations

import collections
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

REPO = "Chreece/shadPS4"
HOME = Path.home()
ORIGINAL_RUNNER = HOME / "projects/shadps4/scripts/got-c925-integrated-native-ebea63b.py"
ORIGINAL_RUNNER_BLOB = "5ec32ffdccd7e2a4dba29b56f8fe8f53343b348b"
BASE = "c925523b49ef39434024b16b2b014d120471ee1a"
PROVEN_PROGRESS = "be3e8c12cf91117917d355d41a03e227a45cd338981d672de4f0ca2c57b68f28"
PREVIOUS_DIAGNOSTIC = "9bdd7be355a5ef030f0fa044934f570c2837d97627bac01664c90e3543124ccd"
SRT_CPP = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
SRT_INC = "src/shader_recompiler/ir/passes/srt_dependency_codegen.inc"
VMA_CPP = "src/core/memory.cpp"
VMA_HEADER = "src/core/mapped_guest_range.h"
SRT_BEFORE = "41f88d8f96d59d2479d4552f92e6593e711af45c0a7e1bdacbbf9b6e35bcd187"
SRT_AFTER = "de3b32848cbc2e4a8d0fab46e56c541babc62226c791b55461fd776960f6515a"
INC_BEFORE = "8fec836128fcf5ffb6b06745aa1fc2b981eebe701d5c6b3555142c85bb37f228"
INC_AFTER = "d9f90e49d438740486a13ca9556bce6e3827602d5871ce203ee2fb463c13a41a"
VMA_BEFORE = "959d720f29924089dfe07933422247efe7a209810f59fb46244580425a02f276"
VMA_AFTER = "0747c7ce28523164a657d155098187eb6c4318e1238718840de7733e08af71aa"
HEADER_SHA = "fb25e8a1f2d1c96e1dd681af03277963f4f6310516d27d7db7a62034a37d81da"
HEADER_BLOB = "3e64ef7f3d7dd79e0040bbaf5483393ebb4581a6"
TEST_BLOB = "2aefed85b8b0e9d685b8f9dbcac6f0cd183e2cbc"
PR76_SOURCE = "da528b540fdbe09e3642570f8d953077adaba691"
SRT_PATCH_REV = "1fe8f50d1cf36c36a00706034feeb129f776504d"
SRT_PATCH_BLOB = "a96026363a49d6167369f82f6903e7f26c91af60"
VMA_PATCH_REV = "a81fda1823ae72069d38c2375517709ba4107e65"
VMA_PATCH_BLOB = "2f117c1c274046e78aa77ab8da324bc07776b6bf"
RASTER_CPP = "c599ad215ee9ffc233b4c3ee0d0bec5615d2acb7bb0681b3291e119179880e15"
RASTER_HEADER = "5a99989156ae9310a1812fd5400a0cff0d12f2ed6d618efe98199b6fb5d122ed"
def pinned_helper():
    if ORIGINAL_RUNNER.is_symlink() or not ORIGINAL_RUNNER.is_file():
        raise RuntimeError("Pinned earlier c925 runner is absent")
    result = subprocess.check_output(
        ["git", "hash-object", str(ORIGINAL_RUNNER)], text=True, timeout=20).strip()
    if result != ORIGINAL_RUNNER_BLOB:
        raise RuntimeError("Earlier runner changed: refuse to run")
    spec = importlib.util.spec_from_file_location("c925_proven_native", ORIGINAL_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot import verified run helpers")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def download(m, name: str, rev: str, path: str, blob: str) -> Path:
    dest = m.OUT / name
    m.command("fetch-" + name, [
        "curl", "-4", "--http1.1", "-fLsS", "--retry", "3",
        "--connect-timeout", "15", "--max-time", "120",
        f"https://raw.githubusercontent.com/{REPO}/{rev}/{path}",
        "-o", str(dest)], timeout=160)
    if m.output("blob-" + name, ["git", "hash-object", str(dest)]) != blob:
        raise m.Abort("REMOTE_SOURCE_GIT_BLOB_MISMATCH_" + name, 2)
    return dest


def check_source(m, rel: str, expected: tuple[str, ...]) -> str:
    path = m.SRC / rel
    if not path.is_file() or path.is_symlink():
        raise m.Abort("SOURCE_NOT_REGULAR_FILE_" + rel.replace("/", "_"), 2)
    actual = m.sha256(path)
    if actual not in expected:
        raise m.Abort("SOURCE_SHA_MISMATCH_" + rel.replace("/", "_"), 2)
    return actual


def preflight(m):
    m.stage("VERIFY_PREVIOUS_NATIVE_PROVEN_GAMEPLAY_ELF_AND_ISOLATED_SOURCE")
    m.EXP = m.ROOT / "experiments/c925-integrated-gpu-20261027-122701"
    m.SRC = m.EXP / "source"
    m.BUILD = m.EXP / "build"
    if any(p.is_symlink() for p in (m.ROOT, m.EXP, m.SRC, m.BUILD)):
        raise m.Abort("SYMLINKED_EXPERIMENT_NOT_ALLOWED", 2)
    if not (m.BUILD / "build.ninja").is_file() or not (m.BUILD / "user").is_dir():
        raise m.Abort("NATIVE_BUILD_CACHE_OR_ISOLATED_SAVE_PROFILE_NOT_FOUND", 2)
    if (m.BUILD / "user").is_symlink():
        raise m.Abort("PORTABLE_PROFILE_IS_SYMLINK", 2)
    cmake = m.BUILD / "CMakeCache.txt"
    if not cmake.is_file() or "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(m.SRC) not in cmake.read_text():
        raise m.Abort("CMAKE_SOURCE_NOT_ISOLATED_WORKTREE", 2)
    if m.output("git-head", ["git", "-C", str(m.SRC), "rev-parse", "HEAD"]) != BASE:
        raise m.Abort("SOURCE_BASE_IS_NOT_C925", 2)
    if Path(m.output("git-root", ["git", "-C", str(m.SRC), "rev-parse", "--show-toplevel"])).resolve() != m.SRC.resolve():
        raise m.Abort("GIT_WORKTREE_ROOT_MISMATCH", 2)
    if not m.PROVEN.is_file() or m.sha256(m.PROVEN) != m.MENU_ELF:
        raise m.Abort("ORIGINAL_POSITIVE_MENU_ELF_CHANGED", 2)
    positive = m.ROOT / "candidates/c925-20261027-154043-descriptor-typed-proven-progress/shadps4"
    if positive.is_symlink() or not positive.is_file() or m.sha256(positive) != PROVEN_PROGRESS:
        raise m.Abort("PROVEN_GAMEPLAY_BINARY_MISSING_OR_SHA_MISMATCH", 2)
    if not m.GAME.is_file():
        raise m.Abort("GOT_GAME_EXECUTABLE_MISSING", 2)
    if subprocess.run(["pgrep", "-x", "shadps4"], capture_output=True).returncode == 0:
        raise m.Abort("OTHER_SHADPS4_RUNNING", 2)
    srt = check_source(m, SRT_CPP, (SRT_BEFORE, SRT_AFTER))
    inc = check_source(m, SRT_INC, (INC_BEFORE, INC_AFTER))
    memory = check_source(m, VMA_CPP, (VMA_BEFORE, VMA_AFTER))
    header = m.SRC / VMA_HEADER
    if header.exists() and (header.is_symlink() or m.sha256(header) != HEADER_SHA):
        raise m.Abort("EXISTING_MAPPED_VMA_HELPER_MISMATCH", 2)
    check_source(m, "src/video_core/renderer_vulkan/vk_rasterizer.cpp", (RASTER_CPP,))
    check_source(m, "src/video_core/renderer_vulkan/vk_rasterizer.h", (RASTER_HEADER,))
    old_elf = m.BUILD / "shadps4"
    if old_elf.is_symlink() or not old_elf.is_file() or m.sha256(old_elf) != PREVIOUS_DIAGNOSTIC:
        raise m.Abort("UNEXPECTED_EXISTING_DIAGNOSTIC_EXECUTABLE", 2)
    m.output("source-diff-check", ["git", "-C", str(m.SRC), "diff", "--check"])
    m.STATE.update({
        "record_type": "GENERIC_VMA_ADJACENCY_FIX_AND_ACTUAL_SRT_BLOCKER_TRACE",
        "base_commit": BASE, "positive_elf_sha256": PROVEN_PROGRESS,
        "pre_test_elf_sha256": PREVIOUS_DIAGNOSTIC,
        "source_hashes_before": {SRT_CPP: srt, SRT_INC: inc, VMA_CPP: memory},
        "source_changes": "one generic VMA span guard and read-only SRT failed-read provenance",
        "automated_game_input": False, "tested_games": [], "proven_binary_overwritten": False,
        "source_rollback": "ONLY_ISOLATED_WORKTREE_MODIFIED; TWO_PINNED_POSITIVE_ELFS_SEPARATE",
        "source_patches": ["generic VMA mapped range", "SRT real deferred-read trace"],
    })


def apply_pinned(m, local_patch: Path, expected: tuple[tuple[str, str], ...]):
    items = [(m.SRC / path, new_hash) for path, new_hash in expected]
    if all(path.is_file() and m.sha256(path) == sha for path, sha in items):
        return
    m.command("patch-check-" + local_patch.stem, [
        "git", "-C", str(m.SRC), "apply", "--check", str(local_patch)])
    m.command("patch-apply-" + local_patch.stem, [
        "git", "-C", str(m.SRC), "apply", str(local_patch)])
    for path, sha in items:
        if m.sha256(path) != sha:
            raise m.Abort("POST_PATCH_SOURCE_HASH_MISMATCH_" + path.name, 2)
    m.output("post-patch-diff-check", ["git", "-C", str(m.SRC), "diff", "--check"])


def fixture_tests(m, original_test: Path):
    m.stage("RUN_REAL_MAPPED_RANGE_HELPER_GCC_CLANG_RELEASE_TESTS")
    for compiler in ("g++", "clang++"):
        if not shutil.which(compiler):
            if compiler == "g++":
                raise m.Abort("GCC_NOT_INSTALLED_FOR_FIXTURE", 2)
            continue
        output = m.OUT / ("vma-range-" + compiler.replace("+", "p"))
        m.command("build-vma-" + compiler.replace("+", "p"), [
            compiler, "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra",
            "-Werror", "-pedantic", "-I", str(m.SRC / "src"),
            str(original_test), "-o", str(output)], timeout=90)
        m.command("test-vma-" + compiler.replace("+", "p"), [str(output)], timeout=20)
        lines = (m.OUT / ("test-vma-" + compiler.replace("+", "p") + ".log")).read_text().splitlines()
        if len(lines) != 10 or any(not line.startswith("VMA_RANGE_PASS=") for line in lines):
            raise m.Abort("VMA_PRODUCTION_HELPER_10_CASES_NOT_VERIFIED", 2)
        print("VMA_GUEST_MAPPED_RANGE_TEST_PASS=" + compiler + " 10/10", flush=True)
    m.tests()


def capture(m, elf: Path):
    m.stage("RUN_GOT_NATIVE_MANUAL_GAMEPLAY_118SEC_AND_EXACT_SRT_FAILURE_CAPTURE")
    env = dict(os.environ)
    if not env.get("DISPLAY"):
        sockets = sorted(Path("/tmp/.X11-unix").glob("X[0-9]*"))
        if sockets:
            env["DISPLAY"] = ":" + sockets[0].name[1:]
    env["XDG_DATA_HOME"] = str(m.EXP / "xdg-data")
    (m.EXP / "xdg-data").mkdir(parents=True, exist_ok=True)
    cmd = ["timeout", "-s", "TERM", "-k", "2s", "118s",
           str(elf), "--game", str(m.GAME), "--fullscreen", "true", "--show-fps"]
    (m.OUT / "game-command.json").write_text(json.dumps(cmd) + "\n")
    with (m.OUT / "game-console.log").open("w") as output:
        proc = subprocess.Popen(cmd, cwd=m.BUILD, env=env, stdout=output,
                                stderr=subprocess.STDOUT)
        m.STATE["tested_games"] = ["CUSA11456"]
        started = time.monotonic()
        for sec in (16, 35, 55, 85, 112):
            while proc.poll() is None and time.monotonic() < started + sec:
                time.sleep(min(0.5, max(0.01, started + sec - time.monotonic())))
            if proc.poll() is not None:
                break
            m.capture_screen(sec, env)
        try:
            code = proc.wait(timeout=14)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                code = proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                code = proc.wait(timeout=5)
    m.STATE["game_exit"] = code
    log = (m.OUT / "game-console.log").read_text(errors="replace")
    markers = ("SRT_ACTUAL_BLOCKER shader=", "SRT_ACTUAL_BLOCKERS_SUMMARY shader=",
               "SRT dependency resolution stalled", "Sharp source was not flatenned",
               "GuestIndexedImageDimensions:", "Clamped size", "Invalid Vulkan descriptor write",
               "ErrorDeviceLost", "context is lost")
    # Filter only useful evidence; retain complete console as compressed file.
    relevant = [line[:1650] for line in log.splitlines()
                if any(marker in line for marker in markers)]
    (m.OUT / "srt-actual-blockers-and-gpu.log").write_text(
        "\n".join(relevant[:650]) + "\n")
    reasons = collections.Counter()
    shaders = collections.Counter()
    for line in relevant:
        if "SRT_ACTUAL_BLOCKER shader=" in line:
            sh = re.search(r"shader=(0x[0-9a-f]+)", line)
            why = re.search(r"reason=([A-Z_]+)", line)
            if sh:
                shaders[sh.group(1)] += 1
            if why:
                reasons[why.group(1)] += 1
    m.STATE["actual_srt_blocker_shader_counts"] = dict(shaders)
    m.STATE["actual_srt_blocker_reasons"] = dict(reasons)
    m.STATE["counts"] = {marker: log.count(marker) for marker in markers}
    m.STATE["screenshots"] = {img.name: m.sha256(img) for img in m.OUT.glob("menu-*.png")}
    m.STATE["positive_gameplay_elf_still_verified"] = (
        m.sha256(m.ROOT / "candidates/c925-20261027-154043-descriptor-typed-proven-progress/shadps4")
        == PROVEN_PROGRESS)
    (m.OUT / "shader-blocker-summary.json").write_text(json.dumps({
        "shaders": m.STATE["actual_srt_blocker_shader_counts"],
        "reasons": m.STATE["actual_srt_blocker_reasons"],
        "counts": m.STATE["counts"]}, indent=2) + "\n")
    print("GAME_EXIT=" + str(code), flush=True)
    print("ACTUAL_SRT_BLOCKER_REASONS=" + json.dumps(dict(reasons)), flush=True)
    print("ACTUAL_SRT_BLOCKER_SHADERS=" + json.dumps(dict(shaders)), flush=True)
    print("SCREENSHOTS=" + str(len(m.STATE["screenshots"])), flush=True)
    if code not in (0, 124):
        raise m.Abort("NATIVE_GPU_CRASH_OR_LOSS_AFTER_VMA_FIX", 10)
    if log.count("SRT dependency resolution stalled") and not sum(shaders.values()):
        raise m.Abort("DEFERRED_SRT_READS_BUT_MISSING_TRUE_TRACE", 10)
    m.stage("NATIVE_VMA_GRAPHICS_COMPARISON_AND_TRUE_SRT_CHAIN_READY_FOR_REVIEW")


def run(m):
    preflight(m)
    m.stage("GET_EXACT_SRT_DEFERRED_ONLY_SOURCE_PATCH")
    srt_patch = download(
        m, "true-deferred-srt.patch", SRT_PATCH_REV,
        "scripts/patchsets/c925-srt-true-deferred-read-chain-20261027.patch",
        SRT_PATCH_BLOB)
    m.stage("GET_VERIFIED_PR76_MAPPED_GUEST_RANGE_AND_TEST")
    vma_patch = download(
        m, "contiguous-guest-vma.patch", VMA_PATCH_REV,
        "scripts/patchsets/c925-pr76-contiguous-vma-20261027.patch",
        VMA_PATCH_BLOB)
    helper = download(m, "mapped_guest_range.h", PR76_SOURCE, VMA_HEADER, HEADER_BLOB)
    if m.sha256(helper) != HEADER_SHA:
        raise m.Abort("MAPPED_RANGE_HELPER_SHA256_MISMATCH", 2)
    fixture = download(
        m, "mapped-guest-range-test.cpp", PR76_SOURCE,
        "tests/guest_mapped_range/test_guest_mapped_range.cpp", TEST_BLOB)
    m.stage("PATCH_ONLY_GOT_EXPERIMENT_SRT_LOGGER_NO_GUEST_SEMANTIC_CHANGE")
    apply_pinned(m, srt_patch, ((SRT_CPP, SRT_AFTER), (SRT_INC, INC_AFTER)))
    m.stage("PATCH_GENERIC_GPU_VMA_CONTIGUITY_FROM_PR76")
    header_path = m.SRC / VMA_HEADER
    if not header_path.exists():
        shutil.copyfile(helper, header_path)
    if m.sha256(header_path) != HEADER_SHA:
        raise m.Abort("EXACT_MAPPED_GUEST_RANGE_HEADER_MISMATCH", 2)
    apply_pinned(m, vma_patch, ((VMA_CPP, VMA_AFTER),))
    fixture_tests(m, fixture)
    m.stage("INCREMENTAL_NATIVE_BUILD_VMA_CPP_AND_SRT_PASS_AND_LINK")
    m.command("native-build-srt-vma", [
        "cmake", "--build", str(m.BUILD), "--parallel", "8", "--", "-k", "0"
    ], timeout=3600, show=True)
    elf = m.BUILD / "shadps4"
    if not elf.is_file() or not os.access(elf, os.X_OK):
        raise m.Abort("COMPILED_CANDIDATE_BINARY_MISSING", 6)
    m.STATE["tested_elf_sha256"] = m.sha256(elf)
    (m.OUT / "candidate.txt").write_text(
        "sha256=" + m.sha256(elf) + "\nsource=" + str(m.SRC) + "\n")
    # Only the isolated candidate executable is linked; baseline paths remain
    # fully separate and hash-checked before the game process starts.
    m.offload_if_needed()
    capture(m, elf)


def main():
    mod = None
    rc = 0
    try:
        mod = pinned_helper()
        run(mod)
    except BaseException as error:
        if mod is not None and isinstance(error, mod.Abort):
            rc = error.code
            mod.STATE["status"] = error.status
        else:
            rc = 9
            if mod is not None:
                mod.STATE["status"] = "NATIVE_SRT_VMA_RUNNER_UNEXPECTED_ERROR"
                (mod.OUT / "unexpected-runner-exception.txt").write_text(traceback.format_exc())
        print("TRIAL_STOP=" + str(error), file=sys.stderr, flush=True)
    finally:
        if mod is not None:
            # Retain every tested or rejected experiment in the single
            # emulator-wide diary, including native compile failures.
            try:
                diary = HOME / "projects/shadps4/diary/trials.jsonl"
                diary.parent.mkdir(parents=True, exist_ok=True)
                with diary.open("a") as file:
                    file.write(json.dumps(mod.STATE, separators=(",", ":")) + "\n")
            except OSError as error:
                mod.STATE["universal_diary_append_error"] = str(error)
            mod.finish(rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
