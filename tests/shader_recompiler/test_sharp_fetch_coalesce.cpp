// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>

#include "shader_recompiler/ir/passes/sharp_fetch_coalesce.h"

using Shader::Optimization::CanCoalesceSharpDwords;

int main() {
    constexpr std::array<std::uint16_t, 8> regular{12,13,14,15,16,17,18,19};
    constexpr std::array<std::uint16_t, 8> mixed{0,1,2,3,4,5,6,7};
    constexpr std::array<std::uint16_t, 8> sparse{12,13,17,15,16,18,19,20};
    constexpr std::array<std::uint16_t, 8> wrapping{65534,65535,0,1,0,0,0,0};

    static_assert(CanCoalesceSharpDwords(0x0f, regular, 4));
    static_assert(CanCoalesceSharpDwords(0xff, regular, 8));
    static_assert(!CanCoalesceSharpDwords(0x0e, mixed, 4));
    static_assert(!CanCoalesceSharpDwords(0x07, regular, 4));
    static_assert(!CanCoalesceSharpDwords(0x0f, sparse, 4));
    static_assert(!CanCoalesceSharpDwords(0x0f, wrapping, 4));
    static_assert(!CanCoalesceSharpDwords(0x00, mixed, 1));
    static_assert(!CanCoalesceSharpDwords(0x01, mixed, 0));

    // Reproduce a mixed descriptor which was incorrectly promoted by
    // checking 0,1,2,3 without first checking its actual source mask.
    const auto old_promoted = mixed[1] == mixed[0] + 1 &&
                              mixed[2] == mixed[0] + 2 &&
                              mixed[3] == mixed[0] + 3;
    assert(old_promoted);
    assert(!CanCoalesceSharpDwords(0x0e, mixed, 4));

    std::size_t valid = 0;
    std::size_t invalid = 0;
    for (unsigned mask = 0; mask <= 255; ++mask) {
        const bool coalesced =
            CanCoalesceSharpDwords(std::uint8_t(mask), regular, 8);
        if (coalesced) ++valid;
        else ++invalid;
    }
    assert(valid == 1 && invalid == 255);
    std::puts("SHARP_MIXED_SOURCE=PASS immediate_preservation=PASS "
              "contiguous_fastpath=PASS masks=256");
}
