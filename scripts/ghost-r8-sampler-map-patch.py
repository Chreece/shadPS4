#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only shader-scoped Vulkan sampler handle mapping.

In the 21:10 Oct 8 Ghost run, fs_0x361a48f5 had three images with the third
R8_UINT and TWO samplers. Vulkan error VUID-vkCmdDraw-magFilter-04553 gives
the offending VkSampler HANDLE, but not which stage.samplers index produced it.

This patch only logs raw VkSampler handles + descriptor bindings for that
specific shader and R8_UINT third image. It does NOT change shader behavior,
sampler filtering, game memory, Vulkan resources, affinity, or CPUID.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REL = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
EXPECTED_BLOB = "bd43011832a83a5eee53519b2923bc2aadfa9787"
INCLUDE = '#include "common/debug.h"\n'
INCLUDE_NEW = '#include <atomic>\n#include <bit>\n\n#include "common/debug.h"\n'
HEAD = (
    "    for (const auto& sampler : stage.samplers) {\n"
    "        auto ssharp = sampler.GetSharp(stage);"
)
HEAD_NEW = (
    "    u32 ghost_r8_sampler_index = 0;\n"
    "    for (const auto& sampler : stage.samplers) {\n"
    "        auto ssharp = sampler.GetSharp(stage);"
)
TAIL = (
    "        const auto vk_sampler =\n"
    "            texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base, sampler.is_depth);\n"
    "        image_infos.emplace_back(vk_sampler, VK_NULL_HANDLE, vk::ImageLayout::eGeneral);\n"
    "        auto& set_write = set_writes[set_write_index++];\n"
    "        set_write.dstSet = VK_NULL_HANDLE;\n"
    "        set_write.dstBinding = binding.unified++;\n"
    "        set_write.dstArrayElement = 0;\n"
    "        set_write.descriptorCount = 1;\n"
    "        set_write.descriptorType = vk::DescriptorType::eSampler;\n"
    "        set_write.pImageInfo = &image_infos.back();\n"
    "    }\n"
    "}"
)
TAIL_NEW = (
    "        const auto vk_sampler =\n"
    "            texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base, sampler.is_depth);\n"
    "        image_infos.emplace_back(vk_sampler, VK_NULL_HANDLE, vk::ImageLayout::eGeneral);\n"
    "        auto& set_write = set_writes[set_write_index++];\n"
    "        set_write.dstSet = VK_NULL_HANDLE;\n"
    "        set_write.dstBinding = binding.unified++;\n"
    "        set_write.dstArrayElement = 0;\n"
    "        set_write.descriptorCount = 1;\n"
    "        set_write.descriptorType = vk::DescriptorType::eSampler;\n"
    "        set_write.pImageInfo = &image_infos.back();\n"
    "        // GHOST_R8_SAMPLER_HANDLE_MAP: keep sampling untouched. Log exact\n"
    "        // Vulkan handle bits so they can be matched to validation error 04553.\n"
    "        if (stage.pgm_hash == 0x361a48f5 && stage.images.size() > 2 &&\n"
    "            !stage.images[2].is_written &&\n"
    "            stage.images[2].GetSharp(stage).GetDataFmt() == AmdGpu::DataFormat::Format8 &&\n"
    "            stage.images[2].GetSharp(stage).GetNumberFmt() == AmdGpu::NumberFormat::Uint) {\n"
    "            static std::atomic<u32> trace_count{0};\n"
    "            if (trace_count.fetch_add(1, std::memory_order_relaxed) < 8) {\n"
    "                static_assert(sizeof(VkSampler) == sizeof(u64));\n"
    "                const u64 raw_handle =\n"
    "                    std::bit_cast<u64>(static_cast<VkSampler>(vk_sampler));\n"
    "                LOG_INFO(Render_Vulkan,\n"
    '                         "GHOST_R8_SAMPLER_MAP shader={:#x} sampler_index={} binding={} "\n'
    '                         "handle={:#x} depth={} mag={} min={} mip={} aniso={}",\n'
    "                         stage.pgm_hash, ghost_r8_sampler_index, set_write.dstBinding,\n"
    "                         raw_handle, sampler.is_depth,\n"
    "                         u32(ssharp.xy_mag_filter.Value()),\n"
    "                         u32(ssharp.xy_min_filter.Value()),\n"
    "                         u32(ssharp.mip_filter.Value()),\n"
    "                         u32(ssharp.max_aniso.Value()));\n"
    "            }\n"
    "        }\n"
    "        ++ghost_r8_sampler_index;\n"
    "    }\n"
    "}"
)


