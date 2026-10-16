// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <chrono>
#include <cstdio>
#include <cstdlib>

#include "common/host_monotonic_clock.h"

#if defined(__linux__) && defined(__x86_64__)
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

static bool Check(bool condition, const char* label) {
    if (!condition) {
        std::fprintf(stderr, "HOST_MONOTONIC_CLOCK=FAIL %s\n", label);
    }
    return condition;
}

int main() {
    using Clock = std::chrono::steady_clock;
    const auto standard_before = Clock::now();
    const auto direct_time = Common::HostMonotonicNow(true);
    const auto standard_after = Clock::now();
    if (!Check(direct_time >= standard_before - std::chrono::milliseconds(10) &&
               direct_time <= standard_after + std::chrono::milliseconds(10),
               "CLOCK_MONOTONIC epoch/units mismatch")) {
        return 1;
    }
    if (!Check(Common::HostMonotonicNow(false) >=
               standard_before - std::chrono::milliseconds(10),
               "standard chrono fallback changed")) {
        return 1;
    }
    auto prev = direct_time;
    for (unsigned i = 0; i < 5000; ++i) {
        const auto next = Common::HostMonotonicNow(true);
        if (!Check(next >= prev, "direct clock went backward")) {
            return 1;
        }
        prev = next;
    }
#if defined(__linux__) && defined(__x86_64__)
    const pid_t child = fork();
    if (!Check(child >= 0, "fork failed")) return 1;
    if (child == 0) {
        // Isolate PR_SET_TSC per child. A fault kills only this test child.
        if (prctl(PR_SET_TSC, PR_TSC_SIGSEGV) != 0) _exit(77);
        auto sample = Common::HostMonotonicNow(true);
        for (unsigned i = 0; i < 5000; ++i) {
            const auto newer = Common::HostMonotonicNow(true);
            if (newer < sample) _exit(20);
            sample = newer;
        }
        if (prctl(PR_SET_TSC, PR_TSC_ENABLE) != 0) _exit(21);
        _exit(0);
    }
    int status = 0;
    if (!Check(waitpid(child, &status, 0) == child, "waitpid failed") ||
        !Check(WIFEXITED(status), "direct monotonic syscall faulted")) {
        return 1;
    }
    if (WEXITSTATUS(status) == 77) {
        std::puts("HOST_MONOTONIC_CLOCK=PASS syscall=PASS trapped_tsc=SKIP");
    } else if (!Check(WEXITSTATUS(status) == 0, "clock fails with RDTSC faults")) {
        return 1;
    } else {
        std::puts("HOST_MONOTONIC_CLOCK=PASS syscall=PASS trapped_tsc=PASS");
    }
#else
    std::puts("HOST_MONOTONIC_CLOCK=PASS fallback=PASS trapped_tsc=NOT_LINUX");
#endif
    return 0;
}
