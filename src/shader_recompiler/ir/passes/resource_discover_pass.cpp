// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cstdlib>
#include <cstdint>
#include <cstdlib>
#include <algorithm>
#include <span>
#include <unordered_map>

#include "common/assert.h"
#include "common/logging/log.h"
#include "shader_recompiler/ir/passes/image_phi_preflight.h"
#include "shader_recompiler/ir/ir_emitter.h"
#include "shader_recompiler/ir/passes/ir_passes.h"
#include "shader_recompiler/ir/passes/indexed_image_selector.h"
#include "shader_recompiler/ir/passes/srt_uniformity.h"
#include "video_core/renderer_vulkan/resource_audit_hash_filter.h"
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

// Retain exact guest bitwise AND masks on T#/S#/V# descriptor words.
// Only peel chains with an actual recognized sharp source at the end.
// Leave all unresolved Phi/attribute/dynamic descriptor producers alone.
void PeelSharpDwordMasks(SharpReference& sharp) {
    for (size_t i=0;i<sharp.num_dwords;++i) {
        IR::Value producer=sharp.dwords[i];
        u32 cleared=0;
        bool had_mask=false;
        IR::Inst* node=producer.TryInst();
        for (unsigned depth=0;node && depth<32;++depth) {
            if (node->GetOpcode()!=IR::Opcode::BitwiseAnd32) break;
            const IR::Value left=node->Arg(0),right=node->Arg(1);
            if (left.IsImmediate()==right.IsImmediate()) break;
            const IR::Value constant=left.IsImmediate()?left:right;
            const IR::Value other=left.IsImmediate()?right:left;
            if (constant.Type()!=IR::Type::U32) break;
            auto* next=other.TryInst();
            if (!next) break;
            if (!IsSharpSource(next) && next->GetOpcode()!=IR::Opcode::BitwiseAnd32) break;
            cleared |= ~constant.U32();
            producer=other;
            node=next;
            had_mask=true;
        }
        if (had_mask && node && IsSharpSource(node)) {
            sharp.dwords[i]=producer;
            sharp.clear_masks[i]|=cleared;
        }
    }
}

