#!/usr/bin/env python3
"""Native GoT hot-log performance and exact material-bind rejection audit on c925.

Retain the current PR52/GPU-index matcher unmodified and reduce noisy hot logs, preserving
the existing real 2D/3D/Cube Vulkan image and dynamic buffer descriptor work.
Fail closed on exact source SHA mismatch; never reset, overwrite or kill another
process. Use the existing Ollama unload/restore policy and the old Ninja cache.
"""
from __future__ import annotations

from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback

REPO = "Chreece/shadPS4"
PROJECT = Path.home() / "projects/shadps4"
ROOT = PROJECT / "GhostOfTsushima"
EXPERIMENT = ROOT / "experiments/c925-integrated-gpu-20261027-122701"
SOURCE = EXPERIMENT / "source"
BUILD = EXPERIMENT / "build"
GAME = Path("/mnt/roms-all/ps4-games/CUSA11456/eboot.bin")
BASE_COMMIT = "c925523b49ef39434024b16b2b014d120471ee1a"
CURRENT_ELF_SHA = "ec0cb0306bd954aff8440c3250f00f76731f1817d7139d3cad8c1fc30d580bd6"
PROVEN_ELF_SHA = "be3e8c12cf91117917d355d41a03e227a45cd338981d672de4f0ca2c57b68f28"
PROVEN_ELF = ROOT / "candidates/c925-20261027-154043-descriptor-typed-proven-progress/shadps4"
PREVIOUS_ELF_SHA = "c8a745c602f382a92c5ae4d41f5923b5077a09624a7efc9082c39b053cc6fdbd"
PREVIOUS_ELF = ROOT / "candidates/c925-20261027-180510-gpu-selected-vsharp/shadps4"
OLD_ELF_SAVE = ROOT / "candidates/c925-20261027-190621-new-strided-384-variant/shadps4"
HELPER = PROJECT / "scripts/got-c925-ollama-vram-headroom-20261027.py"
HELPER_GIT_BLOB = "59e2386ac713c1d388a5b97372061cbf981acc0c"
PATCH_REV = "e5df694f7c5edd9704d8e202841610c5717045d8"
PATCH_PATH = "scripts/patchsets/c925-hotlog-bindreject-exact-20261027.patch"
PATCH_BLOB = "2e053af5911eda8618fd46e28b65585321279eae"
TEST_REV = "d14e64be8c1f4692b3dc5c2461f711301cd54460"
TEST_PATH = "tests/c925_hotlog/test_log_occurrence_gate.cpp"
TEST_BLOB = "00da54629c36b9fc346964be4d1c267c3985cb95"
FILES = {
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp":
        ("c599ad215ee9ffc233b4c3ee0d0bec5615d2acb7bb0681b3291e119179880e15",
         "52a3f71010dc69fd9bee56f8aa4d425d93beb646ebeb4af23f71e5260ea523e4"),
    "src/core/libraries/gnmdriver/gnmdriver.cpp":
        ("9c3da5c818b382cd88a864dc7e0f6d65f7295187c277a689bf853f6eff0e7cef",
         "53a897224ec1890b78680994f9f7fedab06ac9b7a04cfc407f0ac77165aa353f"),
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp":
        ("1ad7b1e8e19035f61ac6935a02b13b9511e065c202094ac6a823f5d85633490e",
         "9444beb9716dbd955ef2f5538d24bc08f9ea48620ff8a13d9aaee81587f0a70e"),
    "src/common/log_occurrence_gate.h":
        (None,
         "67efcbb9ac6ea2c3c55cb15ab1842491362e9be480e0ffd368d2b69b549aa992"),
}
OTHER_PROVEN = {
    "src/shader_recompiler/ir/passes/indexed_image_selector.h":
        "5368cf29fcdac40c9273e06e82682c406835dbed234eee7daa5956acd1e0f35c",
    "src/shader_recompiler/ir/passes/resource_discover_pass.cpp":
        "1651b224bf4764babe46beb9fc7452366a9e9a1addd7a7d43997ff7c3eca943a",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":
        "a46aee5980e343fcf37c1962df22a3dced13d193a349e07715e3433154908fba",
    "src/video_core/renderer_vulkan/vk_rasterizer.h":
        "5a99989156ae9310a1812fd5400a0cff0d12f2ed6d618efe98199b6fb5d122ed",
}


