// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstddef>
#include <cstring>

namespace Libraries::Ngs2::Hle {

enum class GuestAccess { Read = 1, Write = 2, Execute = 4 };

// Implemented by the emulator memory adapter; tests supply a permission model.
// As in other HLE libraries, the caller must not unmap memory during an API call.
bool GuestAccessible(const void* address, std::size_t size, GuestAccess access);

template <typename T>
bool ReadGuest(const T* address, T& value) {
    if (!GuestAccessible(address, sizeof(T), GuestAccess::Read))
        return false;
    std::memcpy(&value, address, sizeof(T));
    return true;
}

template <typename T>
bool WritableGuest(T* address) {
    return GuestAccessible(address, sizeof(T), GuestAccess::Write);
}

// Call only after validating the complete destination range.
template <typename T>
void WriteGuest(T* address, const T& value) {
    std::memcpy(address, &value, sizeof(T));
}

} // namespace Libraries::Ngs2::Hle
