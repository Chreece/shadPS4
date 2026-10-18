#!/usr/bin/env python3
"""Safe 64-KiB read-only stream overlay on user's *actual* pinned GoW source.

Never replace buffer_cache.cpp wholesale with another upstream revision.
Preserve every pre-existing graphics and memory correction byte-for-byte.
"""
from __future__ import annotations
import difflib
import hashlib
from pathlib import Path
import re
import sys

ACTUAL_PROVEN_BUFFER_SHA256 = "2b8d0e35f45e18ecbfc88ef153665d3e9d713638b1fa988e505ef633a8cc71e0"
INCLUDE = '#include "video_core/buffer_cache/memory_tracker.h"'
NEW_INCLUDE = '#include "video_core/buffer_cache/read_only_stream_policy.h"'
EXTENDED_ENV = "SHADPS4_STREAM_READONLY_64K"
# The source's existing condition and all following sparse-residency logic
# are left intact. Only its limit selection is extended when explicitly on.
MODERN_CONDITION = (
    "    if (!is_written && !skip_stream_buffer && size <= STREAM_THRESHOLD &&\n"
    "        !IsRegionGpuModified(device_addr, size)) {"
)
LEGACY_CONDITION = (
    "    if (!is_written && size <= STREAM_THRESHOLD && "
    "!IsRegionGpuModified(device_addr, size)) {"
)
LOG_ANCHOR = (
    "        const auto [data, offset] = "
    "stream_buffer.Map(size, instance.UniformMinAlignment());"
)

def exactly_once(s: str, token: str, description: str) -> None:
    if s.count(token) != 1:
        raise ValueError(f"{description}_MATCHES_{s.count(token)}")

def transform(source: str) -> str:
    if EXTENDED_ENV in source or NEW_INCLUDE in source:
        raise ValueError("PATCH_ALREADY_PRESENT")
    if source.count(MODERN_CONDITION) == 1:
        condition = MODERN_CONDITION
        candidate = "size, is_written, skip_stream_buffer"
        mode = "MODERN_FLAGS"
    elif source.count(LEGACY_CONDITION) == 1:
        condition = LEGACY_CONDITION
        candidate = "size, is_written, false"
        mode = "LEGACY_BOOLS"
    else:
        raise ValueError("ACTUAL_PROTECTED_OBTAIN_BUFFER_CONDITION_NOT_SUPPORTED")

    exactly_once(source, INCLUDE, "MEMORY_TRACKER_INCLUDE")
    exactly_once(source, LOG_ANCHOR, "STREAM_MAP_ANCHOR")
    # Do not change the GPU-writable path, arena allocation or sync flags.
    main_method = source.find("BufferCache::ObtainBuffer(")
    following_method = source.find("BufferCache::ObtainBufferForImage(")
    if not (0 <= main_method < following_method):
        raise ValueError("OBTAIN_BUFFER_METHOD_BOUNDS_CHANGED")
    if source.index(condition) not in range(main_method, following_method):
        raise ValueError("STREAM_CONDITION_NOT_IN_OBTAIN_BUFFER")
    if source.index(LOG_ANCHOR) not in range(main_method, following_method):
        raise ValueError("STREAM_MAP_NOT_IN_OBTAIN_BUFFER")

    updated = source.replace(INCLUDE, INCLUDE + "\n" + NEW_INCLUDE, 1)
    if "#include <cstdlib>" not in updated:
        # Minimal include insertion; avoid resetting other includes.
        exactly_once(updated, "#include <algorithm>", "ALGORITHM_INCLUDE")
        updated = updated.replace("#include <algorithm>",
                                  "#include <algorithm>\n#include <cstdlib>", 1)

    extended = (
        "    // General opt-in host stream expansion: never stream GPU-writable,\n"
        "    // GPU-dirty or explicit IgnoreStreamBuffer resources. Default 16 KiB.\n"
        "    static const u32 readonly_stream_limit = [] {\n"
        '        const char* flag = std::getenv("SHADPS4_STREAM_READONLY_64K");\n'
        "        const bool extended = flag && flag[0] == '1' && flag[1] == '\\0';\n"
        "        return ReadOnlyStreamPolicy::Limit(extended);\n"
        "    }();\n"
        f"    if (ReadOnlyStreamPolicy::IsCandidate({candidate}, readonly_stream_limit) &&\n"
        "        !IsRegionGpuModified(device_addr, size)) {"
    )
    updated = updated.replace(condition, extended, 1)
    log = (
        "        // Report actual extended-route use at exponential intervals,\n"
        "        // without introducing per-buffer logging overhead.\n"
        "        if (size > STREAM_THRESHOLD) {\n"
        "            static thread_local u64 hits = 0;\n"
        "            const u64 seen = ++hits;\n"
        "            if ((seen & (seen - 1)) == 0) {\n"
        '                LOG_WARNING(Render, "READONLY_STREAM_64K_ACTIVE hits={} size={}", seen, size);\n'
        "            }\n"
        "        }\n"
        + LOG_ANCHOR
    )
    updated = updated.replace(LOG_ANCHOR, log, 1)
    if updated.count(EXTENDED_ENV) != 1 or updated.count("READONLY_STREAM_64K_ACTIVE") != 1:
        raise ValueError("STREAM64_PROBE_NOT_UNIQUE")
    if updated.count("IsRegionGpuModified(device_addr, size)") != source.count(
            "IsRegionGpuModified(device_addr, size)"):
        raise ValueError("GPU_DIRTY_STATE_GUARD_WAS_CHANGED")
    # Check the sparse-resident path, readback and every following method are
    # completely identical to the proven original.
    sparse_anchor = "    const u64 first_block = device_addr >> block_shift;"
    sparse_pos = source.find(sparse_anchor, main_method, following_method)
    patched_sparse_pos = updated.find(sparse_anchor, updated.find("BufferCache::ObtainBuffer("))
    if (sparse_pos < 0 or patched_sparse_pos < 0 or
            updated[patched_sparse_pos:] != source[sparse_pos:]):
        raise ValueError("SPARSE_RESIDENCY_OR_FOLLOWING_METHOD_CHANGED")
    if (updated.count("memory->CopySparseMemory(device_addr, data, size)") !=
        source.count("memory->CopySparseMemory(device_addr, data, size)")):
        raise ValueError("STREAM_DATA_COPY_SEMANTICS_CHANGED")
    print(f"PINNED_BUFFER_CACHE_ABI={mode}", flush=True)
    return updated

