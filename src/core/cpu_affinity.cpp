// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <bit>
#include <cerrno>
#include <vector>
#include "core/cpu_affinity.h"
#include "core/libraries/kernel/posix_error.h"

#ifdef _WIN32
#include <windows.h>
#elif defined(__linux__)
#include <pthread.h>
#include <sched.h>
#elif defined(__FreeBSD__)
#include <pthread.h>
#include <pthread_np.h>
#include <sys/cpuset.h>
#endif

namespace Core {

const CpuAffinity& CpuAffinity::Instance() {
    static const CpuAffinity affinity;
    return affinity;
}

CpuAffinity::CpuAffinity() {
    std::vector<int> allowed;
#ifdef _WIN32
    GROUP_AFFINITY affinity{};
    if (!GetThreadGroupAffinity(GetCurrentThread(), &affinity)) {
        return;
    }
    host_group = affinity.Group;
    for (int cpu = 0; cpu < sizeof(KAFFINITY) * 8; ++cpu) {
        if (affinity.Mask & (KAFFINITY{1} << cpu)) {
            allowed.push_back(cpu);
        }
    }
#elif defined(__linux__)
    for (size_t count = CPU_SETSIZE; count <= 65536; count *= 2) {
        const auto size = CPU_ALLOC_SIZE(count);
        auto* affinity = CPU_ALLOC(count);
        if (affinity == nullptr) {
            return;
        }
        CPU_ZERO_S(size, affinity);
        const int ret = sched_getaffinity(0, size, affinity);
        const int error = errno;
        if (ret == 0) {
            for (size_t cpu = 0; cpu < count; ++cpu) {
                if (CPU_ISSET_S(cpu, size, affinity)) {
                    allowed.push_back(static_cast<int>(cpu));
                }
            }
        }
        CPU_FREE(affinity);
        if (ret == 0 || error != EINVAL) {
            break;
        }
    }
#elif defined(__FreeBSD__)
    cpuset_t affinity{};
    if (pthread_getaffinity_np(pthread_self(), sizeof(affinity), &affinity) != 0) {
        return;
    }
    for (int cpu = 0; cpu < CPU_SETSIZE; ++cpu) {
        if (CPU_ISSET(cpu, &affinity)) {
            allowed.push_back(cpu);
        }
    }
#else
    return;
#endif
    if (allowed.empty()) {
        return;
    }
    for (size_t guest = 0; guest < host_cpus.size(); ++guest) {
        host_cpus[guest] = allowed[guest % allowed.size()];
    }
    available = true;
}

int CpuAffinity::SetThreadAffinity(uintptr_t thread, u64 guest_mask) const {
    if (guest_mask == 0 || (guest_mask & ~u64{0xff}) != 0) {
        return POSIX_EINVAL;
    }
#if defined(_WIN32) || defined(__linux__) || defined(__FreeBSD__)
    if (!available) {
        return POSIX_EAGAIN;
    }
#endif
#ifdef _WIN32
    GROUP_AFFINITY affinity{};
    affinity.Group = host_group;
    for (size_t guest = 0; guest < host_cpus.size(); ++guest) {
        if (guest_mask & (u64{1} << guest)) {
            affinity.Mask |= KAFFINITY{1} << host_cpus[guest];
        }
    }
    const auto handle = thread == 0 ? GetCurrentThread() : reinterpret_cast<HANDLE>(thread);
    return SetThreadGroupAffinity(handle, &affinity, nullptr) ? 0 : POSIX_EINVAL;
#elif defined(__linux__)
    const auto count = *std::max_element(host_cpus.begin(), host_cpus.end()) + 1;
    const auto size = CPU_ALLOC_SIZE(count);
    auto* affinity = CPU_ALLOC(count);
    if (affinity == nullptr) {
        return POSIX_ENOMEM;
    }
    CPU_ZERO_S(size, affinity);
    for (size_t guest = 0; guest < host_cpus.size(); ++guest) {
        if (guest_mask & (u64{1} << guest)) {
            CPU_SET_S(host_cpus[guest], size, affinity);
        }
    }
    const auto handle = thread == 0 ? pthread_self() : static_cast<pthread_t>(thread);
    const int ret = pthread_setaffinity_np(handle, size, affinity);
    CPU_FREE(affinity);
    return ret == 0 ? 0 : ret == ESRCH ? POSIX_ESRCH : POSIX_EINVAL;
#elif defined(__FreeBSD__)
    cpuset_t affinity{};
    CPU_ZERO(&affinity);
    for (size_t guest = 0; guest < host_cpus.size(); ++guest) {
        if (guest_mask & (u64{1} << guest)) {
            CPU_SET(host_cpus[guest], &affinity);
        }
    }
    const auto handle = thread == 0 ? pthread_self() : reinterpret_cast<pthread_t>(thread);
    return pthread_setaffinity_np(handle, sizeof(affinity), &affinity);
#else
    return 0;
#endif
}

int CpuAffinity::CurrentGuestCpu(u64 guest_mask) const {
#ifdef _WIN32
    PROCESSOR_NUMBER current{};
    GetCurrentProcessorNumberEx(&current);
    if (current.Group != host_group) {
        return -1;
    }
    const int host_cpu = current.Number;
#elif defined(__linux__) || defined(__FreeBSD__)
    const int host_cpu = sched_getcpu();
#else
    return guest_mask != 0 ? std::countr_zero(guest_mask) : -1;
#endif
#if defined(_WIN32) || defined(__linux__) || defined(__FreeBSD__)
    if (!available || host_cpu < 0) {
        return -1;
    }
    for (size_t guest = 0; guest < host_cpus.size(); ++guest) {
        if ((guest_mask & (u64{1} << guest)) != 0 && host_cpus[guest] == host_cpu) {
            return static_cast<int>(guest);
        }
    }
    return -1;
#endif
}

} // namespace Core
