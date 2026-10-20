// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "shader_recompiler/backend/spirv/emit_spirv_instructions.h"
#include "shader_recompiler/backend/spirv/spirv_emit_context.h"
#include "shader_recompiler/ir/debug_print.h"
#include "shader_recompiler/ir/microinstruction.h"
#include "shader_recompiler/vertex_output_feedback.h"

namespace Shader::Backend::SPIRV {

void EmitPrologue(EmitContext& ctx) {
    if (ctx.hw_stage == HwStage::Fragment) {
        ctx.DefineAmdPerVertexAttribs();
    }
    if (ctx.info.loads.Get(IR::Attribute::WorkgroupIndex)) {
        ctx.DefineWorkgroupIndex();
    }
    ctx.DefineBufferProperties();
}

void ConvertDepthMode(EmitContext& ctx) {
    const Id type{ctx.F32[1]};
    const Id position{ctx.OpLoad(ctx.F32[4], ctx.output_position)};
    const Id z{ctx.OpCompositeExtract(type, position, 2u)};
    const Id w{ctx.OpCompositeExtract(type, position, 3u)};
    const Id screen_depth{ctx.OpFMul(type, ctx.OpFAdd(type, z, w), ctx.Constant(type, 0.5f))};
    const Id vector{ctx.OpCompositeInsert(ctx.F32[4], screen_depth, position, 2u)};
    ctx.OpStore(ctx.output_position, vector);
}

void ConvertPositionToClipSpace(EmitContext& ctx) {
    ASSERT_MSG(!ctx.info.stores.GetAny(IR::Attribute::ViewportIndex),
               "Multi-viewport with shader clip space conversion not yet implemented.");

    const Id type{ctx.F32[1]};
    Id position{ctx.OpLoad(ctx.F32[4], ctx.output_position)};
    const Id x{ctx.OpCompositeExtract(type, position, 0u)};
    const Id y{ctx.OpCompositeExtract(type, position, 1u)};
    const Id z{ctx.OpCompositeExtract(type, position, 2u)};
    const Id w{ctx.OpCompositeExtract(type, position, 3u)};
    const Id xoffset_ptr{ctx.OpAccessChain(ctx.TypePointer(spv::StorageClass::PushConstant, type),
                                           ctx.push_data_block,
                                           ctx.ConstU32(PushData::XOffsetIndex))};
    const Id xoffset{ctx.OpLoad(type, xoffset_ptr)};
    const Id yoffset_ptr{ctx.OpAccessChain(ctx.TypePointer(spv::StorageClass::PushConstant, type),
                                           ctx.push_data_block,
                                           ctx.ConstU32(PushData::YOffsetIndex))};
    const Id yoffset{ctx.OpLoad(type, yoffset_ptr)};
    const Id xscale_ptr{ctx.OpAccessChain(ctx.TypePointer(spv::StorageClass::PushConstant, type),
                                          ctx.push_data_block,
                                          ctx.ConstU32(PushData::XScaleIndex))};
    const Id xscale{ctx.OpLoad(type, xscale_ptr)};
    const Id yscale_ptr{ctx.OpAccessChain(ctx.TypePointer(spv::StorageClass::PushConstant, type),
                                          ctx.push_data_block,
                                          ctx.ConstU32(PushData::YScaleIndex))};
    const Id yscale{ctx.OpLoad(type, yscale_ptr)};
    const Id vport_w =
        ctx.Constant(type, float(std::min<u32>(ctx.profile.max_viewport_width / 2, 8_KB)));
    const Id wnd_x = ctx.OpFAdd(type, ctx.OpFMul(type, x, xscale), xoffset);
    const Id ndc_x = ctx.OpFSub(type, ctx.OpFDiv(type, wnd_x, vport_w), ctx.Constant(type, 1.f));
    const Id vport_h =
        ctx.Constant(type, float(std::min<u32>(ctx.profile.max_viewport_height / 2, 8_KB)));
    const Id wnd_y = ctx.OpFAdd(type, ctx.OpFMul(type, y, yscale), yoffset);
    const Id ndc_y = ctx.OpFSub(type, ctx.OpFDiv(type, wnd_y, vport_h), ctx.Constant(type, 1.f));
    const Id vector{ctx.OpCompositeConstruct(ctx.F32[4], std::array<Id, 4>({ndc_x, ndc_y, z, w}))};
    ctx.OpStore(ctx.output_position, vector);
}

static void RecordVertexOutput(EmitContext& ctx) {
    const auto found = std::ranges::find(ctx.info.buffers, BufferType::VertexOutputFeedback,
                                         &BufferResource::buffer_type);
    if (found == ctx.info.buffers.end() || !Sirit::ValidId(ctx.output_position)) {
        return;
    }
    const u32 index = std::distance(ctx.info.buffers.begin(), found);
    const auto [buffer, pointer_type] = ctx.buffers[index].Alias(EmitContext::PointerType::U32);
    const auto pointer = [&](Id word) {
        return ctx.OpAccessChain(pointer_type, buffer, ctx.u32_zero_value, word);
    };
    const Id active = ctx.OpINotEqual(ctx.U1[1],
        ctx.OpLoad(ctx.U32[1], pointer(ctx.u32_zero_value)), ctx.u32_zero_value);
    const Id record = ctx.OpLabel();
    const Id end = ctx.OpLabel();
    ctx.OpSelectionMerge(end, spv::SelectionControlMask::MaskNone);
    ctx.OpBranchConditional(active, record, end);
    ctx.AddLabel(record);
    const Id slot = ctx.OpAtomicIAdd(ctx.U32[1], pointer(ctx.ConstU32(1u)),
        ctx.ConstU32(u32(spv::Scope::Device)), ctx.u32_zero_value, ctx.ConstU32(1u));
    const Id store = ctx.OpLabel();
    const Id merge = ctx.OpLabel();
    const Id in_bounds = ctx.OpULessThan(ctx.U1[1], slot,
        ctx.ConstU32(Shader::Diagnostics::VertexOutputSamples));
    ctx.OpSelectionMerge(merge, spv::SelectionControlMask::MaskNone);
    ctx.OpBranchConditional(in_bounds, store, merge);
    ctx.AddLabel(store);
    const Id base = ctx.OpIAdd(ctx.U32[1], ctx.ConstU32(2u),
        ctx.OpIMul(ctx.U32[1], slot, ctx.ConstU32(6u)));
    const auto save = [&](u32 word, Id value) {
        ctx.OpStore(pointer(ctx.OpIAdd(ctx.U32[1], base, ctx.ConstU32(word))), value);
    };
    save(0, ctx.OpLoad(ctx.U32[1], ctx.vertex_index));
    save(1, ctx.OpLoad(ctx.U32[1], ctx.instance_id));
    const Id position = ctx.OpLoad(ctx.F32[4], ctx.output_position);
    for (u32 component = 0; component < 4; ++component) {
        save(2 + component, ctx.OpBitcast(ctx.U32[1],
            ctx.OpCompositeExtract(ctx.F32[1], position, component)));
    }
    ctx.OpBranch(merge);
    ctx.AddLabel(merge);
    ctx.OpBranch(end);
    ctx.AddLabel(end);
}

void EmitEpilogue(EmitContext& ctx) {
    if (ctx.hw_stage == HwStage::Vertex &&
        ctx.runtime_info.hw.vs.emulate_depth_negative_one_to_one) {
        ConvertDepthMode(ctx);
    }
    if (ctx.hw_stage == HwStage::Vertex && ctx.runtime_info.hw.vs.clip_disable) {
        ConvertPositionToClipSpace(ctx);
    }
    RecordVertexOutput(ctx);
}

void EmitDiscard(EmitContext& ctx) {
    ctx.OpDemoteToHelperInvocationEXT();
}

void EmitDiscardCond(EmitContext& ctx, Id condition) {
    const Id kill_label{ctx.OpLabel()};
    const Id merge_label{ctx.OpLabel()};
    ctx.OpSelectionMerge(merge_label, spv::SelectionControlMask::MaskNone);
    ctx.OpBranchConditional(condition, kill_label, merge_label);
    ctx.AddLabel(kill_label);
    ctx.OpDemoteToHelperInvocationEXT();
    ctx.OpBranch(merge_label);
    ctx.AddLabel(merge_label);
}

void EmitEmitVertex(EmitContext& ctx) {
    ctx.OpEmitVertex();
}

void EmitEmitPrimitive(EmitContext& ctx) {
    ctx.OpEndPrimitive();
}

void EmitEmitVertex(EmitContext& ctx, const IR::Value& stream) {
    UNREACHABLE_MSG("Geometry streams");
}

void EmitEndPrimitive(EmitContext& ctx, const IR::Value& stream) {
    UNREACHABLE_MSG("Geometry streams");
}

void EmitDebugPrint(EmitContext& ctx, IR::Inst* inst, Id fmt, Id arg0, Id arg1, Id arg2, Id arg3) {
    IR::DebugPrintFlags flags = inst->Flags<IR::DebugPrintFlags>();
    std::array<Id, IR::DEBUGPRINT_NUM_FORMAT_ARGS> fmt_args = {arg0, arg1, arg2, arg3};
    auto fmt_args_span = std::span<Id>(fmt_args.begin(), fmt_args.begin() + flags.num_args);
    ctx.OpDebugPrintf(fmt, fmt_args_span);
}

Id EmitMemtime(EmitContext& ctx) {
    if (ctx.profile.supports_shader_subgroup_clock) {
        return ctx.OpReadClockKHR(ctx.U64, ctx.ConstU32(std::to_underlying(spv::Scope::Subgroup)));
    } else {
        return ctx.Constant(ctx.U64, 1U);
    }
}

} // namespace Shader::Backend::SPIRV
