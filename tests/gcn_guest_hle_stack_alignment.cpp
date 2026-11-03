// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
//
// The user-reported native GoW startup fault occurs before GPU dispatch.
// A PS4 guest->HLE x86-64 boundary must not assume the host's normal
// incoming stack alignment. This standalone probe reproduces the malformed
// incoming call and verifies the proven generic PR90 ABI realignment.
// It does not claim the guest fault address has yet been symbolized.
#include <cstdint>
#include <cstdio>

#if !defined(__x86_64__) || (!defined(__GNUC__) && !defined(__clang__))
#error "This ABI fixture is x86-64 GCC/Clang only"
#endif

extern "C" __attribute__((force_align_arg_pointer, noinline))
int GuestToHleEntryWithNativeFmtBoundary(std::uintptr_t arg) {
    // A native SSE-aligned local forces the compiler to respect the
    // realigned stack. A misaligned guest stack must not escape to C++.
    alignas(16) volatile std::uintptr_t data[2]{arg, arg + 1};
    std::uintptr_t rsp{};
    asm volatile("mov %%rsp, %0" : "=r"(rsp));
    return (rsp % 16 == 0 && data[0] == arg && data[1] == arg + 1) ? 0 : 1;
}

int main() {
    int result = 1;
    // Simulate exactly the guest call's 8-byte stack alignment anomaly.
    // Restore RSP before returning to the host caller.
    asm volatile("sub $8, %%rsp\n\tcall GuestToHleEntryWithNativeFmtBoundary\n\tadd $8, %%rsp"
                 : "=a"(result)
                 : "D"(std::uintptr_t{123})
                 : "cc", "memory", "r11", "r10", "rcx", "rdx", "rsi", "r8", "r9");
    std::printf("GUEST_TO_HLE_ABI_REALIGN=%s\n", result == 0 ? "PASS" : "FAIL");
    return result;
}
