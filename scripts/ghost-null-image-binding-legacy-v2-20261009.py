#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Trial-only invalid T# binding correction for the PINNED older Ghost build.

Unlike the 2026-10-09 v1 patcher, this targets the actual legacy
vk_rasterizer.cpp Git blob bd430118... *after* the pinned R8 patch,
where ImageBindingInfo is indexed with image_bindings[num_images++].
The previous patcher mistakenly targeted a newer refactored version and
therefore failed before the game could launch.

Preserve each shader image's declared descriptor array length and descriptor
type when its guest T# is null or invalid. No changes to valid resources.
The enclosing one-run shell owns all source/binary rollback.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile

FILE_NAME = "vk_rasterizer.cpp"
MARKER = "GHOST_NULL_IMAGE_BINDING shader="
OLD = """            image_bindings[num_images++].image_id = {};
            image_descriptor_array_sizes.push_back(1);
"""
NEW = """            // GHOST_NULL_IMAGE_BINDING_TRIAL: mirror the descriptor layout even when
            // the guest image T# is null/invalid; do not shift following bindings.
            const u32 ghost_null_count = image_desc.NumBindings(stage);
            for (u32 i = 0; i < ghost_null_count; ++i) {
                auto& [image_id, null_desc] = image_bindings[num_images++];
                image_id = {};
                null_desc.type = image_desc.is_written
                                     ? VideoCore::TextureCache::BindingType::Storage
                                     : VideoCore::TextureCache::BindingType::Texture;
            }
            image_descriptor_array_sizes.push_back(ghost_null_count);
            static std::atomic<u32> ghost_null_logged{0};
            if (ghost_null_logged.fetch_add(1, std::memory_order_relaxed) < 16) {
                LOG_WARNING(Render_Vulkan,
                            "GHOST_NULL_IMAGE_BINDING shader={:#x} count={} storage={} addr={:#x}",
                            stage.pgm_hash, ghost_null_count, image_desc.is_written,
                            tsharp.Address());
            }
"""
HEADER = """    u32 num_images{};
    for (const auto& image_desc : stage.images) {
        const auto tsharp = image_desc.GetSharp(stage);
"""
NORMAL = """            auto& [image_id, desc] = image_bindings[num_images++];
            std::construct_at(&desc, tsharp, image_desc);"""


def patch_text(source: str) -> str:
    if MARKER in source or "GHOST_NULL_IMAGE_BINDING_TRIAL" in source:
        raise ValueError("null-image trial already applied")
    checks = {
        "legacy indexed image bindings": source.count(HEADER) == 1,
        "two legacy invalid/null image paths": source.count(OLD) == 2,
        "valid image construction": source.count(NORMAL) == 1,
        "R8 sampler patch already applied":
            source.count("GHOST_R8_INDEX1_APPLIED shader=") == 1,
        "R8-provided atomic include": source.count("#include <atomic>") == 1,
    }
    if not all(checks.values()):
        raise ValueError("unexpected post-R8 source; " +
                         ", ".join(k for k, ok in checks.items() if not ok))
    output = source.replace(OLD, NEW)
    assert output.count(MARKER) == 2
    assert output.count("image_descriptor_array_sizes.push_back(ghost_null_count)") == 2
    assert output.count(NORMAL) == 1
    assert OLD not in output
    return output


def selftest() -> None:
    fixture = (
        '#include <atomic>\n'
        "void Rasterizer::BindTextures() {\n"
        + HEADER
        + "        if (null_tsharp) {\n" + OLD + "        }\n"
        + "        if (bad_mapping) {\n" + OLD + "        }\n"
        + NORMAL
        + '\n        LOG_WARNING(Render_Vulkan, "GHOST_R8_INDEX1_APPLIED shader=");\n'
        + "    }\n}\n"
    )
    candidate = patch_text(fixture)
    assert candidate.count(MARKER) == 2
    assert candidate.count("image_desc.NumBindings(stage)") == 2
    assert candidate.count("null_desc.type = image_desc.is_written") == 2
    assert "GHOST_R8_INDEX1_APPLIED shader=" in candidate
    assert candidate.count(NORMAL) == 1
    for bad in (
        fixture.replace(OLD, "", 1),
        fixture.replace("#include <atomic>\n", ""),
        fixture.replace("GHOST_R8_INDEX1_APPLIED shader=", "NOT_R8"),
        candidate,
    ):
        try:
            patch_text(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("unverified source was accepted")
    print("SELFTEST PASS: two real legacy invalid T# sites, matching descriptor"
          " count and storage/sample type, post-R8 guard, duplicate rejection")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--check-only":
        check_only = True
        target = Path(args[1])
    elif len(args) == 1:
        check_only = False
        target = Path(args[0])
    else:
        raise SystemExit("Usage: patcher.py [--check-only] PATH_TO_vk_rasterizer.cpp")
    if not target.is_file() or target.name != FILE_NAME or target.is_symlink():
        raise ValueError("target is not a regular vk_rasterizer.cpp file")
    before = target.read_text(encoding="utf-8")
    after = patch_text(before)
    if check_only:
        print("GHOST_NULL_IMAGE_BINDING_V2_CHECK_PASS: actual post-R8 source verified")
        return 0
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8",
                dir=target.parent, prefix=".ghost-null-v2-", suffix=".cpp",
                delete=False) as output:
            tmp = Path(output.name)
            output.write(after)
        os.chmod(tmp, stat.S_IMODE(target.stat().st_mode))
        os.replace(tmp, target)
        tmp = None
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
    if target.read_text(encoding="utf-8") != after:
        raise IOError("trial patch read-back differs")
    print("GHOST_NULL_IMAGE_BINDING_PATCH=APPLIED: legacy source, two sites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
