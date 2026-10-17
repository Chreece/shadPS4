// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "shader_recompiler/ir/opcodes.h"

namespace Shader::Optimization {

// The CPU-side SRT walker can evaluate a GPU lane read only when its source
// is demonstrably wave-uniform. Never turn a divergent ReadFirstLane/ReadLane
// into a host instruction, which would silently change shader semantics.
// The proof is conservative: Phi (including loops), per-lane attributes,
// and unknown opcodes remain shader-side expressions.
//
// Template also permits host-independent regression tests with synthetic IR.
template <typename Value>
[[nodiscard]] bool IsHostUniformSrtOffset(const Value& value, unsigned depth = 0) {
    if (depth >= 64) {
        return false; // Including malformed/cyclic SSA dependencies.
    }
    if (value.IsImmediate()) {
        return value.Type() == IR::Type::U32;
    }
    const auto* inst = value.TryInst();
    if (inst == nullptr) {
        return false;
    }
    switch (inst->GetOpcode()) {
    case IR::Opcode::GetUserData:
        // SGPR user data is supplied per draw/dispatch, independent of lane.
        return true;
    case IR::Opcode::GetPcLo:
        // GetPcLo wraps an immediate program-counter value. This is a
        // compile-time scalar for every lane; it has no GPU-lane input.
        return inst->Arg(0).IsImmediate() && inst->Arg(0).Type() == IR::Type::U32;
    case IR::Opcode::ReadConst:
        // S_LOAD uses a complete 64-bit guest pointer and a dword index.
        return IsHostUniformSrtOffset(inst->Arg(0), depth + 1) &&
               IsHostUniformSrtOffset(inst->Arg(1), depth + 1);
    case IR::Opcode::ReadConstBuffer: {
        // S_BUFFER_LOAD uses *only* V# dword 0 and the low eight address
        // bits of dword 1. Descriptor dwords 2/3 encode record count and
        // metadata, never guest-address inputs. Their SSA producers may be
        // dynamically selected without making the resulting scalar load
        // address lane-divergent. Requiring all four to be host-evaluable
        // incorrectly drops valid descriptor reads and downstream T#/S#.
        const auto* const descriptor = inst->Arg(0).TryInst();
        if (descriptor &&
            descriptor->GetOpcode() == IR::Opcode::CompositeConstructU32x4) {
            return IsHostUniformSrtOffset(descriptor->Arg(0), depth + 1) &&
                   IsHostUniformSrtOffset(descriptor->Arg(1), depth + 1) &&
                   IsHostUniformSrtOffset(inst->Arg(1), depth + 1);
        }
        // Other source representations retain the original conservative
        // proof: no accidental admission of unsupported address forms.
        return IsHostUniformSrtOffset(inst->Arg(0), depth + 1) &&
               IsHostUniformSrtOffset(inst->Arg(1), depth + 1);
    }
    case IR::Opcode::ReadLane:
    case IR::Opcode::ReadFirstLane:
        // Selecting any lane of already-uniform data yields the same value.
        // The lane selector itself need not be uniform.
        return IsHostUniformSrtOffset(inst->Arg(0), depth + 1);
    case IR::Opcode::CompositeConstructU32x2:
    case IR::Opcode::CompositeConstructU32x3:
    case IR::Opcode::CompositeConstructU32x4:
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
    case IR::Opcode::BitFieldUExtract:
    case IR::Opcode::FindILsb32:
        for (size_t i = 0; i < inst->NumArgs(); ++i) {
            if (!IsHostUniformSrtOffset(inst->Arg(i), depth + 1)) {
                return false;
            }
        }
        return true;
    default:
        // In particular, do not assume SSA Phi control flow or GPU VGPRs
        // are host-computable. Proper shader-side addressing is future work.
        return false;
    }
}

} // namespace Shader::Optimization
