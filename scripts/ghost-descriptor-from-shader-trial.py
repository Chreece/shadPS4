#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ghost descriptor-type correction trial based on the Oct 9 RADV dump.

VkWriteDescriptorSet descriptorType-00319 showed storage writes for a sampled
fragment image in pipeline fs_0x55308206. The graphics pipeline layout uses
ImageResource.is_written, but BindTextures takes the descriptor write's type
from cached ImageDesc. An invalid/null image skips ImageDesc construction, so
the latter can retain a stale type from a preceding draw.

Only image descriptor *type* selection changes here. No shader, guest, image
contents, usage flags, or synchronization behavior is altered. This is a
temporary validated playtest; revert immediately after the run.
"""
import hashlib
from pathlib import Path
import sys

REL = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
EXPECTED_BASELINE_SHA256 = "8d73198df4f9114aa1a2e79892ed489a89a73fd01c1a982f616116fc08e7588d"
FUNCTION = "void Rasterizer::BindTextures(const Shader::Info& stage, Shader::Backend::Bindings& binding) {"
FOLLOWING = "RenderState Rasterizer::BeginRendering("
SECOND_OLD = """        bool is_storage = desc.type == VideoCore::TextureCache::BindingType::Storage;
        if (!image_id) {
            image_infos.emplace_back(VK_NULL_HANDLE, VK_NULL_HANDLE, vk::ImageLayout::eGeneral);
        } else {
"""
SECOND_NEW = """        if (!image_id) {
            image_infos.emplace_back(VK_NULL_HANDLE, VK_NULL_HANDLE, vk::ImageLayout::eGeneral);
        } else {
            const bool is_storage =
                desc.type == VideoCore::TextureCache::BindingType::Storage;
"""
THIRD_OLD = """    u32 image_info_idx = first_image_idx;
    u32 image_binding_idx = 0;
    for (u32 array_size : image_descriptor_array_sizes) {
        const auto& [_, desc] = image_bindings[image_binding_idx];
        const bool is_storage = desc.type == VideoCore::TextureCache::BindingType::Storage;
"""
THIRD_NEW = """    u32 image_info_idx = first_image_idx;
    for (u32 image_index = 0; image_index < image_descriptor_array_sizes.size(); ++image_index) {
        const u32 array_size = image_descriptor_array_sizes[image_index];
        // GHOST_DESCRIPTOR_FROM_SHADER: match pipeline layout even when
        // ImageDesc was not initialized for an invalid/null guest T#.
        const bool is_storage = stage.images[image_index].is_written;
"""
REMOVED_INCREMENT = "        image_binding_idx += array_size;\n"
MARKER = "GHOST_DESCRIPTOR_FROM_SHADER"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def modify(data: bytes) -> bytes:
    source = data.decode("utf-8")
    assert source.count(FUNCTION) == 1, "Unexpected BindTextures function count"
    assert source.count(FOLLOWING) == 1, "Unexpected following-function count"
    assert MARKER not in source, "Descriptor trial already applied"
    begin = source.index(FUNCTION)
    end = source.index(FOLLOWING, begin)
    part = source[begin:end]
    for name, pattern in (
        ("second-pass invalid-description dereference", SECOND_OLD),
        ("third-pass storage classification", THIRD_OLD),
        ("obsolete image-binding increment", REMOVED_INCREMENT),
    ):
        assert part.count(pattern) == 1, f"Unexpected {name} anchor"
    part = part.replace(SECOND_OLD, SECOND_NEW, 1)
    part = part.replace(THIRD_OLD, THIRD_NEW, 1)
    part = part.replace(REMOVED_INCREMENT, "", 1)
    result = source[:begin] + part + source[end:]
    assert result.count(MARKER) == 1
    assert result.count("stage.images[image_index].is_written") == 1
    assert result.count("image_binding_idx") == 0
    assert result.count("void Rasterizer::BindTextures(") == 1
    return result.encode("utf-8")


def selftest() -> None:
    fixture = (
        FUNCTION + "\n"
        + "    // GHOST_R8_INDEX1_TRIAL\n"
        + SECOND_OLD + THIRD_OLD
        + "        set_write.pImageInfo = &image_infos[image_info_idx];\n"
        + "        image_info_idx += array_size;\n"
        + REMOVED_INCREMENT
        + "    }\n"
        + FOLLOWING
    ).encode()
    changed = modify(fixture).decode()
    assert "stage.images[image_index].is_written" in changed
    assert "if (!image_id) {" in changed
    assert "const bool is_storage =" in changed
    assert "image_binding_idx" not in changed
    try:
        modify(changed.encode())
    except AssertionError:
        pass
    else:
        raise AssertionError("Duplicate application was accepted")
    print("SELFTEST PASS: shader metadata drives descriptor type; null image never "
          "reads stale desc.type; no guest or Vulkan image behavior changed")


def run(root: Path, baseline_dir: Path, check_only: bool) -> None:
    target = root / REL
    baseline = baseline_dir / REL
    assert sha(baseline.read_bytes()) == EXPECTED_BASELINE_SHA256, (
        "Original renderer source differs from the Oct 9 evidence")
    before = target.read_bytes()
    assert "GHOST_R8_INDEX1_TRIAL" in before.decode(), (
        "Expected R8 image-filter trial not applied yet")
    after = modify(before)
    selftest()
    if check_only:
        print("GHOST_DESCRIPTOR_SOURCE_CHECK_PASS (no writes)")
        return
    # The original source is already backed up by the caller's R8 patcher.
    # Make an additional byte-verified immediate preimage before changing it.
    intermediate = baseline_dir.parent / "descriptor-preimage-r8.cpp"
    intermediate.write_bytes(before)
    assert intermediate.read_bytes() == before, "Failed preimage backup"
    target.write_bytes(after)
    assert target.read_bytes() == after, "Failed descriptor patch readback"
    print("GHOST_DESCRIPTOR_FROM_SHADER_PATCHED", target)


def main():
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != "--check-only"):
        raise SystemExit("Usage: patch.py ROOT ORIGINAL_R8_BACKUP_DIR [--check-only]")
    run(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(),
        len(sys.argv) == 4)


if __name__ == "__main__":
    main()
