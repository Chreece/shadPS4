// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cstdint>
#include <cstdio>
#include <random>
#include <xbyak/xbyak.h>
#include <xbyak/xbyak_util.h>
#include "shader_recompiler/ir/passes/srt_inline_read_codegen.h"
using Shader::Optimization::SrtCodegen::EmitSBufferClampDwordIndex;

// Reference GCN scalar buffer offset: clamp according to stride and records
// using a 64-bit stride*records, then align to dword.
static std::uint32_t Expected(std::uint32_t index, std::uint32_t stride,
                              std::uint32_t num_records) {
    const std::uint64_t bytes = std::uint32_t(index << 2);
    const std::uint64_t limit = stride == 0
        ? num_records : std::uint64_t(stride) * num_records;
    const std::uint64_t effective = stride == 0
        ? (bytes >= limit ? limit : bytes)
        : (bytes > limit ? limit : bytes);
    return static_cast<std::uint32_t>((effective & ~std::uint64_t{3}) >> 2);
}

int main() {
#if defined(__x86_64__) || defined(_M_X64)
    using namespace Xbyak::util;
    using Fn = std::uint32_t (*)(std::uint32_t, std::uint32_t, std::uint32_t);
    Xbyak::CodeGenerator jit{4096};
    jit.push(r9);
    jit.push(r11);
#if defined(_WIN32)
    jit.mov(r10d, ecx);
    jit.mov(r11d, edx);
    jit.mov(r9d, r8d);
#else
    jit.mov(r10d, edi);
    jit.mov(r11d, esi);
    jit.mov(r9d, edx);
#endif
    EmitSBufferClampDwordIndex(jit);
    jit.mov(eax, r10d);
    jit.pop(r11);
    jit.pop(r9);
    jit.ret();
    jit.ready();
    const Fn call = jit.getCode<Fn>();

    struct Case {std::uint32_t index, stride, num_records;};
    constexpr std::array<Case, 13> cases{{
        {0,0,0}, {1,0,3}, {3,0,12}, {4,0,15}, {4,0,16},
        {5,16,1}, {20,16,4}, {16,16,4}, {0xffffffffu,0,0xffffffffu},
        {0xffffffffu,0x3fffu,0xffffffffu}, {0x3fffffffu,0,0xffffffffu},
        {100,4096,0}, {100,4096,1}
    }};
    for (auto [idx,stride,num_records]:cases) {
        if (call(idx,stride,num_records) != Expected(idx,stride,num_records)) {
            std::fprintf(stderr, "SBUFFER_XBYAK=FAIL boundary idx=%x stride=%u records=%u\n",
                         idx,stride,num_records);
            return 1;
        }
    }
    std::mt19937 rng{0x1984c1feu};
    for (std::uint32_t trial=0;trial<100000u;++trial) {
        const std::uint32_t idx=rng(), stride=rng()&0x3fffu, count=rng();
        const auto expected=Expected(idx,stride,count);
        const auto actual=call(idx,stride,count);
        if (actual != expected) {
            std::fprintf(stderr, "SBUFFER_XBYAK=FAIL random=%u stride=%u index=%x records=%x\n",
                         trial,stride,idx,count);
            return 1;
        }
    }
    std::puts("SBUFFER_XBYAK=PASS boundary=13 random=100000 same-ISA-clamp=PASS");
#else
    std::puts("SBUFFER_XBYAK=SKIP non-x86-64");
#endif
    return 0;
}
