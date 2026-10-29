// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "shader_recompiler/ir/passes/finite_raw_image_plan.h"

namespace Shader::Optimization::UniformRawSample {
// This bounds compiler expansion, not guest memory or a fabricated table size.
inline constexpr u32 MaxAlternatives = 16;
struct Plan {
    IR::Inst* selector{};
    IR::Value base{};
    u32 first{};
    u32 root_sgpr{};
    u32 count_word{};
    std::array<IR::Value, 8> indices{};
};

inline std::optional<u32> Root(IR::Value value) {
    const auto* pair = value.TryInst();
    if (!pair || pair->GetOpcode() != IR::Opcode::CompositeConstructU32x2) return {};
    const auto* lo = pair->Arg(0).TryInst();
    const auto* hi = pair->Arg(1).TryInst();
    if (!lo || !hi || lo->GetOpcode() != IR::Opcode::GetUserData ||
        hi->GetOpcode() != IR::Opcode::GetUserData) return {};
    const u32 index = static_cast<u32>(lo->Arg(0).ScalarReg());
    if (index + 1u != static_cast<u32>(hi->Arg(0).ScalarReg())) return {};
    return index;
}

inline std::optional<Plan> Analyze(const IR::Inst& sample) {
    if (sample.GetOpcode() != IR::Opcode::ImageSampleRaw) return {};
    const auto flags = sample.Flags<IR::TextureInstInfo>();
    // Only explicit-LOD sampling, never implicit derivatives inside new branches.
    if (flags.is_depth || flags.is_gather || flags.is_array || flags.is_r128 ||
        flags.is_unnormalized || flags.has_derivatives || flags.has_bias ||
        flags.has_lod_clamp || (bool(flags.has_lod) == bool(flags.force_level0))) return {};
    const auto* handle = sample.Arg(0).TryInst();
    const auto* sampler = sample.Arg(1).TryInst();
    if (!handle || handle->GetOpcode() != IR::Opcode::ImageHandle || !sampler ||
        sampler->GetOpcode() != IR::Opcode::CompositeConstructU32x4) return {};
    Plan plan;
    std::optional<u32> root;
    for (u32 word = 0; word < 8; ++word) {
        const auto* part = handle->Arg(word / 4).TryInst();
        if (!part || part->GetOpcode() != IR::Opcode::CompositeConstructU32x4) return {};
        const auto* src = part->Arg(word % 4).TryInst();
        if (!src || src->GetOpcode() != IR::Opcode::ReadConst) return {};
        const auto ptr = Root(src->Arg(0));
        if (!ptr || (root && *root != *ptr)) return {};
        root = ptr;
        if (word == 0) plan.base = src->Arg(0);
        plan.indices[word] = src->Arg(1);
        if (!FiniteRawImage::FindSelector(src->Arg(1), plan.selector)) return {};
    }
    // Preserve a genuine, direct uniform sampler. Do not select a host lane.
    for (u32 word = 0; word < 4; ++word) {
        const auto* src = sampler->Arg(word).TryInst();
        if (!src || src->GetOpcode() != IR::Opcode::ReadConst ||
            Root(src->Arg(0)) != root || !FiniteRawImage::ImmediateU32(src->Arg(1))) return {};
    }
    const auto* phi = plan.selector;
    if (!phi || phi->GetOpcode() != IR::Opcode::Phi || phi->NumArgs() != 2 ||
        phi->Flags<IR::Type>() != IR::Type::U32) return {};
    unsigned seed = FiniteRawImage::ImmediateU32(phi->Arg(0)) ? 0u : 1u;
    if (!FiniteRawImage::ImmediateU32(phi->Arg(seed))) return {};
    auto* header = phi->GetParent();
    auto* latch = phi->PhiBlock(1 - seed);
    auto* entry = phi->PhiBlock(seed);
    const auto* step = phi->Arg(1 - seed).TryInst();
    if (!header || !entry || !latch || entry == latch || latch == header ||
        sample.GetParent() != latch || header->ImmPredecessors().size() != 2 ||
        latch->ImmPredecessors().size() != 1 || latch->ImmPredecessors()[0] != header ||
        !step || step->GetParent() != latch || step->GetOpcode() != IR::Opcode::IAdd32) return {};
    bool have_entry = false, have_latch = false;
    for (const auto* pred : header->ImmPredecessors()) {
        have_entry |= pred == entry;
        have_latch |= pred == latch;
    }
    if (!have_entry || !have_latch) return {};
    const unsigned increment = step->Arg(0).TryInst() == phi ? 1u : 0u;
    if (step->Arg(1 - increment).TryInst() != phi ||
        !FiniteRawImage::ImmediateU32(step->Arg(increment)) ||
        step->Arg(increment).U32() != 1u) return {};
    const auto* back = latch->cfg_block;
    const auto* head = header->cfg_block;
    if (!back || !head || !back->branch_true || back->branch_false ||
        back->branch_true->ir_block != header || !head->branch_true || !head->branch_false) return {};
    const auto* cond = header->branch_cond.TryInst();
    if (!cond || cond->GetOpcode() != IR::Opcode::ConditionRef) return {};
    cond = cond->Arg(0).TryInst();
    bool inverted = false;
    if (cond && cond->GetOpcode() == IR::Opcode::LogicalNot) {
        inverted = true;
        cond = cond->Arg(0).TryInst();
    }
    if (!cond || (cond->GetOpcode() != IR::Opcode::SLessThan32 &&
                  cond->GetOpcode() != IR::Opcode::ULessThan32) ||
        cond->Arg(0).TryInst() != phi) return {};
    const auto* body_target = inverted ? head->branch_false : head->branch_true;
    const auto* exit_target = inverted ? head->branch_true : head->branch_false;
    if (body_target->ir_block != latch || exit_target->ir_block == latch ||
        exit_target->ir_block == header) return {};
    const auto* bound = cond->Arg(1).TryInst();
    // The bound is an unconditional entry read, not a wave/lane/loop-derived value.
    if (!bound || bound->GetOpcode() != IR::Opcode::ReadConst ||
        !bound->GetParent()->ImmPredecessors().empty() ||
        !FiniteRawImage::ImmediateU32(bound->Arg(1)) || Root(bound->Arg(0)) != root) return {};
    plan.first = phi->Arg(seed).U32();
    if (plan.first >= u32(std::numeric_limits<s32>::max())) return {};
    plan.root_sgpr = *root;
    plan.count_word = bound->Arg(1).U32();
    return plan;
}

inline std::optional<std::vector<std::array<u32, 8>>> Offsets(const Plan& plan, u32 bound) {
    // Both signed and unsigned comparison domains coincide below INT_MAX.
    // The condition is a pre-test: the final selected index is bound-1, not bound.
    if (bound <= plan.first || bound >= u32(std::numeric_limits<s32>::max()) ||
        u64(bound) - plan.first > MaxAlternatives) return {};
    std::vector<std::array<u32, 8>> out;
    for (u32 selected = plan.first; selected < bound; ++selected) {
        std::array<u32, 8> words{};
        for (u32 word = 0; word < 8; ++word) {
            const auto offset = FiniteRawImage::Offset(plan.indices[word], plan.selector, selected);
            if (!offset || (word && u64(words[0]) + word != *offset)) return {};
            words[word] = *offset;
        }
        out.push_back(words);
    }
    return out;
}
} // namespace Shader::Optimization::UniformRawSample
