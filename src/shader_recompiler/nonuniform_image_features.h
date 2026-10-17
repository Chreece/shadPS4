// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

namespace Shader::Optimization {
// Vulkan image-array indexing must not be enabled when ANY of the
// descriptorIndexing, nonuniform sampled-array, or core dynamic-array
// features are absent. The VkDevice enables only supported features.
[[nodiscard]] constexpr bool CanUseNonuniformSampledImageArray(
    bool descriptor_indexing, bool image_nonuniform, bool image_dynamic) noexcept {
    return descriptor_indexing && image_nonuniform && image_dynamic;
}
} // namespace Shader::Optimization
