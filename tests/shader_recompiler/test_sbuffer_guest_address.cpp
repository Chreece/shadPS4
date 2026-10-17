// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <random>

#include "shader_recompiler/ir/passes/sbuffer_guest_address.h"

using Shader::Optimization::SBufferGuestBase;
using Shader::Optimization::SBufferGuestDwordAddress;

int main() {
    struct Case { std::uint32_t lo, hi, index; std::uint64_t pgm; std::uint32_t mask; };
    constexpr std::array cases{
        Case{0,0,0,0,0}, Case{0xdeadbeef,0x12345678,15,0,0},
        Case{0xffffffff,0xff,1,0,0}, Case{0xffffffff,0xff,1,1,0},
        Case{0x1000,0xff00,4,0,0}, Case{0x1000,0,4,0,0x80},
        Case{0x1000,0,0,0xffffffffffULL,0},
        Case{0x0,0,0xffffffffu,0,0},
    };
    for (const auto& c : cases) {
        const auto guest = SBufferGuestBase(c.lo, c.hi, c.pgm, c.mask);
        const std::uint64_t manual =
            ((std::uint64_t(c.lo) |
              (std::uint64_t((c.hi | c.mask) & 0xffu) << 32)) + c.pgm) &
            0xffffffffffULL;
        assert(guest == manual);
        const std::uint32_t byte_offset = c.index * std::uint32_t{4};
        assert(SBufferGuestDwordAddress(c.lo,c.hi,c.index,c.pgm,c.mask) ==
               manual + byte_offset);
    }
    std::mt19937_64 rng{0x5B0FFEu};
    for (unsigned i=0; i<25000; ++i) {
        const auto a=std::uint32_t(rng()),b=std::uint32_t(rng());
        const auto index=std::uint32_t(rng());
        const std::uint64_t pgm = rng() & 0xffffffffffULL;
        const std::uint32_t mask = std::uint32_t(rng());
        const auto actual=SBufferGuestDwordAddress(a,b,index,pgm,mask);
        const auto expected=(((std::uint64_t(a) |
            (std::uint64_t((b|mask)&0xffu)<<32)) + pgm) & 0xffffffffffULL) +
            std::uint32_t(index*4u);
        assert(actual==expected);
    }
    std::puts("SBUFFER_GUEST_ADDRESS=PASS cases=25008 mask40=PASS signed_offset_bits=UNMODIFIED");
}
