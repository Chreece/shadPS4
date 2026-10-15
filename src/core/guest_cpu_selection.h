// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once
#include <cstdint>

namespace Core {

// Resolve a virtual PS4 CPU from the host scheduler slot and the guest's
// permitted CPU mask. Every returned index belongs to the guest mask.
// This calculation is also used for kernel current-CPU and CPUID/RDTSCP/RDPID.
inline constexpr unsigned GuestCpuSlots = 8;

// Orbis CPU mode depends on the compiled SDK and process PSF flags,
// not on the native host CPU count. This is the pre-rebase kernel contract.
constexpr unsigned GuestCpuModeFromPsf(std::uint32_t sdk_version, std::uint32_t fw_300,
                                       bool six_cpu_mode, bool seven_cpu_mode) {
    if (sdk_version < fw_300 || (six_cpu_mode && seven_cpu_mode)) {
        return 2;
    }
    if (seven_cpu_mode || !six_cpu_mode) {
        return 5;
    }
    return 0;
}
static_assert(GuestCpuModeFromPsf(0x2000000, 0x3000000, false, false) == 2);
static_assert(GuestCpuModeFromPsf(0x8000000, 0x3000000, false, false) == 5);
static_assert(GuestCpuModeFromPsf(0x8000000, 0x3000000, true, false) == 0);
static_assert(GuestCpuModeFromPsf(0x8000000, 0x3000000, false, true) == 5);
static_assert(GuestCpuModeFromPsf(0x8000000, 0x3000000, true, true) == 2);

// The accessible application CPU mask follows the emulated Orbis CPU mode.
// CPU mode 5 exposes seven application CPUs, the other modes expose six;
// the eight hardware threads are still described by the CPUID topology.
constexpr std::uint64_t GuestCpuMaskFromMode(unsigned cpu_mode) {
    return cpu_mode == 5 ? 0x7fu : 0x3fu;
}
static_assert(GuestCpuMaskFromMode(0) == 0x3f);
static_assert(GuestCpuMaskFromMode(2) == 0x3f);
static_assert(GuestCpuMaskFromMode(5) == 0x7f);

constexpr int SelectGuestCpu(unsigned scheduler_slot, std::uint64_t affinity_mask) {
    constexpr std::uint64_t valid_mask = (std::uint64_t{1} << GuestCpuSlots) - 1;
    const std::uint64_t allowed = affinity_mask & valid_mask;
    if (allowed == 0) {
        return -1;
    }
    const unsigned first = scheduler_slot % GuestCpuSlots;
    for (unsigned step = 0; step < GuestCpuSlots; ++step) {
        const unsigned cpu = (first + step) % GuestCpuSlots;
        if (allowed & (std::uint64_t{1} << cpu)) {
            return static_cast<int>(cpu);
        }
    }
    return -1;
}

static_assert(SelectGuestCpu(0, 1) == 0);
static_assert(SelectGuestCpu(7, 0x80) == 7);
static_assert(SelectGuestCpu(3, 0x04) == 2);
static_assert(SelectGuestCpu(0, 0) == -1);

} // namespace Core
