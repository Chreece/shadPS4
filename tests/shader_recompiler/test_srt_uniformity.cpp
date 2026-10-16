// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Host-only conservative uniformity proof tests for SRT lane selects.
#include <array>
#include <cstddef>
#include <cstdio>

#include "shader_recompiler/ir/passes/srt_uniformity.h"

using Shader::IR::Opcode;
using Shader::IR::Type;
using Shader::Optimization::IsHostUniformSrtOffset;

struct MockInst;
struct MockValue {
    const MockInst* inst{};
    Shader::IR::Type kind{Shader::IR::Type::U32};
    bool immediate{};
    bool IsImmediate() const { return immediate; }
    
    Shader::IR::Type Type() const { return kind; }
    const MockInst* TryInst() const { return inst; }
    static MockValue Imm(Shader::IR::Type value_type = Shader::IR::Type::U32) {
        return {nullptr, value_type, true};
    }
    static MockValue Ref(const MockInst& node) {
        return {&node, Shader::IR::Type::U32, false};
    }
};

struct MockInst {
    Opcode opcode{};
    std::array<MockValue, 3> args{};
    size_t count{};
    Opcode GetOpcode() const { return opcode; }
    MockValue Arg(size_t i) const { return args[i]; }
    size_t NumArgs() const { return count; }
};

int main() {
    const MockInst uniform{Opcode::GetUserData, {}, 0};
    const MockInst pc_constant{Opcode::GetPcLo, {MockValue::Imm()}, 1};
    const MockInst pc_varying{Opcode::GetPcLo, {MockValue::Ref(uniform)}, 1};
    const MockInst varying{Opcode::GetAttribute, {}, 0};
    const MockInst phi{Opcode::Phi, {MockValue::Ref(uniform)}, 1};
    const MockInst uniform_add{Opcode::IAdd32, {MockValue::Ref(uniform), MockValue::Imm()}, 2};
    const MockInst varying_add{Opcode::IAdd32, {MockValue::Ref(uniform), MockValue::Ref(varying)}, 2};
    const MockInst uniform_signed_shift{Opcode::ShiftRightArithmetic32,
        {MockValue::Ref(uniform_add), MockValue::Imm()}, 2};
    const MockInst varying_signed_shift{Opcode::ShiftRightArithmetic32,
        {MockValue::Ref(varying_add), MockValue::Imm()}, 2};
    const MockInst first_uniform{Opcode::ReadFirstLane, {MockValue::Ref(uniform_add)}, 1};
    const MockInst first_varying{Opcode::ReadFirstLane, {MockValue::Ref(varying_add)}, 1};
    const MockInst first_phi{Opcode::ReadFirstLane, {MockValue::Ref(phi)}, 1};
    const MockInst indexed_lane_uniform{Opcode::ReadLane,
        {MockValue::Ref(uniform), MockValue::Ref(varying)}, 2};
    const MockInst indexed_lane_varying{Opcode::ReadLane,
        {MockValue::Ref(varying), MockValue::Imm()}, 2};
    const MockInst find_lsb{Opcode::FindILsb32, {MockValue::Ref(uniform)}, 1};
    const MockInst first_lsb{Opcode::ReadFirstLane, {MockValue::Ref(find_lsb)}, 1};
    const MockInst descriptor{Opcode::CompositeConstructU32x2,
        {MockValue::Ref(uniform), MockValue::Ref(uniform)}, 2};
    const MockInst read_const{Opcode::ReadConstBuffer,
        {MockValue::Ref(descriptor), MockValue::Imm()}, 2};
    const MockInst pc_pointer{Opcode::CompositeConstructU32x2,
        {MockValue::Ref(pc_constant), MockValue::Imm()}, 2};
    const MockInst pc_read{Opcode::ReadConst,
        {MockValue::Ref(pc_pointer), MockValue::Imm()}, 2};
    const MockInst pc_read_varying{Opcode::ReadConst,
        {MockValue::Ref(pc_pointer), MockValue::Ref(varying)}, 2};
    const MockInst first_const{Opcode::ReadFirstLane, {MockValue::Ref(read_const)}, 1};
    const MockInst nonuniform_const{Opcode::ReadConstBuffer,
        {MockValue::Ref(descriptor), MockValue::Ref(varying)}, 2};

    MockInst cycle{Opcode::IAdd32, {}, 2};
    cycle.args[0] = MockValue::Ref(cycle);
    cycle.args[1] = MockValue::Imm();

    struct Case {
        const char* name;
        MockValue value;
        bool expected;
    };
    const std::array<Case, 21> cases{{
        {"u32-constant", MockValue::Imm(), true},
        {"float-constant", MockValue::Imm(Type::F32), false},
        {"user-data", MockValue::Ref(uniform), true},
        {"pc-immediate-constant", MockValue::Ref(pc_constant), true},
        {"pc-dynamic-operand-rejected", MockValue::Ref(pc_varying), false},
        {"pc-based-readconst", MockValue::Ref(pc_read), true},
        {"pc-based-readconst-varying-index", MockValue::Ref(pc_read_varying), false},
        {"varying-attribute", MockValue::Ref(varying), false},
        {"wave-uniform-firstlane", MockValue::Ref(first_uniform), true},
        {"uniform-arithmetic-right-shift", MockValue::Ref(uniform_signed_shift), true},
        {"varying-arithmetic-right-shift", MockValue::Ref(varying_signed_shift), false},
        {"wave-varying-firstlane", MockValue::Ref(first_varying), false},
        {"Phi-must-not-guess", MockValue::Ref(first_phi), false},
        {"lane-index-independent-uniform-source", MockValue::Ref(indexed_lane_uniform), true},
        {"varying-readlane", MockValue::Ref(indexed_lane_varying), false},
        {"uniform-FindILsb32", MockValue::Ref(first_lsb), true},
        {"uniform-descriptor-read", MockValue::Ref(first_const), true},
        {"varying-descriptor-index", MockValue::Ref(nonuniform_const), false},
        {"SSA-cycle", MockValue::Ref(cycle), false},
        {"unsupported-immediate", MockValue::Imm(Type::U64), false},
        {"empty-SSA", MockValue{}, false},
    }};
    for (const auto& test : cases) {
        const bool result = IsHostUniformSrtOffset(test.value);
        if (result != test.expected) {
            std::fprintf(stderr, "SRT_UNIFORMITY=FAIL %s got=%d expected=%d\n",
                         test.name, result, test.expected);
            return 1;
        }
    }
    std::puts("SRT_UNIFORMITY=PASS cases=21 diverging=REJECTED phi=REJECTED");
    return 0;
}
