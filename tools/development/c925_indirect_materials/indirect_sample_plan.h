// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "shader_recompiler/indirect_sample_table.h"
#include "shader_recompiler/ir/passes/uniform_raw_sample_plan.h"

namespace Shader::Optimization::IndirectSamplePlan {
struct Plan { IR::Inst* selector{}; IndirectSample::Source source{}; };
inline bool Imm(IR::Value v) { return FiniteRawImage::ImmediateU32(v); }
inline std::optional<u32> UpperBound(IR::Value value, u32 depth = 0) {
    if (Imm(value)) return value.U32();
    const auto* inst = value.TryInst();
    if (!inst || depth >= 12) return {};
    if (inst->GetOpcode() == IR::Opcode::ReadLane) return UpperBound(inst->Arg(0), depth + 1);
    if (inst->GetOpcode() == IR::Opcode::ShiftRightLogical32 && Imm(inst->Arg(1)) &&
        inst->Arg(1).U32() < 32) {
        return UpperBound(inst->Arg(0), depth + 1).value_or(0xffffffffu) >> inst->Arg(1).U32();
    }
    if (inst->GetOpcode() == IR::Opcode::BitwiseAnd32) {
        if (Imm(inst->Arg(0))) return inst->Arg(0).U32();
        if (Imm(inst->Arg(1))) return inst->Arg(1).U32();
    }
    return {};
}
inline const IR::Inst* Pointer(IR::Value value) {
    const auto* inst = value.TryInst();
    return inst && inst->GetOpcode() == IR::Opcode::CompositeConstructU32x2 ? inst : nullptr;
}
inline bool SamePointer(const IR::Inst* a, const IR::Inst* b) {
    return a && b && a->Arg(0) == b->Arg(0) && a->Arg(1) == b->Arg(1);
}
inline std::optional<Plan> Analyze(const IR::Inst& sample) {
    if (sample.GetOpcode() != IR::Opcode::ImageSampleRaw) return {};
    const auto flags = sample.Flags<IR::TextureInstInfo>();
    if (flags.is_depth || flags.is_gather || flags.is_array || flags.is_r128 ||
        flags.is_unnormalized || flags.has_derivatives || flags.has_bias || flags.has_lod_clamp ||
        bool(flags.has_lod) == bool(flags.force_level0)) return {};
    const auto* handle = sample.Arg(0).TryInst();
    const auto* sampler = sample.Arg(1).TryInst();
    if (!handle || handle->GetOpcode() != IR::Opcode::ImageHandle ||
        !sampler || sampler->GetOpcode() != IR::Opcode::CompositeConstructU32x4) return {};
    Plan out;
    const IR::Inst* pointer = nullptr;
    for (u32 word = 0; word < 12; ++word) {
        const auto* part = word < 8 ? handle->Arg(word / 4).TryInst() : sampler;
        if (!part || part->GetOpcode() != IR::Opcode::CompositeConstructU32x4) return {};
        const auto* load = part->Arg(word % 4).TryInst();
        if (!load || load->GetOpcode() != IR::Opcode::ReadConst || !Imm(load->Arg(1))) return {};
        const auto* current = Pointer(load->Arg(0));
        if (word == 0) pointer = current;
        if (!SamePointer(pointer, current)) return {};
        if (word == 0) out.source.image_word = load->Arg(1).U32();
        if (word == 8) out.source.sampler_word = load->Arg(1).U32();
        const u64 expected = word < 8 ? u64(out.source.image_word) + word
                                     : u64(out.source.sampler_word) + word - 8;
        if (expected != load->Arg(1).U32()) return {};
    }
    const auto* low = pointer->Arg(0).TryInst();
    const auto* high = pointer->Arg(1).TryInst();
    if (!low || !high || low->GetOpcode() != IR::Opcode::ReadConstBuffer ||
        high->GetOpcode() != IR::Opcode::ReadConstBuffer || low->Arg(0) != high->Arg(0)) return {};
    const auto* high_index = high->Arg(1).TryInst();
    if (!high_index || high_index->GetOpcode() != IR::Opcode::IAdd32) return {};
    const unsigned one = Imm(high_index->Arg(0)) ? 0u : 1u;
    if (!Imm(high_index->Arg(one)) || high_index->Arg(one).U32() != 1 ||
        high_index->Arg(1-one) != low->Arg(1)) return {};
    const auto* offset = low->Arg(1).TryInst();
    if (!offset || offset->GetOpcode() != IR::Opcode::ShiftRightLogical32 ||
        !Imm(offset->Arg(1)) || offset->Arg(1).U32() != 2) return {};
    auto bytes = offset->Arg(0);
    const auto* add = bytes.TryInst();
    if (add && add->GetOpcode() == IR::Opcode::IAdd32) {
        const unsigned literal = Imm(add->Arg(0)) ? 0u : 1u;
        if (!Imm(add->Arg(literal))) return {};
        out.source.pointer_offset = add->Arg(literal).U32();
        bytes = add->Arg(1-literal);
    }
    const auto* mul = bytes.TryInst();
    if (!mul || mul->GetOpcode() != IR::Opcode::IMul32) return {};
    const unsigned literal = Imm(mul->Arg(0)) ? 0u : 1u;
    if (!Imm(mul->Arg(literal))) return {};
    out.source.record_bytes = mul->Arg(literal).U32();
    out.selector = mul->Arg(1-literal).TryInst();
    if (!out.selector || out.source.record_bytes < 8 || (out.source.record_bytes & 3) ||
        (out.source.pointer_offset & 3) || out.source.pointer_offset > out.source.record_bytes - 8) return {};
    // Do not replace wraparound scalar-buffer addressing by a non-wrapping ID.
    const auto max_index = UpperBound(mul->Arg(1-literal));
    if (!max_index || u64(*max_index) * out.source.record_bytes + out.source.pointer_offset + 7 > 0xffffffffu) return {};
    const auto* buffer = low->Arg(0).TryInst();
    if (!buffer || buffer->GetOpcode() != IR::Opcode::CompositeConstructU32x4) return {};
    std::optional<u32> root;
    for (u32 i = 0; i < 4; ++i) {
        const auto* dword = buffer->Arg(i).TryInst();
        if (!dword || dword->GetOpcode() != IR::Opcode::ReadConst || !Imm(dword->Arg(1))) return {};
        const auto r = UniformRawSample::Root(dword->Arg(0));
        if (!r || (root && root != r)) return {};
        root = r;
        if (i == 0) out.source.descriptor_word = dword->Arg(1).U32();
        if (u64(out.source.descriptor_word) + i != dword->Arg(1).U32()) return {};
    }
    out.source.root = *root;
    return out;
}
} // namespace Shader::Optimization::IndirectSamplePlan
