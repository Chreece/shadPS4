// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "core/libraries/kernel/orbis_error.h"

namespace Libraries::Kernel {

// Validate the guest module filename before logging, constructing a host
// std::string, or calling the linker. In particular, formatting a null guest
// C-string must never crash the emulator. Non-null pointer permissions remain
// the responsibility of normal guest memory access protection.
[[nodiscard]] constexpr int ValidateModuleFilename(const char* filename) noexcept {
    if (filename == nullptr) {
        return ORBIS_KERNEL_ERROR_EFAULT;
    }
    if (filename[0] == '\0') {
        return ORBIS_KERNEL_ERROR_ENOENT;
    }
    return ORBIS_OK;
}

} // namespace Libraries::Kernel
