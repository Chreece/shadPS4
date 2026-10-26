// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <bit>

#include "common/arch.h"
#include "common/types.h"

#ifdef ARCH_X86_64
#include <xbyak/xbyak.h>
#include <xbyak/xbyak_util.h>
#endif

namespace Shader::Optimization {

// Matches SPIR-V OpFindILsb: index of the least-significant 1-bit, or
// 0xFFFFFFFF if none are set. The CPU SRT walker must preserve this exact
// guest value, including the zero-input case (BSF alone is undefined then).
[[nodiscard]] constexpr u32 FindSrtLeastSignificantBit(u32 value) noexcept {
    return value != 0u ? static_cast<u32>(std::countr_zero(value)) : ~u32{0};
}

static_assert(FindSrtLeastSignificantBit(0u) == 0xffffffffu);
static_assert(FindSrtLeastSignificantBit(1u) == 0u);
static_assert(FindSrtLeastSignificantBit(0x80000000u) == 31u);

#ifdef ARCH_X86_64
// Shared by the production SRT walker and its executable JIT regression.
// BSF on zero sets ZF but leaves destination undefined. CMOVZ selects the
// SPIR-V/IR sentinel, without ever observing BSF's undefined destination.
inline void EmitSrtFindLeastSignificantBit(Xbyak::CodeGenerator& code, Xbyak::Reg32 result) {
    code.mov(Xbyak::util::ecx, 0xffffffffu);
    code.bsf(result, result);
    code.cmovz(result, Xbyak::util::ecx);
}
#endif

} // namespace Shader::Optimization
