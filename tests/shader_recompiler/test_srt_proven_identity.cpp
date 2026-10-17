// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cstdint>
#include <cstdio>
#include "shader_recompiler/ir/passes/srt_uniformity.h"
#include "shader_recompiler/ir/passes/srt_host_barrier.h"
#include "shader_recompiler/ir/passes/srt_proven_identity.h"

using Shader::IR::Opcode;
using Shader::IR::Type;
using Shader::Optimization::ResolveProvenSrtIdentity;
using Shader::Optimization::IsHostUniformSrtOffset;
using Shader::Optimization::FindSrtHostBarrier;
using Kind=Shader::Optimization::SrtHostBarrierKind;

struct MockInst;
struct MockValue {
    const MockInst* inst{};
    Shader::IR::Type kind{Shader::IR::Type::U32};
    bool immediate{};
    std::uint32_t bits{};
    bool IsImmediate() const {return immediate;}
    Shader::IR::Type Type() const {return kind;}
    const MockInst* TryInst() const {return inst;}
    std::uint32_t U32() const {return bits;}
    static MockValue Imm(std::uint32_t x=0) {return {nullptr,Type::U32,true,x};}
    static MockValue FloatImm() {return {nullptr,Type::F32,true,0};}
    static MockValue Ref(const MockInst& node) {return {&node,Type::U32,false,0};}
};
struct MockInst {
    Opcode opcode{};
    std::array<MockValue,4> args{};
    std::size_t count{};
    Opcode GetOpcode() const {return opcode;}
    MockValue Arg(std::size_t i) const {return args.at(i);}
    std::size_t NumArgs() const {return count;}
};
int main() {
    const MockInst sgpr{Opcode::GetUserData,{},0};
    const MockInst sgpr2{Opcode::GetUserData,{},0};
    const MockInst as_float{Opcode::BitCastF32U32,{MockValue::Ref(sgpr)},1};
    const MockInst as_bits{Opcode::BitCastU32F32,{MockValue::Ref(as_float)},1};
    const MockInst float_value{Opcode::FAdd32,{MockValue::FloatImm(),MockValue::FloatImm()},2};
    const MockInst bad_cast{Opcode::BitCastU32F32,{MockValue::Ref(float_value)},1};
    const MockInst phi_one{Opcode::Phi,{MockValue::Ref(sgpr)},1};
    const MockInst phi_same_ref{Opcode::Phi,{MockValue::Ref(sgpr),MockValue::Ref(sgpr)},2};
    const MockInst phi_same_imm{Opcode::Phi,{MockValue::Imm(7),MockValue::Imm(7)},2};
    const MockInst phi_mixed_ref{Opcode::Phi,{MockValue::Ref(sgpr),MockValue::Ref(sgpr2)},2};
    const MockInst phi_mixed_imm{Opcode::Phi,{MockValue::Imm(7),MockValue::Imm(9)},2};
    const MockInst phi_mixed_kind{Opcode::Phi,{MockValue::Imm(7),MockValue::Ref(sgpr)},2};
    const MockInst conditional{Opcode::SelectU32,{MockValue::Imm(1),
                                  MockValue::Imm(7),MockValue::Imm(7)},3};
    MockInst phi_self_and_imm{Opcode::Phi,{},2};
    phi_self_and_imm.args[0]=MockValue::Ref(phi_self_and_imm);
    phi_self_and_imm.args[1]=MockValue::Imm(11);
    MockInst phi_only_self{Opcode::Phi,{},1};
    phi_only_self.args[0]=MockValue::Ref(phi_only_self);
    MockInst phiA{Opcode::Phi,{},1},phiB{Opcode::Phi,{},1};
    phiA.args[0]=MockValue::Ref(phiB);
    phiB.args[0]=MockValue::Ref(phiA);
    const MockInst nested_phi{Opcode::Phi,{MockValue::Ref(phi_same_ref)},1};
    const MockInst shift{Opcode::ShiftRightLogical32,
                         {MockValue::Ref(phi_same_ref),MockValue::Imm(2)},2};
    struct Case {const char* label; MockValue val; bool proven;};
    const std::array<Case, 16> cases{{
        {"bitcast-inverse-pair",MockValue::Ref(as_bits),true},
        {"bitcast-arithmetic-not-identity",MockValue::Ref(bad_cast),false},
        {"phi-one-incoming",MockValue::Ref(phi_one),true},
        {"phi-same-source",MockValue::Ref(phi_same_ref),true},
        {"phi-same-immediate",MockValue::Ref(phi_same_imm),true},
        {"phi-different-source",MockValue::Ref(phi_mixed_ref),false},
        {"phi-different-immediate",MockValue::Ref(phi_mixed_imm),false},
        {"phi-immediate-vs-source",MockValue::Ref(phi_mixed_kind),false},
        {"select-unproven",MockValue::Ref(conditional),false},
        {"phi-self-constant",MockValue::Ref(phi_self_and_imm),true},
        {"phi-only-self",MockValue::Ref(phi_only_self),false},
        {"phi-cycle",MockValue::Ref(phiA),false},
        {"nested-invariant-phi",MockValue::Ref(nested_phi),true},
        {"uniform-index-through-phi",MockValue::Ref(shift),false}, // not identity itself
        {"normal-user-data",MockValue::Ref(sgpr),false}, // no identity to strip
        {"immediate",MockValue::Imm(),false},
    }};
    for (const auto& t:cases) {
        const bool actual=ResolveProvenSrtIdentity(t.val).has_value();
        if (actual!=t.proven) {
            std::fprintf(stderr,"SRT_PROVEN_IDENTITY=FAIL %s actual=%d expected=%d\n",
                         t.label,actual,t.proven);
            return 1;
        }
    }
    // Truth table: a resolved identity inherits host-uniformity of its
    // operand. Distinct Phi branches, float arithmetic and Select fail closed.
    if (!IsHostUniformSrtOffset(MockValue::Ref(as_bits)) ||
        !IsHostUniformSrtOffset(MockValue::Ref(phi_same_ref)) ||
        !IsHostUniformSrtOffset(MockValue::Ref(nested_phi)) ||
        !IsHostUniformSrtOffset(MockValue::Ref(shift)) ||
        IsHostUniformSrtOffset(MockValue::Ref(phi_mixed_ref)) ||
        IsHostUniformSrtOffset(MockValue::Ref(phi_mixed_imm)) ||
        IsHostUniformSrtOffset(MockValue::Ref(phi_only_self)) ||
        IsHostUniformSrtOffset(MockValue::Ref(bad_cast)) ||
        IsHostUniformSrtOffset(MockValue::Ref(conditional)) ||
        IsHostUniformSrtOffset(MockValue::Ref(phiA))) return 2;
    if (FindSrtHostBarrier(MockValue::Ref(as_bits)).kind!=Kind::None ||
        FindSrtHostBarrier(MockValue::Ref(phi_same_ref)).kind!=Kind::None ||
        FindSrtHostBarrier(MockValue::Ref(phi_mixed_imm)).kind!=Kind::UnsupportedOpcode ||
        FindSrtHostBarrier(MockValue::Ref(bad_cast)).kind!=Kind::UnsupportedOpcode)
        return 3;
    std::puts("SRT_PROVEN_IDENTITY=PASS identities=16 bitcast=EXACT phi=INVARIANT_ONLY divergent=REJECTED");
    return 0;
}
