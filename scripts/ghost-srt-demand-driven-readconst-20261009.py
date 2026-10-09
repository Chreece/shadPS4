#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Trial-only shader-demand-driven GPU ReadConst without changing global DMA.

2026-10-09 Ghost: 263 unsupported SRT offsets across 14 shader/opcode pairs,
including 112 ReadLane failures for 0x8e743c8e. The preceding one-shader
0x551d6087 experiment was never triggered. Do not guess another hash.

The shader_info_collection pass already detects unflattened ReadConst and
sets info.uses_dma, but when global DMA is off it forcibly clears that
per-stage requirement. EmitReadConst also checks global instead of stage
capability and can read flatbuf slot 0. Preserve the GPU BDA route ONLY for
shaders that actually need unflattened dynamic ReadConst.

Temporary A/B experiment; no permanent GPU or global configuration changes.
The parent transaction restores both sources, cached/installed ELF and config.
"""
from pathlib import Path
import hashlib
import subprocess
import sys

REL_INFO = "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp"
REL_EMIT = "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp"

OLD_INFO = """    if (!EmulatorSettings.IsDirectMemoryAccessEnabled()) {
        info.uses_dma = false;
        info.readconst_types = Info::ReadConstType::None;
    }

    if (info.uses_dma) {"""
NEW_INFO = """    // GHOST_SRT_DETECTED_DYNAMIC: preserve the existing per-stage result
    // of Visit(ReadConst), rather than disabling it for all shaders when
    // global DMA is off. Every other shader keeps its original flatbuf path.
    const bool ghost_dynamic_readconst = info.uses_dma;
    if (!EmulatorSettings.IsDirectMemoryAccessEnabled() && !ghost_dynamic_readconst) {
        info.uses_dma = false;
        info.readconst_types = Info::ReadConstType::None;
    }
    if (!EmulatorSettings.IsDirectMemoryAccessEnabled() && ghost_dynamic_readconst) {
        LOG_WARNING(Render, "GHOST_SRT_DETECTED_DYNAMIC shader={:#x} uses_dma={} readconst={:#x}",
                    info.pgm_hash, info.uses_dma, static_cast<u32>(info.readconst_types));
    }
    if (info.uses_dma) {"""

OLD_EMIT = """    const u32 flatbuf_off_dw = inst->Flags<u32>();
    if (!EmulatorSettings.IsDirectMemoryAccessEnabled()) {
        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));
    }
    if (flatbuf_off_dw == 0) {"""
NEW_EMIT = """    const u32 flatbuf_off_dw = inst->Flags<u32>();
    // GHOST_SRT_DETECTED_EMIT: decide from shader capability (unflattened
    // dynamic ReadConst), never a global DMA override. Offset zero is
    // reserved for this GPU-side dynamic load and not a flatbuffer slot.
    if (!ctx.info.uses_dma) {
        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));
    }
    if (flatbuf_off_dw == 0) {"""

def change(source: bytes, old: str, new: str, label: str) -> bytes:
    text = source.decode("utf-8")
    if text.count(old) != 1:
        raise ValueError(f"{label}: expected 1 reviewed anchor, saw {text.count(old)}")
    if any(marker in text for marker in
           ("GHOST_SRT_DMA_TRIAL", "GHOST_SRT_551D_ROUTE", "GHOST_SRT_551D_EMIT",
            "GHOST_SRT_DETECTED_DYNAMIC", "GHOST_SRT_DETECTED_EMIT")):
        raise ValueError(f"{label}: prior experimental patch already present")
    patched = text.replace(old, new)
    assert patched.count(new) == 1
    return patched.encode("utf-8")

def hashed(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def selftest():
    source_info = ("// header\n" + OLD_INFO + "\n// footer\n").encode()
    source_emit = ("// header\n" + OLD_EMIT + "\n// footer\n").encode()
    assert NEW_INFO in change(source_info, OLD_INFO, NEW_INFO, REL_INFO).decode()
    assert NEW_EMIT in change(source_emit, OLD_EMIT, NEW_EMIT, REL_EMIT).decode()
    assert 'const bool ghost_dynamic_readconst = info.uses_dma;' in NEW_INFO
    assert 'if (!ctx.info.uses_dma)' in NEW_EMIT
    assert 'GHOST_SRT_DETECTED_DYNAMIC shader=' in NEW_INFO
    assert 'GHOST_SRT_DETECTED_EMIT:' in NEW_EMIT
    assert 'info.pgm_hash ==' not in NEW_INFO
    def route(has_unflattened, global_dma):
        return bool(has_unflattened)
    for h in (0x8e743c8e, 0x551d6087, 0x5c4c2b6f, 0xdeadbeef):
        assert route(True, False) and route(True, True)
        assert not route(False, False) and not route(False, True)
    for original, anchor, revised in (
        (source_info, OLD_INFO, NEW_INFO),
        (source_emit, OLD_EMIT, NEW_EMIT),
    ):
        for invalid in (original.replace(anchor.encode(), b"gone"),
                        change(original, anchor, revised, "first")):
            try:
                change(invalid, anchor, revised, "reject")
            except ValueError:
                pass
            else:
                raise AssertionError("missing or duplicate source accepted")
    print("SELFTEST PASS: demand-driven GPU ReadConst, no shader hash whitelist,"
          " global DMA untouched, unrelated stages unchanged, reject duplicates")

def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        selftest()
        return
    if len(sys.argv) not in (3, 4):
        raise SystemExit("Usage: patch.py ROOT BACKUP_DIR [--check-only] | --self-test")
    root = Path(sys.argv[1]).resolve()
    backup_root = Path(sys.argv[2]).resolve()
    check_only = len(sys.argv) == 4 and sys.argv[3] == "--check-only"
    if len(sys.argv) == 4 and not check_only:
        raise SystemExit("Unknown option; only --check-only is accepted")
    original = {}
    changed = {}
    for path, old, new in ((REL_INFO, OLD_INFO, NEW_INFO),
                           (REL_EMIT, OLD_EMIT, NEW_EMIT)):
        file = root / path
        before = file.read_bytes()
        after = change(before, old, new, path)
        original[path] = before
        changed[path] = after
        print(f"VERIFIED {path}: sha256={hashlib.sha256(before).hexdigest()} "
              f"modified_lines={len(after.splitlines())-len(before.splitlines())}")
    selftest()
    if check_only:
        print("CHECK_OK: no source files modified")
        return
    # Verify *both* backups before modifying *either* source file.
    for path, before in original.items():
        target = backup_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(before)
        assert target.read_bytes() == before, f"backup failed: {path}"
    for path, after in changed.items():
        target = root / path
        target.write_bytes(after)
        assert target.read_bytes() == after
        print(f"TRIAL_PATCHED {path}")
    print("GHOST_SRT_DETECTED_DYNAMIC_PATCH=APPLIED: only detected per-stage dynamic ReadConst, global DMA unchanged")

if __name__ == "__main__":
    main()