def selftest() -> None:
    fixture_prefix = (
        '#include <algorithm>\n' + INCLUDE + '\n'
        + "std::pair<const Buffer*, u64> BufferCache::ObtainBuffer(...) {\n"
        + "    const bool is_written = false;\n"
        + "    const bool skip_stream_buffer = false;\n"
    )
    fixture_suffix = (
        "\n" + LOG_ANCHOR + "\n"
        "        memory->CopySparseMemory(device_addr, data, size);\n"
        "    }\n"
        "    const u64 first_block = device_addr >> block_shift;\n"
        "    KeepSparseAndGpuSemanticsUnchanged();\n"
        "}\nstd::pair<const Buffer*, u64> BufferCache::ObtainBufferForImage(...) {}\n"
    )
    for mode, condition in (("MODERN", MODERN_CONDITION),
                            ("LEGACY", LEGACY_CONDITION)):
        original = fixture_prefix + condition + fixture_suffix
        patched = transform(original)
        assert "READONLY_STREAM_64K_ACTIVE" in patched
        assert patched.split("    const u64 first_block = device_addr >> block_shift;", 1)[1] == \
            original.split("    const u64 first_block = device_addr >> block_shift;", 1)[1]
        assert 'readonly_stream_limit' in patched
        print(f"BUFFER_CPP_SURGICAL_{mode}_SELFTEST=PASS")
    print("UNMODIFIED_SPARSE_RESIDENCY_AND_GPU_WRITE_PATH=PASS")

def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--selftest":
        selftest()
        return
    if len(sys.argv) != 3:
        raise SystemExit("Usage: patcher.py SOURCE_BUFFER_CPP REPORT")
    src, report = map(Path, sys.argv[1:])
    if not src.is_file() or src.is_symlink():
        raise SystemExit("BUFFER_CACHE_NOT_SAFE_REGULAR_FILE")
    before = src.read_bytes()
    actual = hashlib.sha256(before).hexdigest()
    if actual != ACTUAL_PROVEN_BUFFER_SHA256:
        raise SystemExit("ACTUAL_PROVEN_BUFFER_CPP_SHA_MISMATCH=" + actual)
    try:
        output = transform(before.decode("utf-8")).encode("utf-8")
    except Exception as exc:
        raise SystemExit(f"BUFFER_STREAM_SOURCE_OVERLAY_FAILED={exc}") from exc
    diff = "".join(difflib.unified_diff(
        before.decode().splitlines(True), output.decode().splitlines(True),
        fromfile="proven-buffer.cpp", tofile="readonly-64k-overlay.cpp"))
    report.write_text(
        f"PROVEN_BEFORE_SHA256={actual}\n"
        f"OPTIONAL_AFTER_SHA256={hashlib.sha256(output).hexdigest()}\n"
        "SAFE_16_TO_64K_READONLY_ONLY=YES\n"
        "OLD_SOURCE_NOT_RESET_TO_DIFFERENT_UPSTREAM_REVISION=YES\n"
        "SPARSE_WRITEBACK_AND_RENDERER_UNCHANGED=YES\n\n" + diff
    )
    src.write_bytes(output)
    print("PROVEN_USER_BUFFER_CPP_PRESERVED_AND_64K_OVERLAY=PASS")

if __name__ == "__main__":
    main()
