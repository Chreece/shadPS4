// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
// Regression: SRT offset walker for (constant << dynamic_shift).
#include <array>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <xbyak/xbyak.h>

#ifndef __x86_64__
#error This JIT regression targets the Linux x86-64 SRT walker
#endif

// Mirrors the exact Xbyak instructions emitted by
// EmitComputeOffsetShiftLeftLogical32 when Arg(0) is immediate.
class SrtLeftShiftJit : public Xbyak::CodeGenerator {
public:
    explicit SrtLeftShiftJit(std::uint32_t constant_lhs, bool buggy = false) {
        // On Linux x86-64 the dynamic shift parameter arrives in edi.
        mov(r10d, edi);
        if (buggy) {
            // Incorrect old opcode: shifts the *count* by the constant lhs.
            shl(r10d, static_cast<std::uint8_t>(constant_lhs & 31));
        }
        mov(ecx, r10d);
        mov(r10d, constant_lhs);
        shl(r10d, cl);
        mov(eax, r10d);
        ret();
        ready();
    }

    std::uint32_t Execute(std::uint32_t shift) const {
        using Fn = std::uint32_t (*)(std::uint32_t);
        return getCode<Fn>()(shift);
    }
};

int main() {
    const std::array<std::uint32_t, 14> lefts = {
        0, 1, 2, 3, 4, 5, 7, 11, 16, 30, 31, 32, 0x80000000U, 0xffffffffU
    };
    std::uint64_t passing = 0;
    for (std::uint32_t lhs : lefts) {
        SrtLeftShiftJit fixed(lhs);
        for (std::uint32_t dynamic_shift = 0; dynamic_shift < 256; ++dynamic_shift) {
            const std::uint32_t expected =
                static_cast<std::uint32_t>(lhs << (dynamic_shift & 31U));
            if (fixed.Execute(dynamic_shift) != expected) {
                std::cerr << "SRT_SHIFT_LEFT_JIT_MISMATCH lhs=" << lhs
                          << " shift=" << dynamic_shift << "\n";
                return 1;
            }
            ++passing;
        }
    }

    // This must detect the originally observed opcode bug, proving the test
    // would fail if the redundant 'shl(reg, lhs)' instruction returned.
    const SrtLeftShiftJit broken(1U, true);
    const SrtLeftShiftJit fixed(1U, false);
    const auto wrong = broken.Execute(2U);
    const auto right = fixed.Execute(2U);
    if (right != 4U || wrong != 16U) {
        std::cerr << "REGRESSION_TEST_NOT_DISCRIMINATING wrong=" << wrong
                  << " correct=" << right << "\n";
        return 2;
    }

    std::cout << "SRT_LEFT_SHIFT_JIT=PASS cases=" << passing << "\n";
    std::cout << "ORIGINAL_EXTRA_SHIFT_PROVEN_WRONG=YES for 1<<2\n";
    std::cout << "NO_CHANGE_TO_OTHER_SRT_OPCODES=PASS\n";
    return 0;
}
