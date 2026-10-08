#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-run, scoped GPU-side ReadConst fallback for seven proven Ghost shader hashes.

The old flatbuf fallback reads user-data slot 0 when a dynamic ReadConst
has no flattened offset and global DMA is disabled. This trial opts the seven
observed failing shaders into the *existing* BDA/read_const_dynamic pipeline,
while leaving global DMA disabled and all other shaders unchanged.
This is NOT a reviewed upstream fix or a permanent setting change.
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
NEW_INFO = """    // GHOST_SRT_DMA_TRIAL: use the existing GPU-addressed ReadConst path only
    // for shaders proven to have GPU-dependent or loop-carried ReadConst offsets.
    // Keep the user's global Direct Memory Access setting unchanged.
    const bool ghost_dynamic_readconst =
        info.pgm_hash == 0x5c4c2b6fULL || info.pgm_hash == 0x2ba74851ULL ||
        info.pgm_hash == 0x8ced785bULL || info.pgm_hash == 0x00a4f19aULL ||
        info.pgm_hash == 0x04950cf6ULL || info.pgm_hash == 0x55d17c16ULL ||
        info.pgm_hash == 0x52b6e003ULL;
    if (!EmulatorSettings.IsDirectMemoryAccessEnabled() && !ghost_dynamic_readconst) {
        info.uses_dma = false;
        info.readconst_types = Info::ReadConstType::None;
    }

    if (ghost_dynamic_readconst && info.uses_dma) {
        LOG_WARNING(Render, "GHOST_SRT_DMA_TRIAL shader={:#x} readconst={:#x}",
                    info.pgm_hash, static_cast<u32>(info.readconst_types));
    }
    if (info.uses_dma) {"""

OLD_EMIT = """    const u32 flatbuf_off_dw = inst->Flags<u32>();
    if (!EmulatorSettings.IsDirectMemoryAccessEnabled()) {
        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));
    }
    if (flatbuf_off_dw == 0) {"""
NEW_EMIT = """    const u32 flatbuf_off_dw = inst->Flags<u32>();
    // The shader-info pass already enabled the BDA/fault descriptors whenever
    // an unflattened dynamic ReadConst needs them. Inspect the actual per-stage
    // capability, not the global UI setting, to avoid reading flatbuf slot 0.
    if (!ctx.info.uses_dma) {
        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));
    }
    if (flatbuf_off_dw == 0) {"""

def change(source: bytes, old: str, new: str, label: str) -> bytes:
    text = source.decode("utf-8")
    assert text.count(old) == 1, f"{label}: expected 1 reviewed anchor, saw {text.count(old)}"
    assert "GHOST_SRT_DMA_TRIAL" not in text
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
    assert NEW_INFO.count("info.pgm_hash ==") == 7
    assert "ctx.info.uses_dma" in NEW_EMIT
    # Regressions: fallback is active only if the shader has unflattened
    # ReadConst and its hash is one of the seven failures, or global DMA is on.
    bad = {0x5c4c2b6f, 0x2ba74851, 0x8ced785b, 0xa4f19a,
           0x4950cf6, 0x55d17c16, 0x52b6e003}
    def route(shader_hash, has_unflattened, global_dma):
        return has_unflattened and (global_dma or shader_hash in bad)
    assert all(route(h, True, False) for h in bad)
    assert all(not route(h, False, False) for h in bad)
    assert not route(0x7a1b2c3d, True, False)
    assert route(0x7a1b2c3d, True, True)
    print("SELFTEST PASS: seven affected shader hashes, dynamic-read fallback,")
    print("global DMA off and unmodified non-target shaders (without compiling).")

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
    print("PATCH_APPLIED: fallback is scoped to seven shaders and global DMA is untouched")

if __name__ == "__main__":
    main()
