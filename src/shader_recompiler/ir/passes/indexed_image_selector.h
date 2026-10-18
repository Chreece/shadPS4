// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstdint>
#include <type_traits>
#include "shader_recompiler/ir/opcodes.h"

namespace Shader::Optimization {
struct IndexedSharpWord {
    const void* selector{};
    std::uint32_t addend{};
    bool valid{};
};

template<typename Value>
[[nodiscard]] IndexedSharpWord AnalyzeIndexedSharpWord(Value index,unsigned depth=0) {
    if(depth>16)return {};
    if(index.IsImmediate()) {
        if(index.Type()!=IR::Type::U32)return {};
        return {nullptr,index.U32(),true};
    }
    const auto* const inst=index.TryInst();
    if(!inst)return {};
    switch(inst->GetOpcode()){
    case IR::Opcode::IAdd32: {
        const auto a=inst->Arg(0),b=inst->Arg(1);
        if(a.IsImmediate()!=b.IsImmediate()){
            const auto immediate=a.IsImmediate()?a:b;
            const auto remaining=a.IsImmediate()?b:a;
            if(immediate.Type()!=IR::Type::U32)return {};
            auto base=AnalyzeIndexedSharpWord(remaining,depth+1);
            if(base.valid)base.addend+=immediate.U32();
            return base;
        }
        break;
    }
    case IR::Opcode::ISub32: {
        const auto b=inst->Arg(1);
        if(b.IsImmediate()&&b.Type()==IR::Type::U32){
            auto base=AnalyzeIndexedSharpWord(inst->Arg(0),depth+1);
            if(base.valid)base.addend-=b.U32();
            return base;
        }
        break;
    }
    default:break;
    }
    // Any other SSA expression remains a GPU-run selector. This helper
    // NEVER attempts to evaluate Phi, GetAttribute or GPU buffer reads.
    return {inst,0,true};
}

[[nodiscard]] constexpr bool AreContiguousIndexedSharpWords(
    const std::array<IndexedSharpWord,8>& words) noexcept {
    const void* selector=words[0].selector;
    if(!selector || !words[0].valid)return false;
    for(std::size_t i=1;i<words.size();++i){
        if(!words[i].valid || words[i].selector!=selector ||
           words[i].addend!=static_cast<std::uint32_t>(words[0].addend+i))
            return false;
    }
    return true;
}
// Some PS4 materials store an eight-dword T# inside each array-of-structs
// record: ((gpu_record_id * record_stride_bytes) + byte_offset) >> 2.
// This decoder preserves the ORIGINAL GPU SSA producer (including Phi,
// per-thread attributes and scalar loads). It only removes proven literal
// arithmetic, and never substitutes a CPU-evaluated texture ID.
struct StridedIndexedSharpWord {
    const void* gpu_record_id{};
    std::uint32_t record_stride_bytes{};
    std::uint32_t offset_dwords{};
    bool valid{};
};

template <typename Value>
[[nodiscard]] StridedIndexedSharpWord AnalyzeStridedIndexedSharpWord(Value index) {
    const auto normalized = AnalyzeIndexedSharpWord(index);
    using Inst = std::remove_cv_t<std::remove_pointer_t<decltype(index.TryInst())>>;
    const auto* const shift = static_cast<const Inst*>(normalized.selector);
    if (!normalized.valid || !shift ||
        shift->GetOpcode() != IR::Opcode::ShiftRightLogical32 ||
        !shift->Arg(1).IsImmediate() ||
        shift->Arg(1).Type() != IR::Type::U32 ||
        shift->Arg(1).U32() != 2u) return {};

    const auto* const add = shift->Arg(0).TryInst();
    if (!add || add->GetOpcode() != IR::Opcode::IAdd32) return {};
    const auto a = add->Arg(0);
    const auto b = add->Arg(1);
    if (a.IsImmediate() == b.IsImmediate()) return {};
    const auto literal = a.IsImmediate() ? a : b;
    const auto expression = a.IsImmediate() ? b : a;
    if (literal.Type() != IR::Type::U32) return {};
    const std::uint32_t prefix = literal.U32();
    if (prefix % 4u != 0u) return {};
    const auto* const mul = expression.TryInst();
    if (!mul || mul->GetOpcode() != IR::Opcode::IMul32) return {};
    const auto lhs = mul->Arg(0);
    const auto rhs = mul->Arg(1);
    if (lhs.IsImmediate() == rhs.IsImmediate()) return {};
    const auto stride = lhs.IsImmediate() ? lhs : rhs;
    const auto gpu_id = lhs.IsImmediate() ? rhs : lhs;
    if (stride.Type() != IR::Type::U32 || !gpu_id.TryInst() ||
        stride.U32() < 32u || stride.U32() % 4u ||
        stride.U32() > 65536u) return {};
    const std::uint64_t offset =
        std::uint64_t(prefix / 4u) + normalized.addend;
    // The complete 32-byte T# must lie inside exactly one guest record.
    if (offset > std::uint64_t(stride.U32() / 4u)) return {};
    return {gpu_id.TryInst(),stride.U32(),
            static_cast<std::uint32_t>(offset),true};
}

template <std::size_t N>
[[nodiscard]] constexpr bool AreContiguousStridedSharpWords(
    const std::array<StridedIndexedSharpWord,N>& words) noexcept {
    static_assert(N == 4 || N == 8);
    const auto& first = words[0];
    if (!first.valid || !first.gpu_record_id ||
        first.record_stride_bytes < 32u) return false;
    for (std::size_t i=0;i<words.size();++i) {
        const auto& cur=words[i];
        if (!cur.valid || cur.gpu_record_id != first.gpu_record_id ||
            cur.record_stride_bytes != first.record_stride_bytes ||
            std::uint64_t(cur.offset_dwords) !=
                std::uint64_t(first.offset_dwords) + i) return false;
    }
    return (std::uint64_t(first.offset_dwords) + N) <=
           first.record_stride_bytes / 4u;
}

// Flat guest texture-table words are also sometimes loaded as individual
// scalar-buffer reads with the byte offset applied *before* the >> 2.
// The selector can be lane-dependent (e.g. BitCastU32F32(GetAttribute)):
// the GPU must retain that exact source. Only recover an array index when
// every word proves (id * 32 + 4 * word) >> 2, possibly with literal
// dword suffixes. This is not a host texture descriptor evaluation.
//
// The multiplication and addition are 32-bit guest operations: the effective
// index is id modulo 2^27. A 27-bit mask on the GPU selector preserves
// that overflow, including out-of-range null-slot selection.
template <typename Value>
[[nodiscard]] StridedIndexedSharpWord AnalyzePackedGuestTsharpWord(Value index) {
    const auto normalized = AnalyzeIndexedSharpWord(index);
    using Inst = std::remove_cv_t<std::remove_pointer_t<decltype(index.TryInst())>>;
    if (!normalized.valid) return {};
    const auto* const shift = static_cast<const Inst*>(normalized.selector);
    if (!shift || shift->GetOpcode() != IR::Opcode::ShiftRightLogical32 ||
        !shift->Arg(1).IsImmediate() || shift->Arg(1).Type() != IR::Type::U32 ||
        shift->Arg(1).U32() != 2u) return {};

    auto term = shift->Arg(0);
    std::uint32_t prefix_bytes = 0;
    if (const auto* const add = term.TryInst();
        add && add->GetOpcode() == IR::Opcode::IAdd32) {
        const auto lhs = add->Arg(0);
        const auto rhs = add->Arg(1);
        if (lhs.IsImmediate() == rhs.IsImmediate()) return {};
        const auto literal = lhs.IsImmediate() ? lhs : rhs;
        if (literal.Type() != IR::Type::U32) return {};
        prefix_bytes = literal.U32();
        term = lhs.IsImmediate() ? rhs : lhs;
    }
    if (prefix_bytes >= 32u || (prefix_bytes & 3u)) return {};

    const auto* const scaled = term.TryInst();
    if (!scaled) return {};
    Value guest_id{};
    if (scaled->GetOpcode() == IR::Opcode::ShiftLeftLogical32 &&
        scaled->Arg(1).IsImmediate() &&
        scaled->Arg(1).Type() == IR::Type::U32 &&
        scaled->Arg(1).U32() == 5u) {
        guest_id = scaled->Arg(0);
    } else if (scaled->GetOpcode() == IR::Opcode::IMul32 &&
               (scaled->Arg(0).IsImmediate() != scaled->Arg(1).IsImmediate())) {
        const auto lhs=scaled->Arg(0), rhs=scaled->Arg(1);
        const auto literal = lhs.IsImmediate() ? lhs : rhs;
        if (literal.Type() != IR::Type::U32 || literal.U32() != 32u) return {};
        guest_id = lhs.IsImmediate() ? rhs : lhs;
    } else {
        return {};
    }
    // Do not replace guest attributes/Phis with CPU-evaluated constants.
    const auto* const producer=guest_id.TryInst();
    if (!producer || guest_id.IsImmediate()) return {};
    const std::uint64_t word = std::uint64_t(prefix_bytes / 4u) + normalized.addend;
    if (word >= 8u) return {};
    return {producer, 32u, static_cast<std::uint32_t>(word), true};
}

[[nodiscard]] constexpr std::uint32_t GuestTsharpIdWrapped27(std::uint32_t id) noexcept {
    return id & 0x07ffffffu;
}

// GCN constructs a texture-table dword index with (texture_id << 5) >> 2.
// The shift-left is 32-bit and wraps before the unsigned shift right.
// Recover the exact GPU texture-id producer but NEVER evaluate its value.
struct IndexedTsharpSelector {
    const void* source{};
    std::uint32_t descriptor_stride_words{1};
    bool normalized{};
};

template <typename Inst>
[[nodiscard]] IndexedTsharpSelector IdentifyTsharpSelector(const Inst* index) {
    if (!index) return {};
    if (index->GetOpcode()!=IR::Opcode::ShiftRightLogical32 ||
        !index->Arg(1).IsImmediate() ||
        index->Arg(1).Type()!=IR::Type::U32 ||
        index->Arg(1).U32()!=2u) {
        return {index,1u,false};
    }
    const auto* const shl=index->Arg(0).TryInst();
    if (!shl || shl->GetOpcode()!=IR::Opcode::ShiftLeftLogical32 ||
        !shl->Arg(1).IsImmediate() ||
        shl->Arg(1).Type()!=IR::Type::U32 ||
        shl->Arg(1).U32()!=5u) {
        return {index,1u,false};
    }
    const auto* const gpu_id=shl->Arg(0).TryInst();
    if (!gpu_id) return {index,1u,false};
    return {gpu_id,8u,true};
}

[[nodiscard]] constexpr std::uint32_t GcnTextureDwordIndex(
    std::uint32_t id_or_offset, std::uint32_t addend,
    std::uint32_t descriptor_stride_words) noexcept {
    return descriptor_stride_words==8u
        ? ((id_or_offset<<5u)>>2u)+addend
        : id_or_offset+addend;
}
} // namespace Shader::Optimization
