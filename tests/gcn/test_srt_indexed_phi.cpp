// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <memory>
#include <gtest/gtest.h>

#include "shader_recompiler/ir/microinstruction.h"
#include "shader_recompiler/ir/passes/image_phi_preflight.h"
#include "shader_recompiler/ir/passes/indexed_image_selector.h"

using namespace Shader;

TEST(SrtIndexedPhiDescriptors, DynamicPhiOffsetNormalizesEightContiguousWords) {
    IR::Inst phi{IR::Opcode::Phi, 0};
    phi.SetFlags(IR::Type::U32);
    phi.AddPhiOperand(nullptr, IR::Value{24u});
    phi.AddPhiOperand(nullptr, IR::Value{80u});

    std::array<std::unique_ptr<IR::Inst>, 8> adds{};
    std::array<Optimization::IndexedSharpWord, 8> words{};
    for (u32 index = 0; index < 8; ++index) {
        adds[index] = std::make_unique<IR::Inst>(IR::Opcode::IAdd32, 0);
        adds[index]->SetArg(0, IR::Value{&phi});
        adds[index]->SetArg(1, IR::Value{index});
        words[index] = Optimization::AnalyzeIndexedSharpWord(IR::Value{adds[index].get()});
        EXPECT_EQ(words[index].selector, &phi);
        EXPECT_TRUE(words[index].valid);
        EXPECT_EQ(words[index].addend, index);
    }
    EXPECT_TRUE(Optimization::AreContiguousIndexedSharpWords(words));
    // Exact two-way descriptor choice, not a host guess about which path runs.
    EXPECT_EQ(phi.Arg(0).U32() + words[7].addend, 31u);
    EXPECT_EQ(phi.Arg(1).U32() + words[7].addend, 87u);
}

TEST(SrtIndexedPhiDescriptors, UnrelatedPhiSelectorsCannotShareOneIndexedSharp) {
    IR::Inst first{IR::Opcode::Phi, 0};
    IR::Inst other{IR::Opcode::Phi, 0};
    first.SetFlags(IR::Type::U32);
    other.SetFlags(IR::Type::U32);
    std::array<Optimization::IndexedSharpWord, 8> words{};
    for (u32 i = 0; i < 8; ++i) {
        words[i] = {i == 7u ? static_cast<const void*>(&other)
                             : static_cast<const void*>(&first), i, true};
    }
    EXPECT_FALSE(Optimization::AreContiguousIndexedSharpWords(words));
}

TEST(SrtIndexedPhiDescriptors, NonContiguousOffsetsAreNotFabricated) {
    IR::Inst phi{IR::Opcode::Phi, 0};
    phi.SetFlags(IR::Type::U32);
    std::array<Optimization::IndexedSharpWord, 8> words{};
    for (u32 i = 0; i < 8; ++i) {
        words[i] = {&phi, i == 5 ? i + 2 : i, true};
    }
    EXPECT_FALSE(Optimization::AreContiguousIndexedSharpWords(words));
}

TEST(SrtIndexedPhiDescriptors, GpuOperationCannotBePretendedToBeStaticPhi) {
    IR::Inst gpu_expression{IR::Opcode::FindILsb32, 0};
    gpu_expression.SetArg(0, IR::Value{0x80u});
    const auto word = Optimization::AnalyzeIndexedSharpWord(IR::Value{&gpu_expression});
    ASSERT_TRUE(word.valid);
    EXPECT_EQ(word.selector, &gpu_expression);
    EXPECT_NE(gpu_expression.GetOpcode(), IR::Opcode::Phi);
}

TEST(SrtIndexedPhiDescriptors, FinitePhiBudgetBoundsExtraShaderSamples) {
    EXPECT_TRUE(Optimization::CanSplitSharpImagePhi(2, true, true, 1));
    EXPECT_TRUE(Optimization::CanSplitSharpImagePhi(3, true, true, 2));
    EXPECT_FALSE(Optimization::CanSplitSharpImagePhi(3, true, true, 1));
    EXPECT_FALSE(Optimization::CanSplitSharpImagePhi(4, true, true, 32));
    EXPECT_FALSE(Optimization::CanSplitSharpImagePhi(2, false, true, 4));
    EXPECT_FALSE(Optimization::CanSplitSharpImagePhi(2, true, false, 4));
}
