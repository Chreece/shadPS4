#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Experimental Ghost shader-specific fix for invalid integer texture filtering.

2026-10-08 live Vulkan validation: fs_0x361a48f5 sampled R8_UINT image 2 using
VkSampler 0xb3f0000000b3f. A read-only trace matched that handle uniquely to
sampler index 1, descriptor binding 6. Sampler index 0 was already point-filtered.

This trial changes only S#1 for this shader when T#2 is R8_UINT, there are
exactly two sampler resources, both the shader and descriptor match, and S#1
is non-depth. It does NOT claim to repair all integer texture filtering or
the later unrelated mip-copy assertion. A driver script must restore source.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

REL = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
EXPECTED_BLOB = "bd43011832a83a5eee53519b2923bc2aadfa9787"
INCLUDE = '#include "common/debug.h"\n'
INCLUDE_NEW = '#include <atomic>\n\n#include "common/debug.h"\n'
START = (
    "    for (const auto& sampler : stage.samplers) {\n"
    "        auto ssharp = sampler.GetSharp(stage);"
)
START_NEW = (
    "    u32 ghost_sampler_index = 0;\n"
    "    for (const auto& sampler : stage.samplers) {\n"
    "        auto ssharp = sampler.GetSharp(stage);"
)
CALL = (
    "            ssharp = AmdGpu::Sampler{};\n"
    "        }\n"
    "        const auto vk_sampler =\n"
    "            texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base, sampler.is_depth);"
)
CALL_NEW = (
    "            ssharp = AmdGpu::Sampler{};\n"
    "        }\n"
    "        // GHOST_R8_INDEX1_TRIAL: validation mapped S#1 to the linear\n"
    "        // VkSampler used with fs_img2 (VK_FORMAT_R8_UINT). Never mutate\n"
    "        // S#0, unrelated shader hashes, other formats or depth samplers.\n"
    "        if (stage.hw_stage == Shader::HwStage::Fragment &&\n"
    "            stage.pgm_hash == 0x361a48f5 && ghost_sampler_index == 1 &&\n"
    "            stage.samplers.size() == 2 && !sampler.is_depth &&\n"
    "            static_cast<bool>(ssharp) && stage.images.size() == 3 &&\n"
    "            !stage.images[2].is_written &&\n"
    "            stage.images[2].GetSharp(stage).GetDataFmt() ==\n"
    "                AmdGpu::DataFormat::Format8 &&\n"
    "            stage.images[2].GetSharp(stage).GetNumberFmt() ==\n"
    "                AmdGpu::NumberFormat::Uint) {\n"
    "            ssharp.xy_mag_filter.Assign(AmdGpu::Filter::Point);\n"
    "            ssharp.xy_min_filter.Assign(AmdGpu::Filter::Point);\n"
    "            ssharp.mip_filter.Assign(AmdGpu::MipFilter::Point);\n"
    "            ssharp.max_aniso.Assign(AmdGpu::AnisoRatio::One);\n"
    "            static std::atomic<u32> logged{0};\n"
    "            if (logged.fetch_add(1, std::memory_order_relaxed) < 4) {\n"
    "                LOG_WARNING(Render_Vulkan,\n"
    '                            "GHOST_R8_INDEX1_APPLIED shader={:#x} sampler_index=1 "\n'
    '                            "image_index=2 format=R8_UINT mag=POINT min=POINT aniso=OFF",\n'
    "                            stage.pgm_hash);\n"
    "            }\n"
    "        }\n"
    "        const auto vk_sampler =\n"
    "            texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base, sampler.is_depth);"
)
END = (
    "        set_write.pImageInfo = &image_infos.back();\n"
    "    }\n"
    "}"
)
END_NEW = (
    "        set_write.pImageInfo = &image_infos.back();\n"
    "        ++ghost_sampler_index;\n"
    "    }\n"
    "}"
)


def modify(data: bytes) -> bytes:
    text = data.decode("utf-8")
    for name, anchor in (("include", INCLUDE), ("sampler loop", START),
                         ("sampler call", CALL), ("sampler end", END)):
        if text.count(anchor) != 1:
            raise RuntimeError(f"Unexpected {name} anchor count: {text.count(anchor)}")
    if "GHOST_R8_INDEX1_TRIAL" in text:
        raise RuntimeError("R8 sampler-index trial already applied")
    changed = text.replace(INCLUDE, INCLUDE_NEW, 1)
    changed = changed.replace(START, START_NEW, 1)
    changed = changed.replace(CALL, CALL_NEW, 1)
    changed = changed.replace(END, END_NEW, 1)
    assert changed.count("GHOST_R8_INDEX1_APPLIED shader=") == 1
    assert changed.count("++ghost_sampler_index;") == 1
    assert changed.count("ssharp.xy_mag_filter.Assign(AmdGpu::Filter::Point);") == 1
    return changed.encode("utf-8")


def sha_blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def selftest():
    fixture = (INCLUDE + "void Rasterizer::BindTextures() {\n" + START +
               "\nother sampler setup;\n" + CALL + "\nother descriptor setup;\n" + END)
    candidate = modify(fixture.encode()).decode()
    assert "stage.hw_stage == Shader::HwStage::Fragment" in candidate
    assert "stage.pgm_hash == 0x361a48f5" in candidate
    assert "ghost_sampler_index == 1" in candidate
    assert "stage.samplers.size() == 2" in candidate
    assert "stage.images.size() == 3" in candidate
    assert "stage.images[2].GetSharp(stage).GetNumberFmt()" in candidate
    assert "static_cast<bool>(ssharp)" in candidate
    assert "ssharp.xy_mag_filter.Assign(AmdGpu::Filter::Point);" in candidate
    assert "ssharp.xy_min_filter.Assign(AmdGpu::Filter::Point);" in candidate
    assert "ssharp.max_aniso.Assign(AmdGpu::AnisoRatio::One);" in candidate
    assert candidate.count("++ghost_sampler_index;") == 1
    try:
        modify(candidate.encode())
    except RuntimeError:
        pass
    else:
        raise AssertionError("Did not reject already-patched source")
    print("SELFTEST PASS: validated shader, index=1, 2 samplers, R8_UINT image=2, "
          "unchanged other bindings, point-only filter, duplicate rejection")


def main():
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return
    if len(sys.argv) not in (3, 4) or (
        len(sys.argv) == 4 and sys.argv[3] != "--check-only"
    ):
        raise SystemExit("Usage: patch.py ROOT BACKUP_DIR [--check-only]")
    root = Path(sys.argv[1]).resolve()
    backup_root = Path(sys.argv[2]).resolve()
    target = root / REL
    before = target.read_bytes()
    blob = sha_blob(target)
    if blob != EXPECTED_BLOB:
        raise RuntimeError(f"Unexpected rasterizer blob; expected={EXPECTED_BLOB}, got={blob}")
    after = modify(before)
    selftest()
    if len(sys.argv) == 4:
        print("GHOST_R8_INDEX1_SOURCE_CHECK_PASS; no changes")
        return
    backup = backup_root / REL
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(before)
    if backup.read_bytes() != before:
        raise IOError("R8 source backup verification failed")
    target.write_bytes(after)
    if target.read_bytes() != after:
        raise IOError("R8 source patch readback failed")
    print("GHOST_R8_INDEX1_SOURCE_PATCHED", target)


if __name__ == "__main__":
    main()
