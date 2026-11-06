// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <map>
#include <set>
#include <vector>
#include "shader_recompiler/frontend/control_flow_graph.h"
#include "shader_recompiler/frontend/ordered_gds_state.h"
#include "shader_recompiler/ir/ir_emitter.h"
#include "shader_recompiler/ir/passes/ordered_gds_continuation_pass.h"
#include "shader_recompiler/recompiler.h"

namespace Shader::Optimization {
namespace {

using IR::Opcode;
using IR::RegTag;
using IR::RegType;

RegTag RegisterOf(const IR::Inst& inst) {
    switch (inst.GetOpcode()) {
    case Opcode::GetScalarRegister:
    case Opcode::SetScalarRegister:
        return RegTag{inst.Arg(0).ScalarReg()};
    case Opcode::GetVectorRegister:
    case Opcode::SetVectorRegister:
        return RegTag{inst.Arg(0).VectorReg()};
    case Opcode::GetVirtualRegister:
    case Opcode::SetVirtualRegister:
        return RegTag{inst.Arg(0).VirtualReg()};
    case Opcode::GetScc:
    case Opcode::SetScc:
        return RegTag{RegType::Scc};
    case Opcode::GetExec:
    case Opcode::SetExec:
        return RegTag{RegType::Exec};
    case Opcode::GetVccLo:
    case Opcode::SetVccLo:
        return RegTag{RegType::VccLo};
    case Opcode::GetVccHi:
    case Opcode::SetVccHi:
        return RegTag{RegType::VccHi};
    case Opcode::GetM0:
    case Opcode::SetM0:
        return RegTag{RegType::M0};
    default:
        return {};
    }
}

u64 RegisterKey(RegTag tag) {
    return (u64(tag.type) << 32) | tag.index;
}

IR::Type RegisterType(RegTag tag) {
    if (tag.type == RegType::VirtualReg) {
        return tag.reg.type;
    }
    return tag.type == RegType::Exec || tag.type == RegType::Scc ? IR::Type::U1 : IR::Type::U32;
}

void SetRegister(IR::IREmitter& ir, RegTag tag, IR::Value value) {
    switch (tag.type) {
    case RegType::ScalarReg:
        ir.SetScalarReg(tag.sreg, IR::U32{value});
        break;
    case RegType::VectorReg:
        ir.SetVectorReg(tag.vreg, IR::U32{value});
        break;
    case RegType::VirtualReg:
        ir.SetVirtualReg(tag.reg, value);
        break;
    case RegType::Scc:
        ir.SetScc(IR::U1{value});
        break;
    case RegType::Exec:
        ir.SetExec(IR::U1{value});
        break;
    case RegType::VccLo:
        ir.SetVccLo(IR::U32{value});
        break;
    case RegType::VccHi:
        ir.SetVccHi(IR::U32{value});
        break;
    case RegType::M0:
        ir.SetM0(IR::U32{value});
        break;
    default:
        UNREACHABLE();
    }
}

} // namespace

void OrderedGdsContinuationPass(IR::Program& program, Gcn::CFG& cfg, Pools& pools) {
    if (!program.info.uses_ordered_gds) {
        return;
    }
    ASSERT(program.info.hw_stage == HwStage::Compute);
    ASSERT(program.info.buffers.size() + 2 <= NUM_BUFFERS);
    program.info.buffers.push_back({.used_types = IR::Type::U32,
                                    .buffer_type = BufferType::OrderedGdsState,
                                    .is_written = true});
    program.info.buffers.push_back(
        {.used_types = IR::Type::U32, .buffer_type = BufferType::OrderedGdsResume});

    std::vector<IR::Inst*> original;
    std::map<u64, RegTag> registers;
    for (auto* block : program.blocks) {
        for (auto& inst : *block) {
            original.push_back(&inst);
            if (const auto reg = RegisterOf(inst); reg) {
                registers.emplace(RegisterKey(reg), reg);
            }
        }
    }

    // Resource discovery has already resolved handles. Spill only values crossing
    // GCN blocks; resuming must not replay the loads that originally produced them.
    for (auto* inst : original) {
        if (inst->Type() == IR::Type::Void) {
            continue;
        }
        std::vector<IR::Use> uses;
        for (const auto& use : inst->Uses()) {
            if (use.user->GetParent() != inst->GetParent()) {
                uses.push_back(use);
            }
        }
        if (uses.empty()) {
            continue;
        }
        const IR::VirtualReg reg{program.next_reg_index++, inst->Type()};
        registers.emplace(RegisterKey(RegTag{reg}), RegTag{reg});
        auto* block = inst->GetParent();
        IR::IREmitter ir{*block, std::next(IR::Block::InstructionList::s_iterator_to(*inst))};
        ir.SetVirtualReg(reg, IR::Value{inst});
        for (const auto& use : uses) {
            IR::IREmitter load{*use.user->GetParent(),
                               IR::Block::InstructionList::s_iterator_to(*use.user)};
            use.user->SetArg(use.operand, load.GetVirtualReg(reg));
        }
    }

    struct BlockRegisters {
        std::set<u64> used;
        std::set<u64> defined;
        std::set<u64> live_in;
    };
    std::map<const Gcn::Block*, BlockRegisters> liveness;
    for (const auto& block : cfg) {
        auto& state = liveness[&block];
        for (const auto& inst : *block.ir_block) {
            const auto reg = RegisterOf(inst);
            if (!reg) {
                continue;
            }
            const auto key = RegisterKey(reg);
            if (inst.Type() == IR::Type::Void) {
                state.defined.insert(key);
            } else if (!state.defined.contains(key)) {
                state.used.insert(key);
            }
        }
        state.live_in = state.used;
    }
    bool changed;
    do {
        changed = false;
        for (const auto& block : cfg) {
            auto& state = liveness.at(&block);
            auto live = state.used;
            for (const auto* successor : {block.branch_true, block.branch_false}) {
                if (!successor) {
                    continue;
                }
                for (const auto key : liveness.at(successor).live_in) {
                    if (!state.defined.contains(key)) {
                        live.insert(key);
                    }
                }
            }
            if (live != state.live_in) {
                state.live_in = std::move(live);
                changed = true;
            }
        }
    } while (changed);

    std::set<u64> live_at_resume;
    for (const auto& site : program.info.ordered_gds_sites) {
        const auto target = std::find_if(cfg.begin(), cfg.end(), [&](const auto& block) {
            return !block.is_dummy && !block.is_exec_guard && block.begin == site.pc;
        });
        ASSERT(target != cfg.end());
        const auto& live = liveness.at(&*target).live_in;
        live_at_resume.insert(live.begin(), live.end());
    }
    std::erase_if(registers,
                  [&](const auto& entry) { return !live_at_resume.contains(entry.first); });

    std::map<u64, u32> offsets;
    u32 words{};
    for (const auto& [key, reg] : registers) {
        offsets.emplace(key, words);
        words += Gcn::OrderedGds::StateWords(RegisterType(reg));
    }
    ASSERT(words <= 1u << 20);
    program.info.ordered_gds_state_words = std::max(words, 1u);
    program.info.loads.Set(IR::Attribute::WorkgroupIndex);
    program.info.loads.Set(IR::Attribute::LocalInvocationIndex);

    for (auto* block : program.blocks) {
        for (auto it = block->begin(); it != block->end(); ++it) {
            const auto reg = RegisterOf(*it);
            if (!reg || it->Type() != IR::Type::Void || !offsets.contains(RegisterKey(reg))) {
                continue;
            }
            const auto value = it->Arg(it->NumArgs() - 1);
            block->PrependNewInst(std::next(it), Opcode::OrderedGdsStoreState,
                                  {IR::Value{offsets.at(RegisterKey(reg))}, value},
                                  u32(RegisterType(reg)));
        }
    }

    auto* original_entry = &*cfg.begin();
    const auto make_block = [&]() {
        auto* block = cfg.block_pool.Create();
        block->is_dummy = true;
        block->end_class = Gcn::EndClass::Branch;
        block->cond = IR::Condition::True;
        block->ir_block = pools.block_pool.Create(pools.inst_pool);
        block->ir_block->cfg_block = block;
        return block;
    };
    auto* entry = make_block();
    cfg.blocks.insert_before(cfg.begin(), *entry);
    IR::IREmitter ir{*entry->ir_block};
    ir.Prologue();
    for (auto* inst : original) {
        if (inst->GetOpcode() == Opcode::Prologue) {
            inst->Invalidate();
        }
    }
    const IR::U32 site{IR::Value{
        &*entry->ir_block->PrependNewInst(entry->ir_block->end(), Opcode::OrderedGdsResumeSite)}};
    for (const auto& [key, reg] : registers) {
        const auto load =
            entry->ir_block->PrependNewInst(entry->ir_block->end(), Opcode::OrderedGdsLoadState,
                                            {IR::Value{offsets.at(key)}}, u32(RegisterType(reg)));
        SetRegister(ir, reg, IR::Value{&*load});
    }
    entry->cond = IR::Condition::Scc1;
    entry->ir_block->branch_cond =
        ir.ConditionRef(ir.IEqual(site, ir.Imm32(Gcn::OrderedGds::InitialResumeSite)));
    entry->branch_true = original_entry;
    auto* previous = entry;
    for (const auto& decoded : program.info.ordered_gds_sites) {
        auto target = std::find_if(cfg.begin(), cfg.end(), [&](const auto& block) {
            return !block.is_dummy && !block.is_exec_guard && block.begin == decoded.pc;
        });
        ASSERT(target != cfg.end());
        auto* select = make_block();
        cfg.blocks.insert_before(cfg.blocks.iterator_to(*original_entry), *select);
        previous->branch_false = select;
        select->cond = IR::Condition::Scc1;
        select->branch_true = &*target;
        IR::IREmitter branch{*select->ir_block};
        select->ir_block->branch_cond =
            branch.ConditionRef(branch.IEqual(site, branch.Imm32(decoded.pc)));
        previous = select;
    }
    auto* done = make_block();
    done->end_class = Gcn::EndClass::Exit;
    cfg.blocks.insert_before(cfg.blocks.iterator_to(*original_entry), *done);
    previous->branch_false = done;
}

} // namespace Shader::Optimization
