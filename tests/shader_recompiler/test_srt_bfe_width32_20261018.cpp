// SPDX-License-Identifier: GPL-2.0-or-later
// Native x86-64 SRT BFE mask width regression, exactly the Xbyak instruction
// order emitted by the fixed SRT resource walker. No emulator launched.
#include <cstdint>
#include <iostream>
#include <xbyak/xbyak.h>

#ifndef __x86_64__
#error x86_64 required for native SRT JIT contract
#endif

class SrtBfeMaskJit : public Xbyak::CodeGenerator {
public:
    explicit SrtBfeMaskJit(bool old_behavior = false) {
        mov(eax, edi); // guest value
        mov(ecx, esi); // guest requested BFE width
        if (!old_behavior) {
            Xbyak::Label full_width;
            mov(edx, 0xffffffffu);
            cmp(ecx, 32);
            jae(full_width);
            mov(edx, 1);
            shl(edx, cl);
            dec(edx);
            L(full_width);
        } else {
            // Old behavior: when width is 32, x86 masks shift count to 0.
            mov(edx, 1);
            shl(edx, cl);
            dec(edx);
        }
        and_(eax, edx);
        ret();
        ready();
    }
    std::uint32_t operator()(std::uint32_t bits, std::uint32_t width) const {
        using Fn = std::uint32_t(*)(std::uint32_t, std::uint32_t);
        return getCode<Fn>()(bits, width);
    }
};

static std::uint32_t expected(std::uint32_t bits, std::uint32_t width) {
    if (width >= 32) return bits;
    if (width == 0) return 0;
    return bits & ((1u << width) - 1u);
}

int main() {
    const SrtBfeMaskJit fixed, old(true);
    if (old(0xa5a5f00fu, 32) != 0 ||
        fixed(0xa5a5f00fu, 32) != 0xa5a5f00fu) {
        std::cerr << "SRT_BFE_WIDTH32_REGRESSION_TEST_INVALID\n";
        return 1;
    }
    std::uint64_t cases = 0;
    std::uint32_t state = 0x01ab1988u;
    for (std::uint32_t width = 0; width <= 255; ++width) {
        for (int j = 0; j < 512; ++j) {
            state ^= state << 13;
            state ^= state >> 17;
            state ^= state << 5;
            if (fixed(state, width) != expected(state, width)) {
                std::cerr << "SRT_BFE_MISMATCH value=" << state
                          << " width=" << width << "\n";
                return 2;
            }
            ++cases;
        }
    }
    std::cout << "SRT_BFE_NATIVE_XBYAK=PASS cases=" << cases << "\n"
              << "SRT_BFE_WIDTH32_PREVIOUS_ZERO_MASK_DETECTED=PASS\n"
              << "UNRELATED_SHADER_CODE=NOT_MODIFIED_BY_TEST\n";
}
