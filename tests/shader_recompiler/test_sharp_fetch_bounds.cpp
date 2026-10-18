// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <random>

#include "shader_recompiler/sharp_fetch_bounds.h"

using Shader::Optimization::SharpFetchOffsetsInBounds;
using Shader::Optimization::SharpFetchWordRangeFits;

static_assert(SharpFetchWordRangeFits(20, 16, 4));
static_assert(!SharpFetchWordRangeFits(20, 17, 4));
static_assert(SharpFetchWordRangeFits(0, 0, 0));
static_assert(!SharpFetchWordRangeFits(0, 0, 1));
static_assert(!SharpFetchWordRangeFits(20, std::numeric_limits<std::size_t>::max(), 4));
static_assert(!SharpFetchWordRangeFits(20, 1, std::numeric_limits<std::size_t>::max()));

constexpr std::array<std::uint16_t, 4> good{16, 17, 18, 19};
constexpr std::array<std::uint16_t, 4> bad{16, 17, 65535, 19};
static_assert(SharpFetchOffsetsInBounds(20, good, 0xf, 4, true));
static_assert(!SharpFetchOffsetsInBounds(19, good, 0xf, 4, true));
static_assert(SharpFetchOffsetsInBounds(20, good, 0xf, 4, false));
static_assert(!SharpFetchOffsetsInBounds(20, bad, 0xf, 4, false));
static_assert(SharpFetchOffsetsInBounds(20, bad, 0xb, 4, false));
static_assert(SharpFetchOffsetsInBounds(0, bad, 0, 4, false));
static_assert(!SharpFetchOffsetsInBounds(20, bad, 0xf, 5, false));
static_assert(!SharpFetchOffsetsInBounds(20, bad, 0xf, 33, false));
static_assert(SharpFetchOffsetsInBounds(20, good, 0, 0, false));

int main() {
    std::mt19937 rng{0x5362026u};
    for (unsigned n = 0; n < 100000; ++n) {
        std::array<std::uint16_t, 4> offsets{};
        for (auto& off : offsets) {
            off = static_cast<std::uint16_t>(rng() % 100u);
        }
        const auto flat_words = static_cast<std::size_t>(rng() % 100u);
        const auto mask = rng() % 16u;
        bool expected = true;
        for (std::size_t i = 0; i < 4; ++i) {
            if ((mask & (1u << i)) && offsets[i] >= flat_words) expected = false;
        }
        assert(SharpFetchOffsetsInBounds(flat_words, offsets, mask, 4, false) == expected);
        const bool contiguous = offsets[0] <= flat_words && 4 <= flat_words - offsets[0];
        assert(SharpFetchOffsetsInBounds(flat_words, offsets, mask, 4, true) == contiguous);
    }
    std::puts("SHARP_FETCH_BOUNDS=PASS cases=100000 "
              "invalid_ranges_fail_closed=PASS valid_and_immediate_values_preserved=PASS");
}
