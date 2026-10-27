// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <unordered_set>

#include "shader_recompiler/ir/microinstruction.h"
#include "shader_recompiler/ir/srt_gvn_table.h"

namespace Shader::Optimization {

// The SRT walker runs on the CPU before shader execution. It may fold a Phi only
// when every reachable incoming value is equivalent independently of the chosen
// control-flow edge. A loop-carried/different-value Phi must remain dynamic;
// taking its first incoming operand would silently bind the wrong descriptors.
inline IR::Value ResolveInvariantSrtPhi(IR::Value value, SrtGvnTable& gvn,
                                         std::unordered_set<IR::Inst*>& active_phis) {
    if (value.IsEmpty() || value.IsImmediate()) {
        return value;
    }
    IR::Inst* const inst = value.Inst();
    if (inst->GetOpcode() != IR::Opcode::Phi) {
        return value;
    }
    if (!active_phis.insert(inst).second) {
        return {}; // Indirect Phi cycle: cannot choose a control-flow edge on the host.
    }

    IR::Value invariant{};
    u32 invariant_number{};
    for (size_t i = 0; i < inst->NumArgs(); ++i) {
        const IR::Value input = inst->Arg(i);
        if (input.TryInst() == inst) {
            continue; // Self-reference does not change an otherwise invariant Phi.
        }
        const IR::Value candidate = ResolveInvariantSrtPhi(input, gvn, active_phis);
        if (candidate.IsEmpty()) {
            active_phis.erase(inst);
            return {};
        }
        const u32 number = gvn.GetValueNumber(candidate);
        if (invariant.IsEmpty()) {
            invariant = candidate;
            invariant_number = number;
        } else if (number != invariant_number) {
            active_phis.erase(inst);
            return {}; // Runtime/control-flow-dependent Phi.
        }
    }

    active_phis.erase(inst);
    return invariant; // Empty for a Phi with no non-self incoming values.
}

inline IR::Value ResolveInvariantSrtPhi(IR::Value value, SrtGvnTable& gvn) {
    std::unordered_set<IR::Inst*> active_phis;
    return ResolveInvariantSrtPhi(value, gvn, active_phis);
}

} // namespace Shader::Optimization
