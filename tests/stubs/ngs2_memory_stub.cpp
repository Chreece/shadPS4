// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "core/libraries/ngs2/hle/guest_memory.h"

// Legacy helper tests use valid host objects. The standalone API suite has a
// strict mapped-range/permission model, including invalid guest pointers.
namespace Libraries::Ngs2::Hle {
bool GuestAccessible(const void* address, std::size_t size, GuestAccess) {
    return address && size;
}
} // namespace Libraries::Ngs2::Hle
