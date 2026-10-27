#!/usr/bin/env python3
"""Native resume of generic Vulkan descriptor-write correctness on recovered c925.

Only modifies the isolated 2026-10-27 12:27 GoT experiment source/build.
No PS4 title/shader-ID exceptions, no DynamoRIO, no CPU-ID overrides.
Never touches the protected old menu-capable executable, installed launcher,
original shadPS4 user profile/saves, system graphics, X11 or Sunshine.
"""
from __future__ import annotations

import gzip
import hashlib
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

HOME = Path.home()
PROJECT = HOME / "projects/shadps4"
ORIGINAL_RUNNER = PROJECT / "scripts/got-c925-integrated-native-ebea63b.py"
ORIGINAL_RUNNER_BLOB = "5ec32ffdccd7e2a4dba29b56f8fe8f53343b348b"
PATCH_REV = "75d6d53890cc380fd28263faa55f4b898816bb43"
PATCH_REL = "scripts/patchsets/c925-generic-descriptor-write-exactness-20261027.patch"
PATCH_BLOB = "073bfd9ccd8c2dd6cd9d382f6c5d5dadc9d9a9f1"
TEST_REV = "6bb2cfb7e249130aaea1d47de90c142fcbffc73a"
TEST_REL = "tests/descriptor_write_exactness_release_test.cpp"
TEST_BLOB = "c3e708217080e904280d7a7d69d2cee8a10b0ae4"
C925 = "c925523b49ef39434024b16b2b014d120471ee1a"
CANDIDATE_BEFORE = "ed9cda903dd3094a7ac0c0edb845fa86e7eeb56480c7b4a0444540ecbeeb4022"
RASTERIZER_BEFORE = "2977a375627d9db49cd453cf40df78800a1de4d22e84753f2600a07873a290c4"
HEADER_BEFORE = "f003fa81204d9d766f4ea8bc7de88bbaf15286dbc27dfccaf4d3077b1eb0dd94"
RASTERIZER_AFTER = "c599ad215ee9ffc233b4c3ee0d0bec5615d2acb7bb0681b3291e119179880e15"
HEADER_AFTER = "5a99989156ae9310a1812fd5400a0cff0d12f2ed6d618efe98199b6fb5d122ed"
INSTANCE_HEADER_SHA = "fd5e730ab62b37062d7abdcd52f6c897e65f9f33893ea480490008f1875e8a33"
OTHER_PINNED_FILES = {
    "src/shader_recompiler/backend/spirv/emit_spirv_image.cpp": "7895466b9021f48aad7cc8ab4475decda14b7f1a3c213e7bd3ddb2562a3facab",
    "src/shader_recompiler/backend/spirv/spirv_emit_context.cpp": "3f7548e5d8eb995122779b4f45661c8cf2f27865db46016a32b80c5f3de358bc",
    "src/shader_recompiler/backend/spirv/spirv_emit_context.h": "981d5aef1dfff48468aee76e4d58af8055a386dab0be684b4712b2784a4a5404",
    "src/shader_recompiler/compact_image_indices.h": "1f8288c4e4d548b4a0d842862a2d3c3c10abc8fa9c2c26ecf31dffefe44457f0",
    "src/shader_recompiler/guest_descriptor_coherence.h": "7c7ccc5e5095ecb0fbca33bd5505be1a58122c08286b22837b345b601a1ac521",
    "src/shader_recompiler/ir/passes/resource_discover_pass.cpp": "b1db2e1fe70c402301fe6ab23c7ab52fbe51185f0eac4ea15752b45783f8f1bb",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp": "3db63031e40a759c8e597bdb9482b72cbcb3e0417eac73ce5968de4411b70eb1",
    "src/shader_recompiler/profile.h": "71dba84eb64bc6cee8b09f256fda251524c57b2cf3a0d5798365715ee8359b23",
    "src/shader_recompiler/resource.h": "cbc0e4b0ce9f067c9972d11893c2109a158cfbfc9f8fe31c93f85e460f49f3ee",
    "src/video_core/renderer_vulkan/vk_compute_pipeline.cpp": "963d04ce367bf9e7a7c403264b7fa8b70f6727b72bf1a41a3ca2f4737d22e936",
    "src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp": "14621783c47220fb0e052a59d3e94434213eaca4987456052970aef9a9135ae2",
    "src/video_core/renderer_vulkan/vk_instance.cpp": "b26369863680ab25b8df235271d6fe3da613c32ecb8e41e0bb7850f61a3071c9",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp": "7c40b40f0b1389f1fee78746ee6cc0f9e44a9c542fe3d69c9aa99a9225e6b6b4",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp": "2c7ccefc0bb57226ef9f48a9435c2b050db028b42c057a073f4b91e38e6ba93d",
    "tests/compact_image_indices_test.cpp": "0d0c5230064b5114909f830755596ae97e67caa4188c4a092204e7ae2fa7cd61",
    "tests/guest_descriptor_coherence_test.cpp": "a5350f0d411ba1ead1caf1affa2d0e5d44a8f3e5d942e8e3ed0638bed1f3ebb9",
}


