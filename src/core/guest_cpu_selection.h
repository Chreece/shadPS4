// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once
#include <cstdint>

namespace Core {

// Resolve a virtual PS4 CPU from the host scheduler slot and the guest's
// permitted CPU mask. Every returned index belongs to the guest mask.
// This calculation is also used for kernel current-CPU and CPUID/RDTSCP/RDPID.
inline constexpr unsigned GuestCpuSlots = 8;

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
