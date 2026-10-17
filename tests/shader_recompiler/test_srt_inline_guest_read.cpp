// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <random>
#include <xbyak/xbyak.h>
#include <xbyak/xbyak_util.h>

#include "shader_recompiler/ir/passes/srt_inline_read_codegen.h"

using Shader::Optimization::SrtCodegen::GuestReadDwordAddress;
using Shader::Optimization::SrtCodegen::EmitGuestReadDword;

static_assert(GuestReadDwordAddress(0xffffffffu, 0, 1) == 0x100000003ULL);
static_assert(GuestReadDwordAddress(0, 0, 0xffffffffu) == 0xfffffffcULL);
static_assert(GuestReadDwordAddress(0, 0x10000u, 0) == 0);

int main() {
#if defined(__x86_64__) || defined(_M_X64)
    using namespace Xbyak::util;
    using ReadFn = std::uint32_t (*)(std::uint64_t, std::uint32_t);
    Xbyak::CodeGenerator c(4096);
    c.push(rdi);
#if defined(_WIN32)
    c.mov(rax, rcx);
    c.mov(r10d, edx);
#else
    c.mov(rax, rdi);
    c.mov(r10d, esi);
#endif
    EmitGuestReadDword(c);
    c.mov(eax, r10d);
    c.pop(rdi);
    c.ret();
    c.ready();
    ReadFn read = c.getCode<ReadFn>();
    std::array<std::uint32_t, 128> words{};
    for (std::size_t i = 0; i < words.size(); ++i) {
        words[i] = static_cast<std::uint32_t>(i * 7919u + 0x125a);
    }
    for (std::size_t i = 0; i < words.size(); ++i) {
        assert(read(reinterpret_cast<std::uint64_t>(words.data()),
                    static_cast<std::uint32_t>(i)) == words[i]);
    }
    std::mt19937_64 random{0x5a17a11u};
    for (int i=0;i<25000;++i) {
        const auto low = std::uint32_t(random()), high = std::uint32_t(random());
        const auto index = std::uint32_t(random());
        const std::uint64_t expected = 
            ((std::uint64_t(low) | (std::uint64_t(high) << 32)) +
              std::uint32_t(index * 4u)) & 0xffffffffffffULL;
        assert(GuestReadDwordAddress(low, high, index) == expected);
    }
    std::puts("SRT_INLINE_GUEST_READ=PASS scalar_reads=128 random_addresses=25000");
#else
    std::puts("SRT_INLINE_GUEST_READ=SKIP non-x86-64 host");
#endif
    return 0;
}
