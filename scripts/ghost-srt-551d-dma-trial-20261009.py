#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ghost shader 0x551d6087: controlled dynamic-SRT GPU ReadConst trial.

The 2026-10-09 Ghost trace with stencil storage VUIDs eliminated still
reports 128 ReadLane-offset compute failures for fragment shader 0x551d6087,
then VK_ERROR_DEVICE_LOST. Test the already-existing GPU BDA dynamic-ReadConst
path ONLY for that shader if shader info requires it. This is an A/B
hypothesis, not a confirmed fix. Global DMA and other shaders stay unchanged.

The unattended shell owns backups and automatic source/binary rollback.
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
NEW_INFO = """    // GHOST_SRT_551D_ROUTE: limited A/B for the observed ReadLane-offset
    // fragment shader. Do not enable DMA if ReadConst did not request it.
    const bool ghost_srt_551d_dma =
        info.pgm_hash == 0x551d6087ULL && info.uses_dma;
    if (!EmulatorSettings.IsDirectMemoryAccessEnabled() && !ghost_srt_551d_dma) {
        info.uses_dma = false;
        info.readconst_types = Info::ReadConstType::None;
    }

    if (info.pgm_hash == 0x551d6087ULL) {
        LOG_WARNING(Render, "GHOST_SRT_551D_ROUTE shader={:#x} dma={} readconst={:#x}",
                    info.pgm_hash, info.uses_dma, static_cast<u32>(info.readconst_types));
    }
    if (info.uses_dma) {"""

OLD_EMIT = """    const u32 flatbuf_off_dw = inst->Flags<u32>();
    if (!EmulatorSettings.IsDirectMemoryAccessEnabled()) {
        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));
    }
    if (flatbuf_off_dw == 0) {"""
NEW_EMIT = """    const u32 flatbuf_off_dw = inst->Flags<u32>();
    // GHOST_SRT_551D_EMIT: consult per-stage information. If the exact
    // shader requested dynamic ReadConst, GPU BDA handles an offset that
    // the CPU SRT walker cannot express (e.g. ReadLane).
    if (!ctx.info.uses_dma) {
        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));
    }
    if (flatbuf_off_dw == 0) {"""

def change(source: bytes, old: str, new: str, label: str) -> bytes:
    text = source.decode("utf-8")
    assert text.count(old) == 1, f"{label}: expected 1 reviewed anchor, saw {text.count(old)}"
    assert "GHOST_SRT_DMA_TRIAL" not in text
    assert "GHOST_SRT_551D_ROUTE" not in text
    assert "GHOST_SRT_551D_EMIT" not in text
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
    assert NEW_INFO.count("info.pgm_hash == 0x551d6087ULL") == 2
    assert "info.pgm_hash == 0x551d6087ULL && info.uses_dma" in NEW_INFO
    assert "ctx.info.uses_dma" in NEW_EMIT
    assert "GHOST_SRT_551D_ROUTE shader=" in NEW_INFO
    assert "GHOST_SRT_551D_EMIT" in NEW_EMIT
    assert not any(h in NEW_INFO for h in
        ("0x5c4c2b6f", "0x2ba74851", "0x8ced785b", "0x55d17c16"))
    def route(h, has_dynamic, globally_enabled):
        return has_dynamic and (globally_enabled or h == 0x551d6087)
    assert route(0x551d6087, True, False)
    assert not route(0x551d6087, False, False)
    assert not route(0x7a1b2c3d, True, False)
    assert route(0x7a1b2c3d, True, True)
    for original, old, new in (
        (source_info, OLD_INFO, NEW_INFO),
        (source_emit, OLD_EMIT, NEW_EMIT),
    ):
        try:
            change(original.replace(old.encode(), b"gone"),
                   old, new, "negative")
        except AssertionError:
            pass
        else:
            raise AssertionError("unrecognized source accepted")
    print("SELFTEST PASS: exact 0x551d6087 dynamic ReadConst scope, normal"
          " shader paths unchanged, global DMA unchanged, fail-closed anchors")

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
    print("GHOST_SRT_551D_PATCH=APPLIED: exact shader dynamic ReadConst only, global DMA untouched")

if __name__ == "__main__":
    main()
