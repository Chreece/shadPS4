// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <xbyak/xbyak.h>
#include <xbyak/xbyak_util.h>

namespace Shader::Optimization {

// IR FindILsb32 / SPIR-V FindILsb semantics for a dynamic SRT offset.
// BSF is available on baseline x86-64, unlike BMI1 TZCNT. It sets ZF
// for zero input, where the guest instruction result must be UINT32_MAX.
// EFLAGS are scratch for the SRT walker; ECX is its existing temporary
// register and must not be the destination.
inline void EmitSrtFindILsb32(Xbyak::CodeGenerator& c, const Xbyak::Reg32 dst) {
    using namespace Xbyak::util;
    c.bsf(dst, dst);
    c.mov(ecx, 0xffffffffU);  // MOV leaves the BSF zero flag unchanged.
    c.cmovz(dst, ecx);
}

// A failed offset calculation may already have emitted pushes, register
// changes, or a partial expression. Never leave these bytes in a completed
// walker: on failure restore the exact previous code position. This affects
// only host code *generation*, not the guest shader or any runtime patches.
template <typename Emit>
[[nodiscard]] bool EmitSrtWalkerAtomically(Xbyak::CodeGenerator& code, Emit&& emit) {
    const auto checkpoint = code.getSize();
    if (emit()) {
        return true;
    }
    code.setSize(checkpoint);
    return false;
}

} // namespace Shader::Optimization
