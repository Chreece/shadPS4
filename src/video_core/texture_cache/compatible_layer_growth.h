// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

namespace VideoCore::TextureOverlap {
// A guest 1D image and a layered 2D view use the SAME Vulkan 2D backing
// geometry when height=depth=1 and all per-layer storage properties match.
// Require a strict one-layer old image, identical padded bytes per slice,
// and valid growth. No host data or GPU lane index is fabricated.
template <typename Info, typename ImageType>
[[nodiscard]] constexpr bool CanExpandColor1DToColor2DLayers(
    const Info& requested, const Info& cached, ImageType color1d, ImageType color2d) {
    if (cached.type != color1d || requested.type != color2d ||
        cached.guest_address != requested.guest_address ||
        cached.guest_address == 0 || cached.resources.layers != 1 ||
        requested.resources.layers <= cached.resources.layers ||
        cached.resources.levels != 1 || requested.resources.levels != 1 ||
        cached.size != requested.size || requested.size.height != 1 ||
        requested.size.depth != 1 || cached.pitch != requested.pitch ||
        cached.num_bits != requested.num_bits || cached.num_samples != requested.num_samples ||
        cached.pixel_format != requested.pixel_format ||
        cached.tile_mode != requested.tile_mode || cached.array_mode != requested.array_mode ||
        cached.bank_swizzle != requested.bank_swizzle ||
        cached.alt_tile != requested.alt_tile ||
        cached.props.is_tiled != requested.props.is_tiled ||
        cached.props.is_pow2 != requested.props.is_pow2 ||
        cached.props.is_block || requested.props.is_block ||
        cached.props.is_depth || requested.props.is_depth ||
        cached.props.is_volume || requested.props.is_volume ||
        cached.props.has_stencil || requested.props.has_stencil ||
        cached.guest_size == 0 || requested.guest_size <= cached.guest_size) {
        return false;
    }
    // The same tiling and pitch must occupy exactly the same padded bytes
    // per layer; otherwise a Vulkan copy could silently alias other data.
    return requested.guest_size % requested.resources.layers == 0 &&
           requested.guest_size / requested.resources.layers == cached.guest_size;
}
} // namespace VideoCore::TextureOverlap
