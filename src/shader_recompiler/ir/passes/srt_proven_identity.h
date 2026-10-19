// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include <cstddef>
#include <optional>
#include "shader_recompiler/ir/opcodes.h"

namespace Shader::Optimization {

// Only accept equality provable without executing guest control flow.
// Equal immediates must be U32 and bitwise equal; other sources must be
// literally the same SSA instruction (not merely the same opcode).
template <typename Value>
[[nodiscard]] bool SameSrtU32Value(const Value& lhs, const Value& rhs) {
    if (lhs.IsImmediate() || rhs.IsImmediate()) {
        return lhs.IsImmediate() && rhs.IsImmediate() &&
               lhs.Type() == IR::Type::U32 && rhs.Type() == IR::Type::U32 &&
               lhs.U32() == rhs.U32();
    }
    const auto* a = lhs.TryInst();
    const auto* b = rhs.TryInst();
    return a && a == b;
}

// Resolve only semantics-preserving U32 identities for the native SRT walker.
// * BitCastU32F32(BitCastF32U32(x)) returns the exact 32-bit bits of x.
// * Phi merges with the SAME incoming SSA value/constant on every reachable
//   edge need no branch decision. A direct self-edge is a recurrence of the
//   already established value, but a Phi with ONLY self-edges is unknown.
//
// Distinct incoming values, float arithmetic, nested cyclic Phis and all
// unrecognized opcodes stay on the original GPU shader path. This is not an
// attempt to run divergent GPU control flow on the host.
template <typename Value>
[[nodiscard]] std::optional<Value> ResolveProvenSrtIdentity(const Value& value) {
    Value current = value;
    std::array<const void*, 64> visited{};
    std::size_t depth = 0;
    bool changed = false;
    while (depth < visited.size()) {
        if (current.IsImmediate()) {
            return changed && current.Type() == IR::Type::U32
                ? std::optional<Value>{current} : std::nullopt;
        }
        const auto* inst = current.TryInst();
        if (!inst) return std::nullopt;
        for (std::size_t i = 0; i < depth; ++i) {
            if (visited[i] == inst) return std::nullopt;
        }
        visited[depth++] = inst;
        if (inst->GetOpcode() == IR::Opcode::BitCastU32F32) {
            const auto* reverse = inst->Arg(0).TryInst();
            if (!reverse || reverse->GetOpcode() != IR::Opcode::BitCastF32U32) {
                return std::nullopt;
            }
            current = reverse->Arg(0);
            changed = true;
            continue;
        }
        if (inst->GetOpcode() == IR::Opcode::Phi) {
            const std::size_t n = inst->NumArgs();
            if (n == 0 || n > 64) return std::nullopt;
            std::optional<Value> incoming;
            for (std::size_t i = 0; i < n; ++i) {
                const Value v = inst->Arg(i);
                if (!v.IsImmediate() && v.TryInst() == inst) continue;
                if (!incoming) incoming = v;
                else if (!SameSrtU32Value(*incoming, v)) return std::nullopt;
            }
            if (!incoming) return std::nullopt;
            current = *incoming;
            changed = true;
            continue;
        }
        return changed ? std::optional<Value>{current} : std::nullopt;
    }
    return std::nullopt;
}

} // namespace Shader::Optimization
