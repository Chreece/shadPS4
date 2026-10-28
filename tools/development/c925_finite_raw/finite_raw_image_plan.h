// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <limits>
#include <optional>
#include <vector>
#include "shader_recompiler/frontend/control_flow_graph.h"
#include "shader_recompiler/ir/basic_block.h"

namespace Shader::Optimization {
namespace FiniteRawImage {
// Compiler expansion budget, never a guessed guest table length.
inline constexpr u32 MaxAlternatives = 16;
struct Range { u32 first; u32 count; };
struct Plan {
    IR::Inst* selector{};
    IR::Value base{};
    Range range{};
    std::vector<std::array<u32, 8>> offsets;
};
inline bool ImmediateU32(IR::Value value) {
    return value.IsImmediate() && value.Type() == IR::Type::U32;
}
inline bool OffsetOp(IR::Opcode op) {
    switch (op) {
    case IR::Opcode::IAdd32: case IR::Opcode::ISub32: case IR::Opcode::IMul32:
    case IR::Opcode::ShiftLeftLogical32: case IR::Opcode::ShiftRightLogical32:
    case IR::Opcode::BitwiseAnd32: return true;
    default: return false;
    }
}
inline bool FindSelector(IR::Value value, IR::Inst*& selector, unsigned depth = 0) {
    if (ImmediateU32(value)) return true;
    auto* inst = value.TryInst();
    if (!inst || depth >= 16) return false;
    if (inst->GetOpcode() == IR::Opcode::Phi) {
        if (selector && selector != inst) return false;
        selector = inst;
        return true;
    }
    return OffsetOp(inst->GetOpcode()) && inst->NumArgs() == 2 &&
           FindSelector(inst->Arg(0), selector, depth + 1) &&
           FindSelector(inst->Arg(1), selector, depth + 1);
}
inline std::optional<u32> Offset(IR::Value value, const IR::Inst* selector,
                                 u32 selected, unsigned depth = 0) {
    if (ImmediateU32(value)) return value.U32();
    const auto* inst = value.TryInst();
    if (!inst || depth >= 16) return {};
    if (inst == selector) return selected;
    if (!OffsetOp(inst->GetOpcode()) || inst->NumArgs() != 2) return {};
    const auto a = Offset(inst->Arg(0), selector, selected, depth + 1);
    const auto b = Offset(inst->Arg(1), selector, selected, depth + 1);
    if (!a || !b) return {};
    switch (inst->GetOpcode()) {
    case IR::Opcode::IAdd32: return u32(*a + *b);
    case IR::Opcode::ISub32: return u32(*a - *b);
    case IR::Opcode::IMul32: return u32(u64(*a) * *b);
    case IR::Opcode::BitwiseAnd32: return *a & *b;
    case IR::Opcode::ShiftLeftLogical32:
        if (*b < 32) return u32(*a << *b);
        return {};
    case IR::Opcode::ShiftRightLogical32:
        if (*b < 32) return *a >> *b;
        return {};
    default: return {};
    }
}
inline std::optional<Range> ProveInduction(const IR::Inst* phi) {
    if (!phi || phi->GetOpcode() != IR::Opcode::Phi || phi->NumArgs() != 2 ||
        phi->Flags<IR::Type>() != IR::Type::U32) return {};
    unsigned seed_index = 0;
    if (!ImmediateU32(phi->Arg(seed_index))) seed_index = 1;
    if (!ImmediateU32(phi->Arg(seed_index))) return {};
    const auto* step = phi->Arg(1 - seed_index).TryInst();
    auto* header = phi->GetParent();
    auto* latch = phi->PhiBlock(1 - seed_index);
    auto* entry = phi->PhiBlock(seed_index);
    if (!step || !latch || !entry || latch == entry || latch == header ||
        header->ImmPredecessors().size() != 2 ||
        step->GetParent() != latch || step->GetOpcode() != IR::Opcode::IAdd32) return {};
    bool have_entry = false, have_latch = false;
    for (const auto* pred : header->ImmPredecessors()) {
        have_entry |= pred == entry;
        have_latch |= pred == latch;
    }
    if (!have_entry || !have_latch) return {};
    unsigned increment = 1;
    if (step->Arg(0).TryInst() != phi) increment = 0;
    if (step->Arg(1 - increment).TryInst() != phi ||
        !ImmediateU32(step->Arg(increment)) || step->Arg(increment).U32() != 1u) return {};
    // The *actual* true backedge must be guarded by current_i < constant.
    // A comparison elsewhere in the shader is not a range proof.
    const auto* cfg = latch->cfg_block;
    if (!cfg || !cfg->branch_true || !cfg->branch_false ||
        cfg->branch_true->ir_block != header || cfg->branch_false->ir_block == header) return {};
    const auto* cond = latch->branch_cond.TryInst();
    if (!cond || cond->GetOpcode() != IR::Opcode::ConditionRef) return {};
    cond = cond->Arg(0).TryInst();
    if (!cond || (cond->GetOpcode() != IR::Opcode::SLessThan32 &&
                  cond->GetOpcode() != IR::Opcode::ULessThan32) ||
        cond->Arg(0).TryInst() != phi || !ImmediateU32(cond->Arg(1))) return {};
    const u32 first = phi->Arg(seed_index).U32();
    const u32 last = cond->Arg(1).U32();
    // Include the terminating iteration (the condition tests current_i, not i+1).
    if (first > last || last >= u32(std::numeric_limits<s32>::max()) ||
        u64(last) - first + 1 > MaxAlternatives) return {};
    return Range{first, last - first + 1};
}
inline std::optional<Plan> Analyze(const IR::Inst& read) {
    if (read.GetOpcode() != IR::Opcode::ImageRead) return {};
    const auto flags = read.Flags<IR::TextureInstInfo>();
    if (flags.is_depth || flags.is_array || flags.is_r128 || flags.has_lod ||
        flags.has_derivatives || flags.is_gather) return {};
    const auto* handle = read.Arg(0).TryInst();
    if (!handle || handle->GetOpcode() != IR::Opcode::ImageHandle) return {};
    std::array<IR::Value, 8> indices{};
    IR::Value base;
    IR::Inst* selector = nullptr;
    for (u32 word = 0; word < 8; ++word) {
        const auto* part = handle->Arg(word / 4).TryInst();
        if (!part || part->GetOpcode() != IR::Opcode::CompositeConstructU32x4) return {};
        const auto* source = part->Arg(word % 4).TryInst();
        if (!source || source->GetOpcode() != IR::Opcode::ReadConst) return {};
        const auto* pointer = source->Arg(0).TryInst();
        if (!pointer || pointer->GetOpcode() != IR::Opcode::CompositeConstructU32x2) return {};
        // Only a genuine root SGPR pointer. Wave-selected/runtime pointer chains
        // are explicitly outside this transform.
        for (u32 half = 0; half < 2; ++half) {
            const auto* root = pointer->Arg(half).TryInst();
            if (!root || root->GetOpcode() != IR::Opcode::GetUserData) return {};
        }
        if (word == 0) {
            base = source->Arg(0);
        } else {
            const auto* root = base.TryInst();
            if (pointer->Arg(0) != root->Arg(0) || pointer->Arg(1) != root->Arg(1)) return {};
        }
        indices[word] = source->Arg(1);
        if (!FindSelector(indices[word], selector)) return {};
    }
    const auto range = ProveInduction(selector);
    if (!range) return {};
    Plan plan{selector, base, *range, {}};
    for (u32 i = 0; i < range->count; ++i) {
        std::array<u32, 8> offsets{};
        for (u32 word = 0; word < 8; ++word) {
            const auto offset = Offset(indices[word], selector, range->first + i);
            if (!offset) return {};
            offsets[word] = *offset;
            if (word && u64(offsets[0]) + word != offsets[word]) return {};
        }
        plan.offsets.push_back(offsets);
    }
    return plan;
}
} // namespace FiniteRawImage
} // namespace Shader::Optimization
