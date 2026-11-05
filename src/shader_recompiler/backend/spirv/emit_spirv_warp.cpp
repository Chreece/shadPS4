// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "shader_recompiler/backend/spirv/emit_spirv_instructions.h"
#include "shader_recompiler/backend/spirv/spirv_emit_context.h"

namespace Shader::Backend::SPIRV {

Id SubgroupScope(EmitContext& ctx) {
    return ctx.ConstU32(static_cast<u32>(spv::Scope::Subgroup));
}

static Id ReduceActiveLanes(EmitContext& ctx, Id value, bool minimum) {
    const Id scope = SubgroupScope(ctx);
    const Id active = ctx.OpGroupNonUniformBallot(ctx.U32[4], scope, ctx.true_value);
    const Id first = ctx.OpGroupNonUniformBallotFindLSB(ctx.U32[1], scope, active);
    const Id identity = ctx.ConstU32(minimum ? ~0u : 0u);
    Id result = identity;
    for (u32 lane = 0; lane < 64; ++lane) {
        const Id mask = ctx.OpCompositeExtract(ctx.U32[1], active, lane / 32);
        const Id bit = ctx.OpBitwiseAnd(ctx.U32[1], mask, ctx.ConstU32(1u << (lane % 32)));
        const Id present = ctx.OpINotEqual(ctx.U1[1], bit, ctx.u32_zero_value);
        const Id source = ctx.OpSelect(ctx.U32[1], present, ctx.ConstU32(lane), first);
        const Id shuffled = ctx.OpGroupNonUniformShuffle(ctx.U32[1], scope, value, source);
        const Id operand = ctx.OpSelect(ctx.U32[1], present, shuffled, identity);
        result = minimum ? ctx.OpUMin(ctx.U32[1], result, operand)
                         : ctx.OpBitwiseOr(ctx.U32[1], result, operand);
    }
    return result;
}

Id EmitGroupReduceUMin(EmitContext& ctx, Id value) {
    return ReduceActiveLanes(ctx, value, true);
}

Id EmitGroupReduceOr(EmitContext& ctx, Id value) {
    return ReduceActiveLanes(ctx, value, false);
}

Id EmitWarpId(EmitContext& ctx) {
    UNREACHABLE();
}

Id EmitLaneId(EmitContext& ctx) {
    return ctx.OpLoad(ctx.U32[1], ctx.subgroup_local_invocation_id);
}

static Id ShuffleActiveLanes(EmitContext& ctx, Id value, Id index) {
    const Id scope = SubgroupScope(ctx);
    const Id active = ctx.OpGroupNonUniformBallot(ctx.U32[4], scope, ctx.true_value);
    const Id word = ctx.OpShiftRightLogical(ctx.U32[1], index, ctx.ConstU32(5u));
    const Id bit = ctx.OpBitwiseAnd(ctx.U32[1], index, ctx.ConstU32(31u));
    const Id mask = ctx.OpVectorExtractDynamic(ctx.U32[1], active, word);
    const Id enabled = ctx.OpBitFieldUExtract(ctx.U32[1], mask, bit, ctx.ConstU32(1u));
    const Id present = ctx.OpINotEqual(ctx.U1[1], enabled, ctx.u32_zero_value);
    const Id source = ctx.OpSelect(ctx.U32[1], present, index, EmitLaneId(ctx));
    const Id shuffled = ctx.OpGroupNonUniformShuffle(ctx.U32[1], scope, value, source);
    return ctx.OpSelect(ctx.U32[1], present, shuffled, ctx.u32_zero_value);
}

Id EmitQuadBroadcast(EmitContext& ctx, Id value, Id index) {
    if (ctx.info.hw_stage == HwStage::Compute) {
        const Id quad_base = ctx.OpBitwiseAnd(ctx.U32[1], EmitLaneId(ctx), ctx.ConstU32(~3u));
        return ShuffleActiveLanes(ctx, value, ctx.OpBitwiseOr(ctx.U32[1], quad_base, index));
    }
    return ctx.OpGroupNonUniformQuadBroadcast(ctx.U32[1], SubgroupScope(ctx), value, index);
}

Id EmitReadFirstLane(EmitContext& ctx, Id value, Id exec) {
    const Id scope = SubgroupScope(ctx);
    if (exec.value == ctx.true_value.value) {
        return ctx.OpGroupNonUniformBroadcastFirst(ctx.U32[1], scope, value);
    }
    const Id active = ctx.OpGroupNonUniformBallot(ctx.U32[4], scope, exec);
    const Id zero = ctx.ConstantComposite(ctx.U32[4], ctx.u32_zero_value, ctx.u32_zero_value,
                                          ctx.u32_zero_value, ctx.u32_zero_value);
    const Id empty = ctx.OpAll(ctx.U1[1], ctx.OpIEqual(ctx.U1[4], active, zero));
    const Id low = ctx.OpCompositeExtract(ctx.U32[1], active, 0);
    const Id first_word = ctx.OpSelect(ctx.U32[1], empty, ctx.ConstU32(1u), low);
    const Id mask = ctx.OpCompositeInsert(ctx.U32[4], first_word, active, 0);
    const Id first = ctx.OpGroupNonUniformBallotFindLSB(ctx.U32[1], scope, mask);
    return ctx.OpGroupNonUniformShuffle(ctx.U32[1], scope, value, first);
}

Id EmitShuffle(EmitContext& ctx, Id value, Id index) {
    if (ctx.info.hw_stage == HwStage::Compute) {
        return ShuffleActiveLanes(ctx, value, index);
    }
    return ctx.OpGroupNonUniformShuffle(ctx.U32[1], SubgroupScope(ctx), value, index);
}

Id EmitShuffleXor(EmitContext& ctx, Id value, Id mask) {
    if (ctx.info.hw_stage == HwStage::Compute) {
        const Id index = ctx.OpBitwiseXor(ctx.U32[1], EmitLaneId(ctx), mask);
        return ShuffleActiveLanes(ctx, value, index);
    }
    return ctx.OpGroupNonUniformShuffleXor(ctx.U32[1], SubgroupScope(ctx), value, mask);
}

Id EmitReadLane(EmitContext& ctx, Id value, Id lane) {
    return ctx.OpGroupNonUniformBroadcast(ctx.U32[1], SubgroupScope(ctx), value, lane);
}

Id EmitWriteLane(EmitContext& ctx, Id value, Id write_value, u32 lane) {
    return ctx.u32_zero_value;
}

Id EmitBallot(EmitContext& ctx, Id bit) {
    const Id ballot{ctx.OpGroupNonUniformBallot(ctx.U32[4], SubgroupScope(ctx), bit)};
    return ctx.OpBitcast(ctx.U64, ctx.OpVectorShuffle(ctx.U32[2], ballot, ballot, 0, 1));
}

Id EmitBallotFindLsb(EmitContext& ctx, Id mask) {
    const Id value{ctx.OpCompositeConstruct(ctx.U32[4], ctx.OpBitcast(ctx.U32[2], mask),
                                            ctx.u32_zero_value, ctx.u32_zero_value)};
    return ctx.OpGroupNonUniformBallotFindLSB(ctx.U32[1], SubgroupScope(ctx), value);
}

Id EmitInverseBallot(EmitContext& ctx, Id mask) {
    const Id value{ctx.OpCompositeConstruct(ctx.U32[4], ctx.OpBitcast(ctx.U32[2], mask),
                                            ctx.u32_zero_value, ctx.u32_zero_value)};
    return ctx.OpGroupNonUniformInverseBallot(ctx.U1[1], SubgroupScope(ctx), value);
}

Id EmitGroupAny(EmitContext& ctx, Id bit) {
    return ctx.OpGroupNonUniformAny(ctx.U1[1], SubgroupScope(ctx), bit);
}

} // namespace Shader::Backend::SPIRV
