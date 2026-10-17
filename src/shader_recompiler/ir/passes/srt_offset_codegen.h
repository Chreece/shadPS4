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


// Portable x86-64 SRT FindILsb32 lowering, validated independently in GoW.
// The GCN instruction returns UINT32_MAX for a zero input, unlike TZCNT.
// BSF sets ZF and MOV preserves it, allowing CMOVZ to select the sentinel.
inline void EmitSrtFindILsb32(Xbyak::CodeGenerator& c, const Xbyak::Reg32 dst) {
    using namespace Xbyak::util;
    c.bsf(dst, dst);
    c.mov(ecx, 0xffffffffU);
    c.cmovz(dst, ecx);
}

// Failed SRT expressions must not leave partial native code, including PUSHes,
// after returning to the caller. This is code generation only: no guest patching.
template <typename Emit>
[[nodiscard]] bool EmitSrtWalkerAtomically(Xbyak::CodeGenerator& code, Emit&& emit) {
    const auto checkpoint = code.getSize();
    if (emit()) {
        return true;
    }
    code.setSize(checkpoint);
    return false;
}

} // namespace Shader::Optimization::SrtCodegen
