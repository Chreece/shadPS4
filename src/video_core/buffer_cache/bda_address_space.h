// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "common/types.h"

namespace VideoCore {

// This bound is shared by the CPU sparse arena and GPU BDA address resolver.
// Checking the address before truncating the page to u32 is essential.
inline constexpr u64 GuestGpuAddressSpaceBits = 40;
inline constexpr u64 GuestGpuAddressLimit = 1ULL << GuestGpuAddressSpaceBits;

constexpr bool IsGuestGpuDwordInRange(u64 address) noexcept {
    return address < GuestGpuAddressLimit - (sizeof(u32) - 1);
}

} // namespace VideoCore
