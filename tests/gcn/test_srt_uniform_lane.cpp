// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <gtest/gtest.h>

#include "shader_recompiler/ir/passes/srt_uniform_lane.h"

using namespace Shader;

TEST(SrtUniformFirstLane, ImmediateDwordIsUniformButMissingValueIsNot) {
    Optimization::SrtGvnTable gvn;
    EXPECT_TRUE(Optimization::IsHostUniformSrtValue(IR::Value{37u}, gvn));
    EXPECT_FALSE(Optimization::IsHostUniformSrtValue(IR::Value{}, gvn));
}

TEST(SrtUniformFirstLane, ProgramUserDataSgprIsUniform) {
    IR::Inst sgpr{IR::Opcode::GetUserData, 0};
    sgpr.SetArg(0, IR::Value{IR::ScalarReg::S12});
    Optimization::SrtGvnTable gvn;
    EXPECT_TRUE(Optimization::IsHostUniformSrtValue(IR::Value{&sgpr}, gvn));
}

TEST(SrtUniformFirstLane, PureScalarArithmeticOfUserDataIsUniform) {
    IR::Inst sgpr{IR::Opcode::GetUserData, 0};
    sgpr.SetArg(0, IR::Value{IR::ScalarReg::S4});
    IR::Inst add{IR::Opcode::IAdd32, 0};
    add.SetArg(0, IR::Value{&sgpr});
    add.SetArg(1, IR::Value{12u});
    IR::Inst lsb{IR::Opcode::FindILsb32, 0};
    lsb.SetArg(0, IR::Value{&add});
    Optimization::SrtGvnTable gvn;
    EXPECT_TRUE(Optimization::IsHostUniformSrtValue(IR::Value{&lsb}, gvn));
}

TEST(SrtUniformFirstLane, ReadFirstLaneOfProvenUniformInputCanBeUnwrapped) {
    IR::Inst sgpr{IR::Opcode::GetUserData, 0};
    sgpr.SetArg(0, IR::Value{IR::ScalarReg::S18});
    IR::Inst first_lane{IR::Opcode::ReadFirstLane, 0};
    first_lane.SetArg(0, IR::Value{&sgpr});
    Optimization::SrtGvnTable gvn;
    EXPECT_TRUE(Optimization::IsHostUniformSrtValue(IR::Value{&first_lane}, gvn));
}

TEST(SrtUniformFirstLane, VertexAttributesCannotBeFlattenedOnHost) {
    IR::Inst attribute{IR::Opcode::GetAttributeU32, 0};
    IR::Inst first_lane{IR::Opcode::ReadFirstLane, 0};
    first_lane.SetArg(0, IR::Value{&attribute});
    Optimization::SrtGvnTable gvn;
    EXPECT_FALSE(Optimization::IsHostUniformSrtValue(IR::Value{&attribute}, gvn));
    EXPECT_FALSE(Optimization::IsHostUniformSrtValue(IR::Value{&first_lane}, gvn));
}

TEST(SrtUniformFirstLane, DifferentIncomingPhiValuesRemainGpuDependent) {
    IR::Inst phi{IR::Opcode::Phi, 0};
    phi.AddPhiOperand(nullptr, IR::Value{11u});
    phi.AddPhiOperand(nullptr, IR::Value{12u});
    IR::Inst first_lane{IR::Opcode::ReadFirstLane, 0};
    first_lane.SetArg(0, IR::Value{&phi});
    Optimization::SrtGvnTable gvn;
    EXPECT_FALSE(Optimization::IsHostUniformSrtValue(IR::Value{&first_lane}, gvn));
}

TEST(SrtUniformFirstLane, InvariantPhiOfSameScalarInputIsSafe) {
    IR::Inst phi{IR::Opcode::Phi, 0};
    phi.AddPhiOperand(nullptr, IR::Value{11u});
    phi.AddPhiOperand(nullptr, IR::Value{11u});
    Optimization::SrtGvnTable gvn;
    EXPECT_TRUE(Optimization::IsHostUniformSrtValue(IR::Value{&phi}, gvn));
}

TEST(SrtUniformFirstLane, UnflattenedConstantReadsDoNotProveUniformity) {
    IR::Inst read{IR::Opcode::ReadConst, 0};
    Optimization::SrtGvnTable gvn;
    EXPECT_FALSE(Optimization::IsHostUniformSrtValue(IR::Value{&read}, gvn));
    read.SetFlags(u16{5u});
    EXPECT_TRUE(Optimization::IsHostUniformSrtValue(IR::Value{&read}, gvn));
}

TEST(SrtUniformFirstLane, UnflattenedSharpSourcesRemainGpuDependent) {
    IR::Inst read{IR::Opcode::ReadConstBuffer, 0};
    Optimization::SrtGvnTable gvn;
    EXPECT_FALSE(Optimization::IsHostUniformSrtValue(IR::Value{&read}, gvn));
    IR::BufferInstInfo info{};
    info.flatbuf_off_dw.Assign(7u);
    read.SetFlags(info);
    EXPECT_TRUE(Optimization::IsHostUniformSrtValue(IR::Value{&read}, gvn));
}
