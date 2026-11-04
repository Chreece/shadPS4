// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <bit>
#include "shader_recompiler/ir/ir_emitter.h"
#include "shader_recompiler/ir/program.h"
#include "shader_recompiler/profile.h"

namespace Shader::Optimization {
namespace {

IR::Inst* Match(IR::Value value, IR::Opcode opcode, IR::Block* block) {
    auto* inst = value.TryInst();
    return inst && inst->GetOpcode() == opcode && inst->GetParent() == block ? inst : nullptr;
}

IR::Value MatchReduction(IR::Inst& root) {
    auto* block = root.GetParent();
    const auto opcode = root.GetOpcode();
    auto* low = Match(root.Arg(0), IR::Opcode::ReadLane, block);
    auto* high = Match(root.Arg(1), IR::Opcode::ReadLane, block);
    if (!low || !high || low->Arg(0) != high->Arg(0) || !low->Arg(1).IsImmediate() ||
        !high->Arg(1).IsImmediate()) {
        return {};
    }
    const u32 lane0 = low->Arg(1).U32();
    const u32 lane1 = high->Arg(1).U32();
    if (!((lane0 == 31 && lane1 == 63) || (lane0 == 63 && lane1 == 31))) {
        return {};
    }
    IR::Value value = low->Arg(0);
    for (u32 mask = 1; mask <= 16; mask *= 2) {
        auto* operation = Match(value, opcode, block);
        if (!operation) {
            return {};
        }
        IR::Inst* shuffle{};
        IR::Value input;
        for (u32 arg = 0; arg < 2; ++arg) {
            auto* candidate = Match(operation->Arg(arg), IR::Opcode::ShuffleXor, block);
            if (candidate && candidate->Arg(0) == operation->Arg(1 - arg) &&
                candidate->Arg(1).IsImmediate() && candidate->Arg(1).U32() == mask) {
                shuffle = candidate;
                input = operation->Arg(1 - arg);
                break;
            }
        }
        if (!shuffle) {
            return {};
        }
        value = input;
    }
    auto* cast = Match(value, IR::Opcode::BitCastU32F32, block);
    auto* select = cast ? Match(cast->Arg(0), IR::Opcode::SelectF32, block) : nullptr;
    if (!select || !select->Flags<bool>() || !select->Arg(2).IsImmediate()) {
        return {};
    }
    const u32 identity = opcode == IR::Opcode::UMin32 ? ~0u : 0u;
    if (std::bit_cast<u32>(select->Arg(2).F32()) != identity) {
        return {};
    }
    return value;
}

} // namespace

void FullWaveReductionPass(IR::Program& program, const Profile& profile) {
    if ((program.info.hw_stage != HwStage::Compute && program.info.hw_stage != HwStage::Fragment) ||
        profile.subgroup_size != 64) {
        return;
    }
    for (auto* block : program.blocks) {
        for (auto& inst : block->Instructions()) {
            const auto opcode = inst.GetOpcode();
            if (opcode != IR::Opcode::UMin32 && opcode != IR::Opcode::BitwiseOr32) {
                continue;
            }
            const IR::Value input = MatchReduction(inst);
            if (input.IsEmpty()) {
                continue;
            }
            const auto insert = IR::Block::InstructionList::s_iterator_to(inst);
            auto* reduced =
                &*block->PrependNewInst(insert,
                                        opcode == IR::Opcode::UMin32 ? IR::Opcode::GroupReduceUMin
                                                                     : IR::Opcode::GroupReduceOr,
                                        {input});
            inst.ReplaceUsesWith(IR::Value{reduced});
        }
    }
}

} // namespace Shader::Optimization
