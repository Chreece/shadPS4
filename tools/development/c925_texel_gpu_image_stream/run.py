#!/usr/bin/env python3
"""C925 isolated candidate: bypass stale CPU stream for GPU-owned texel image aliases.

Upstream reasoning: shadps4-emu/shadPS4#5467.
Reuses the verified direct-compile, 8 GiB VRAM/Ollama, 118s+2s runtime,
private game profile, universal diary and exact source/object/ELF restoration.
"""
from __future__ import annotations
import hashlib
import re
import sys
from pathlib import Path

P = Path.home() / "projects/shadps4"
BASE_RUNNER = P / "scripts/got-raw-image-coherence.py"
EXPECTED_BASE_RUNNER_SHA = "df271dc83e033277b2e3efe8bc050b04907c3be35b5cdc5585e112defc1f497c"
MARK = "TEXEL_GPU_IMAGE_STREAM_BYPASS_V1"


def patch_cpp(src: str) -> str:
    begin = "std::pair<const Buffer*, u64> BufferCache::ObtainBuffer("
    end = "std::pair<const Buffer*, u64> BufferCache::ObtainBufferForImage("
    if src.count(begin) != 1 or src.count(end) != 1:
        raise RuntimeError("TEXEL_OBTAIN_FUNCTION_ANCHORS_CHANGED")
    a, b = src.index(begin), src.index(end, src.index(begin))
    if a >= b:
        raise RuntimeError("TEXEL_FUNCTION_ORDER_INVALID")
    body = src[a:b]
    if MARK in src:
        raise RuntimeError("TEXEL_FIX_ALREADY_APPLIED")
    if body.count("SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);") != 1:
        raise RuntimeError("TEXEL_ARENA_SYNC_NOT_FOUND")
    if "FindImageFromRange(device_addr, size)" in body:
        raise RuntimeError("TEXEL_SOURCE_ALREADY_HAS_IMAGE_ALIAS_LOGIC")

    guard = re.compile(r"if\s*\(([^{};]*?)\)\s*\{", re.S)
    gates = [m for m in guard.finditer(body)
             if all(token in m.group(1) for token in
                    ("!is_written", "!skip_stream_buffer", "size <= STREAM_THRESHOLD",
                     "!IsRegionGpuModified(device_addr, size)"))]
    if len(gates) != 1:
        raise RuntimeError("TEXEL_STREAM_FASTPATH_NOT_UNIQUE_" + str(len(gates)))
    found = gates[0]
    condition = found.group(1).strip()
    if "texel_gpu_image_alias" in condition:
        raise RuntimeError("TEXEL_FIX_ALREADY_PRESENT")

    prefix = """// TEXEL_GPU_IMAGE_STREAM_BYPASS_V1: formatted reads must not fetch stale CPU
    // bytes when an existing GPU-written image owns the guest address.
    const ImageId texel_image = is_texel_buffer && size != 0
                                    ? texture_cache.FindImageFromRange(device_addr, size)
                                    : ImageId{};
    const bool texel_gpu_image_alias = static_cast<bool>(texel_image);
    if (texel_gpu_image_alias && !is_written && !skip_stream_buffer &&
        size <= STREAM_THRESHOLD && !IsRegionGpuModified(device_addr, size)) {
        static std::atomic<u32> bypasses{};
        const u32 n = bypasses.fetch_add(1, std::memory_order_relaxed) + 1;
        if (n <= 4 || (n & (n - 1)) == 0) {
            LOG_INFO(Render_Vulkan, "TEXEL_GPU_IMAGE_STREAM_BYPASS event={} bytes={}", n, size);
        }
    }
    """
    updated = "if (!texel_gpu_image_alias && " + condition + ") {"
    body = body[:found.start()] + prefix + updated + body[found.end():]
    out = src[:a] + body + src[b:]
    if "#include <atomic>" not in out:
        header = "#include <algorithm>"
        if out.count(header) != 1:
            raise RuntimeError("TEXEL_ATOMIC_INCLUDE_ANCHOR_CHANGED")
        out = out.replace(header, "#include <atomic>\n" + header, 1)
    if out.count(MARK) != 1 or out.count("TEXEL_GPU_IMAGE_STREAM_BYPASS event") != 1:
        raise RuntimeError("TEXEL_PATCH_OUTPUT_NOT_UNIQUE")
    return out


