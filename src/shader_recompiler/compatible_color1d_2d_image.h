// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>

namespace Shader::CompatibleColor1D2D {
// Sea Islands T# dword 3, bits 28..31 contain the actual guest image type.
// The host deliberately backs BOTH Color1D and Color2D with VkImageViewType::e2D;
// a one-dimensional guest texture has height 1 and requires y=0 sampling.
inline constexpr std::uint32_t TypeShift = 28;
inline constexpr std::uint32_t TypeMask = 0xFu;
inline constexpr std::uint32_t Dword3Offset = 3;
inline constexpr std::uint32_t DwordsPerImage = 8;

template <typename ImageType>
[[nodiscard]] constexpr bool IsCompatibleSampled2D(ImageType reference, ImageType candidate,
                                                   ImageType color1d, ImageType color2d) noexcept {
    const auto supported = [=](ImageType t) { return t == color1d || t == color2d; };
    return supported(reference) && supported(candidate);
}

[[nodiscard]] constexpr std::uint32_t GuestType(std::uint32_t tsharp_dword3) noexcept {
    return (tsharp_dword3 >> TypeShift) & TypeMask;
}

[[nodiscard]] constexpr float SampleY(std::uint32_t type, std::uint32_t color1d,
                                      float original_y) noexcept {
    return type == color1d ? 0.0f : original_y;
}
} // namespace Shader::CompatibleColor1D2D
