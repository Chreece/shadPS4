// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <cstdint>
#include <xbyak/xbyak.h>
#include <xbyak/xbyak_util.h>

namespace Shader::Optimization::SrtCodegen {

// The destination initially contains the dynamic shift count; preserve that
// count before loading the immediate left operand. Destination must not be ECX.
inline void EmitConstantLeftShiftByDynamicCount(Xbyak::CodeGenerator& c,
                                                 const Xbyak::Reg32& destination,
                                                 std::uint32_t left_operand) {
    c.mov(Xbyak::util::ecx, destination);
    c.mov(destination, left_operand);
    c.shl(destination, Xbyak::util::cl);
}

// Return a 32-bit low-bit mask for a runtime width.
// width == 0 -> 0, width in [1,31] -> (1u<<width)-1,
// width >= 32 -> 0xffffffff. x86 shift counts wrap modulo 32, so a plain
// SHL/DEC sequence is incorrect for widths 32, 64, etc.
// The input must not alias ECX/EDX; the result register is EDX.
inline void EmitLowBitMask(Xbyak::CodeGenerator& c, const Xbyak::Reg32& count,
                           const Xbyak::Reg32& mask) {
    c.mov(Xbyak::util::ecx, count);
    c.mov(mask, 1);
    c.shl(mask, Xbyak::util::cl);
    c.dec(mask);
    c.cmp(Xbyak::util::ecx, 32);
    // MOV does not change the comparison flags.
    c.mov(Xbyak::util::ecx, 0xffffffffu);
    c.cmovae(mask, Xbyak::util::ecx);
}

} // namespace Shader::Optimization::SrtCodegen
