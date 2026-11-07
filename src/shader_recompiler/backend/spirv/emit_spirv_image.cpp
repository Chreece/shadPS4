// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <boost/container/static_vector.hpp>
#include "shader_recompiler/backend/spirv/emit_spirv_instructions.h"
#include "shader_recompiler/backend/spirv/spirv_emit_context.h"
#include "shader_recompiler/ir/microinstruction.h"

namespace Shader::Backend::SPIRV {

template <bool is_float>
static Id FixImageCoords(EmitContext& ctx, Id coords, AmdGpu::ImageType image_type) {
    const auto coord_type = is_float ? ctx.F32 : ctx.U32;
    const auto zero = is_float ? ctx.f32_zero_value : ctx.u32_zero_value;

    switch (image_type) {
    case AmdGpu::ImageType::Color1D: {
        // Lowered to 2D with height 1
        const auto x = coords;
        return ctx.OpCompositeConstruct(coord_type[2], x, zero);
    }
    case AmdGpu::ImageType::Color1DArray: {
        // Lowered to 2D array with height 1
        const auto x = ctx.OpCompositeExtract(coord_type[1], coords, 0U);
        const auto slice = ctx.OpCompositeExtract(coord_type[1], coords, 1U);
        return ctx.OpCompositeConstruct(coord_type[3], x, zero, slice);
    }
    case AmdGpu::ImageType::Cube: {
        // Lowered to 2D array
        if (is_float) {
            const auto x = ctx.OpCompositeExtract(coord_type[1], coords, 0U);
            const auto y = ctx.OpCompositeExtract(coord_type[1], coords, 1U);
            const auto face = ctx.OpCompositeExtract(coord_type[1], coords, 2U);

            // AMD cube math results in coordinates in the range [1.0, 2.0]. We need
            // to convert this to the range [0.0, 1.0] to get correct results.
            const auto one = ctx.ConstF32(1.f);
            const auto fixed_x = ctx.OpFSub(coord_type[1], x, one);
            const auto fixed_y = ctx.OpFSub(coord_type[1], y, one);
            const auto fixed_face = ctx.OpFma(
                coord_type[1],
                ctx.OpFloor(coord_type[1], ctx.OpFDiv(coord_type[1], face, ctx.ConstF32(8.f))),
                ctx.ConstF32(-2.f), face);
            return ctx.OpCompositeConstruct(coord_type[3], fixed_x, fixed_y, fixed_face);
        }
        return coords;
    }
    default:
        return coords;
    }
}

template <bool is_float>
static Id PadImageOperand(EmitContext& ctx, Id operand, AmdGpu::ImageType image_type) {
    if (image_type == AmdGpu::ImageType::Color1D || image_type == AmdGpu::ImageType::Color1DArray) {
        const auto coord_type = is_float ? ctx.F32 : ctx.U32;
        const auto zero = is_float ? ctx.f32_zero_value : ctx.u32_zero_value;
        return ctx.OpCompositeConstruct(coord_type[2], operand, zero);
    }
    return operand;
}

struct ImageOperands {
    void Add(spv::ImageOperandsMask new_mask, Id value) {
        if (!Sirit::ValidId(value)) {
            return;
        }
        mask = static_cast<spv::ImageOperandsMask>(static_cast<u32>(mask) |
                                                   static_cast<u32>(new_mask));
        operands.push_back(value);
    }
    void Add(spv::ImageOperandsMask new_mask, Id value1, Id value2) {
        mask = static_cast<spv::ImageOperandsMask>(static_cast<u32>(mask) |
                                                   static_cast<u32>(new_mask));
        operands.push_back(value1);
        operands.push_back(value2);
    }

    void AddOffset(EmitContext& ctx, AmdGpu::ImageType image_type, const IR::Value& offset,
                   bool can_use_runtime_offsets = false, bool force_y_zero = false) {
        if (offset.IsEmpty()) {
            return;
        }
        const bool two_components = image_type == AmdGpu::ImageType::Color2D ||
                                    image_type == AmdGpu::ImageType::Color2DArray ||
                                    image_type == AmdGpu::ImageType::Cube;
        spv::ImageOperandsMask op_mask{};
        Id value{};
        if (offset.IsImmediate()) {
            op_mask = spv::ImageOperandsMask::ConstOffset;
            value = ctx.ConstS32(static_cast<s32>(offset.U32()));
        } else {
            IR::Inst* const inst{offset.Inst()};
            if (inst->AreAllArgsImmediates()) {
                switch (inst->GetOpcode()) {
                case IR::Opcode::CompositeConstructU32x2:
                    op_mask = spv::ImageOperandsMask::ConstOffset;
                    value = ctx.ConstS32(static_cast<s32>(inst->Arg(0).U32()),
                                         force_y_zero ? 0 : static_cast<s32>(inst->Arg(1).U32()));
                    break;
                case IR::Opcode::CompositeConstructU32x3:
                    op_mask = spv::ImageOperandsMask::ConstOffset;
                    value = two_components
                                ? ctx.ConstS32(static_cast<s32>(inst->Arg(0).U32()),
                                               force_y_zero ? 0 : static_cast<s32>(inst->Arg(1).U32()))
                                : ctx.ConstS32(static_cast<s32>(inst->Arg(0).U32()),
                                               force_y_zero ? 0 : static_cast<s32>(inst->Arg(1).U32()),
                                               static_cast<s32>(inst->Arg(2).U32()));
                    break;
                default:
                    break;
                }
            }
        }

        if (op_mask != spv::ImageOperandsMask::ConstOffset) {
            if (!can_use_runtime_offsets && !ctx.profile.supports_image_sample_runtime_offset) {
                LOG_WARNING(Render_Vulkan,
                            "Runtime offset provided to unsupported image sample instruction");
                return;
            }
            op_mask = spv::ImageOperandsMask::Offset;
            value = ctx.Def(offset);
            if (two_components && offset.Type() == IR::Type::U32x3) {
                value = ctx.OpVectorShuffle(ctx.U32[2], value, value, 0u, 1u);
            }
            if (force_y_zero) {
                value = ctx.OpCompositeConstruct(ctx.U32[2],
                                                 ctx.OpCompositeExtract(ctx.U32[1], value, 0u),
                                                 ctx.u32_zero_value);
            }
            ctx.AddCapability(spv::Capability::ImageGatherExtended);
            if (!can_use_runtime_offsets) {
                LOG_INFO(Render_Vulkan, "Emitting runtime image sample offset: shader={:#x}",
                         ctx.info.pgm_hash);
            }
        }

        value = PadImageOperand<false>(ctx, value, image_type);
        Add(op_mask, value);
    }

