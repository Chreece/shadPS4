// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>

namespace Shader::Optimization {
[[nodiscard]] constexpr std::uint32_t ApplySharpWordMask(
    std::uint32_t value, std::uint32_t cleared_bits) noexcept {
    return value & ~cleared_bits;
}
template <std::size_t N>
[[nodiscard]] constexpr bool SharpMasksAreZero(
    const std::array<std::uint32_t, N>& masks, std::size_t count) noexcept {
    if (count > N) return false;
    for (std::size_t i = 0; i < count; ++i) {
        if (masks[i] != 0) return false;
    }
    return true;
}
} // namespace Shader::Optimization
