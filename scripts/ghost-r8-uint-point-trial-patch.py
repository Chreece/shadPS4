#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Guarded one-run Ghost R8_UINT sampled-image nearest-filter experiment.

2026-10-08 CUSA11456 validation: immediately after two corrected multi-mip
copies, fragment shader 0x361a48f5 was compiled and a VkCmdDraw referenced
fs_img2, VK_FORMAT_R8_UINT, through a VK_FILTER_LINEAR sampler (VUID 04553).
Restrict this experiment to that fragment hash, exact sampled-image index
and format, and ONLY if the shader has exactly one sampler descriptor.
Other formats/shaders/samplers retain their original behavior.

NOTE: Association between image and sampler has not been proven; the marker
reports whether the guards matched. This is NOT a production fix.
"""
from __future__ import annotations
from pathlib import Path
import sys

REL = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"

INCLUDE_ANCHOR = '#include "common/debug.h"\n'
INCLUDE_CHANGE = '#include <atomic>\n\n#include "common/debug.h"\n'

SAMPLER_ANCHOR = """        const auto vk_sampler =
            texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base, sampler.is_depth);"""
SAMPLER_CHANGE = """        // GHOST_R8_UINT_POINT_TRIAL: scope an experimental point sampler to
        // the observed failing fragment resource only. Separate samplers can
        // serve multiple images; refuse to alter stages with >1 sampler.
        if (stage.pgm_hash == 0x361a48f5) {
            const bool r8_uint_fs_img2 =
                stage.images.size() > 2 && !stage.images[2].is_written &&
                stage.images[2].GetSharp(stage).GetDataFmt() == AmdGpu::DataFormat::Format8 &&
                stage.images[2].GetSharp(stage).GetNumberFmt() == AmdGpu::NumberFormat::Uint;
            const bool one_plain_sampler = stage.samplers.size() == 1 && !sampler.is_depth;
            static std::atomic_bool first_probe{false};
            if (!first_probe.exchange(true, std::memory_order_relaxed)) {
                LOG_INFO(Render_Vulkan,
                         "GHOST_R8_UINT_POINT_PROBE shader={:#x} images={} samplers={} "
                         "target_r8_uint={} one_plain_sampler={}",
                         stage.pgm_hash, stage.images.size(), stage.samplers.size(),
                         r8_uint_fs_img2, one_plain_sampler);
            }
            if (r8_uint_fs_img2 && one_plain_sampler) {
                // UINT images lack VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT.
                // Point filtering and disabled anisotropy are legal for them.
                ssharp.xy_mag_filter.Assign(AmdGpu::Filter::Point);
                ssharp.xy_min_filter.Assign(AmdGpu::Filter::Point);
                ssharp.mip_filter.Assign(AmdGpu::MipFilter::Point);
                ssharp.max_aniso.Assign(AmdGpu::AnisoRatio::One);
                static std::atomic_bool first_apply{false};
                if (!first_apply.exchange(true, std::memory_order_relaxed)) {
                    LOG_WARNING(Render_Vulkan,
                                "GHOST_R8_UINT_POINT_APPLIED shader={:#x} image=2",
                                stage.pgm_hash);
                }
            }
        }
        const auto vk_sampler =
            texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base, sampler.is_depth);"""


def modify(data: bytes) -> bytes:
    src = data.decode("utf-8")
    if src.count(INCLUDE_ANCHOR) != 1 or src.count(SAMPLER_ANCHOR) != 1:
        raise ValueError("The exact include or BindTextures sampler anchor is missing/ambiguous.")
    if "GHOST_R8_UINT_POINT_TRIAL" in src:
        raise ValueError("The experimental patch is already present; refusing to repatch.")
    # The validated binary came from a specific local fullstack commit. Strict
    # source anchors also gate the patch, allowing other reviewed changes.
    if 'void Rasterizer::BindTextures(' not in src or 'stage.images' not in src:
        raise ValueError("Renderer sampler source appears unexpected.")
    out = src.replace(INCLUDE_ANCHOR, INCLUDE_CHANGE, 1)
    out = out.replace(SAMPLER_ANCHOR, SAMPLER_CHANGE, 1)
    if out.count("GHOST_R8_UINT_POINT_APPLIED") != 1 or \
       out.count("GHOST_R8_UINT_POINT_PROBE") != 1:
        raise ValueError("Patched source diagnostic verification failed.")
    return out.encode("utf-8")


def selftest():
    fixture = (INCLUDE_ANCHOR + "\nvoid Rasterizer::BindTextures() {"
               + "const auto& items = stage.images;\n" + SAMPLER_ANCHOR + "\n}\n").encode()
    out = modify(fixture).decode()
    assert out.count(SAMPLER_ANCHOR) == 1
    assert out.count(INCLUDE_CHANGE) == 1
    assert 'stage.pgm_hash == 0x361a48f5' in out
    assert 'stage.samplers.size() == 1' in out
    assert 'stage.images[2].GetSharp(stage).GetDataFmt()' in out
    assert 'AmdGpu::NumberFormat::Uint' in out
    assert out.count('ssharp.xy_mag_filter.Assign(AmdGpu::Filter::Point);') == 1
    assert out.count('std::atomic_bool first_apply') == 1
    try:
        modify(out.encode())
    except ValueError:
        pass
    else:
        raise AssertionError("already-patched source was not rejected")
    print("SELFTEST PASS: unique renderer anchors, exact shader/image/format/sampler "
          "guards, point filter, immutable outside guarded path, double patch rejected")


def main():
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return
    if len(sys.argv) not in (3, 4) or (
        len(sys.argv) == 4 and sys.argv[3] != "--check-only"
    ):
        raise SystemExit("Usage: patch.py ROOT BACKUP_DIR [--check-only]")
    root, save = map(lambda s: Path(s).resolve(), sys.argv[1:3])
    file = root / REL
    before = file.read_bytes()
    after = modify(before)
    selftest()
    if len(sys.argv) == 4:
        print("GHOST_R8_UINT_SOURCE_CHECK_PASS: no edits")
        return
    backup = save / REL
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(before)
    if backup.read_bytes() != before:
        raise OSError("Exact original rasterizer backup failed")
    file.write_bytes(after)
    if file.read_bytes() != after:
        raise OSError("Patched rasterizer verification failed")
    print("GHOST_R8_UINT_POINT_TRIAL_SOURCE_PATCHED:",file)


if __name__ == "__main__":
    main()
