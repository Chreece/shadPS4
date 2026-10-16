// SPDX-FileCopyrightText: Copyright 2026 shadPS4 contributors
// SPDX-License-Identifier: GPL-2.0-or-later
// Native x86-64 Xbyak regression for the SRT-offset emission operations.
// This tests the machine-instruction sequences emitted by the corresponding
// branches of flatten_extended_userdata_pass.cpp, with independent references.
#include <array>
#include <cstdint>
#include <cstdio>
#include <xbyak/xbyak.h>

namespace {
using u32 = std::uint32_t;

u32 BitMask(u32 width) {
    return width >= 32 ? 0xffffffffu : (u32{1} << width) - 1;
}

class ImmediateLeftShift final : public Xbyak::CodeGenerator {
public:
    explicit ImmediateLeftShift(u32 lhs) : Xbyak::CodeGenerator(1024) {
        // The production code computes Arg(1) into reg and then uses CL to
        // shift the constant Arg(0). ABI input: edi = dynamic right operand.
        mov(r10d, edi);
        mov(ecx, r10d);
        mov(r10d, lhs);
        shl(r10d, cl);
        mov(eax, r10d);
        ret();
        ready();
    }
};

class DynamicOffsetImmediateWidth final : public Xbyak::CodeGenerator {
public:
    explicit DynamicOffsetImmediateWidth(u32 width) : Xbyak::CodeGenerator(1024) {
        // ABI: edi=source value; esi=offset; the shifted result lives on stack.
        mov(r10d, edi);
        push(r10);
        mov(r10d, esi);
        mov(ecx, r10d);
        shr(dword[rsp], cl);
        pop(r10);
        mov(eax, r10d);
        if (width < 32) {
            and_(eax, BitMask(width));
        }
        ret();
        ready();
    }
};

class DynamicOffsetDynamicWidth final : public Xbyak::CodeGenerator {
public:
    DynamicOffsetDynamicWidth() : Xbyak::CodeGenerator(1024) {
        // ABI: edi=source, esi=offset, edx=width.
        mov(r10d, edi);
        push(r10);
        mov(r10d, esi);
        mov(ecx, r10d);
        shr(dword[rsp], cl);
        mov(r10d, edx);
        mov(ecx, r10d);
        Xbyak::Label full_width;
        mov(edx, 0xffffffffu);
        cmp(ecx, 32);
        jae(full_width);
        mov(edx, 1);
        shl(edx, cl);
        dec(edx);
        L(full_width);
        and_(dword[rsp], edx);
        pop(r10);
        mov(eax, r10d);
        ret();
        ready();
    }
};

u32 Next(u32& state) {
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    return state;
}

bool Run() {
    using Shift = u32 (*)(u32);
    using ExtractImmediate = u32 (*)(u32, u32);
    using ExtractDynamic = u32 (*)(u32, u32, u32);
    constexpr std::array<u32, 6> bases{0, 1, 7, 0x80, 0x12345678, 0xffffffff};
    constexpr std::array<u32, 10> shifts{0, 1, 2, 3, 7, 16, 31, 32, 33, 63};
    constexpr std::array<u32, 10> widths{0, 1, 2, 7, 8, 16, 30, 31, 32, 39};
    u32 checks = 0;
    for (u32 lhs : bases) {
        ImmediateLeftShift code(lhs);
        Shift fn = code.getCode<Shift>();
        for (u32 rhs : shifts) {
            const u32 expected = lhs << (rhs & 31u);
            const u32 actual = fn(rhs);
            if (expected != actual) {
                std::printf("FAIL shl lhs=%x rhs=%u expected=%x got=%x\n",
                            lhs, rhs, expected, actual);
                return false;
            }
            ++checks;
        }
    }
    DynamicOffsetDynamicWidth dynamic;
    ExtractDynamic df = dynamic.getCode<ExtractDynamic>();
    u32 seed = 0x19e70e13u;
    for (u32 width : widths) {
        DynamicOffsetImmediateWidth fixed(width);
        ExtractImmediate sf = fixed.getCode<ExtractImmediate>();
        for (u32 offset : shifts) {
            for (u32 iteration = 0; iteration < 1024; ++iteration) {
                const u32 value = Next(seed);
                const u32 expected = (value >> (offset & 31u)) & BitMask(width);
                const u32 single = sf(value, offset);
                const u32 variadic = df(value, offset, width);
                if (expected != single || expected != variadic) {
                    std::printf(
                        "FAIL bfe value=%x offset=%u width=%u exp=%x single=%x dynamic=%x\n",
                        value, offset, width, expected, single, variadic);
                    return false;
                }
                checks += 2;
            }
        }
    }
    std::printf("SRT_NATIVE_XBYAK_REGRESSION=PASS assertions=%u\n", checks);
    return true;
}
} // namespace

int main() {
    return Run() ? 0 : 1;
}
