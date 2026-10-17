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
} // namespace Shader::Optimization
