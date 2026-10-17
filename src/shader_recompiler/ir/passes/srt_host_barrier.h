// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include "shader_recompiler/ir/passes/srt_uniformity.h"

namespace Shader::Optimization {

// Diagnostic-only counterpart of IsHostUniformSrtOffset. Return the first
// blocker, not a synthesized value or a guess about the guest's branch.
// It deliberately models the exact same supported native-walker operands.
enum class SrtHostBarrierKind : unsigned char {
    None,
    UnsupportedOpcode,
    InvalidImmediate,
    MissingValue,
    InvalidPcOperand,
    RecursionLimit,
};

struct SrtHostBarrier {
    SrtHostBarrierKind kind{SrtHostBarrierKind::None};
    IR::Opcode opcode{IR::Opcode::Phi};
    unsigned depth{};

    [[nodiscard]] constexpr bool HasBarrier() const noexcept {
        return kind != SrtHostBarrierKind::None;
    }
};

[[nodiscard]] constexpr const char* SrtHostBarrierKindName(SrtHostBarrierKind kind) {
    switch (kind) {
    case SrtHostBarrierKind::None: return "none";
    case SrtHostBarrierKind::UnsupportedOpcode: return "unsupported-opcode";
    case SrtHostBarrierKind::InvalidImmediate: return "invalid-immediate";
    case SrtHostBarrierKind::MissingValue: return "missing-value";
    case SrtHostBarrierKind::InvalidPcOperand: return "invalid-pc-operand";
    case SrtHostBarrierKind::RecursionLimit: return "recursion-limit";
    }
    return "unknown";
}

template <typename Value>
[[nodiscard]] SrtHostBarrier FindSrtHostBarrier(const Value& value, unsigned depth = 0) {
    if (depth >= 64) {
        return {SrtHostBarrierKind::RecursionLimit, IR::Opcode::Phi, depth};
    }
    if (value.IsImmediate()) {
        return value.Type() == IR::Type::U32
            ? SrtHostBarrier{}
            : SrtHostBarrier{SrtHostBarrierKind::InvalidImmediate, IR::Opcode::Phi, depth};
    }
    const auto* inst = value.TryInst();
    if (!inst) {
        return {SrtHostBarrierKind::MissingValue, IR::Opcode::Phi, depth};
    }
    const auto op = inst->GetOpcode();
    auto visit = [&](const Value& arg) {
        return FindSrtHostBarrier(arg, depth + 1);
    };
    switch (op) {
    case IR::Opcode::GetUserData:
        return {};
    case IR::Opcode::GetPcLo:
        return inst->Arg(0).IsImmediate() && inst->Arg(0).Type() == IR::Type::U32
            ? SrtHostBarrier{}
            : SrtHostBarrier{SrtHostBarrierKind::InvalidPcOperand, op, depth};
    case IR::Opcode::ReadConst:
        for (unsigned i=0;i<2;++i) {
            if (auto b=visit(inst->Arg(i)); b.HasBarrier()) return b;
        }
        return {};
    case IR::Opcode::ReadConstBuffer: {
        // Only V# dwords 0,1,2 and the index determine scalar guest reads;
        // dword 3 must not falsely reject a host-uniform scalar address.
        const auto* const desc=inst->Arg(0).TryInst();
        if (desc && desc->GetOpcode() == IR::Opcode::CompositeConstructU32x4) {
            for (unsigned i=0;i<3;++i) {
                if (auto b=visit(desc->Arg(i)); b.HasBarrier()) return b;
            }
            return visit(inst->Arg(1));
        }
        for (unsigned i=0;i<2;++i) {
            if (auto b=visit(inst->Arg(i)); b.HasBarrier()) return b;
        }
        return {};
    }
    case IR::Opcode::ReadLane:
    case IR::Opcode::ReadFirstLane:
        return visit(inst->Arg(0));
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
        for (unsigned i=0;i<inst->NumArgs();++i) {
            if (auto b=visit(inst->Arg(i)); b.HasBarrier()) return b;
        }
        return {};
    default:
        // Select, Phi, lane inputs and any unknown opcode must remain on
        // the guest GPU path unless a future correct evaluator supports them.
        return {SrtHostBarrierKind::UnsupportedOpcode, op, depth};
    }
}

} // namespace Shader::Optimization
