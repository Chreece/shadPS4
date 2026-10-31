// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Exact host SRT JIT instruction contract: FindILsb32 zero and nonzero,
// and an immediate left operand with a dynamically evaluated shift count.
// These are general resource-address operations, not game-specific values.
#include <bit>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <xbyak/xbyak.h>

using u32 = std::uint32_t;
using Fn = u32 (*)(u32);

struct FirstSetBitJit final : Xbyak::CodeGenerator {
    FirstSetBitJit() : Xbyak::CodeGenerator(4096) {
        // System V x86-64: input in edi, result in eax.
        mov(eax, edi);
        bsf(eax, eax);
        mov(ecx, ~u32{0});
        cmovz(eax, ecx);
        ret();
        ready();
    }
    Fn Entry() const { return getCode<Fn>(); }
};

struct ConstantShiftJit final : Xbyak::CodeGenerator {
    ConstantShiftJit() : Xbyak::CodeGenerator(4096) {
        mov(ecx, edi);
        mov(eax, 0x12345678u);
        shl(eax, cl);
        ret();
        ready();
    }
    Fn Entry() const { return getCode<Fn>(); }
};

int main() {
    FirstSetBitJit ffs;
    ConstantShiftJit shift;
    const auto find = ffs.Entry();
    const auto left = shift.Entry();
    for (const u32 x : {0u, 1u, 2u, 3u, 8u, 0x7fffffffu,
                        0x80000000u, 0x80808080u, 0xffffffffu}) {
        const u32 expected = x ? std::countr_zero(x) : ~u32{0};
        assert(find(x) == expected);
    }
    // Loop through a broad range of set bit patterns, verifying all 32 bits
    // including index 31 and the zero sentinel.
    for (u32 bit = 0; bit < 32; ++bit) {
        assert(find(u32{1} << bit) == bit);
        assert(find((u32{1} << bit) | 0x80000000u) == bit);
    }
    for (u32 count = 0; count < 96; ++count) {
        // x86 masks variable 32-bit shifts to 5 bits, as SPIR-V operations
        // on 32-bit values do; no bogus first shift of the COUNT by VALUE.
        assert(left(count) == (0x12345678u << (count & 31)));
    }
    std::cout << "SRT_HOST_FIRST_SET_BIT_JIT=PASS "
                 "zero_returns_ffffffff=PASS nonzero_ffs=PASS "
                 "dynamic_shift_constant_lhs=PASS\n";
}
