#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-trial Ghost null image descriptor count/type correction.

The pipeline layout reserves ImageResource.NumBindings() slots per image and
uses ImageResource.is_written for the descriptor type. Rasterizer::BindTextures
currently substitutes exactly ONE default sampled-image descriptor on an
invalid T#, even when the layout expects storage or multiple mip slots.

Apply only to the already-patched R8 diagnostic source. The enclosing trial
backs it up, compiles, deploys, records validation, and restores the baseline.
This is an experimental correctness correction, not proof of a GPU-loss fix.
"""
from __future__ import annotations
import os
from pathlib import Path
import sys
import tempfile

ANCHOR = """    for (const auto& image_desc : stage.images) {
        const auto tsharp = image_desc.GetSharp(stage);
"""
PLACEHOLDER = """            image_bindings.emplace_back(std::piecewise_construct, std::tuple{}, std::tuple{});
            image_descriptor_array_sizes.push_back(1);
"""
INSERT = """        // Match the pipeline layout for null/invalid T# descriptors too.
        // A dynamic-mip binding may occupy multiple descriptor slots.
        const auto append_null_image_bindings = [&] {
            const u32 count = image_desc.NumBindings(stage);
            for (u32 i = 0; i < count; ++i) {
                auto& [id, null_desc] = image_bindings.emplace_back(
                    std::piecewise_construct, std::tuple{}, std::tuple{});
                null_desc.type = image_desc.is_written
                                     ? VideoCore::TextureCache::BindingType::Storage
                                     : VideoCore::TextureCache::BindingType::Texture;
            }
            image_descriptor_array_sizes.push_back(count);
            static std::atomic<u32> reports{0};
            if (reports.fetch_add(1, std::memory_order_relaxed) < 32) {
                LOG_WARNING(Render_Vulkan,
                            "GHOST_NULL_IMAGE_BINDING shader={:#x} count={} storage={} addr={:#x}",
                            stage.pgm_hash, count, image_desc.is_written, tsharp.Address());
            }
        };
"""


def patch_text(src: str, require_r8: bool = True) -> str:
    if "GHOST_NULL_IMAGE_BINDING" in src:
        raise ValueError("already patched")
    if require_r8 and "GHOST_R8_INDEX1_APPLIED" not in src:
        raise ValueError("expected pre-existing validated sampler trial marker missing")
    if src.count(ANCHOR) != 1:
        raise ValueError(f"shader image loop does not match ({src.count(ANCHOR)})")
    if src.count(PLACEHOLDER) != 2:
        raise ValueError(f"invalid descriptor sites differ ({src.count(PLACEHOLDER)})")
    if "#include <atomic>" in src:
        raise ValueError("unexpected atomic include; inspect source first")
    out = src.replace(ANCHOR, ANCHOR + INSERT)
    out = out.replace(PLACEHOLDER, "            append_null_image_bindings();\n")
    out = out.replace('#include "common/debug.h"', '#include <atomic>\n\n#include "common/debug.h"')
    assert out.count('append_null_image_bindings();') == 2
    assert out.count('GHOST_NULL_IMAGE_BINDING shader=') == 1
    assert 'image_descriptor_array_sizes.push_back(1);' not in out
    return out


def selftest():
    fixture = ('#include "common/debug.h"\n' + ANCHOR +
               '        // a\n' + PLACEHOLDER +
               '        // b\n' + PLACEHOLDER +
               '        LOG_WARNING(Render_Vulkan, "GHOST_R8_INDEX1_APPLIED");\n')
    patched = patch_text(fixture)
    assert patched.count('append_null_image_bindings();') == 2
    assert patched.count('image_descriptor_array_sizes.push_back(count);') == 1
    assert 'null_desc.type = image_desc.is_written' in patched
    assert 'std::atomic<u32>' in patched
    for broken in (fixture.replace(PLACEHOLDER, '', 1),
                   fixture.replace(ANCHOR, 'not a stage loop\n')):
        try:
            patch_text(broken)
        except ValueError:
            pass
        else:
            raise AssertionError("ambiguous source incorrectly accepted")
    print("SELFTEST PASS: null texture and storage placeholders retain layout count/type; exact-site guard")


def main():
    if sys.argv[1:] == ['--self-test']:
        selftest()
        return 0
    check = (len(sys.argv) == 3 and sys.argv[1] == '--check-only')
    target = Path(sys.argv[2] if check else sys.argv[1]) if len(sys.argv) in (2, 3) else None
    if target is None or not target.is_file():
        raise SystemExit('Usage: ghost_null_binding_patcher.py [--check-only] PATH_TO_vk_rasterizer.cpp')
    if target.name != 'vk_rasterizer.cpp':
        raise ValueError('unexpected target source file')
    raw = target.read_text()
    result = patch_text(raw)
    if check:
        print('CHECK_PASS source-specific null descriptor patch applicable')
        return 0
    mode = target.stat().st_mode
    stage = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=target.parent, prefix='.ghost-null-',
                                         suffix='.cpp', delete=False) as out:
            stage = Path(out.name)
            out.write(result)
        os.chmod(stage, mode)
        os.replace(stage, target)
        stage = None
    finally:
        if stage and stage.exists():
            stage.unlink()
    print('GHOST_NULL_IMAGE_BINDING_PATCH=APPLIED; baseline must be restored by enclosing trial')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
