// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <bit>
#include <boost/container/small_vector.hpp>
#include "shader_recompiler/ir/ir_emitter.h"
#include "shader_recompiler/ir/program.h"
#include "shader_recompiler/profile.h"

namespace Shader::Optimization {

static void LowerCmpClass(IR::Block& block, IR::Inst& inst) {
    ASSERT_MSG(inst.Arg(1).IsImmediate(), "Unable to resolve compare operation");
    const auto class_mask = static_cast<IR::FloatClassFunc>(inst.Arg(1).U32());
    if ((class_mask & IR::FloatClassFunc::NaN) == IR::FloatClassFunc::NaN) {
        inst.ReplaceOpcode(IR::Opcode::FPIsNan32);
    } else if ((class_mask & IR::FloatClassFunc::Infinity) == IR::FloatClassFunc::Infinity) {
        inst.ReplaceOpcode(IR::Opcode::FPIsInf32);
    } else if ((class_mask & IR::FloatClassFunc::Finite) == IR::FloatClassFunc::Finite) {
        IR::IREmitter ir{block, IR::Block::InstructionList::s_iterator_to(inst)};
        const IR::F32 value = IR::F32{inst.Arg(0)};
        inst.ReplaceUsesWithAndRemove(
            ir.LogicalNot(ir.LogicalOr(ir.FPIsNan(value), ir.FPIsInf(value))));
    } else {
        UNREACHABLE();
    }
}

static void LowerPackedAncillary(IR::Inst& value) {
    boost::container::small_vector<IR::Inst*, 4> extracts;
    for (const auto& [user, operand] : value.Uses()) {
        if (user->GetOpcode() == IR::Opcode::BitFieldUExtract && operand == 0 &&
            user->Arg(1).IsImmediate() && user->Arg(2).IsImmediate()) {
            extracts.push_back(user);
        }
    }
    for (IR::Inst* const inst : extracts) {
        const u32 offset = inst->Arg(1).U32();
        const u32 bits = inst->Arg(2).U32();
        IR::IREmitter ir{*inst->GetParent(), IR::Block::InstructionList::s_iterator_to(*inst)};
        if (offset >= 8 && offset + bits <= 12) {
            const auto sample_index = ir.GetAttributeU32(IR::Attribute::SampleIndex);
            if (offset == 8 && bits == 4) {
                inst->ReplaceUsesWithAndRemove(sample_index);
            } else {
                inst->ReplaceUsesWithAndRemove(
                    ir.BitFieldExtract(sample_index, ir.Imm32(offset - 8), ir.Imm32(bits)));
            }
        } else if (offset >= 16 && offset + bits <= 27) {
            const auto mrt_index = ir.GetAttributeU32(IR::Attribute::RenderTargetIndex);
            if (offset == 16 && bits == 11) {
                inst->ReplaceUsesWithAndRemove(mrt_index);
            } else {
                inst->ReplaceUsesWithAndRemove(
                    ir.BitFieldExtract(mrt_index, ir.Imm32(offset - 16), ir.Imm32(bits)));
            }
        } else {
            UNREACHABLE_MSG("Unhandled bitfield extract from ancillary VGPR offset={}, bits={}",
                            offset, bits);
        }
    }
    value.ReplaceUsesWithAndRemove(IR::Value{0U});
}

static void LowerMaskedBitCount(IR::Block& block, IR::Inst& inst) {
    IR::IREmitter ir{block, IR::Block::InstructionList::s_iterator_to(inst)};
    const IR::U32 thread_mask{
        ir.GetAttributeU32(IR::Attribute::SubgroupLtMask, inst.Arg(2).U1() ? 1 : 0)};
    const IR::U32 masked_value{ir.BitCount(ir.BitwiseAnd(IR::U32{inst.Arg(0)}, thread_mask))};
    inst.ReplaceUsesWithAndRemove(ir.IAdd(masked_value, IR::U32{inst.Arg(1)}));
}

static const IR::Inst* ReadLaneInHalf(const IR::Value& value, u32 half) {
    if (value.IsImmediate()) {
        return nullptr;
    }
    const IR::Inst* const inst{value.Inst()};
    if (inst->GetOpcode() != IR::Opcode::ReadLane || !inst->Arg(1).IsImmediate() ||
        inst->Arg(1).U32() / 32 != half) {
        return nullptr;
    }
    return inst;
}

static void FoldWaveUMin(IR::Program& program, IR::Block& block, IR::Inst& inst) {
    const IR::Inst* low{ReadLaneInHalf(inst.Arg(0), 0)};
    const IR::Inst* high{ReadLaneInHalf(inst.Arg(1), 1)};
    if (!low || !high) {
        low = ReadLaneInHalf(inst.Arg(1), 0);
        high = ReadLaneInHalf(inst.Arg(0), 1);
    }
    if (!low || !high || low->Arg(0).IsImmediate() || low->Arg(0) != high->Arg(0)) {
        return;
    }
    if (low->GetParent() != &block || high->GetParent() != &block) {
        return;
    }
    boost::container::small_vector<const IR::Inst*, 13> chain{&inst, low, high};
    IR::Value value{low->Arg(0)};
    u32 masks{};
    for (u32 step = 0; step < 5; ++step) {
        const IR::Inst* const min{value.IsImmediate() ? nullptr : value.Inst()};
        if (!min || min->GetOpcode() != IR::Opcode::UMin32 || min->GetParent() != &block) {
            return;
        }
        const IR::Inst* shuffle{};
        for (u32 i = 0; i < 2 && !shuffle; ++i) {
            const IR::Value arg{min->Arg(i)};
            if (!arg.IsImmediate() && arg.Inst()->GetOpcode() == IR::Opcode::ShuffleXor &&
                arg.Inst()->GetParent() == &block && arg.Inst()->Arg(1).IsImmediate() &&
                arg.Inst()->Arg(0) == min->Arg(1 - i)) {
                shuffle = arg.Inst();
                value = min->Arg(1 - i);
            }
        }
        if (!shuffle) {
            return;
        }
        const u32 mask{shuffle->Arg(1).U32()};
        if (mask > 16 || !std::has_single_bit(mask) || (masks & mask) != 0) {
            return;
        }
        masks |= mask;
        chain.push_back(min);
        chain.push_back(shuffle);
    }
    for (const IR::Inst* const link : chain) {
        if (link != &inst && std::ranges::any_of(link->Uses(), [&](const auto& use) {
                return !std::ranges::contains(chain, use.user);
            })) {
            return;
        }
    }
    IR::IREmitter ir{block, IR::Block::InstructionList::s_iterator_to(inst)};
    IR::U32 minimum = ir.GroupUMin(IR::U32{value});
    if (program.info.hw_stage == HwStage::Fragment) {
        minimum = IR::U32{
            ir.Select(ir.GetAttributeU1(IR::Attribute::IsHelperInvocation), value, minimum)};
    }
    inst.ReplaceUsesWithAndRemove(minimum);
}

static void Lower(IR::Program& program, IR::Block& block, IR::Inst& inst, bool fold_wave_min) {
    switch (inst.GetOpcode()) {
    case IR::Opcode::FPCmpClass32:
        return LowerCmpClass(block, inst);
    case IR::Opcode::GetAttributeU32:
        if (inst.Arg(0).Attribute() == IR::Attribute::PackedAncillary) {
            LowerPackedAncillary(inst);
        }
        break;
    case IR::Opcode::MaskedBitCount32:
        return LowerMaskedBitCount(block, inst);
    case IR::Opcode::UMin32:
        if (fold_wave_min) {
            FoldWaveUMin(program, block, inst);
        }
        break;
    default:
        break;
    }
}

void LowerHardwareIntrinsics(IR::Program& program, const Profile& profile) {
    const HwStage stage = program.info.hw_stage;
    const bool fold_wave_min = profile.supports_group_arithmetic &&
                               (stage == HwStage::Fragment || stage == HwStage::Compute);
    for (IR::Block* const block : program.blocks) {
        for (IR::Inst& inst : block->Instructions()) {
            Lower(program, *block, inst, fold_wave_min);
        }
    }
}

} // namespace Shader::Optimization
