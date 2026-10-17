// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Real Linux-kernel regression: guest-created host workers must retain native
// RDTSC and RDTSCP access, even if their creator previously disabled it.
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <pthread.h>
#include <sys/prctl.h>
#include "core/linux_guest_tsc.h"

namespace {

struct WorkerState {
    int observed_tsc_mode{};
    bool clock_executed{};
};

void* Worker(void* opaque) {
    auto& state = *static_cast<WorkerState*>(opaque);
    int mode{};
    assert(prctl(PR_GET_TSC, &mode) == 0);
    state.observed_tsc_mode = mode;
    if (mode == PR_TSC_ENABLE) {
        std::uint32_t lo, hi, aux;
        asm volatile("rdtsc" : "=a"(lo), "=d"(hi));
        const std::uint64_t first = (std::uint64_t(hi) << 32) | lo;
        asm volatile("rdtscp" : "=a"(lo), "=d"(hi), "=c"(aux) :: "memory");
        const std::uint64_t second = (std::uint64_t(hi) << 32) | lo;
        state.clock_executed = second >= first;
    }
    return nullptr;
}

void SpawnAndJoin(WorkerState& state) {
    pthread_t thread{};
    assert(pthread_create(&thread, nullptr, Worker, &state) == 0);
    assert(pthread_join(thread, nullptr) == 0);
}

} // namespace

int main() {
    // First demonstrate the inherited kernel restriction without executing
    // a timestamp instruction in a restricted worker.
    assert(prctl(PR_SET_TSC, PR_TSC_SIGSEGV) == 0);
    WorkerState restricted{};
    SpawnAndJoin(restricted);
    assert(restricted.observed_tsc_mode == PR_TSC_SIGSEGV);
    assert(!restricted.clock_executed);

    // Exercise the exact helper called by shadPS4's EnableCpuIdFaulting().
    assert(Core::RestoreHostNativeTsc() == 0);
    WorkerState native{};
    SpawnAndJoin(native);
    assert(native.observed_tsc_mode == PR_TSC_ENABLE);
    assert(native.clock_executed);

    std::puts("GUEST_CPU_NATIVE_TSC_INHERITANCE=PASS");
}
