// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Execute the exact reusable SRT lowering against an independent C++ oracle.
// No game, Vulkan, GPU drivers, plugins or external instrumentation involved.
#include <array>
#include <bit>
#include <cstdint>
#include <cstdio>
#include <random>
#include <xbyak/xbyak.h>
#include <xbyak/xbyak_util.h>

#include "shader_recompiler/ir/passes/srt_offset_codegen.h"

using Function = std::uint32_t(*)(std::uint32_t);

std::uint32_t Expected(std::uint32_t value) {
    return value ? static_cast<std::uint32_t>(std::countr_zero(value)) : 0xffffffffU;
}

bool Verify(const char* label, Function f) {
    constexpr std::array<std::uint32_t, 15> values{
        0U, 1U, 2U, 3U, 4U, 8U, 16U, 31U, 32U, 0x100U,
        0x80000000U, 0xffffffffU, 0xfffffffeU, 0x00010000U, 0x80000001U,
    };
    for (const auto value : values) {
        const auto actual = f(value);
        if (actual != Expected(value)) {
            std::fprintf(stderr, "%s incorrect input=%08x output=%08x expected=%08x\n",
                         label, value, actual, Expected(value));
            return false;
        }
    }
    std::mt19937 generator{0x5a175b32U};
    for (unsigned i = 0; i < 20000; ++i) {
        const std::uint32_t value = generator();
        if (f(value) != Expected(value)) {
            std::fprintf(stderr, "%s randomized failure input=%08x\n", label, value);
            return false;
        }
    }
    return true;
}

bool VerifyTransactionalRollback() {
    using namespace Xbyak::util;
    // A failed nested offset must not leave a push or bogus computation in
    // a function that later returns to the caller.
    Xbyak::CodeGenerator rolled_back(4096);
#if defined(_WIN32)
    rolled_back.mov(eax, ecx);
#else
    rolled_back.mov(eax, edi);
#endif
    const auto checkpoint = rolled_back.getSize();
    const bool failed = Shader::Optimization::SrtCodegen::EmitSrtWalkerAtomically(rolled_back, [&] {
        rolled_back.push(rdi);
        rolled_back.push(rcx);
        rolled_back.mov(eax, 0xdeadbeefU);
        return false;
    });
    if (failed || rolled_back.getSize() != checkpoint) {
        std::fputs("SRT rollback emitted bytes after failure\n", stderr);
        return false;
    }
    rolled_back.ret();
    rolled_back.ready();
    const Function identity = rolled_back.getCode<Function>();
    for (const auto value : {0U, 1U, 15U, 0x80000000U, 0xffffffffU}) {
        if (identity(value) != value) {
            std::fputs("SRT rollback corrupted register or return stack\n", stderr);
            return false;
        }
    }

    Xbyak::CodeGenerator committed(4096);
#if defined(_WIN32)
    committed.mov(eax, ecx);
#else
    committed.mov(eax, edi);
#endif
    const bool succeeded = Shader::Optimization::SrtCodegen::EmitSrtWalkerAtomically(committed, [&] {
        committed.add(eax, 1);
        return true;
    });
    committed.ret();
    committed.ready();
    const Function plus_one = committed.getCode<Function>();
    if (!succeeded || plus_one(4) != 5) {
        std::fputs("SRT committed offset was unexpectedly discarded\n", stderr);
        return false;
    }
    std::puts("SRT_WALKER_ROLLBACK=PASS malformed_offsets=REWOUND");
    return true;
}

int main() {
#if !defined(__x86_64__) && !defined(_M_X64)
    std::puts("SRT_FINDILSB32=SKIP non-x86-64 host");
    return 0;
#else
    using namespace Xbyak::util;
    Xbyak::CodeGenerator eax_code(4096);
#if defined(_WIN32)
    eax_code.mov(eax, ecx);
#else
    eax_code.mov(eax, edi);
#endif
    Shader::Optimization::SrtCodegen::EmitSrtFindILsb32(eax_code, eax);
    eax_code.ret();
    eax_code.ready();

    Xbyak::CodeGenerator r10_code(4096);
#if defined(_WIN32)
    r10_code.mov(r10d, ecx);
#else
    r10_code.mov(r10d, edi);
#endif
    Shader::Optimization::SrtCodegen::EmitSrtFindILsb32(r10_code, r10d);
    r10_code.mov(eax, r10d);
    r10_code.ret();
    r10_code.ready();

    if (!Verify("eax", eax_code.getCode<Function>()) ||
        !Verify("r10d", r10_code.getCode<Function>()) ||
        !VerifyTransactionalRollback()) {
        return 1;
    }
    std::puts("SRT_FINDILSB32=PASS cases=40030 zero=UINT32_MAX");
    return 0;
#endif
}
