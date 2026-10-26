// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <gtest/gtest.h>

#include "shader_recompiler/ir/microinstruction.h"
#include "shader_recompiler/ir/passes/srt_phi.h"
#include "shader_recompiler/ir/passes/srt_dma_policy.h"
#include "video_core/buffer_cache/bda_address_space.h"

using namespace Shader;

TEST(SrtInvariantPhi, IdenticalIncomingConstants) {
    IR::Inst phi{IR::Opcode::Phi, 0};
    phi.AddPhiOperand(nullptr, IR::Value{19u});
    phi.AddPhiOperand(nullptr, IR::Value{19u});
    Optimization::SrtGvnTable gvn;
    const auto resolved = Optimization::ResolveInvariantSrtPhi(IR::Value{&phi}, gvn);
    ASSERT_TRUE(resolved.IsImmediate());
    EXPECT_EQ(resolved.U32(), 19u);
}

TEST(SrtInvariantPhi, DifferentIncomingConstantsAreDynamic) {
    IR::Inst phi{IR::Opcode::Phi, 0};
    phi.AddPhiOperand(nullptr, IR::Value{19u});
    phi.AddPhiOperand(nullptr, IR::Value{20u});
    Optimization::SrtGvnTable gvn;
    EXPECT_TRUE(Optimization::ResolveInvariantSrtPhi(IR::Value{&phi}, gvn).IsEmpty());
}

TEST(SrtInvariantPhi, StructurallyIdenticalExpressions) {
    IR::Inst a{IR::Opcode::IAdd32, 0};
    IR::Inst b{IR::Opcode::IAdd32, 0};
    a.SetArg(0, IR::Value{5u});
    a.SetArg(1, IR::Value{7u});
    b.SetArg(0, IR::Value{5u});
    b.SetArg(1, IR::Value{7u});
    IR::Inst phi{IR::Opcode::Phi, 0};
    phi.AddPhiOperand(nullptr, IR::Value{&a});
    phi.AddPhiOperand(nullptr, IR::Value{&b});
    Optimization::SrtGvnTable gvn;
    EXPECT_EQ(Optimization::ResolveInvariantSrtPhi(IR::Value{&phi}, gvn).TryInst(), &a);
}

TEST(SrtInvariantPhi, NestedAndSelfReferencedInvariant) {
    IR::Inst inner{IR::Opcode::Phi, 0};
    inner.AddPhiOperand(nullptr, IR::Value{31u});
    inner.AddPhiOperand(nullptr, IR::Value{31u});
    IR::Inst outer{IR::Opcode::Phi, 0};
    outer.AddPhiOperand(nullptr, IR::Value{&inner});
    outer.AddPhiOperand(nullptr, IR::Value{&outer});
    Optimization::SrtGvnTable gvn;
    const auto resolved = Optimization::ResolveInvariantSrtPhi(IR::Value{&outer}, gvn);
    ASSERT_TRUE(resolved.IsImmediate());
    EXPECT_EQ(resolved.U32(), 31u);
    outer.ClearArgs(); // Detach self-reference before destruction.
}

TEST(SrtInvariantPhi, LoopInductionCannotBeFlattenedOnHost) {
    IR::Inst phi{IR::Opcode::Phi, 0};
    IR::Inst increment{IR::Opcode::IAdd32, 0};
    increment.SetArg(0, IR::Value{&phi});
    increment.SetArg(1, IR::Value{1u});
    phi.AddPhiOperand(nullptr, IR::Value{0u});
    phi.AddPhiOperand(nullptr, IR::Value{&increment});
    Optimization::SrtGvnTable gvn;
    EXPECT_TRUE(Optimization::ResolveInvariantSrtPhi(IR::Value{&phi}, gvn).IsEmpty());
    phi.ClearArgs();
    increment.ClearArgs(); // Break reference cycle before destruction.
}

TEST(SrtInvariantPhi, NestedPhiCycleCannotBeFlattenedOnHost) {
    IR::Inst first{IR::Opcode::Phi, 0};
    IR::Inst second{IR::Opcode::Phi, 0};
    first.AddPhiOperand(nullptr, IR::Value{&second});
    second.AddPhiOperand(nullptr, IR::Value{&first});
    second.AddPhiOperand(nullptr, IR::Value{10u});
    Optimization::SrtGvnTable gvn;
    EXPECT_TRUE(Optimization::ResolveInvariantSrtPhi(IR::Value{&first}, gvn).IsEmpty());
    first.ClearArgs();
    second.ClearArgs();
}

TEST(SrtDynamicDma, OnDemandGpuReadForUnflattenedResource) {
    constexpr Optimization::SrtDmaPolicy policy{true, true};
    static_assert(policy.NeedsGpuRead());
    EXPECT_TRUE(policy.NeedsGpuRead());
    EXPECT_FALSE(policy.IsUnsupported());
}

TEST(SrtDynamicDma, FlattenedReadNeverRequiresGpuAddress) {
    constexpr Optimization::SrtDmaPolicy policy{false, true};
    static_assert(!policy.NeedsGpuRead());
    EXPECT_FALSE(policy.NeedsGpuRead());
    EXPECT_FALSE(policy.IsUnsupported());
}

TEST(SrtDynamicDma, MissingBdaIsDiagnosableNotSilentlyEnabled) {
    constexpr Optimization::SrtDmaPolicy policy{true, false};
    static_assert(policy.IsUnsupported());
    EXPECT_FALSE(policy.NeedsGpuRead());
    EXPECT_TRUE(policy.IsUnsupported());
}

TEST(SrtDynamicDma, DisabledBdaWithoutDynamicLoadsNeedsNoResources) {
    constexpr Optimization::SrtDmaPolicy policy{false, false};
    static_assert(!policy.IsUnsupported());
    EXPECT_FALSE(policy.NeedsGpuRead());
    EXPECT_FALSE(policy.IsUnsupported());
}

TEST(SrtDynamicDma, GuestPageTableBoundsIncludeLastValidDword) {
    using VideoCore::GuestGpuAddressLimit;
    EXPECT_TRUE(VideoCore::IsGuestGpuDwordInRange(0));
    EXPECT_TRUE(VideoCore::IsGuestGpuDwordInRange(GuestGpuAddressLimit - 4));
    EXPECT_FALSE(VideoCore::IsGuestGpuDwordInRange(GuestGpuAddressLimit - 3));
    EXPECT_FALSE(VideoCore::IsGuestGpuDwordInRange(GuestGpuAddressLimit));
}

TEST(SrtDynamicDma, LargeGuestAddressNeverWrapsIntoValidPage) {
    using VideoCore::GuestGpuAddressLimit;
    static_assert(!VideoCore::IsGuestGpuDwordInRange(1ULL << 48));
    EXPECT_FALSE(VideoCore::IsGuestGpuDwordInRange(1ULL << 48));
    EXPECT_FALSE(VideoCore::IsGuestGpuDwordInRange(0xFFFFFFFFFFFFULL));
    EXPECT_FALSE(VideoCore::IsGuestGpuDwordInRange(GuestGpuAddressLimit + 1));
}
