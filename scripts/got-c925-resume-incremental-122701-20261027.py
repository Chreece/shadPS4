#!/usr/bin/env python3
"""Resume the failed 2026-10-27 c925 build without rebuilding from scratch.

Only removes a duplicate Vulkan image-gather accessor added by the old typed
source patch. Reuses its Ninja objects and the existing isolated worktree.
Preserves the menu-positive ELF, game installs, real saves, ES-DE and SSH.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

HOME = Path.home()
SCRIPTS = HOME / "projects/shadps4/scripts"
PREVIOUS_RUNNER = SCRIPTS / "got-c925-integrated-native-ebea63b.py"
PREVIOUS_RUNNER_BLOB = "5ec32ffdccd7e2a4dba29b56f8fe8f53343b348b"
ORIGINAL_HEADER_SHA = "9041f4a089473fcfadd2bc4aa83334faf1ffd46836cfa31c9e4719fdcf70c8a1"
FIXED_HEADER_SHA = "fd5e730ab62b37062d7abdcd52f6c897e65f9f33893ea480490008f1875e8a33"
SOURCE_HASHES = {
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
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp": "2977a375627d9db49cd453cf40df78800a1de4d22e84753f2600a07873a290c4",
    "src/video_core/renderer_vulkan/vk_rasterizer.h": "f003fa81204d9d766f4ea8bc7de88bbaf15286dbc27dfccaf4d3077b1eb0dd94",
    "tests/compact_image_indices_test.cpp": "0d0c5230064b5114909f830755596ae97e67caa4188c4a092204e7ae2fa7cd61",
    "tests/guest_descriptor_coherence_test.cpp": "a5350f0d411ba1ead1caf1affa2d0e5d44a8f3e5d942e8e3ed0638bed1f3ebb9",
}

def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()

def import_pinned_runner():
    if not PREVIOUS_RUNNER.is_file() or PREVIOUS_RUNNER.is_symlink():
        raise RuntimeError("Original pinned runner is absent or a symlink")
    blob = subprocess.check_output(
        ["git", "hash-object", str(PREVIOUS_RUNNER)], text=True).strip()
    if blob != PREVIOUS_RUNNER_BLOB:
        raise RuntimeError("Original runner blob changed - refusing unsafe resume")
    spec = importlib.util.spec_from_file_location("got_c925_original_runner", PREVIOUS_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot import original pinned runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def main() -> int:
    m = import_pinned_runner()
    m.EXP = m.ROOT / "experiments/c925-integrated-gpu-20261027-122701"
    m.SRC = m.EXP / "source"
    m.BUILD = m.EXP / "build"
    m.STATE.update({
        "mode": "RESUME_EXACT_SOURCE_NATIVE_BUILD",
        "resumed_prior_archive": "c925-integrated-gpu-20261027-122701.tar.gz",
        "original_patch_blob": "f4a2677222256fbc6e0ca10361729e73ea2abe88",
        "corrected_patch_blob": "94b9152c5cb7db8d687082d91cba506e027064bc",
        "patch_rev": "978fd068e8c7b9d57edbcae5e1a21b28700b91da",
        "prior_game_execution": False,
        "tested_games": [],
    })
    result = 0
    try:
        m.stage("VERIFY_EXACT_FAILED_C925_BUILD_SOURCE_AND_PROTECTED_ELF")
        if any(p.is_symlink() for p in (m.EXP, m.SRC, m.BUILD)):
            raise m.Abort("REFUSE_SYMLINKED_WORKSPACE", 2)
        if not (m.SRC / ".git").exists() or not (m.BUILD / "build.ninja").is_file():
            raise m.Abort("ORIGINAL_INCOMPLETE_NINJA_BUILD_MISSING", 2)
        if m.output("source-head", ["git", "-C", str(m.SRC), "rev-parse", "HEAD"]) != m.C925:
            raise m.Abort("EXISTING_SOURCE_COMMIT_CHANGED", 2)
        root = Path(m.output(
            "source-root", ["git", "-C", str(m.SRC), "rev-parse", "--show-toplevel"]))
        if root.resolve() != m.SRC.resolve():
            raise m.Abort("WRONG_GIT_WORKTREE_ROOT", 2)
        cache = m.BUILD / "CMakeCache.txt"
        if (not cache.is_file() or
                ("CMAKE_HOME_DIRECTORY:INTERNAL=" + str(m.SRC)) not in cache.read_text()):
            raise m.Abort("WRONG_CMAKE_BUILD_CACHE_FOR_WORKTREE", 2)
        if not m.PROVEN.is_file() or file_sha(m.PROVEN) != m.MENU_ELF:
            raise m.Abort("PREVIOUS_MENU_CAPABLE_ELF_CHANGED", 2)
        if not m.GAME.is_file():
            raise m.Abort("GAME_IMAGE_NOT_FOUND", 2)
        header_name = "src/video_core/renderer_vulkan/vk_instance.h"
        header = m.SRC / header_name
        if header.is_symlink():
            raise m.Abort("REFUSE_SYMLINKED_HEADER", 2)
        expected_before = {}
        for name, expected in SOURCE_HASHES.items():
            path = m.SRC / name
            if not path.is_file() or path.is_symlink() or file_sha(path) != expected:
                raise m.Abort("SOURCE_SHA_MISMATCH_" + name.replace("/", "_"), 2)
            expected_before[name] = expected
        header_before = file_sha(header)
        if header_before not in (ORIGINAL_HEADER_SHA, FIXED_HEADER_SHA):
            raise m.Abort("UNKNOWN_EXISTING_VULKAN_HEADER", 2)
        # Source changes from the original experiment must be only our patches.
        changed = m.output("changed-source",
                           ["git", "-C", str(m.SRC), "diff", "--name-only"])
        unexpected = set(changed.splitlines()) - set(SOURCE_HASHES) - {header_name}
        if unexpected:
            raise m.Abort("OTHER_MODIFIED_SOURCE_IN_EXPERIMENT", 2)
        if header_before == ORIGINAL_HEADER_SHA:
            old = header.read_bytes()
            fragment = (
                b"    bool IsShaderImageGatherExtendedSupported() const {\n"
                b"        return bool(features.shaderImageGatherExtended);\n"
                b"    }\n\n"
            )
            if (old.count(fragment) != 1 or
                    old.count(b"bool IsShaderImageGatherExtendedSupported() const") != 2):
                raise m.Abort("DUPLICATE_SOURCE_NOT_EXACTLY_AS_LOGGED", 2)
            fixed = old.replace(fragment, b"", 1)
            if hashlib.sha256(fixed).hexdigest() != FIXED_HEADER_SHA:
                raise m.Abort("REPAIRED_VULKAN_HEADER_HASH_DIFFERS_FROM_C925", 2)
            m.stage("REPAIR_ONLY_SECOND_DUPLICATE_VULKAN_GATHER_ACCESSOR")
            newfile = header.with_name("vk_instance.h.c925-repair-new")
            if newfile.exists():
                raise m.Abort("PREEXISTING_REPAIR_TEMP_FILE", 2)
            with newfile.open("xb") as fd:
                fd.write(fixed)
            newfile.chmod(header.stat().st_mode & 0o777)
            newfile.replace(header)
        if file_sha(header) != FIXED_HEADER_SHA:
            raise m.Abort("POST_REPAIR_HEADER_HASH_MISMATCH", 2)
        m.output("diff-check", ["git", "-C", str(m.SRC), "diff", "--check"])
        m.STATE["header_sha256_before"] = header_before
        m.STATE["header_sha256_after"] = file_sha(header)
        m.STATE["source_integrity_verified"] = True
        (m.OUT / "pinned-source-integrity.json").write_text(json.dumps({
            "expected_original_source_sha256": expected_before,
            "header_before": header_before,
            "header_after": file_sha(header),
            "source": str(m.SRC), "build": str(m.BUILD),
        }, indent=2) + "\n")
        m.tests()
        m.stage("INCREMENTAL_NATIVE_CPP_BUILD_KEEP_ALL_COMPLETED_NINJA_OBJECTS")
        m.command("native-build-incremental", [
            "cmake", "--build", str(m.BUILD), "--parallel", "8", "--", "-k", "0"
        ], timeout=3600, show=True)
        elf = m.BUILD / "shadps4"
        if not (elf.is_file() and os.access(elf, os.X_OK)):
            raise m.Abort("INCREMENTAL_LINKED_EXECUTABLE_NOT_PRESENT", 6)
        m.STATE["candidate_sha256"] = file_sha(elf)
        (m.OUT / "candidate.txt").write_text(
            "source_commit=" + m.C925 + "\n"
            "source=" + str(m.SRC) + "\n"
            "candidate_sha256=" + m.STATE["candidate_sha256"] + "\n")
        m.stage("FULL_NATIVE_LINK_PASS")
        if (m.BUILD / "user").exists():
            raise m.Abort("PORTABLE_USER_DIR_ALREADY_PRESENT_REFUSE_OVERWRITE", 2)
        m.prepare_portable_user_data()
        m.offload_if_needed()
        m.launch(elf)
    except m.Abort as error:
        m.STATE["status"] = error.status
        result = error.code
        print("TRIAL_STOP=" + error.status, file=sys.stderr, flush=True)
    except BaseException:
        result = 9
        m.STATE["status"] = "UNEXPECTED_RESUME_RUNNER_EXCEPTION"
        (m.OUT / "resume-python-error.txt").write_text(traceback.format_exc())
        print("TRIAL_STOP=UNEXPECTED_RESUME_RUNNER_EXCEPTION", file=sys.stderr, flush=True)
    finally:
        m.finish(result)
    return result

if __name__ == "__main__":
    sys.exit(main())
