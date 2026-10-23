// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <span>
#include <unordered_map>
#include "common/assert.h"
#include "shader_recompiler/ir/ir_emitter.h"
#include "shader_recompiler/ir/passes/ir_passes.h"
#include "shader_recompiler/ir/passes/image_phi_preflight.h"
#include "shader_recompiler/ir/passes/indexed_image_selector.h"
#include "shader_recompiler/ir/passes/resource_pass.h"
#include "shader_recompiler/ir/program.h"
#include "shader_recompiler/profile.h"
#include "shader_recompiler/resource.h"

namespace Shader::Optimization {

static bool IsSharpSource(const IR::Inst* inst) {
    return inst->GetOpcode() == IR::Opcode::GetUserData ||
           inst->GetOpcode() == IR::Opcode::ReadConst ||
           inst->GetOpcode() == IR::Opcode::ReadConstBuffer;
}

struct StridePatchResult {
    IR::Value vsharp_dw1;
    u32 dw1_mask;
    bool found;
};
StridePatchResult CheckStridePatchPattern(IR::Value value) {
    // s_or_b32        s37, s101, 0x400000
    // s_mov_b32       s36, s100
    // s_mov_b32       s38, -1
    // s_mov_b32       s39, 0x2000c004
    // s_movk_i32      s0, 0x6d0
    // buffer_load_dwordx4 v[17:20], v59, s[36:39], s0 idxen

    auto* inst = value.TryInst();
    if (!inst) {
        return {value, 0, false};
    }
    if (inst->GetOpcode() != IR::Opcode::BitwiseOr32 || !inst->Arg(1).IsImmediate()) {
        return {value, 0, false};
    }
    return {inst->Arg(0), inst->Arg(1).U32(), true};
}

struct ClearStrideAndPatchRecordsResult {
    IR::Value vsharp_dw1;
    IR::Value vsharp_dw2;
    u32 records_shift;
    bool found;
};
ClearStrideAndPatchRecordsResult CheckClearStrideAndPatchRecordsPattern(IR::Value dword1,
                                                                        IR::Value dword2) {
    // s_and_b32       s21, s21, 0xffff
    // s_lshl_b32      s22, s22, <imm>
    // s_buffer_load_dwordx4 s[20:23], s[20:23], 0x0

    auto* dw1_inst = dword1.TryInst();
    if (!dw1_inst) {
        return {dword1, dword2, false};
    }
    if (dw1_inst->GetOpcode() != IR::Opcode::BitwiseAnd32 || !dw1_inst->Arg(1).IsImmediate() ||
        dw1_inst->Arg(1).U32() != 0xffffu) {
        return {dword1, dword2, false};
    }
    auto* dw2_inst = dword2.TryInst();
    if (!dw2_inst) {
        return {dword1, dword2, false};
    }
    if (dw2_inst->GetOpcode() != IR::Opcode::ShiftLeftLogical32 ||
        !dw2_inst->Arg(1).IsImmediate()) {
        return {dword1, dword2, false};
    }

    return {dw1_inst->Arg(0), dw2_inst->Arg(0), dw2_inst->Arg(1).U32(), true};
}

struct InlineCbufResult {
    IR::Value vsharp_dw0;
    bool found;
};
InlineCbufResult CheckInlineCbufPattern(IR::Value value) {
    // Assuming V# is in s[32:35]
    // The next pattern:
    //  s_getpc_b64 s[32:33]
    //  s_add_u32   s32, <const>, s32
    //  s_addc_u32  s33, 0, s33
    //  s_mov_b32   s35, <const>
    //  s_movk_i32  s34, <const>
    //  buffer_load_format_xyz v[8:10], v1, s[32:35], 0 ...
    // is used to define an inline constant buffer

    auto* inst = value.TryInst();
    if (!inst) {
        return {value, false};
    }
    if (inst->GetOpcode() != IR::Opcode::IAdd32 ||
        inst->Arg(0).IsImmediate() == inst->Arg(1).IsImmediate()) {
        return {value, false};
    }
    const u32 offset = inst->Arg(0).IsImmediate() ? inst->Arg(0).U32() : inst->Arg(1).U32();
    auto* prod = inst->Arg(0).IsImmediate() ? inst->Arg(1).Inst() : inst->Arg(0).Inst();
    if (prod->GetOpcode() != IR::Opcode::GetPcLo) {
        return {value, false};
    }
    return {IR::Value{prod->Arg(0).U32() + offset}, true};
}

struct CubeTo2DArrayResult {
    IR::Value tsharp_dw3;
    IR::Value tsharp_dw4;
    bool found;
};
CubeTo2DArrayResult CheckCubeTo2DArrayPattern(IR::Value dword3, IR::Value dword4) {
    // Assuming T# is in s[12:19]
    // The next pattern:
    //  s_bfe_u32       vcc_lo, s16, 0xd0000
    //  s_mul_i32       vcc_lo, vcc_lo, 6
    //  s_add_i32       vcc_lo, vcc_lo, 5
    //  s_and_b32       vcc_hi, 0x1fff, vcc_lo
    //  s_and_b32       vcc_lo, s16, 0xffffe000
    //  s_or_b32        s16, vcc_hi, vcc_lo
    //  s_and_b32       vcc_lo, s15, 0xfffffff
    //  s_or_b32        s15, 0xd0000000, vcc_lo
    //  image_load_mip  v[4:7], v[0:3], s[12:19] dmask:15 da
    // is used to convert the resource from cubemap to 2d-array

    // Check pattern that forces type to Color2DArray
    auto* dw3_inst = dword3.TryInst();
    if (!dw3_inst) {
        return {dword3, dword4, false};
    }

    if (dw3_inst->GetOpcode() != IR::Opcode::BitwiseOr32 || dw3_inst->Arg(1).IsImmediate() ||
        !dw3_inst->Arg(0).IsImmediate() || dw3_inst->Arg(0).U32() != 0xd0000000u) {
        return {dword3, dword4, false};
    }

    auto* dw3_and = dw3_inst->Arg(1).Inst();
    if (dw3_and->GetOpcode() != IR::Opcode::BitwiseAnd32 || !dw3_and->Arg(1).IsImmediate() ||
        dw3_and->Arg(1).U32() != 0xfffffffu) {
        return {dword3, dword4, false};
    }

    // Check pattern that translates cubemap depth to slices
    auto* dw4_inst = dword4.TryInst();
    if (!dw4_inst) {
        return {dword3, dword4, false};
    }

    if (dw4_inst->GetOpcode() != IR::Opcode::BitwiseOr32 || dw4_inst->Arg(0).IsImmediate() ||
        dw4_inst->Arg(1).IsImmediate()) {
        return {dword3, dword4, false};
    }

    auto* dw4_rhs = dw4_inst->Arg(1).Inst();
    if (dw4_rhs->GetOpcode() != IR::Opcode::BitwiseAnd32 || !dw4_rhs->Arg(1).IsImmediate() ||
        dw4_rhs->Arg(1).U32() != 0xffffe000u) {
        return {dword3, dword4, false};
    }

    return {dw3_and->Arg(0), dw4_rhs->Arg(0), true};
}

struct AnisoLod0Result {
    IR::Value ssharp_dw0;
    IR::Value tsharp_dw3;
    bool found;
};
AnisoLod0Result CheckDisableAnisoLod0Pattern(IR::Value value) {
    // Assuming S# is in s[12:15] and T# is in s[4:11]
    // The next pattern:
    //  s_bfe_u32     s0, s7,  $0x0008000c
    //  s_and_b32     s1, s12, $0xfffff1ff
    //  s_cmp_eq_u32  s0, 0
    //  s_cselect_b32 s0, s1, s12
    //  s_mov_b32     s12, s0
    // is used to disable anisotropy in the sampler if the sampled texture doesn't have mips

    auto* inst = value.TryInst();
    if (!inst) {
        return {value, {}, false};
    }

    if (inst->GetOpcode() != IR::Opcode::SelectU32) {
        return {value, {}, false};
    }

    // Select should be based on zero check
    const auto* prod0 = inst->Arg(0).Inst();
    if (prod0->GetOpcode() != IR::Opcode::IEqual32 ||
        !(prod0->Arg(1).IsImmediate() && prod0->Arg(1).U32() == 0u)) {
        return {value, {}, false};
    }

    auto* prod0_arg0 = prod0->Arg(0).Inst();
    ASSERT(prod0_arg0->GetOpcode() != IR::Opcode::Phi);

    // The bits range is for lods (note that constants are changed after constant propagation pass)
    if (prod0_arg0->GetOpcode() != IR::Opcode::BitFieldUExtract ||
        !(prod0_arg0->Arg(1).IsImmediate() && prod0_arg0->Arg(1).U32() == 12) ||
        !(prod0_arg0->Arg(2).IsImmediate() && prod0_arg0->Arg(2).U32() == 8)) {
        return {value, {}, false};
    }

    // Make sure mask is masking out anisotropy
    const auto* prod1 = inst->Arg(1).Inst();
    if (prod1->GetOpcode() != IR::Opcode::BitwiseAnd32 || prod1->Arg(1).U32() != 0xfffff1ff) {
        return {value, {}, false};
    }

    // We're working on the first dword of S#
    auto* prod2 = inst->Arg(2).Inst();
    ASSERT(prod2->GetOpcode() != IR::Opcode::Phi);
    return {inst->Arg(2), prod0_arg0->Arg(0), true};
}

struct SamplerPatchResult {
    IR::Value ssharp_dw0;
    bool found;
};
SamplerPatchResult CheckForceClampToWrapPattern(IR::Value value) {
    // s_and_b32 s12, 0xfffffe00, s12
    // is used to force clamp_x/y/z to wrap

    auto* inst = value.TryInst();
    if (!inst) {
        return {value, false};
    }

    if (inst->GetOpcode() != IR::Opcode::BitwiseAnd32 || !inst->Arg(0).IsImmediate() ||
        inst->Arg(0).U32() != 0xfffffe00u) {
        return {value, false};
    }

    return {inst->Arg(1), true};
}

SamplerPatchResult CheckForceClampToLastTexelPattern(IR::Value value) {
    // s_and_b32 s12, 0xfffffe00, s12
    // s_or_b32  0x12, s12
    // is used to force clamp_x/y to last texel

    auto* inst = value.TryInst();
    if (!inst) {
        return {value, false};
    }

    if (inst->GetOpcode() != IR::Opcode::BitwiseOr32 || inst->Arg(1).IsImmediate() ||
        !inst->Arg(0).IsImmediate() || inst->Arg(0).U32() != 0x12u) {
        return {value, false};
    }

    auto* prod = inst->Arg(1).Inst();
    if (prod->GetOpcode() != IR::Opcode::BitwiseAnd32 || !prod->Arg(0).IsImmediate() ||
        prod->Arg(0).U32() != 0xfffffe00u) {
        return {value, false};
    }

    return {prod->Arg(1), true};
}

SamplerPatchResult CheckClearAnisoRatioAndThresholdPattern(IR::Value value) {
    // s_and_b32 s12, s12, 0xfff8f1ff
    // is used to clear anisotropy ratio and threshold fields

    auto* inst = value.TryInst();
    if (!inst) {
        return {value, false};
    }

    if (inst->GetOpcode() != IR::Opcode::BitwiseAnd32 || !inst->Arg(1).IsImmediate() ||
        inst->Arg(1).U32() != 0xfff8f1ffu) {
        return {value, false};
    }

    return {inst->Arg(0), true};
}

IR::Inst* FindSharpSource(IR::Inst* handle) {
    ASSERT(IsSharpSource(handle));
    return handle;
}

// Preserve exact guest AND masks on descriptor words. Only peel a chain when it
// terminates in a real static sharp source; Phi/attribute/dynamic expressions remain untouched.
static void PeelSharpDwordMasks(SharpReference& sharp) {
    for (size_t i = 0; i < sharp.num_dwords; ++i) {
        IR::Value producer = sharp.dwords[i];
        u32 cleared = 0;
        bool had_mask = false;
        IR::Inst* node = producer.TryInst();
        for (unsigned depth = 0; node && depth < 32; ++depth) {
            if (node->GetOpcode() != IR::Opcode::BitwiseAnd32) break;
            const IR::Value left = node->Arg(0), right = node->Arg(1);
            if (left.IsImmediate() == right.IsImmediate()) break;
            const IR::Value constant = left.IsImmediate() ? left : right;
            const IR::Value other = left.IsImmediate() ? right : left;
            if (constant.Type() != IR::Type::U32) break;
            auto* next = other.TryInst();
            if (!next) break;
            if (!IsSharpSource(next) && next->GetOpcode() != IR::Opcode::BitwiseAnd32) break;
            cleared |= ~constant.U32();
            producer = other;
            node = next;
            had_mask = true;
        }
        if (had_mask && node && IsSharpSource(node)) {
            sharp.dwords[i] = producer;
            sharp.clear_masks[i] |= cleared;
        }
    }
}

#include "shader_recompiler/ir/passes/image_phi_selection.inc"

void MarkReadConstBufferSharpSources(const SharpReference& sharp) {
    // In cases of bindless sharp fetches mark all producer instructions
    // so the extended userdata flattening pass will include them.
    for (size_t i = 0; i < sharp.num_dwords; i++) {
        IR::Inst* source = sharp.dwords[i].TryInst();
        if (!source) {
            continue;
        }
        ASSERT(IsSharpSource(source));
        if (source->GetOpcode() == IR::Opcode::ReadConstBuffer) {
            auto flags = source->Flags<IR::BufferInstInfo>();
            flags.sharp_source.Assign(1u);
            source->SetFlags(flags);
        }
    }
}

void DiscoverBufferSharp(IR::Block& block, IR::Inst& inst, ResourceDiscoveryList& resources) {
    IR::Inst* handle = inst.Arg(0).Inst();
    auto& resource = resources.emplace_back(&inst);
    auto& vsharp = resource.sharps[0];

    // Gather V# dwords
    vsharp.num_dwords = handle->NumArgs();
    for (size_t i = 0; i < handle->NumArgs(); ++i) {
        vsharp.dwords[i] = handle->Arg(i);
        if (auto* inst = vsharp.dwords[i].TryInst();
            inst && inst->GetOpcode() == IR::Opcode::ReadFirstLane) {
            vsharp.dwords[i] = inst->Arg(0);
        }
    }

    // Attempt to "see through" various V# access patterns and have binding reproduce them
    if (auto [dword0, found] = CheckInlineCbufPattern(vsharp.dwords[0]); found) {
        vsharp.post_op = SharpFetchPostOp::OffsetByProgramBase;
        vsharp.dwords[0] = dword0;
        vsharp.dwords[1] = IR::Value{0U};
    } else if (auto [dword1, mask, found] = CheckStridePatchPattern(handle->Arg(1)); found) {
        vsharp.post_op = SharpFetchPostOp::BitwiseOrDw1WithImm;
        vsharp.post_op_data.dw1_mask = mask;
        vsharp.dwords[1] = dword1;
    } else if (auto [dword1, dword2, shift, found] =
                   CheckClearStrideAndPatchRecordsPattern(handle->Arg(1), handle->Arg(2));
               found) {
        vsharp.post_op = SharpFetchPostOp::ClearStrideAndBitshiftDw2WithImm;
        vsharp.post_op_data.dw2_shift = shift;
        vsharp.dwords[1] = dword1;
        vsharp.dwords[2] = dword2;
    }

    PeelSharpDwordMasks(vsharp);
    MarkReadConstBufferSharpSources(vsharp);
}

void DiscoverImageSharp(IR::Block& block, IR::Inst& inst, ResourceDiscoveryList& resources) {
    IR::Inst* image_handle = inst.Arg(0).Inst();
    ASSERT(image_handle->GetOpcode() == IR::Opcode::ImageHandle);
    auto& resource = resources.emplace_back(&inst);
    auto& tsharp = resource.sharps[0];

    // Gather T# dwords
    IR::Inst* tsharp_low = image_handle->Arg(0).Inst();
    for (size_t i = 0; i < tsharp_low->NumArgs(); ++i) {
        tsharp.dwords[tsharp.num_dwords++] = tsharp_low->Arg(i);
    }
    if (auto tsharp_high = image_handle->Arg(1).TryInst()) {
        for (size_t i = 0; i < tsharp_high->NumArgs(); ++i) {
            tsharp.dwords[tsharp.num_dwords++] = tsharp_high->Arg(i);
        }
    }

    if (auto [tsharp_dw3, tsharp_dw4, found] =
            CheckCubeTo2DArrayPattern(tsharp.dwords[3], tsharp.dwords[4]);
        found) {
        tsharp.post_op = SharpFetchPostOp::ConvertCubeTo2DArray;
        tsharp.dwords[3] = tsharp_dw3;
        tsharp.dwords[4] = tsharp_dw4;
    }

    PeelSharpDwordMasks(tsharp);
    MarkReadConstBufferSharpSources(tsharp);

    if (inst.GetOpcode() != IR::Opcode::ImageSampleRaw) {
        return;
    }

    // Gather S# dwords
    const IR::Inst* sampler = inst.Arg(1).Inst();
    auto& ssharp = resource.sharps[1];
    for (size_t i = 0; i < sampler->NumArgs(); ++i) {
        ssharp.dwords[ssharp.num_dwords++] = sampler->Arg(i);
    }
    if (auto [ssharp_dw0, tsharp_dw3, found] = CheckDisableAnisoLod0Pattern(ssharp.dwords[0]);
        found) {
        ssharp.post_op = SharpFetchPostOp::DisableAnisoIfSingleLod;
        ssharp.post_op_data.lod_prod = tsharp_dw3;
        ssharp.dwords[0] = ssharp_dw0;
    } else if (auto [ssharp_dw0, found] = CheckForceClampToWrapPattern(ssharp.dwords[0]); found) {
        ssharp.post_op = SharpFetchPostOp::ForceRepeatXyzClamp;
        ssharp.dwords[0] = ssharp_dw0;
    } else if (auto [ssharp_dw0, found] = CheckForceClampToLastTexelPattern(ssharp.dwords[0]);
               found) {
        ssharp.post_op = SharpFetchPostOp::ForceLastTexelXyClamp;
        ssharp.dwords[0] = ssharp_dw0;
    } else if (auto [ssharp_dw0, found] = CheckClearAnisoRatioAndThresholdPattern(ssharp.dwords[0]);
               found) {
        ssharp.post_op = SharpFetchPostOp::ClearAnisoRatioAndThreshold;
        ssharp.dwords[0] = ssharp_dw0;
    }

    PeelSharpDwordMasks(ssharp);
    MarkReadConstBufferSharpSources(ssharp);
}


struct IndexedImageTableSource {
    const IR::Inst* buffer{};
    const IR::Inst* selector{};
    u32 descriptor_stride_words{1};
    bool gpu_texture_id_indexed{};
    std::array<const IR::Inst*, 8> words{};
    std::array<u32, 8> addends{};
    bool contiguous{};
};

static IndexedImageTableSource AnalyzeIndexedImageTable(const IR::Inst& sample) {
    IndexedImageTableSource out{};
    if (sample.GetOpcode() != IR::Opcode::ImageSampleRaw) {
        return out;
    }
    const auto* image = sample.Arg(0).TryInst();
    if (!image || image->GetOpcode() != IR::Opcode::ImageHandle) {
        return out;
    }
    const auto* low = image->Arg(0).TryInst();
    const auto* high = image->Arg(1).TryInst();
    if (!low || !high || low->NumArgs() != 4 || high->NumArgs() != 4) {
        return out;
    }
    std::array<IndexedSharpWord, 8> normalized{};
    for (std::size_t i = 0; i < 8; ++i) {
        const auto* read = (i < 4 ? low->Arg(i) : high->Arg(i - 4)).TryInst();
        if (!read || read->GetOpcode() != IR::Opcode::ReadConstBuffer) {
            return out;
        }
        const auto* buffer = read->Arg(0).TryInst();
        if (!buffer || buffer->GetOpcode() != IR::Opcode::CompositeConstructU32x4) {
            return out;
        }
        if (i == 0) {
            out.buffer = buffer;
        } else if (out.buffer != buffer) {
            return out;
        }
        out.words[i] = read;
        normalized[i] = AnalyzeIndexedSharpWord(read->Arg(1));
        if (!normalized[i].valid) {
            return out;
        }
        out.addends[i] = normalized[i].addend;
    }
    const auto* selector = static_cast<const IR::Inst*>(normalized[0].selector);
    if (!selector || !AreContiguousIndexedSharpWords(normalized)) {
        return out;
    }
    const auto identity = IdentifyTsharpSelector(selector);
    if (!identity.source) {
        return out;
    }
    out.selector = static_cast<const IR::Inst*>(identity.source);
    out.descriptor_stride_words = identity.descriptor_stride_words;
    out.gpu_texture_id_indexed = identity.normalized;
    out.contiguous = true;
    return out;
}

ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {
    // Finite control-flow descriptor choices are lowered to exact static
    // alternatives while the original GPU Phi selects the sampled result.
    std::size_t original_samples = 0;
    for (const IR::Block* const block : program.blocks) {
        for (const IR::Inst& inst : block->Instructions()) {
            original_samples += inst.GetOpcode() == IR::Opcode::ImageSampleRaw;
        }
    }
    const std::size_t resource_cap = std::min<std::size_t>(NUM_IMAGES, NUM_SAMPLERS);
    std::size_t image_split_budget =
        original_samples < resource_cap ? resource_cap - original_samples : 0;
    for (IR::Block* const block : program.blocks) {
        for (auto it = block->begin(); it != block->end();) {
            IR::Inst& inst = *it++;
            if (inst.GetOpcode() == IR::Opcode::ImageSampleRaw) {
                SplitImageSampleBySharpPhi(*block, inst, image_split_budget);
            }
        }
    }
    ResourceDiscoveryList sharp_usages;

    for (IR::Block* const block : program.blocks) {
        for (IR::Inst& inst : block->Instructions()) {
            if (IsBufferInstruction(inst)) {
                DiscoverBufferSharp(*block, inst, sharp_usages);
            } else if (IsImageInstruction(inst)) {
                DiscoverImageSharp(*block, inst, sharp_usages);
                if (inst.GetOpcode() == IR::Opcode::ImageSampleRaw &&
                    profile.supports_sampled_image_nonuniform_indexing) {
                    const auto table = AnalyzeIndexedImageTable(inst);
                    if (table.contiguous && table.gpu_texture_id_indexed &&
                        table.descriptor_stride_words == 8u) {
                        auto& usage = sharp_usages.back();
                        bool bindable = true;
                        for (std::size_t dw = 0; dw < 4; ++dw) {
                            const IR::Value value = table.buffer->Arg(dw);
                            if (!value.IsImmediate()) {
                                const auto* producer = value.TryInst();
                                bindable &= producer && IsSharpSource(producer);
                            }
                            usage.indexed_table_sharp.dwords[dw] = value;
                        }
                        if (bindable) {
                            usage.indexed_table_sharp.num_dwords = 4;
                            MarkReadConstBufferSharpSources(usage.indexed_table_sharp);
                            usage.indexed_texture_id =
                                IR::Value{const_cast<IR::Inst*>(table.selector)};
                            usage.has_indexed_table = true;
                        }
                    }
                }
            } else if (IsDataRingInstruction(inst)) {
                sharp_usages.emplace_back(&inst);
            }
        }
    }
    return sharp_usages;
}

} // namespace Shader::Optimization