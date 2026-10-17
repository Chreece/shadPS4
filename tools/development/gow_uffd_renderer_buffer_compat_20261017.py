#!/usr/bin/env python3
"""Port pinned deferred-UFFD renderer to current BufferCache interface (fail closed)."""
import hashlib
from pathlib import Path
import re
import sys

BEFORE = "a86fd050d18d3f5b942756bf4e74b69ccb856183f86d526c9346a07f78ab4feb"
AFTER = "7ab9db8b940936ae96df2467d91be7ff9b3f3ea13ed0a25056f7eb51ef5c250f"


def port(source, header):
    for symbol in ("enum class ObtainBufferFlags", "IsWritten = 1 << 0",
                   "IsTexelBuffer = 1 << 1", "DECLARE_ENUM_FLAG_OPERATORS(ObtainBufferFlags)",
                   "ObtainBufferFlags flags = ObtainBufferFlags::None",
                   "bool is_write = false, bool async = false,", "bool assume_locks = false"):
        assert symbol in header, "BUFFER_CACHE_ABI_INCOMPATIBLE: " + symbol
    pairs = (
        ("buffer_cache.ObtainBuffer(arg_address + offset, size, false)",
         "buffer_cache.ObtainBuffer(arg_address + offset, size)"),
        ("buffer_cache.ObtainBuffer(count_address, 4, false)",
         "buffer_cache.ObtainBuffer(count_address, 4)"),
        ("buffer_cache.ObtainBuffer(address + offset, size, false)",
         "buffer_cache.ObtainBuffer(address + offset, size)"),
        ("buffer_cache.ObtainBuffer(range.base_address, size, false)",
         "buffer_cache.ObtainBuffer(range.base_address, size)"),
        ("buffer_cache.ObtainBuffer(index_address, index_buffer_size, false)",
         "buffer_cache.ObtainBuffer(index_address, index_buffer_size)"),
        ('''const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, size, desc.is_written, desc.is_formatted);''',
         '''VideoCore::ObtainBufferFlags flags = {};
                if (desc.is_written) {
                    flags |= VideoCore::ObtainBufferFlags::IsWritten;
                }
                if (desc.is_formatted) {
                    flags |= VideoCore::ObtainBufferFlags::IsTexelBuffer;
                }
                const auto [buffer, offset] =
                    buffer_cache.ObtainBuffer(vsharp.base_address, size, flags);'''),
        ("buffer_cache.ObtainBuffer(address, num_bytes, true)",
         "buffer_cache.ObtainBuffer(address, num_bytes, VideoCore::ObtainBufferFlags::IsWritten)"),
        ("buffer_cache.ObtainBuffer(src, num_bytes, false, true)",
         "buffer_cache.ObtainBuffer(src, num_bytes, VideoCore::ObtainBufferFlags::IsTexelBuffer)"),
        ("buffer_cache.ObtainBuffer(dst, num_bytes, true, true)",
         '''buffer_cache.ObtainBuffer(dst, num_bytes,
                    VideoCore::ObtainBufferFlags::IsWritten |
                        VideoCore::ObtainBufferFlags::IsTexelBuffer)'''),
        ("buffer_cache.ReadMemory(addr, size, false, assume_locks)",
         "buffer_cache.ReadMemory(addr, size, false, false, assume_locks)"),
    )
    original = source
    for index, (old, new) in enumerate(pairs):
        assert source.count(old) == 1, f"OLD_CALL_{index}_COUNT={source.count(old)}"
        source = source.replace(old, new, 1)
    assert not re.search(r'ObtainBuffer\([^;]*?\b(?:true|false)\s*[,)]', source, re.DOTALL)
    for marker in ("InvalidateMemoryOnGpuThread(", "page_manager.DrainPendingInvalidations();",
                   "memory->ClampRangeSize(", "buffer_cache.ObtainBuffer(",
                   "texture_cache.InvalidateMemoryFromGPU("):
        assert original.count(marker) == source.count(marker), "SEMANTIC_CALL_CHANGED:" + marker
    assert hashlib.sha256(source.encode()).hexdigest() == AFTER, "PORT_OUTPUT_SHA_MISMATCH"
    return source


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: compat.py isolated_renderer.cpp active_buffer_cache.h")
    p, h = map(Path, sys.argv[1:])
    assert p.is_file() and not p.is_symlink(), "ISOLATED_RENDERER_INVALID"
    assert h.is_file() and not h.is_symlink(), "ACTIVE_BUFFER_CACHE_HEADER_INVALID"
    raw = p.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == BEFORE, "HISTORICAL_RENDERER_PREIMAGE_MISMATCH"
    changed = port(raw.decode(), h.read_text())
    p.write_text(changed)
    assert hashlib.sha256(p.read_bytes()).hexdigest() == AFTER, "POST_WRITE_VERIFY_FAILED"
    print("RENDERER_API_PORT=PASS; 9 BUFFER_CALLS + 1 READMEMORY CALL; UFFD PRESERVED")
    print("PINNED_RENDERER_SHA256=" + AFTER)


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, ValueError, OSError) as exc:
        print("RENDERER_API_PORT_FAILED: " + str(exc), file=sys.stderr)
        sys.exit(2)
