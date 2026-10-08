#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Evidence-only Vulkan mip count instrumentation for Ghost of Tsushima.

Never changes the assertion or image-copy semantics. Exact source hash and
anchor are required before any changes. The caller restores source on exit.
"""
import hashlib
from pathlib import Path
import sys

EXPECTED_BLOB = "eea809e9539dd9e105df83e93c0ef243f6c7f9f8"
SOURCE_REL = "src/video_core/renderer_vulkan/vk_runtime.cpp"
OLD = "    ASSERT(num_mips == 1);\n"
NEW = """    if (num_mips != 1) {
        LOG_ERROR(Render_Vulkan,
                  "GHOST_MIP_ASSERT src_mips={} dst_mips={} common_mips={} src_layers={} "
                  "dst_layers={} src_extent={}x{}x{} dst_extent={}x{}x{} "
                  "src_format={} dst_format={} src_type={} dst_type={} "
                  "src_samples={} dst_samples={}",
                  src->info.resources.levels, dst->info.resources.levels, num_mips,
                  src->info.resources.layers, dst->info.resources.layers,
                  src->info.size.width, src->info.size.height, src->info.size.depth,
                  dst->info.size.width, dst->info.size.height, dst->info.size.depth,
                  static_cast<u32>(src->info.pixel_format),
                  static_cast<u32>(dst->info.pixel_format),
                  static_cast<u32>(src->info.type), static_cast<u32>(dst->info.type),
                  src->info.num_samples, dst->info.num_samples);
    }
    ASSERT(num_mips == 1);
"""

def git_blob_hash(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def instrument(source: bytes) -> bytes:
    h = git_blob_hash(source)
    assert h == EXPECTED_BLOB, f"Source changed; expected {EXPECTED_BLOB}, got {h}"
    text = source.decode("utf-8")
    assert text.count(OLD) == 1, "Unexpected mip assertion count"
    location = text.index(OLD)
    assert "void Runtime::CopyImageWithBuffer(" in text[max(0, location-500):location], (
        "Assertion is no longer in CopyImageWithBuffer")
    assert "GHOST_MIP_ASSERT" not in text, "Already instrumented"
    patched = text.replace(OLD, NEW)
    assert patched.count(OLD) == 1, "Original assertion must remain enabled"
    assert patched.count("GHOST_MIP_ASSERT") == 1
    return patched.encode("utf-8")

def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        path = Path(sys.argv[0])
        print("SELFTEST: patcher loaded; exact source is validated during deployment")
        return
    if len(sys.argv) != 3:
        raise SystemExit("Usage: instrument_ghost_mips.py SOURCE_ROOT BACKUP_DIR")
    source = Path(sys.argv[1]).resolve() / SOURCE_REL
    backup = Path(sys.argv[2]).resolve() / SOURCE_REL
    before = source.read_bytes()
    after = instrument(before)
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(before)
    assert backup.read_bytes() == before, "Backup verification failed"
    source.write_bytes(after)
    assert source.read_bytes() == after
    print(f"INSTRUMENTED {source} | previous sha={EXPECTED_BLOB}")
    print("Assertion and GPU-copy behavior unchanged; diagnostic log only.")

if __name__ == "__main__":
    main()
