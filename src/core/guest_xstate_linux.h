// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <ucontext.h>
#include "core/guest_xstate.h"

namespace Core {

enum class LinuxXstateStatus {
    NotHandled,
    Completed,
    Fault,
    InvalidContext,
    UnsupportedState,
};

struct LinuxXstateResult {
    LinuxXstateStatus status;
    XstateResult fault{};
};

// The caller supplies a bounded Linux signal frame, stable instruction bytes, guest segment bases,
// and fault-safe memory access. UnsupportedState requires a software execution backend.
// XSTATE_BV uses native in-use tracking; it can conservatively mark initial guest components in
// use.
LinuxXstateResult ExecuteLinuxXstateInstruction(std::span<const u8> instruction, ucontext_t& host,
                                                std::span<u8> fpstate, const XstateMemory& memory,
                                                u64 fs_base, u64 gs_base);

} // namespace Core
