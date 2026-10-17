// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cstdio>
#include "shader_recompiler/ir/passes/srt_host_barrier.h"
using Shader::IR::Opcode;
using Shader::IR::Type;
using Shader::Optimization::FindSrtHostBarrier;
using Shader::Optimization::IsHostUniformSrtOffset;
using Kind=Shader::Optimization::SrtHostBarrierKind;
struct MockInst;
struct MockValue {
    const MockInst* inst{};
    Shader::IR::Type kind{Shader::IR::Type::U32};
    bool immediate{};
    bool IsImmediate() const {return immediate;}
    Shader::IR::Type Type() const {return kind;}
    const MockInst* TryInst() const {return inst;}
    static MockValue Imm(Shader::IR::Type type=Shader::IR::Type::U32){return {nullptr,type,true};}
    static MockValue Ref(const MockInst& node){return {&node,Type::U32,false};}
};
struct MockInst {
    Opcode opcode{};
    std::array<MockValue,4> args{};
    std::size_t count{};
    Opcode GetOpcode() const {return opcode;}
    MockValue Arg(std::size_t i) const {return args.at(i);}
    std::size_t NumArgs() const {return count;}
};
int main(){
    const MockInst user{Opcode::GetUserData,{},0};
    const MockInst phi{Opcode::Phi,{MockValue::Ref(user)},1};
    const MockInst selector{Opcode::SelectU32,{MockValue::Ref(user),
                               MockValue::Imm(),MockValue::Imm()},3};
    const MockInst attrib{Opcode::GetAttribute,{},0};
    const MockInst arithmetic{Opcode::ShiftRightLogical32,
                                {MockValue::Ref(phi),MockValue::Imm()},2};
    const MockInst pointer{Opcode::CompositeConstructU32x4,
        {MockValue::Ref(user),MockValue::Ref(user),MockValue::Ref(user),
         MockValue::Ref(attrib)},4};
    const MockInst scalar{Opcode::ReadConstBuffer,
                          {MockValue::Ref(pointer),MockValue::Imm()},2};
    const MockInst scalarBad{Opcode::ReadConstBuffer,
                          {MockValue::Ref(pointer),MockValue::Ref(arithmetic)},2};
    const MockInst lowBad{Opcode::CompositeConstructU32x4,
        {MockValue::Ref(selector),MockValue::Ref(user),MockValue::Ref(user),
         MockValue::Imm()},4};
    const MockInst lowRead{Opcode::ReadConstBuffer,
        {MockValue::Ref(lowBad),MockValue::Imm()},2};
    const MockInst recordBad{Opcode::CompositeConstructU32x4,
        {MockValue::Ref(user),MockValue::Ref(user),MockValue::Ref(phi),
         MockValue::Imm()},4};
    const MockInst recordRead{Opcode::ReadConstBuffer,
        {MockValue::Ref(recordBad),MockValue::Imm()},2};
    MockInst cycle{Opcode::IAdd32,{},2};
    cycle.args[0]=MockValue::Ref(cycle);
    cycle.args[1]=MockValue::Imm();
    struct Case{const char* label;MockValue value;Kind kind;Opcode opcode;};
    const std::array<Case,10> cases{{
        {"valid-user",MockValue::Ref(user),Kind::None,Opcode::Phi},
        {"valid-buffer-unused-dw3",MockValue::Ref(scalar),Kind::None,Opcode::Phi},
        {"reject-phi-index",MockValue::Ref(scalarBad),Kind::UnsupportedOpcode,Opcode::Phi},
        {"reject-select-pointer",MockValue::Ref(lowRead),Kind::UnsupportedOpcode,Opcode::SelectU32},
        {"reject-phi-records",MockValue::Ref(recordRead),Kind::UnsupportedOpcode,Opcode::Phi},
        {"reject-attrib",MockValue::Ref(attrib),Kind::UnsupportedOpcode,Opcode::GetAttribute},
        {"reject-unsupported-immediate",MockValue::Imm(Type::F32),Kind::InvalidImmediate,Opcode::Phi},
        {"reject-empty",MockValue{},Kind::MissingValue,Opcode::Phi},
        {"reject-cycle",MockValue::Ref(cycle),Kind::RecursionLimit,Opcode::Phi},
        {"valid-constant",MockValue::Imm(),Kind::None,Opcode::Phi},
    }};
    for(const auto& c:cases){
        const auto result=FindSrtHostBarrier(c.value);
        if(result.kind!=c.kind ||
           (result.kind==Kind::UnsupportedOpcode && result.opcode!=c.opcode) ||
           (result.kind==Kind::None)!=IsHostUniformSrtOffset(c.value)){
            std::fprintf(stderr,"SRT_HOST_BARRIER=FAIL %s kind=%d expected=%d\n",
                         c.label,static_cast<int>(result.kind),static_cast<int>(c.kind));
            return 1;
        }
    }
    std::puts("SRT_HOST_BARRIER=PASS cases=10 phi/select/attribute/loop diagnosed");
    return 0;
}
