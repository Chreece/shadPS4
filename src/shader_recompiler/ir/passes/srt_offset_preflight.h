// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <unordered_set>
#include <vector>

#include "shader_recompiler/ir/microinstruction.h"
#include "shader_recompiler/ir/value.h"

namespace Shader::Optimization {

// The SRT native walker cannot execute control-flow-dependent offsets such as
// Phi and SelectU32. Inspect the expression before emitting ANY native code;
// such reads must retain their unflattened ReadConst / buffer path instead.
// This predicate is a strict over-approximation of structural JIT support:
// other dynamic failures (e.g. unassigned flat-buffer sources) continue through
// the existing checked ComputeOffset fallback unchanged.
[[nodiscard]] inline bool CanLowerSrtOffsetStructurally(const IR::Value& offset) {
    if (offset.IsImmediate()) {
        return true;
    }
    const IR::Inst* first = offset.TryInst();
    if (first == nullptr) {
        return false;
    }

    std::vector<const IR::Inst*> pending{first};
    std::unordered_set<const IR::Inst*> visited;
    while (!pending.empty()) {
        const IR::Inst* inst = pending.back();
        pending.pop_back();
        if (!visited.insert(inst).second) {
            continue;
        }
        switch (inst->GetOpcode()) {
        // These are leaf values read directly from the flattened user-data
        // buffer. Whether a source has actually been assigned an offset is
        // separately checked at emission time by ComputeOffset.
        case IR::Opcode::GetUserData:
        case IR::Opcode::ReadConst:
        case IR::Opcode::ReadConstBuffer:
            continue;

        case IR::Opcode::IAdd32:
        case IR::Opcode::ISub32:
        case IR::Opcode::IMul32:
        case IR::Opcode::ShiftLeftLogical32:
        case IR::Opcode::ShiftRightLogical32:
        case IR::Opcode::ShiftRightArithmetic32:
        case IR::Opcode::BitwiseAnd32:
        case IR::Opcode::BitwiseOr32:
        case IR::Opcode::BitwiseXor32:
        case IR::Opcode::BitwiseNot32:
        case IR::Opcode::UMin32:
        case IR::Opcode::UMax32:
        case IR::Opcode::ReadFirstLane:
        case IR::Opcode::FindILsb32:
            break;

        case IR::Opcode::BitFieldUExtract:
            // The native evaluator does not lower immediate widths above 32;
            // do not emit partial instructions that would subsequently fail.
            if (inst->Arg(2).IsImmediate() && inst->Arg(2).U32() > 32) {
                return false;
            }
            break;

        default:
            // Phi, SelectU32, GetAttributeU32 and other IR instructions are
            // not valid operands in the scalar SRT JIT. This is NOT a shader
            // exclusion: the unflattened access remains for guest-memory IO.
            return false;
        }

        for (size_t i = 0; i < inst->NumArgs(); ++i) {
            const IR::Value arg = inst->Arg(i);
            if (arg.IsImmediate()) {
                continue;
            }
            if (const IR::Inst* child = arg.TryInst()) {
                pending.push_back(child);
            } else {
                return false;
            }
        }
    }
    return true;
}

} // namespace Shader::Optimization
