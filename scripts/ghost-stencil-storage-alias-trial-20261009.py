#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-run Ghost depth/stencil storage-image alias experiment.

2026-10-09 08:48 Vulkan evidence: a compute storage descriptor is bound to
VK_FORMAT_D32_SFLOAT_S8_UINT at guest stencil address 0x1504a08000,
triggering VUID-VkWriteDescriptorSet-descriptorType-00339 and
VUID-vkCmdDispatchIndirect-OpTypeImage-07028 (10 events each).

Rasterizer::BindTextures unconditionally redirects linked stencil aliases to
their depth/stencil image, even when the shader requests a writable storage
image. A D32_SFLOAT_S8_UINT depth image has no STORAGE usage or format support.

EXPERIMENT: Preserve the original non-depth stencil/colour alias for storage
access (instead of redirecting to D32S8). Keep redirect for all sampled
images and any depth-only case. Record the alias/linked format and log if
an illegal depth storage binding still reaches the second pass.

The surrounding no-GDB trial restores source, built/installed executables
and game-specific config, including on failure. This is not an upstream fix.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile

REL = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
MARKER = "GHOST_STENCIL_STORAGE_ALIAS"
OLD_REDIRECT = """            if (auto depth_image_id = texture_cache.GetAssociatedDepth(*image)) {
                // If this image has an associated depth image, it's a stencil attachment.
                // Redirect the access to the actual depth-stencil buffer.
                image_id = depth_image_id;
                image = &texture_cache.GetImage(image_id);
            }"""
NEW_REDIRECT = """            if (auto depth_image_id = texture_cache.GetAssociatedDepth(*image)) {
                // GHOST_STENCIL_STORAGE_ALIAS: the guest T# is a storage image.
                // Keep its non-depth backing alias: the associated D32S8 depth
                // image cannot legally be bound as a Vulkan storage image.
                // Sampled access retains the original depth/stencil redirect.
                if (image_desc.is_written && !image->info.props.is_depth) {
                    static std::atomic<u32> ghost_alias_logged{0};
                    if (ghost_alias_logged.fetch_add(1, std::memory_order_relaxed) < 16) {
                        const auto& depth_image = texture_cache.GetImage(depth_image_id);
                        LOG_WARNING(Render_Vulkan,
                                    "GHOST_STENCIL_STORAGE_ALIAS shader={:#x} addr={:#x} "
                                    "alias_fmt={} linked_depth_fmt={}",
                                    stage.pgm_hash, tsharp.Address(),
                                    static_cast<u32>(image->info.pixel_format),
                                    static_cast<u32>(depth_image.info.pixel_format));
                    }
                } else {
                    image_id = depth_image_id;
                    image = &texture_cache.GetImage(image_id);
                }
            }"""

OLD_SECOND = """            auto& image = texture_cache.GetImage(image_id);
            auto& image_view = texture_cache.FindTexture(image_id, desc);
            const auto binding = image.binding;"""
NEW_SECOND = """            auto& image = texture_cache.GetImage(image_id);
            if (is_storage && (image.info.props.is_depth ||
                               !(image.usage_flags & vk::ImageUsageFlagBits::eStorage))) {
                static std::atomic<u32> ghost_bad_storage_logged{0};
                if (ghost_bad_storage_logged.fetch_add(1, std::memory_order_relaxed) < 16) {
                    LOG_WARNING(Render_Vulkan,
                                "GHOST_STORAGE_IMAGE_INVALID shader={:#x} addr={:#x} "
                                "depth={} fmt={} storage_usage={}",
                                stage.pgm_hash, image.info.guest_address,
                                static_cast<u32>(image.info.props.is_depth),
                                static_cast<u32>(image.info.pixel_format),
                                static_cast<bool>(image.usage_flags &
                                                  vk::ImageUsageFlagBits::eStorage));
                }
            }
            auto& image_view = texture_cache.FindTexture(image_id, desc);
            const auto binding = image.binding;"""