    void AddDerivatives(EmitContext& ctx, AmdGpu::ImageType image_type, Id derivatives_dx,
                        Id derivatives_dy) {
        if (!Sirit::ValidId(derivatives_dx) || !Sirit::ValidId(derivatives_dy)) {
            return;
        }
        derivatives_dx = PadImageOperand<true>(ctx, derivatives_dx, image_type);
        derivatives_dy = PadImageOperand<true>(ctx, derivatives_dy, image_type);
        Add(spv::ImageOperandsMask::Grad, derivatives_dx, derivatives_dy);
    }

    spv::ImageOperandsMask mask{};
    boost::container::static_vector<Id, 4> operands;
};

Id EmitImageHandle(EmitContext& ctx, Id, Id) {
    UNREACHABLE_MSG("Unreachable instruction");
}

Id EmitImageSampleRaw(EmitContext& ctx, IR::Inst* inst, u32 handle, Id address1, Id address2,
                      Id address3, Id address4) {
    UNREACHABLE_MSG("Unreachable instruction");
}

Id EmitImageSampleImplicitLod(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords, Id bias,
                              const IR::Value& offset) {
    const auto& texture = ctx.images[handle & 0xFFFF];
    const Id image = ctx.OpLoad(texture.image_type, texture.id);
    const Id result_type = texture.data_types->Get(4);
    const Id sampler = ctx.OpLoad(ctx.sampler_type, ctx.samplers[handle >> 16]);
    const Id sampled_image = ctx.OpSampledImage(texture.sampled_type, image, sampler);
    const Id fixed_coords = FixImageCoords<true>(ctx, coords, texture.view_type);
    ImageOperands operands;
    operands.Add(spv::ImageOperandsMask::Bias, bias);
    operands.AddOffset(ctx, texture.view_type, offset);
    const Id sample = ctx.OpImageSampleImplicitLod(result_type, sampled_image, fixed_coords,
                                                   operands.mask, operands.operands);
    return texture.is_integer ? ctx.OpBitcast(ctx.F32[4], sample) : sample;
}

static Id LoadIndexedGuestSampler(EmitContext& ctx, u32 handle, Id bounded, u32 expected_count,
                                  bool is_integer) {
    const u32 sampler_index =
        is_integer ? ctx.info.samplers[handle >> 16].integer_binding : handle >> 16;
    ASSERT(sampler_index < ctx.samplers.size());
    const u32 sampler_count = ctx.sampler_array_sizes[sampler_index];
    if (sampler_count == 1u) {
        return ctx.OpLoad(ctx.sampler_type, ctx.samplers[sampler_index]);
    }
    ASSERT(ctx.profile.supports_sampler_array_nonuniform_indexing);
    ASSERT(sampler_count == expected_count);
    const Id pointer_type = ctx.TypePointer(spv::StorageClass::UniformConstant, ctx.sampler_type);
    const Id pointer =
        ctx.OpAccessChain(pointer_type, ctx.samplers[sampler_index], std::array{bounded});
    ctx.Decorate(pointer, spv::Decoration::NonUniform);
    const Id value = ctx.OpLoad(ctx.sampler_type, pointer);
    ctx.Decorate(value, spv::Decoration::NonUniform);
    return value;
}

static Id ConvertIndexedSample(EmitContext& ctx, Id raw, Id metadata) {
    const Id conversion = ctx.OpBitwiseAnd(
        ctx.U32[1],
        ctx.OpShiftRightLogical(ctx.U32[1], metadata,
                                ctx.ConstU32(IndexedImageMetadata::ConversionShift)),
        ctx.ConstU32(IndexedImageMetadata::ConversionMask));
    std::array<Id, 4> channels;
    for (u32 i = 0; i < channels.size(); ++i) {
        const Id bit = ctx.ConstU32(1u << (IndexedImageMetadata::ComponentShift + i));
        channels[i] = ctx.OpINotEqual(ctx.U1[1], ctx.OpBitwiseAnd(ctx.U32[1], metadata, bit),
                                      ctx.ConstU32(0u));
    }
    const Id real_channels = ctx.OpCompositeConstruct(ctx.U1[4], channels);
    const auto constant = [&](float value) {
        const Id scalar = ctx.ConstF32(value);
        return ctx.ConstantComposite(ctx.F32[4], scalar, scalar, scalar, scalar);
    };
    std::array<Id, 8> labels;
    std::array<Sirit::Literal, 8> literals;
    for (u32 i = 0; i < labels.size(); ++i) {
        labels[i] = ctx.OpLabel();
        literals[i] = i;
    }
    const Id merge = ctx.OpLabel();
    ctx.OpSelectionMerge(merge, spv::SelectionControlMask::MaskNone);
    ctx.OpSwitch(conversion, labels[0], literals, labels);
    std::array<Id, 16> incoming;
    for (u32 i = 0; i < labels.size(); ++i) {
        ctx.AddLabel(labels[i]);
        Id result = raw;
        Id defaults = raw;
        switch (AmdGpu::NumberConversion(i)) {
        case AmdGpu::NumberConversion::None:
            break;
        case AmdGpu::NumberConversion::UintToUscaled:
            result = ctx.OpConvertUToF(ctx.F32[4], ctx.OpBitcast(ctx.U32[4], raw));
            defaults = result;
            break;
        case AmdGpu::NumberConversion::SintToSscaled:
            result = ctx.OpConvertSToF(ctx.F32[4], ctx.OpBitcast(ctx.S32[4], raw));
            defaults = result;
            break;
        case AmdGpu::NumberConversion::UnormToUbnorm:
            result =
                ctx.OpFSub(ctx.F32[4], ctx.OpFMul(ctx.F32[4], raw, constant(2.f)), constant(1.f));
            break;
        case AmdGpu::NumberConversion::Sint8ToSnormNz:
        case AmdGpu::NumberConversion::Sint16ToSnormNz:
            defaults = ctx.OpConvertSToF(ctx.F32[4], ctx.OpBitcast(ctx.S32[4], raw));
            result = ctx.OpFDiv(
                ctx.F32[4],
                ctx.OpFAdd(ctx.F32[4], ctx.OpFMul(ctx.F32[4], defaults, constant(2.f)),
                           constant(1.f)),
                constant(i == u32(AmdGpu::NumberConversion::Sint8ToSnormNz) ? 255.f : 65535.f));
            break;
        case AmdGpu::NumberConversion::Uint32ToUnorm:
            defaults = ctx.OpConvertUToF(ctx.F32[4], ctx.OpBitcast(ctx.U32[4], raw));
            result = ctx.OpFDiv(ctx.F32[4], defaults, constant(float(UINT32_MAX)));
            break;
        case AmdGpu::NumberConversion::SrgbToNorm: {
            const Id upper =
                ctx.OpPow(ctx.F32[4],
                          ctx.OpFMul(ctx.F32[4], ctx.OpFAdd(ctx.F32[4], raw, constant(0.055f)),
                                     constant(1.f / 1.055f)),
                          constant(2.4f));
            const Id lower = ctx.OpFMul(ctx.F32[4], raw, constant(1.f / 12.92f));
            result =
                ctx.OpSelect(ctx.F32[4], ctx.OpFOrdGreaterThan(ctx.U1[4], raw, constant(0.04045f)),
                             upper, lower);
            break;
        }
        }
        result = ctx.OpSelect(ctx.F32[4], real_channels, result, defaults);
        incoming[i * 2] = result;
        incoming[i * 2 + 1] = labels[i];
        ctx.OpBranch(merge);
    }
    ctx.AddLabel(merge);
    return ctx.OpPhi(ctx.F32[4], incoming);
}

static Id SampleIndexedImage(EmitContext& ctx, u32 handle, Id gpu_texture_id, Id dimension,
                             const std::array<Id, 3>& coords, const std::array<Id, 3>& dx,
                             const std::array<Id, 3>& dy, const IR::Value& offset, Id lod = {}, Id is_1d = {}) {
    const auto& texture = ctx.images[handle & 0xffffu];
    ASSERT(texture.is_guest_indexed && texture.image_array_size >= 2u);
    const Id valid =
        ctx.OpULessThan(ctx.U1[1], gpu_texture_id, ctx.ConstU32(texture.image_array_size - 1u));
    const Id bounded = ctx.OpSelect(ctx.U32[1], valid, gpu_texture_id,
                                    ctx.ConstU32(texture.image_array_size - 1u));
    ctx.Decorate(bounded, spv::Decoration::NonUniform);
    const Id metadata_pointer =
        ctx.OpAccessChain(texture.indexed_metadata_pointer_type, texture.indexed_metadata,
                          std::array{ctx.ConstU32(0u), bounded});
    const Id metadata = ctx.OpLoad(ctx.U32[1], metadata_pointer);
    const Id numeric =
        ctx.OpBitwiseAnd(ctx.U32[1], metadata, ctx.ConstU32(IndexedImageMetadata::ClassMask));
    const u32 dimensions = texture.is_guest_mixed_indexed ? 3u : 1u;
    const u32 variants = dimensions * 3u;
    const Id selected = ctx.OpIAdd(ctx.U32[1], dimension,
                                   ctx.OpIMul(ctx.U32[1], numeric, ctx.ConstU32(dimensions)));
    boost::container::small_vector<Id, 9> labels;
    boost::container::small_vector<Sirit::Literal, 9> literals;
    for (u32 i = 0; i < variants; ++i) {
        labels.push_back(ctx.OpLabel());
        literals.push_back(i);
    }
    const Id merge = ctx.OpLabel();
    ctx.OpSelectionMerge(merge, spv::SelectionControlMask::MaskNone);
    ctx.OpSwitch(selected, labels[0], literals, labels);
    boost::container::small_vector<Id, 18> incoming;
    for (u32 i = 0; i < variants; ++i) {
        ctx.AddLabel(labels[i]);
        const u32 dim = i % dimensions;
        const u32 number = i / dimensions;
        const Id image_type = texture.indexed_types[i];
        const Id pointer_type = ctx.TypePointer(spv::StorageClass::UniformConstant, image_type);
        const Id pointer =
            ctx.OpAccessChain(pointer_type, texture.indexed_ids[i], std::array{bounded});
        ctx.Decorate(pointer, spv::Decoration::NonUniform);
        const Id image = ctx.OpLoad(image_type, pointer);
        ctx.Decorate(image, spv::Decoration::NonUniform);
        const Id sampler =
            LoadIndexedGuestSampler(ctx, handle, bounded, texture.image_array_size, number != 0);
        const auto view = dim == 0   ? AmdGpu::ImageType::Color2D
                          : dim == 1 ? AmdGpu::ImageType::Color3D
                                     : AmdGpu::ImageType::Cube;
        const Id fixed_coords = FixImageCoords<true>(ctx, coords[dim], view);
        const Id result_type = number == 0 ? ctx.F32[4] : number == 1 ? ctx.S32[4] : ctx.U32[4];
        const auto sample = [&](bool force_y_zero) {
            const Id sampled = ctx.OpSampledImage(ctx.TypeSampledImage(image_type), image, sampler);
            ctx.Decorate(sampled, spv::Decoration::NonUniform);
            ImageOperands operands;
            if (Sirit::ValidId(lod)) {
                operands.Add(spv::ImageOperandsMask::Lod, lod);
            } else if (Sirit::ValidId(dx[dim])) {
                operands.AddDerivatives(ctx, view, dx[dim], dy[dim]);
            } else {
                operands.Add(spv::ImageOperandsMask::Lod, ctx.ConstF32(0.f));
            }
            operands.AddOffset(ctx, view, offset, false, force_y_zero);
            return ctx.OpImageSampleExplicitLod(result_type, sampled, fixed_coords,
                                                operands.mask, operands.operands);
        };
        Id end_label = labels[i];
        Id raw;
        if (dim == 0 && Sirit::ValidId(is_1d) && !offset.IsEmpty()) {
            const Id one = ctx.OpLabel();
            const Id two = ctx.OpLabel();
            end_label = ctx.OpLabel();
            ctx.OpSelectionMerge(end_label, spv::SelectionControlMask::MaskNone);
            ctx.OpBranchConditional(is_1d, one, two);
            ctx.AddLabel(one);
            const Id one_sample = sample(true);
            ctx.OpBranch(end_label);
            ctx.AddLabel(two);
            const Id two_sample = sample(false);
            ctx.OpBranch(end_label);
            ctx.AddLabel(end_label);
            raw = ctx.OpPhi(result_type, std::array{one_sample, one, two_sample, two});
        } else {
            raw = sample(false);
        }
        const Id result = number == 0 ? raw : ctx.OpBitcast(ctx.F32[4], raw);
        incoming.push_back(result);
        incoming.push_back(end_label);
        ctx.OpBranch(merge);
    }
    ctx.AddLabel(merge);
    return ConvertIndexedSample(ctx, ctx.OpPhi(ctx.F32[4], incoming), metadata);
}

Id EmitImageSampleImplicitLodIndexed(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords,
                                     Id bias, const IR::Value& offset, Id gpu_texture_id) {
    const auto& texture = ctx.images[handle & 0xffffu];
    std::array<Id, 3> positions{coords};
    std::array<Id, 3> dx{};
    std::array<Id, 3> dy{};
    Id dimension = ctx.ConstU32(0u);
    Id is_1d{};
    if (texture.is_guest_mixed_indexed) {
        const Id word3 = ctx.OpBitcast(ctx.U32[1], ctx.OpCompositeExtract(ctx.F32[1], coords, 3u));
        const Id type = ctx.OpShiftRightLogical(ctx.U32[1], word3, ctx.ConstU32(28u));
        const Id is_3d =
            ctx.OpIEqual(ctx.U1[1], type, ctx.ConstU32(u32(AmdGpu::ImageType::Color3D)));
        const Id is_cube =
            ctx.OpIEqual(ctx.U1[1], type, ctx.ConstU32(u32(AmdGpu::ImageType::Cube)));
        is_1d = ctx.OpLogicalOr(
            ctx.U1[1], ctx.OpIEqual(ctx.U1[1], type, ctx.ConstU32(u32(AmdGpu::ImageType::Color1D))),
            ctx.OpIEqual(ctx.U1[1], type, ctx.ConstU32(u32(AmdGpu::ImageType::Color1DArray))));
        dimension = ctx.OpSelect(ctx.U32[1], is_3d, ctx.ConstU32(1u),
                                 ctx.OpSelect(ctx.U32[1], is_cube, ctx.ConstU32(2u), dimension));
        positions[0] = ctx.OpVectorShuffle(ctx.F32[2], coords, coords, 0u, 1u);
        positions[1] = ctx.OpVectorShuffle(ctx.F32[3], coords, coords, 0u, 1u, 2u);
        positions[2] = positions[1];
    }
    // Evaluate derivatives before the nonuniform numeric/dimension dispatch.
    if (ctx.info.hw_stage == HwStage::Fragment) {
        const Id scale = Sirit::ValidId(bias) ? ctx.OpExp2(ctx.F32[1], bias) : ctx.ConstF32(1.f);
        const u32 dimensions = texture.is_guest_mixed_indexed ? 3u : 1u;
        for (u32 i = 0; i < dimensions; ++i) {
            const u32 components = i == 1 ? 3u : 2u;
            const Id position = i == 2 ? positions[0] : positions[i];
            const Id scale_vector = components == 2
                                        ? ctx.OpCompositeConstruct(ctx.F32[2], scale, scale)
                                        : ctx.OpCompositeConstruct(ctx.F32[3], scale, scale, scale);
            dx[i] = ctx.OpFMul(ctx.F32[components], ctx.OpDPdx(ctx.F32[components], position),
                               scale_vector);
            dy[i] = ctx.OpFMul(ctx.F32[components], ctx.OpDPdy(ctx.F32[components], position),
                               scale_vector);
        }
    }
    if (texture.is_guest_mixed_indexed) {
        const Id x = ctx.OpCompositeExtract(ctx.F32[1], positions[0], 0u);
        positions[0] =
            ctx.OpSelect(ctx.F32[2], is_1d,
                         ctx.OpCompositeConstruct(ctx.F32[2], x, ctx.ConstF32(0.5f)), positions[0]);
        for (auto* gradient : {&dx[0], &dy[0]}) {
            if (Sirit::ValidId(*gradient)) {
                const Id x_grad = ctx.OpCompositeExtract(ctx.F32[1], *gradient, 0u);
                *gradient = ctx.OpSelect(
                    ctx.F32[2], is_1d,
                    ctx.OpCompositeConstruct(ctx.F32[2], x_grad, ctx.ConstF32(0.f)), *gradient);
            }
        }
    }
    return SampleIndexedImage(ctx, handle, gpu_texture_id, dimension, positions, dx, dy, offset);
}

Id EmitImageSampleExplicitLodIndexed(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords,
                                     Id lod, const IR::Value& offset, Id gpu_texture_id) {
    const auto& texture = ctx.images[handle & 0xffffu];
    std::array<Id, 3> positions{coords};
    Id dimension = ctx.ConstU32(0u);
    Id is_1d{};
    if (texture.is_guest_mixed_indexed) {
        const Id word3 = ctx.OpBitcast(ctx.U32[1], ctx.OpCompositeExtract(ctx.F32[1], coords, 3u));
        const Id type = ctx.OpShiftRightLogical(ctx.U32[1], word3, ctx.ConstU32(28u));
        const Id is_3d =
            ctx.OpIEqual(ctx.U1[1], type, ctx.ConstU32(u32(AmdGpu::ImageType::Color3D)));
        const Id is_cube =
            ctx.OpIEqual(ctx.U1[1], type, ctx.ConstU32(u32(AmdGpu::ImageType::Cube)));
        is_1d = ctx.OpLogicalOr(
            ctx.U1[1], ctx.OpIEqual(ctx.U1[1], type, ctx.ConstU32(u32(AmdGpu::ImageType::Color1D))),
            ctx.OpIEqual(ctx.U1[1], type, ctx.ConstU32(u32(AmdGpu::ImageType::Color1DArray))));
        dimension = ctx.OpSelect(ctx.U32[1], is_3d, ctx.ConstU32(1u),
                                 ctx.OpSelect(ctx.U32[1], is_cube, ctx.ConstU32(2u), dimension));
        const Id x = ctx.OpCompositeExtract(ctx.F32[1], coords, 0u);
        positions[0] = ctx.OpSelect(
            ctx.F32[2], is_1d, ctx.OpCompositeConstruct(ctx.F32[2], x, ctx.ConstF32(0.5f)),
            ctx.OpVectorShuffle(ctx.F32[2], coords, coords, 0u, 1u));
        positions[1] = ctx.OpVectorShuffle(ctx.F32[3], coords, coords, 0u, 1u, 2u);
        positions[2] = positions[1];
    }
    return SampleIndexedImage(ctx, handle, gpu_texture_id, dimension, positions, {}, {}, offset,
                              lod, is_1d);
}

Id EmitImageSampleExplicitLod(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords, Id lod,
                              const IR::Value& offset) {
    const auto& texture = ctx.images[handle & 0xFFFF];
    const Id image = ctx.OpLoad(texture.image_type, texture.id);
    const Id result_type = texture.data_types->Get(4);
    const Id sampler = ctx.OpLoad(ctx.sampler_type, ctx.samplers[handle >> 16]);
    const Id sampled_image = ctx.OpSampledImage(texture.sampled_type, image, sampler);
    const Id fixed_coords = FixImageCoords<true>(ctx, coords, texture.view_type);
    ImageOperands operands;
    operands.Add(spv::ImageOperandsMask::Lod, lod);
    operands.AddOffset(ctx, texture.view_type, offset);
    const Id sample = ctx.OpImageSampleExplicitLod(result_type, sampled_image, fixed_coords,
                                                   operands.mask, operands.operands);
    return texture.is_integer ? ctx.OpBitcast(ctx.F32[4], sample) : sample;
}

Id EmitImageSampleDrefImplicitLod(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords, Id dref,
                                  Id bias, const IR::Value& offset) {
    const auto& texture = ctx.images[handle & 0xFFFF];
    const Id image = ctx.OpLoad(texture.image_type, texture.id);
    const Id result_type = texture.data_types->Get(1);
    const Id sampler = ctx.OpLoad(ctx.sampler_type, ctx.samplers[handle >> 16]);
    const Id sampled_image = ctx.OpSampledImage(texture.sampled_type, image, sampler);
    const Id fixed_coords = FixImageCoords<true>(ctx, coords, texture.view_type);
    ImageOperands operands;
    operands.Add(spv::ImageOperandsMask::Bias, bias);
    operands.AddOffset(ctx, texture.view_type, offset);
    const Id sample = ctx.OpImageSampleDrefImplicitLod(result_type, sampled_image, fixed_coords,
                                                       dref, operands.mask, operands.operands);
    const Id sample_typed = texture.is_integer ? ctx.OpBitcast(ctx.F32[1], sample) : sample;
    return ctx.OpCompositeConstruct(ctx.F32[4], sample_typed, ctx.f32_zero_value,
                                    ctx.f32_zero_value, ctx.f32_zero_value);
}

Id EmitImageSampleDrefExplicitLod(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords, Id dref,
                                  Id lod, const IR::Value& offset) {
    const auto& texture = ctx.images[handle & 0xFFFF];
    const Id image = ctx.OpLoad(texture.image_type, texture.id);
    const Id result_type = texture.data_types->Get(1);
    const Id sampler = ctx.OpLoad(ctx.sampler_type, ctx.samplers[handle >> 16]);
    const Id sampled_image = ctx.OpSampledImage(texture.sampled_type, image, sampler);
    const Id fixed_coords = FixImageCoords<true>(ctx, coords, texture.view_type);
    ImageOperands operands;
    operands.Add(spv::ImageOperandsMask::Lod, lod);
    operands.AddOffset(ctx, texture.view_type, offset);
    const Id sample = ctx.OpImageSampleDrefExplicitLod(result_type, sampled_image, fixed_coords,
                                                       dref, operands.mask, operands.operands);
    const Id sample_typed = texture.is_integer ? ctx.OpBitcast(ctx.F32[1], sample) : sample;
    return ctx.OpCompositeConstruct(ctx.F32[4], sample_typed, ctx.f32_zero_value,
                                    ctx.f32_zero_value, ctx.f32_zero_value);
}

Id EmitImageGather(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords,
                   const IR::Value& offset) {
    const auto& texture = ctx.images[handle & 0xFFFF];
    const Id image = ctx.OpLoad(texture.image_type, texture.id);
    const Id result_type = texture.data_types->Get(4);
    const Id sampler = ctx.OpLoad(ctx.sampler_type, ctx.samplers[handle >> 16]);
    const Id sampled_image = ctx.OpSampledImage(texture.sampled_type, image, sampler);
    const Id fixed_coords = FixImageCoords<true>(ctx, coords, texture.view_type);
    const u32 comp = inst->Flags<IR::TextureInstInfo>().gather_comp.Value();
    ImageOperands operands;
    operands.AddOffset(ctx, texture.view_type, offset, true);
    const Id texels = ctx.OpImageGather(result_type, sampled_image, fixed_coords,
                                        ctx.ConstU32(comp), operands.mask, operands.operands);
    return texture.is_integer ? ctx.OpBitcast(ctx.F32[4], texels) : texels;
}

Id EmitImageGatherDref(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords,
                       const IR::Value& offset, Id dref) {
    const auto& texture = ctx.images[handle & 0xFFFF];
    const Id image = ctx.OpLoad(texture.image_type, texture.id);
    const Id result_type = texture.data_types->Get(4);
    const Id sampler = ctx.OpLoad(ctx.sampler_type, ctx.samplers[handle >> 16]);
    const Id sampled_image = ctx.OpSampledImage(texture.sampled_type, image, sampler);
    const Id fixed_coords = FixImageCoords<true>(ctx, coords, texture.view_type);
    ImageOperands operands;
    operands.AddOffset(ctx, texture.view_type, offset, true);
    const Id texels = ctx.OpImageDrefGather(result_type, sampled_image, fixed_coords, dref,
                                            operands.mask, operands.operands);
    return texture.is_integer ? ctx.OpBitcast(ctx.F32[4], texels) : texels;
}

Id EmitImageQueryDimensions(EmitContext& ctx, IR::Inst* inst, u32 handle, Id lod, bool has_mips) {
    const auto& texture = ctx.images[handle & 0xFFFF];
    const Id image = ctx.OpLoad(texture.image_type, texture.id);
    const Id zero = ctx.u32_zero_value;
    const auto mips{[&] { return has_mips ? ctx.OpImageQueryLevels(ctx.U32[1], image) : zero; }};
    const bool uses_lod{texture.view_type != AmdGpu::ImageType::Color2DMsaa && !texture.is_storage};
    const auto query{[&](Id type) {
        return uses_lod ? ctx.OpImageQuerySizeLod(type, image, lod)
                        : ctx.OpImageQuerySize(type, image);
    }};
    switch (texture.view_type) {
    case AmdGpu::ImageType::Color1D: {
        const auto width = ctx.OpCompositeExtract(ctx.U32[1], query(ctx.U32[2]), 0U);
        return ctx.OpCompositeConstruct(ctx.U32[4], width, zero, zero, mips());
    }
    case AmdGpu::ImageType::Color1DArray: {
        const auto base = query(ctx.U32[3]);
        const auto width = ctx.OpCompositeExtract(ctx.U32[1], base, 0U);
        const auto slices = ctx.OpCompositeExtract(ctx.U32[1], base, 2U);
        return ctx.OpCompositeConstruct(ctx.U32[4], width, slices, zero, mips());
    }
    case AmdGpu::ImageType::Color2D:
    case AmdGpu::ImageType::Color2DMsaa:
        return ctx.OpCompositeConstruct(ctx.U32[4], query(ctx.U32[2]), zero, mips());
    case AmdGpu::ImageType::Color2DArray:
    case AmdGpu::ImageType::Cube:
    case AmdGpu::ImageType::Color3D:
        return ctx.OpCompositeConstruct(ctx.U32[4], query(ctx.U32[3]), mips());
    default:
        UNREACHABLE_MSG("SPIR-V Instruction");
    }
}

Id EmitImageQueryLod(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords) {
    const auto& texture = ctx.images[handle & 0xFFFF];
    const Id image = ctx.OpLoad(texture.image_type, texture.id);
    const Id sampler = ctx.OpLoad(ctx.sampler_type, ctx.samplers[handle >> 16]);
    const Id sampled_image = ctx.OpSampledImage(texture.sampled_type, image, sampler);
    const Id fixed_coords = FixImageCoords<true>(ctx, coords, texture.view_type);
    return ctx.OpImageQueryLod(ctx.F32[2], sampled_image, fixed_coords);
}

Id EmitImageGradient(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords, Id derivatives_dx,
                     Id derivatives_dy, const IR::Value& offset, const IR::Value& lod_clamp) {
    const auto& texture = ctx.images[handle & 0xFFFF];
    const Id image = ctx.OpLoad(texture.image_type, texture.id);
    const Id result_type = texture.data_types->Get(4);
    const Id sampler = ctx.OpLoad(ctx.sampler_type, ctx.samplers[handle >> 16]);
    const Id sampled_image = ctx.OpSampledImage(texture.sampled_type, image, sampler);
    const Id fixed_coords = FixImageCoords<true>(ctx, coords, texture.view_type);
    ImageOperands operands;
    operands.AddDerivatives(ctx, texture.view_type, derivatives_dx, derivatives_dy);
    operands.AddOffset(ctx, texture.view_type, offset);
    const Id sample = ctx.OpImageSampleExplicitLod(result_type, sampled_image, fixed_coords,
                                                   operands.mask, operands.operands);
    return texture.is_integer ? ctx.OpBitcast(ctx.F32[4], sample) : sample;
}

Id EmitImageGradientIndexed(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords,
                            Id derivatives_dx, Id derivatives_dy, const IR::Value& offset,
                            Id gpu_texture_id) {
    ASSERT(!ctx.images[handle & 0xffffu].is_guest_mixed_indexed);
    return SampleIndexedImage(ctx, handle, gpu_texture_id, ctx.ConstU32(0u), {coords},
                              {derivatives_dx}, {derivatives_dy}, offset);
}

Id EmitImageRead(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords, Id lod, Id ms) {
    const auto& texture = ctx.images[handle & 0xFFFF];
    const Id color_type = texture.data_types->Get(4);
    const Id fixed_coords = FixImageCoords<false>(ctx, coords, texture.view_type);
    ImageOperands operands;
    Id texel;
    if (!texture.is_storage) {
        const Id image = ctx.OpLoad(texture.image_type, texture.id);
        if (texture.view_type == AmdGpu::ImageType::Color2DMsaa) {
            // GCN hardware wraps out-of-range MSAA sample indices
            if (Sirit::ValidId(ms)) {
                const Id sample_count = ctx.OpImageQuerySamples(ctx.U32[1], image);
                const Id wrapped_ms = ctx.OpUMod(ctx.U32[1], ms, sample_count);
                operands.Add(spv::ImageOperandsMask::Sample, wrapped_ms);
            }
        } else {
            if (Sirit::ValidId(ms)) {
                LOG_ERROR(Render_Recompiler, "image is not MS but ms operand is provided");
            }
            operands.Add(spv::ImageOperandsMask::Lod, lod);
            operands.Add(spv::ImageOperandsMask::Sample, ms);
        }
        texel = ctx.OpImageFetch(color_type, image, fixed_coords, operands.mask, operands.operands);
    } else {
        Id image_ptr = texture.id;
        if (ctx.profile.supports_image_load_store_lod) {
            operands.Add(spv::ImageOperandsMask::Lod, lod);
        } else if (Sirit::ValidId(lod)) {
#if 1
            // It's confusing what interactions will cause this code path so leave it as
            // unreachable until a case is found.
            // Normally IMAGE_LOAD_MIP should translate -> OpImageFetch
            UNREACHABLE_MSG("Unsupported ImageRead with Lod");
#else
            LOG_WARNING(Render, "Fallback for ImageRead with LOD");
            ASSERT(texture.mip_fallback_mode == MipStorageFallbackMode::DynamicIndex);
            const Id single_image_ptr_type =
                ctx.TypePointer(spv::StorageClass::UniformConstant, texture.image_type);
            image_ptr = ctx.OpAccessChain(single_image_ptr_type, image_ptr, std::array{lod});
#endif
        }
        const Id image = ctx.OpLoad(texture.image_type, image_ptr);
        texel = ctx.OpImageRead(color_type, image, fixed_coords, operands.mask, operands.operands);
    }
    return texture.is_integer ? ctx.OpBitcast(ctx.F32[4], texel) : texel;
}

void EmitImageWrite(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords, Id lod, Id ms,
                    Id color) {
    const auto& texture = ctx.images[handle & 0xFFFF];
    Id image_ptr = texture.id;
    const Id color_type = texture.data_types->Get(4);
    const Id fixed_coords = FixImageCoords<false>(ctx, coords, texture.view_type);
    ImageOperands operands;
    operands.Add(spv::ImageOperandsMask::Sample, ms);
    if (ctx.profile.supports_image_load_store_lod) {
        operands.Add(spv::ImageOperandsMask::Lod, lod);
    } else if (Sirit::ValidId(lod)) {
        LOG_WARNING(Render, "Fallback for ImageWrite with LOD");
        ASSERT(texture.mip_fallback_mode == MipStorageFallbackMode::DynamicIndex);
        const Id single_image_ptr_type =
            ctx.TypePointer(spv::StorageClass::UniformConstant, texture.image_type);
        image_ptr = ctx.OpAccessChain(single_image_ptr_type, image_ptr, std::array{lod});
    }
    const Id image = ctx.OpLoad(texture.image_type, image_ptr);
    const Id texel = texture.is_integer ? ctx.OpBitcast(color_type, color) : color;
    ctx.OpImageWrite(image, fixed_coords, texel, operands.mask, operands.operands);
}

static Id SelectCubeResult(EmitContext& ctx, Id x, Id y, Id z, Id x_res, Id y_res, Id z_res) {
    const auto abs_x = ctx.OpFAbs(ctx.F32[1], x);
    const auto abs_y = ctx.OpFAbs(ctx.F32[1], y);
    const auto abs_z = ctx.OpFAbs(ctx.F32[1], z);

    const auto z_face_cond{ctx.OpLogicalAnd(ctx.U1[1],
                                            ctx.OpFOrdGreaterThanEqual(ctx.U1[1], abs_z, abs_x),
                                            ctx.OpFOrdGreaterThanEqual(ctx.U1[1], abs_z, abs_y))};
    const auto y_face_cond{ctx.OpFOrdGreaterThanEqual(ctx.U1[1], abs_y, abs_x)};

    return ctx.OpSelect(ctx.F32[1], z_face_cond, z_res,
                        ctx.OpSelect(ctx.F32[1], y_face_cond, y_res, x_res));
}

Id EmitCubeFaceIndex(EmitContext& ctx, IR::Inst* inst, Id x, Id y, Id z) {
    if (ctx.profile.supports_native_cube_calc) {
        return ctx.OpCubeFaceIndexAMD(ctx.F32[1], ctx.OpCompositeConstruct(ctx.F32[3], x, y, z));
    }

    const auto x_neg_cond{ctx.OpFOrdLessThan(ctx.U1[1], x, ctx.f32_zero_value)};
    const auto y_neg_cond{ctx.OpFOrdLessThan(ctx.U1[1], y, ctx.f32_zero_value)};
    const auto z_neg_cond{ctx.OpFOrdLessThan(ctx.U1[1], z, ctx.f32_zero_value)};
    const auto x_face{ctx.OpSelect(ctx.F32[1], x_neg_cond, ctx.ConstF32(1.f), ctx.ConstF32(0.f))};
    const auto y_face{ctx.OpSelect(ctx.F32[1], y_neg_cond, ctx.ConstF32(3.f), ctx.ConstF32(2.f))};
    const auto z_face{ctx.OpSelect(ctx.F32[1], z_neg_cond, ctx.ConstF32(5.f), ctx.ConstF32(4.f))};

    return SelectCubeResult(ctx, x, y, z, x_face, y_face, z_face);
}

Id EmitCubeFaceCoordS(EmitContext& ctx, IR::Inst* inst, Id x, Id y, Id z) {
    const auto x_neg_cond{ctx.OpFOrdLessThan(ctx.U1[1], x, ctx.f32_zero_value)};
    const auto z_neg_cond{ctx.OpFOrdLessThan(ctx.U1[1], z, ctx.f32_zero_value)};
    const auto x_sc{ctx.OpSelect(ctx.F32[1], x_neg_cond, z, ctx.OpFNegate(ctx.F32[1], z))};
    const auto y_sc{x};
    const auto z_sc{ctx.OpSelect(ctx.F32[1], z_neg_cond, ctx.OpFNegate(ctx.F32[1], x), x)};

    return SelectCubeResult(ctx, x, y, z, x_sc, y_sc, z_sc);
}

Id EmitCubeFaceCoordT(EmitContext& ctx, IR::Inst* inst, Id x, Id y, Id z) {
    const auto y_neg_cond{ctx.OpFOrdLessThan(ctx.U1[1], y, ctx.f32_zero_value)};
    const auto x_z_tc{ctx.OpFNegate(ctx.F32[1], y)};
    const auto y_tc{ctx.OpSelect(ctx.F32[1], y_neg_cond, ctx.OpFNegate(ctx.F32[1], z), z)};

    return SelectCubeResult(ctx, x, y, z, x_z_tc, y_tc, x_z_tc);
}

Id EmitCubeFaceMajorAxis(EmitContext& ctx, IR::Inst* inst, Id x, Id y, Id z) {
    const auto two{ctx.ConstF32(2.f)};
    const auto x_major_axis{ctx.OpFMul(ctx.F32[1], x, two)};
    const auto y_major_axis{ctx.OpFMul(ctx.F32[1], y, two)};
    const auto z_major_axis{ctx.OpFMul(ctx.F32[1], z, two)};

    return SelectCubeResult(ctx, x, y, z, x_major_axis, y_major_axis, z_major_axis);
}

} // namespace Shader::Backend::SPIRV
