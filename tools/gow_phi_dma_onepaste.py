#!/usr/bin/env python3
"""Build and capture the guarded GoW Phi/SRT/DMA trial without disturbing ES-DE or Ghost.

Standard library only. Changes to the verified source/build tree are temporary and
restored in finally, with the previous executable and source contents backed up.
GitHub full tests are never invoked. Refuses to start with an active emulator/build.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.request

HOME = Path.home()
SOURCE = HOME / "shadps4-esde-verified-builds/20261009-173836/source"
BUILD_ROOT = HOME / "shadps4-esde-verified-builds"
GAME = "CUSA34384"
SHADER = "57b077ac"
BASE_SHA = "aa5b281c0016d64844e784566ef9dd092655ba8b"
VERIFIED_SHA = "8b921edc53fa1d52c40acc0b9dae23553499cf40"
GRAPHICS_SHA = "b64f66078ec0fb5efbf10b86496dade0942a591d"
FRAGMENT_SHA = "77341a4c4d076ea8af7b00038a884e903069f007"
SHARP_SHA = "2492be06a203373bcb58c7b7d993f27dc54de661"
INDEX_SHA = "28957366755addb221d3956a69de4d601a83b390"
FLATTEN_SHA = "0b14e5f5c7961ffcbd691f32223a1b20f25154b2"
ROOT_PRIORITY_SHA = "55c7ef1d1ca61078b90e4dabc83b5c35ee552e3f"
AUTO_SHA = "41b199383806734f3b753bf9f96761de7f7e5234"
BROAD_SHA = "ae1ab93fa6b89e37efc07d8f6f4d09e70880eeda"
GBUFFER_SHA = "88fbecd1abdd224ce74b39e204357403fa85e907"
GPU_PROBE_BASE_SHA = "3b8c11e6cdad20cb039f76eaa9fe28677a31b80a"
HEAD_SHA = "ff31dc5c39bd5aa45d658efb8aba9fac280b1ceb"
WRITER_PREVIOUS_RUNNER_SHA = "de56da87eb9a1593901deb29eee14a12beaaf508"
WRITER_HEAD_SHA = "ddeaf86eaa0e758d12e7d981b54c0014365cd1d8"
PRE_DRAW_BASE_SHA = "0c416c3af8e401076439f5857a5a7e4e9f57a3bc"
PRE_DRAW_HEAD_SHA = "ae8c33a6d5f5ccc9f53901cffaedf0469063b28e"
CANARY_73_BASE_SHA = "1d08d2efe2e4017d4bb9174d227a4dc33a42a0b9"
CANARY_73_SOURCE_SHA = "f82d951999e98e130053ac22fbca3d566c89efa4"
CANARY_73_DIFF_SHA256 = "0312992dc6f12ef4303542981b4e0aa82ea0a0a41c1a9c7d6b049ca8fb63d6ed"
WRITER_PATCH_SHA256 = "f1f2676e35b6dea461341e08da3268d78f2493b6db0b8ed75d74669654e72dc6"
INDIRECT_GPU_DIFF_SHA256 = "30693439d0ba218354aa65e8b6ca6c88a7f24d45665d30d77455e893f0eb8055"
BROAD_PATCH_SHA256 = "83000ad98104b0f335f4539ad9879d7212b678cdc327a18234fbd7117f2c47f6"
AUTO_PATCH_SHA256 = "7743ac55071ce5656b84b8943a486b23ad729778460b1b354d355e879fb42def"
ROOT_PATCH_SHA256 = "88b7fd1ded0f2d429e0b759a44e58a91107d9c023ab08ae8f0a049814b355185"
FLATTEN_PATCH_SHA256 = "3f55a88b945aacabfe07846f8528f8560ca94f356fe672cc0d81e3dd038401ac"
INDEX_PATCH_SHA256 = "fe149e0618d65504a78d71a72810a900340b3a5fe888a54fd70651ae55914c49"
SHARP_PATCH_SHA256 = "af20618e05a7d007f7c73e5c4cb8e0f9aac0117b64ed7b3bd0c7aa35e18a8e5e"
FRAGMENT_PATCH_SHA256 = "82cde74c723e35a4593c63c3d04657fd790d194c962769668dc3a3dac98137e4"
GRAPHICS_PATCH_SHA256 = "bb6a023fe0fca2c192d3da649a3c9bb71006ee4886cd152993960c2f570492e6"
# Exact source preimages from the successful 2026-10-10 16:09 test.
# A changed source is NOT silently patched or overwritten.
EXPECTED_SOURCE_HASHES = {
    "src/core/libraries/kernel/process.cpp":
        "19f0f273b1cb3d36bca923fd1cb78277b0676a8faa74f91bf145555afc09e5ea",
    "src/core/libraries/videoout/driver.cpp":
        "b33eebf943320b00493d86094a96e0f37b70a8f24076c12a6a51e40323d7da77",
    "src/shader_recompiler/backend/spirv/emit_spirv.cpp":
        "eb2dc86d538549b1cfc2eb83ef2ed60cfa377d0d5db11a16e276ef2063cc0585",
    "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp":
        "a916c1ffac0152202b0d46217b7fcee36db2f47ac5b3852f784fc14729e0fd29",
    "src/shader_recompiler/frontend/translate/data_share.cpp":
        "b77d42862c8c14e7e9cc7cec2101918002f9a9649ab284db46ffa24e7bcd4478",
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp":
        "bfcfb28400afddc0e8ac4aad85d41419c302d9807e53f4b736bc497dfcc252db",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp":
        "3ad8a3c389b9ab8da205b12b9ac348f2fd0b0a15f7d38333fc1a4a17b03640c6",
    "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp":
        "a41011fc1b7ea14f21cb02d4f26a7e39c1551e2624c9091ddc659dd6dbb7ed7c",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp":
        "8de5f2822c0cce350ff41f48ea742598e03d165f11bbb2732f1579a64839ee46",
    "src/video_core/renderer_vulkan/vk_presenter.cpp":
        "9905ed541376c6b45798526df6a5b1e00920bb2085b3fffa33214bc1b328489b",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp":
        "8d73198df4f9114aa1a2e79892ed489a89a73fd01c1a982f616116fc08e7588d",
}
VERIFIED_BASE_PATCH_SHA256 = "d1236a631e1bd4c7bf9e2b69a53370f3ea0eb93cc62a1648dbcaf9cb9cbf7058"
REQUIRED = {
    "src/core/libraries/kernel/process.cpp",
    "src/core/libraries/videoout/driver.cpp",
    "src/shader_recompiler/backend/spirv/emit_spirv.cpp",
    "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp",
    "src/shader_recompiler/frontend/translate/data_share.cpp",
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
    "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
    "src/video_core/renderer_vulkan/vk_presenter.cpp",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
}
TIME_LIMIT = 75
NEW_SOURCE = "src/shader_recompiler/ir/passes/resource_patching_pass.cpp"
PROTECTED_SOURCES = REQUIRED | {NEW_SOURCE}

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()

def run(args, *, cwd=None, timeout=30):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)

def processes_in_use(exclude=(), exclude_group=None, ignored_launchers=None):
    """Guard real emulator/build processes, not the ES-DE package launcher shell.

    /proc/PID/comm can be named after a shell script rather than /proc/PID/exe.
    An idle /bin/bash /home/.../.local/bin/shadps4-pkg-auto was falsely
    classified as an emulator. Only that exact shell+script combination is
    exempt; any actual shadps4 binary or build process still blocks the trial.
    """
    problems = []
    known_launcher = str(HOME / ".local/bin/shadps4-pkg-auto")
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        pid = int(item.name)
        if pid == os.getpid() or pid in exclude:
            continue
        if exclude_group is not None:
            try:
                if os.getpgid(pid) == exclude_group:
                    continue
            except (ProcessLookupError, PermissionError):
                pass
        try:
            if item.stat().st_uid != os.getuid():
                continue
            argv = (item / "cmdline").read_bytes().split(b"\x00")
            cmd = " ".join(os.fsdecode(arg) for arg in argv if arg)
            name = (item / "comm").read_text().strip().lower()
            # Read the executable, not merely 'comm': the kernel may use the
            # interpreted script's basename as comm even though bash runs it.
            try:
                executable = os.readlink(item / "exe").removesuffix(" (deleted)")
            except OSError:
                executable = ""
            launcher_shell = (Path(executable).name in ("bash", "sh", "dash") and
                              len(argv) > 1 and os.fsdecode(argv[1]) == known_launcher)
            if launcher_shell:
                if ignored_launchers is not None:
                    ignored_launchers.append({
                        "pid": pid, "executable": executable,
                        "reason": "verified_shell_launcher", "command": cmd[:140]})
                continue
            if name.startswith("shadps4") or re.search(r"(?<![\w-])shadps4(?:\s|$)", cmd.lower()):
                problems.append({"pid": pid, "reason": "emulator", "command": cmd[:140]})
            elif name in ("ninja", "cmake", "c++", "cc1plus") and "shadps4" in cmd.lower():
                problems.append({"pid": pid, "reason": "build", "command": cmd[:140]})
        except (OSError, PermissionError, RuntimeError):
            pass
    return problems

def find_build():
    caches = sorted(BUILD_ROOT.glob("*/build/CMakeCache.txt"), reverse=True)
    for cache in caches:
        try:
            match = re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.*)$",
                              cache.read_text(errors="replace"), flags=re.MULTILINE)
            if match and Path(match.group(1)).resolve() == SOURCE.resolve():
                path = cache.parent
                if (path / "build.ninja").is_file() or (path / "Makefile").is_file():
                    return path
        except OSError:
            pass
    raise RuntimeError("No existing CMake build configured for the verified source; nothing modified")

def fetch_strict_patch(base_sha, head_sha, expected_changed):
    url = f"https://api.github.com/repos/Chreece/shadPS4/compare/{base_sha}...{head_sha}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "gow-graphics-census-diagnostics",
        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=25) as res:
        payload = json.load(res)
    commits = payload.get("commits", [])
    if (payload.get("base_commit", {}).get("sha") != base_sha or
            payload.get("merge_base_commit", {}).get("sha") != base_sha or
            not commits or commits[-1].get("sha") != head_sha or
            payload.get("total_commits") != len(commits)):
        raise RuntimeError("Unexpected pinned comparison ancestry or incomplete GitHub diff")
    files = payload.get("files", [])
    changed = {f["filename"] for f in files}
    if changed != expected_changed:
        raise RuntimeError("Unexpected changed files in pinned compare: " + repr(changed))
    parts = []
    for f in files:
        rel = f["filename"]
        if rel == "tools/gow_phi_dma_onepaste.py":
            continue
        if (f.get("status") != "modified" or not f.get("patch") or
                not rel.startswith("src/") or ".." in Path(rel).parts):
            raise RuntimeError("Unsafe or unavailable pinned patch: " + rel)
        parts.append(f"diff --git a/{rel} b/{rel}\n--- a/{rel}\n+++ b/{rel}\n"
                     + f["patch"].rstrip("\n") + "\n")
    return "".join(parts).encode()

def get_patches():
    # Phase one is byte-for-byte the patch that compiled and ran at 16:09.
    # The host source differs from the GitHub BASE_SHA tree.
    verified_patch = fetch_strict_patch(
        BASE_SHA, VERIFIED_SHA, REQUIRED | {"tools/gow_phi_dma_onepaste.py"})
    actual_hash = hashlib.sha256(verified_patch).hexdigest()
    if actual_hash != VERIFIED_BASE_PATCH_SHA256:
        raise RuntimeError("Previously proven patch bytes changed: " + actual_hash)
    # The 16:47 graphics patch ran successfully on this exact host.
    graphics_patch = fetch_strict_patch(
        VERIFIED_SHA, GRAPHICS_SHA, {
            "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
            "src/video_core/renderer_vulkan/vk_presenter.cpp"})
    graphics_hash = hashlib.sha256(graphics_patch).hexdigest()
    if graphics_hash != GRAPHICS_PATCH_SHA256:
        raise RuntimeError("Previous successful graphics patch changed: " + graphics_hash)
    # Phase three also ran successfully at 17:16; enforce identical bytes.
    fragment_patch = fetch_strict_patch(
        GRAPHICS_SHA, FRAGMENT_SHA, {"src/video_core/renderer_vulkan/vk_rasterizer.cpp"})
    fragment_hash = hashlib.sha256(fragment_patch).hexdigest()
    if fragment_hash != FRAGMENT_PATCH_SHA256:
        raise RuntimeError("Previously proven fragment patch changed: " + fragment_hash)
    # Phase four is exactly the successful 17:40 host-proven descriptor trace.
    sharp_probe = fetch_strict_patch(
        FRAGMENT_SHA, SHARP_SHA, {NEW_SOURCE, "tools/gow_phi_dma_onepaste.py"})
    known_hash = hashlib.sha256(sharp_probe).hexdigest()
    if known_hash != SHARP_PATCH_SHA256:
        raise RuntimeError("Proven SHARP probe patch bytes changed: " + known_hash)
    # Phase five is the exact image-index tree source used in the 18:05
    # successful test, verified against its recorded diff digest.
    index_probe = fetch_strict_patch(
        SHARP_SHA, INDEX_SHA, {NEW_SOURCE, "tools/gow_phi_dma_onepaste.py"})
    known_index_hash = hashlib.sha256(index_probe).hexdigest()
    if known_index_hash != INDEX_PATCH_SHA256:
        raise RuntimeError("Proven IR index-tree patch changed: " + known_index_hash)

    # Phase six is the same bounded, read-only flattening probe that
    # compiled and captured all 24 failures in the 18:23 host run.
    flatten_path = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
    flatten_probe = fetch_strict_patch(
        INDEX_SHA, FLATTEN_SHA, {flatten_path, "tools/gow_phi_dma_onepaste.py"})
    flatten_hash = hashlib.sha256(flatten_probe).hexdigest()
    if flatten_hash != FLATTEN_PATCH_SHA256:
        raise RuntimeError("Previously proven flatten trace changed: " + flatten_hash)

    # Phase seven is the exact 18:32 host-proven SGPR8-first patch.
    root_priority_patch = fetch_strict_patch(
        FLATTEN_SHA, ROOT_PRIORITY_SHA, {flatten_path})
    root_digest = hashlib.sha256(root_priority_patch).hexdigest()
    if root_digest != ROOT_PATCH_SHA256:
        raise RuntimeError("Proven SGPR8 root priority patch changed: " + root_digest)

    # Phase eight is byte-identical to the 18:45 successful host test.
    auto_topo_patch = fetch_strict_patch(
        ROOT_PRIORITY_SHA, AUTO_SHA,
        {flatten_path, "tools/gow_phi_dma_onepaste.py"})
    auto_hash = hashlib.sha256(auto_topo_patch).hexdigest()
    if auto_hash != AUTO_PATCH_SHA256:
        raise RuntimeError("Host-proven auto-topology patch changed: " + auto_hash)

    # Phase nine compiled/reran successfully in the 19:04 host archive.
    broad_probe = fetch_strict_patch(
        AUTO_SHA, BROAD_SHA,
        {flatten_path, "tools/gow_phi_dma_onepaste.py"})
    broad_hash = hashlib.sha256(broad_probe).hexdigest()
    if broad_hash != BROAD_PATCH_SHA256:
        raise RuntimeError("Proven generalized SRT source changed: " + broad_hash)

    # Only addition: passive, first-128 G-buffer draw issuance and raster
    # state telemetry. The source tree includes this diagnostic runner as
    # a known extra, which is never applied to the emulator source.
    draw_path = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
    draw_probe = fetch_strict_patch(
        BROAD_SHA, GBUFFER_SHA, {draw_path, "tools/gow_phi_dma_onepaste.py"})
    additions = [
        l for l in draw_probe.splitlines()
        if l.startswith(b"+") and not l.startswith(b"+++")
    ]
    if (b'GOW_GBUFFER_DRAW seq=' not in draw_probe or
            b'SHADPS4_GOW_GBUFFER_DRAW_TRACE' not in draw_probe or
            b'TraceGoWGBufferDraw' not in draw_probe or
            any(b'cmdBuf.draw' in l or b'cmdbuf.draw' in l or
                b'inst.SetArg(' in l for l in additions)):
        raise RuntimeError("Unexpected graphics-semantic change in passive draw trace")
    # Phase eleven: the one new diagnostic, on exactly one source file.
    # GPIO-style source changes from earlier host runs remain unchanged.
    gpu_args_patch = fetch_strict_patch(
        GPU_PROBE_BASE_SHA, HEAD_SHA, {GBUFFER_SOURCE})
    if hashlib.sha256(gpu_args_patch).hexdigest() != INDIRECT_GPU_DIFF_SHA256:
        raise RuntimeError("GPU indirect command probe differs from pinned code")
    # A twelfth, additive-only source probe: identify writable compute
    # resources intersecting the six known zero-instance indirect commands.
    # The separate branch is an exact GitHub commit descendant of the last
    # runner that compiled successfully on the user's Debian host.
    writer_probe = fetch_strict_patch(
        WRITER_PREVIOUS_RUNNER_SHA, WRITER_HEAD_SHA,
        {GBUFFER_SOURCE, "tools/gow_phi_dma_onepaste.py"})
    if hashlib.sha256(writer_probe).hexdigest() != WRITER_PATCH_SHA256:
        raise RuntimeError("20:10 host-proven writer patch bytes changed")
    for marker in (b'TraceGoWIndirectWriterCandidates',
                   b'GOW_INDIRECT_WRITER_CANDIDATE',
                   b'GOW_INDIRECT_BUFFER_ORIGIN'):
        if marker not in writer_probe:
            raise RuntimeError("Pinned writer-provenance probe is incomplete")
    # Exactly four added, bounded probe sections in the new source revision.
    producer_probe = fetch_strict_patch(
        PRE_DRAW_BASE_SHA, PRE_DRAW_HEAD_SHA, {GBUFFER_SOURCE})
    for marker in (b'GOW_PRODUCER_RESOURCE shader=', b'GOW_PRODUCER_FIRST_DRAW',
                   b'TraceGoWProducerResourceChain(', b'SHADPS4_GOW_PRODUCER_INPUTS'):
        if marker not in producer_probe:
            raise RuntimeError("Pre-draw producer patch missing expected instrumentation")
    # Phase fourteen: one specifically allowed 0x73ad8e38 compute shader,
    # 128x128x1 with exact observed 32-byte input and 512-byte output.
    one_shot_73 = fetch_strict_patch(
        CANARY_73_BASE_SHA, CANARY_73_SOURCE_SHA, {GBUFFER_SOURCE})
    if hashlib.sha256(one_shot_73).hexdigest() != CANARY_73_DIFF_SHA256:
        raise RuntimeError("The guarded producer canary patch changed")
    for marker in (b'SHADPS4_GOW_ENABLE_73_ONE_SHOT',
                   b'GOW_73_ADMISSION', b'0x73ad8e38ULL',
                   b'out_sharp.GetSize() == 512'):
        if marker not in one_shot_73:
            raise RuntimeError("Missing exact-resource guard for 73 canary")
    return (verified_patch, graphics_patch, fragment_patch, sharp_probe,
            index_probe, flatten_probe, root_priority_patch, auto_topo_patch,
            broad_probe, draw_probe, gpu_args_patch, writer_probe, producer_probe,
            one_shot_73)


def verify_preimages():
    if set(EXPECTED_SOURCE_HASHES) != PROTECTED_SOURCES:
        raise RuntimeError("Incomplete known-good source preimage list")
    observed = {}
    for rel in sorted(PROTECTED_SOURCES):
        path = SOURCE / rel
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("Missing or symlinked source: " + rel)
        observed[rel] = sha(path)
    # Every source preimage, including the descriptor patcher, was measured
    # by the previous preflight and must match before any live source change.
    changed = {
        rel: {"expected": EXPECTED_SOURCE_HASHES[rel], "observed": observed[rel]}
        for rel in sorted(PROTECTED_SOURCES)
        if observed[rel] != EXPECTED_SOURCE_HASHES[rel]
    }
    if changed:
        raise RuntimeError("Verified source preimages changed; refusing build: "
                           + json.dumps(changed, sort_keys=True))
    return observed

def verify_staged_instrumentation(staged):
    raster = (staged / "src/video_core/renderer_vulkan/vk_rasterizer.cpp").read_text()
    presenter = (staged / "src/video_core/renderer_vulkan/vk_presenter.cpp").read_text()
    desc_patcher = (staged / NEW_SOURCE).read_text()
    if ("GOW_FS_IMAGE_SHARP_BEGIN" not in desc_patcher or
            "GOW_FS_INDEX_GRAPH_BEGIN" not in desc_patcher or
            "GOW_FS_INDEX_GRAPH_END" not in desc_patcher or
            "SHADPS4_GOW_FS_INDEX_TREE_TRACE" not in desc_patcher or
            "const bool immediate_offset = arg.IsImmediate()" not in desc_patcher):
        raise RuntimeError("Staged read-only SHARP diagnostic missing")
    flat = (staged / "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp").read_text()
    if ("GOW_SRT_FLATTEN_BEGIN" not in flat or
            "GOW_SRT_FLATTEN_END" not in flat or
            "SHADPS4_GOW_SRT_FLATTEN_TRACE" not in flat or
            "SHADPS4_GOW_SRT_SGPR8_FIRST" not in flat or
            "SHADPS4_GOW_SRT_AUTO_ROOTS" not in flat or
            "GOW_SRT_TOPO_PLAN" not in flat or
            "MakeSrtRootDependencyPlan" not in flat or
            "all_shader_trial" not in flat or
            "GOW_SRT_ROOT_ORDER" not in flat or
            "event=USE_INDEX_FAILED" not in flat):
        raise RuntimeError("Staged flattening-only instrumentation missing")
    # Direct Draw has a local regs reference; DrawIndirect does not.
    # Validate the *compiled source form* of both instrumentation callsites.
    if (raster.count("void TraceGoWGBufferDraw(") != 1 or
            raster.count(
                "TraceGoWGBufferDraw(pipeline, regs, state, is_indexed, false,") != 1 or
            raster.count(
                "TraceGoWGBufferDraw(pipeline, liverpool->regs, state, "
                "is_indexed, true,") != 1 or
            "TraceGoWGBufferDraw(pipeline, regs, state, is_indexed, true," in raster or
            "SHADPS4_GOW_GBUFFER_DRAW_TRACE" not in raster or
            "GOW_INDIRECT_WRITER_CANDIDATE" not in raster or
            "GOW_INDIRECT_BUFFER_ORIGIN" not in raster or
            "GOW_PRODUCER_RESOURCE shader={:#x}" not in raster or
            "GOW_PRODUCER_FIRST_DRAW" not in raster or
            "GOW_73_ADMISSION shader={:#x}" not in raster or
            "SHADPS4_GOW_ENABLE_73_ONE_SHOT" not in raster or
            "std::array<GoWComputeCanary, 7>" not in raster or
            raster.count("static void TraceGoWProducerResourceChain(") != 1 or
            raster.count("static void TraceGoWIndirectGpuArgs(") != 1 or
            raster.count("TraceGoWIndirectGpuArgs(pipeline, liverpool->regs,") != 1 or
            "GOW_INDIRECT_GPU_CAPTURE slot={}" not in raster):
        raise RuntimeError("Staged passive G-buffer draw trace missing")
    if (raster.count("void LogGoWGraphicsDrawTotals(u32 frame)") != 1 or
            raster.count("RecordGoWPreparedDrawTargets(regs, key.mrt_mask, pipeline);") != 1 or
            "RecordGoWGraphicsAudit(" in raster or
            "GOW_GRAPHICS_SUMMARY frame={}" not in raster or
            "GOW_GRAPHICS_FRAGMENT frame={}" not in raster or
            'fmt::format("f{:02}_{}", gow_frame_id, label)' not in presenter or
            "LogGoWGraphicsDrawTotals(gow_frame_id)" not in presenter or
            "gow_frame_id == 6" not in presenter):
        raise RuntimeError("Staged graphics instrumentation integrity check failed")

# The host's original Draw/DrawIndirect layout intentionally differs from
# GitHub's pinned base. The first nine patches are already host-proven,
# but git-apply rejects the tenth probe's context at Draw line 258.
# Extract ONLY the three inserted snippets from the sha-pinned tenth diff,
# and inject them at unique semantic anchors. Never rewrite a draw command.
DRAW_DIAGNOSTIC_DIFF_SHA256 = (
    "69d5c77dbe9458e98d5abe0ef522771c1d5d49dc80126cb9744e6ba849b32d98"
)
GBUFFER_SOURCE = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"

def apply_portable_gbuffer_probe(root, pinned_diff):
    if hashlib.sha256(pinned_diff).hexdigest() != DRAW_DIAGNOSTIC_DIFF_SHA256:
        raise RuntimeError("The approved G-buffer diagnostic patch bytes changed")
    diff_text = pinned_diff.decode("utf-8", errors="strict")
    if diff_text.count("diff --git ") != 1 or not diff_text.startswith(
        "diff --git a/" + GBUFFER_SOURCE + " b/" + GBUFFER_SOURCE + "\n"
    ):
        raise RuntimeError("G-buffer probe diff must modify only vk_rasterizer.cpp")
    parts = re.split(r"(?m)^@@[^\n]*\n", diff_text)
    if len(parts) != 4:
        raise RuntimeError("G-buffer probe expected exactly three insertion hunks")
    def inserted(block):
        lines = [
            line[1:] for line in block.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        return "\n".join(lines) + "\n"

    helper, direct_log, indirect_log = (inserted(part) for part in parts[1:])
    if (not helper.startswith("// GoW Ragnarok: correlate the GPU-zero G-buffer")
            or helper.count("static void TraceGoWGBufferDraw(") != 1
            or not direct_log.startswith("    TraceGoWGBufferDraw(")
            or not indirect_log.startswith("    TraceGoWGBufferDraw(")
            or "pipeline, regs, state, is_indexed, false," not in direct_log
            or "pipeline, liverpool->regs, state, is_indexed, true," not in indirect_log
            or "pipeline, regs, state, is_indexed, true," in indirect_log):
        raise RuntimeError("G-buffer probe diagnostic snippets do not match approved scope")

    path = root / GBUFFER_SOURCE
    original_bytes = path.read_bytes()
    original = original_bytes.decode("utf-8", errors="strict")
    if "\r\n" in original or "TraceGoWGBufferDraw" in original:
        raise RuntimeError("Unexpected source encoding or duplicate G-buffer probe")
    direct_tag = "void Rasterizer::Draw(bool is_indexed, u32 index_offset) {"
    indirect_tag = "void Rasterizer::DrawIndirect(bool is_indexed"
    tail_tag = "// This diagnostics-only guard deliberately runs AFTER pipeline creation."
    for tag in (direct_tag, indirect_tag, tail_tag):
        if original.count(tag) != 1:
            raise RuntimeError("Ambiguous or missing known rasterizer anchor: " + tag)
    start = original.index(direct_tag)
    middle = original.index(indirect_tag, start)
    end = original.index(tail_tag, middle)
    direct = original[start:middle]
    indirect = original[middle:end]
    if (not all(term in direct for term in ("cmdbuf.draw(", "cmdbuf.drawIndexed"))
            or not all(term in indirect for term in (
                "cmdbuf.drawIndirect", "cmdbuf.drawIndexedIndirect"))):
        raise RuntimeError("Graphics draw emission differs from expected proven code")
    debug_anchor = "    DebugState.IncDrawCall();\n"
    reset_anchor = "    ResetBindings(false);\n"
    if direct.count(debug_anchor) == 1:
        direct = direct.replace(debug_anchor, debug_anchor + direct_log)
    elif direct.count(reset_anchor) == 1:
        # Older host direct-draw layout lacks a DebugState counter.
        # This anchor remains AFTER the original submitted draw.
        direct = direct.replace(reset_anchor, direct_log + reset_anchor)
    else:
        raise RuntimeError("Cannot safely identify completed direct draw insertion point")
    if indirect.count(reset_anchor) != 1:
        raise RuntimeError("Cannot safely identify completed indirect draw insertion point")
    indirect = indirect.replace(reset_anchor, indirect_log + reset_anchor)
    patched = original[:start] + helper + direct + indirect + original[end:]
    if (patched.count("TraceGoWGBufferDraw(pipeline, regs, state, is_indexed, false") != 1
            or patched.count(
                "TraceGoWGBufferDraw(pipeline, liverpool->regs, state, is_indexed, true") != 1
            or patched.count("static void TraceGoWGBufferDraw(") != 1
            or patched.replace(helper, "", 1).replace(direct_log, "", 1)
               .replace(indirect_log, "", 1) != original
            or len(patched) != len(original) +
               len(helper) + len(direct_log) + len(indirect_log)):
        raise RuntimeError("G-buffer portable injection failed no-semantic-change check")
    path.write_bytes(patched.encode("utf-8"))
    return {
        "method": "sha256_pinned_semantic_anchor_insertion",
        "diff_sha256": DRAW_DIAGNOSTIC_DIFF_SHA256,
        "original_source_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "patched_source_sha256": sha(path),
        "matched_direct": True,
        "matched_indirect": True,
        "no_existing_lines_modified": True,
    }


# The installed host has a different DrawIndirect layout from the GitHub
# baseline. Stage the new probe at unique, verified source anchors instead of
# reusing a context-dependent diff that already failed in an earlier attempt.
# The git comparison, exact SHA and 3 insertion hunks are all mandatory.
def apply_portable_indirect_gpu_probe(root, pinned_diff):
    if hashlib.sha256(pinned_diff).hexdigest() != INDIRECT_GPU_DIFF_SHA256:
        raise RuntimeError("Pinned indirect GPU diff hash mismatch")
    rel = GBUFFER_SOURCE
    text_diff = pinned_diff.decode("utf-8", errors="strict")
    if (text_diff.count("diff --git ") != 1 or not text_diff.startswith(
        f"diff --git a/{rel} b/{rel}\n"
    )):
        raise RuntimeError("Indirect GPU probe attempted to change another source file")
    blocks = re.split(r"(?m)^@@[^\n]*\n", text_diff)
    if len(blocks) != 4:
        raise RuntimeError("Indirect GPU probe must have exactly 3 additive hunks")
    def inserted(block):
        if any(line.startswith("-") and not line.startswith("---")
               for line in block.splitlines()):
            raise RuntimeError("Indirect GPU probe may not remove existing code")
        return "\n".join(
            line[1:] for line in block.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ) + "\n"

    inc, helper, call = (inserted(block) for block in blocks[1:])
    if (inc != "#include <algorithm>\n" or
            not helper.startswith("// GoW Ragnarok: read back the ACTUAL 20-byte") or
            helper.count("static void TraceGoWIndirectGpuArgs(") != 1 or
            "GOW_INDIRECT_GPU_CAPTURE slot={}" not in helper or
            "sizeof(VkDrawIndexedIndirectCommand)" not in helper or
            "scheduler.DeferPriorityOperation(" not in helper or
            "runtime.CopyBuffer(buffer, capture.buffer" not in helper or
            "SHADPS4_GOW_INDIRECT_GPU_ARGS" not in helper or
            not call.startswith("    // Snapshot Vulkan's true indirect arguments") or
            call.count("TraceGoWIndirectGpuArgs(") != 1):
        raise RuntimeError("Unexpected content in indirect GPU diagnostic")
    source_path = root / rel
    before = source_path.read_bytes()
    original = before.decode("utf-8", errors="strict")
    head = "#include <array>\n"
    indirect = "void Rasterizer::DrawIndirect(bool is_indexed, VAddr arg_address, u32 offset, u32 stride,"
    tail = "// This diagnostics-only guard deliberately runs AFTER pipeline creation."
    if (original.count(head) != 1 or original.count(indirect) != 1 or
            original.count(tail) != 1 or
            "#include <algorithm>\n" in original or
            "TraceGoWIndirectGpuArgs" in original):
        raise RuntimeError("Indeterminate or already-instrumented source anchors")
    start = original.index(indirect)
    end = original.index(tail, start)
    body = original[start:end]
    barrier = (
        "    if (needs_barrier) {\n"
        "        runtime.FlushBarriers();\n"
        "    }\n\n"
        "    pipeline->BindResources(set_writes, push_data);"
    )
    if (body.count(barrier) != 1 or
            "cmdbuf.drawIndexedIndirect(buffer->Handle(), base, max_count, stride);" not in body or
            "TraceGoWGBufferDraw(pipeline, liverpool->regs, state, is_indexed, true," not in body):
        raise RuntimeError("Indirect draw code is not the 19:32 working layout")
    updated_body = body.replace(
        barrier,
        barrier.replace("    pipeline->BindResources(set_writes, push_data);",
                        call + "    pipeline->BindResources(set_writes, push_data);"))
    modified = original.replace(head, head + inc, 1)
    begin = modified.index(indirect)
    finish = modified.index(tail, begin)
    modified = modified[:begin] + helper + updated_body + modified[finish:]
    if (modified.count("static void TraceGoWIndirectGpuArgs(") != 1 or
            modified.count("TraceGoWIndirectGpuArgs(pipeline, liverpool->regs,") != 1 or
            modified.replace(inc, "", 1).replace(helper, "", 1)
                    .replace(call, "", 1) != original or
            len(modified) != len(original) + len(inc) + len(helper) + len(call)):
        raise RuntimeError("Indirect GPU probe altered existing draw code")
    source_path.write_bytes(modified.encode("utf-8"))
    return {
        "source_before_sha256": hashlib.sha256(before).hexdigest(),
        "source_after_sha256": sha(source_path),
        "patch_sha256": INDIRECT_GPU_DIFF_SHA256,
        "original_lines_unchanged": True,
        "record_bytes": 20,
        "unique_address_limit": 6,
    }


def apply_portable_writer_origin_probe(root, pinned_diff):
    # Stage twelve is additive only, pinned to a GitHub compare with exactly
    # one source file, and never changes the draw/dispatch execution path.
    text_diff = pinned_diff.decode('utf-8', errors='strict')
    prefix = f'diff --git a/{GBUFFER_SOURCE} b/{GBUFFER_SOURCE}\n'
    if (text_diff.count('diff --git ') != 1 or
            not text_diff.startswith(prefix)):
        raise RuntimeError('Writer probe changed unexpected source files')
    parts = re.split(r'(?m)^@@[^\n]*\n', text_diff)[1:]
    if len(parts) < 3 or len(parts) > 5:
        raise RuntimeError('Unexpected writer-probe diff structure')
    sections = []
    for block in parts:
        lines = block.splitlines()
        if any(line.startswith('-') and not line.startswith('---') for line in lines):
            raise RuntimeError('Writer-probe diff removes existing source code')
        sections.append('\n'.join(line[1:] for line in lines
                                  if line.startswith('+') and not line.startswith('+++')) + '\n')
    def unique_section(marker):
        matches = [chunk for chunk in sections if marker in chunk]
        if len(matches) != 1:
            raise RuntimeError('Writer-probe marker not unique: ' + marker)
        return matches[0]
    helper = unique_section('static void TraceGoWIndirectWriterCandidates(')
    compute_call = unique_section('TraceGoWIndirectWriterCandidates(cs,')
    draw_origin = unique_section('GOW_INDIRECT_BUFFER_ORIGIN')
    if (not helper.startswith('// GoW Ragnarok: passive provenance census') or
            not draw_origin.startswith('    // Non-mutating cache-state provenance') or
            not compute_call.startswith('    TraceGoWIndirectWriterCandidates(cs,') or
            helper.count('GOW_INDIRECT_WRITER_CANDIDATE') != 1 or
            helper.count('GOW_INDIRECT_WRITER_SUMMARY') != 1 or
            'buffer_cache.IsRegionGpuModified' not in draw_origin or
            'SHADPS4_GOW_INDIRECT_WRITER_SCAN' not in helper or
            'unsafe_fetch' not in helper or
            'Shader::UNKNOWN_LOCATION' not in helper):
        raise RuntimeError('New source additions do not match audited writer probe')
    path = root / GBUFFER_SOURCE
    pre = path.read_bytes()
    original = pre.decode('utf-8')
    dispatch_tag = 'void Rasterizer::DispatchDirect() {'
    indirect_tag = 'void Rasterizer::DrawIndirect(bool is_indexed'
    after_indirect = '// This diagnostics-only guard deliberately runs AFTER pipeline creation.'
    cs_anchor = '    const auto& cs = pipeline->GetStage(Shader::SwStage::Compute);\n'
    state_anchor = '    const auto state = BeginRendering(pipeline);\n\n'
    if (original.count(dispatch_tag) != 1 or
            original.count(indirect_tag) != 1 or
            original.count(after_indirect) != 1 or
            'GOW_INDIRECT_WRITER_CANDIDATE' in original or
            'GOW_INDIRECT_BUFFER_ORIGIN' in original or
            'GOW_INDIRECT_GPU_CAPTURE' not in original):
        raise RuntimeError('Unexpected source state for incremental writer probe')
    result = original.replace(dispatch_tag, helper + dispatch_tag, 1)
    dispatch_start = result.index(dispatch_tag)
    dispatch_stop_tag = 'void Rasterizer::DispatchIndirect('
    dispatch_stop = result.index(dispatch_stop_tag, dispatch_start)
    dispatch_body = result[dispatch_start:dispatch_stop]
    if dispatch_body.count(cs_anchor) != 1:
        raise RuntimeError('Compute shader metadata anchor missing in DispatchDirect')
    result = (result[:dispatch_start] +
              dispatch_body.replace(cs_anchor, cs_anchor + compute_call, 1) +
              result[dispatch_stop:])
    indirect_start = result.index(indirect_tag)
    indirect_end = result.index(after_indirect, indirect_start)
    body = result[indirect_start:indirect_end]
    if body.count(state_anchor) != 1:
        raise RuntimeError('Indirect draw-state anchor missing')
    result = (result[:indirect_start] + body.replace(
        state_anchor, state_anchor + draw_origin, 1) + result[indirect_end:])
    if (result.replace(helper, '', 1).replace(compute_call, '', 1)
            .replace(draw_origin, '', 1) != original or
            len(result) != len(original) + len(helper) + len(compute_call) + len(draw_origin)):
        raise RuntimeError('Writer probe modified existing source semantics')
    path.write_bytes(result.encode('utf-8'))
    return {'source_before': hashlib.sha256(pre).hexdigest(),
            'source_after': sha(path),
            'no_existing_lines_modified': True,
            'producer_probe_installed': True}


def apply_portable_pre_draw_producer_probe(root, pinned_diff):
    # Exactly four additive-only hunks with GitHub SHA/ancestry verification.
    text_diff = pinned_diff.decode("utf-8", errors="strict")
    prefix = f"diff --git a/{GBUFFER_SOURCE} b/{GBUFFER_SOURCE}\n"
    if text_diff.count("diff --git ") != 1 or not text_diff.startswith(prefix):
        raise RuntimeError("Pre-draw probe changed another source file")
    blocks = re.split(r"(?m)^@@[^\n]*\n", text_diff)
    if len(blocks) != 5:
        raise RuntimeError("Pre-draw producer patch must contain four hunks")
    snippets = []
    for block in blocks[1:]:
        lines = block.splitlines()
        if any(line.startswith("-") and not line.startswith("---") for line in lines):
            raise RuntimeError("Pre-draw producer patch removes source lines")
        snippets.append("\n".join(
            line[1:] for line in lines
            if line.startswith("+") and not line.startswith("+++")
        ) + "\n")
    declaration, draw_insert, helper, compute_insert = snippets
    if (not declaration.startswith("// Diagnostic only: stop enumerating") or
            not draw_insert.startswith('    const char* producer_flag = std::getenv(') or
            "GOW_PRODUCER_FIRST_DRAW" not in draw_insert or
            not helper.startswith("// Passive, bounded dependency discovery") or
            helper.count("static void TraceGoWProducerResourceChain(") != 1 or
            "GOW_PRODUCER_RESOURCE shader={:#x}" not in helper or
            "Shader::UNKNOWN_LOCATION" not in helper or
            "cache.IsRegionGpuModified(address, bytes)" not in helper or
            not compute_insert.startswith("    TraceGoWProducerResourceChain(cs, buffer_cache,")):
        raise RuntimeError("Pre-draw source probe differs from reviewed additions")
    source_path = root / GBUFFER_SOURCE
    before = source_path.read_bytes()
    original = before.decode("utf-8", errors="strict")
    namespace_tag = "namespace Vulkan {\n\n"
    helper_tag = "static void TraceGoWIndirectWriterCandidates("
    draw_tag = "void Rasterizer::DrawIndirect(bool is_indexed"
    after_draw = "// This diagnostics-only guard deliberately runs AFTER pipeline creation."
    compute_tag = "void Rasterizer::DispatchDirect() {"
    after_compute = "void Rasterizer::DispatchIndirect("
    draw_state_tag = "    const auto state = BeginRendering(pipeline);\n\n"
    cs_tag = "    const auto& cs = pipeline->GetStage(Shader::SwStage::Compute);\n"
    for tag in (namespace_tag, helper_tag, draw_tag, after_draw, compute_tag, after_compute):
        if original.count(tag) != 1:
            raise RuntimeError("Ambiguous or missing producer source anchor: " + tag)
    if "TraceGoWProducerResourceChain(" in original:
        raise RuntimeError("New probe already installed")
    changed = original.replace(namespace_tag, namespace_tag + declaration, 1)
    changed = changed.replace(helper_tag, helper + helper_tag, 1)
    start = changed.index(draw_tag)
    stop = changed.index(after_draw, start)
    body = changed[start:stop]
    if body.count(draw_state_tag) != 1:
        raise RuntimeError("DrawIndirect state anchor is ambiguous")
    changed = (changed[:start] + body.replace(draw_state_tag, draw_state_tag + draw_insert, 1)
               + changed[stop:])
    start = changed.index(compute_tag)
    stop = changed.index(after_compute, start)
    body = changed[start:stop]
    if body.count(cs_tag) != 1:
        raise RuntimeError("DispatchDirect stage anchor is ambiguous")
    changed = (changed[:start] + body.replace(cs_tag, cs_tag + compute_insert, 1)
               + changed[stop:])
    if (changed.replace(declaration, "", 1).replace(draw_insert, "", 1)
               .replace(helper, "", 1).replace(compute_insert, "", 1) != original or
            len(changed) != len(original) +
               sum(map(len, (declaration, draw_insert, helper, compute_insert)))):
        raise RuntimeError("Pre-draw probe changed original source statements")
    source_path.write_bytes(changed.encode("utf-8"))
    return {"original_sha256": hashlib.sha256(before).hexdigest(),
            "patched_sha256": sha(source_path),
            "no_original_lines_modified": True}


def apply_portable_73_one_shot(root, pinned_diff):
    if hashlib.sha256(pinned_diff).hexdigest() != CANARY_73_DIFF_SHA256:
        raise RuntimeError("Phase14 producer patch digest mismatch")
    patch = pinned_diff.decode("utf-8", errors="strict")
    header = "diff --git a/" + GBUFFER_SOURCE + " b/" + GBUFFER_SOURCE + "\n"
    if patch.count("diff --git ") != 1 or not patch.startswith(header):
        raise RuntimeError("Phase14 tried modifying an unexpected source file")
    blocks = re.split(r"(?m)^@@[^\n]*\n", patch)
    if len(blocks) != 3:
        raise RuntimeError("Phase14 requires exactly two audited hunks")
    def changes(block):
        added = [x[1:] for x in block.splitlines()
                 if x.startswith("+") and not x.startswith("+++")]
        removed = [x[1:] for x in block.splitlines()
                   if x.startswith("-") and not x.startswith("---")]
        return added, removed
    first_add, first_remove = changes(blocks[1])
    guard_add, guard_remove = changes(blocks[2])
    old_array = "static constexpr std::array<GoWComputeCanary, 6> gow_canaries{{"
    new_array = "static constexpr std::array<GoWComputeCanary, 7> gow_canaries{{"
    old_permit = "        const bool permit = shape_ok && image_ok && sampler_ok && !cs.uses_dma &&"
    guarded_permit = ("        const bool permit = shape_ok && producer_73_eligible && "
                      "image_ok && sampler_ok && !cs.uses_dma &&")
    special_entry = ("    {0x73ad8e38ULL, 128, 128, 1, 2, 0, 0}, "
                     "// Experimental once, only with strict opt-in")
    if (first_remove != [old_array] or
            first_add != [new_array, special_entry] or
            guard_remove != [old_permit] or
            guard_add[-1] != guarded_permit or
            "GOW_73_ADMISSION shader={:#x}" not in "\n".join(guard_add) or
            "0x1039242d00ULL" not in "\n".join(guard_add) or
            "0x1038bc2b80ULL" not in "\n".join(guard_add) or
            "SHADPS4_GOW_ENABLE_73_ONE_SHOT" not in "\n".join(guard_add)):
        raise RuntimeError("Phase14 violates approved guarded shader scope")
    source_path = root / GBUFFER_SOURCE
    before = source_path.read_bytes()
    original = before.decode("utf-8", errors="strict")
    f2_entry = ("    {0xf2d59856ULL, 1, 1, 1, 5, 0, 0},   "
                "// GPU timeline completed, buffers\n")
    for anchor in (old_array, old_permit + "\n", f2_entry):
        if original.count(anchor) != 1:
            raise RuntimeError("Phase14 ambiguous source anchor: " + anchor)
    if "GOW_73_ADMISSION" in original or new_array in original:
        raise RuntimeError("Phase14 already applied")
    guard_insert = "\n".join(guard_add) + "\n"
    patched = original.replace(old_array, new_array, 1)
    patched = patched.replace(f2_entry, f2_entry + special_entry + "\n", 1)
    patched = patched.replace(old_permit + "\n", guard_insert, 1)
    if (patched.replace(guard_insert, old_permit + "\n", 1)
               .replace(special_entry + "\n", "", 1)
               .replace(new_array, old_array, 1) != original or
            patched.count("GOW_73_ADMISSION") != 1 or
            patched.count("0x73ad8e38ULL, 128, 128, 1, 2, 0, 0") != 1):
        raise RuntimeError("Phase14 modified unrelated emulator source")
    source_path.write_bytes(patched.encode("utf-8"))
    return {"source_before": hashlib.sha256(before).hexdigest(),
            "source_after": sha(source_path),
            "pinned_diff": CANARY_73_DIFF_SHA256,
            "only_one_opted_in_shader": True}


def preflight_patches(patches, temp, report):
    # Every git-apply step runs against exact copies of host files.
    # No installed source file is changed during this preflight.
    staged = temp / "staged-preflight"
    staged.mkdir()
    for rel in sorted(PROTECTED_SOURCES):
        dst = staged / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / rel, dst)
    if len(patches) != 14:
        raise RuntimeError("Expected 13 proven patches plus one strictly gated compute trial")
    for step, patch in enumerate(patches):
        filename = temp / f"pinned-{step}.diff"
        filename.write_bytes(patch)
        if step == 9:
            report["gbuffer_portable_preflight"] = apply_portable_gbuffer_probe(staged, patch)
            continue
        if step == 10:
            report["indirect_gpu_preflight"] = apply_portable_indirect_gpu_probe(staged, patch)
            continue
        if step == 11:
            report["writer_origin_preflight"] = apply_portable_writer_origin_probe(staged, patch)
            continue
        if step == 12:
            report["producer_chain_preflight"] = apply_portable_pre_draw_producer_probe(staged, patch)
            continue
        if step == 13:
            report["one_shot_73_preflight"] = apply_portable_73_one_shot(staged, patch)
            continue
        check = run(["git", "apply", "--check", "--whitespace=nowarn", str(filename)],
                    cwd=staged)
        if check.returncode:
            raise RuntimeError(f"Staged patch {step} rejected: " + check.stderr[-2600:])
        applied = run(["git", "apply", "--whitespace=nowarn", str(filename)], cwd=staged)
        if applied.returncode:
            raise RuntimeError(f"Staged apply {step} failed: " + applied.stderr[-2600:])
    verify_staged_instrumentation(staged)
    report["staged_fourteen_patch_preflight"] = True
    report["stage_source_hashes"] = {
        rel: sha(staged / rel) for rel in sorted(PROTECTED_SOURCES)
    }

def stop_owned(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        group = os.getpgid(proc.pid)
        if group == os.getpgrp():
            raise RuntimeError("Refusing to signal the SSH process group")
        os.killpg(group, signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(group, signal.SIGKILL)
            proc.wait(timeout=8)
    except ProcessLookupError:
        pass

def do_build(build, patches, temp, result):
    backup = temp / "original"
    backup.mkdir()
    originals = {}
    for rel in sorted(PROTECTED_SOURCES):
        src = SOURCE / rel
        if not src.is_file() or src.is_symlink():
            raise RuntimeError(f"Missing or symlinked source: {rel}")
        originals[rel] = (src.read_bytes(), src.stat().st_mode)
    result["source_hashes_before"] = {
        rel: hashlib.sha256(blob).hexdigest() for rel, (blob, _) in originals.items()
    }
    target = build / "shadps4"
    executable_backup = backup / "shadps4"
    had_target = target.is_file()
    if had_target:
        shutil.copy2(target, executable_backup)
        result["build_binary_sha256_before"] = sha(target)
    changed = False
    proc = None
    trial_binary = temp / "trial" / "shadps4"
    try:
        for step, patch in enumerate(patches):
            patch_path = temp / f"pinned-{step}.diff"
            if not patch_path.is_file() or patch_path.read_bytes() != patch:
                raise RuntimeError("Patches do not match staged preflight")
            if step == 9:
                # The same byte-pinned insertion was already validated on
                # isolated host source copies. Source restoration is armed
                # BEFORE the first live file is changed.
                changed = True
                result["gbuffer_portable_live"] = apply_portable_gbuffer_probe(SOURCE, patch)
                continue
            if step == 10:
                changed = True
                result["indirect_gpu_live"] = apply_portable_indirect_gpu_probe(SOURCE, patch)
                continue
            if step == 11:
                changed = True
                result["writer_origin_live"] = apply_portable_writer_origin_probe(SOURCE, patch)
                continue
            if step == 12:
                changed = True
                result["producer_chain_live"] = apply_portable_pre_draw_producer_probe(SOURCE, patch)
                continue
            if step == 13:
                changed = True
                result["one_shot_73_live"] = apply_portable_73_one_shot(SOURCE, patch)
                continue
            check = run(["git", "apply", "--check", "--whitespace=nowarn",
                         str(patch_path)], cwd=SOURCE)
            if check.returncode:
                raise RuntimeError(f"Live patch {step} failed check: " + check.stderr[-2600:])
            changed = True  # Restore exact preimages on any partial apply.
            applied = run(["git", "apply", "--whitespace=nowarn", str(patch_path)], cwd=SOURCE)
            if applied.returncode:
                raise RuntimeError(f"Live patch {step} failed apply: " + applied.stderr[-2600:])
        # Exact hash comparison with staged two-patch output.
        for rel, expected in result["stage_source_hashes"].items():
            if sha(SOURCE / rel) != expected:
                raise RuntimeError(f"Live source differs from staged preview: {rel}")
        command = ["cmake", "--build", str(build), "--target", "shadps4", "--parallel", "4"]
        with (temp / "build.log").open("wb") as logfile:
            proc = subprocess.Popen(command, cwd=SOURCE, stdout=logfile,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
            result["build_pid"] = proc.pid
            result["build_exit_code"] = proc.wait(timeout=900)
        if result["build_exit_code"] != 0:
            raise RuntimeError("Focused local build failed (see build.log)")
        if not target.is_file():
            raise RuntimeError("Build completed but shadps4 binary missing")
        # The Linux CPU-identity loader finds DynamoRIO next to /proc/self/exe.
        # An emulator binary without its matching cpu-id-runtime package cannot
        # reliably start --cpu-id-mode auto. Copy the bundle produced by this build.
        runtime = build / "cpu-id-runtime"
        required_runtime = ("bin64/drrun", "libshadps4_cpu_id.so",
                            "lib64/release/libdynamorio.so")
        missing = [rel for rel in required_runtime if not (runtime / rel).is_file()]
        if missing:
            raise RuntimeError("Bundled CPU identity runtime missing: " + ", ".join(missing))
        trial_binary.parent.mkdir(parents=True)
        shutil.copy2(target, trial_binary)
        shutil.copytree(runtime, trial_binary.parent / "cpu-id-runtime", symlinks=False)
        result["trial_binary_sha256"] = sha(trial_binary)
        result["cpu_runtime_bundled"] = True
        result["cpu_runtime_sha256"] = {
            rel: sha(trial_binary.parent / "cpu-id-runtime" / rel)
            for rel in required_runtime
        }
        return trial_binary
    finally:
        stop_owned(proc)
        # Exact-byte restoration, regardless of build status or interruption.
        if changed:
            for rel, (data, mode) in originals.items():
                path = SOURCE / rel
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".gow-restore-",
                                                 delete=False) as temp_file:
                    os.fchmod(temp_file.fileno(), mode & 0o7777)
                    temp_file.write(data)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                    tmp_path = Path(temp_file.name)
                os.replace(tmp_path, path)
        result["sources_restored"] = all(
            (SOURCE / rel).read_bytes() == data for rel, (data, _) in originals.items()
        )
        if had_target:
            shutil.copy2(executable_backup, target)
            result["build_binary_restored"] = sha(target) == result["build_binary_sha256_before"]
        else:
            if target.exists():
                target.unlink()
            result["build_binary_restored"] = not target.exists()

def validate_spv(directory, evidence, result):
    files = sorted(directory.glob("*57b077ac*.spv"))
    result["spv"] = []
    for item in files:
        data = item.read_bytes()
        valid_header = len(data) >= 20 and len(data) % 4 == 0 and \
                       struct.unpack_from("<I", data, 0)[0] == 0x07230203
        result["spv"].append({"file": item.name, "size": len(data),
                              "sha256": sha(item), "header_valid": valid_header})
        if valid_header:
            for tool in ("spirv-val", "spirv-dis"):
                if not shutil.which(tool):
                    continue
                cmd = ([tool, "--target-env", "vulkan1.3", str(item)]
                       if tool == "spirv-val"
                       else [tool, str(item), "-o", str(evidence / (item.name + ".spvasm"))])
                try:
                    p = run(cmd, timeout=30)
                    (evidence / (item.name + "." + tool + ".txt")).write_text(
                        f"exit={p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
                    result.setdefault("spv_tools", {})[tool] = p.returncode
                except Exception as exc:
                    result.setdefault("spv_tools", {})[tool] = str(exc)

def trial_run(binary, temp, result):
    # Native settings can be rewritten on startup when config versions differ.
    # Preserve the exact user file while retaining the verified native profile.
    config_path = HOME / ".local/share/shadPS4/config.json"
    if config_path.is_symlink() or not config_path.is_file():
        raise RuntimeError("Native config is missing or symlinked; refusing trial")
    original_config = config_path.read_bytes()
    original_mode = config_path.stat().st_mode & 0o7777
    result["native_config_sha256_before"] = hashlib.sha256(original_config).hexdigest()
    evidence = temp / "evidence"
    dump_dir = evidence / "target_shader"
    dump_dir.mkdir(parents=True)
    frame_dir = evidence / "pre_fsr"
    frame_dir.mkdir(parents=True)
    offscreen_dir = evidence / "offscreen"
    offscreen_dir.mkdir(parents=True)
    log_dir = HOME / ".local/share/shadPS4/log"
    before = {}
    if log_dir.is_dir():
        for p in log_dir.glob("*.log"):
            if p.name in ("shadps4.log", GAME + ".log") and p.is_file():
                st = p.stat()
                before[p.name] = (st.st_ino, st.st_size)
    env = os.environ.copy()
    env.pop("XDG_DATA_HOME", None)
    env.pop("XDG_CACHE_HOME", None)
    env.pop("SHADPS4_CPU_ID_RESTART", None)
    env.pop("SHADPS4_CPU_ID_MODE", None)
    env.pop("SHADPS4_GOW_SAFE_COMPUTE_ONESHOT", None)
    env.pop("SHADPS4_GOW_COMPUTE_CANARIES", None)
    env.pop("SHADPS4_GOW_FRAME_SOURCE_DIR", None)
    env.pop("SHADPS4_GOW_OFFSCREEN_DIR", None)
    # Remove inherited GoW diagnostic flags: this test MUST remain focused
    # on one fragment image SHARP and must not enable other compute canaries.
    for key in list(env):
        if key.startswith("SHADPS4_GOW_"):
            env.pop(key, None)
    env.update({
        "SHADPS4_ENABLE_IPC": "false",
        "SHADPS4_GOW_SUPPRESS_GPU_COMPUTE": "1",
        # Retain the previously proven automatic resource dependencies.
        # Their verbose per-shader flattened-buffer trace stays disabled.
        "SHADPS4_GOW_SRT_AUTO_ROOTS": "2",
        # Seven total canary definitions, but the seventh 0x73ad8e38 shader
        # requires its OWN explicit flag, exact grid, descriptor count, and
        # independently verified 32-byte read/512-byte write locations.
        # Everything else remains suppressed. This is an experimental GPU
        # execution and can still trigger a driver hang if the guest shader is
        # unsupported; no unconditional GPU compute is enabled.
        "SHADPS4_GOW_COMPUTE_CANARIES": "1",
        "SHADPS4_GOW_ENABLE_73_ONE_SHOT": "1",
        # Reuse existing tiny, GPU-ordered six-command capture to compare
        # with the 19:53 proven control where instanceCount was zero.
        "SHADPS4_GOW_INDIRECT_GPU_ARGS": "1",
        # No large frame or offscreen readbacks.
        "SHADPS4_GOW_DIAGNOSTIC_GDS_NONEXECUTING": "1",
    })
    env.setdefault("DISPLAY", ":0")
    command = [str(binary), "--cpu-id-mode", "auto", "--game", GAME, "--fullscreen", "true"]
    proc = None
    start = time.monotonic()
    wall_start = dt.datetime.now()
    last_spv = None
    try:
        with (evidence / "console.log").open("wb") as output:
            proc = subprocess.Popen(command, env=env, cwd=temp, stdout=output,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
            result["trial_pid"] = proc.pid
            while time.monotonic() - start < TIME_LIMIT:
                time.sleep(2)
                raw_console = (evidence / "console.log").read_text(errors="replace")
                srt_done = re.search(
                    r"GOW_SRT_FLATTEN_END shader=0x7f710602 [^\n]*result=CAPTURED",
                    raw_console)
                sharp_done = (
                    "GOW_FS_IMAGE_SHARP_END shader=0x7f710602 result=CAPTURED"
                    in raw_console)
                source_png_count = len(list(
                    frame_dir.glob("gow_guest_pre_fsr_*.png")))
                frame6 = "GOW_GRAPHICS_SUMMARY frame=6 " in raw_console
                # Only GPU-completed (not queued) command readbacks qualify.
                # The exact instance-count values were already confirmed
                # from six GPU-completed readbacks in the 19:53 archive.
                # This trial observes their candidate writers only.
                # A/B trial: six tiny GPU-completed argument readbacks.
                completed_slots = set(re.findall(
                    r"GOW_INDIRECT_GPU_CAPTURE slot=([0-5]) [^\n]*"
                    r"result=GPU_READBACK_COMPLETE", raw_console))
                candidate_seen = "GOW_73_ADMISSION shader=0x73ad8e38" in raw_console
                candidate_denied = re.search(
                    r"GOW_73_ADMISSION shader=0x73ad8e38 [^\n]*result=DENIED",
                    raw_console)
                gpu_complete = re.search(
                    r"GOW_COMPUTE_CANARY_GPU_COMPLETE shader=0x73ad8e38 "
                    r"tick=\d+ result=TIMELINE_SIGNALED", raw_console)
                if len(completed_slots) == 6 and candidate_seen and (
                        candidate_denied or gpu_complete or
                        time.monotonic() - start >= 30):
                    result["end_reason"] = (
                        "ONE_SHOT_73_AND_SIX_INDIRECT_ARGS_CAPTURED" if gpu_complete else
                        "ONE_SHOT_73_DENIED_OR_UNCONFIRMED")
                    break
                others = processes_in_use(exclude=(proc.pid,), exclude_group=os.getpgid(proc.pid))
                if others:
                    result["end_reason"] = "ANOTHER_EMULATOR_OR_BUILD_STARTED"
                    result["other_processes"] = others
                    break
                if proc.poll() is not None:
                    result["end_reason"] = "PROCESS_EXIT"
                    break
                if time.monotonic() - start > 45 and "Starting shadps4 emulator" not in raw_console:
                    result["end_reason"] = "EARLY_STARTUP_STALL"
                    try:
                        task_dir = Path("/proc") / str(proc.pid) / "task"
                        result["startup_thread_wchans"] = {
                            t.name: (t / "wchan").read_text().strip()
                            for t in list(task_dir.iterdir())[:48] if t.name.isdigit()
                        }
                    except (OSError, PermissionError) as exc:
                        result["startup_thread_wchans"] = {"error": str(exc)}
                    break
            else:
                result["end_reason"] = "TIME_LIMIT"
    finally:
        stop_owned(proc)
        result["trial_return_code"] = proc.poll() if proc else None
        result["trial_elapsed_s"] = round(time.monotonic() - start, 2)
        try:
            if config_path.read_bytes() != original_config:
                fd, name = tempfile.mkstemp(prefix=".gow-restore-", dir=config_path.parent)
                try:
                    os.fchmod(fd, original_mode)
                    with os.fdopen(fd, "wb") as fp:
                        fp.write(original_config)
                        fp.flush()
                        os.fsync(fp.fileno())
                    os.replace(name, config_path)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
            result["native_config_restored"] = (config_path.read_bytes() == original_config)
        except Exception as exc:
            result["native_config_restored"] = False
            result["native_config_restore_error"] = str(exc)
    # Read-only kernel evidence for a GPU reset/hang; no sudo, driver or
    # display changes. Some Debian accounts cannot read the kernel journal.
    if shutil.which("journalctl"):
        try:
            kernel_log = run(
                ["journalctl", "-k", "--since",
                 wall_start.strftime("%Y-%m-%d %H:%M:%S"),
                 "--no-pager", "-o", "short-iso", "-n", "900"],
                timeout=15)
            if kernel_log.returncode == 0:
                suspicious = [ln for ln in kernel_log.stdout.splitlines()
                              if re.search(r"amdgpu|drm|gpu reset|ring timeout|gpu hang",
                                           ln, re.I)]
                (evidence / "kernel-gpu.txt").write_text("\n".join(suspicious[-200:]))
                result["kernel_gpu_lines"] = len(suspicious)
                result["kernel_gpu_hang_logged"] = any(
                    re.search(r"gpu reset|ring.*timeout|gpu hang|job timed out",
                              ln, re.I) for ln in suspicious)
            else:
                result["kernel_gpu_evidence_unavailable"] = True
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["kernel_gpu_evidence_error"] = str(exc)
    joined = (evidence / "console.log").read_text(errors="replace")
    if log_dir.is_dir():
        for p in log_dir.glob("*.log"):
            if p.name not in ("shadps4.log", GAME + ".log") or not p.is_file():
                continue
            old = before.get(p.name)
            with p.open("rb") as f:
                if old and p.stat().st_ino == old[0] and p.stat().st_size >= old[1]:
                    f.seek(old[1])
                data = f.read()[-16000000:]
            (evidence / ("new-" + p.name)).write_bytes(data)
            joined += data.decode("utf-8", "replace")
    # Remove native ANSI colour codes, which previously broke int parsing.
    joined = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", joined)
    # VideoOut and presenter diagnostics are sourced from game-local logs.
    # The source captures occur before any host FSR / postprocessing pass.
    def _kv(line):
        return dict(re.findall(r"([a-z_][a-z_0-9]*)=([^\s]+)", line))

    # The new opt-in mode applies the SAME planner to all shaders. Every
    # generated SRT walker logs its compact plan; deduplicate mirrored
    # console and game log records and preserve the full census in archive.
    global_plan_rows = []
    global_plan_seen = set()
    for line in joined.splitlines():
        if "GOW_SRT_TOPO_PLAN shader=" not in line:
            continue
        part = line[line.index("GOW_SRT_TOPO_PLAN shader="):]
        if part in global_plan_seen:
            continue
        global_plan_seen.add(part)
        global_plan_rows.append(part)
    (evidence / "srt-topology-all-shaders.txt").write_text(
        "\n".join(global_plan_rows) + "\n")
    global_plan = [_kv(line) for line in global_plan_rows]
    result["srt_topology_shader_plans"] = global_plan
    result["srt_topology_shaders_seen"] = len({
        (r.get("shader", ""), r.get("hw_stage", ""))
        for r in global_plan if r.get("shader")
    })
    result["srt_topology_plans"] = len(global_plan)
    result["srt_topology_reordered"] = sum(
        item.get("reordered") == "true" for item in global_plan)
    result["srt_topology_dependency_edges"] = sum(
        int(item.get("dependency_edges", "0")) for item in global_plan)
    result["srt_topology_ambiguous"] = sum(
        item.get("ambiguous") == "true" for item in global_plan)
    result["srt_topology_cyclic"] = sum(
        item.get("cyclic") == "true" for item in global_plan)
    result["srt_topology_capped"] = sum(
        item.get("capped") == "true" for item in global_plan)
    result["srt_topology_not_enabled"] = sum(
        item.get("enabled") != "true" or item.get("all_shader_trial") != "true"
        for item in global_plan)
    # Recover the bounded, one-shader flattening walk from the existing
    # console log. Keep every failure/assignment event in the evidence file.
    seen_flat_lines = set()
    flatten_lines = []
    for line in joined.splitlines():
        if "GOW_SRT_FLATTEN_" not in line or "shader=0x7f710602" not in line:
            continue
        part = line[line.index("GOW_SRT_FLATTEN_"):]
        if part not in seen_flat_lines:
            seen_flat_lines.add(part)
            flatten_lines.append(part)
    (evidence / "fs-7f710602-flatten-trace.txt").write_text(
        "\n".join(flatten_lines) + "\n")
    flatten_ends = [
        line for line in flatten_lines
        if line.startswith("GOW_SRT_FLATTEN_END") and "result=CAPTURED" in line
    ]
    flat_sum = _kv(flatten_ends[-1]) if flatten_ends else {}
    event_counts = {}
    for line in flatten_lines:
        if not line.startswith("GOW_SRT_FLATTEN_TRACE"):
            continue
        event = _kv(line).get("event", "UNSPECIFIED")
        event_counts[event] = event_counts.get(event, 0) + 1
    result["fs_srt_flatten_complete"] = bool(flatten_ends)
    result["fs_srt_flatten_summary"] = flat_sum
    result["fs_srt_flatten_events"] = event_counts
    result["fs_srt_flatten_trace_lines"] = len(flatten_lines)
    result["fs_srt_flatten_unresolved"] = int(flat_sum.get("unresolved_sharps", "0"))
    result["fs_srt_flatten_resolved"] = int(flat_sum.get("resolved_sharps", "0"))
    result["fs_srt_flatten_truncated"] = flat_sum.get("trace_capped") == "true"
    root_visits = []
    for line in flatten_lines:
        if not line.startswith("GOW_SRT_FLATTEN_TRACE"):
            continue
        kv = _kv(line)
        if kv.get("event") == "ROOT_VISIT" and kv.get("sgpr", "").isdigit():
            root_visits.append(int(kv["sgpr"]))
    topo_lines = []
    for line in joined.splitlines():
        if "GOW_SRT_TOPO_" in line and "shader=0x7f710602" in line:
            item = line[line.index("GOW_SRT_TOPO_"):]
            if item not in topo_lines:
                topo_lines.append(item)
    (evidence / "fs-7f710602-root-topology.txt").write_text(
        "\n".join(topo_lines) + "\n")
    plans = [_kv(v) for v in topo_lines if v.startswith("GOW_SRT_TOPO_PLAN")]
    edges = [_kv(v) for v in topo_lines if v.startswith("GOW_SRT_TOPO_EDGE")]
    root_rows = [_kv(v) for v in topo_lines if v.startswith("GOW_SRT_TOPO_ORDER")]
    topo_plan = plans[-1] if plans else {}
    root_rows = sorted(
        (row for row in root_rows
         if row.get("position", "").isdigit() and row.get("sgpr", "").isdigit()),
        key=lambda row: int(row["position"]))
    planned_roots = [int(row["sgpr"]) for row in root_rows]
    prerequisites = [
        {"dependent": int(edge["dependent_sgpr"]),
         "prerequisite": int(edge["prerequisite_sgpr"])}
        for edge in edges
        if edge.get("dependent_sgpr", "").isdigit() and
           edge.get("prerequisite_sgpr", "").isdigit()
    ]
    valid_plan = (
        topo_plan.get("enabled") == "true"
        and topo_plan.get("reordered") == "true"
        and topo_plan.get("ambiguous") == "false"
        and topo_plan.get("cyclic") == "false"
        and topo_plan.get("capped") == "false")
    dependency_found = {"dependent": 6, "prerequisite": 8} in prerequisites
    correctly_ordered = (
        len(planned_roots) == 3 and len(set(planned_roots)) == 3
        and 8 in planned_roots and 6 in planned_roots
        and planned_roots.index(8) < planned_roots.index(6))
    result["fs_srt_root_order"] = root_visits
    result["fs_srt_auto_plan"] = topo_plan
    result["fs_srt_auto_roots"] = planned_roots
    result["fs_srt_auto_dependencies"] = prerequisites
    result["fs_srt_auto_plan_valid"] = valid_plan
    result["fs_srt_auto_dependency_found"] = dependency_found
    result["fs_srt_auto_proved_order"] = (
        valid_plan and dependency_found and correctly_ordered
        and root_visits == planned_roots)
    result["fs_srt_all_24_resolved"] = (
        result["fs_srt_flatten_complete"]
        and result["fs_srt_auto_proved_order"]
        and result["fs_srt_flatten_resolved"] >= 24
        and result["fs_srt_flatten_unresolved"] == 0
        and not result["fs_srt_flatten_truncated"])

    # Deduplicate duplicated stdout and game-log lines while preserving tree
    # order. Keep full node/literal/edge details in a separate archive file.
    seen_index_lines = set()
    index_lines = []
    for line in joined.splitlines():
        if "GOW_FS_INDEX_" not in line or "shader=0x7f710602" not in line:
            continue
        trimmed = line[line.index("GOW_FS_INDEX_"):]
        if trimmed in seen_index_lines:
            continue
        seen_index_lines.add(trimmed)
        index_lines.append(trimmed)
    (evidence / "fs-7f710602-index-tree.txt").write_text("\n".join(index_lines) + "\n")
    completed = [
        line for line in index_lines
        if line.startswith("GOW_FS_INDEX_GRAPH_END") and "result=CAPTURED" in line]
    parsed_end = _kv(completed[-1]) if completed else {}
    result["fs_index_tree_captured"] = bool(completed)
    result["fs_index_tree_roots"] = [
        line for line in index_lines if line.startswith("GOW_FS_INDEX_ROOT")]
    result["fs_index_tree_summary"] = parsed_end
    result["fs_index_tree_node_count"] = int(parsed_end.get("nodes", "0"))
    result["fs_index_tree_truncated"] = parsed_end.get("truncated") == "true"
    result["fs_index_tree_has_phi"] = int(parsed_end.get("phi", "0")) > 0
    result["fs_index_tree_has_readfirstlane"] = int(parsed_end.get("readfirstlane", "0")) > 0
    result["fs_index_tree_line_count"] = len(index_lines)

    # Per-word origin, rather than 6,572 unscoped SRT failures.
    trace_begin = []
    trace_words = {}
    trace_done = False
    for line in joined.splitlines():
        if "GOW_FS_IMAGE_SHARP_BEGIN shader=0x7f710602" in line:
            trace_begin.append(_kv(line[line.index("GOW_FS_IMAGE_SHARP_BEGIN shader="):]))
        elif "GOW_FS_IMAGE_SHARP_WORD shader=0x7f710602" in line:
            data = _kv(line[line.index("GOW_FS_IMAGE_SHARP_WORD shader="):])
            if data.get("word", "").isdigit():
                trace_words[int(data["word"])] = data
        elif "GOW_FS_IMAGE_SHARP_END shader=0x7f710602 result=CAPTURED" in line:
            trace_done = True
    result["fs_image_sharp_trace_begin"] = trace_begin
    result["fs_image_sharp_trace_words"] = trace_words
    result["fs_image_sharp_trace_complete"] = trace_done and bool(trace_begin)
    result["fs_image_sharp_unknown_words"] = [
        word for word, row in sorted(trace_words.items())
        if row.get("unknown") == "true"
    ]
    result["fs_image_sharp_descriptor_word_count"] = len(trace_words)
    summary_values = [
        item.get("summary") for item in trace_begin
        if item.get("summary") in ("0", "1", "2")
    ]
    result["fs_image_sharp_fetch_summary"] = (
        summary_values[-1] if summary_values else None)
    # SharpFetch::Summary enum: 0=SingleLoad, 1=MultiLoad, 2=Invalid.
    result["fs_image_sharp_valid_after_reorder"] = (
        result["fs_image_sharp_trace_complete"] and
        result["fs_image_sharp_descriptor_word_count"] == 8 and
        not result["fs_image_sharp_unknown_words"] and
        result["fs_image_sharp_fetch_summary"] in ("0", "1"))


    flips = {}
    source_meta = {}
    source_captures = {}
    for line in joined.splitlines():
        if "GOW_FRAME_FLIP sequence=" in line:
            kv = _kv(line[line.index("GOW_FRAME_FLIP sequence="):])
            if kv.get("sequence", "").isdigit():
                flips[int(kv["sequence"])] = kv
        if "GOW_FRAME_SOURCE_META frame=" in line:
            kv = _kv(line[line.index("GOW_FRAME_SOURCE_META frame="):])
            if kv.get("frame", "").isdigit():
                source_meta[int(kv["frame"])] = kv
        if "GOW_FRAME_GUEST_CAPTURE frame=" in line:
            kv = _kv(line[line.index("GOW_FRAME_GUEST_CAPTURE frame="):])
            if kv.get("frame", "").isdigit():
                source_captures[int(kv["frame"])] = kv

    source_pngs = {}
    for path in sorted(frame_dir.glob("gow_guest_pre_fsr_*.png")):
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 20_000_000:
            continue
        with path.open("rb") as fp:
            header = fp.read(24)
        if (len(header) != 24 or not header.startswith(b"\x89PNG\r\n\x1a\n")
                or header[12:16] != b"IHDR"):
            continue
        width, height = struct.unpack(">II", header[16:24])
        source_pngs[path.name] = {
            "width": width, "height": height,
            "size_bytes": path.stat().st_size,
            "sha256": sha(path),
        }
    result["videoout_flip_events"] = list(flips.values())[:16]
    result["videoout_flip_count"] = len(flips)
    result["guest_frame_source_metadata"] = [
        source_meta[key] for key in sorted(source_meta)]
    result["guest_frame_gpu_captures"] = [
        source_captures[key] for key in sorted(source_captures)]
    result["guest_frame_pngs"] = source_pngs
    result["guest_frame_png_count"] = len(source_pngs)
    verified_captures = [
        row for row in source_captures.values()
        if row.get("result") == "SAVED" and
        row.get("file", "").split("/")[-1] in source_pngs]
    result["guest_frame_verified_count"] = len(verified_captures)
    result["guest_frame_nonblack_count"] = sum(
        int(row.get("nonblack_pixels", 0)) > 0 for row in verified_captures)
    if verified_captures:
        result["guest_frame_pixels_status"] = (
            "SOURCE_NONBLACK_BEFORE_HOST_POSTPROCESS"
            if result["guest_frame_nonblack_count"] else
            "SOURCE_BLACK_BEFORE_HOST_POSTPROCESS")
    elif source_meta:
        result["guest_frame_pixels_status"] = "SOURCE_IMAGE_PRESENT_READBACK_INCOMPLETE"
    elif flips:
        result["guest_frame_pixels_status"] = "FLIPS_RECORDED_NO_SOURCE_IMAGE"
    else:
        result["guest_frame_pixels_status"] = "NO_GUEST_FRAME_OBSERVED"
    # Census the other dispatches while allowing only one guarded direct dispatch.
    census_pattern = re.compile(
        r"GOW_COMPUTE_CENSUS_DIRECT shader=(0x[0-9a-fA-F]+) "
        r"grid=(\d+)x(\d+)x(\d+) groups=(\d+) "
        r"buffers=(\d+) images=(\d+) samplers=(\d+) "
        r"invalid_buffers=(\d+) invalid_images=(\d+) invalid_samplers=(\d+) "
        r"uses_dma=(true|false) gds=(true|false) shared=(true|false) "
        r"small_grid=(true|false)")
    census = {}
    for entry in census_pattern.finditer(joined):
        (shader, x, y, z, groups, buffers, images, samplers,
         bad_buffers, bad_images, bad_samplers, dma, gds, shared,
         small_grid) = entry.groups()
        key = (shader, int(x), int(y), int(z))
        if key in census:
            continue  # console and game logs can report the same dispatch
        census[key] = {
            "shader": shader, "grid": [int(x), int(y), int(z)],
            "workgroups": int(groups),
            "buffers": int(buffers), "images": int(images),
            "samplers": int(samplers), "invalid_buffers": int(bad_buffers),
            "invalid_images": int(bad_images), "invalid_samplers": int(bad_samplers),
            "uses_dma": dma == "true", "gds": gds == "true",
            "shared": shared == "true", "small_grid": small_grid == "true",
        }
    result["compute_census"] = list(census.values())
    result["compute_census_count"] = len(census)
    result["small_grid_candidates"] = [
        item for item in census.values()
        if item["small_grid"] and not item["uses_dma"]
        and not item["gds"] and not item["shared"]
        and not (item["invalid_buffers"] or item["invalid_images"]
                 or item["invalid_samplers"])
    ]
    # This means at least one actual guarded command was submitted, not
    # simply that a canary-capable binary was compiled.
    result["compute_execution_enabled"] = bool(re.search(
        r"GOW_COMPUTE_CANARY_RESULT shader=0x[0-9a-fA-F]+ result=SUBMITTED",
        joined))
    canary_specs = (
        ("0x6e9a8b98", "2x1x1", 2, 0, 0),
        ("0xf2d59856", "1x1x1", 5, 0, 0),
        ("0xf875ea48", "1x1x1", 4, 0, 0),
        ("0xd80cbb16", "4x1x1", 1, 1, 0),
        ("0xb223c956", "1x1x1", 1, 2, 1),
        ("0x3b8b91e6", "15x9x1", 1, 2, 0),
    )
    canaries = {}
    for shader, grid, expected_buffers, expected_images, expected_samplers in canary_specs:
        prefix = re.escape(shader)
        candidate = re.search(
            r"GOW_COMPUTE_CANARY_CANDIDATE shader=" + prefix +
            r" grid=" + grid + r"[^\n]*permit=(true|false)", joined)
        recorded = re.search(
            r"GOW_COMPUTE_CANARY_RESULT shader=" + prefix +
            r" result=SUBMITTED grid=" + grid, joined)
        bind_failed = re.search(
            r"GOW_COMPUTE_CANARY_RESULT shader=" + prefix +
            r" result=BIND_FAILED", joined)
        gpu_tick = re.search(
            r"GOW_COMPUTE_CANARY_GPU_TICK shader=" + prefix +
            r" tick=(\d+) result=WAITING_FOR_NORMAL_SUBMISSION", joined)
        completed = re.search(
            r"GOW_COMPUTE_CANARY_GPU_COMPLETE shader=" + prefix +
            r" tick=(\d+) result=TIMELINE_SIGNALED", joined)
        tick = int(gpu_tick.group(1)) if gpu_tick else None
        finished_tick = int(completed.group(1)) if completed else None
        image_guard = re.search(
            r"GOW_COMPUTE_CANARY_CANDIDATE shader=" + prefix +
            r" grid=" + grid +
            r"[^\n]*image_ok=(true|false) image_type=(\d+) "
            r"width=(\d+) height=(\d+) image_written=(true|false) "
            r"image_count_checked=(\d+) sampler_ok=(true|false) "
            r"sampler_count_checked=(\d+) permit=",
            joined)
        canaries[shader] = {
            "grid": grid,
            "expected_buffers": expected_buffers,
            "expected_images": expected_images,
            "expected_samplers": expected_samplers,
            "sampler_guard_ok": (
                image_guard.group(7) == "true" if image_guard else None),
            "checked_image_count": (
                int(image_guard.group(6)) if image_guard else None),
            "checked_sampler_count": (
                int(image_guard.group(8)) if image_guard else None),
            "image_guard_ok": (image_guard.group(1) == "true"
                               if image_guard else None),
            "image_type": int(image_guard.group(2)) if image_guard else None,
            "image_dimensions": (
                [int(image_guard.group(3)), int(image_guard.group(4))]
                if image_guard else None),
            "image_written": (
                image_guard.group(5) == "true" if image_guard else None),
            "candidate_seen": candidate is not None,
            "permit": candidate.group(1) == "true" if candidate else None,
            "command_recorded": bool(recorded),
            "bind_failed": bool(bind_failed),
            "scheduler_tick": tick,
            "completed_tick": finished_tick,
            "timeline_completed": bool(
                recorded and tick is not None and tick == finished_tick),
        }
    # Resource map before the first zero-instance indexed-indirect draw.
    # The 20:10 archive proved late giant-buffer overlaps are not causal for
    # the *first* empty draw, so retain encounter order and exclude late data.
    producer_map = []
    keys_seen = set()
    first_draw_seen = False
    for line in joined.splitlines():
        if "GOW_PRODUCER_FIRST_DRAW address=0x1039242c40" in line:
            first_draw_seen = True
            continue
        if first_draw_seen or "GOW_PRODUCER_RESOURCE shader=" not in line:
            continue
        fragment = line[line.index("GOW_PRODUCER_RESOURCE shader="):]
        fields = _kv(fragment)
        if not (fields.get("address", "").startswith("0x") and
                fields.get("bytes", "").isdigit() and
                fields.get("buffer", "").isdigit()):
            continue
        key = (fields.get("shader"), fields.get("buffer"),
               fields.get("address"), fields.get("bytes"), fields.get("written"))
        if key in keys_seen:
            continue
        keys_seen.add(key)
        fields["capture_order"] = len(producer_map)
        producer_map.append(fields)
    (evidence / "pre-draw-producer-resource-map.txt").write_text(
        "\n".join(" ".join(f"{k}={v}" for k, v in fields.items())
                  for fields in producer_map) + "\n")
    target_shaders = ("0xf2d59856", "0xf875ea48")
    target_inputs = {
        shader: [row for row in producer_map if row.get("shader") == shader]
        for shader in target_shaders
    }
    def intersect(a, b):
        aa, ab = int(a["address"], 16), int(a["bytes"])
        ba, bb = int(b["address"], 16), int(b["bytes"])
        return aa < ba + bb and ba < aa + ab
    possible_edges = {}
    for shader in target_shaders:
        for consume in target_inputs[shader]:
            # Ignore the indirect command target itself: we already know both
            # shaders write its 160-byte descriptor. Find their OTHER inputs.
            if consume["address"] == "0x1039242c40":
                continue
            for produce in producer_map:
                if (produce["capture_order"] >= consume["capture_order"] or
                        produce.get("written") != "true" or
                        produce.get("shader") == shader or
                        not intersect(consume, produce)):
                    continue
                key = (produce.get("shader"), shader,
                       produce["address"], consume["address"])
                possible_edges[key] = {
                    "producer_shader": produce.get("shader"),
                    "producer_buffer": produce.get("buffer"),
                    "consumer_shader": shader,
                    "consumer_buffer": consume.get("buffer"),
                    "producer_address": produce.get("address"),
                    "consumer_address": consume.get("address"),
                    "consumer_writable": consume.get("written") == "true",
                    "producer_gpu_modified_before": produce.get("gpu_modified_before"),
                    "consumer_gpu_modified_before": consume.get("gpu_modified_before"),
                    "proof_level": "ADDRESS_OVERLAP_ONLY",
                }
    result["pre_draw_producer_map"] = producer_map
    result["pre_draw_producer_count"] = len(producer_map)
    result["pre_draw_first_draw_observed"] = first_draw_seen
    result["pre_draw_target_resources"] = target_inputs
    result["pre_draw_possible_input_edges"] = list(possible_edges.values())
    result["pre_draw_possible_input_edge_count"] = len(possible_edges)
    result["pre_draw_target_has_read_only_inputs"] = {
        sh: any(row.get("written") == "false" for row in target_inputs[sh])
        for sh in target_shaders
    }
    result["pre_draw_both_writers_seen"] = all(target_inputs.values())

    # Passive candidate provenance for the six previously confirmed
    # zero-instance commands. Deduplicate console/game-log copies, but
    # preserve candidate buffer addresses and descriptor indices.
    seen_origin = set()
    origin_rows = []
    summary_rows = {}
    candidate_rows = {}
    for line in joined.splitlines():
        for marker in ("GOW_INDIRECT_BUFFER_ORIGIN", "GOW_INDIRECT_WRITER_SUMMARY",
                       "GOW_INDIRECT_WRITER_CANDIDATE"):
            if marker not in line:
                continue
            payload = line[line.index(marker):]
            if payload in seen_origin:
                continue
            seen_origin.add(payload)
            row = _kv(payload)
            if marker == "GOW_INDIRECT_BUFFER_ORIGIN":
                origin_rows.append(row)
            elif marker == "GOW_INDIRECT_WRITER_SUMMARY":
                key = (row.get("shader"), row.get("grid"))
                summary_rows[key] = row
            else:
                key = (row.get("shader"), row.get("buffer"), row.get("address"))
                candidate_rows[key] = row
            break
    (evidence / "indirect-writer-census.txt").write_text(
        "\n".join(sorted(seen_origin)) + "\n")
    result["indirect_writer_origin"] = origin_rows[-1] if origin_rows else None
    result["indirect_writer_gpu_modified"] = (
        origin_rows[-1].get("gpu_modified_before_obtain") == "true"
        if origin_rows else None)
    result["indirect_writer_summaries"] = list(summary_rows.values())
    result["indirect_writer_candidates"] = list(candidate_rows.values())
    result["indirect_writer_summary_count"] = len(summary_rows)
    result["indirect_writer_candidate_count"] = len(candidate_rows)
    result["indirect_writer_special_count"] = sum(
        int(row.get("special_writable", "0")) > 0
        for row in summary_rows.values())
    result["indirect_writer_dma_count"] = sum(
        row.get("uses_dma") == "true" for row in summary_rows.values())
    result["indirect_writer_invalid_writable_count"] = sum(
        int(row.get("invalid_writable", "0")) > 0
        for row in summary_rows.values())

    # Decode only deferred, GPU-completed copies of the same 20-byte VkBuffer
    # arguments passed to drawIndexedIndirect. Never infer from regs.num_indices.
    indirect_queued = {}
    indirect_complete = {}
    indirect_skips = {}
    for line in joined.splitlines():
        if "GOW_INDIRECT_GPU_" not in line:
            continue
        data = line[line.index("GOW_INDIRECT_GPU_"):]
        kv = _kv(data)
        if not kv.get("slot", "").isdigit():
            continue
        slot = int(kv["slot"])
        if not 0 <= slot < 6:
            continue
        if data.startswith("GOW_INDIRECT_GPU_QUEUED"):
            indirect_queued[slot] = kv
        elif data.startswith("GOW_INDIRECT_GPU_CAPTURE") and kv.get("result") == "GPU_READBACK_COMPLETE":
            indirect_complete[slot] = kv
        elif data.startswith("GOW_INDIRECT_GPU_SKIP"):
            indirect_skips[slot] = kv
    result["indirect_gpu_queued"] = [indirect_queued[k] for k in sorted(indirect_queued)]
    result["indirect_gpu_captures"] = [indirect_complete[k] for k in sorted(indirect_complete)]
    result["indirect_gpu_skips"] = [indirect_skips[k] for k in sorted(indirect_skips)]
    result["indirect_gpu_completed_count"] = len(indirect_complete)
    result["indirect_gpu_all_six"] = (
        set(indirect_complete) == set(range(6)) and not indirect_skips)
    result["indirect_gpu_nonzero_draws"] = [
        x for x in result["indirect_gpu_captures"]
        if int(x.get("index_count", "0")) > 0 and
           int(x.get("instance_count", "0")) > 0
    ]
    result["indirect_gpu_zero_index"] = sum(
        int(x.get("index_count", "0")) == 0
        for x in result["indirect_gpu_captures"])
    result["indirect_gpu_zero_instance"] = sum(
        int(x.get("instance_count", "0")) == 0
        for x in result["indirect_gpu_captures"])
    result["indirect_gpu_distinct_addresses"] = len({
        x.get("address", "") for x in result["indirect_gpu_captures"]
    })
    # The earlier, unchanged 19:53 control captured six GPU commands with
    # nonzero indices and zero instances. The only extra compute execution
    # in this treatment is 0x73ad8e38 (and only if all admission guards pass).
    admission = []
    for line in joined.splitlines():
        if "GOW_73_ADMISSION shader=0x73ad8e38" not in line:
            continue
        row = _kv(line[line.index("GOW_73_ADMISSION shader="):])
        if row not in admission:
            admission.append(row)
    admitted = any(row.get("strict_resources") == "true" and
                   row.get("result") == "ELIGIBLE" for row in admission)
    denied = any(row.get("result") == "DENIED" for row in admission)
    submit_marker = re.search(
        r"GOW_COMPUTE_CANARY_RESULT shader=0x73ad8e38 "
        r"result=SUBMITTED grid=128x128x1", joined)
    complete_marker = re.search(
        r"GOW_COMPUTE_CANARY_GPU_COMPLETE shader=0x73ad8e38 "
        r"tick=(\d+) result=TIMELINE_SIGNALED", joined)
    result["producer73_admission"] = admission
    result["producer73_admitted"] = admitted
    result["producer73_denied"] = denied
    result["producer73_submitted"] = bool(submit_marker)
    result["producer73_gpu_completed"] = bool(complete_marker)
    result["producer73_completed_tick"] = (
        int(complete_marker.group(1)) if complete_marker else None)
    result["producer73_exact_grid"] = [128, 128, 1]
    result["producer73_suppression_still_active"] = (
        "GOW_DIAG_COMPUTE_SUPPRESSED" in joined)
    result["producer73_treatment_nonzero_instance_count"] = len(
        result["indirect_gpu_nonzero_draws"])
    result["producer73_baseline_control_zero_instances"] = 6

    # G-buffer draw emission (vs merely binding/render-target preparation).
    # Dedup stdout and game-log copies by the monotonic per-process seq.
    draw_rows = {}
    for line in joined.splitlines():
        if "GOW_GBUFFER_DRAW seq=" not in line:
            continue
        fields = _kv(line[line.index("GOW_GBUFFER_DRAW seq="):])
        if fields.get("seq", "").isdigit() and fields.get("shader") == "0x7f710602":
            draw_rows[int(fields["seq"])] = fields
    draws = [draw_rows[k] for k in sorted(draw_rows)]
    result["gbuffer_draws"] = draws
    result["gbuffer_draws_issued"] = len(draws)
    result["gbuffer_direct_issued"] = sum(row.get("indirect") == "false" for row in draws)
    result["gbuffer_indirect_issued"] = sum(row.get("indirect") == "true" for row in draws)
    result["gbuffer_direct_zero_geometry"] = sum(
        row.get("indirect") == "false" and
        int(row.get("vertex_or_index_count", "0")) == 0 for row in draws)
    result["gbuffer_direct_zero_instances"] = sum(
        row.get("indirect") == "false" and
        int(row.get("instance_count", "0")) == 0 for row in draws)
    result["gbuffer_zero_screen_scissor"] = sum(
        int(row.get("screen_scissor_w", "0")) == 0 or
        int(row.get("screen_scissor_h", "0")) == 0 for row in draws)
    result["gbuffer_zero_render_extent"] = sum(
        int(row.get("render_width", "0")) == 0 or
        int(row.get("render_height", "0")) == 0 for row in draws)
    result["gbuffer_zero_viewport_scale"] = sum(
        float(row.get("vp_xscale", "0")) == 0.0 or
        float(row.get("vp_yscale", "0")) == 0.0 for row in draws)
    result["gbuffer_zero_color_mask"] = sum(
        int(row.get("guest_color_mask", "0"), 0) == 0 for row in draws)
    result["gbuffer_draw_truncated"] = len(draws) >= 128

    # Cumulative graphics-call census covering all draws (not just the first
    # 256). Per-target totals are snapshots at each VideoOut presentation.
    graphics_snapshots = {}
    graphics_targets = {}
    for line in joined.splitlines():
        if "GOW_GRAPHICS_SUMMARY frame=" in line:
            kv = _kv(line[line.index("GOW_GRAPHICS_SUMMARY frame="):])
            if kv.get("frame", "").isdigit():
                graphics_snapshots[kv["frame"]] = kv
        elif "GOW_GRAPHICS_TARGET frame=" in line:
            kv = _kv(line[line.index("GOW_GRAPHICS_TARGET frame="):])
            if kv.get("frame", "").isdigit() and kv.get("address"):
                graphics_targets.setdefault(kv["frame"], {})[kv["address"].lower()] = int(
                    kv.get("prepared_attachment_calls", "0"))
    result["graphics_census_snapshots"] = graphics_snapshots
    result["graphics_target_snapshots"] = graphics_targets
    result["graphics_census_frame6"] = "6" in graphics_snapshots
    result["graphics_census_incomplete"] = any(
        snap.get("target_audit_truncated") == "true"
        for snap in graphics_snapshots.values())
    result["graphics_videoout_prepared_attachment_calls"] = {}
    for f, stats in graphics_snapshots.items():
        videoout_addresses = {
            item["image_address"].lower()
            for item in result.get("guest_frame_source_metadata", [])
            if item.get("image_address")
        }
        match_counts = graphics_targets.get(f, {})
        result["graphics_videoout_prepared_attachment_calls"][f] = sum(
            match_counts.get(addr, 0) for addr in videoout_addresses)
    fragments = {}
    for line in joined.splitlines():
        if "GOW_GRAPHICS_FRAGMENT frame=" not in line:
            continue
        entry = _kv(line[line.index("GOW_GRAPHICS_FRAGMENT frame="):])
        if not (entry.get("frame", "").isdigit() and
                entry.get("shader", "").startswith("0x") and
                entry.get("address", "").startswith("0x")):
            continue
        key = entry["address"].lower() + ":" + entry["shader"].lower()
        fragments.setdefault(entry["frame"], {})[key] = {
            "address": entry["address"].lower(),
            "shader": entry["shader"].lower(),
            "prepared_attachment_calls": int(entry.get("prepared_attachment_calls", "0")),
            "buffers": int(entry.get("buffers", "0")),
            "images": int(entry.get("images", "0")),
            "samplers": int(entry.get("samplers", "0")),
            "invalid_buffers": int(entry.get("invalid_buffers", "0")),
            "invalid_images": int(entry.get("invalid_images", "0")),
            "invalid_samplers": int(entry.get("invalid_samplers", "0")),
        }
    result["fragment_target_snapshots"] = fragments
    result["fragment_provenance_frame6"] = bool(fragments.get("6"))
    result["fragment_target_pair_count_frame6"] = len(fragments.get("6", {}))
    result["fragment_shaders_with_invalid_resources_frame6"] = sorted({
        v["shader"] for v in fragments.get("6", {}).values()
        if any(v[key] for key in ("invalid_buffers", "invalid_images", "invalid_samplers"))
    })
    result["graphics_census_incomplete"] = (
        result["graphics_census_incomplete"] or any(
            snap.get("fragment_audit_truncated") == "true"
            for snap in graphics_snapshots.values()))
    # Read only GPU-completed readback records; printed QUEUED != completed.
    offscreen_lines = {}
    offscreen_skips = {}
    for line in joined.splitlines():
        if "GOW_OFFSCREEN_GPU_CAPTURE label=" in line:
            kv = _kv(line[line.index("GOW_OFFSCREEN_GPU_CAPTURE label="):])
            if kv.get("result") == "GPU_READBACK_COMPLETE" and kv.get("label"):
                offscreen_lines[kv["label"]] = kv
        elif "GOW_OFFSCREEN_READBACK label=" in line:
            kv = _kv(line[line.index("GOW_OFFSCREEN_READBACK label="):])
            if kv.get("result") in ("NOT_CACHED", "GUARD_REJECTED"):
                offscreen_skips[kv.get("label", "unknown")] = kv
    offscreen_files = {}
    for item in sorted(offscreen_dir.glob("gow_offscreen_*")):
        if not item.is_file() or item.is_symlink() or item.stat().st_size > 25_000_000:
            continue
        offscreen_files[item.name] = {
            "bytes": item.stat().st_size, "sha256": sha(item),
        }
    result["offscreen_gpu_captures"] = offscreen_lines
    result["offscreen_skips"] = offscreen_skips
    result["offscreen_files"] = offscreen_files
    result["offscreen_complete_count"] = sum(
        row.get("raw_saved") == "true" and
        ("gow_offscreen_" + name + ".bin") in offscreen_files
        for name, row in offscreen_lines.items())
    result["offscreen_nonzero_count"] = sum(
        int(row.get("nonzero_bytes", "0")) > 0
        for row in offscreen_lines.values())
    # An unchanged, near-zero shading gradient was already captured in the
    # black-screen baseline. Do NOT mistake that for rendered geometry.
    # Compare only frame-six G-buffer and composition readbacks, if confirmed
    # GPU-complete and actually saved, to the earlier all-zero samples.
    scene_labels = ("f06_gbuffer0", "f06_composition")
    scene_samples = {}
    for label in scene_labels:
        row = offscreen_lines.get(label)
        saved = bool(row and row.get("raw_saved") == "true" and
                     ("gow_offscreen_" + label + ".bin") in offscreen_files)
        scene_samples[label] = {
            "gpu_readback_saved": saved,
            "nonzero_bytes": int(row.get("nonzero_bytes", "0")) if saved else None,
            "checksum": row.get("checksum") if saved else None,
            "skipped": offscreen_skips.get(label, {}).get("result"),
        }
    result["scene_target_samples"] = scene_samples
    result["scene_target_nonzero"] = any(
        v["nonzero_bytes"] is not None and v["nonzero_bytes"] > 0
        for v in scene_samples.values())
    result["gbuffer_f06_captured"] = scene_samples["f06_gbuffer0"]["gpu_readback_saved"]
    result["offscreen_status"] = (
        "MULTIFRAME_GPU_CAPTURE_" +
        ("NONZERO_PRESENT" if result["offscreen_nonzero_count"] else "ALL_ZERO")
        if result["offscreen_complete_count"] >= 2
        else "GPU_TARGET_READBACK_INCOMPLETE")
    # GPU-to-host staging readback, ordered before and after the original
    # canary. Changed bytes establish a GPU-side effect, NOT game correctness.
    image_output_pattern = re.compile(
        r"GOW_IMAGE_OUTPUT_DELTA shader=(0x[0-9a-fA-F]+) "
        r"bytes=(\d+) changed_bytes=(\d+) "
        r"before_hash=(0x[0-9a-fA-F]+) after_hash=(0x[0-9a-fA-F]+) "
        r"nonzero_before=(\d+) nonzero_after=(\d+) "
        r"result=GPU_READBACK_COMPLETE")
    output_deltas = {}
    for match in image_output_pattern.finditer(joined):
        (shader, nbytes, changed, before_hash, after_hash,
         nonzero_before, nonzero_after) = match.groups()
        output_deltas[shader.lower()] = {
            "bytes": int(nbytes), "changed_bytes": int(changed),
            "before_hash": before_hash, "after_hash": after_hash,
            "nonzero_before": int(nonzero_before),
            "nonzero_after": int(nonzero_after),
            "gpu_output_changed": int(changed) > 0,
        }
    output_skips = {}
    skip_pattern = re.compile(
        r"GOW_IMAGE_OUTPUT_SKIP shader=(0x[0-9a-fA-F]+) "
        r"reason=([A-Z_]+)")
    for match in skip_pattern.finditer(joined):
        output_skips[match.group(1).lower()] = match.group(2)
    result["gpu_image_output_deltas"] = output_deltas
    result["gpu_image_output_skips"] = output_skips
    result["gpu_image_output_completed_count"] = len(output_deltas)
    result["gpu_image_output_changed_count"] = sum(
        1 for x in output_deltas.values() if x["gpu_output_changed"])
    result["gpu_image_output_targets"] = {
        shader: {"delta": output_deltas.get(shader),
                 "skip": output_skips.get(shader)}
        for shader in ("0xd80cbb16", "0xb223c956", "0x3b8b91e6")
    }
    # The prior A/B control established that D80 was responsible for its
    # image-byte change. This run restores normal D80 dispatch and tests one
    # exact 135-group image workload independently.
    result["d80_no_dispatch_control_requested"] = False
    result["medium_135_target"] = {
        "shader": "0x3b8b91e6", "grid": [15, 9, 1],
        "groups": 135, "buffers": 1, "images": 2, "samplers": 0,
    }
    result["medium_135_output"] = output_deltas.get("0x3b8b91e6")
    result["medium_135_output_skip"] = output_skips.get("0x3b8b91e6")
    result["compute_canaries"] = canaries
    result["compute_canary_completed_count"] = sum(
        candidate["timeline_completed"] for candidate in canaries.values())
    result["compute_canary_all_completed"] = (
        result["compute_canary_completed_count"] == len(canary_specs))
    result["compute_canary_any_submitted"] = any(
        candidate["command_recorded"] for candidate in canaries.values())
    result["gpu_device_lost_logged"] = bool(
        re.search(r"VK_ERROR_DEVICE_LOST|ErrorDeviceLost|device lost|GPU hang",
                  joined, re.IGNORECASE))
    result["dma_info_logged"] = "GOW_TARGET_DMA_INFO" in joined
    result["dma_codegen_logged"] = "GOW_TARGET_DMA_DYNAMIC_CODEGEN" in joined
    result["spv_dump_logged"] = "GOW_TARGET_SPV_DUMP" in joined
    result["compute_suppression_logged"] = "GOW_DIAG_COMPUTE_SUPPRESSED" in joined
    result["bind_probe_logged"] = "GOW_TARGET_BIND_PROBE" in joined
    result["bind_probe_passed"] = bool(re.search(
        r"GOW_TARGET_BIND_PROBE[^\n]*bound=true[^\n]*uses_dma=true[^\n]*"
        r"writes=18[^\n]*bda_valid=true[^\n]*fault_valid=true[^\n]*dispatch=SKIPPED",
        joined))
    result["gds_placeholder_logged"] = "GOW_GDS_DIAG_TRANSLATED_ONLY" in joined
    audit = re.search(
        r"GOW_TARGET_RESOURCE_AUDIT shader=0x57b077ac "
        r"invalid_guest_buffers=(\d+) invalid_images=(\d+) "
        r"invalid_samplers=(\d+) dispatch=SKIPPED", joined)
    result["resource_audit_logged"] = bool(audit)
    result["resource_audit_counts"] = (
        dict(zip(("guest_buffers", "images", "samplers"),
                 (int(value) for value in audit.groups())))
        if audit else None)
    result["resource_audit_passed"] = (
        all(value == 0 for value in result["resource_audit_counts"].values())
        if audit else False)
    if result["resource_audit_counts"] and not result["resource_audit_passed"]:
        result["resource_integrity_warning"] = (
            "Some guest descriptor sources are unresolved; GPU dispatch must remain disabled.")
    # The IR's IMul/IAdd calculate byte displacements, then SHR 2 supplies
    # ReadConst's dword index. Accept only the corrected byte-address probe.
    table_pattern = re.compile(
        r"GOW_IMAGE_TABLE_AUDIT group=(A|B) stride_bytes=(\d+) "
        r"image_offset_bytes=(\d+) mask_dw=(\d+) bound_dw=(\d+) "
        r"raw_bound=(\d+) bounded_limit=(\d+) slots=32 "
        r"sampled=(\d+) populated=(\d+) type_valid=(\d+) "
        r"sampled_mask=(0x[0-9a-fA-F]+) populated_mask=(0x[0-9a-fA-F]+) "
        r"valid_mask=(0x[0-9a-fA-F]+) address_mask=(0x[0-9a-fA-F]+) "
        r"selected_mask=(0x[0-9a-fA-F]+) "
        r"bounded_selection=(0x[0-9a-fA-F]+) "
        r"selected_valid_mask=(0x[0-9a-fA-F]+) "
        r"selected_nonzero_addr_mask=(0x[0-9a-fA-F]+) "
        r"selected_unresolved_mask=(0x[0-9a-fA-F]+) "
        r"valid_type_mask=(0x[0-9a-fA-F]+) "
        r"selected_type_mask=(0x[0-9a-fA-F]+) "
        r"mask_source_nonzero=(true|false) "
        r"bound_source_nonzero=(true|false) dispatch=SKIPPED")
    table_audits = {}
    for match in table_pattern.finditer(joined):
        (group, stride_bytes, image_offset_bytes, mask_dw, bound_dw,
         raw_bound, bounded_limit, sampled, populated, type_valid,
         sampled_mask, populated_mask, valid_mask, address_mask,
         selected_mask, bounded_selection, selected_valid_mask,
         selected_nonzero_addr_mask, selected_unresolved_mask,
         valid_type_mask, selected_type_mask, mask_source_nonzero,
         bound_source_nonzero) = match.groups()
        table_audits[group] = {
            "stride_bytes": int(stride_bytes),
            "image_offset_bytes": int(image_offset_bytes),
            "mask_dw": int(mask_dw),
            "bound_dw": int(bound_dw),
            "raw_bound": int(raw_bound),
            "bounded_limit": int(bounded_limit),
            "slots": 32,
            "sampled": int(sampled),
            "populated": int(populated),
            "type_valid": int(type_valid),
            "sampled_mask": sampled_mask,
            "populated_mask": populated_mask,
            "valid_mask": valid_mask,
            "address_mask": address_mask,
            "selected_mask": selected_mask,
            "bounded_selection": bounded_selection,
            "selected_valid_mask": selected_valid_mask,
            "selected_nonzero_addr_mask": selected_nonzero_addr_mask,
            "selected_unresolved_mask": selected_unresolved_mask,
            "valid_type_mask": valid_type_mask,
            "selected_type_mask": selected_type_mask,
            "mask_source_nonzero": mask_source_nonzero == "true",
            "bound_source_nonzero": bound_source_nonzero == "true",
            "selected_type_mixed": int(selected_type_mask, 16).bit_count() > 1,
            "bounded_selected_count": int(bounded_selection, 16).bit_count(),
            "selected_unresolved_count": int(selected_unresolved_mask, 16).bit_count(),
            "selected_nonzero_texture_count": int(selected_nonzero_addr_mask, 16).bit_count(),
        }
    result["image_table_audits"] = table_audits
    result["image_table_audit_complete"] = set(table_audits) == {"A", "B"}
    result["image_table_audit_expected_layout"] = (
        table_audits.get("A", {}).get("stride_bytes") == 776 and
        table_audits.get("A", {}).get("image_offset_bytes") == 544 and
        table_audits.get("A", {}).get("mask_dw") == 5899 and
        table_audits.get("A", {}).get("bound_dw") == 0 and
        table_audits.get("B", {}).get("stride_bytes") == 264 and
        table_audits.get("B", {}).get("image_offset_bytes") == 3536 and
        table_audits.get("B", {}).get("mask_dw") == 5900 and
        table_audits.get("B", {}).get("bound_dw") == 1)
    result["image_live_masks_captured"] = (
        result["image_table_audit_complete"] and
        all(item["mask_source_nonzero"] and item["bound_source_nonzero"]
            for item in table_audits.values()))
    result["image_selected_slot_counts"] = {
        group: {
            "candidate_count": item["bounded_selected_count"],
            "type_invalid_count": item["selected_unresolved_count"],
            "nonzero_texture_count": item["selected_nonzero_texture_count"],
            "mixed_image_types": item["selected_type_mixed"],
            "index_upper_bound": item["bounded_limit"],
        }
        for group, item in table_audits.items()
    }
    relevant = [line[:1600] for line in joined.splitlines() if
                re.search(r"GOW_|failed|error|shader 0x57b077ac|Vulkan|CPU identity", line, re.I)]
    (evidence / "key-events.txt").write_text("\n".join(relevant[-3500:]))
    validate_spv(dump_dir, evidence, result)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    archive = HOME / ("gow-phi-dma-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                      + ".tar.gz")
    report = {"branch_head": HEAD_SHA, "baseline": BASE_SHA,
              "source": str(SOURCE), "installed_emulator_modified": False,
              "native_config_modified_by_helper": False, "user_session_preserved": True}
    lock_path = HOME / ".cache/gow-phi-dma-diagnostic.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock, tempfile.TemporaryDirectory(prefix="gow-phi-", dir=HOME) as tmp:
        tmp = Path(tmp)
        evidence = tmp / "evidence"
        evidence.mkdir()
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            ignored_launchers = []
            busy = processes_in_use(ignored_launchers=ignored_launchers)
            report["ignored_shell_launchers"] = ignored_launchers
            if busy:
                report["busy_processes"] = busy
                raise RuntimeError("Another real emulator/build process active; trial refused")
            if not SOURCE.is_dir() or not (SOURCE / ".git").exists():
                raise RuntimeError("Verified shadPS4 source tree is unavailable")
            check = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=SOURCE)
            if check.returncode or check.stdout.strip():
                raise RuntimeError("Verified source has modifications; refusing concurrent changes")
            build = find_build()
            report["build_directory"] = str(build)
            report["verified_source_hashes"] = verify_preimages()
            patches = get_patches()
            for step, patch in enumerate(patches):
                (evidence / f"pinned-{step}.diff").write_bytes(patch)
            report["patch_sha256"] = [hashlib.sha256(p).hexdigest() for p in patches]
            preflight_patches(patches, tmp, report)
            report["patch_applies"] = True
            if args.preflight_only:
                report["result"] = "PREFLIGHT_PASS"
            else:
                trial = do_build(build, patches, tmp, report)
                shutil.copy2(tmp / "build.log", evidence / "build.log")
                if not report.get("sources_restored") or not report.get("build_binary_restored"):
                    raise RuntimeError("Source/build restore verification failed; no game launched")
                report["result"] = "BUILD_PASS"
                trial_run(trial, tmp, report)
                report["result"] = report.get("end_reason", "TRIAL_COMPLETE")
                if report.get("resource_audit_logged") and not report.get("resource_audit_passed"):
                    report["result"] = (
                        "DYNAMIC_IMAGE_MASKS_CAPTURED"
                        if report.get("image_live_masks_captured")
                        else "DYNAMIC_IMAGE_MASKS_NOT_CAPTURED")
                if report.get("gpu_device_lost_logged") or report.get("kernel_gpu_hang_logged"):
                    report["result"] = "GPU_FAULT_EVIDENCE"
                elif report.get("producer73_denied"):
                    report["result"] = "PRODUCER73_EXACT_GUARD_DENIED"
                elif not report.get("producer73_admitted"):
                    report["result"] = "PRODUCER73_ADMISSION_NOT_OBSERVED"
                elif not report.get("producer73_submitted"):
                    report["result"] = "PRODUCER73_GPU_NOT_SUBMITTED"
                elif not report.get("producer73_gpu_completed"):
                    report["result"] = "PRODUCER73_GPU_COMPLETION_UNCONFIRMED"
                elif not report.get("indirect_gpu_all_six"):
                    report["result"] = "PRODUCER73_GPU_ARGUMENT_READBACK_INCOMPLETE"
                elif report.get("producer73_treatment_nonzero_instance_count"):
                    report["result"] = "PRODUCER73_RESTORED_NONZERO_INSTANCES"
                elif report.get("indirect_gpu_zero_instance", 0) == 6:
                    report["result"] = "PRODUCER73_COMPLETED_ALL_INSTANCES_STILL_ZERO"
                else:
                    report["result"] = "PRODUCER73_MIXED_INSTANCE_COUNTS"

        except KeyboardInterrupt:
            report["result"] = "INTERRUPTED"
        except Exception as exc:
            report["result"] = "FAIL"
            report["error"] = str(exc)
            report["traceback"] = traceback.format_exc()[-9000:]
        finally:
            if (tmp / "build.log").is_file() and not (evidence / "build.log").exists():
                shutil.copy2(tmp / "build.log", evidence / "build.log")
            report["source_tree_clean_after"] = (
                run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=SOURCE).stdout.strip() == ""
                if SOURCE.is_dir() and (SOURCE / ".git").exists() else None)
            (evidence / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            with tarfile.open(archive, "w:gz") as result_archive:
                for p in evidence.rglob("*"):
                    if p.is_file() and p.stat().st_size < 32_000_000:
                        result_archive.add(p, arcname=str(p.relative_to(tmp)))
    print("GOW_SRT_FLATTEN_RESULT=" + report.get("result", "UNKNOWN"))
    print("PRODUCER73_ADMISSION=" + str(report.get("producer73_admission", [])))
    print("PRODUCER73_SUBMITTED=" + str(report.get("producer73_submitted", False)))
    print("PRODUCER73_GPU_COMPLETED=" + str(report.get("producer73_gpu_completed", False)))
    print("INDIRECT_GPU_SIX_CAPTURED=" + str(report.get("indirect_gpu_all_six", False)))
    print("BASELINE_ZERO_INSTANCE_COMMANDS=6")
    print("TREATMENT_NONZERO_INSTANCE_COMMANDS=" +
          str(report.get("producer73_treatment_nonzero_instance_count", 0)))
    print("TREATMENT_ZERO_INSTANCE_COMMANDS=" +
          str(report.get("indirect_gpu_zero_instance", 0)))
    print("TREATMENT_GPU_COMMANDS=" + str(report.get("indirect_gpu_captures", [])))
    print("ARCHIVE=" + str(archive))
    print("SRT_TRACE_COMPLETE=" + str(report.get("fs_srt_flatten_complete", False)))
    print("SRT_ROOT_ORDER=" + str(report.get("fs_srt_root_order", [])))
    print("IMAGE_SHARP_VALID=" + str(report.get("fs_image_sharp_valid_after_reorder", False)))
    print("IMAGE_SHARP_SUMMARY=" + str(report.get("fs_image_sharp_fetch_summary")))
    print("IMAGE_SHARP_UNRESOLVED_WORDS=" + str(report.get("fs_image_sharp_unknown_words", [])))
    print("SRT_AUTO_ROOTS=" + str(report.get("fs_srt_auto_roots", [])))
    print("SRT_AUTO_DEPENDENCIES=" + str(report.get("fs_srt_auto_dependencies", [])))
    print("SRT_AUTO_TOPO_PROVEN=" + str(report.get("fs_srt_auto_proved_order", False)))
    print("SRT_TOPO_SHADERS=" + str(report.get("srt_topology_shaders_seen", 0)))
    print("GUARDED_CANARIES_SUBMITTED=" + str(sum(
        bool(x.get("command_recorded")) for x in report.get("compute_canaries", {}).values())))
    print("GUARDED_CANARIES_GPU_COMPLETED=" + str(report.get("compute_canary_completed_count", 0)))
    print("GUARDED_CANARIES_DETAILS=" + str(report.get("compute_canaries", {})))
    print("GPU_TARGET_READBACKS=" + str(report.get("offscreen_complete_count", 0)))
    print("GPU_TARGET_NONZERO=" + str(report.get("offscreen_nonzero_count", 0)))
    print("GPU_TARGET_SKIPS=" + str(report.get("offscreen_skips", {})))
    print("SCENE_TARGET_SAMPLES=" + str(report.get("scene_target_samples", {})))
    print("SCENE_TARGET_NONZERO=" + str(report.get("scene_target_nonzero", False)))
    print("GPU_IMAGE_OUTPUT_DELTAS=" + str(report.get("gpu_image_output_deltas", {})))
    print("SRT_TOPO_REORDERED=" + str(report.get("srt_topology_reordered", 0)))
    print("SRT_TOPO_EDGES=" + str(report.get("srt_topology_dependency_edges", 0)))
    print("SRT_TOPO_AMBIGUOUS=" + str(report.get("srt_topology_ambiguous", 0)))
    print("SRT_TOPO_CYCLIC=" + str(report.get("srt_topology_cyclic", 0)))
    print("SRT_TOPO_CAPPED=" + str(report.get("srt_topology_capped", 0)))
    print("VIDEOOUT_FLIPS=" + str(report.get("videoout_flip_count", 0)))
    print("SOURCE_FRAMES=" + str(report.get("guest_frame_verified_count", 0)))
    print("SOURCE_NONBLACK=" + str(report.get("guest_frame_nonblack_count", 0)))
    print("SRT_RESOLVED_SHARPS=" + str(report.get("fs_srt_flatten_resolved", 0)))
    print("SRT_UNRESOLVED_SHARPS=" + str(report.get("fs_srt_flatten_unresolved", 0)))
    print("SRT_EVENTS=" + str(report.get("fs_srt_flatten_events", {})))
    print("SRT_TRACE_CAPPED=" + str(report.get("fs_srt_flatten_truncated", False)))
    print("BUILD_EXIT_CODE=" + str(report.get("build_exit_code")))
    print("SOURCE_RESTORED=" + str(report.get("sources_restored")))
    print("BUILD_BINARY_RESTORED=" + str(report.get("build_binary_restored")))
    print("NATIVE_CONFIG_RESTORED=" + str(report.get("native_config_restored")))
    if report.get("error"):
        print("ERROR=" + report["error"])
    if report.get("result") in ("FAIL", "INTERRUPTED", "FS_SRT_FLATTEN_TRACE_MISSING"):
        raise SystemExit(1)

if __name__ == "__main__":
    main()