def tests() -> None:
    fixture = """#include <algorithm>
std::pair<const Buffer*, u64> BufferCache::ObtainBuffer(VAddr device_addr, u32 size,
                                                        bool is_written, bool is_texel_buffer) {
    const bool skip_stream_buffer = True(flags & ObtainBufferFlags::IgnoreStreamBuffer);
    if (!is_written && !skip_stream_buffer && size <= STREAM_THRESHOLD &&
        !IsRegionGpuModified(device_addr, size)) {
        return {&stream_buffer, 0};
    }
    SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);
    return {arena, arena->Offset(device_addr)};
}
std::pair<const Buffer*, u64> BufferCache::ObtainBufferForImage(VAddr a, u32 b) {
    return {};
}
"""
    for base in (fixture, fixture.replace("if (!is_written", "if( !is_written")):
        result = patch_cpp(base)
        assert "if (!texel_gpu_image_alias && !is_written && !skip_stream_buffer" in result.replace("\n", " ")
        assert result.count("SynchronizeMemory(arena, device_addr, size, is_written, is_texel_buffer);") == 1
        assert result.count("FindImageFromRange(device_addr, size)") == 1
        assert result.count("#include <atomic>") == 1
        try:
            patch_cpp(result)
        except RuntimeError as exc:
            assert str(exc) == "TEXEL_FIX_ALREADY_APPLIED"
        else:
            raise AssertionError("DUPLICATE_TEXEL_PATCH_ACCEPTED")
    for is_texel, has_image, expected in ((False, True, False), (True, False, False),
                                          (True, True, True)):
        assert bool(is_texel and has_image) == expected
    print("TEXEL_STREAM_BYPASS_FIXTURE_TESTS=PASS", flush=True)


def main() -> int:
    tests()
    if not BASE_RUNNER.is_file() or BASE_RUNNER.is_symlink():
        raise RuntimeError("PRIOR_SUCCESSFUL_REVERSIBLE_RUNNER_MISSING")
    data = BASE_RUNNER.read_bytes()
    if hashlib.sha256(data).hexdigest() != EXPECTED_BASE_RUNNER_SHA:
        raise RuntimeError("PRIOR_RUNNER_SHA_CHANGED_ABORT")
    code = data.decode()
    updates = {
        "Synchronize GPU-rendered image aliases before raw buffer reads":
            "Avoid CPU stream-buffer reads of GPU-owned texel images",
        "raw-gpu-image-sync-": "texel-image-stream-bypass-",
        "https://github.com/shadps4-emu/shadPS4/pull/5280":
            "https://github.com/shadps4-emu/shadPS4/pull/5467",
    }
    for before, after in updates.items():
        if before not in code:
            raise RuntimeError("BASE_RUNNER_METADATA_NOT_FOUND:" + before)
        code = code.replace(before, after)
    ns = {"__name__": "c925_texel_candidate", "__file__": str(BASE_RUNNER)}
    exec(compile(code, str(BASE_RUNNER), "exec"), ns)
    ns["patch_cpp"] = patch_cpp
    ns["tests"] = tests
    print("REUSE=LAST_SUCCESSFUL_REVERSIBLE_BUILD_RUNNER", flush=True)
    print("VRAM=MINIMUM_8_GiB_OLLAMA_CONDITIONAL_UNLOAD_RESTORE", flush=True)
    print("GAME=GOT_MENU_MAX_120s_NO_INPUT", flush=True)
    print("RESTORE=PROVEN_SOURCE_OBJECT_ELF", flush=True)
    print("DIARY=UNIVERSAL", flush=True)
    return ns["main"]()


if __name__ == "__main__":
    sys.exit(main())
