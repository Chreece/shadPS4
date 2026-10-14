// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <gtest/gtest.h>

#include "common/object_pool.h"
#include "shader_recompiler/ir/basic_block.h"
#include "shader_recompiler/ir/ir_emitter.h"
#include "shader_recompiler/ir/passes/resource_pass.h"

namespace {

using namespace Shader;

Optimization::SharpReference MakeDynamicSharp(IR::IREmitter& ir, IR::U32 index, u32 stride_bytes,
                                              u32 field_offset_bytes, u32 num_dwords) {
    Optimization::SharpReference sharp{};
    sharp.num_dwords = num_dwords;

    const auto t0 = ir.GetUserData(IR::ScalarReg::S0);
    const auto t1 = ir.GetUserData(IR::ScalarReg::S1);
    const auto t2 = ir.GetUserData(IR::ScalarReg::S2);
    const auto t3 = ir.GetUserData(IR::ScalarReg::S3);
    const auto table = ir.CompositeConstruct(t0, t1, t2, t3);

    const IR::U32 record_bytes{ir.IMul(index, ir.Imm32(stride_bytes))};
    const IR::U32 field_bytes{ir.IAdd(record_bytes, ir.Imm32(field_offset_bytes))};
    const IR::U32 first_dword{ir.ShiftRightLogical(field_bytes, ir.Imm32(2))};

    for (u32 i = 0; i < num_dwords; ++i) {
        const IR::U32 offset =
            i == 0 ? first_dword : IR::U32{ir.IAdd(first_dword, ir.Imm32(i))};
        sharp.dwords[i] = ir.ReadConstBuffer(table, offset, IR::BufferInstInfo{});
    }
    return sharp;
}

TEST(ResourceDiscovery, DetectsRuntimeIndexedImageDescriptorTable) {
    Common::ObjectPool<IR::Inst> inst_pool;
    IR::Block block{inst_pool};
    IR::IREmitter ir{block};

    const IR::U32 index = ir.GetVectorReg<IR::U32>(IR::VectorReg::V0);
    const auto sharp = MakeDynamicSharp(ir, index, 340, 96, 8);
    const auto table = Optimization::DetectDynamicSharpTable(sharp);

    ASSERT_TRUE(table.Valid());
    EXPECT_EQ(table.index, index);
    EXPECT_EQ(table.record_stride_bytes, 340u);
    EXPECT_EQ(table.field_offset_bytes, 96u);
    EXPECT_EQ(table.table_dwords[0].Inst()->GetOpcode(), IR::Opcode::GetUserData);
    EXPECT_EQ(table.table_dwords[0].Inst()->Arg(0).ScalarReg(), IR::ScalarReg::S0);
    EXPECT_EQ(table.table_dwords[3].Inst()->Arg(0).ScalarReg(), IR::ScalarReg::S3);
}

TEST(ResourceDiscovery, DetectsFourDwordDynamicSharp) {
    Common::ObjectPool<IR::Inst> inst_pool;
    IR::Block block{inst_pool};
    IR::IREmitter ir{block};

    const IR::U32 index = ir.GetVectorReg<IR::U32>(IR::VectorReg::V7);
    const auto sharp = MakeDynamicSharp(ir, index, 64, 16, 4);
    const auto table = Optimization::DetectDynamicSharpTable(sharp);

    ASSERT_TRUE(table.Valid());
    EXPECT_EQ(table.index, index);
    EXPECT_EQ(table.record_stride_bytes, 64u);
    EXPECT_EQ(table.field_offset_bytes, 16u);
}

TEST(ResourceDiscovery, RejectsNonContiguousDescriptorDwords) {
    Common::ObjectPool<IR::Inst> inst_pool;
    IR::Block block{inst_pool};
    IR::IREmitter ir{block};

    const IR::U32 index = ir.GetVectorReg<IR::U32>(IR::VectorReg::V0);
    auto sharp = MakeDynamicSharp(ir, index, 340, 96, 8);

    const auto* original = sharp.dwords[7].Inst();
    const auto table = original->Arg(0);
    const auto bad_offset = IR::U32{ir.IAdd(original->Arg(1), ir.Imm32(1))};
    sharp.dwords[7] = ir.ReadConstBuffer(table, bad_offset, IR::BufferInstInfo{});

    EXPECT_FALSE(Optimization::DetectDynamicSharpTable(sharp).Valid());
}

TEST(ResourceDiscovery, RejectsDescriptorCrossingRecordBoundary) {
    Common::ObjectPool<IR::Inst> inst_pool;
    IR::Block block{inst_pool};
    IR::IREmitter ir{block};

    const IR::U32 index = ir.GetVectorReg<IR::U32>(IR::VectorReg::V0);
    const auto sharp = MakeDynamicSharp(ir, index, 64, 40, 8);

    EXPECT_FALSE(Optimization::DetectDynamicSharpTable(sharp).Valid());
}

TEST(ResourceDiscovery, RejectsFixedSharp) {
    Optimization::SharpReference sharp{};
    sharp.num_dwords = 8;
    for (u32 i = 0; i < sharp.num_dwords; ++i) {
        sharp.dwords[i] = IR::Value{i};
    }
    EXPECT_FALSE(Optimization::DetectDynamicSharpTable(sharp).Valid());
}

} // namespace
