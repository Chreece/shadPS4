#!/usr/bin/env python3
"""Fail-closed source contract for 1D-to-layered-2D image storage growth."""
from pathlib import Path

root = Path(__file__).resolve().parents[1]
def read(relative):
    path = root / relative
    assert path.is_file() and not path.is_symlink(), path
    return path.read_text(encoding="utf-8")

s = read("src/video_core/texture_cache/texture_cache.cpp")
h = read("src/video_core/texture_cache/compatible_layer_growth.h")
unit = read("tests/texture_cache_1d_to_2d_layer_growth.cpp")
runtime = read("src/video_core/renderer_vulkan/vk_runtime.cpp")
assert '#include "video_core/texture_cache/compatible_layer_growth.h"' in s
assert s.count("TextureOverlap::CanExpandColor1DToColor2DLayers(") == 1
assert "AmdGpu::ImageType::Color1D" in s and "AmdGpu::ImageType::Color2D" in s
assert "TEXTURE_1D_TO_2D_SAME_STORAGE_EXPANSION" in s
growth = s[s.index("if (TextureOverlap::CanExpandColor1DToColor2DLayers("):
           s.index("// Size is greater but resources are not")]
assert growth.count("return {ExpandImage(image_info, cache_image_id), -1, -1};") == 1
# The production path must keep using the real GPU-copy-aware expansion.
start = s.index("ImageId TextureCache::ExpandImage(")
end = s.index("ImageId TextureCache::FindImage(", start)
expand = s[start:end]
for token in ("RegisterImage(new_image_id);", "RefreshImage(new_image);",
              "runtime.CopyImage(&src_image, &new_image);",
              "src_image.binding.needs_rebind = 1u;",
              "FreeImage(image_id);", "TrackImage(new_image_id);"):
    assert token in expand, token
assert expand.index("RefreshImage(new_image);") < expand.index("runtime.CopyImage(")
assert expand.index("runtime.CopyImage(") < expand.index("FreeImage(image_id);")
assert s.index("// Size and resources are less than or equal, use image view.") < s.index(
    "TextureOverlap::CanExpandColor1DToColor2DLayers(")
assert s.index("TextureOverlap::CanExpandColor1DToColor2DLayers(") < s.index(
    'UNREACHABLE_MSG("Encountered unresolvable image overlap')
# The Vulkan copy respects the minimum source/destination array-layer count,
# so only the existing first layer is copied over the new guest-data upload.
assert "const u32 copy_layers = std::min(src_layers, dst_layers);" in runtime
assert "region.srcSubresource.layerCount = copy_layers;" in runtime
assert "region.dstSubresource.layerCount = copy_layers;" in runtime
# Strict structural equivalence; no per-title conditions or arbitrary format reinterpretation.
for term in (
    "cached.type != color1d || requested.type != color2d",
    "cached.guest_address != requested.guest_address",
    "cached.resources.layers != 1",
    "requested.resources.layers <= cached.resources.layers",
    "cached.resources.levels != 1 || requested.resources.levels != 1",
    "requested.size.height != 1",
    "cached.pixel_format != requested.pixel_format",
    "cached.tile_mode != requested.tile_mode",
    "cached.array_mode != requested.array_mode",
    "cached.num_samples != requested.num_samples",
    "cached.props.is_depth || requested.props.is_depth",
    "cached.props.is_block || requested.props.is_block",
    "cached.props.is_volume || requested.props.is_volume",
    "requested.guest_size % requested.resources.layers == 0",
    "requested.guest_size / requested.resources.layers == cached.guest_size",
):
    assert term in h, term
assert "CUSA" not in h and "shader" not in h.lower()
for marker in (
    "TEXTURE_1D_TO_2D_SAME_STORAGE_LAYER_GROWTH=PASS",
    "TEXTURE_DIFFERENT_TILING_OR_FORMAT_REJECTED=PASS",
    "TEXTURE_GPU_DATA_PRESERVATION_PRECONDITIONS=PASS",
):
    assert marker in unit, marker
print("TEXTURE_1D_TO_2D_OVERLAP_FAIL_CLOSED=PASS")
print("TEXTURE_EXISTING_GPU_DATA_PRESERVED_ON_LAYER_GROWTH=PASS")
