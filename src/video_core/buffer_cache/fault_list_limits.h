// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <cstddef>
#include "common/types.h"

namespace VideoCore {

// The first 64-bit word of the downloaded GPU fault list is the count.
// The remaining words are page addresses, written by fault_buffer_process.comp.
inline constexpr size_t MaxGpuFaultListWords = 1024;
inline constexpr size_t MaxGpuFaultAddresses = MaxGpuFaultListWords - 1;
inline constexpr size_t GpuFaultListBytes = MaxGpuFaultListWords * sizeof(u64);

constexpr size_t ClampGpuFaultCount(u64 reported) noexcept {
    return reported < MaxGpuFaultAddresses ? static_cast<size_t>(reported)
                                          : MaxGpuFaultAddresses;
}

static_assert(GpuFaultListBytes == 8192);
static_assert(ClampGpuFaultCount(1023) == 1023);
static_assert(ClampGpuFaultCount(1024) == 1023);
} // namespace VideoCore
