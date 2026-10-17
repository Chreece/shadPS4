// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstdint>
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
// Some games select a genuine T# table entry with a finite GPU expression.
// Keep all side effects of the original GPU selector (Phi, attribute or scalar
// buffer read) on the device. We only enumerate values when the IR itself
// PROVES the complete set, never from host-observed values or a table prefix.
//
// A two-way SelectU32 explicitly exposes both candidate IDs. For AND with
// literal 1 or 3, integer arithmetic proves all possible indices without
// evaluating the source (which may be divergent or loaded from guest RAM).
// Four candidates are the maximum; larger masks need a proper bindless heap.
enum class IndexedImageChoiceKind : std::uint8_t {
    None, SelectTwo, MaskTwo, MaskFour,
};

struct IndexedImageChoices {
    std::array<std::uint32_t, 4> ids{};
    std::uint32_t count{};
    IndexedImageChoiceKind kind{IndexedImageChoiceKind::None};
};

template <typename Inst>
[[nodiscard]] IndexedImageChoices AnalyzeFiniteIndexedImageChoices(const Inst* selector) {
    if (!selector) return {};
    using Shader::IR::Opcode;
    using Shader::IR::Type;
    if (selector->GetOpcode() == Opcode::SelectU32) {
        const auto left = selector->Arg(1);
        const auto right = selector->Arg(2);
        if (!left.IsImmediate() || !right.IsImmediate() ||
            left.Type() != Type::U32 || right.Type() != Type::U32) return {};
        return {{left.U32(), right.U32()}, 2u, IndexedImageChoiceKind::SelectTwo};
    }
    if (selector->GetOpcode() != Opcode::BitwiseAnd32) return {};
    const auto left = selector->Arg(0);
    const auto right = selector->Arg(1);
    if (left.IsImmediate() == right.IsImmediate()) return {};
    const auto mask = left.IsImmediate() ? left : right;
    const auto source = left.IsImmediate() ? right : left;
    if (mask.Type() != Type::U32 || !source.TryInst()) return {};
    if (mask.U32() == 1u) {
        return {{0u, 1u}, 2u, IndexedImageChoiceKind::MaskTwo};
    }
    if (mask.U32() == 3u) {
        return {{0u, 1u, 2u, 3u}, 4u, IndexedImageChoiceKind::MaskFour};
    }
    return {};
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
