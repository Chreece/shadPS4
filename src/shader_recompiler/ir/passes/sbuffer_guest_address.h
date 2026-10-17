// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include "common/types.h"

namespace Shader::Optimization {

constexpr u64 SBufferGuestBase(u32 word0, u32 word1, u64 program_base = 0,
                               u32 word1_or_mask = 0) {
    constexpr u64 mask40 = (u64{1} << 40) - 1;
    const u64 address = static_cast<u64>(word0) |
                        (static_cast<u64>((word1 | word1_or_mask) & 0xffu) << 32);
    return (address + program_base) & mask40;
}

constexpr u64 SBufferGuestDwordAddress(u32 word0, u32 word1, u32 index,
                                      u64 program_base = 0, u32 word1_or_mask = 0) {
    // The scalar-memory offset is U32 dwords, converted to U32 byte-offset
    // by the existing SPIR-V ReadConst dynamic-memory code.
    return SBufferGuestBase(word0, word1, program_base, word1_or_mask) +
           static_cast<u32>(index << 2);
}

static_assert(SBufferGuestBase(0xdeadbeef, 0x12345678) == 0x78deadbeefULL);
static_assert(SBufferGuestDwordAddress(0xfffffffc, 0x0, 1) == 0x100000000ULL);
static_assert(SBufferGuestBase(0xffffffff, 0xff, 1) == 0);
static_assert(SBufferGuestBase(0xabcd, 0x11, 0, 0x40) == 0x51'0000abcdULL);

} // namespace Shader::Optimization