def load_verified_helper():
    if not HELPER.is_file() or HELPER.is_symlink():
        raise RuntimeError("Previous verified Ollama/GoT helper absent")
    blob = subprocess.check_output(
        ["git", "hash-object", str(HELPER)], text=True, timeout=15).strip()
    if blob != HELPER_GIT_BLOB:
        raise RuntimeError("Previous Ollama helper content changed; refusing unverified code")
    spec = importlib.util.spec_from_file_location("verified_c925_vram", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not import pinned runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stage_file(m, label: str, revision: str, path: str, blob: str) -> Path:
    dest = m.OUT / label
    url = f"https://raw.githubusercontent.com/{REPO}/{revision}/{path}"
    m.command("fetch-" + label, [
        "curl", "-4", "--http1.1", "-fLsS", "--retry", "3",
        "--connect-timeout", "15", "--max-time", "120", url, "-o", str(dest)],
        timeout=155)
    if m.output("git-blob-" + label, ["git", "hash-object", str(dest)]) != blob:
        raise m.Abort("GIT_BLOB_MISMATCH_" + label, 2)
    return dest


def preflight(m) -> Path:
    m.stage("VERIFY_EXACT_C925_190621_ELF_AND_STRIDED_MATERIAL_SOURCE")
    m.EXP, m.SRC, m.BUILD = EXPERIMENT, SOURCE, BUILD
    if any(p.is_symlink() for p in (ROOT, EXPERIMENT, SOURCE, BUILD, BUILD / "user")):
        raise m.Abort("SYMLINKED_EXPERIMENT_OR_USER_PROFILE", 2)
    if not (BUILD / "build.ninja").is_file() or not (BUILD / "user").is_dir():
        raise m.Abort("EXISTING_NATIVE_C925_BUILD_OR_ISOLATED_USER_MISSING", 2)
    cmake = BUILD / "CMakeCache.txt"
    if not cmake.is_file() or "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(SOURCE) not in cmake.read_text():
        raise m.Abort("CMAKE_CACHE_FROM_OTHER_EMULATOR_SOURCE", 2)
    if m.output("source-commit", ["git", "-C", str(SOURCE), "rev-parse", "HEAD"]) != BASE_COMMIT:
        raise m.Abort("SOURCE_NOT_EXACT_RECOVERED_C925", 2)
    if Path(m.output("source-root", ["git", "-C", str(SOURCE),
                  "rev-parse", "--show-toplevel"])).resolve() != SOURCE.resolve():
        raise m.Abort("SOURCE_WORKTREE_ROOT_MISMATCH", 2)
    if not GAME.is_file() or not m.PROVEN.is_file() or m.sha256(m.PROVEN) != m.MENU_ELF:
        raise m.Abort("PROTECTED_OLD_MENU_OR_GAME_NOT_FOUND", 2)
    for path, digest in ((PROVEN_ELF, PROVEN_ELF_SHA),
                         (PREVIOUS_ELF, PREVIOUS_ELF_SHA)):
        if not path.is_file() or path.is_symlink() or m.sha256(path) != digest:
            raise m.Abort("PRIOR_GO_T_GAMEPLAY_POSITIVE_ELF_MISSING_OR_CHANGED", 2)
    for rel, (original, _) in FILES.items():
        fp = SOURCE / rel
        if original is None:
            if fp.exists() or fp.is_symlink():
                raise m.Abort("NEW_HEADER_ALREADY_EXISTS_" + rel.replace("/", "_"), 2)
            continue
        if not fp.is_file() or fp.is_symlink() or m.sha256(fp) != original:
            raise m.Abort("HOTLOG_SOURCE_PREIMAGE_MISMATCH_" + rel.replace("/", "_"), 2)
    for rel, expected in OTHER_PROVEN.items():
        fp = SOURCE / rel
        if not fp.is_file() or fp.is_symlink() or m.sha256(fp) != expected:
            raise m.Abort("EXISTING_VALIDATED_RENDERER_OR_BUFFER_CHANGED_" +
                          rel.replace("/", "_"), 2)
    elf = BUILD / "shadps4"
    if not elf.is_file() or elf.is_symlink() or m.sha256(elf) != CURRENT_ELF_SHA:
        raise m.Abort("EXACT_LINKED_190621_CANDIDATE_ELF_SHA_MISMATCH", 2)
    if subprocess.run(["pgrep", "-x", "shadps4"], capture_output=True).returncode == 0:
        raise m.Abort("OTHER_SHADPS4_GAME_ALREADY_RUNNING", 2)
    m.output("source-unchanged-diffcheck", ["git", "-C", str(SOURCE), "diff", "--check"])
    m.STATE.update({
        "record_type": "GOT_C925_HOTLOG_PERF_AND_REAL_INDEXED_MATERIAL_BIND_REJECTION",
        "game_id": "CUSA11456",
        "tested_games": [],
        "source_base": BASE_COMMIT,
        "original_candidate_elf": CURRENT_ELF_SHA,
        "protected_binary_overwritten": False,
        "old_and_new_gpu_descriptor_logic_unchanged": True,
        "no_auto_input": True,
        "patch_git_blob": PATCH_BLOB,
    })
    return elf


def preserve_pretest_binary(m, elf: Path) -> None:
    m.stage("PRESERVE_CURRENT_190621_GO_T_NATIVE_ELF_BEFORE_LINK")
    if OLD_ELF_SAVE.exists():
        if (not OLD_ELF_SAVE.is_file() or OLD_ELF_SAVE.is_symlink() or
                m.sha256(OLD_ELF_SAVE) != CURRENT_ELF_SHA):
            raise m.Abort("EXISTING_PROTECTED_190621_ELF_HAS_OTHER_SHA", 2)
    else:
        if OLD_ELF_SAVE.parent.is_symlink():
            raise m.Abort("UNSAFE_PROTECTED_BINARY_DESTINATION", 2)
        OLD_ELF_SAVE.parent.mkdir(parents=True, exist_ok=True)
        temp = OLD_ELF_SAVE.with_name(".190621-protection-in-progress")
        if temp.exists():
            raise m.Abort("PROTECTED_ELF_TEMPORARY_ALREADY_EXISTS", 2)
        shutil.copy2(elf, temp)
        if m.sha256(temp) != CURRENT_ELF_SHA:
            temp.unlink(missing_ok=True)
            raise m.Abort("PROTECTED_ELF_COPY_HASH_MISMATCH", 2)
        temp.replace(OLD_ELF_SAVE)
        OLD_ELF_SAVE.chmod(0o555)
    m.STATE["protected_190621_binary"] = str(OLD_ELF_SAVE)
    print("PROTECTED_190621_STRIDED_GAME_BINARY=PASS", flush=True)


def scratch_patch_roundtrip(m, patch: Path) -> Path:
    m.stage("VERIFY_EXACT_FOUR_FILES_HEADER_PLUS_HOTLOG_PATCH_ROUNDTRIP")
    root = m.OUT / "source-patch-scratch"
    if root.exists():
        raise m.Abort("SCRATCH_PATCH_PATH_ALREADY_EXISTS", 2)
    for rel, (source_sha, _) in FILES.items():
        if source_sha is None:
            continue
        src, dst = SOURCE / rel, root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    m.command("git-init-scratch", ["git", "init", "-q", str(root)], timeout=30)
    m.command("git-apply-check-scratch", ["git", "-C", str(root),
                                         "apply", "--check", str(patch)])
    m.command("git-apply-scratch", ["git", "-C", str(root),
                                   "apply", str(patch)])
    for rel, (_, expected) in FILES.items():
        if m.sha256(root / rel) != expected:
            raise m.Abort("SCRATCH_PATCH_POST_SHA_MISMATCH_" + rel.replace("/", "_"), 2)
    m.command("git-reverse-check-scratch", ["git", "-C", str(root),
                                           "apply", "-R", "--check", str(patch)])
    m.command("git-reverse-scratch", ["git", "-C", str(root),
                                     "apply", "-R", str(patch)])
    for rel, (expected, _) in FILES.items():
        path = root / rel
        if expected is None:
            if path.exists() or path.is_symlink():
                raise m.Abort("REVERSED_NEW_HEADER_STILL_PRESENT", 2)
        elif m.sha256(path) != expected:
            raise m.Abort("SCRATCH_PATCH_REVERSE_SHA_MISMATCH_" + rel.replace("/", "_"), 2)
    m.command("git-reapply-scratch", ["git", "-C", str(root),
                                     "apply", str(patch)])
    m.STATE["source_four_file_roundtrip"] = "PASS"
    print("EXACT_HOTLOG_PATCH_FORWARD_REVERSE=PASS", flush=True)
    return root


def native_matcher_fixture(m, scratch: Path) -> None:
    m.stage("GCC_CLANG_RELEASE_BUILD_TEST_REAL_COMMON_HOTLOG_HEADER_8_THREADS")
    test = stage_file(m, "hotlog-test.cpp", TEST_REV, TEST_PATH, TEST_BLOB)
    for compiler in ("g++", "clang++"):
        if not shutil.which(compiler):
            if compiler == "g++":
                raise m.Abort("GCC_CXX_COMPILER_NOT_AVAILABLE", 2)
            continue
        name = compiler.replace("+", "p")
        exe = m.OUT / ("hotlog-test-" + name)
        m.command("fixture-build-" + name,
                  [compiler, "-std=c++20", "-O2", "-DNDEBUG",
                   "-Wall", "-Wextra", "-Werror", "-pedantic", "-pthread",
                   "-I", str(scratch / "src"),
                   str(test), "-o", str(exe)], timeout=90)
        m.command("fixture-execute-" + name, [str(exe)], timeout=25)
        output = (m.OUT / ("fixture-execute-" + name + ".log")).read_text()
        if "PRODUCTION_LOG_OCCURRENCE_GATE_TEST=PASS" not in output:
            raise m.Abort("REAL_COMMON_HOTLOG_HEADER_TEST_FAILED_" + name, 2)
        print("HOTLOG_500000_SERIAL_PLUS_160000_PARALLEL_PASS=" + compiler,
              flush=True)
    m.STATE["common_hotlog_header_native_release_tests"] = "PASS"


def summarize_runtime(m) -> None:
    path = m.OUT / "game-console.log"
    if not path.is_file():
        return
    raw = path.read_text(errors="replace")
    patterns = ("IndexedStridedSource:", "IndexedBindingDecision:",
                "GuestIndexedImageDimensions:", "INDEXED_MATERIAL_BIND_REJECT",
                "SHARP_RESOURCE_PROVENANCE", "GPU_DYNAMIC_BUFFER_SHARP",
                "SRT dependency resolution stalled",
                "Sharp source was not flatenned", "ErrorDeviceLost",
                "context is lost", "Invalid Vulkan descriptor write",
                "Clamped size from", "Stencil test requires test_val",
                "sceGnmSetWaveLimitMultipliers remains stubbed")
    counts = {pattern: raw.count(pattern) for pattern in patterns}
    select = [line[:1500] for line in raw.splitlines()
              if any(k in line for k in patterns)]
    (m.OUT / "material-binding-and-hotlog-evidence.log").write_text(
        "\n".join(select[:750]) + "\n")
    m.STATE["native_gpu_runtime_counts"] = counts
    m.STATE["material_rejections"] = [
        line[:1200] for line in select if "INDEXED_MATERIAL_BIND_REJECT" in line][:180]
    summary = {
        "counts": counts,
        "rejection_samples": m.STATE["material_rejections"],
        "max_typed_bindings": m.STATE.get("maximum_typed_bindings", {})
    }
    (m.OUT / "gpu-material-binding-rejections.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    print("INDEXED_MATERIAL_BIND_REJECTIONS=" +
          str(counts["INDEXED_MATERIAL_BIND_REJECT"]), flush=True)
    print("SHARP_UNFLATTENED_WARNINGS=" +
          str(counts["Sharp source was not flatenned"]), flush=True)
    print("HOTLOG_RETAINED_WAVELIMIT=" +
          str(counts["sceGnmSetWaveLimitMultipliers remains stubbed"]), flush=True)
    print("HOTLOG_RETAINED_CLAMP=" +
          str(counts["Clamped size from"]), flush=True)
    print("HOTLOG_RETAINED_STENCIL=" +
          str(counts["Stencil test requires test_val"]), flush=True)


def run(m) -> None:
    old_elf = preflight(m)
    preserve_pretest_binary(m, old_elf)
    patch = stage_file(m, "gpu-hotlog-and-bind-rejection.patch",
                       PATCH_REV, PATCH_PATH, PATCH_BLOB)
    scratch = scratch_patch_roundtrip(m, patch)
    native_matcher_fixture(m, scratch)
    m.stage("APPLY_ONLY_LOG_THROTTLING_AND_MATERIAL_BIND_REJECTION_REASON")
    m.command("real-patch-check", ["git", "-C", str(SOURCE), "apply", "--check", str(patch)])
    m.command("real-patch-apply", ["git", "-C", str(SOURCE), "apply", str(patch)])
    for rel, (_, expected) in FILES.items():
        if m.sha256(SOURCE / rel) != expected:
            raise m.Abort("REAL_SOURCE_POST_SHA_MISMATCH_" + rel.replace("/", "_"), 2)
    m.output("real-source-diffcheck", ["git", "-C", str(SOURCE), "diff", "--check"])
    m.STATE["patched_source_sha256"] = {
        rel: m.sha256(SOURCE / rel) for rel in FILES}
    m.stage("INCREMENTAL_NATIVE_CPP_SHADER_COMPILE_AND_FULL_LINK")
    m.command("real-native-hotlog-build",
              ["cmake", "--build", str(BUILD), "--parallel", "8", "--", "-k", "0"],
              timeout=3600, show=True)
    elf = BUILD / "shadps4"
    if not elf.is_file() or not os.access(elf, os.X_OK):
        raise m.Abort("NATIVE_SHADPS4_BINARY_NOT_LINKED", 6)
    m.STATE["new_candidate_sha256"] = m.sha256(elf)
    if m.sha256(OLD_ELF_SAVE) != CURRENT_ELF_SHA or m.sha256(PROVEN_ELF) != PROVEN_ELF_SHA:
        raise m.Abort("PREVIOUS_GO_T_GAMEPLAY_POSITIVE_BINARY_CHANGED", 2)
    m.stage("BUILT_NATIVE_PERF_DIAGNOSTIC_PROTECTED_OLDER_GO_T_EXECUTABLES")
    m.offload_if_needed()
    try:
        m.launch(elf)
    finally:
        summarize_runtime(m)


def main() -> int:
    mod = None
    status = 0
    try:
        mod = load_verified_helper()
        run(mod)
    except BaseException as exc:
        if mod is not None and isinstance(exc, mod.Abort):
            mod.STATE["status"] = exc.status
            status = exc.code
        else:
            status = 9
            if mod is not None:
                mod.STATE["status"] = "UNEXPECTED_HOTLOG_NATIVE_RUNNER_ERROR"
                (mod.OUT / "unexpected-python-error.txt").write_text(traceback.format_exc())
        print("TRIAL_STOP=" + str(exc), file=sys.stderr, flush=True)
    finally:
        if mod is not None:
            try:
                diary = PROJECT / "diary/trials.jsonl"
                diary.parent.mkdir(parents=True, exist_ok=True)
                with diary.open("a") as stream:
                    stream.write(json.dumps(mod.STATE, separators=(",", ":")) + "\n")
            except OSError as error:
                mod.STATE["universal_diary_error"] = str(error)
            mod.finish(status)
    return status


if __name__ == "__main__":
    sys.exit(main())