def change(original: bytes) -> bytes:
    source = original.decode("utf-8")
    if source.count(INCLUDE) != 1 or source.count(HEAD) != 1 or source.count(TAIL) != 1:
        raise RuntimeError("Sampler source anchors differ; refusing any edit.")
    if "GHOST_R8_SAMPLER_MAP" in source:
        raise RuntimeError("The sampler diagnostic is already applied.")
    updated = source.replace(INCLUDE, INCLUDE_NEW, 1)
    updated = updated.replace(HEAD, HEAD_NEW, 1)
    updated = updated.replace(TAIL, TAIL_NEW, 1)
    assert updated.count("GHOST_R8_SAMPLER_MAP shader=") == 1
    assert updated.count("++ghost_r8_sampler_index;") == 1
    assert updated.count("std::bit_cast<u64>(static_cast<VkSampler>(vk_sampler))") == 1
    return updated.encode("utf-8")


def git_blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def selftest() -> None:
    mock = (INCLUDE + "void Rasterizer::BindTextures() {\n" + HEAD +
            "\n ... ignored sample setup ...\n" + TAIL + "\n").encode("utf-8")
    updated = change(mock)
    source = updated.decode("utf-8")
    assert "GHOST_R8_SAMPLER_MAP shader=" in source
    assert "stage.pgm_hash == 0x361a48f5" in source
    assert "stage.images[2].GetDataFmt()" not in source
    assert "stage.images[2].GetSharp(stage).GetDataFmt()" in source
    assert "stage.images[2].GetSharp(stage).GetNumberFmt()" in source
    assert "std::bit_cast<u64>(static_cast<VkSampler>(vk_sampler))" in source
    assert "texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base, sampler.is_depth)" in source
    assert ".Assign(AmdGpu::Filter::Point)" not in source
    try:
        change(updated)
    except RuntimeError:
        pass
    else:
        raise AssertionError("Double application was not rejected.")
    print("SELFTEST PASS: unique exact renderer anchors, shader-and-format-only handle "
          "logging, no sampler modifications, second application rejected")


def main():
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and
                                     sys.argv[3] != "--check-only"):
        raise SystemExit("Usage: patch.py ROOT BACKUP_DIR [--check-only]")
    root, backups = (Path(s).resolve() for s in sys.argv[1:3])
    target = root / REL
    original = target.read_bytes()
    actual = git_blob(target)
    if actual != EXPECTED_BLOB:
        raise RuntimeError(f"Source blob mismatch: expected={EXPECTED_BLOB} actual={actual}")
    updated = change(original)
    selftest()
    print(f"GHOST_R8_SAMPLER_SOURCE_VERIFIED={actual}")
    if len(sys.argv) == 4:
        print("GHOST_R8_SAMPLER_CHECK_ONLY_PASS")
        return
    backup = backups / REL
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(original)
    if backup.read_bytes() != original:
        raise RuntimeError("Byte-exact source backup failed.")
    target.write_bytes(updated)
    if target.read_bytes() != updated:
        raise RuntimeError("Sampler diagnostic source verification failed.")
    print("GHOST_R8_SAMPLER_MAP_PATCHED:", target)
    print("No Vulkan sampler filtering or other gameplay behavior changed.")


if __name__ == "__main__":
    main()
