// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

namespace Shader::Optimization {
// Exactly the image-sampling subset implemented by PatchImageSharp's indexed
// Vulkan sampled-image path. Call at discovery AND binding to avoid accepting
// dynamically indexed descriptors that cannot be bound by that later path.
// No shader hash, title-specific condition, or fabricated guest value.
template <typename TextureFlags>
[[nodiscard]] constexpr bool IsBindableGuestIndexedSampleMode(const TextureFlags& flags) noexcept {
    return !flags.is_depth && !flags.is_array && !flags.is_gather && !flags.has_lod &&
           !flags.force_level0 && !flags.has_lod_clamp && !flags.is_r128 && !flags.is_unnormalized;
}
} // namespace Shader::Optimization
