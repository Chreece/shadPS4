// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <span>

#include "shader_recompiler/frontend/instruction.h"

namespace Shader::Gcn {

// On GCN, a scalar instruction is executed for the whole wave even when EXEC
// masks out every lane. The same applies to branches and lane read/write
// operations. Keep this predicate shared by the CFG pass and its regression
// tests so neither side silently diverges.
[[nodiscard]] inline bool IgnoresExecMask(const GcnInst& inst) noexcept {
    switch (inst.category) {
    case InstCategory::ScalarALU:
    case InstCategory::ScalarMemory:
    case InstCategory::FlowControl:
        return true;
    default:
        break;
    }
    switch (inst.opcode) {
    case Opcode::V_READLANE_B32:
    case Opcode::V_WRITELANE_B32:
    case Opcode::V_READFIRSTLANE_B32:
        return true;
    default:
        break;
    }
    return false;
}

// Find the first SALU instruction that must run outside an EXEC-masked region
// with affected vector instructions both before and after it.
//
// Reordering scalar effects across an EXEC branch is NOT generally correct.
// The operation can be hoisted only when earlier instructions in this scope
// have not written scalar state, and the SALU itself neither references EXEC
// or VCC nor modifies EXEC. A conservative failure means "do not split",
// never "choose one lane" or "use an incorrect scalar result".
[[nodiscard]] inline s32 FindSafeScalarExecScopeSplit(std::span<const GcnInst> instructions,
                                                       s32 first_masked, s32 last_masked) noexcept {
    if (first_masked < 0 || last_masked <= first_masked ||
        static_cast<size_t>(last_masked) >= instructions.size()) {
        return -1;
    }

    bool earlier_scalar_state_may_have_changed = false;
    // Skip the first masked instruction. A split at its index would create
    // an empty conditional block; the last masked instruction must also stay
    // in the following scope.
    for (s32 i = first_masked + 1; i < last_masked; ++i) {
        const GcnInst& inst = instructions[i];
        if (inst.category == InstCategory::ScalarALU) {
            if (earlier_scalar_state_may_have_changed) {
                return -1;
            }
            for (u32 dst = 0; dst < inst.dst_count; ++dst) {
                const auto field = inst.dst[dst].field;
                if (field == OperandField::ExecLo || field == OperandField::ExecHi) {
                    return -1;
                }
            }
            for (u32 src = 0; src < inst.src_count; ++src) {
                const auto field = inst.src[src].field;
                if (field == OperandField::VccLo || field == OperandField::VccHi ||
                    field == OperandField::VccZ || field == OperandField::ExecLo ||
                    field == OperandField::ExecHi || field == OperandField::ExecZ) {
                    return -1;
                }
            }
            return i;
        }
        if (IgnoresExecMask(inst)) {
            // A preceding scalar load, lane read or branch can have modified
            // state that a following SALU consumes. Reject conservatively.
            earlier_scalar_state_may_have_changed = true;
            continue;
        }
        for (u32 dst = 0; dst < inst.dst_count; ++dst) {
            const auto field = inst.dst[dst].field;
            if (field != OperandField::VectorGPR && field != OperandField::Undefined) {
                earlier_scalar_state_may_have_changed = true;
            }
        }
    }
    return -1;
}

} // namespace Shader::Gcn
