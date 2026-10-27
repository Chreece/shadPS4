// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>

#include <gtest/gtest.h>

#include "shader_recompiler/frontend/safe_scalar_exec_scope_split.h"

using namespace Shader::Gcn;

namespace {

GcnInst MaskedVectorOp(OperandField destination = OperandField::VectorGPR) {
    GcnInst inst{};
    inst.opcode = Opcode::V_MOVRELD_B32;
    inst.category = InstCategory::VectorALU;
    inst.dst_count = 1;
    inst.dst[0].field = destination;
    return inst;
}

GcnInst ScalarOp() {
    GcnInst inst{};
    inst.opcode = Opcode::S_ANDN2_B64;
    inst.category = InstCategory::ScalarALU;
    inst.src_count = 2;
    inst.dst_count = 1;
    inst.src[0].field = OperandField::ScalarGPR;
    inst.src[1].field = OperandField::ScalarGPR;
    inst.dst[0].field = OperandField::ScalarGPR;
    return inst;
}

} // namespace

TEST(ScalarExecDivergence, WaveScalarOperationSeparatesTwoMaskedVectorOps) {
    const std::array code{MaskedVectorOp(), ScalarOp(), MaskedVectorOp()};
    EXPECT_EQ(FindSafeScalarExecScopeSplit(code, 0, 2), 1);
    EXPECT_TRUE(IgnoresExecMask(code[1]));
    EXPECT_FALSE(IgnoresExecMask(code[0]));
}

TEST(ScalarExecDivergence, MaintainsVectorWorkBeforeAndAfterScalar) {
    const std::array code{MaskedVectorOp(), MaskedVectorOp(), ScalarOp(),
                          MaskedVectorOp(), MaskedVectorOp()};
    EXPECT_EQ(FindSafeScalarExecScopeSplit(code, 0, 4), 2);
}

TEST(ScalarExecDivergence, CannotSplitWhenNoMaskedWorkAfterScalar) {
    const std::array code{MaskedVectorOp(), ScalarOp()};
    EXPECT_EQ(FindSafeScalarExecScopeSplit(code, 0, 1), -1);
}

TEST(ScalarExecDivergence, RejectsPriorScalarValueProducedByMaskedVector) {
    const std::array code{MaskedVectorOp(OperandField::ScalarGPR),
                          ScalarOp(), MaskedVectorOp()};
    EXPECT_EQ(FindSafeScalarExecScopeSplit(code, 0, 2), -1);
}

TEST(ScalarExecDivergence, RejectsPriorVccValueProducedByMaskedVector) {
    const std::array code{MaskedVectorOp(OperandField::VccLo),
                          ScalarOp(), MaskedVectorOp()};
    EXPECT_EQ(FindSafeScalarExecScopeSplit(code, 0, 2), -1);
}

TEST(ScalarExecDivergence, RejectsScalarOpThatWritesExec) {
    auto scalar = ScalarOp();
    scalar.dst[0].field = OperandField::ExecLo;
    const std::array code{MaskedVectorOp(), scalar, MaskedVectorOp()};
    EXPECT_EQ(FindSafeScalarExecScopeSplit(code, 0, 2), -1);
}

TEST(ScalarExecDivergence, RejectsScalarOpReadingExecOrVcc) {
    auto scalar = ScalarOp();
    const std::array operands{OperandField::ExecLo, OperandField::ExecHi,
                              OperandField::ExecZ, OperandField::VccLo,
                              OperandField::VccHi, OperandField::VccZ};
    for (const OperandField field : operands) {
        scalar.src[0].field = field;
        const std::array code{MaskedVectorOp(), scalar, MaskedVectorOp()};
        EXPECT_EQ(FindSafeScalarExecScopeSplit(code, 0, 2), -1);
    }
}

TEST(ScalarExecDivergence, RejectsEarlierReadFirstLaneScalarDependency) {
    GcnInst read{};
    read.opcode = Opcode::V_READFIRSTLANE_B32;
    read.category = InstCategory::VectorALU;
    read.dst_count = 1;
    read.dst[0].field = OperandField::ScalarGPR;
    const std::array code{MaskedVectorOp(), read, ScalarOp(), MaskedVectorOp()};
    ASSERT_TRUE(IgnoresExecMask(read));
    EXPECT_EQ(FindSafeScalarExecScopeSplit(code, 0, 3), -1);
}

TEST(ScalarExecDivergence, RejectsEarlierScalarMemoryLoadDependency) {
    GcnInst read{};
    read.opcode = Opcode::S_LOAD_DWORD;
    read.category = InstCategory::ScalarMemory;
    const std::array code{MaskedVectorOp(), read, ScalarOp(), MaskedVectorOp()};
    EXPECT_EQ(FindSafeScalarExecScopeSplit(code, 0, 3), -1);
}

TEST(ScalarExecDivergence, RefusesInvalidOrEmptyBoundaries) {
    const std::array code{MaskedVectorOp(), ScalarOp(), MaskedVectorOp()};
    EXPECT_EQ(FindSafeScalarExecScopeSplit(code, -1, 2), -1);
    EXPECT_EQ(FindSafeScalarExecScopeSplit(code, 0, 0), -1);
    EXPECT_EQ(FindSafeScalarExecScopeSplit(code, 0, 3), -1);
}