def patch_text(text: str) -> str:
    guards = {
        "correct prior v4 invalid image descriptor fix":
            text.count("GHOST_NULL_IMAGE_BINDING shader=") == 2,
        "prior R8 sampler fix": text.count("GHOST_R8_INDEX1_APPLIED shader=") == 1,
        "one depth/stencil redirect": text.count(OLD_REDIRECT) == 1,
        "one shader image second pass": text.count(OLD_SECOND) == 1,
        "post-R8 atomic header": text.count("#include <atomic>") == 1,
        "pinned legacy rasterizer, indexed image array":
            text.count("image_bindings[num_images++]") == 3,
        "unmodified guard": MARKER not in text,
    }
    if not all(guards.values()):
        raise ValueError("unexpected trial source: " +
                         ", ".join(k for k, ok in guards.items() if not ok))
    output = text.replace(OLD_REDIRECT, NEW_REDIRECT, 1)
    output = output.replace(OLD_SECOND, NEW_SECOND, 1)
    assert output.count("GHOST_STENCIL_STORAGE_ALIAS shader=") == 1
    assert output.count("GHOST_STORAGE_IMAGE_INVALID shader=") == 1
    assert output.count("GHOST_NULL_IMAGE_BINDING shader=") == 2
    assert output.count("GHOST_R8_INDEX1_APPLIED shader=") == 1
    assert OLD_REDIRECT not in output and OLD_SECOND not in output
    return output


def selftest() -> None:
    fixture = (
        '#include <atomic>\n'
        + 'void Rasterizer::BindTextures() {\n'
        + '    // GHOST_NULL_IMAGE_BINDING shader=\n'
        + '    // GHOST_NULL_IMAGE_BINDING shader=\n'
        + '    // GHOST_R8_INDEX1_APPLIED shader=\n'
        + '    // image_bindings[num_images++]\n'
        + '    // image_bindings[num_images++]\n'
        + '    // image_bindings[num_images++]\n'
        + OLD_REDIRECT + '\n' + OLD_SECOND + '\n}\n'
    )
    candidate = patch_text(fixture)
    assert 'if (image_desc.is_written && !image->info.props.is_depth)' in candidate
    assert 'image_id = depth_image_id;' in candidate
    assert 'GHOST_STORAGE_IMAGE_INVALID shader=' in candidate
    assert 'GHOST_NULL_IMAGE_BINDING shader=' in candidate
    assert 'GHOST_R8_INDEX1_APPLIED shader=' in candidate
    for bad in (candidate, fixture.replace(OLD_REDIRECT, ""),
                fixture.replace(OLD_SECOND, ""),
                fixture.replace("GHOST_R8_INDEX1_APPLIED shader=", "")):
        try:
            patch_text(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("mismatched or already modified source accepted")
    print("SELFTEST PASS: storage uses non-depth linked alias, sampled redirect "
          "preserved, illegal storage diagnostic, exact v4/R8 source guards")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    args = sys.argv[1:]
    check_only = len(args) == 2 and args[0] == "--check-only"
    if not (len(args) == 1 or check_only):
        raise SystemExit("Usage: ghost-stencil-storage-alias-trial.py [--check-only] PATH")
    target = Path(args[-1])
    if target.is_symlink() or not target.is_file() or target.name != "vk_rasterizer.cpp":
        raise ValueError("target must be a regular vk_rasterizer.cpp")
    old = target.read_text(encoding="utf-8")
    new = patch_text(old)
    if check_only:
        print("GHOST_STENCIL_STORAGE_ALIAS_CHECK_PASS; no source edits")
        return 0
    stage = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                                         dir=target.parent,
                                         prefix=".ghost-stencil-alias-",
                                         suffix=".cpp", delete=False) as out:
            stage = Path(out.name)
            out.write(new)
        os.chmod(stage, stat.S_IMODE(target.stat().st_mode))
        os.replace(stage, target)
        stage = None
    finally:
        if stage is not None and stage.exists():
            stage.unlink()
    if target.read_text(encoding="utf-8") != new:
        raise IOError("source patch read-back mismatch")
    print("GHOST_STENCIL_STORAGE_ALIAS_PATCH=APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
