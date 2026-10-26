// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <gtest/gtest.h>

#include "shader_recompiler/ir/microinstruction.h"
#include "shader_recompiler/ir/passes/srt_phi.h"
#include "shader_recompiler/ir/passes/srt_find_lsb.h"
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

TEST(SrtFindILsb32, ZeroMatchesSpirvUndefinedBitIndexSentinel) {
    EXPECT_EQ(Optimization::FindSrtLeastSignificantBit(0u), 0xffffffffu);
}

TEST(SrtFindILsb32, AllOneHotInputsReturnExactlyBitIndex) {
    for (u32 bit = 0; bit < 32; ++bit) {
        EXPECT_EQ(Optimization::FindSrtLeastSignificantBit(1u << bit), bit);
    }
}

TEST(SrtFindILsb32, MixedBitsChooseLeastSignificantSetBit) {
    EXPECT_EQ(Optimization::FindSrtLeastSignificantBit(0x80008000u), 15u);
    EXPECT_EQ(Optimization::FindSrtLeastSignificantBit(0xffffffffu), 0u);
    EXPECT_EQ(Optimization::FindSrtLeastSignificantBit(0x80000001u), 0u);
}

TEST(SrtFindILsb32, IdenticalHostExpressionsShareValueNumber) {
    IR::Inst first{IR::Opcode::FindILsb32, 0};
    IR::Inst second{IR::Opcode::FindILsb32, 0};
    first.SetArg(0, IR::Value{0x80000000u});
    second.SetArg(0, IR::Value{0x80000000u});
    Optimization::SrtGvnTable gvn;
    EXPECT_EQ(gvn.GetValueNumber(&first), gvn.GetValueNumber(&second));
}

TEST(SrtFindILsb32, DifferentSourcesDoNotAliasAtValueNumbering) {
    IR::Inst first{IR::Opcode::FindILsb32, 0};
    IR::Inst second{IR::Opcode::FindILsb32, 0};
    first.SetArg(0, IR::Value{0x4u});
    second.SetArg(0, IR::Value{0x8u});
    Optimization::SrtGvnTable gvn;
    EXPECT_NE(gvn.GetValueNumber(&first), gvn.GetValueNumber(&second));
}

TEST(SrtFindILsb32, ControlFlowDependentPhiMustRemainOnGpu) {
    IR::Inst phi{IR::Opcode::Phi, 0};
    phi.AddPhiOperand(nullptr, IR::Value{0x1u});
    phi.AddPhiOperand(nullptr, IR::Value{0x2u});
    Optimization::SrtGvnTable gvn;
    EXPECT_TRUE(Optimization::ResolveInvariantSrtPhi(IR::Value{&phi}, gvn).IsEmpty());
}

#ifdef ARCH_X86_64
TEST(SrtFindILsb32, ExecutableJitMatchesScalarReference) {
    // Exercise the EXACT emitter used by FlattenExtendedUserdataPass.
    // The input register and return register follow the native host ABI.
    Xbyak::CodeGenerator host(256);
#ifdef _WIN32
    host.mov(Xbyak::util::r10d, Xbyak::util::ecx);
#else
    host.mov(Xbyak::util::r10d, Xbyak::util::edi);
#endif
    Optimization::EmitSrtFindLeastSignificantBit(host, Xbyak::util::r10d);
    host.mov(Xbyak::util::eax, Xbyak::util::r10d);
    host.ret();
    host.ready();
    const auto compute = host.getCode<u32(*)(u32)>();

    EXPECT_EQ(compute(0u), 0xffffffffu);
    for (u32 bit = 0; bit < 32; ++bit) {
        EXPECT_EQ(compute(1u << bit), bit);
    }
    u32 value = 0x4d595df4u;
    for (u32 i = 0; i < 2048; ++i) {
        value ^= value << 13u;
        value ^= value >> 17u;
        value ^= value << 5u;
        EXPECT_EQ(compute(value), Optimization::FindSrtLeastSignificantBit(value));
    }
}
#endif
