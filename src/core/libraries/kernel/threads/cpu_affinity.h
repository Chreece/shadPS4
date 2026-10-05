// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <bit>

#include "common/types.h"

namespace Libraries::Kernel {

constexpr s32 SelectGuestCpu(u64 affinity_mask, s32 current_cpu) noexcept {
    if (affinity_mask == 0) {
        return current_cpu;
    }
    if (current_cpu >= 0 && current_cpu < 64 &&
        (affinity_mask & (u64{1} << static_cast<u32>(current_cpu))) != 0) {
        return current_cpu;
    }
    return static_cast<s32>(std::countr_zero(affinity_mask));
}

} // namespace Libraries::Kernel
