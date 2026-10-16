// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "video_core/renderer_vulkan/vk_common.h"

namespace Vulkan {

// Shader resource access determines the descriptor type. A retained
// texture-cache entry may contain a stale type after an invalid guest T#.
// Both pipeline layouts and descriptor updates must use this contract.
[[nodiscard]] constexpr vk::DescriptorType ShaderImageDescriptorType(bool is_written) noexcept {
    return is_written ? vk::DescriptorType::eStorageImage
                      : vk::DescriptorType::eSampledImage;
}

static_assert(ShaderImageDescriptorType(true) == vk::DescriptorType::eStorageImage);
static_assert(ShaderImageDescriptorType(false) == vk::DescriptorType::eSampledImage);

} // namespace Vulkan