void MarkReadConstBufferSharpSources(const SharpReference& sharp) {
    // In cases of bindless sharp fetches mark all producer instructions
    // so the extended userdata flattening pass will include them.
    for (size_t i = 0; i < sharp.num_dwords; i++) {
        IR::Inst* source = sharp.dwords[i].TryInst();
        if (!source) {
            continue;
        }
        ASSERT_MSG(IsSharpSource(source),
                   "Unsupported descriptor producer opcode={} at dword={}",
                   static_cast<u32>(source->GetOpcode()), i);
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
    // Preserve the original guest IR handle, including any lane selectors or
    // bitwise descriptor operations, instead of the simplified host-sharp
    // representation that may have stripped ReadFirstLane.
    resource.original_buffer_handle = inst.Arg(0);
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

static bool IsSelectableBufferLoad(const IR::Inst& inst) {
    switch (inst.GetOpcode()) {
    case IR::Opcode::LoadBufferU32:
    case IR::Opcode::LoadBufferU32x2:
    case IR::Opcode::LoadBufferU32x3:
    case IR::Opcode::LoadBufferU32x4:
    case IR::Opcode::LoadBufferU64:
    case IR::Opcode::LoadBufferF32:
    case IR::Opcode::LoadBufferF32x2:
    case IR::Opcode::LoadBufferF32x3:
    case IR::Opcode::LoadBufferF32x4:
    case IR::Opcode::LoadBufferFormatF32:
        return true;
    default:
        return false;
    }
}

static bool IsEntryValue(const IR::Value& value) {
    return value.IsImmediate() || value.Inst()->GetParent()->ImmPredecessors().empty();
}

static bool CanRebuildSharpValue(const IR::Value& value, u32 depth) {
    if (IsEntryValue(value)) {
        return true;
    }
    const IR::Inst* const inst = value.Inst();
    switch (inst->GetOpcode()) {
    case IR::Opcode::GetUserData:
    case IR::Opcode::ReadConst:
    case IR::Opcode::CompositeConstructU32x2:
        break;
    default:
        return false;
    }
    if (depth == 0) {
        return false;
    }
    for (size_t i = 0; i < inst->NumArgs(); ++i) {
        if (!CanRebuildSharpValue(inst->Arg(i), depth - 1)) {
            return false;
        }
    }
    return true;
}

static IR::Value RebuildSharpValue(IR::Block& block, IR::Block::iterator insert_point,
                                   const IR::Value& value,
                                   std::unordered_map<const IR::Inst*, IR::Value>& rebuilt) {
    if (IsEntryValue(value)) {
        return value;
    }
    const IR::Inst* const inst = value.Inst();
    if (const auto it = rebuilt.find(inst); it != rebuilt.end()) {
        return it->second;
    }
    boost::container::small_vector<IR::Value, 2> args;
    for (size_t i = 0; i < inst->NumArgs(); ++i) {
        args.push_back(RebuildSharpValue(block, insert_point, inst->Arg(i), rebuilt));
    }
    IR::Inst* const copy = &*block.PrependNewInst(insert_point, *inst);
    for (size_t i = 0; i < args.size(); ++i) {
        copy->SetArg(i, args[i]);
    }
    return rebuilt.emplace(inst, IR::Value{copy}).first->second;
}

static IR::Value SelectSideResult(IR::IREmitter& ir, const IR::U1& condition,
                                  const IR::Value& true_value, const IR::Value& false_value) {
    size_t num_components = 0;
    switch (true_value.Type()) {
    case IR::Type::U32x2:
    case IR::Type::F32x2:
        num_components = 2;
        break;
    case IR::Type::U32x3:
    case IR::Type::F32x3:
        num_components = 3;
        break;
    case IR::Type::U32x4:
    case IR::Type::F32x4:
        num_components = 4;
        break;
    default:
        return ir.Select(condition, true_value, false_value);
    }
    boost::container::small_vector<IR::Value, 4> components;
    for (size_t i = 0; i < num_components; ++i) {
        components.push_back(ir.Select(condition, ir.CompositeExtract(true_value, i),
                                       ir.CompositeExtract(false_value, i)));
    }
    return ir.CompositeConstruct(std::span<const IR::Value>{components.data(), components.size()});
}

static void SplitBufferLoadBySharpPhi(IR::Block& block, IR::Inst& inst) {
    IR::Inst* const handle = inst.Arg(0).TryInst();
    if (!handle || handle->GetOpcode() != IR::Opcode::CompositeConstructU32x4) {
        return;
    }
    std::array<IR::Value, 4> dwords;
    std::array<bool, 4> from_phi{};
    const IR::Inst* first_phi{};
    for (size_t i = 0; i < dwords.size(); ++i) {
        dwords[i] = handle->Arg(i);
        if (const IR::Inst* const rfl = dwords[i].TryInst();
            rfl && rfl->GetOpcode() == IR::Opcode::ReadFirstLane) {
            dwords[i] = rfl->Arg(0);
        }
        const IR::Inst* const source = dwords[i].TryInst();
        if (!source || IsSharpSource(source)) {
            continue;
        }
        if (source->GetOpcode() != IR::Opcode::Phi) {
            return;
        }
        if (!first_phi) {
            first_phi = source;
        } else if (source->GetParent() != first_phi->GetParent() ||
                   source->NumArgs() != first_phi->NumArgs()) {
            return;
        }
        from_phi[i] = true;
    }
    if (!first_phi || first_phi->NumArgs() < 2) {
        return;
    }
    const size_t num_sides = first_phi->NumArgs();
    boost::container::small_vector<std::array<IR::Value, 4>, 4> sides(num_sides);
    for (size_t side = 0; side < num_sides; ++side) {
        IR::Block* const pred = first_phi->PhiBlock(side);
        for (size_t other = 0; other < side; ++other) {
            if (first_phi->PhiBlock(other) == pred) {
                return;
            }
        }
        for (size_t i = 0; i < dwords.size(); ++i) {
            if (!from_phi[i]) {
                sides[side][i] = dwords[i];
                continue;
            }
            const IR::Inst* const phi = dwords[i].Inst();
            size_t arg = 0;
            while (arg < phi->NumArgs() && phi->PhiBlock(arg) != pred) {
                ++arg;
            }
            if (arg == phi->NumArgs()) {
                return;
            }
            const IR::Value value = phi->Arg(arg);
            const IR::Inst* const side_source = value.TryInst();
            if ((side_source && !IsSharpSource(side_source)) || !CanRebuildSharpValue(value, 4)) {
                return;
            }
            sides[side][i] = value;
        }
    }
    IR::Block* const phi_block = first_phi->GetParent();
    IR::Inst* const side_index{&*phi_block->PrependNewInst(phi_block->begin(), IR::Opcode::Phi)};
    side_index->SetFlags(IR::Type::U32);
    for (size_t side = 0; side < num_sides; ++side) {
        side_index->AddPhiOperand(first_phi->PhiBlock(side), IR::Value{static_cast<u32>(side)});
    }
    const auto insert_point = IR::Block::InstructionList::s_iterator_to(inst);
    std::unordered_map<const IR::Inst*, IR::Value> rebuilt;
    boost::container::small_vector<IR::Value, 4> loads;
    for (size_t side = 0; side < num_sides; ++side) {
        std::array<IR::Value, 4> values;
        for (size_t i = 0; i < dwords.size(); ++i) {
            values[i] = from_phi[i]
                            ? RebuildSharpValue(block, insert_point, sides[side][i], rebuilt)
                            : sides[side][i];
        }
        IR::Inst* const side_handle = &*block.PrependNewInst(insert_point, *handle);
        for (size_t i = 0; i < values.size(); ++i) {
            side_handle->SetArg(i, values[i]);
        }
        IR::Inst* const load = &*block.PrependNewInst(insert_point, inst);
        load->SetArg(0, IR::Value{side_handle});
        loads.push_back(IR::Value{load});
    }
    IR::IREmitter ir{block, insert_point};
    IR::Value result = loads.back();
    for (size_t side = num_sides - 1; side-- > 0;) {
        const IR::U1 taken = ir.IEqual(IR::U32{side_index}, ir.Imm32(static_cast<u32>(side)));
        result = SelectSideResult(ir, taken, loads[side], result);
    }
    inst.ReplaceUsesWithAndRemove(result);
}

// A read-only image sample whose T#/S# is selected by a finite, direct SSA
// Phi can use the existing static Vulkan descriptors for each *exact*
// predecessor. The selection happens on the GPU after sampling, using a
// real Phi tracking the incoming block. No guest descriptor values are
// synthesized and arbitrary attribute/loop-dependent texture indexing
// remains on the existing dynamic path.
//
// Only handles up to 3 complete, statically discoverable branches, and
// only ImageSampleRaw (never writes or image atomics). The bound on side
// count prevents exponential shader/descriptor growth.
static bool SplitImageSampleBySharpPhi(IR::Block& block, IR::Inst& inst,
                                       std::size_t& remaining_budget,
                                       const Shader::Info& info) {
    if (inst.GetOpcode() != IR::Opcode::ImageSampleRaw || remaining_budget == 0) {
        return false;
    }
    const IR::Inst* const handle = inst.Arg(0).TryInst();
    if (!handle || handle->GetOpcode() != IR::Opcode::ImageHandle) {
        return false;
    }
    const IR::Inst* const low = handle->Arg(0).TryInst();
    const IR::Inst* const high = handle->Arg(1).TryInst();
    const IR::Inst* const sampler = inst.Arg(1).TryInst();
    if (!low || !high || !sampler || low->NumArgs() != 4 ||
        high->NumArgs() != 4 || sampler->NumArgs() != 4) {
        return false;
    }

    const std::array<const IR::Inst*, 3> pieces{low, high, sampler};
    std::array<IR::Value, 12> words{};
    std::array<bool, 12> comes_from_phi{};
    const IR::Inst* first_phi{};
    for (std::size_t k=0; k < words.size(); ++k) {
        const IR::Value v = pieces[k/4]->Arg(k%4);
        words[k]=v;
        if (v.IsImmediate()) {
            if (v.Type() != IR::Type::U32) return false;
            continue;
        }
        const IR::Inst* const producer = v.TryInst();
        if (!producer) return false;
        if (IsSharpSource(producer)) continue;
        if (producer->GetOpcode() != IR::Opcode::Phi) return false;
        if (!first_phi) {
            first_phi=producer;
        } else if (producer->GetParent() != first_phi->GetParent() ||
                   producer->NumArgs() != first_phi->NumArgs()) {
            return false;
        }
        comes_from_phi[k]=true;
    }
    if (!first_phi) return false;
    const std::size_t branches=first_phi->NumArgs();
    if (!CanSplitSharpImagePhi(branches, true, true, remaining_budget)) return false;

    // Reject repeated/mismatched predecessors and side expressions that
    // cannot be represented by existing static descriptor sources.
    std::array<IR::Block*, 3> predecessors{};
    for (std::size_t side=0; side<branches; ++side) {
        IR::Block* const predecessor=first_phi->PhiBlock(side);
        if (!predecessor) return false;
        for (std::size_t j=0; j<side; ++j) {
            if (predecessors[j]==predecessor) return false;
        }
        predecessors[side]=predecessor;
    }
    std::array<std::array<IR::Value, 12>, 3> selected{};
    for (std::size_t side=0; side<branches; ++side) {
        for (std::size_t word=0; word<words.size(); ++word) {
            if (!comes_from_phi[word]) {
                selected[side][word]=words[word];
                continue;
            }
            const IR::Inst* const phi=words[word].TryInst();
            std::size_t arg=0;
            while (arg<phi->NumArgs() && phi->PhiBlock(arg)!=predecessors[side]) ++arg;
            if (arg==phi->NumArgs()) return false;
            const IR::Value v=phi->Arg(arg);
            if (v.IsImmediate()) {
                if (v.Type()!=IR::Type::U32) return false;
            } else {
                const IR::Inst* const incoming=v.TryInst();
                if (!incoming || !IsSharpSource(incoming) ||
                    !CanRebuildSharpValue(v, 4)) return false;
            }
            selected[side][word]=v;
        }
    }

    IR::Block* const phi_block=first_phi->GetParent();
    IR::Inst* const selector=&*phi_block->PrependNewInst(phi_block->begin(),
                                                        IR::Opcode::Phi);
    selector->SetFlags(IR::Type::U32);
    for (std::size_t side=0; side<branches; ++side) {
        selector->AddPhiOperand(predecessors[side],
                                IR::Value{static_cast<u32>(side)});
    }
    const auto insert_point=IR::Block::InstructionList::s_iterator_to(inst);
    std::unordered_map<const IR::Inst*,IR::Value> rebuilt;
    boost::container::small_vector<IR::Value, 3> sampled;
    for (std::size_t side=0; side<branches; ++side) {
        std::array<IR::Inst*, 3> copies{};
        for (std::size_t group=0; group<3; ++group) {
            IR::Inst* const clone=&*block.PrependNewInst(insert_point,*pieces[group]);
            for (std::size_t word=0; word<4; ++word) {
                const std::size_t idx=group*4+word;
                const IR::Value v = comes_from_phi[idx]
                    ? RebuildSharpValue(block,insert_point,selected[side][idx],rebuilt)
                    : selected[side][idx];
                clone->SetArg(word,v);
            }
            copies[group]=clone;
        }
        IR::Inst* const image_handle=&*block.PrependNewInst(insert_point,*handle);
        image_handle->SetArg(0,IR::Value{copies[0]});
        image_handle->SetArg(1,IR::Value{copies[1]});
        IR::Inst* const read=&*block.PrependNewInst(insert_point,inst);
        read->SetArg(0,IR::Value{image_handle});
        read->SetArg(1,IR::Value{copies[2]});
        sampled.push_back(IR::Value{read});
    }
    IR::IREmitter ir{block,insert_point};
    IR::Value output=sampled.back();
    for (std::size_t side=branches-1;side-- > 0;) {
        const IR::U1 choose=ir.IEqual(IR::U32{selector},
                                      ir.Imm32(static_cast<u32>(side)));
        output=SelectSideResult(ir,choose,sampled[side],output);
    }
    inst.ReplaceUsesWithAndRemove(output);
    remaining_budget-=branches-1;
    static const bool audit=[]{
        const char* const flag=std::getenv("SHADPS4_IMAGE_PHI_SPLIT_AUDIT");
        return flag && flag[0]=='1' && flag[1]=='\0';
    }();
    if (audit) {
        // Shader modules may compile concurrently. Keep audit throttling
        // thread-local instead of racing on a shared non-atomic counter.
        static thread_local u32 reports=0;
        if (reports++<64) {
            LOG_INFO(Render_Recompiler,
                     "ImagePhiSplit: shader={:#x} stage={} alternatives={} "
                     "resource_budget_left={} mode=exact_predecessor_select",
                     info.pgm_hash,info.hw_stage,branches,remaining_budget);
        }
    }
    return true;
}

// Dynamic T#/S# tables need a GPU-selected descriptor binding. Before a
// general bindless table is available, one exact and semantically safe case
// can be lowered with ordinary Vulkan bindings: a shader SelectU32 choosing
// between TWO constant descriptor indices into the SAME genuine scalar table.
// We sample both real guest descriptors and select the result using the
// original GPU condition. This is the same bounded read-only strategy as
// SplitImageSampleBySharpPhi, but addresses a Phi/Select in the *index* of
// eight ReadConstBuffer producers (rather than in descriptor words).
//
// Attribute- or loop-dependent selectors whose possible indices cannot be
// proven are never replaced by CPU guesses. They remain on the original
// code path for eventual indexed Vulkan resource tables.
struct IndexedImageTableSource {
    const IR::Inst* buffer{};
    const IR::Inst* selector{};
    u32 descriptor_stride_words{1};
    u32 record_stride_bytes{};
    u32 tsharp_offset_bytes{};
    bool gpu_texture_id_indexed{};
    std::array<const IR::Inst*, 8> words{};
    std::array<u32, 8> addends{};
    bool contiguous{};
};

static IndexedImageTableSource AnalyzeIndexedImageTable(const IR::Inst& sample) {
    IndexedImageTableSource out{};
    if (sample.GetOpcode()!=IR::Opcode::ImageSampleRaw) return out;
    const auto* const image=sample.Arg(0).TryInst();
    if (!image || image->GetOpcode()!=IR::Opcode::ImageHandle) return out;
    const auto* const low=image->Arg(0).TryInst();
    const auto* const high=image->Arg(1).TryInst();
    if (!low || !high || low->NumArgs()!=4 || high->NumArgs()!=4) return out;
    std::array<IndexedSharpWord,8> normalized{};
    for (std::size_t i=0;i<8;++i) {
        const auto* const read=(i<4?low->Arg(i):high->Arg(i-4)).TryInst();
        if (!read || read->GetOpcode()!=IR::Opcode::ReadConstBuffer) return out;
        const auto* const buffer=read->Arg(0).TryInst();
        if (!buffer || buffer->GetOpcode()!=IR::Opcode::CompositeConstructU32x4) {
            return out;
        }
        if (i==0) {
            out.buffer=buffer;
        } else if (out.buffer!=buffer) {
            // Two copies of the same V# descriptor are common when the low
            // and high T# dwords are loaded from separately formed handles.
            // Comparing the four original SSA operands is exact; comparing
            // host pointers would spuriously reject these GPU tables.
            bool identical=true;
            for (u32 dw=0;dw<4;++dw) {
                identical &= out.buffer->Arg(dw)==buffer->Arg(dw);
            }
            if (!identical) return out;
        }
        out.words[i]=read;
        normalized[i]=AnalyzeIndexedSharpWord(read->Arg(1));
        if (!normalized[i].valid) return out;
        out.addends[i]=normalized[i].addend;
    }
    if (AreContiguousIndexedSharpWords(normalized)) {
        const auto* const selector=static_cast<const IR::Inst*>(normalized[0].selector);
        const auto identity=IdentifyTsharpSelector(selector);
        if (!identity.source) return out;
        out.selector=static_cast<const IR::Inst*>(identity.source);
        out.descriptor_stride_words=identity.descriptor_stride_words;
        out.gpu_texture_id_indexed=identity.normalized;
        out.contiguous=true;
        return out;
    }
    // A genuine T# can also live within an array-of-structs: the two halves
    // use different, but provably equivalent, 536-byte affine expressions.
    // Reconstruct all eight dword indices and require exactly consecutive
    // descriptor bytes before activating a GPU-indexed Vulkan image array.
    std::array<StridedIndexedSharpWord,8> strided{};
    for (std::size_t i=0;i<strided.size();++i) {
        strided[i]=AnalyzeStridedIndexedSharpWord(out.words[i]->Arg(1));
    }
    if (!AreContiguousStridedSharpWords(strided)) return out;
    out.selector=static_cast<const IR::Inst*>(strided[0].gpu_record_id);
    out.record_stride_bytes=strided[0].record_stride_bytes;
    out.tsharp_offset_bytes=strided[0].offset_dwords*4u;
    out.descriptor_stride_words=out.record_stride_bytes / 4u;
    out.gpu_texture_id_indexed=true;
    out.contiguous=true;
    return out;
}

// Preserve the matching S# from the SAME GPU-selected material record.
// The unmodified guest shader controls the record index; never use a
// host-evaluated index or a default sampler for a divergent S# table.
struct IndexedSamplerTableSource {
    u32 offset_bytes{};
    bool valid{};
};

static IndexedSamplerTableSource AnalyzeIndexedSamplerTable(
    const IR::Inst& sample, const IndexedImageTableSource& image) {
    if (sample.GetOpcode() != IR::Opcode::ImageSampleRaw ||
        !image.contiguous || image.record_stride_bytes == 0 ||
        !image.selector || !image.buffer) return {};
    const auto* const sampler = sample.Arg(1).TryInst();
    if (!sampler || sampler->GetOpcode() != IR::Opcode::CompositeConstructU32x4 ||
        sampler->NumArgs() != 4u) return {};
    std::array<StridedIndexedSharpWord,4> words{};
    for (u32 i=0;i<4;++i) {
        const auto* const read = sampler->Arg(i).TryInst();
        if (!read || read->GetOpcode() != IR::Opcode::ReadConstBuffer) return {};
        const auto* const buffer = read->Arg(0).TryInst();
        if (!buffer || buffer->GetOpcode() != IR::Opcode::CompositeConstructU32x4 ||
            buffer->NumArgs() != 4u) return {};
        for (u32 j=0;j<4;++j) {
            if (buffer->Arg(j) != image.buffer->Arg(j)) return {};
        }
        words[i] = AnalyzeStridedIndexedSharpWord(read->Arg(1));
    }
    if (!AreContiguousStridedSharpWords(words) ||
        words[0].gpu_record_id != image.selector ||
        words[0].record_stride_bytes != image.record_stride_bytes ||
        u64(words[0].offset_dwords)*4u + sizeof(AmdGpu::Sampler) >
            image.record_stride_bytes) return {};
    return {words[0].offset_dwords*4u,true};
}

// The strided material-table lowering remains opt-in until game-facing
// image and sampler correctness has been validated across GPU vendors.
static bool StridedGuestTsharpEnabled() {
    static const bool enabled=[] {
        const char* const value=std::getenv("SHADPS4_STRIDED_GUEST_TSHARP");
        return value && value[0]=='1' && value[1]=='\0';
    }();
    return enabled;
}

static bool IndexedImageAuditEnabled(u64 hash) {
    static const bool enabled=[] {
        const char* const v=std::getenv("SHADPS4_INDEXED_IMAGE_AUDIT");
        return v && v[0]=='1' && v[1]=='\0';
    }();
    if (!enabled) return false;
    static const auto filter=VideoCore::Diagnostics::ParseShaderAuditHashFilter(
        std::getenv("SHADPS4_INDEXED_IMAGE_AUDIT_HASHES"));
    return !filter.enabled || (filter.valid && filter.Matches(hash));
}

static bool SplitImageSampleByIndexedSelect(IR::Block& block, IR::Inst& sample,
                                            std::size_t& budget,
                                            const Shader::Info& info) {
    if (sample.GetOpcode()!=IR::Opcode::ImageSampleRaw) return false;
    const auto table=AnalyzeIndexedImageTable(sample);
    if (IndexedImageAuditEnabled(info.pgm_hash)) {
        static thread_local u32 reports=0;
        if (reports++<64) {
            const auto* const index=table.selector;
            LOG_INFO(Render_Recompiler,
                     "IndexedTSharpSource: shader={:#x} stage={} matched={} "
                     "index_op={} dword_start={} base_op={} "
                     "texture_id_indexed={} descriptor_stride_words={} ",
                     info.pgm_hash,info.hw_stage,table.contiguous,
                     index ? IR::NameOf(index->GetOpcode()) : "unmatched",
                     table.contiguous ? table.addends[0] : 0,
                     table.buffer ? IR::NameOf(table.buffer->GetOpcode()) : "none",
                     table.gpu_texture_id_indexed,table.descriptor_stride_words);
        }
    }
    if (!table.contiguous || budget<1 ||
        table.selector->GetOpcode()!=IR::Opcode::SelectU32) return false;
    const auto* const select=table.selector;
    // Only the index is GPU-dependent. The descriptor table itself must
    // have a stable, host-evaluable base/stride/count across invocations.
    for (unsigned i=0;i<3;++i) {
        if (!IsHostUniformSrtOffset(table.buffer->Arg(i))) return false;
    }
    if (!select->Arg(1).IsImmediate() || !select->Arg(2).IsImmediate() ||
        select->Arg(1).Type()!=IR::Type::U32 ||
        select->Arg(2).Type()!=IR::Type::U32) return false;
    const auto* const image=sample.Arg(0).TryInst();
    const auto* const low=image->Arg(0).TryInst();
    const auto* const high=image->Arg(1).TryInst();
    const auto* const sampler=sample.Arg(1).TryInst();
    if (!sampler || sampler->NumArgs()!=4) return false;
    // Only split when the sampler descriptor is the same host-evaluable
    // source in both cases. A dynamic sampler needs bindless S# as well.
    for(std::size_t i=0;i<4;++i) {
        const IR::Value word=sampler->Arg(i);
        if (word.IsImmediate()) {
            if (word.Type()!=IR::Type::U32) return false;
        } else {
            const auto* const producer=word.TryInst();
            if (!producer || !IsSharpSource(producer)) return false;
            if ((producer->GetOpcode()==IR::Opcode::ReadConst ||
                 producer->GetOpcode()==IR::Opcode::ReadConstBuffer) &&
                !IsHostUniformSrtOffset(producer->Arg(1))) {
                return false;
            }
        }
    }
    const auto pos=IR::Block::InstructionList::s_iterator_to(sample);
    std::array<IR::Value,2> sampled{};
    for(std::size_t side=0;side<2;++side) {
        const u32 index=select->Arg(side+1).U32();
        std::array<IR::Inst*,8> words{};
        for(std::size_t i=0;i<words.size();++i) {
            IR::Inst* const read=&*block.PrependNewInst(pos,*table.words[i]);
            const u32 dword=GcnTextureDwordIndex(index,table.addends[i],
                                                  table.descriptor_stride_words);
            read->SetArg(1,IR::Value{dword});
            words[i]=read;
        }
        const std::array<const IR::Inst*, 2> source_parts{low, high};
        std::array<IR::Inst*,2> parts{};
        for(std::size_t half=0;half<2;++half) {
            auto* const copy=&*block.PrependNewInst(pos,*source_parts[half]);
            for(std::size_t word=0;word<4;++word) {
                copy->SetArg(word,IR::Value{words[half*4+word]});
            }
            parts[half]=copy;
        }
        auto* const newImage=&*block.PrependNewInst(pos,*image);
        newImage->SetArg(0,IR::Value{parts[0]});
        newImage->SetArg(1,IR::Value{parts[1]});
        auto* const newSample=&*block.PrependNewInst(pos,sample);
        newSample->SetArg(0,IR::Value{newImage});
        sampled[side]=IR::Value{newSample};
    }
    IR::IREmitter ir{block,pos};
    const IR::U1 taken{select->Arg(0)};
    const IR::Value result=SelectSideResult(ir,taken,sampled[0],sampled[1]);
    sample.ReplaceUsesWithAndRemove(result);
    --budget;
    if (IndexedImageAuditEnabled(info.pgm_hash)) {
        LOG_INFO(Render_Recompiler,
                 "IndexedTSharpSplit: shader={:#x} stage={} branch_count=2 "
                 "mode=exact_static_index_candidates stride_dwords={}",
                 info.pgm_hash,info.hw_stage,table.descriptor_stride_words);
    }
    return true;
}

ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {
    // Conservative cap: each split replaces one read-only sample with at
    // most three independently bound images/samplers. Already large
    // programs continue through the old unchanged resource path.
    std::size_t original_samples=0;
    for (const IR::Block* const block : program.blocks) {
        for (const IR::Inst& inst : block->Instructions()) {
            original_samples += inst.GetOpcode()==IR::Opcode::ImageSampleRaw;
        }
    }
    const std::size_t resource_cap=std::min<std::size_t>(NUM_IMAGES,NUM_SAMPLERS);
    std::size_t image_split_budget=
        original_samples<resource_cap ? resource_cap-original_samples : 0;
    for (IR::Block* const block : program.blocks) {
        // Both buffer and image Phi splitting may remove the original IR
        // instruction. Advance the iterator BEFORE transformation; a
        // range-for would increment an invalidated intrusive-list iterator.
        for (auto it=block->begin(); it!=block->end();) {
            IR::Inst& inst=*it++;
            if (IsSelectableBufferLoad(inst)) {
                SplitBufferLoadBySharpPhi(*block, inst);
                continue;
            }
            if (inst.GetOpcode() == IR::Opcode::ImageSampleRaw) {
                // Execute at most one resource transformation because either
                // pass removes the original intrusive-list instruction.
                if (!SplitImageSampleByIndexedSelect(*block,inst,image_split_budget,
                                                      program.info)) {
                    SplitImageSampleBySharpPhi(*block,inst,image_split_budget,program.info);
                }
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
                if (inst.GetOpcode()==IR::Opcode::ImageSampleRaw &&
                    profile.supports_sampled_image_nonuniform_indexing) {
                    const auto table=AnalyzeIndexedImageTable(inst);
                    if (table.contiguous && table.gpu_texture_id_indexed &&
                        (table.descriptor_stride_words==8u ||
                         (table.record_stride_bytes!=0u &&
                          StridedGuestTsharpEnabled()))) {
                        auto& usage=sharp_usages.back();
                        bool uniform=true;
                        for (std::size_t dw=0;dw<4;++dw) {
                            const auto value=table.buffer->Arg(dw);
                            uniform &= IsHostUniformSrtOffset(value);
                            usage.indexed_table_sharp.dwords[dw]=value;
                        }
                        // A dynamic guest S# is as essential as the
                        // matching T#. The prior texture-only path paired a
                        // per-invocation texture with a null/default sampler.
                        // Reject the new indexed path unless BOTH tables
                        // share the same GPU SSA record selector.
                        const auto sampler = table.record_stride_bytes
                            ? AnalyzeIndexedSamplerTable(inst,table)
                            : IndexedSamplerTableSource{};
                        if (uniform && (table.record_stride_bytes == 0 ||
                                        sampler.valid)) {
                            usage.indexed_sampler_offset_bytes = sampler.offset_bytes;
                            usage.has_indexed_sampler_table = sampler.valid;
                            usage.indexed_table_sharp.num_dwords=4;
                            MarkReadConstBufferSharpSources(usage.indexed_table_sharp);
                            usage.indexed_texture_id=IR::Value{
                                const_cast<IR::Inst*>(table.selector)};
                            usage.indexed_record_stride_bytes=table.record_stride_bytes;
                            usage.indexed_descriptor_offset_bytes=table.tsharp_offset_bytes;
                            usage.has_indexed_table=true;
                            if (table.record_stride_bytes != 0u) {
                                LOG_INFO(Render_Recompiler,
                                         "StridedIndexedTSharpSource: shader={:#x} stage={} "
                                         "record_stride={} descriptor_offset={} gpu_id_op={}",
                                         program.info.pgm_hash,program.info.hw_stage,
                                         table.record_stride_bytes,table.tsharp_offset_bytes,
                                         IR::NameOf(table.selector->GetOpcode()));
                            }
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