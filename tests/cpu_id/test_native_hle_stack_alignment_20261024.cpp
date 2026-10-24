// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Standalone regression for guest -> HLE stack alignment. Compile with
// g++ or clang++ -std=c++20 -O2 -mavx2 -Isrc and execute the result.
// A PS4 guest can enter with the alternate stack parity. A native host
// function using aligned SIMD must still receive a valid host ABI stack.
#include <cstdio>

#if defined(__linux__) && defined(__x86_64__)
#include <immintrin.h>
#include "common/types.h"

extern "C" __attribute__((noinline)) int NativeHostSimdLoad() {
    alignas(16) volatile __m128i value = _mm_set1_epi32(123);
    const __m128i loaded = _mm_load_si128((const __m128i*)&value);
    return _mm_cvtsi128_si32(loaded);
}

extern "C" __attribute__((noinline)) int PS4_SYSV_ABI GuestHleEntry() {
    const int result = NativeHostSimdLoad();
    asm volatile("" ::: "memory"); // no tail-call: validate the HLE call boundary
    return result;
}

extern "C" __attribute__((naked, noinline)) int GuestMisalignedCall() {
    asm volatile(
        "push %rbp\n"
        "mov %rsp, %rbp\n"
        "sub $8, %rsp\n" // intentionally violate host pre-call alignment
        "call GuestHleEntry\n"
        "mov %rbp, %rsp\n"
        "pop %rbp\n"
        "ret\n");
}
#endif

int main() {
#if defined(__linux__) && defined(__x86_64__)
    if (GuestMisalignedCall() != 123) {
        return 2;
    }
    std::puts("NATIVE_HLE_STACK_ALIGNMENT_TEST=PASS");
#else
    std::puts("NATIVE_HLE_STACK_ALIGNMENT_TEST=SKIPPED_UNSUPPORTED_HOST");
#endif
    return 0;
}
