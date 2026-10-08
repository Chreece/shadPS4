#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Extend VERIFIED Ghost 9-mip R32->D32 copy to the observed inverse D32->R32.

The 2026-10-08 22:13 read-only capture proved an exact inverse image-copy
request with src D32_SFLOAT (126), dst R32_SFLOAT (100), 480x270, 9 mips,
1 layer/sample, 128MiB staging buffer, offset 0. Existing validated code
handles the first direction, then the original num_mips==1 assert triggers
on the reverse. Both formats have a single 32-bit floating-point texel.

After the first candidate patcher, rewrite ONLY its guarded multi-mip branch:
select Vulkan COLOR/DEPTH aspects according to source/destination direction.
Keep the original single-mip implementation and fallback assertion intact.
This is an EXPERIMENTAL Vulkan trial, not an upstream production fix.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys

REL = "src/video_core/renderer_vulkan/vk_runtime.cpp"
EXPECTED_CANDIDATE_SHA256 = "21741fb333fb9b5b6b5358126cbffc2a8ce85b4d8db51dfc24d49e512083db2c"
BEGIN = "    // GHOST_MIP_COPY: a single-sample R32 color image can back a D32 depth image."
END = "    ASSERT(num_mips == 1);"
ORIGINAL_CONDITION = (
    "    if (num_mips > 1 && src->info.pixel_format == vk::Format::eR32Sfloat &&\n"
    "        dst->info.pixel_format == vk::Format::eD32Sfloat) {"
)
DIRECTION_CONDITION = (
    "    const bool r32_to_d32 = src->info.pixel_format == vk::Format::eR32Sfloat &&\n"
    "                            dst->info.pixel_format == vk::Format::eD32Sfloat;\n"
    "    const bool d32_to_r32 = src->info.pixel_format == vk::Format::eD32Sfloat &&\n"
    "                            dst->info.pixel_format == vk::Format::eR32Sfloat;\n"
    "    if (num_mips > 1 && (r32_to_d32 || d32_to_r32)) {"
)
ORIGINAL_LOG = (
    '        LOG_INFO(Render_Vulkan, "GHOST_MIP_COPY mips={} size={}x{} bytes={}", num_mips,\n'
    "                 src->info.size.width, src->info.size.height, bytes_used);"
)
DIRECTION_LOG = (
    ORIGINAL_LOG + "\n"
    "        if (d32_to_r32) {\n"
    '            LOG_INFO(Render_Vulkan, "GHOST_MIP_REVERSE_COPY mips={} size={}x{} bytes={}",\n'
    "                     num_mips, src->info.size.width, src->info.size.height, bytes_used);\n"
    "        }"
)
ORIGINAL_COMMENT = (
    "    // GHOST_MIP_COPY: a single-sample R32 color image can back a D32 depth image.\n"
    "    // Both formats have exactly one 4-byte texel. The old buffer path only\n"
    "    // transferred mip zero and asserted when the guest supplied a mip chain.\n"
    "    // Restrict this first correction to the exact, observed reinterpretation."
)
REVISED_COMMENT = (
    "    // GHOST_MIP_COPY: only the confirmed R32_SFLOAT<->D32_SFLOAT directions.\n"
    "    // Each texel is 4 bytes; transfer all mip levels through the staging\n"
    "    // buffer, selecting color/depth aspects from the actual image formats.\n"
    "    // Keep every other case on the original guarded single-mip path."
)


def require_one(text: str, needle: str, label: str) -> None:
    n = text.count(needle)
    if n != 1:
        raise ValueError(f"Expected exactly one {label} anchor; observed {n}")


