// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace Shader::Optimization {

[[nodiscard]] constexpr bool SharpFetchWordRangeFits(std::size_t flat_words,
                                                      std::size_t start,
                                                      std::size_t words) noexcept {
    return start <= flat_words && words <= flat_words - start;
}

// Validate every flattened-userdata read before loading a guest SHARP.
// A coalesced descriptor uses a contiguous span; a mixed/immediate SHARP
// reads only the locations selected by the load mask. No guest memory access.
template <typename Offset, std::size_t N>
[[nodiscard]] constexpr bool SharpFetchOffsetsInBounds(
    std::size_t flat_words, const std::array<Offset, N>& offsets,
    std::uint32_t load_mask, std::size_t descriptor_words,
    bool single_load) noexcept {
    if (descriptor_words > N || descriptor_words > 32) return false;
    if (single_load) {
        if constexpr (N == 0) return false;
        return SharpFetchWordRangeFits(flat_words,
                                      static_cast<std::size_t>(offsets[0]),
                                      descriptor_words);
    }
    for (std::size_t i = 0; i < descriptor_words; ++i) {
        if ((load_mask & (std::uint32_t{1} << i)) &&
            !SharpFetchWordRangeFits(flat_words,
                                     static_cast<std::size_t>(offsets[i]), 1)) {
            return false;
        }
    }
    return true;
}

} // namespace Shader::Optimization
