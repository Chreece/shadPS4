// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <chrono>

// Linux native guest threads configured with PR_SET_TSC_SIGSEGV trap on
// glibc's clock_gettime vDSO RDTSC. The guest HLE event-queue wait calls
// steady_clock::now() repeatedly; trapping that host clock operation needs
// signal delivery and several prctl/sigprocmask syscalls each time.
//
// CLOCK_MONOTONIC is exactly the Linux libstdc++ steady_clock timebase.
// Read it directly from the kernel when host RDTSC is trapped. Preserve
// all timeouts/intervals and leave other modes/platforms unchanged.
#if defined(__linux__) && defined(__x86_64__)
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#endif

namespace Common {

[[nodiscard]] inline std::chrono::steady_clock::time_point HostMonotonicNow(
    bool native_guest_tsc_faulting) noexcept {
#if defined(__linux__) && defined(__x86_64__)
    if (native_guest_tsc_faulting) {
        timespec ts{};
        if (::syscall(SYS_clock_gettime, CLOCK_MONOTONIC, &ts) == 0) {
            return std::chrono::steady_clock::time_point{
                std::chrono::seconds{ts.tv_sec} + std::chrono::nanoseconds{ts.tv_nsec}};
        }
        // Never invent a clock reading or alter the guest TSC state.
    }
#else
    (void)native_guest_tsc_faulting;
#endif
    return std::chrono::steady_clock::now();
}

} // namespace Common
