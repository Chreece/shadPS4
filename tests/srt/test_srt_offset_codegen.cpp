// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Standalone, hardware-independent JIT regression for SRT pointer arithmetic.
// Compile with: c++ -std=c++20 -O2 -I src -I externals/xbyak
// tests/srt/test_srt_offset_codegen.cpp -o /path/to/temporary-test
#include <array>
#include <cstdint>
#include <iostream>

#include <xbyak/xbyak.h>
#include <xbyak/xbyak_util.h>
#include "shader_recompiler/ir/passes/srt_offset_codegen.h"

using namespace Xbyak::util;

static std::uint32_t DynamicLeftShift(std::uint32_t value, std::uint32_t count) {
    Xbyak::CodeGenerator code(4096);
#if defined(_WIN32)
    code.mov(r10d, edx);
#else
    code.mov(r10d, esi);
#endif
    Shader::Optimization::SrtCodegen::EmitConstantLeftShiftByDynamicCount(
        code, r10d, value);
    code.mov(eax, r10d);
    code.ret();
    code.ready();
    return code.getCode<std::uint32_t (*)(std::uint32_t, std::uint32_t)>()(value, count);
}

static std::uint32_t DynamicMask(std::uint32_t width) {
    Xbyak::CodeGenerator code(4096);
#if defined(_WIN32)
    code.mov(r10d, ecx);
#else
    code.mov(r10d, edi);
#endif
    Shader::Optimization::SrtCodegen::EmitLowBitMask(code, r10d, edx);
    code.mov(eax, edx);
    code.ret();
    code.ready();
    return code.getCode<std::uint32_t (*)(std::uint32_t)>()(width);
}

int main() {
    constexpr std::array<std::uint32_t, 8> values{
        0u, 1u, 3u, 8u, 16u, 0x7fffffffu, 0x80000000u, 0xffffffffu};
    constexpr std::array<std::uint32_t, 14> widths{
        0u, 1u, 7u, 15u, 30u, 31u, 32u, 33u, 63u, 64u, 65u,
        127u, 0x80000000u, 0xffffffffu};
    std::uint32_t count{};
    for (const auto value : values) {
        for (const auto width : widths) {
            const std::uint32_t expected = value << (width & 31u);
            const std::uint32_t actual = DynamicLeftShift(value, width);
            if (actual != expected) {
                std::cerr << "SRT shift failed for value=" << value
                          << " width=" << width << '\n';
                return 1;
            }
            ++count;
        }
    }
    for (const auto width : widths) {
        const std::uint32_t expected =
            width >= 32 ? 0xffffffffu : width == 0 ? 0u : (1u << width) - 1u;
        if (DynamicMask(width) != expected) {
            std::cerr << "SRT mask failed for width=" << width << '\n';
            return 2;
        }
        ++count;
    }
    std::cout << "GENERAL_SRT_OFFSET_JIT=PASS cases=" << count << '\n';
}
