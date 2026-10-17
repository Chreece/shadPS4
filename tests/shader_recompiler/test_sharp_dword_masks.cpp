// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cstdint>
#include <cstdio>
#include <random>
#include "shader_recompiler/ir/passes/sharp_dword_mask.h"
#include "shader_recompiler/ir/passes/sharp_fetch_coalesce.h"
using Shader::Optimization::ApplySharpWordMask;
using Shader::Optimization::CanCoalesceSharpDwords;
using Shader::Optimization::SharpMasksAreZero;
int main() {
    constexpr std::array<std::uint16_t,8> offsets{12,13,14,15,16,17,18,19};
    constexpr std::array<std::uint32_t,8> clean{};
    constexpr std::array<std::uint32_t,8> masked{0,0x3c000000u,0,0,0,0,0,0};
    static_assert(SharpMasksAreZero(clean,8));
    static_assert(!SharpMasksAreZero(masked,4));
    static_assert(SharpMasksAreZero(masked,1));
    static_assert(!SharpMasksAreZero(clean,9));
    static_assert(CanCoalesceSharpDwords(0x0fu,offsets,4));
    static_assert(ApplySharpWordMask(0xf3ffffffu,0x3c000000u) == 0xc3ffffffu);
    static_assert(ApplySharpWordMask(0xffffffffu,0) == 0xffffffffu);
    // A masked descriptor MUST bypass the old contiguous memcpy fast path.
    if (CanCoalesceSharpDwords(0x0f,offsets,4) &&
        SharpMasksAreZero(masked,4)) return 1;
    constexpr std::array<std::uint32_t,4> raw{0xffffffffu,0xffffffffu,
                                             0xa536f941u,0x76543210u};
    std::array<std::uint32_t,4> actual{};
    for(std::size_t i=0;i<4;++i) actual[i]=ApplySharpWordMask(raw[i],masked[i]);
    if(actual[1]!=0xc3ffffffu || actual[0]!=raw[0] ||
       actual[2]!=raw[2] || actual[3]!=raw[3]) return 2;
    std::mt19937 gen{0x70acb3d5u};
    for(unsigned i=0;i<100000u;++i) {
        const std::uint32_t x=gen(), a=gen(), b=gen();
        const std::uint32_t masked_direct=(x&a)&b;
        const std::uint32_t removed=(~a)|(~b);
        if(ApplySharpWordMask(x,removed)!=masked_direct) return 3;
        const std::size_t n=1+gen()%8;
        std::array<std::uint32_t,8> masks{};
        for(std::size_t j=0;j<n;++j) masks[j]=gen();
        bool expected=true;
        for(std::size_t j=0;j<n;++j) expected &= masks[j]==0;
        if(SharpMasksAreZero(masks,n)!=expected) return 4;
    }
    std::puts("SHARP_DWORD_MASKS=PASS nested_masks=100000 mixed=PASS fastpath=GUARDED");
    return 0;
}
