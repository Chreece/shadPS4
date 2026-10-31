// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <mutex>
#include <span>
#include <type_traits>
#include <utility>
#include <vector>
#include <magic_enum/magic_enum.hpp>

#include "common/assert.h"
#include "common/func_traits.h"
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/backend/spirv/emit_spirv_instructions.h"
#include "shader_recompiler/backend/spirv/spirv_emit_context.h"
#include "shader_recompiler/frontend/ordered_gds_capture_packet.h"
#include "shader_recompiler/frontend/ordered_gds_epoch_auth.h"
#include "shader_recompiler/frontend/ordered_gds_word_layout.h"
#include "shader_recompiler/frontend/translate/translate.h"
#include "shader_recompiler/ir/basic_block.h"
#include "shader_recompiler/ir/program.h"
#include "shader_recompiler/runtime_info.h"

namespace Shader::Backend::SPIRV {
namespace {

static constexpr spv::ExecutionMode GetInputPrimitiveType(AmdGpu::PrimitiveType type) {
    switch (type) {
    case AmdGpu::PrimitiveType::PointList:
        return spv::ExecutionMode::InputPoints;
    case AmdGpu::PrimitiveType::LineList:
    case AmdGpu::PrimitiveType::LineStrip:
        return spv::ExecutionMode::InputLines;
    case AmdGpu::PrimitiveType::TriangleList:
    case AmdGpu::PrimitiveType::TriangleStrip:
    case AmdGpu::PrimitiveType::RectList:
        return spv::ExecutionMode::Triangles;
    case AmdGpu::PrimitiveType::AdjTriangleList:
        return spv::ExecutionMode::InputTrianglesAdjacency;
    case AmdGpu::PrimitiveType::AdjLineList:
        return spv::ExecutionMode::InputLinesAdjacency;
    default:
        UNREACHABLE_MSG("Unknown input primitive type {}", u32(type));
    }
}

static constexpr spv::ExecutionMode GetOutputPrimitiveType(AmdGpu::GsOutputPrimitiveType type) {
    switch (type) {
    case AmdGpu::GsOutputPrimitiveType::PointList:
        return spv::ExecutionMode::OutputVertices;
    case AmdGpu::GsOutputPrimitiveType::LineStrip:
        return spv::ExecutionMode::OutputLineStrip;
    case AmdGpu::GsOutputPrimitiveType::TriangleStrip:
        return spv::ExecutionMode::OutputTriangleStrip;
    default:
        UNREACHABLE_MSG("Unknown output primitive type {}", u32(type));
    }
}

template <auto func, typename... Args>
void SetDefinition(EmitContext& ctx, IR::Inst* inst, Args... args) {
    inst->SetDefinition<Id>(func(ctx, std::forward<Args>(args)...));
}

template <typename ArgType>
ArgType Arg(EmitContext& ctx, const IR::Value& arg) {
    if constexpr (std::is_same_v<ArgType, Id>) {
        return ctx.Def(arg);
    } else if constexpr (std::is_same_v<ArgType, const IR::Value&>) {
        return arg;
    } else if constexpr (std::is_same_v<ArgType, u32>) {
        if (!arg.IsImmediate() && arg.Inst()->GetOpcode() == IR::Opcode::ImageDescriptorHandle) {
            return arg.Inst()->Arg(0).U32();
        }
        return arg.U32();
    } else if constexpr (std::is_same_v<ArgType, u64>) {
        return arg.U64();
    } else if constexpr (std::is_same_v<ArgType, bool>) {
        return arg.U1();
    } else if constexpr (std::is_same_v<ArgType, IR::Attribute>) {
        return arg.Attribute();
    } else if constexpr (std::is_same_v<ArgType, IR::ScalarReg>) {
        return arg.ScalarReg();
    } else if constexpr (std::is_same_v<ArgType, IR::VectorReg>) {
        return arg.VectorReg();
    } else if constexpr (std::is_same_v<ArgType, const char*>) {
        return arg.StringLiteral();
    } else if constexpr (std::is_same_v<ArgType, IR::Patch>) {
        return arg.Patch();
    }
    UNREACHABLE();
}

template <auto func, bool is_first_arg_inst, size_t... I>
void Invoke(EmitContext& ctx, IR::Inst* inst, std::index_sequence<I...>) {
    using Traits = Common::FuncTraits<decltype(func)>;
    if constexpr (std::is_same_v<typename Traits::ReturnType, Id>) {
        if constexpr (is_first_arg_inst) {
            SetDefinition<func>(
                ctx, inst, inst,
                Arg<typename Traits::template ArgType<I + 2>>(ctx, inst->Arg(I))...);
        } else {
            SetDefinition<func>(
                ctx, inst, Arg<typename Traits::template ArgType<I + 1>>(ctx, inst->Arg(I))...);
        }
    } else {
        if constexpr (is_first_arg_inst) {
            func(ctx, inst, Arg<typename Traits::template ArgType<I + 2>>(ctx, inst->Arg(I))...);
        } else {
            func(ctx, Arg<typename Traits::template ArgType<I + 1>>(ctx, inst->Arg(I))...);
        }
    }
}

template <auto func>
void Invoke(EmitContext& ctx, IR::Inst* inst) {
    using Traits = Common::FuncTraits<decltype(func)>;
    static_assert(Traits::NUM_ARGS >= 1, "Insufficient arguments");
    if constexpr (Traits::NUM_ARGS == 1) {
        Invoke<func, false>(ctx, inst, std::make_index_sequence<0>{});
    } else {
        using FirstArgType = typename Traits::template ArgType<1>;
        static constexpr bool is_first_arg_inst = std::is_same_v<FirstArgType, IR::Inst*>;
        using Indices = std::make_index_sequence<Traits::NUM_ARGS - (is_first_arg_inst ? 2 : 1)>;
        Invoke<func, is_first_arg_inst>(ctx, inst, Indices{});
    }
}

void EmitInst(EmitContext& ctx, IR::Inst* inst) {
    switch (inst->GetOpcode()) {
#define OPCODE(name, result_type, ...)                                                             \
    case IR::Opcode::name:                                                                         \
        return Invoke<&Emit##name>(ctx, inst);
#include "shader_recompiler/ir/opcodes.inc"
#undef OPCODE
    }
    UNREACHABLE_MSG("Invalid opcode {}", inst->GetOpcode());
}

Id TypeId(const EmitContext& ctx, IR::Type type) {
    switch (type) {
    case IR::Type::U1:
        return ctx.U1[1];
    case IR::Type::U32:
        return ctx.U32[1];
    case IR::Type::U32x2:
        return ctx.U32[2];
    case IR::Type::U32x3:
        return ctx.U32[3];
    case IR::Type::U32x4:
        return ctx.U32[4];
    case IR::Type::F32:
        return ctx.F32[1];
    case IR::Type::U64:
        return ctx.U64;
    default:
        UNREACHABLE_MSG("Phi node type {}", type);
    }
}

// The original guest shader may terminate at S_ENDPGM without ever
// executing DS_ORDERED_COUNT. Each *completed wave* must contribute a
// canonical bypass record; otherwise host readback mistakes a legitimate
// terminal wave for a lost or stale checkpoint.
//
// Called only from original structured GCN Return paths. Ordered sites
// terminate earlier in EmitOrderedGdsCount and NEVER reach this writer.
// No guest order, counter seed, synthetic resumed register, or replay is
// generated. This source path is still diagnostic until a real producer/
// consumer dispatch scheduler is implemented.
void EmitOrderedGdsTerminalBypass(EmitContext& ctx) {
    namespace Gds = Shader::Gcn::OrderedGds;
    ASSERT_MSG(ctx.ordered_gds_capture_pass &&
                   ctx.hw_stage == HwStage::Compute &&
                   ctx.has_ordered_gds_capture_binding &&
                   ctx.has_ordered_gds_auth_binding &&
                   ctx.info.loads.Get(IR::Attribute::WorkgroupIndex) &&
                   ctx.info.loads.Get(IR::Attribute::LocalInvocationIndex) &&
                   Sirit::ValidId(ctx.workgroup_index_id) &&
                   Sirit::ValidId(ctx.local_invocation_index),
               "GDS terminal bypass needs the original GCN capture identities");
    const auto& local = ctx.runtime_info.hw.cs.workgroup_size;
    const u64 threads = u64(local[0]) * local[1] * local[2];
    ASSERT_MSG(threads >= Gds::GcnWaveLanes && threads <= 1024 &&
                   threads % Gds::GcnWaveLanes == 0 &&
                   ctx.info.shared_memory_scratch_size == 0 &&
                   (ctx.runtime_info.hw.cs.shared_memory_size == 0 ||
                    ctx.info.ordered_gds_persistent_lds),
               "GDS terminal bypass requires complete and preserved guest waves");

    const auto& packet = ctx.buffers.at(ctx.ordered_gds_capture_index);
    const auto& auth = ctx.buffers.at(ctx.ordered_gds_auth_index);
    ASSERT_MSG(packet.buffer_type == BufferType::OrderedGdsCapture &&
                   auth.buffer_type == BufferType::OrderedGdsEpochAuth,
               "GDS terminal packet and host authorization buffers swapped");
    const auto [packet_storage, packet_pointer_type] =
        packet.Alias(EmitContext::PointerType::U32);
    const auto [auth_storage, auth_pointer_type] =
        auth.Alias(EmitContext::PointerType::U32);
    ASSERT_MSG(Sirit::ValidId(packet_storage) && Sirit::ValidId(auth_storage),
               "GDS terminal capture missing GPU descriptor storage");

    const Id local_index = ctx.OpLoad(ctx.U32[1], ctx.local_invocation_index);
    const Id lane = ctx.OpUMod(ctx.U32[1], local_index,
                              ctx.ConstU32(Gds::GcnWaveLanes));
    const Id wave_in_group = ctx.OpUDiv(ctx.U32[1], local_index,
                                       ctx.ConstU32(Gds::GcnWaveLanes));
    const Id physical_slot = ctx.OpIAdd(
        ctx.U32[1], ctx.OpIMul(ctx.U32[1], ctx.workgroup_index_id,
                              ctx.ConstU32(u32(threads / Gds::GcnWaveLanes))),
        wave_in_group);
    // Exactly one physical invocation owns the 16-byte aligned packet header.
    // Never permit 64 lanes to race over the same 16-DWORD packet.
    const Id lane_zero = ctx.OpIEqual(ctx.U1[1], lane, ctx.u32_zero_value);
    const Id write_label = ctx.OpLabel();
    const Id merge_label = ctx.OpLabel();
    ctx.OpSelectionMerge(merge_label, spv::SelectionControlMask::MaskNone);
    ctx.OpBranchConditional(lane_zero, write_label, merge_label);
    ctx.AddLabel(write_label);

    const Id auth_base = ctx.OpIMul(ctx.U32[1], physical_slot,
                                    ctx.ConstU32(Gds::GcnEpochAuthDwords));
    const auto load_auth = [&](u32 word) {
        const Id index = ctx.OpIAdd(ctx.U32[1], auth_base, ctx.ConstU32(word));
        return ctx.OpLoad(ctx.U32[1], ctx.OpAccessChain(
            auth_pointer_type, auth_storage, ctx.u32_zero_value, index));
    };
    const Id tag = load_auth(0);
    const Id epoch = load_auth(1);
    const Id source_slot = load_auth(2);
    const Id wave_slots = load_auth(3);
    const Id token_lo = load_auth(4);
    const Id token_hi = load_auth(5);
    const Id flags = load_auth(6);
    const Id reserved = load_auth(7);

    const Id declared_slots_valid = ctx.OpLogicalAnd(
        ctx.U1[1],
        ctx.OpINotEqual(ctx.U1[1], wave_slots, ctx.u32_zero_value),
        ctx.OpULessThanEqual(ctx.U1[1], wave_slots,
                            ctx.ConstU32(Gds::GcnEpochAuthMaximumWaves)));
    const Id slot_valid = ctx.OpLogicalAnd(
        ctx.U1[1],
        ctx.OpIEqual(ctx.U1[1], physical_slot, source_slot),
        ctx.OpULessThan(ctx.U1[1], physical_slot, wave_slots));
    const Id source_valid = ctx.OpLogicalAnd(
        ctx.U1[1],
        ctx.OpIEqual(ctx.U1[1], tag, ctx.ConstU32(Gds::GcnEpochAuthTag)),
        ctx.OpIEqual(ctx.U1[1], flags, ctx.ConstU32(Gds::GcnEpochAuthFlags)));
    const Id epoch_valid = ctx.OpLogicalAnd(
        ctx.U1[1],
        ctx.OpINotEqual(ctx.U1[1], epoch, ctx.u32_zero_value),
        ctx.OpIEqual(ctx.U1[1], reserved, ctx.u32_zero_value));
    const Id token_valid = ctx.OpLogicalOr(
        ctx.U1[1], ctx.OpINotEqual(ctx.U1[1], token_lo, ctx.u32_zero_value),
        ctx.OpINotEqual(ctx.U1[1], token_hi, ctx.u32_zero_value));
    const Id valid = ctx.OpLogicalAnd(
        ctx.U1[1],
        ctx.OpLogicalAnd(ctx.U1[1], declared_slots_valid, slot_valid),
        ctx.OpLogicalAnd(
            ctx.U1[1],
            ctx.OpLogicalAnd(ctx.U1[1], source_valid, epoch_valid),
            token_valid));

    // Bypass does not consume DS, return a VDST, or need a GPU continuation
    // token. The host validates this exact canonical packet and separately
    // authenticates the original per-slot epoch record. All other fields
    // are zero; 0xffffffff marks the absence of any site and active lane.
    const Id valid_tag = ctx.OpSelect(
        ctx.U32[1], valid, ctx.ConstU32(Gds::CapturePacketTag), ctx.u32_zero_value);
    const Id valid_epoch = ctx.OpSelect(
        ctx.U32[1], valid, epoch, ctx.u32_zero_value);
    const Id valid_flags = ctx.OpSelect(
        ctx.U32[1], valid, ctx.ConstU32(Gds::CaptureBypassFlags),
        ctx.u32_zero_value);
    const std::array<Id, Gds::GpuCapturePacketDwords> words{
        valid_tag, valid_epoch, ctx.ConstU32(Gds::NoActiveLane),
        ctx.u32_zero_value, physical_slot, ctx.u32_zero_value,
        ctx.u32_zero_value, ctx.u32_zero_value,
        ctx.ConstU32(Gds::NoActiveLane), ctx.u32_zero_value,
        ctx.u32_zero_value, ctx.u32_zero_value, valid_flags,
        ctx.u32_zero_value, ctx.u32_zero_value, ctx.u32_zero_value,
    };
    const Id packet_base = ctx.OpIMul(
        ctx.U32[1], physical_slot, ctx.ConstU32(Gds::GpuCapturePacketDwords));
    for (u32 word = 0; word < words.size(); ++word) {
        const Id index = ctx.OpIAdd(ctx.U32[1], packet_base, ctx.ConstU32(word));
        const Id ptr = ctx.OpAccessChain(
            packet_pointer_type, packet_storage, ctx.u32_zero_value, index);
        ctx.OpStore(ptr, words[word]);
    }
    ctx.OpBranch(merge_label);
    ctx.AddLabel(merge_label);
}

void Traverse(EmitContext& ctx, const IR::Program& program) {
    IR::Block* current_block{};
    for (const IR::AbstractSyntaxNode& node : program.syntax_list) {
        switch (node.type) {
        case IR::AbstractSyntaxNode::Type::Block: {
            const Id label{node.data.block->Definition<Id>()};
            if (current_block) {
                ctx.OpBranch(label);
            }
            current_block = node.data.block;
            ctx.AddLabel(label);
            for (IR::Inst& inst : node.data.block->Instructions()) {
                EmitInst(ctx, &inst);
            }
            ctx.first_to_last_label_map[label.value] = ctx.last_label;
            break;
        }
        case IR::AbstractSyntaxNode::Type::If: {
            const Id if_label{node.data.if_node.body->Definition<Id>()};
            const Id endif_label{node.data.if_node.merge->Definition<Id>()};
            ctx.OpSelectionMerge(endif_label, spv::SelectionControlMask::MaskNone);
            ctx.OpBranchConditional(ctx.Def(node.data.if_node.cond), if_label, endif_label);
            break;
        }
        case IR::AbstractSyntaxNode::Type::Loop: {
            const Id body_label{node.data.loop.body->Definition<Id>()};
            const Id continue_label{node.data.loop.continue_block->Definition<Id>()};
            const Id endloop_label{node.data.loop.merge->Definition<Id>()};

            ctx.OpLoopMerge(endloop_label, continue_label, spv::LoopControlMask::MaskNone);
            ctx.OpBranch(body_label);
            break;
        }
        case IR::AbstractSyntaxNode::Type::Break: {
            const Id break_label{node.data.break_node.merge->Definition<Id>()};
            const Id skip_label{node.data.break_node.skip->Definition<Id>()};
            ctx.OpBranchConditional(ctx.Def(node.data.break_node.cond), break_label, skip_label);
            break;
        }
        case IR::AbstractSyntaxNode::Type::EndIf:
            if (current_block) {
                ctx.OpBranch(node.data.end_if.merge->Definition<Id>());
            }
            break;
        case IR::AbstractSyntaxNode::Type::Repeat: {
            Id cond{ctx.Def(node.data.repeat.cond)};
            const Id loop_header_label{node.data.repeat.loop_header->Definition<Id>()};
            const Id merge_label{node.data.repeat.merge->Definition<Id>()};
            ctx.OpBranchConditional(cond, loop_header_label, merge_label);
            break;
        }
        case IR::AbstractSyntaxNode::Type::Return:
            if (ctx.ordered_gds_capture_pass) {
                // Only the ORIGINAL decoded GCN terminal path reaches this.
                // An ordered wave has already returned at the DS opcode.
                EmitOrderedGdsTerminalBypass(ctx);
            }
            ctx.OpReturn();
            break;
        case IR::AbstractSyntaxNode::Type::Unreachable:
            ctx.OpUnreachable();
            break;
        }
        if (node.type != IR::AbstractSyntaxNode::Type::Block) {
            current_block = nullptr;
        }
    }
}

Id DefineMain(EmitContext& ctx, const IR::Program& program) {
    const Id void_function{ctx.TypeFunction(ctx.void_id)};
    const Id main{ctx.OpFunction(ctx.void_id, spv::FunctionControlMask::MaskNone, void_function)};
    for (IR::Block* const block : program.blocks) {
        block->SetDefinition(ctx.OpLabel());
    }
    Traverse(ctx, program);
    ctx.OpFunctionEnd();
    return main;
}

spv::ExecutionMode ExecutionMode(AmdGpu::TessellationType primitive) {
    switch (primitive) {
    case AmdGpu::TessellationType::Isoline:
        return spv::ExecutionMode::Isolines;
    case AmdGpu::TessellationType::Triangle:
        return spv::ExecutionMode::Triangles;
    case AmdGpu::TessellationType::Quad:
        return spv::ExecutionMode::Quads;
    }
    UNREACHABLE_MSG("Tessellation primitive {}", magic_enum::enum_name(primitive));
}

spv::ExecutionMode ExecutionMode(AmdGpu::TessellationPartitioning spacing) {
    switch (spacing) {
    case AmdGpu::TessellationPartitioning::Integer:
        return spv::ExecutionMode::SpacingEqual;
    case AmdGpu::TessellationPartitioning::FracOdd:
        return spv::ExecutionMode::SpacingFractionalOdd;
    case AmdGpu::TessellationPartitioning::FracEven:
        return spv::ExecutionMode::SpacingFractionalEven;
    case AmdGpu::TessellationPartitioning::Pow2:
        // Pow2 rounds tessellation factors to the nearest power of 2, which has no
        // direct Vulkan equivalent. SpacingEqual (integer) is the closest match.
        LOG_WARNING(Render_Vulkan, "Tessellation partitioning Pow2 has no Vulkan equivalent, "
                                   "falling back to SpacingEqual");
        return spv::ExecutionMode::SpacingEqual;
    default:
        break;
    }
    UNREACHABLE_MSG("Tessellation spacing {}", magic_enum::enum_name(spacing));
}

void SetupCapabilities(const Info& info, const Profile& profile, const RuntimeInfo& runtime_info,
                       EmitContext& ctx) {
    ctx.AddCapability(spv::Capability::Image1D);
    ctx.AddCapability(spv::Capability::Sampled1D);
    ctx.AddCapability(spv::Capability::ImageQuery);
    ctx.AddCapability(spv::Capability::Int8);
    ctx.AddCapability(spv::Capability::Int16);
    ctx.AddCapability(spv::Capability::Int64);
    ctx.AddCapability(spv::Capability::StorageBuffer8BitAccess);
    ctx.AddCapability(spv::Capability::StorageBuffer16BitAccess);
    if (info.uses_fp16) {
        ctx.AddCapability(spv::Capability::Float16);
    }
    if (info.uses_fp64) {
        ctx.AddCapability(spv::Capability::Float64);
    }
    if (info.has_storage_images) {
        ctx.AddCapability(spv::Capability::StorageImageExtendedFormats);
        ctx.AddCapability(spv::Capability::StorageImageReadWithoutFormat);
        ctx.AddCapability(spv::Capability::StorageImageWriteWithoutFormat);
        if (profile.supports_image_load_store_lod) {
            ctx.AddExtension("SPV_AMD_shader_image_load_store_lod");
            ctx.AddCapability(spv::Capability::ImageReadWriteLodAMD);
        }
    }
    if (info.has_image_gather) {
        ctx.AddCapability(spv::Capability::ImageGatherExtended);
    }
    if (info.has_image_query) {
        ctx.AddCapability(spv::Capability::ImageQuery);
    }
    if ((info.uses_image_atomic_float_min_max && profile.supports_image_fp32_atomic_min_max) ||
        (info.uses_buffer_atomic_float_min_max && profile.supports_buffer_fp32_atomic_min_max)) {
        ctx.AddExtension("SPV_EXT_shader_atomic_float_min_max");
        ctx.AddCapability(spv::Capability::AtomicFloat32MinMaxEXT);
    }
    if (info.uses_lane_id) {
        ctx.AddCapability(spv::Capability::GroupNonUniform);
    }
    if (info.uses_group_quad) {
        ctx.AddCapability(spv::Capability::GroupNonUniformQuad);
    }
    if (info.uses_group_shuffle) {
        ctx.AddCapability(spv::Capability::GroupNonUniformShuffle);
    }
    if (info.uses_group_ballot || info.loads.Get(IR::Attribute::SubgroupLtMask)) {
        ctx.AddCapability(spv::Capability::GroupNonUniformBallot);
    }
    if (info.uses_shader_clock) {
        if (ctx.profile.supports_shader_subgroup_clock) {
            ctx.AddExtension("SPV_KHR_shader_clock");
            ctx.AddCapability(spv::Capability::ShaderClockKHR);
        } else {
            LOG_WARNING(Render_Recompiler,
                        "Shader requires support for ShaderClockKHR capability"
                        " that your Vulkan instance does not advertise. Results may vary");
        }
    }
    const auto stage = info.sw_stage;
    if (stage == SwStage::Vertex) {
        ctx.AddExtension("SPV_KHR_shader_draw_parameters");
        ctx.AddCapability(spv::Capability::DrawParameters);
    }
    if (stage == SwStage::Geometry) {
        ctx.AddCapability(spv::Capability::Geometry);
    }
    if (info.hw_stage == HwStage::Fragment) {
        if (profile.supports_amd_shader_explicit_vertex_parameter) {
            ctx.AddExtension("SPV_AMD_shader_explicit_vertex_parameter");
        } else if (profile.supports_fragment_shader_barycentric) {
            ctx.AddExtension("SPV_KHR_fragment_shader_barycentric");
            ctx.AddCapability(spv::Capability::FragmentBarycentricKHR);
            ctx.AddCapability(spv::Capability::InterpolationFunction);
        }
        if (info.loads.Get(IR::Attribute::SampleIndex) ||
            runtime_info.hw.fs.addr_flags.linear_sample_ena ||
            runtime_info.hw.fs.addr_flags.persp_sample_ena ||
            (!profile.supports_amd_shader_explicit_vertex_parameter &&
             profile.supports_fragment_shader_barycentric &&
             info.loads.Get(IR::Attribute::BaryCoordSmoothSample))) {
            ctx.AddCapability(spv::Capability::SampleRateShading);
        }
        if (info.loads.GetAny(IR::Attribute::RenderTargetIndex)) {
            ctx.AddCapability(spv::Capability::Geometry);
        }
        if (info.stores.Get(IR::Attribute::StencilRef) && profile.supports_shader_stencil_export) {
            ctx.AddExtension("SPV_EXT_shader_stencil_export");
            ctx.AddCapability(spv::Capability::StencilExportEXT);
        }
    }
    if (stage == SwStage::TessellationControl || stage == SwStage::TessellationEval) {
        ctx.AddCapability(spv::Capability::Tessellation);
    }
    if (stage == SwStage::Vertex || stage == SwStage::TessellationControl ||
        stage == SwStage::TessellationEval) {
        if (info.stores.GetAny(IR::Attribute::RenderTargetIndex)) {
            ctx.AddCapability(spv::Capability::ShaderLayer);
        }
        if (info.stores.GetAny(IR::Attribute::ViewportIndex)) {
            ctx.AddCapability(spv::Capability::ShaderViewportIndex);
        }
    } else if (stage == SwStage::Geometry && info.stores.GetAny(IR::Attribute::ViewportIndex)) {
        ctx.AddCapability(spv::Capability::MultiViewport);
    }
    if (info.uses_dma) {
        ctx.AddCapability(spv::Capability::PhysicalStorageBufferAddresses);
        ctx.AddExtension("SPV_KHR_physical_storage_buffer");
    }
    const auto shared_type_count = std::popcount(static_cast<u32>(info.shared_types));
    if (shared_type_count > 1 && profile.supports_workgroup_explicit_memory_layout) {
        ctx.AddExtension("SPV_KHR_workgroup_memory_explicit_layout");
        ctx.AddCapability(spv::Capability::WorkgroupMemoryExplicitLayoutKHR);
        ctx.AddCapability(spv::Capability::WorkgroupMemoryExplicitLayout16BitAccessKHR);
    }
    if (info.uses_buffer_int64_atomics || info.uses_shared_int64_atomics) {
        if (info.uses_buffer_int64_atomics) {
            ASSERT_MSG(ctx.profile.supports_buffer_int64_atomics,
                       "Shader requires support for atomic Int64 buffer operations that your "
                       "Vulkan instance does not advertise");
        }
        if (info.uses_shared_int64_atomics) {
            ASSERT_MSG(ctx.profile.supports_shared_int64_atomics,
                       "Shader requires support for atomic Int64 shared memory operations that "
                       "your Vulkan instance does not advertise");
        }
        ctx.AddCapability(spv::Capability::Int64Atomics);
    }
}

void DefineEntryPoint(const Info& info, EmitContext& ctx, Id main) {
    const std::span interfaces(ctx.interfaces.data(), ctx.interfaces.size());
    spv::ExecutionModel execution_model{};
    switch (info.sw_stage) {
    case SwStage::Compute: {
        const std::array<u32, 3> workgroup_size{ctx.runtime_info.hw.cs.workgroup_size};
        execution_model = spv::ExecutionModel::GLCompute;
        ctx.AddExecutionMode(main, spv::ExecutionMode::LocalSize, workgroup_size[0],
                             workgroup_size[1], workgroup_size[2]);
        break;
    }
    case SwStage::Vertex:
        execution_model = spv::ExecutionModel::Vertex;
        break;
    case SwStage::TessellationControl:
        execution_model = spv::ExecutionModel::TessellationControl;
        ctx.AddCapability(spv::Capability::Tessellation);
        ctx.AddExecutionMode(main, spv::ExecutionMode::OutputVertices,
                             ctx.runtime_info.sw.tcs.NumOutputControlPoints());
        break;
    case SwStage::TessellationEval: {
        execution_model = spv::ExecutionModel::TessellationEvaluation;
        ctx.AddExecutionMode(main, ExecutionMode(ctx.runtime_info.sw.tes.tess_type));
        ctx.AddExecutionMode(main, ExecutionMode(ctx.runtime_info.sw.tes.tess_partitioning));
        ctx.AddExecutionMode(main, ctx.runtime_info.sw.tes.tess_topology ==
                                           AmdGpu::TessellationTopology::TriangleCcw
                                       ? spv::ExecutionMode::VertexOrderCcw
                                       : spv::ExecutionMode::VertexOrderCw);
        break;
    }
    case SwStage::Fragment:
        execution_model = spv::ExecutionModel::Fragment;
        if (ctx.profile.lower_left_origin_mode) {
            ctx.AddExecutionMode(main, spv::ExecutionMode::OriginLowerLeft);
        } else {
            ctx.AddExecutionMode(main, spv::ExecutionMode::OriginUpperLeft);
        }
        if (info.has_discard) {
            ctx.AddCapability(spv::Capability::DemoteToHelperInvocation);
        }
        if (info.stores.GetAny(IR::Attribute::Depth)) {
            ctx.AddExecutionMode(main, spv::ExecutionMode::DepthReplacing);
        }
        break;
    case SwStage::Geometry:
        execution_model = spv::ExecutionModel::Geometry;
        ctx.AddExecutionMode(main, GetInputPrimitiveType(ctx.runtime_info.hw.gs.in_primitive));
        ctx.AddExecutionMode(main, GetOutputPrimitiveType(ctx.runtime_info.hw.gs.out_primitive[0]));
        ctx.AddExecutionMode(main, spv::ExecutionMode::OutputVertices,
                             ctx.runtime_info.hw.gs.output_vertices);
        ctx.AddExecutionMode(main, spv::ExecutionMode::Invocations,
                             ctx.runtime_info.hw.gs.num_invocations);
        break;
    default:
        UNREACHABLE_MSG("Stage {}", u32(info.hw_stage));
    }
    ctx.AddEntryPoint(execution_model, main, "main", interfaces);
}

void SetupDenormFlushMode(EmitContext& ctx, const Profile& profile, const RuntimeInfo& runtime_info,
                          Id main_func) {
    const auto fp32_denorm_mode = runtime_info.props.fp_denorm_mode32;
    if (fp32_denorm_mode == AmdGpu::FpDenormMode::InOutFlush) {
        if (profile.support_fp32_denorm_flush) {
            ctx.AddCapability(spv::Capability::DenormFlushToZero);
            ctx.AddExecutionMode(main_func, spv::ExecutionMode::DenormFlushToZero, 32U);
        } else {
            static std::once_flag logged;
            std::call_once(logged, [] {
                LOG_WARNING(Render_Vulkan, "Float32 denorm flushing is not supported by the GPU");
            });
        }
    } else {
        LOG_WARNING(Render_Vulkan, "Unknown FP denorm mode {}", u32(fp32_denorm_mode));
    }

    if (!ctx.info.uses_fp16 && !ctx.info.uses_fp64) {
        return;
    }

    const auto fp16_64_denorm_mode = runtime_info.props.fp_denorm_mode16_64;
    const auto same_denorm_mode = fp16_64_denorm_mode == fp32_denorm_mode;
    if (!same_denorm_mode && !profile.supports_denorm_behavior_independence) {
        static std::once_flag logged;
        std::call_once(logged, [] {
            LOG_WARNING(Render_Vulkan, "Float32 and Float16/64 denorm modes are different but the "
                                       "GPU does not support that");
        });
        return;
    }

    if (fp16_64_denorm_mode == AmdGpu::FpDenormMode::InOutFlush) {
        if (profile.support_fp16_denorm_flush) {
            ctx.AddCapability(spv::Capability::DenormFlushToZero);
            ctx.AddExecutionMode(main_func, spv::ExecutionMode::DenormFlushToZero, 16U);
        } else {
            static std::once_flag logged;
            std::call_once(logged, [] {
                LOG_WARNING(Render_Vulkan, "Float16 denorm flushing is not supported by the GPU");
            });
        }
        if (profile.support_fp64_denorm_flush) {
            ctx.AddCapability(spv::Capability::DenormFlushToZero);
            ctx.AddExecutionMode(main_func, spv::ExecutionMode::DenormFlushToZero, 64U);
        } else {
            static std::once_flag logged;
            std::call_once(logged, [] {
                LOG_WARNING(Render_Vulkan, "Float64 denorm flushing is not supported by the GPU");
            });
        }
    } else if (fp16_64_denorm_mode == AmdGpu::FpDenormMode::InOutAllow) {
        if (profile.support_fp16_denorm_preserve) {
            ctx.AddCapability(spv::Capability::DenormPreserve);
            ctx.AddExecutionMode(main_func, spv::ExecutionMode::DenormPreserve, 16U);
        } else {
            static std::once_flag logged;
            std::call_once(logged, [] {
                LOG_WARNING(Render_Vulkan, "Float16 denorm preserving is not supported by the GPU");
            });
        }
        if (profile.support_fp64_denorm_preserve) {
            ctx.AddCapability(spv::Capability::DenormPreserve);
            ctx.AddExecutionMode(main_func, spv::ExecutionMode::DenormPreserve, 64U);
        } else {
            static std::once_flag logged;
            std::call_once(logged, [] {
                LOG_WARNING(Render_Vulkan, "Float64 denorm preserving is not supported by the GPU");
            });
        }
    } else {
        LOG_WARNING(Render_Vulkan, "Unknown Float16/64 denorm mode {}", u32(fp16_64_denorm_mode));
    }
}

void SetupRoundingMode(EmitContext& ctx, const Profile& profile, const RuntimeInfo& runtime_info,
                       Id main_func) {
    const auto fp_round_mode = runtime_info.props.fp_round_mode32;
    if (fp_round_mode == AmdGpu::FpRoundMode::ToZero) {
        if (profile.support_fp32_round_to_zero) {
            ctx.AddCapability(spv::Capability::RoundingModeRTZ);
            ctx.AddExecutionMode(main_func, spv::ExecutionMode::RoundingModeRTZ, 32U);
        } else {
            static std::once_flag logged;
            std::call_once(logged, [] {
                LOG_WARNING(Render_Vulkan, "Float32 rounding to zero is not supported by the GPU");
            });
        }
    } else if (fp_round_mode != AmdGpu::FpRoundMode::NearestEven) {
        LOG_WARNING(Render_Vulkan, "Unimplemented FP rounding mode {}",
                    magic_enum::enum_name(fp_round_mode));
    }

    if (ctx.info.uses_fp16 || ctx.info.uses_fp64) {
        const auto fp16_64_round_mode = runtime_info.props.fp_round_mode16_64;
        const auto same_round_mode = fp16_64_round_mode == fp_round_mode;
        if (!same_round_mode && !profile.supports_rounding_mode_independence) {
            static std::once_flag logged;
            std::call_once(logged, [] {
                LOG_WARNING(Render_Vulkan, "Float32 and Float16/64 rounding modes are different "
                                           "but the GPU does not support that");
            });
            return;
        }

        if (fp16_64_round_mode == AmdGpu::FpRoundMode::ToZero) {
            if (ctx.info.uses_fp16) {
                if (profile.support_fp16_round_to_zero) {
                    ctx.AddCapability(spv::Capability::RoundingModeRTZ);
                    ctx.AddExecutionMode(main_func, spv::ExecutionMode::RoundingModeRTZ, 16U);
                } else {
                    static std::once_flag logged;
                    std::call_once(logged, [] {
                        LOG_WARNING(Render_Vulkan,
                                    "Float16 rounding to zero is not supported by the GPU");
                    });
                }
            }
            if (ctx.info.uses_fp64) {
                if (profile.support_fp64_round_to_zero) {
                    ctx.AddCapability(spv::Capability::RoundingModeRTZ);
                    ctx.AddExecutionMode(main_func, spv::ExecutionMode::RoundingModeRTZ, 64U);
                } else {
                    static std::once_flag logged;
                    std::call_once(logged, [] {
                        LOG_WARNING(Render_Vulkan,
                                    "Float64 rounding to zero is not supported by the GPU");
                    });
                }
            }
        } else if (fp16_64_round_mode != AmdGpu::FpRoundMode::NearestEven) {
            LOG_WARNING(Render_Vulkan, "Unknown Float16/64 rounding mode {}",
                        u32(fp16_64_round_mode));
        }
    }
}

void SetupInfNanPreserveMode(EmitContext& ctx, const Profile& profile,
                             const RuntimeInfo& runtime_info, Id main_func) {
    if (profile.support_fp16_signed_zero_inf_nan_preserve ||
        profile.support_fp32_signed_zero_inf_nan_preserve ||
        profile.support_fp64_signed_zero_inf_nan_preserve) {
        ctx.AddCapability(spv::Capability::SignedZeroInfNanPreserve);
    }

    if (profile.support_fp32_signed_zero_inf_nan_preserve) {
        ctx.AddExecutionMode(main_func, spv::ExecutionMode::SignedZeroInfNanPreserve, 32U);
    } else {
        static std::once_flag logged;
        std::call_once(logged, [] {
            LOG_WARNING(Render_Vulkan,
                        "Float32 signed zero/inf/nan preserve mode is not supported by the GPU");
        });
    }
    if (ctx.info.uses_fp16) {
        if (profile.support_fp16_signed_zero_inf_nan_preserve) {
            ctx.AddExecutionMode(main_func, spv::ExecutionMode::SignedZeroInfNanPreserve, 16U);
        } else {
            static std::once_flag logged;
            std::call_once(logged, [] {
                LOG_WARNING(
                    Render_Vulkan,
                    "Float16 signed zero/inf/nan preserve mode is not supported by the GPU");
            });
        }
    }
    if (ctx.info.uses_fp64) {
        if (profile.support_fp64_signed_zero_inf_nan_preserve) {
            ctx.AddExecutionMode(main_func, spv::ExecutionMode::SignedZeroInfNanPreserve, 64U);
        } else {
            static std::once_flag logged;
            std::call_once(logged, [] {
                LOG_WARNING(
                    Render_Vulkan,
                    "Float64 signed zero/inf/nan preserve mode is not supported by the GPU");
            });
        }
    }
}

void SetupFloatMode(EmitContext& ctx, const Profile& profile, const RuntimeInfo& runtime_info,
                    Id main_func) {
    ctx.AddExtension("SPV_KHR_float_controls");
    SetupDenormFlushMode(ctx, profile, runtime_info, main_func);
    SetupRoundingMode(ctx, profile, runtime_info, main_func);
    SetupInfNanPreserveMode(ctx, profile, runtime_info, main_func);
}

void PatchPhiNodes(const IR::Program& program, EmitContext& ctx) {
    auto inst{program.blocks.front()->begin()};
    size_t block_index{0};
    ctx.PatchDeferredPhi([&](u32 phi_arg, Id first_parent) {
        if (phi_arg == 0) {
            ++inst;
            while (inst == program.blocks[block_index]->end() ||
                   inst->GetOpcode() != IR::Opcode::Phi) {
                ++block_index;
                inst = program.blocks[block_index]->begin();
            }
        }
        ASSERT(inst != program.blocks[block_index]->end());
        const Id arg = ctx.Def(inst->Arg(phi_arg));
        const Id parent = ctx.first_to_last_label_map[first_parent.value];
        return std::make_pair(arg, parent);
    });
}
} // Anonymous namespace

// Both shader compilation modes share the identical REAL GCN translation,
// resource bindings, structurized CFG, SSA and SPIR-V capabilities. The
// mode switch affects ONLY how an actual DS_ORDERED_COUNT terminates a
// capture dispatch. Normal guest execution is never allowed to use it.
static std::vector<u32> EmitSPIRVWithCaptureMode(
    const Profile& profile, const RuntimeInfo& runtime_info,
    const IR::Program& program, Bindings& binding, bool capture_only) {
    EmitContext ctx{profile, runtime_info, program.info, binding};
    ctx.ordered_gds_capture_pass = capture_only;
    const Id main{DefineMain(ctx, program)};
    DefineEntryPoint(program.info, ctx, main);
    SetupCapabilities(program.info, profile, runtime_info, ctx);
    SetupFloatMode(ctx, profile, runtime_info, main);
    PatchPhiNodes(program, ctx);
    return ctx.Assemble();
}

std::vector<u32> EmitSPIRV(const Profile& profile, const RuntimeInfo& runtime_info,
                           const IR::Program& program, Bindings& binding) {
    // Normal guest execution MUST remain blocked until the dedicated CIK
    // packer seed, complete-wave handling and true GPU continuation exist.
    ASSERT_MSG(!program.info.uses_ordered_gds,
               "DS_ORDERED_COUNT needs guest wave creation ordering; refusing unordered "
               "SPIR-V atomic lowering");
    return EmitSPIRVWithCaptureMode(profile, runtime_info, program, binding, false);
}

std::vector<u32> EmitSPIRVOrderedGdsCapturePass(
    const Profile& profile, const RuntimeInfo& runtime_info,
    const IR::Program& program, Bindings& binding) {
    // Explicit FIRST PASS only: continue ORIGINAL guest shader until the
    // ordered site, write the real live GPU checkpoint and immediately
    // terminate the wave BEFORE any instruction that consumes the returned
    // counter. This helper is intentionally not called by the gameplay
    // pipeline or the normal EmitSPIRV path.
    ASSERT_MSG(program.info.uses_ordered_gds &&
               program.info.hw_stage == HwStage::Compute &&
               !program.info.ordered_gds_sites.empty(),
               "GCN ordered capture pass requires decoded compute ordered sites");
    return EmitSPIRVWithCaptureMode(profile, runtime_info, program, binding, true);
}

Id EmitPhi(EmitContext& ctx, IR::Inst* inst) {
    const size_t num_args{inst->NumArgs()};
    boost::container::small_vector<Id, 32> blocks;
    blocks.reserve(num_args);
    for (size_t index = 0; index < num_args; ++index) {
        blocks.push_back(inst->PhiBlock(index)->Definition<Id>());
    }
    // The type of a phi instruction is stored in its flags
    const Id result_type{TypeId(ctx, inst->Flags<IR::Type>())};
    return ctx.DeferredOpPhi(result_type, std::span(blocks.data(), blocks.size()));
}

void EmitVoid(EmitContext&) {}

Id EmitConditionRef(EmitContext& ctx, const IR::Value& value) {
    const Id id{ctx.Def(value)};
    ASSERT_MSG(Sirit::ValidId(id), "Forward identity declaration");
    return id;
}

Id EmitGetPcLo(EmitContext& ctx, const IR::Value& value) {
    const Id id{ctx.Def(value)};
    ASSERT_MSG(Sirit::ValidId(id), "Forward identity declaration");
    return id;
}

void EmitGetScc(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

void EmitGetExec(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

void EmitGetVcc(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

void EmitGetVccLo(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

void EmitGetVccHi(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

void EmitGetM0(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

void EmitOrderedGdsCheckpointRegister(EmitContext& ctx, u32 site_pc, u32 bank,
                                      u32 index, Id guest_value, Id logical_slot, Id lane) {
    // IR operands are taken from the actual guest shader SSA at the original
    // ordered-GDS site. Bank 4 writes *packet* DWORDs, never register state.
    // Both real GPU SSBOs are independent and must be owned by the dispatch.
    // The global guest opcode execution gate remains active until the
    // first-pass packet and second-pass GCN continuation are authenticated.
    namespace Gds = Shader::Gcn::OrderedGds;
    ASSERT_MSG(ctx.hw_stage == HwStage::Compute &&
                   ctx.has_ordered_gds_checkpoint_binding &&
                   ctx.has_ordered_gds_capture_binding &&
                   ctx.has_ordered_gds_auth_binding,
               "GCN capture requires independent checkpoint, packet and host-authority SSBOs");
    ASSERT_MSG(std::ranges::any_of(ctx.info.ordered_gds_sites,
                                  [site_pc](const auto& site) { return site.pc == site_pc; }),
               "GCN checkpoint instruction site must come from decoded guest ISA");
    const bool packet = bank == 4u;
    const u32 vgprs = ctx.runtime_info.props.num_allocated_vgprs;
    const auto relative = packet ? Gds::GpuCapturePacketDwordOffset(0, index)
                                 : Gds::CheckpointRelativeDword(vgprs, bank, index, 0);
    ASSERT_MSG(relative.has_value(), "Invalid GCN register or packet DWORD index");
    const u32 stride = packet ? Gds::GpuCapturePacketDwords
                              : Gds::CheckpointStrideDwords(vgprs);
    const auto& buffer = ctx.buffers.at(
        packet ? ctx.ordered_gds_capture_index : ctx.ordered_gds_checkpoint_index);
    ASSERT_MSG(buffer.buffer_type ==
                   (packet ? BufferType::OrderedGdsCapture
                           : BufferType::OrderedGdsCheckpoint),
               "GCN packet/register checkpoint SSBO resource type mismatch");
    const auto [storage, pointer_type] = buffer.Alias(EmitContext::PointerType::U32);
    ASSERT_MSG(Sirit::ValidId(storage) && Sirit::ValidId(guest_value),
               "Missing live GCN packet/checkpoint SPIR-V SSBO or register value");

    Id real_value = guest_value;
    // A capture-only module may stamp packet/register validity ONLY when a
    // trusted host-owned epoch record matches this exact physical wave slot.
    // Invalid/missing authority must leave tags and flags at zero; normal
    // guest shaders never reach this path while ordered GDS is unsupported.
    const bool identity_dword =
        (packet && (index == 1u || index == 10u || index == 11u)) ||
        bank == u32(Gds::CheckpointRegisterBank::AuthenticatedIdentity);
    const bool packet_stamp = packet && (index == 0u || index == 12u);
    const bool checkpoint_stamp =
        bank == u32(Gds::CheckpointRegisterBank::SiteMetadata) &&
        (index == 0u || index == 8u);
    if (identity_dword || packet_stamp || checkpoint_stamp) {
        const auto& auth = ctx.buffers.at(ctx.ordered_gds_auth_index);
        ASSERT_MSG(auth.buffer_type == BufferType::OrderedGdsEpochAuth,
                   "GCN wave token source is not the host authority SSBO");
        const auto [auth_storage, auth_pointer_type] =
            auth.Alias(EmitContext::PointerType::U32);
        ASSERT_MSG(Sirit::ValidId(auth_storage),
                   "missing verified GPU epoch/token binding");
        const Id auth_base = ctx.OpIMul(ctx.U32[1], logical_slot,
                                       ctx.ConstU32(Gds::GcnEpochAuthDwords));
        const auto load_auth = [&](u32 word) {
            const Id offset = ctx.OpIAdd(ctx.U32[1], auth_base, ctx.ConstU32(word));
            const Id pointer = ctx.OpAccessChain(
                auth_pointer_type, auth_storage, ctx.u32_zero_value, offset);
            return ctx.OpLoad(ctx.U32[1], pointer);
        };
        const Id tag = load_auth(0);
        const Id epoch = load_auth(1);
        const Id actual_slot = load_auth(2);
        const Id wave_slots = load_auth(3);
        const Id lo = load_auth(4);
        const Id hi = load_auth(5);
        const Id flags = load_auth(6);
        const Id reserved = load_auth(7);
        const Id correct_tag = ctx.OpIEqual(
            ctx.U1[1], tag, ctx.ConstU32(Gds::GcnEpochAuthTag));
        const Id correct_slot = ctx.OpIEqual(ctx.U1[1], actual_slot, logical_slot);
        const Id correct_flags = ctx.OpIEqual(
            ctx.U1[1], flags, ctx.ConstU32(Gds::GcnEpochAuthFlags));
        const Id epoch_nonzero = ctx.OpINotEqual(ctx.U1[1], epoch, ctx.u32_zero_value);
        const Id token_nonzero = ctx.OpLogicalOr(
            ctx.U1[1], ctx.OpINotEqual(ctx.U1[1], lo, ctx.u32_zero_value),
            ctx.OpINotEqual(ctx.U1[1], hi, ctx.u32_zero_value));
        const Id slot_in_range = ctx.OpULessThan(ctx.U1[1], logical_slot, wave_slots);
        const Id bounded_waves = ctx.OpULessThanEqual(
            ctx.U1[1], wave_slots, ctx.ConstU32(Gds::GcnEpochAuthMaximumWaves));
        const Id reserved_zero = ctx.OpIEqual(ctx.U1[1], reserved, ctx.u32_zero_value);
        const Id valid0 = ctx.OpLogicalAnd(ctx.U1[1], correct_tag, correct_slot);
        const Id valid1 = ctx.OpLogicalAnd(ctx.U1[1], correct_flags, epoch_nonzero);
        const Id valid2 = ctx.OpLogicalAnd(ctx.U1[1], slot_in_range, bounded_waves);
        const Id valid3 = ctx.OpLogicalAnd(ctx.U1[1], token_nonzero, reserved_zero);
        const Id valid = ctx.OpLogicalAnd(
            ctx.U1[1], ctx.OpLogicalAnd(ctx.U1[1], valid0, valid1),
            ctx.OpLogicalAnd(ctx.U1[1], valid2, valid3));
        if (packet_stamp || checkpoint_stamp) {
            // Original guest LDS must either be absent or be lowered in
            // its entirety into the separately bound, persistent per-group
            // GPU SSBO. A transient Vulkan Workgroup allocation can NOT
            // survive the first-pass return and is never stamped valid.
            const auto& local = ctx.runtime_info.hw.cs.workgroup_size;
            const u64 threads = u64(local[0]) * local[1] * local[2];
            const bool zero_guest_lds =
                ctx.runtime_info.hw.cs.shared_memory_size == 0;
            const bool persistent_guest_lds =
                ctx.info.ordered_gds_persistent_lds &&
                ctx.runtime_info.hw.cs.shared_memory_size != 0;
            const bool complete_wave64_lds_ready =
                ctx.ordered_gds_capture_pass &&
                (zero_guest_lds || persistent_guest_lds) &&
                ctx.info.shared_memory_scratch_size == 0 &&
                threads >= Gds::GcnWaveLanes && threads <= 1024u &&
                threads % Gds::GcnWaveLanes == 0;
            const u32 valid_stamp =
                packet_stamp
                    ? (index == 0u ? Gds::CapturePacketTag : Gds::CaptureFlags)
                    : (index == 0u ? Gds::GcnCheckpointTag : Gds::GcnCheckpointFlags);
            // No valid stamp for unpreserved LDS or partial guest waves.
            // Actual counter resolution and guest shader re-entry remain
            // separate, fail-closed operations.
            real_value = complete_wave64_lds_ready
                             ? ctx.OpSelect(ctx.U32[1], valid, ctx.ConstU32(valid_stamp),
                                            ctx.u32_zero_value)
                             : ctx.u32_zero_value;
        } else {
            const u32 authority_word =
                packet ? (index == 1u ? 1u : index == 10u ? 4u : 5u)
                       : (index == 0u ? 1u : index == 1u ? 4u : 5u);
            const Id value = authority_word == 1u ? epoch :
                             authority_word == 4u ? lo : hi;
            real_value = ctx.OpSelect(ctx.U32[1], valid, value, ctx.u32_zero_value);
        }
    }

    const Id wave_offset = ctx.OpIMul(ctx.U32[1], logical_slot, ctx.ConstU32(stride));
    Id dword_offset = ctx.OpIAdd(ctx.U32[1], wave_offset, ctx.ConstU32(*relative));
    const bool vector_register = bank == u32(Gds::CheckpointRegisterBank::Vector);
    if (vector_register) {
        // Every invocation writes only its actual guest VGPR lane.
        dword_offset = ctx.OpIAdd(ctx.U32[1], dword_offset, lane);
    }
    const Id pointer{ctx.OpAccessChain(pointer_type, storage, ctx.u32_zero_value, dword_offset)};
    if (vector_register) {
        ctx.OpStore(pointer, real_value);
        return;
    }
    // Packet/header/SGPR words are wave-wide: only guest lane zero writes.
    // This is a physical slot for storage, not a guest creation rank.
    const Id first_lane = ctx.OpIEqual(ctx.U1[1], lane, ctx.u32_zero_value);
    const Id write_label = ctx.OpLabel();
    const Id merge_label = ctx.OpLabel();
    ctx.OpSelectionMerge(merge_label, spv::SelectionControlMask::MaskNone);
    ctx.OpBranchConditional(first_lane, write_label, merge_label);
    ctx.AddLabel(write_label);
    ctx.OpStore(pointer, real_value);
    ctx.OpBranch(merge_label);
    ctx.AddLabel(merge_label);
}

Id EmitOrderedGdsCount(EmitContext& ctx, Id, Id, Id /*guest_exec_mask*/,
                       u32 encoded_control, u32 site_pc) {
    if (ctx.ordered_gds_capture_pass) {
        // The REAL guest GCN checkpoint and 16-DWORD packet were emitted
        // immediately BEFORE this IR operation at the original decoded
        // DS_ORDERED_COUNT site. The first pass must not execute this guest
        // instruction with a guessed host counter or proceed to any
        // instruction that depends on its old value.
        ASSERT_MSG(ctx.hw_stage == HwStage::Compute &&
                   ctx.has_ordered_gds_checkpoint_binding &&
                   ctx.has_ordered_gds_capture_binding &&
                   ctx.has_ordered_gds_auth_binding &&
                   std::ranges::any_of(ctx.info.ordered_gds_sites,
                       [site_pc, encoded_control](const auto& site) {
                           return site.pc == site_pc &&
                                  site.encoded_control == encoded_control;
                       }),
                   "capture-pass stop requires original decoded guest GCN site");
        // An undefined SSA ID exists ONLY for code in the UNREACHABLE
        // continuation of the compiler's original structured CFG. It is
        // never an executed counter result, cannot reach guest memory and
        // must NEVER be reused in a resumed dispatch. The real old result
        // is materialized into decoded VDST by the authenticated host
        // resolver, then requires a separate second-pass guest shader.
        const Id never_executed_guest_result = ctx.OpUndef(ctx.U32[1]);
        ctx.OpReturn();
        // Sirit still needs an open block to emit later syntactic IR and
        // preserve the original structurized labels/phi bookkeeping. This
        // block has NO predecessor: the capture wave already returned.
        ctx.AddLabel();
        return never_executed_guest_result;
    }
    // The normal shader compiler MUST NEVER replace the ordered operation
    // with a host atomic, an arbitrary value or a partial capture result.
    UNREACHABLE_MSG(
        "Cannot emit ordered GDS count (guest site PC={:#x}, control={:#x}) "
        "without a verified wave-creation-order GDS scheduler",
        site_pc, encoded_control);
}

void EmitSetScc(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

void EmitSetExec(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

void EmitSetVcc(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

void EmitSetVccLo(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

void EmitSetVccHi(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

void EmitSetM0(EmitContext& ctx) {
    UNREACHABLE_MSG("Unreachable instruction");
}

} // namespace Shader::Backend::SPIRV