def modify(before: bytes, check_hash: bool = True) -> bytes:
    if check_hash and hashlib.sha256(before).hexdigest() != EXPECTED_CANDIDATE_SHA256:
        raise ValueError(
            "Candidate source SHA differs from the reviewed 9-mip baseline; refusing change."
        )
    original = before.decode("utf-8")
    require_one(original, BEGIN, "mip branch start")
    start = original.index(BEGIN)
    end = original.index(END, start)
    branch = original[start:end]
    if "GHOST_MIP_REVERSE_COPY" in original:
        raise ValueError("Reverse direction correction is already applied")

    anchors = [
        (ORIGINAL_COMMENT, REVISED_COMMENT, "comment"),
        (ORIGINAL_CONDITION, DIRECTION_CONDITION, "direction"),
        ('"GHOST_MIP_COPY unsupported image shape for R32->D32 {} mips"',
         '"GHOST_MIP_COPY unsupported R32/D32 image shape {} mips"', "shape"),
        ("        SmallVector<vk::BufferImageCopy, 16> depth_copies;",
         "        SmallVector<vk::BufferImageCopy, 16> destination_copies;", "destination list"),
        ("                    .aspectMask = vk::ImageAspectFlagBits::eColor,",
         "                    .aspectMask = r32_to_d32 ? vk::ImageAspectFlagBits::eColor\n"
         "                                            : vk::ImageAspectFlagBits::eDepth,", "source aspect"),
        ("            copy.imageSubresource.aspectMask = vk::ImageAspectFlagBits::eDepth;",
         "            copy.imageSubresource.aspectMask =\n"
         "                r32_to_d32 ? vk::ImageAspectFlagBits::eDepth\n"
         "                           : vk::ImageAspectFlagBits::eColor;", "destination aspect"),
        ("            depth_copies.push_back(copy);",
         "            destination_copies.push_back(copy);", "destination list push"),
        ("                                 vk::ImageLayout::eTransferDstOptimal, depth_copies);",
         "                                 vk::ImageLayout::eTransferDstOptimal, destination_copies);",
         "copy-to-image list"),
        (ORIGINAL_LOG, DIRECTION_LOG, "reverse logger"),
    ]
    for old, new, label in anchors:
        require_one(branch, old, label)
        branch = branch.replace(old, new, 1)

    # Aspect matches must be true in both observed directions; a color/depth
    # format combination alone is not sufficient if image metadata disagrees.
    shape_prefix = (
        "                       dst->info.size.depth == 1 && !src->info.props.is_volume &&\n"
        "                       !dst->info.props.is_volume &&"
    )
    shape_new = (
        "                       dst->info.size.depth == 1 && !src->info.props.is_volume &&\n"
        "                       !dst->info.props.is_volume &&\n"
        "                       src->info.props.is_depth == d32_to_r32 &&\n"
        "                       dst->info.props.is_depth == r32_to_d32 &&"
    )
    require_one(branch, shape_prefix, "actual image aspects")
    branch = branch.replace(shape_prefix, shape_new, 1)

    after = original[:start] + branch + original[end:]
    if after.count(END) != 1 or after.count("GHOST_MIP_REVERSE_COPY mips=") != 1:
        raise ValueError("Modified Vulkan source lost fallback assert or reverse diagnostic")
    if after.count("GHOST_MIP_COPY mips=") != 1:
        raise ValueError("Original multi-mip diagnostic changed")
    return after.encode("utf-8")


def selftest() -> None:
    # A precise sample of the vetted C++ branch validates all replacements.
    fixture = (
        ORIGINAL_COMMENT + "\n"
        + ORIGINAL_CONDITION + "\n"
        + '                       dst->info.size.depth == 1 && !src->info.props.is_volume &&\n'
        + '                       !dst->info.props.is_volume &&\n'
        + '                   "GHOST_MIP_COPY unsupported image shape for R32->D32 {} mips",\n'
        + '        SmallVector<vk::BufferImageCopy, 16> depth_copies;\n'
        + '                    .aspectMask = vk::ImageAspectFlagBits::eColor,\n'
        + '            copy.imageSubresource.aspectMask = vk::ImageAspectFlagBits::eDepth;\n'
        + '            depth_copies.push_back(copy);\n'
        + ORIGINAL_LOG + "\n"
        + '                                 vk::ImageLayout::eTransferDstOptimal, depth_copies);\n'
        + '    }\n' + END
    ).encode()
    updated = modify(fixture, check_hash=False).decode()
    assert updated.count("r32_to_d32") >= 3
    assert updated.count("d32_to_r32") >= 3
    assert "eColor\\n                                            : vk::ImageAspectFlagBits::eDepth" in updated
    assert "r32_to_d32 ? vk::ImageAspectFlagBits::eDepth" in updated
    assert "destination_copies.push_back(copy)" in updated
    assert "GHOST_MIP_REVERSE_COPY mips=" in updated
    assert updated.count(END) == 1
    try:
        modify(updated.encode(), check_hash=False)
    except ValueError:
        pass
    else:
        raise AssertionError("Second patch application did not fail")
    print("SELFTEST PASS: reverse D32->R32 plus original R32->D32, correct image "
          "aspects, guarded shape, nine-mip staging lists, fallback assert intact")


def main() -> None:
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != "--check-only"):
        raise SystemExit("Usage: patch.py ROOT BACKUP_DIR [--check-only]")
    root, backup_root = (Path(value).resolve() for value in sys.argv[1:3])
    target = root / REL
    before = target.read_bytes()
    after = modify(before)
    selftest()
    if len(sys.argv) == 4:
        print("GHOST_MIP_REVERSE_SOURCE_CHECK_PASS; no source edits")
        return
    backup_root.mkdir(parents=True, exist_ok=True)
    existing = backup_root / "mip-candidate-before-reverse.cpp"
    existing.write_bytes(before)
    if existing.read_bytes() != before:
        raise OSError("Could not verify nine-mip candidate backup before modifying it")
    target.write_bytes(after)
    if target.read_bytes() != after:
        raise OSError("Reverse direction patch verification failed")
    print("GHOST_MIP_REVERSE_SOURCE_PATCHED=", str(target))
    print("Original single-mip fallback and assertion remain enabled")


if __name__ == "__main__":
    main()
