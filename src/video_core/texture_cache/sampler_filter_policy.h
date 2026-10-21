// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <vulkan/vulkan.hpp>

namespace VideoCore {

// Vulkan requires VK_FILTER_NEAREST when sampling an unsigned/signed integer
// image format without SAMPLED_IMAGE_FILTER_LINEAR_BIT. The guest's S# can be
// reused by shaders with different numeric T# types, so this choice belongs
// to a numeric-format-specific Vulkan sampler, not the shared guest S#.
[[nodiscard]] constexpr vk::Filter ImageSamplerFilter(vk::Filter requested,
                                                       bool is_integer) {
    return is_integer ? vk::Filter::eNearest : requested;
}

[[nodiscard]] constexpr vk::SamplerMipmapMode ImageSamplerMipmapMode(
    vk::SamplerMipmapMode requested, bool is_integer) {
    return is_integer ? vk::SamplerMipmapMode::eNearest : requested;
}

[[nodiscard]] constexpr bool SamplerAnisotropyAllowed(bool requested,
                                                       bool is_integer) {
    return requested && !is_integer;
}

} // namespace VideoCore
