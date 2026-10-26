// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <unordered_set>

#include "shader_recompiler/ir/microinstruction.h"
#include "shader_recompiler/ir/passes/srt_phi.h"

namespace Shader::Optimization {

// GCN ReadFirstLane takes the first ACTIVE GPU lane, so it is generally not
// evaluable by a CPU SRT walker. It may be unwrapped only when its source is
// proven identical in every lane, independently of execution-mask selection.
// Be deliberately conservative: GPU attributes, buffer loads, lane IDs,
// mutable memory, and non-invariant Phi values must remain GPU-dependent.
inline bool IsHostUniformSrtValue(
    IR::Value value, SrtGvnTable& gvn,
    std::unordered_set<const IR::Inst*>& active, unsigned depth) {
    if (value.IsEmpty() || depth > 32) {
        return false;
    }
    if (value.IsImmediate()) {
        return value.Type() == IR::Type::U32;
    }
    const IR::Inst* inst = value.TryInst();
    if (!inst || !active.insert(inst).second) {
        return false;
    }
    const auto all_args_uniform = [&]() {
        for (size_t arg = 0; arg < inst->NumArgs(); ++arg) {
            if (!IsHostUniformSrtValue(inst->Arg(arg), gvn, active, depth + 1)) {
                return false;
            }
        }
        return true;
    };
    bool uniform = false;
    switch (inst->GetOpcode()) {
    case IR::Opcode::GetUserData:
        // Program user-data SGPRs are launch-uniform by construction.
        uniform = true;
        break;
    case IR::Opcode::ReadConst:
        // Already flattened by the host walker; a zero offset is NOT a
        // valid cached dword and cannot be used to prove uniformity.
        uniform = inst->Flags<u16>() != 0;
        break;
    case IR::Opcode::ReadConstBuffer:
        uniform = inst->Flags<IR::BufferInstInfo>().flatbuf_off_dw != 0;
        break;
    case IR::Opcode::Phi: {
        const IR::Value invariant = ResolveInvariantSrtPhi(value, gvn);
        uniform = !invariant.IsEmpty() &&
                  IsHostUniformSrtValue(invariant, gvn, active, depth + 1);
        break;
    }
    case IR::Opcode::IAdd32:
    case IR::Opcode::ISub32:
    case IR::Opcode::IMul32:
    case IR::Opcode::ShiftLeftLogical32:
    case IR::Opcode::ShiftRightLogical32:
    case IR::Opcode::BitwiseAnd32:
    case IR::Opcode::BitwiseOr32:
    case IR::Opcode::BitwiseXor32:
    case IR::Opcode::BitwiseNot32:
    case IR::Opcode::UMin32:
    case IR::Opcode::UMax32:
    case IR::Opcode::BitFieldUExtract:
    case IR::Opcode::FindILsb32:
    case IR::Opcode::ReadFirstLane:
        uniform = all_args_uniform();
        break;
    default:
        // Explicit rejection: GetAttributeU32, ReadLane, vector input,
        // dynamic buffer accesses and all other GPU-dependent operations.
        break;
    }
    active.erase(inst);
    return uniform;
}

inline bool IsHostUniformSrtValue(IR::Value value, SrtGvnTable& gvn) {
    std::unordered_set<const IR::Inst*> active;
    return IsHostUniformSrtValue(value, gvn, active, 0);
}

} // namespace Shader::Optimization
