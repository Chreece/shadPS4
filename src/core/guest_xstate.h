// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <cstddef>
#include <span>
#include "common/types.h"

namespace Core {

constexpr u64 GuestXcr0 = 7;
constexpr std::size_t GuestXstateSize = 832;
constexpr u32 GuestMxcsrMask = 0x2ffff;

enum class XstateFault {
    None,
    GeneralProtection,
    PageFault,
};

struct XstateResult {
    XstateFault fault{};
    u64 address{};
    bool write{};

    explicit operator bool() const {
        return fault == XstateFault::None;
    }
};

// Canonical FXSAVE64 legacy fields and YMM upper halves at their standard offsets. The execution
// backend owns in-use tracking and the x87 selectors; this is not a host XSAVE buffer.
struct GuestXstate {
    std::array<u8, GuestXstateSize> bytes{};
    u64 in_use{};
    u16 x87_cs{};
    u16 x87_ds{};
};

struct XstateMemory {
    void* context;
    XstateResult (*read)(void*, u64, std::span<u8>);
    XstateResult (*write)(void*, u64, std::span<const u8>);
};

struct XstateContext {
    std::array<u64, 16> registers{};
    u64 rip{};
    u64 rflags{};
    u64 fs_base{};
    u64 gs_base{};
    GuestXstate state;
};

struct XstateInstructionResult {
    bool handled{};
    XstateResult result;
};

XstateResult ReadGuestXcr(u32 index, u64& rax, u64& rdx);

// XSAVEOPT may use the same full-save path; omitted-store optimization is optional.
XstateResult SaveGuestXstate(const GuestXstate& state, u64 mask, u64 address,
                             const XstateMemory& memory, bool format64);
XstateResult RestoreGuestXstate(GuestXstate& state, u64 mask, u64 address,
                                const XstateMemory& memory, bool format64);

// Used at an execution boundary with a stable instruction snapshot. Faults preserve RIP and the
// register context; the execution backend is responsible for delivering the guest exception.
XstateInstructionResult ExecuteGuestXstateInstruction(std::span<const u8> instruction,
                                                      XstateContext& context,
                                                      const XstateMemory& memory);

} // namespace Core
