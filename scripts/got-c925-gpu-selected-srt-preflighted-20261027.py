#!/usr/bin/env python3
"""One bounded GoT native A/B: preserve genuine GPU-dependent SRT pointers and
translate dynamic guest buffer SHARPs using existing shader GPU-BDA reads.

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
PREVIOUS_DIAGNOSTIC = "8ad5cdbae02b87c5277b45d2a07a6320767c9e792a8fee5cf8613f0dce3f3842"
SRT_CPP = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
SRT_INC = "src/shader_recompiler/ir/passes/srt_dependency_codegen.inc"
VMA_CPP = "src/core/memory.cpp"
VMA_HEADER = "src/core/mapped_guest_range.h"
SRT_BEFORE = "de3b32848cbc2e4a8d0fab46e56c541babc62226c791b55461fd776960f6515a"
SRT_AFTER = "de3b32848cbc2e4a8d0fab46e56c541babc62226c791b55461fd776960f6515a"
INC_BEFORE = "d9f90e49d438740486a13ca9556bce6e3827602d5871ce203ee2fb463c13a41a"
INC_AFTER = "55de6de0f99544fb5a444367eb50a2c99d3e272a835fbb29f93ca4610fd2c8b1"
VMA_BEFORE = "0747c7ce28523164a657d155098187eb6c4318e1238718840de7733e08af71aa"
VMA_AFTER = "0747c7ce28523164a657d155098187eb6c4318e1238718840de7733e08af71aa"
HEADER_SHA = "fb25e8a1f2d1c96e1dd681af03277963f4f6310516d27d7db7a62034a37d81da"
HEADER_BLOB = "3e64ef7f3d7dd79e0040bbaf5483393ebb4581a6"
TEST_BLOB = "2aefed85b8b0e9d685b8f9dbcac6f0cd183e2cbc"
PR76_SOURCE = "da528b540fdbe09e3642570f8d953077adaba691"
SRT_PATCH_REV = "fee9f83e57b0426dd331b3aa459790cd0b09b4c3"
SRT_PATCH_BLOB = "63bc0794791a2f3103953ed7e0dc84010a37a9f3"
DYNAMIC_PATCH_REV = "93fb648f81194b3fdeb478c34f391177318ff976"
DYNAMIC_PATCH_BLOB = "3d54af02957061b4079dc0ed0a842f623be7cd70"
RASTER_CPP = "c599ad215ee9ffc233b4c3ee0d0bec5615d2acb7bb0681b3291e119179880e15"
RASTER_HEADER = "5a99989156ae9310a1812fd5400a0cff0d12f2ed6d618efe98199b6fb5d122ed"
DYNAMIC_TEST_REV = "9d25e085e00d1380d8e56047561ad321ea52a702"
DYNAMIC_TEST_BLOB = "b386528b46668aee66cca3d8a6d5000b45cc0931"
DYNAMIC_PREIMAGES = {
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp":
        "3db63031e40a759c8e597bdb9482b72cbcb3e0417eac73ce5968de4411b70eb1",
    "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp":
        "9d3d080b1ec0e11619ef014cc515df9288336b8494244ab3723fce6cee02cfc6",
    "src/shader_recompiler/backend/spirv/emit_spirv_instructions.h":
        "ca3f077ac1bbfc0732fdf0d7ce808a167aef71d9c860207120ef96de74e28981",
    "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp":
        "4365058c71d28a0e443fe59183845784a5cd12ead3f5b038179129419dc8cee2",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":
        "2c7ccefc0bb57226ef9f48a9435c2b050db028b42c057a073f4b91e38e6ba93d",
}
DYNAMIC_POSTIMAGES = {
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp":
        "051be99a73649647ad9b23f424b45d4b4ff28bf67c5413a109e6964df8b17fe9",
    "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp":
        "df6c50b95c48c1a2c87ed5e9cf500a48a74d26e686924bce1e1d5c7c59b9edee",
    "src/shader_recompiler/backend/spirv/emit_spirv_instructions.h":
        "7bd1902cc737edf7f73bcfadce3cf921049a7ede75c76ca1e3feb4e77ea889bc",
    "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp":
        "9da1eee80e5490d71109c27a4a65c42195e803f88a4965b1d4703fa60f929e47",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":
        "bcaa955d33bbf4f59823e7df73e9274c256ac755ccc5676a5ba4f40c98a4d15f",
}
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
    # Require exactly the source version from the preserved graphics-positive
    # candidate, not an unrelated already-mutated worktree. The pinned patch
    # plus these exact preimages uniquely determine the post-patch bytes.
    original_dynamic_hashes = {
        rel: check_source(m, rel, (DYNAMIC_PREIMAGES[rel],))
        for rel in DYNAMIC_PREIMAGES
    }
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
        "record_type": "GENERIC_GPU_SELECTED_VSHARP_AND_SRT_DYNAMIC_PARENT_FIX",
        "base_commit": BASE, "positive_elf_sha256": PROVEN_PROGRESS,
        "pre_test_elf_sha256": PREVIOUS_DIAGNOSTIC,
        "source_hashes_before": {SRT_CPP: srt, SRT_INC: inc, VMA_CPP: memory, **original_dynamic_hashes},
        "source_changes": "GPU selected scalar V# via shader-BDA and preserve divergent SRT child",
        "automated_game_input": False, "tested_games": [], "proven_binary_overwritten": False,
        "source_rollback": "ONLY_ISOLATED_WORKTREE_MODIFIED; TWO_PINNED_POSITIVE_ELFS_SEPARATE",
        "source_patches": ["SRT GPU-divergence classification", "dynamic V# shader-BDA with cache version"],
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


def verify_patch_in_clean_source_copy(m, patch: Path):
    """Prove a complete clean apply and reverse before any worktree edits."""
    root = m.OUT / "source-patch-preflight"
    if root.exists():
        raise m.Abort("PATCH_PREFLIGHT_DIRECTORY_ALREADY_EXISTS", 2)
    for rel, expected in DYNAMIC_PREIMAGES.items():
        source = m.SRC / rel
        if not source.is_file() or source.is_symlink() or m.sha256(source) != expected:
            raise m.Abort("PATCH_PREFLIGHT_SOURCE_SHA_MISMATCH_" + rel.replace("/", "_"), 2)
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    m.command("patch-preflight-git-init", ["git", "init", "-q", str(root)], timeout=20)
    m.command("patch-preflight-check", ["git", "-C", str(root),
                                        "apply", "--check", str(patch)])
    m.command("patch-preflight-apply", ["git", "-C", str(root),
                                        "apply", str(patch)])
    for rel, expected in DYNAMIC_POSTIMAGES.items():
        dest = root / rel
        if not dest.is_file() or m.sha256(dest) != expected:
            raise m.Abort("PATCH_PREFLIGHT_POST_SHA_MISMATCH_" + rel.replace("/", "_"), 2)
    m.command("patch-preflight-reverse-check", ["git", "-C", str(root),
                                                "apply", "--reverse", "--check", str(patch)])
    m.command("patch-preflight-reverse", ["git", "-C", str(root),
                                          "apply", "--reverse", str(patch)])
    for rel, expected in DYNAMIC_PREIMAGES.items():
        if m.sha256(root / rel) != expected:
            raise m.Abort("PATCH_PREFLIGHT_RESTORATION_MISMATCH", 2)
    m.STATE["exact_clean_patch_roundtrip"] = "PASS"
    print("EXACT_PATCH_FORWARD_REVERSE_VERIFIED=PASS", flush=True)


def preserve_last_prepatch_elf(m):
    """Preserve the exact preceding diagnostics binary separately from protected positives."""
    src = m.BUILD / "shadps4"
    target = m.ROOT / "candidates/c925-20261027-164125-vma-srt-pre-dynamic/shadps4"
    if not src.is_file() or src.is_symlink() or m.sha256(src) != PREVIOUS_DIAGNOSTIC:
        raise m.Abort("PREPATCH_ELF_SHA_MISMATCH", 2)
    if target.exists():
        if not target.is_file() or target.is_symlink() or m.sha256(target) != PREVIOUS_DIAGNOSTIC:
            raise m.Abort("EXISTING_PREPATCH_ELF_BACKUP_NOT_SAME", 2)
    else:
        if target.parent.is_symlink():
            raise m.Abort("BACKUP_FOLDER_SYMLINK_NOT_ALLOWED", 2)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(".pre-dynamic-elf-copying")
        if temp.exists():
            raise m.Abort("PREPATCH_ELF_TEMP_ALREADY_EXISTS", 2)
        shutil.copy2(src, temp)
        if m.sha256(temp) != PREVIOUS_DIAGNOSTIC:
            temp.unlink(missing_ok=True)
            raise m.Abort("PREPATCH_ELF_COPY_HASH_MISMATCH", 2)
        temp.replace(target)
        target.chmod(0o555)
    m.STATE["pre_dynamic_candidate_backup"] = str(target)
    return target


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
    markers = ("GPU_DYNAMIC_BUFFER_SHARP", "SRT_ACTUAL_BLOCKER shader=", "SRT_ACTUAL_BLOCKERS_SUMMARY shader=",
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
    print("GPU_DYNAMIC_BUFFER_SHARP_EVENTS=" + str(log.count("GPU_DYNAMIC_BUFFER_SHARP")), flush=True)
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
    m.stage("FETCH_HASH_PINNED_GPU_SELECTED_BUFFER_AND_SRT_FIXES")
    srt_patch = download(
        m, "srt-preserve-gpu-dynamic.patch", SRT_PATCH_REV,
        "scripts/patchsets/c925-srt-preserve-gpu-divergent-parents-20261027.patch",
        SRT_PATCH_BLOB)
    dynamic_patch = download(
        m, "gpu-selected-vsharp.patch", DYNAMIC_PATCH_REV,
        "scripts/patchsets/c925-gpu-selected-vsharp-build-verified-20261027.patch",
        DYNAMIC_PATCH_BLOB)
    vma_fixture = download(
        m, "mapped-guest-range-test.cpp", PR76_SOURCE,
        "tests/guest_mapped_range/test_guest_mapped_range.cpp", TEST_BLOB)
    dynamic_fixture = download(
        m, "gpu-selected-vsharp-test.cpp", DYNAMIC_TEST_REV,
        "tests/gpu_selected_vsharp_address_test.cpp", DYNAMIC_TEST_BLOB)
    m.stage("PRESERVE_GPU_VARYING_SRT_PARENT_AS_SHADER_SIDE_READ")
    apply_pinned(m, srt_patch, ((SRT_CPP, SRT_AFTER), (SRT_INC, INC_AFTER)))
    m.stage("CLEAN_COPY_PATCH_AND_REVERSE_PRECHECK_BEFORE_SOURCE_MUTATION")
    verify_patch_in_clean_source_copy(m, dynamic_patch)
    preserve_last_prepatch_elf(m)
    m.stage("APPLY_VERIFIED_GPU_SELECTED_BUFFER_SHADER_SOURCE")
    m.command("gpu-dynamic-patch-check", ["git", "-C", str(m.SRC),
                                          "apply", "--check", str(dynamic_patch)])
    m.command("gpu-dynamic-patch-apply", ["git", "-C", str(m.SRC),
                                          "apply", str(dynamic_patch)])
    m.output("gpu-dynamic-patch-diffcheck", ["git", "-C", str(m.SRC),
                                            "diff", "--check"])
    m.STATE["shader_fix_sha256"] = {
        rel: m.sha256(m.SRC / rel) for rel in DYNAMIC_POSTIMAGES
    }
    for rel, expected in DYNAMIC_POSTIMAGES.items():
        if m.STATE["shader_fix_sha256"][rel] != expected:
            raise m.Abort("POST_PATCH_SOURCE_SHA_MISMATCH_" + rel.replace("/", "_"), 2)
    resource = (m.SRC / "src/shader_recompiler/ir/passes/resource_patching_pass.cpp").read_text()
    backend = (m.SRC / "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp").read_text()
    dispatcher = (m.SRC / "src/shader_recompiler/backend/spirv/emit_spirv.cpp").read_text()
    cache = (m.SRC / "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp").read_text()
    if ("GPU_DYNAMIC_BUFFER_SHARP" not in resource or
            "ctx.Def(handle)" not in backend or
            "ctx.EmitDwordMemoryRead(address" not in backend or
            "std::is_same_v<ArgType, const IR::Value&>" not in dispatcher or
            "ShaderBinaryVersion = 0x43520007u" not in cache):
        raise m.Abort("GENERIC_GPU_DYNAMIC_SHADER_ABI_OR_CACHE_GUARD_MISSING", 2)
    fixture_tests(m, vma_fixture)
    m.stage("GCC_CLANG_NATIVE_RELEASE_GPU_SELECTED_VSHARP_100000_TESTS")
    for compiler in ("g++", "clang++"):
        if not shutil.which(compiler):
            if compiler == "g++":
                raise m.Abort("GCC_MISSING_FOR_GPU_SELECTED_VSHARP_TEST", 2)
            continue
        suffix = compiler.replace("+", "p")
        binary = m.OUT / ("gpu-vsharp-" + suffix)
        m.command("build-gpu-vsharp-" + suffix,
                  [compiler, "-std=c++23", "-O2", "-DNDEBUG", "-Wall",
                   "-Wextra", "-Werror", "-pedantic",
                   str(dynamic_fixture), "-o", str(binary)], timeout=90)
        m.command("test-gpu-vsharp-" + suffix, [str(binary)], timeout=25)
        output = (m.OUT / ("test-gpu-vsharp-" + suffix + ".log")).read_text()
        if "GPU_SELECTED_VSHARP_RELEASE_PASS=100000" not in output:
            raise m.Abort("GPU_SELECTED_VSHARP_SOURCE_FIXTURE_FAILED", 2)
        print("GPU_VSHARP_RELEASE_TEST_PASS=" + compiler, flush=True)
    m.stage("INCREMENTAL_FULL_NATIVE_COMPILE_LINK_COLLECT_ALL_FAILURES")
    m.command("native-build-dynamic-srt", [
        "cmake", "--build", str(m.BUILD), "--parallel", "8", "--", "-k", "0"
    ], timeout=3600, show=True)
    elf = m.BUILD / "shadps4"
    if not elf.is_file() or not os.access(elf, os.X_OK):
        raise m.Abort("DYNAMIC_SRT_NATIVE_EXECUTABLE_LINK_FAILED", 6)
    m.STATE["tested_elf_sha256"] = m.sha256(elf)
    (m.OUT / "candidate.txt").write_text(
        "sha256=" + m.STATE["tested_elf_sha256"] + "\nsource=" + str(m.SRC) + "\n")
    positive = m.ROOT / "candidates/c925-20261027-154043-descriptor-typed-proven-progress/shadps4"
    if not positive.is_file() or m.sha256(positive) != PROVEN_PROGRESS:
        raise m.Abort("PROTECTED_GOT_GAMEPLAY_ELF_SHA_MISMATCH_POST_LINK", 2)
    m.stage("REAL_NATIVE_LINK_PASS_BOTH_PROTECTED_MENU_AND_GAMEPLAY_ELFS_UNCHANGED")
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
