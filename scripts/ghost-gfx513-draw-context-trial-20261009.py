#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reversible GPU hang correlation: guest shaders for RADV's final 513x513 draw.

The 2026-10-09 RADV trace reaches marker 1925, then a PS_PARTIAL_FLUSH;
marker 1926 is NOT reached. The draw immediately before that flush is a
four-vertex, nonindexed 513x513 graphics pass. The driver reports graphics
pipeline hash 0x4f65ace2cd42f652 and a pixel shader with texture reads and
a loop. This patch logs guest shader hashes and source resources for that
draw's exact geometry, so it can be correlated without speculative fixes.

Existing v4 null-image and stencil/storage alias behavior is left unchanged.
The enclosing one-run shell restores the entire original vk_rasterizer.cpp,
the cached/installed executable and per-game validation settings.
"""
from pathlib import Path
import os
import stat
import sys
import tempfile

FN = "vk_rasterizer.cpp"
SOURCE_FUNCTION = "void Rasterizer::Draw(bool is_indexed, u32 index_offset) {"
NEXT_FUNCTION = "void Rasterizer::DrawIndirect("
OLD = """    const auto state = BeginRendering(pipeline);

    BindVertexBuffers(pipeline);
    if (is_indexed) {"""
NEW = """    const auto state = BeginRendering(pipeline);

    // GHOST_GFX513_DRAW_CONTEXT: hardware RADV hang points to the 513x513
    // 4-vertex nonindexed pass immediately preceding PS_PARTIAL_FLUSH.
    // Read-only and bounded; no renderer command or guest state is modified.
    if (!is_indexed && regs.num_indices == 4 && state.width == 513 &&
        state.height == 513) {
        static std::atomic<u32> ghost_513_draw_seq{0};
        const u32 ghost_seq = ghost_513_draw_seq.fetch_add(1, std::memory_order_relaxed);
        if (ghost_seq < 128) {
            const auto& cb0 = cb_descs[0];
            LOG_WARNING(Render_Vulkan,
                        "GHOST_GFX513_DRAW seq={} width={} height={} vertices={} instances={} "
                        "cb_addr={:#x} cb_fmt={} depth_addr={:#x} mrt={}",
                        ghost_seq, state.width, state.height, regs.num_indices,
                        regs.num_instances.NumInstances(),
                        cb0.first ? cb0.second.info.guest_address : 0,
                        cb0.first ? static_cast<u32>(cb0.second.info.pixel_format) : 0,
                        db_desc.first ? db_desc.second.info.guest_address : 0,
                        state.num_color_attachments);
            for (const auto* guest_stage : pipeline->GetStages()) {
                if (!guest_stage) {
                    continue;
                }
                LOG_WARNING(Render_Vulkan,
                            "GHOST_GFX513_STAGE seq={} stage={} guest_shader={:#x} "
                            "images={} buffers={} samplers={} uses_dma={}",
                            ghost_seq, static_cast<u32>(guest_stage->sw_stage),
                            guest_stage->pgm_hash, guest_stage->images.size(),
                            guest_stage->buffers.size(), guest_stage->samplers.size(),
                            guest_stage->uses_dma);
                if (guest_stage->sw_stage == Shader::SwStage::Fragment) {
                    for (u32 i = 0; i < guest_stage->images.size() && i < 12; ++i) {
                        const auto& res = guest_stage->images[i];
                        const auto tsharp = res.GetSharp(*guest_stage);
                        LOG_WARNING(Render_Vulkan,
                                    "GHOST_GFX513_IMAGE seq={} idx={} addr={:#x} fmt={} "
                                    "numberfmt={} width={} height={} mips={} storage={} depth={}",
                                    ghost_seq, i, tsharp.Address(),
                                    static_cast<u32>(tsharp.GetDataFmt()),
                                    static_cast<u32>(tsharp.GetNumberFmt()),
                                    static_cast<u32>(tsharp.width),
                                    static_cast<u32>(tsharp.height),
                                    tsharp.NumLevels(), static_cast<u32>(res.is_written),
                                    static_cast<u32>(res.is_depth));
                    }
                }
            }
        }
    }

    BindVertexBuffers(pipeline);
    if (is_indexed) {"""


def transform(source: str) -> str:
    markers = {
        "v4 invalid image handling": source.count("GHOST_NULL_IMAGE_BINDING shader=") == 2,
        "stencil image storage alias": source.count("GHOST_STENCIL_STORAGE_ALIAS shader=") == 1,
        "R8 point sampler": source.count("GHOST_R8_INDEX1_APPLIED shader=") == 1,
        "atomics from R8": source.count("#include <atomic>") == 1,
        "correct legacy Draw": source.count(SOURCE_FUNCTION) == 1,
        "correct following DrawIndirect": source.count(NEXT_FUNCTION) == 1,
        "not already patched": "GHOST_GFX513_DRAW_CONTEXT" not in source,
    }
    if not all(markers.values()):
        raise ValueError("unexpected legacy post-stencil source: " +
                         ", ".join(k for k, ok in markers.items() if not ok))
    start = source.index(SOURCE_FUNCTION)
    end = source.index(NEXT_FUNCTION)
    if start >= end or source[start:end].count(OLD) != 1:
        raise ValueError("exact target draw placement not found once")
    body = source[start:end].replace(OLD, NEW, 1)
    result = source[:start] + body + source[end:]
    assert result.count("GHOST_GFX513_DRAW seq=") == 1
    assert result.count("GHOST_GFX513_STAGE seq=") == 1
    assert result.count("GHOST_GFX513_IMAGE seq=") == 1
    assert result.count("GHOST_NULL_IMAGE_BINDING shader=") == 2
    assert result.count("GHOST_STENCIL_STORAGE_ALIAS shader=") == 1
    assert source[:start] == result[:start] and source[end:] == result[len(source[:start]+body):]
    return result


def selftest():
    src = ('#include <atomic>\n'
           'GHOST_NULL_IMAGE_BINDING shader=\n'
           'GHOST_NULL_IMAGE_BINDING shader=\n'
           'GHOST_STENCIL_STORAGE_ALIAS shader=\n'
           'GHOST_R8_INDEX1_APPLIED shader=\n'
           + SOURCE_FUNCTION + "\n"
           + OLD + "\n}\n" + NEXT_FUNCTION + "\n")
    changed = transform(src)
    assert "state.width == 513" in changed
    assert "state.height == 513" in changed
    assert "regs.num_indices == 4" in changed
    assert "!is_indexed" in changed
    assert "GHOST_GFX513_STAGE seq=" in changed
    assert "GHOST_GFX513_IMAGE seq=" in changed
    assert changed.count(OLD) == 0
    for bad in (changed, src.replace(OLD, ""), src.replace(NEXT_FUNCTION, ""),
                src.replace("GHOST_STENCIL_STORAGE_ALIAS shader=", "GONE")):
        try:
            transform(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("unrecognized or multiply patched source accepted")
    print("SELFTEST PASS: exact 513x513 nonindexed 4-vertex draw only, bounded "
          "guest stage and image logs, source guards and duplicate rejection")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    args = sys.argv[1:]
    check = len(args) == 2 and args[0] == "--check-only"
    if not (len(args) == 1 or check):
        raise SystemExit("Usage: ghost-gfx513-draw-context-trial.py [--check-only] PATH")
    target = Path(args[-1])
    if not target.is_file() or target.is_symlink() or target.name != FN:
        raise ValueError("expected regular vk_rasterizer.cpp")
    before = target.read_text(encoding="utf-8")
    after = transform(before)
    if check:
        print("GHOST_GFX513_CONTEXT_CHECK_PASS: verified v4+stencil draw source")
        return 0
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                dir=target.parent, prefix=".ghost-gfx513-",
                suffix=".cpp", delete=False) as out:
            temp = Path(out.name)
            out.write(after)
        os.chmod(temp, stat.S_IMODE(target.stat().st_mode))
        os.replace(temp, target)
        temp = None
    finally:
        if temp is not None and temp.exists():
            temp.unlink()
    if target.read_text(encoding="utf-8") != after:
        raise IOError("source readback failed")
    print("GHOST_GFX513_CONTEXT_PATCH=APPLIED: logging only; no draw suppression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
