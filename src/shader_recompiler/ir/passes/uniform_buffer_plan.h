// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <optional>
#include "shader_recompiler/frontend/control_flow_graph.h"
#include "shader_recompiler/ir/dominance.h"

namespace Shader::Optimization {

struct UniformBufferPlan {
    IR::Inst* selector{};
    IR::Value root;
    std::array<IR::Value, 4> offsets;
    u32 root_sgpr{};
    u32 count_word{};
    u32 first{};
};

inline std::optional<u32> UniformBufferRoot(IR::Value value) {
    const auto* pair = value.TryInst();
    if (!pair || pair->GetOpcode() != IR::Opcode::CompositeConstructU32x2) {
        return std::nullopt;
    }
    const auto* lo = pair->Arg(0).TryInst();
    const auto* hi = pair->Arg(1).TryInst();
    if (!lo || !hi || lo->GetOpcode() != IR::Opcode::GetUserData ||
        hi->GetOpcode() != IR::Opcode::GetUserData) {
        return std::nullopt;
    }
    const u32 index = static_cast<u32>(lo->Arg(0).ScalarReg());
    if (static_cast<u32>(hi->Arg(0).ScalarReg()) != index + 1) {
        return std::nullopt;
    }
    return index;
}

inline std::optional<u32> EvaluateUniformBufferOffset(IR::Value value, IR::Inst*& selector,
                                                      u32 selected, u32 depth = 0) {
    if (depth > 32) {
        return std::nullopt;
    }
    if (value.IsImmediate()) {
        return value.Type() == IR::Type::U32 ? std::optional{value.U32()} : std::nullopt;
    }
    auto* inst = value.Inst();
    if (inst->GetOpcode() == IR::Opcode::Phi) {
        if (selector && selector != inst) {
            return std::nullopt;
        }
        selector = inst;
        return selected;
    }
    switch (inst->GetOpcode()) {
    case IR::Opcode::IAdd32:
    case IR::Opcode::ISub32:
    case IR::Opcode::IMul32:
    case IR::Opcode::ShiftLeftLogical32:
    case IR::Opcode::ShiftRightLogical32:
    case IR::Opcode::BitwiseAnd32:
        break;
    default:
        return std::nullopt;
    }
    const auto lhs = EvaluateUniformBufferOffset(inst->Arg(0), selector, selected, depth + 1);
    const auto rhs = EvaluateUniformBufferOffset(inst->Arg(1), selector, selected, depth + 1);
    if (!lhs || !rhs) {
        return std::nullopt;
    }
    switch (inst->GetOpcode()) {
    case IR::Opcode::IAdd32:
        return *lhs + *rhs;
    case IR::Opcode::ISub32:
        return *lhs - *rhs;
    case IR::Opcode::IMul32:
        return *lhs * *rhs;
    case IR::Opcode::ShiftLeftLogical32:
        return *rhs < 32 ? std::optional{*lhs << *rhs} : std::nullopt;
    case IR::Opcode::ShiftRightLogical32:
        return *rhs < 32 ? std::optional{*lhs >> *rhs} : std::nullopt;
    case IR::Opcode::BitwiseAnd32:
        return *lhs & *rhs;
    default:
        return std::nullopt;
    }
}

inline std::optional<UniformBufferPlan> AnalyzeUniformBufferStore(IR::Inst& store) {
    switch (store.GetOpcode()) {
    case IR::Opcode::StoreBufferU32:
    case IR::Opcode::StoreBufferU32x2:
    case IR::Opcode::StoreBufferU32x3:
    case IR::Opcode::StoreBufferU32x4:
        break;
    default:
        return std::nullopt;
    }
    const auto* handle = store.Arg(0).TryInst();
    if (!handle || handle->GetOpcode() != IR::Opcode::CompositeConstructU32x4) {
        return std::nullopt;
    }
    UniformBufferPlan plan;
    for (u32 i = 0; i < 4; ++i) {
        const auto* read = handle->Arg(i).TryInst();
        if (!read || read->GetOpcode() != IR::Opcode::ReadConst) {
            return std::nullopt;
        }
        const auto root = UniformBufferRoot(read->Arg(0));
        if (!root || (i != 0 && *root != plan.root_sgpr)) {
            return std::nullopt;
        }
        plan.root_sgpr = *root;
        plan.root = read->Arg(0);
        plan.offsets[i] = read->Arg(1);
        if (!EvaluateUniformBufferOffset(plan.offsets[i], plan.selector, 0)) {
            return std::nullopt;
        }
    }
    auto* phi = plan.selector;
    if (!phi || phi->Type() != IR::Type::U32 || phi->NumArgs() != 2) {
        return std::nullopt;
    }
    const u32 seed_arg = phi->Arg(0).IsImmediate() ? 0 : 1;
    const auto seed = phi->Arg(seed_arg);
    if (!seed.IsImmediate() || seed.Type() != IR::Type::U32 || seed.U32() >= 0x7fffffff) {
        return std::nullopt;
    }
    plan.first = seed.U32();
    auto* header = phi->GetParent();
    auto* entry = phi->PhiBlock(seed_arg);
    auto* latch = phi->PhiBlock(1 - seed_arg);
    const auto* next = phi->Arg(1 - seed_arg).TryInst();
    if (!next || next->GetOpcode() != IR::Opcode::IAdd32 || next->GetParent() != latch ||
        !((next->Arg(0) == IR::Value{phi} && next->Arg(1) == IR::Value{1u}) ||
          (next->Arg(1) == IR::Value{phi} && next->Arg(0) == IR::Value{1u}))) {
        return std::nullopt;
    }
    if (header == latch || header == entry || entry == latch ||
        header->ImmPredecessors().size() != 2 ||
        std::ranges::find(header->ImmPredecessors(), entry) == header->ImmPredecessors().end() ||
        std::ranges::find(header->ImmPredecessors(), latch) == header->ImmPredecessors().end() ||
        !latch->cfg_block || latch->cfg_block->branch_false || !latch->cfg_block->branch_true ||
        latch->cfg_block->branch_true->ir_block != header || !header->cfg_block ||
        !header->cfg_block->branch_true || !header->cfg_block->branch_false) {
        return std::nullopt;
    }
    auto* condition = header->branch_cond.TryInst();
    if (condition && condition->GetOpcode() == IR::Opcode::ConditionRef) {
        condition = condition->Arg(0).TryInst();
    }
    const bool inverted = condition && condition->GetOpcode() == IR::Opcode::LogicalNot;
    if (inverted) {
        condition = condition->Arg(0).TryInst();
    }
    if (!condition ||
        (condition->GetOpcode() != IR::Opcode::ULessThan32 &&
         condition->GetOpcode() != IR::Opcode::SLessThan32) ||
        condition->Arg(0) != IR::Value{phi}) {
        return std::nullopt;
    }
    auto* body =
        (inverted ? header->cfg_block->branch_false : header->cfg_block->branch_true)->ir_block;
    auto* exit =
        (inverted ? header->cfg_block->branch_true : header->cfg_block->branch_false)->ir_block;
    if (!body || !exit || body == exit || body == header || exit == header ||
        !IR::Dominates(header, body) || !IR::Dominates(header, latch) ||
        !IR::Dominates(body, store.GetParent())) {
        return std::nullopt;
    }
    const auto* bound = condition->Arg(1).TryInst();
    if (!bound || bound->GetOpcode() != IR::Opcode::ReadConst ||
        UniformBufferRoot(bound->Arg(0)) != std::optional{plan.root_sgpr} ||
        !bound->Arg(1).IsImmediate() || bound->Arg(1).Type() != IR::Type::U32 ||
        !IR::Dominates(bound->GetParent(), header)) {
        return std::nullopt;
    }
    plan.count_word = bound->Arg(1).U32();
    return plan;
}

} // namespace Shader::Optimization
