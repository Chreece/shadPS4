// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace Shader::Optimization {

// A contiguous flattened descriptor is possible only if *every* word comes
// from a genuine flat-buffer source. A zero-initialized offset for an
// immediate descriptor word must never be interpreted as a register load.
//
// This is independent of the descriptor type (V#, T#, S#) and preserves
// both immediate words and sparse sources through SharpFetch::MultiLoad.
template <typename Offset, std::size_t N>
[[nodiscard]] constexpr bool CanCoalesceSharpDwords(
    std::uint8_t load_mask, const std::array<Offset, N>& offsets,
    std::size_t num_dwords) noexcept {
    if (num_dwords == 0 || num_dwords > N || num_dwords > 8) {
        return false;
    }
    const auto all_loads = (1u << num_dwords) - 1u;
    if (load_mask != all_loads) {
        return false;
    }
    const std::uint32_t base = offsets[0];
    for (std::size_t i = 1; i < num_dwords; ++i) {
        if (std::uint32_t(offsets[i]) != base + i) {
            return false;
        }
    }
    return true;
}

} // namespace Shader::Optimization