def import_runner():
    if not ORIGINAL_RUNNER.is_file() or ORIGINAL_RUNNER.is_symlink():
        raise RuntimeError("Original pinned 12:27 runner unavailable/symlinked")
    blob = subprocess.check_output(
        ["git", "hash-object", str(ORIGINAL_RUNNER)], text=True, timeout=10
    ).strip()
    if blob != ORIGINAL_RUNNER_BLOB:
        raise RuntimeError("Pinned original runner blob changed; refusing to continue")
    spec = importlib.util.spec_from_file_location("c925_native_runner", ORIGINAL_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot import pinned original runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verified_download(m, name: str, rev: str, rel: str, expected: str) -> Path:
    path = m.OUT / name
    url = "https://raw.githubusercontent.com/Chreece/shadPS4/" + rev + "/" + rel
    m.command("download-" + name, [
        "curl", "-4", "--http1.1", "-fLsS", "--retry", "3",
        "--connect-timeout", "15", "--max-time", "120",
        url, "-o", str(path)
    ], timeout=150)
    if m.output("gitblob-" + name, ["git", "hash-object", str(path)]) != expected:
        raise m.Abort("PINNED_GIT_BLOB_MISMATCH_" + name, 2)
    return path


def preflight(m):
    m.stage("VERIFY_EXACT_PREVIOUS_NATIVE_C925_ELF_AND_SOURCE")
    root = m.ROOT / "experiments/c925-integrated-gpu-20261027-122701"
    m.EXP, m.SRC, m.BUILD = root, root / "source", root / "build"
    if any(p.is_symlink() for p in (root, m.SRC, m.BUILD)):
        raise m.Abort("ISOLATED_WORKSPACE_IS_SYMLINKED", 2)
    if not (m.BUILD / "build.ninja").is_file() or not (m.BUILD / "user").is_dir():
        raise m.Abort("ORIGINAL_ISOLATED_NATIVE_BUILD_OR_PROFILE_MISSING", 2)
    if m.output("source-commit", ["git", "-C", str(m.SRC), "rev-parse", "HEAD"]) != C925:
        raise m.Abort("EXACT_C925_SOURCE_COMMIT_MISMATCH", 2)
    repo_root = Path(m.output(
        "source-root", ["git", "-C", str(m.SRC), "rev-parse", "--show-toplevel"]))
    if repo_root.resolve() != m.SRC.resolve():
        raise m.Abort("EXPERIMENT_WORKTREE_ROOT_CHANGED", 2)
    cache = m.BUILD / "CMakeCache.txt"
    if (not cache.is_file() or
            "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(m.SRC) not in cache.read_text()):
        raise m.Abort("CMAKE_CACHE_NOT_FROM_THIS_WORKTREE", 2)
    if not m.PROVEN.is_file() or m.sha256(m.PROVEN) != m.MENU_ELF:
        raise m.Abort("PROTECTED_MENU_POSITIVE_ELF_MISSING_OR_CHANGED", 2)
    if not m.GAME.is_file():
        raise m.Abort("GAME_EBOOT_MISSING", 2)
    for rel, expected_sha in OTHER_PINNED_FILES.items():
        source_file = m.SRC / rel
        if (not source_file.is_file() or source_file.is_symlink() or
                m.sha256(source_file) != expected_sha):
            raise m.Abort("UNEXPECTED_OTHER_SOURCE_" + rel.replace("/", "_"), 2)
    if m.sha256(m.SRC / "src/video_core/renderer_vulkan/vk_instance.h") != INSTANCE_HEADER_SHA:
        raise m.Abort("PREVIOUS_FIXED_VULKAN_INSTANCE_HEADER_CHANGED", 2)
    raster = m.SRC / "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
    header = m.SRC / "src/video_core/renderer_vulkan/vk_rasterizer.h"
    if raster.is_symlink() or header.is_symlink():
        raise m.Abort("RASTERIZER_SOURCE_IS_SYMLINKED", 2)
    current = (m.sha256(raster), m.sha256(header))
    before = (RASTERIZER_BEFORE, HEADER_BEFORE)
    after = (RASTERIZER_AFTER, HEADER_AFTER)
    if current not in (before, after):
        raise m.Abort("EXPERIMENT_RENDERER_SOURCE_DIVERGED_STOP", 2)
    elf = m.BUILD / "shadps4"
    if not elf.is_file() or elf.is_symlink():
        raise m.Abort("PREVIOUS_FAILED_EXPERIMENT_ELF_MISSING", 2)
    if current == before and m.sha256(elf) != CANDIDATE_BEFORE:
        raise m.Abort("OLD_CANDIDATE_ELF_CHANGED_WITHOUT_SOURCE_UPDATE", 2)
    m.output("pre-git-diff-check", ["git", "-C", str(m.SRC), "diff", "--check"])
    m.STATE.update({
        "mode": "REAL_GENERIC_VULKAN_DESCRIPTOR_WRITE_CORRECTION",
        "old_elf_sha256": m.sha256(elf), "old_source_sha256": current,
        "base_source": C925, "source_isolation": str(m.SRC),
        "source_patch_rev": PATCH_REV, "patch_blob": PATCH_BLOB,
        "tested_games": [], "guest_hacks": False, "no_auto_input": True,
    })
    return raster, header, current


def static_checks(m, raster: Path, header: Path) -> None:
    m.stage("CHECK_PRODUCTION_VULKAN_DESCRIPTOR_WRITE_INVARIANTS")
    src = raster.read_text()
    hdr = header.read_text()
    predicates = {
        "exactly_five_emplaced_writes": src.count("set_writes.emplace_back()") == 5,
        "no_zero_filled_write_resize": "set_writes.resize(" not in src,
        "upper_bound_only_reservation": "set_writes.reserve(required_writes)" in src,
        "storage_info_validation": "write.pBufferInfo == nullptr" in src,
        "image_info_validation": "write.pImageInfo == nullptr" in src,
        "cardinality_validation": "set_writes.size() != required_writes" in src,
        "three_image_type_records": (
            src.count("image_descriptor_is_storage.push_back(image_desc.is_written)") == 3),
        "removed_obsolete_index": "set_write_index" not in src + hdr,
    }
    (m.OUT / "vulkan-source-invariants.json").write_text(
        json.dumps(predicates, indent=2) + "\n")
    if not all(predicates.values()):
        raise m.Abort("GENERIC_VULKAN_DESCRIPTOR_SOURCE_INVARIANT_FAILED", 2)


def native_test(m) -> None:
    m.stage("GCC_RELEASE_NDEBUG_DESCRIPTOR_WRITE_TEST_50003_CASES")
    fixture = verified_download(m, "descriptor-write-exactness-test.cpp",
                                TEST_REV, TEST_REL, TEST_BLOB)
    binary = m.OUT / "descriptor-write-exactness-test"
    m.command("descriptor-cxx-build", [
        "g++", "-std=c++23", "-O2", "-DNDEBUG",
        "-Wall", "-Wextra", "-Werror", "-pedantic",
        str(fixture), "-o", str(binary)
    ], timeout=90)
    m.command("descriptor-cxx-execute", [str(binary)], timeout=25)
    output = (m.OUT / "descriptor-cxx-execute.log").read_text(errors="replace")
    if "descriptor_plan_50003_cases_PASS" not in output:
        raise m.Abort("RELEASE_FIXTURE_NO_SUCCESS_MARKER", 2)
    m.STATE["descriptor_release_fixture_pass"] = True
    m.tests()


def capture_and_run(m, elf: Path):
    m.stage("GOT_NATIVE_118S_RENDER_TEST_EARLY_AND_MENU_FRAMES")
    env = dict(os.environ)
    if not env.get("DISPLAY"):
        socks = sorted(Path("/tmp/.X11-unix").glob("X[0-9]*"))
        if socks:
            env["DISPLAY"] = ":" + socks[0].name[1:]
    env["XDG_DATA_HOME"] = str(m.EXP / "xdg-data")
    (m.EXP / "xdg-data").mkdir(parents=True, exist_ok=True)
    cmd = ["timeout", "-s", "TERM", "-k", "2s", "118s", str(elf),
           "--game", str(m.GAME), "--fullscreen", "true", "--show-fps"]
    (m.OUT / "game-command.json").write_text(json.dumps(cmd) + "\n")
    with (m.OUT / "game-console.log").open("w") as stream:
        proc = subprocess.Popen(cmd, cwd=m.BUILD, env=env,
                                stdout=stream, stderr=subprocess.STDOUT)
        m.STATE["tested_games"] = ["CUSA11456"]
        start = time.monotonic()
        for sec in (8, 16, 26, 40, 55, 85, 112):
            while proc.poll() is None and time.monotonic() < start + sec:
                time.sleep(min(0.5, max(0.01, start + sec - time.monotonic())))
            if proc.poll() is not None:
                break
            m.capture_screen(sec, env)
        try:
            exit_code = proc.wait(timeout=14)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                exit_code = proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                exit_code = proc.wait(timeout=5)
    m.STATE["game_exit"] = exit_code
    log = (m.OUT / "game-console.log").read_text(errors="replace")
    invalid = [line[:1200] for line in log.splitlines() if (
        "Invalid Vulkan descriptor write" in line or
        "Descriptor write count mismatch" in line)]
    m.STATE["invalid_descriptor_rejections"] = len(invalid)
    (m.OUT / "invalid-descriptor-writes.txt").write_text("\n".join(invalid[:100]) + "\n")
    tables = {"entries": 0, "2d": 0, "3d": 0, "cube": 0}
    gpu_events = []
    for line in log.splitlines():
        if any(key in line for key in (
            "GuestIndexedImageDimensions:", "IndexedUniformSamplerBound",
            "GpuDescriptorTableCpuCoherence:", "ErrorDeviceLost",
            "context is lost", "RADV", "SIGSEGV")):
            if len(gpu_events) < 500:
                gpu_events.append(line[:1250])
        if "GuestIndexedImageDimensions:" in line:
            for label, key in (("entries", "entries"), ("bound_2d", "2d"),
                               ("bound_3d", "3d"), ("bound_cube", "cube")):
                match = re.search(r"\b" + label + r"=(\d+)", line)
                if match:
                    tables[key] = max(tables[key], int(match.group(1)))
    (m.OUT / "gpu-key-events.txt").write_text("\n".join(gpu_events) + "\n")
    m.STATE["maximum_typed_bindings"] = tables
    m.STATE["gpu_device_lost_text"] = "ErrorDeviceLost" in log or "context is lost" in log
    m.STATE["screenshots"] = {
        p.name: m.sha256(p) for p in m.OUT.glob("menu-*.png")}
    print("GAME_EXIT=" + str(exit_code), flush=True)
    print("INVALID_DESCRIPTOR_REJECTIONS=" + str(len(invalid)), flush=True)
    print("MAX_GPU_GUEST_TABLE=" + json.dumps(tables), flush=True)
    print("SCREENSHOTS=" + str(len(m.STATE["screenshots"])), flush=True)
    if exit_code not in (0, 124):
        raise m.Abort("GAME_CRASH_AFTER_GENERIC_DESCRIPTOR_FIX", 10)
    m.stage("NATIVE_GPU_CAPTURE_COMPLETE_VISUAL_ASSESSMENT_PENDING")


def run(m):
    raster, header, current = preflight(m)
    patch = verified_download(m, "generic-descriptor-write-exactness.patch",
                              PATCH_REV, PATCH_REL, PATCH_BLOB)
    if current == (RASTERIZER_BEFORE, HEADER_BEFORE):
        m.stage("APPLY_GENERIC_DESCRIPTOR_CORRECTNESS_TO_ISOLATED_EXPERIMENT")
        m.command("patch-check", ["git", "-C", str(m.SRC), "apply", "--check", str(patch)])
        m.command("patch-apply", ["git", "-C", str(m.SRC), "apply", str(patch)])
    if (m.sha256(raster), m.sha256(header)) != (RASTERIZER_AFTER, HEADER_AFTER):
        raise m.Abort("APPLIED_REAL_CPP_SOURCE_SHA_MISMATCH", 2)
    m.output("post-git-diff-check", ["git", "-C", str(m.SRC), "diff", "--check"])
    m.STATE["actual_native_source_sha256"] = {
        "vk_rasterizer.cpp": m.sha256(raster),
        "vk_rasterizer.h": m.sha256(header),
    }
    static_checks(m, raster, header)
    native_test(m)
    m.stage("BUILD_ONLY_CHANGED_NATIVE_VULKAN_OBJECTS_AND_LINK")
    m.command("native-build-incremental", [
        "cmake", "--build", str(m.BUILD), "--parallel", "8", "--", "-k", "0"
    ], timeout=3600, show=True)
    elf = m.BUILD / "shadps4"
    if not elf.is_file() or not os.access(elf, os.X_OK):
        raise m.Abort("INCREMENTAL_LINKED_EXECUTABLE_MISSING", 6)
    m.STATE["new_elf_sha256"] = m.sha256(elf)
    (m.OUT / "linked-candidate.txt").write_text(
        "source=" + str(m.SRC) + "\n"
        + "old_sha256=" + m.STATE["old_elf_sha256"] + "\n"
        + "new_sha256=" + m.STATE["new_elf_sha256"] + "\n")
    m.stage("NATIVE_LINK_PASS_PROTECTED_MENU_ELF_UNTOUCHED")
    m.offload_if_needed()
    capture_and_run(m, elf)


def main() -> int:
    m = None
    result = 0
    try:
        m = import_runner()
        m.STATE["record_type"] = "UNIVERSAL_VULKAN_DESCRIPTOR_WRITE_CORRECTNESS_C925"
        run(m)
    except BaseException as exc:
        if m is not None and isinstance(exc, m.Abort):
            m.STATE["status"] = exc.status
            result = exc.code
        else:
            result = 9
            if m is not None:
                m.STATE["status"] = "GENERIC_RUNNER_UNEXPECTED_EXCEPTION"
                (m.OUT / "unexpected-python-error.txt").write_text(traceback.format_exc())
        print("TRIAL_STOP=" + str(exc), file=sys.stderr, flush=True)
    finally:
        if m is not None:
            m.finish(result)
    return result


if __name__ == "__main__":
    sys.exit(main())
