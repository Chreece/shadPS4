// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <cstdlib>
#include <cstring>
#include <boost/container/flat_map.hpp>
#include <queue>
#include <xbyak/xbyak.h>
#include <xbyak/xbyak_util.h>
#include "common/arch.h"
#include "common/decoder.h"
#include "common/io_file.h"
#include "common/logging/log.h"
#include "common/path_util.h"
#include "common/signal_context.h"
#include "core/emulator_settings.h"
#include "core/signals.h"
#include "shader_recompiler/info.h"
#include "shader_recompiler/ir/ir_emitter.h"
#include "shader_recompiler/ir/opcodes.h"
#include "shader_recompiler/ir/passes/srt.h"
#include "shader_recompiler/ir/program.h"
#include "shader_recompiler/ir/reg.h"
#include "shader_recompiler/ir/srt_gvn_table.h"
#include "shader_recompiler/ir/value.h"

#ifdef ARCH_X86_64

using namespace Xbyak::util;

static Xbyak::CodeGenerator g_srt_codegen(32_MB);
static const u8* g_srt_codegen_start = nullptr;

namespace Shader {

PFN_SrtWalker RegisterWalkerCode(const u8* ptr, size_t size) {
    const auto func_addr = (PFN_SrtWalker)g_srt_codegen.getCurr();
    g_srt_codegen.db(ptr, size);
    g_srt_codegen.ready();
    return func_addr;
}

} // namespace Shader

namespace {

static void DumpSrtProgram(const Shader::Info& info, const u8* code, size_t codesize) {
    using namespace Common::FS;

    const auto dump_dir = GetUserPath(PathType::ShaderDir) / "dumps";
    if (!std::filesystem::exists(dump_dir)) {
        std::filesystem::create_directories(dump_dir);
    }
    const auto filename = fmt::format("{}_{:#018x}.srtprogram.txt", info.hw_stage, info.pgm_hash);
    const auto file = IOFile{dump_dir / filename, FileAccessMode::Create, FileType::TextFile};

    u64 address = reinterpret_cast<u64>(code);
    u64 code_end = address + codesize;
    ZydisDecodedInstruction instruction;
    ZydisDecodedOperand operands[ZYDIS_MAX_OPERAND_COUNT];
    ZyanStatus status = ZYAN_STATUS_SUCCESS;
    while (address < code_end && ZYAN_SUCCESS(Common::Decoder::Instance()->decodeInstruction(
                                     instruction, operands, reinterpret_cast<void*>(address)))) {
        std::string s =
            Common::Decoder::Instance()->disassembleInst(instruction, operands, address);
        s += "\n";
        file.WriteString(s);
        address += instruction.length;
    }
}

static bool SrtWalkerSignalHandler(void* context, void* fault_address) {
    // Only handle if the fault address is within the SRT code range
    const u8* code_start = g_srt_codegen_start;
    const u8* code_end = code_start + g_srt_codegen.getSize();
    const void* code = Common::GetRip(context);
    if (code < code_start || code >= code_end) {
        return false; // Not in SRT code range
    }

    // Patch instruction to zero register
    ZydisDecodedInstruction instruction;
    ZydisDecodedOperand operands[ZYDIS_MAX_OPERAND_COUNT];
    ZyanStatus status = Common::Decoder::Instance()->decodeInstruction(instruction, operands,
                                                                       const_cast<void*>(code), 15);

    ASSERT(ZYAN_SUCCESS(status) && instruction.mnemonic == ZYDIS_MNEMONIC_MOV &&
           operands[0].type == ZYDIS_OPERAND_TYPE_REGISTER &&
           operands[1].type == ZYDIS_OPERAND_TYPE_MEMORY);

    size_t len = instruction.length;
    const size_t patch_size = 3;
    u8* code_patch = const_cast<u8*>(reinterpret_cast<const u8*>(code));

    // We can only encounter rdi or r10d as the first operand in a
    // fault memory access for SRT walker.
    switch (operands[0].reg.value) {
    case ZYDIS_REGISTER_RDI:
        // mov rdi, [rdi + (off_dw << 2)] -> xor rdi, rdi
        code_patch[0] = 0x48;
        code_patch[1] = 0x31;
        code_patch[2] = 0xFF;
        break;
    case ZYDIS_REGISTER_R10D:
        // mov r10d, [rdi + (off_dw << 2)] -> xor r10d, r10d
        code_patch[0] = 0x45;
        code_patch[1] = 0x31;
        code_patch[2] = 0xD2;
        break;
    default:
        UNREACHABLE_MSG("Unsupported register for SRT walker patch");
        return false;
    }

    // Fill nops
    memset(code_patch + patch_size, 0x90, len - patch_size);

    LOG_WARNING(Render_Recompiler, "Patched SRT walker at {}, fault address {}", code,
                fault_address);

    return true;
}

using namespace Shader;

struct PassInfo {
    struct PointerListCompare {
        bool operator()(const IR::Value& a, const IR::Value& b) const {
            // Keep order with the following criteria:
            // 1. If immediate, sort by immediate value
            // 2. If not immediate, sort by instruction order
            // 3. Immediates come first, then instructions
            if (a.IsImmediate() && b.IsImmediate()) {
                return a.U32() < b.U32();
            } else if (!a.IsImmediate() && !b.IsImmediate()) {
                auto a_inst = a.Inst();
                auto b_inst = b.Inst();
                auto a_block = a_inst->GetParent();
                auto b_block = b_inst->GetParent();
                if (a_block != b_block) {
                    return a_block < b_block; // Sort by block address
                }

                auto it = a_block->Instructions().iterator_to(*a_inst);
                auto end = a_block->Instructions().end();
                for (; it != end; ++it) {
                    if (&*it == b_inst) {
                        return true;
                    }
                }
                return false;
            } else {
                return a.IsImmediate(); // Immediates come first
            }
        }
    };

    // map offset to inst
    using PtrUserList =
        boost::container::flat_map<IR::Value, Shader::IR::Inst*, PointerListCompare>;

    Optimization::SrtGvnTable gvn_table;
    // keys are GetUserData, ReadConst or ReadConstBuffer instructions that are used as pointers
    std::unordered_map<IR::Inst*, PtrUserList> pointer_uses;
    // GetUserData instructions corresponding to sgpr_base of SRT roots
    boost::container::small_flat_map<IR::ScalarReg, IR::Inst*, 1> srt_roots;

    // pick a single inst for a given value number
    std::unordered_map<u32, IR::Inst*> vn_to_inst;

    // Bumped during codegen to assign offsets to readconsts
    u16 dst_off_dw;

    // GoW-targeted read-only instrumentation. IDs identify nodes within
    // this shader compilation without logging host pointers or guest memory.
    bool gow_diag{};
    u64 gow_shader{};
    u32 gow_events{};
    u32 gow_next_id{};
    std::unordered_map<const IR::Inst*, u32> gow_ids;

    u32 GoWId(const IR::Inst* inst) {
        if (!gow_diag || !inst) {
            return 0;
        }
        const auto [it, inserted] = gow_ids.try_emplace(inst, 0);
        if (inserted) {
            it->second = ++gow_next_id;
        }
        return it->second;
    }

    bool GoWLog() {
        if (!gow_diag || gow_events >= 1024) {
            return false;
        }
        ++gow_events;
        return true;
    }

    PtrUserList* GetUsesAsPointer(IR::Inst* inst) {
        auto it = pointer_uses.find(inst);
        if (it != pointer_uses.end()) {
            return &it->second;
        }
        return nullptr;
    }

    // Return a single instruction that this instruction is identical to, according
    // to value number
    // The "original" is arbitrary. Here it's the first instruction found for a given value number
    IR::Inst* DeduplicateInstruction(IR::Inst* inst) {
        auto it = vn_to_inst.try_emplace(gvn_table.GetValueNumber(inst), inst);
        return it.first->second;
    }
};
} // namespace

namespace Shader::Optimization {

namespace {

static inline u16 GetFlatbufOffset(const IR::Inst* inst) {
    if (inst->GetOpcode() == IR::Opcode::ReadConstBuffer) {
        auto inst_info = inst->Flags<IR::BufferInstInfo>();
        return inst_info.flatbuf_off_dw;
    }
    if (inst->GetOpcode() == IR::Opcode::ReadConst) {
        return inst->Flags<u16>();
    }
    UNREACHABLE_MSG("Instruction not supported");
}

static inline void SetFlatbufOffset(IR::Inst* inst, u16 offset) {
    if (inst->GetOpcode() == IR::Opcode::ReadConstBuffer) {
        auto inst_info = inst->Flags<IR::BufferInstInfo>();
        inst_info.flatbuf_off_dw.Assign(offset);
        inst->SetFlags(inst_info);
        return;
    }
    if (inst->GetOpcode() == IR::Opcode::ReadConst) {
        inst->SetFlags(offset);
        return;
    }
    UNREACHABLE_MSG("Instruction not supported");
}

static bool ComputeOffset(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                          const IR::Value& off_dw);

#define ABORT_ON_FAILURE(expr)                                                                     \
    if (!(expr)) {                                                                                 \
        return false;                                                                              \
    }

#define POP_ABORT_ON_FAILURE(expr)                                                                 \
    if (!(expr)) {                                                                                 \
        c.add(rsp, 8);                                                                             \
        return false;                                                                              \
    }

static bool EmitComputeOffsetIAdd32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                                    IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.add(reg, inst->Arg(0).U32());
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.add(reg, inst->Arg(1).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.add(reg, dword[rsp]);
        c.add(rsp, 8);
    }
    return true;
}

static bool EmitComputeOffsetISub32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                                    IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.neg(reg);
        c.add(reg, inst->Arg(0).U32());
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.sub(reg, inst->Arg(1).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.neg(reg);
        c.add(reg, dword[rsp]);
        c.add(rsp, 8);
    }
    return true;
}

static bool EmitComputeOffsetIMul32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                                    IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.imul(reg, reg, inst->Arg(0).U32());
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.imul(reg, reg, inst->Arg(1).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.imul(reg, dword[rsp]);
        c.add(rsp, 8);
    }
    return true;
}

static bool EmitComputeOffsetShiftLeftLogical32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                                PassInfo& pass_info, IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.shl(reg, inst->Arg(0).U32());
        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.shl(reg, cl);
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.shl(reg, inst->Arg(1).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.pop(rcx);
        c.shl(reg, cl);
    }
    return true;
}

static bool EmitComputeOffsetShiftRightLogical32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                                 PassInfo& pass_info, IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.shr(reg, cl);
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.shr(reg, inst->Arg(1).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.pop(rcx);
        c.shr(reg, cl);
    }
    return true;
}

static bool EmitComputeOffsetBitwiseAnd32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                          PassInfo& pass_info, IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.and_(reg, inst->Arg(0).U32());
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.and_(reg, inst->Arg(1).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.and_(reg, dword[rsp]);
        c.add(rsp, 8);
    }
    return true;
}

static bool EmitComputeOffsetBitwiseOr32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                         PassInfo& pass_info, IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.or_(reg, inst->Arg(0).U32());
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.or_(reg, inst->Arg(1).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.or_(reg, dword[rsp]);
        c.add(rsp, 8);
    }
    return true;
}

static bool EmitComputeOffsetBitwiseXor32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                          PassInfo& pass_info, IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.xor_(reg, inst->Arg(0).U32());
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.xor_(reg, inst->Arg(1).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.xor_(reg, dword[rsp]);
        c.add(rsp, 8);
    }
    return true;
}

static bool EmitComputeOffsetBitwiseNot32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                          PassInfo& pass_info, IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
    c.not_(reg);
    return true;
}

static bool EmitComputeOffsetUMin32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                                    IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.mov(ecx, inst->Arg(0).U32());
        c.cmp(reg, ecx);
        c.cmova(reg, ecx);
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.mov(ecx, inst->Arg(1).U32());
        c.cmp(reg, ecx);
        c.cmova(reg, ecx);
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.cmp(reg, dword[rsp]);
        c.cmova(reg, dword[rsp]);
        c.add(rsp, 8);
    }
    return true;
}

static bool EmitComputeOffsetUMax32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                                    IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.mov(ecx, inst->Arg(0).U32());
        c.cmp(reg, ecx);
        c.cmovb(reg, ecx);
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.mov(ecx, inst->Arg(1).U32());
        c.cmp(reg, ecx);
        c.cmovb(reg, ecx);
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.cmp(reg, dword[rsp]);
        c.cmovb(reg, dword[rsp]);
        c.add(rsp, 8);
    }
    return true;
}

static bool EmitComputeOffsetBitFieldUExtract(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                              PassInfo& pass_info, IR::Inst* inst) {
    // We asume that the count is always less than 32, Is this correct?
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        c.mov(reg, inst->Arg(0).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
    }
    bool in_stack = false;
    if (inst->Arg(1).IsImmediate()) {
        c.shr(reg, inst->Arg(1).U32());
    } else {
        c.push(reg.cvt64());
        in_stack = true;
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.mov(ecx, reg);
        c.shr(dword[rsp], cl);
    }
    if (inst->Arg(2).IsImmediate()) {
        if (in_stack) {
            c.pop(reg.cvt64());
        }
        c.and_(reg, (1U << inst->Arg(2).U32()) - 1);
    } else {
        if (!in_stack) {
            c.push(reg.cvt64());
        }
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(2)));
        c.mov(ecx, reg);
        c.mov(edx, 1);
        c.shl(edx, cl);
        c.dec(edx);
        c.and_(dword[rsp], edx);
        c.pop(reg.cvt64());
    }
    return true;
}

// FindILsb32 is host-evaluable only when its operand is itself host-evaluable.
// A Phi carries GPU loop state and must remain a dynamic read (flatbuf offset zero).
static bool EmitComputeOffsetFindILsb32(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                        PassInfo& pass_info, IR::Inst* inst) {
    const IR::Value value = inst->Arg(0);
    if (value.IsImmediate()) {
        c.mov(reg, value.U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, value));
    }
    Xbyak::Label nonzero;
    c.bsf(reg, reg);
    c.jnz(nonzero);
    c.mov(reg, 0xffffffffU); // SPIR-V FindILsb32 returns all ones for zero.
    c.L(nonzero);
    return true;
}

static bool IsAllowedOffsetInstruction(const IR::Inst* inst) {
    switch (inst->GetOpcode()) {
    case IR::Opcode::GetUserData:
    case IR::Opcode::ReadConst:
    case IR::Opcode::ReadConstBuffer:
    case IR::Opcode::IAdd32:
    case IR::Opcode::ISub32:
    case IR::Opcode::IMul32:
    case IR::Opcode::ShiftLeftLogical32:
    case IR::Opcode::ShiftRightLogical32:
    case IR::Opcode::BitwiseAnd32:
    case IR::Opcode::BitwiseOr32:
    case IR::Opcode::BitwiseXor32:
    case IR::Opcode::BitwiseNot32:
    case IR::Opcode::UMin32:
    case IR::Opcode::UMax32:
    case IR::Opcode::BitFieldUExtract:
    case IR::Opcode::FindILsb32:
        return true;
    default:
        return false;
    }
}

static bool ComputeOffset(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                          const IR::Value& off_dw) {
    ASSERT(reg != ecx && reg != edx);
    auto inst = off_dw.Inst();
    switch (inst->GetOpcode()) {
    case IR::Opcode::GetUserData:
        c.mov(reg, ptr[rsi + (static_cast<u32>(inst->Arg(0).ScalarReg()) << 2)]);
        return true;
    case IR::Opcode::ReadConst:
    case IR::Opcode::ReadConstBuffer:
        if (u16 offset = GetFlatbufOffset(pass_info.DeduplicateInstruction(inst)); offset != 0) {
            c.mov(reg, ptr[rsi + (offset << 2)]);
            return true;
        }
        if (pass_info.GoWLog()) {
            LOG_WARNING(Render_Recompiler,
                        "GOW_SRT_FLATTEN_TRACE shader={:#x} event=DEPENDENCY_ZERO "
                        "node={} opcode={}",
                        pass_info.gow_shader, pass_info.GoWId(inst),
                        IR::NameOf(inst->GetOpcode()));
        }
        return false;
    case IR::Opcode::IAdd32:
        ABORT_ON_FAILURE(EmitComputeOffsetIAdd32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::ISub32:
        ABORT_ON_FAILURE(EmitComputeOffsetISub32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::IMul32:
        ABORT_ON_FAILURE(EmitComputeOffsetIMul32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::ShiftLeftLogical32:
        ABORT_ON_FAILURE(EmitComputeOffsetShiftLeftLogical32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::ShiftRightLogical32:
        ABORT_ON_FAILURE(EmitComputeOffsetShiftRightLogical32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::BitwiseAnd32:
        ABORT_ON_FAILURE(EmitComputeOffsetBitwiseAnd32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::BitwiseOr32:
        ABORT_ON_FAILURE(EmitComputeOffsetBitwiseOr32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::BitwiseXor32:
        ABORT_ON_FAILURE(EmitComputeOffsetBitwiseXor32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::BitwiseNot32:
        ABORT_ON_FAILURE(EmitComputeOffsetBitwiseNot32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::UMin32:
        ABORT_ON_FAILURE(EmitComputeOffsetUMin32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::UMax32:
        ABORT_ON_FAILURE(EmitComputeOffsetUMax32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::BitFieldUExtract:
        ABORT_ON_FAILURE(EmitComputeOffsetBitFieldUExtract(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::FindILsb32:
        ABORT_ON_FAILURE(EmitComputeOffsetFindILsb32(c, reg, pass_info, inst));
        return true;
    default:
        if (pass_info.GoWLog()) {
            LOG_WARNING(Render_Recompiler,
                        "GOW_SRT_FLATTEN_TRACE shader={:#x} event=UNSUPPORTED_OFFSET "
                        "node={} opcode={}",
                        pass_info.gow_shader, pass_info.GoWId(inst),
                        IR::NameOf(inst->GetOpcode()));
        }
        LOG_ERROR(Render_Recompiler, "Unexpected instruction for offset computation, {}",
                  magic_enum::enum_name(inst->GetOpcode()));
        return false;
    }
}

static inline bool PushPtr(Xbyak::CodeGenerator& c, PassInfo& pass_info, const IR::Value& off_dw) {
    c.push(rdi);
    if (off_dw.IsImmediate()) {
        c.mov(rdi, ptr[rdi + (off_dw.U32() << 2)]);
    } else {
        if (!ComputeOffset(c, r10d, pass_info, off_dw)) {
            // PushPtr already emitted a push(rdi). Keep the generated walker stack
            // balanced even if a dynamic Phi offset cannot be flattened.
            c.pop(rdi);
            return false;
        }
        c.shl(r10d, 2);
        c.mov(r10d, r10d);
        c.mov(rdi, ptr[rdi + r10]);
    }
    c.mov(r10, 0xFFFFFFFFFFFFULL);
    c.and_(rdi, r10);
    return true;
}

static inline void PopPtr(Xbyak::CodeGenerator& c) {
    c.pop(rdi);
}

static void VisitPointer(const IR::Value& off_dw, IR::Inst* subtree, PassInfo& pass_info,
                         Xbyak::CodeGenerator& c) {
    const auto sub_op = subtree->GetOpcode();
    const bool unflattened =
        (sub_op == IR::Opcode::ReadConst && subtree->Flags<u16>() == 0) ||
        (sub_op == IR::Opcode::ReadConstBuffer &&
         subtree->Flags<IR::BufferInstInfo>().flatbuf_off_dw == 0);
    if (pass_info.GoWLog()) {
        const auto* off_inst = off_dw.TryInst();
        LOG_WARNING(Render_Recompiler,
                    "GOW_SRT_FLATTEN_TRACE shader={:#x} event=VISIT "
                    "node={} opcode={} index_opcode={} index_immediate={} "
                    "index_literal={} unflattened={}",
                    pass_info.gow_shader, pass_info.GoWId(subtree), IR::NameOf(sub_op),
                    off_inst ? IR::NameOf(off_inst->GetOpcode()) : "Immediate",
                    off_dw.IsImmediate(),
                    off_dw.IsImmediate() && off_dw.Type() == IR::Type::U32
                        ? off_dw.U32() : 0u,
                    unflattened);
    }
    if (unflattened) {
        return;
    }

    if (!PushPtr(c, pass_info, off_dw)) {
        if (pass_info.GoWLog()) {
            LOG_WARNING(Render_Recompiler,
                        "GOW_SRT_FLATTEN_TRACE shader={:#x} event=POINTER_INDEX_FAILED "
                        "node={}", pass_info.gow_shader, pass_info.GoWId(subtree));
        }
        LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");
        return;
    }
    PassInfo::PtrUserList* use_list = pass_info.GetUsesAsPointer(subtree);
    ASSERT(use_list);
    if (pass_info.GoWLog()) {
        LOG_WARNING(Render_Recompiler,
                    "GOW_SRT_FLATTEN_TRACE shader={:#x} event=POINTER_CHILDREN "
                    "node={} child_count={}",
                    pass_info.gow_shader, pass_info.GoWId(subtree), use_list->size());
    }

    // First copy all the src data from this tree level
    // That way, all data that was contiguous in the guest SRT is also contiguous in the
    // flattened buffer.
    // TODO src and dst are contiguous. Optimize with wider loads/stores
    // TODO if this subtree is dynamically indexed, don't compact it (keep it sparse)
    for (auto [src_off_dw, use] : *use_list) {
        if (src_off_dw.IsImmediate()) {
            c.mov(r10d, ptr[rdi + (src_off_dw.U32() << 2)]);
        } else {
            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                if (pass_info.GoWLog()) {
                    const auto* source = src_off_dw.TryInst();
                    LOG_WARNING(Render_Recompiler,
                                "GOW_SRT_FLATTEN_TRACE shader={:#x} "
                                "event=USE_INDEX_FAILED parent={} child={} "
                                "offset_opcode={}",
                                pass_info.gow_shader, pass_info.GoWId(subtree),
                                pass_info.GoWId(use),
                                source ? IR::NameOf(source->GetOpcode()) : "Immediate");
                }
                LOG_ERROR(Render_Recompiler, "Failed to compute offset for SRT walker");
                continue;
            }
            c.shl(r10d, 2);
            c.mov(r10d, r10d);
            c.mov(r10d, dword[rdi + r10]);
        }
        c.mov(ptr[rsi + (pass_info.dst_off_dw << 2)], r10d);

        SetFlatbufOffset(use, pass_info.dst_off_dw);
        if (pass_info.GoWLog()) {
            LOG_WARNING(Render_Recompiler,
                        "GOW_SRT_FLATTEN_TRACE shader={:#x} event=OFFSET_ASSIGNED "
                        "parent={} child={} child_opcode={} offset={}",
                        pass_info.gow_shader, pass_info.GoWId(subtree),
                        pass_info.GoWId(use), IR::NameOf(use->GetOpcode()),
                        pass_info.dst_off_dw);
        }
        pass_info.dst_off_dw++;
    }

    // Then visit any children used as pointers
    for (const auto [src_off_dw, use] : *use_list) {
        if (pass_info.GetUsesAsPointer(use)) {
            VisitPointer(src_off_dw, use, pass_info, c);
        }
    }

    PopPtr(c);
}

// Topologically order *root* walkers by the ReadConst/ReadConstBuffer
// instructions consumed by their guest index expressions. A read produced
// under another root must be assigned a flat-buffer slot before we JIT the
// dependent root. Never execute IR, evaluate guest addresses, reorder
// independent roots, or invent an offset to break an actual dependency cycle.
struct SrtRootDependencyPlan {
    std::vector<u32> order;
    std::vector<std::pair<u32, u32>> edges; // (dependent, prerequisite) root indices
    bool reordered{};
    bool cyclic{};
    bool ambiguous{};
    bool capped{};
};

static SrtRootDependencyPlan MakeSrtRootDependencyPlan(PassInfo& pass_info) {
    SrtRootDependencyPlan plan;
    using Root = std::pair<IR::ScalarReg, IR::Inst*>;
    std::vector<Root> roots;
    for (const auto& [sgpr, root] : pass_info.srt_roots) {
        roots.emplace_back(sgpr, root);
    }
    for (u32 i = 0; i < roots.size(); ++i) {
        plan.order.push_back(i);
    }
    constexpr size_t MAX_ROOTS = 32;
    constexpr size_t MAX_WALK_NODES = 4096;
    constexpr size_t MAX_INDEX_NODES = 4096;
    if (roots.size() > MAX_ROOTS) {
        plan.capped = true;
        return plan;
    }

    std::unordered_map<IR::Inst*, u32> produced_by_root;
    std::vector<std::vector<IR::Value>> index_expressions(roots.size());
    size_t walked_nodes = 0;
    for (u32 root_id = 0; root_id < roots.size(); ++root_id) {
        std::vector<IR::Inst*> todo{roots[root_id].second};
        std::unordered_set<IR::Inst*> visited;
        for (size_t pos = 0; pos < todo.size(); ++pos) {
            if (++walked_nodes > MAX_WALK_NODES) {
                plan.capped = true;
                return plan;
            }
            auto* parent = todo[pos];
            if (!visited.insert(parent).second) {
                continue;
            }
            auto* children = pass_info.GetUsesAsPointer(parent);
            if (!children) {
                continue;
            }
            for (const auto& [src_offset, child] : *children) {
                index_expressions[root_id].push_back(src_offset);
                // The walker consults the canonical GVN representative when
                // evaluating nested ReadConst offsets.
                IR::Inst* canonical = pass_info.DeduplicateInstruction(child);
                for (IR::Inst* source : {child, canonical}) {
                    const auto [it, inserted] =
                        produced_by_root.try_emplace(source, root_id);
                    if (!inserted && it->second != root_id) {
                        plan.ambiguous = true;
                    }
                }
                if (pass_info.GetUsesAsPointer(child)) {
                    todo.push_back(child);
                }
            }
        }
    }
    if (plan.ambiguous) {
        return plan; // Preserve original order when ownership is shared.
    }

    std::vector<std::vector<bool>> deps(
        roots.size(), std::vector<bool>(roots.size(), false));
    size_t index_nodes = 0;
    for (u32 consumer = 0; consumer < roots.size(); ++consumer) {
        std::vector<IR::Value> pending = index_expressions[consumer];
        std::unordered_set<IR::Inst*> visited;
        while (!pending.empty()) {
            if (++index_nodes > MAX_INDEX_NODES) {
                plan.capped = true;
                return plan;
            }
            const IR::Value value = pending.back();
            pending.pop_back();
            if (value.IsImmediate()) {
                continue;
            }
            IR::Inst* inst = value.TryInst();
            if (!inst || !visited.insert(inst).second) {
                continue;
            }
            const auto op = inst->GetOpcode();
            if (op == IR::Opcode::ReadConst || op == IR::Opcode::ReadConstBuffer) {
                // ComputeOffset reads a flattened value for these instructions,
                // so this is a producer dependency, not an instruction to
                // evaluate recursively at code-generation time.
                auto it = produced_by_root.find(inst);
                if (it == produced_by_root.end()) {
                    it = produced_by_root.find(pass_info.DeduplicateInstruction(inst));
                }
                if (it != produced_by_root.end() && it->second != consumer) {
                    deps[consumer][it->second] = true;
                }
                continue;
            }
            if (op == IR::Opcode::GetUserData || !IsAllowedOffsetInstruction(inst)) {
                continue; // Dynamic/unsupported instructions remain untouched.
            }
            for (u32 arg = 0; arg < inst->NumArgs(); ++arg) {
                IR::Value child = inst->Arg(arg);
                if (!child.IsImmediate()) {
                    pending.push_back(child);
                }
            }
        }
    }

    for (u32 consumer = 0; consumer < roots.size(); ++consumer) {
        for (u32 prerequisite = 0; prerequisite < roots.size(); ++prerequisite) {
            if (deps[consumer][prerequisite]) {
                plan.edges.emplace_back(consumer, prerequisite);
            }
        }
    }
    std::vector<u32> sorted;
    std::vector<bool> completed(roots.size(), false);
    while (sorted.size() < roots.size()) {
        bool advanced = false;
        for (u32 candidate = 0; candidate < roots.size(); ++candidate) {
            if (completed[candidate]) {
                continue;
            }
            bool ready = true;
            for (u32 dependency = 0; dependency < roots.size(); ++dependency) {
                if (deps[candidate][dependency] && !completed[dependency]) {
                    ready = false;
                    break;
                }
            }
            if (ready) {
                completed[candidate] = true;
                sorted.push_back(candidate);
                advanced = true;
                break; // Stable original order among independent roots.
            }
        }
        if (!advanced) {
            plan.cyclic = true;
            return plan; // Never guess an order for a dependency cycle.
        }
    }
    plan.reordered = sorted != plan.order;
    plan.order = std::move(sorted);
    return plan;
}

static void GenerateSrtProgram(Info& info, PassInfo& pass_info) {
    Xbyak::CodeGenerator& c = g_srt_codegen;

    if (pass_info.srt_roots.empty()) {
        return;
    }

    // Register the signal handler for SRT walker, if not already registered
    if (g_srt_codegen_start == nullptr) {
        g_srt_codegen_start = c.getCurr();
        auto* signals = Core::Signals::Instance();
        // Call after the memory invalidation handler
        constexpr u32 priority = 1;
        signals->RegisterAccessViolationHandler(SrtWalkerSignalHandler, priority);
    }

    info.srt_info.walker_func = c.getCurr<PFN_SrtWalker>();
    pass_info.dst_off_dw = NUM_USER_DATA_REGS;
    ASSERT(pass_info.dst_off_dw == info.srt_info.flattened_bufsize_dw);

    // "1": proven-shader-only control. "2": opt-in experimental coverage
    // for all shaders in the isolated trial executable. Both modes retain
    // the stable dependency planner and the unchanged original fallback.
    // Installed shadPS4 runs without this environment flag.
    const char* topo_flag = std::getenv("SHADPS4_GOW_SRT_AUTO_ROOTS");
    const bool all_shader_trial =
        topo_flag && std::strcmp(topo_flag, "2") == 0;
    const bool auto_topology_requested =
        all_shader_trial ||
        (pass_info.gow_diag && topo_flag && std::strcmp(topo_flag, "1") == 0);
    if (auto_topology_requested) {
        std::vector<std::pair<IR::ScalarReg, IR::Inst*>> roots;
        for (const auto& [sgpr, root] : pass_info.srt_roots) {
            roots.emplace_back(sgpr, root);
        }
        const auto plan = MakeSrtRootDependencyPlan(pass_info);
        // Every shader with SRT roots yields one compact audit record.
        // The target-specific edge and order records below remain unchanged.
        LOG_WARNING(Render_Recompiler,
                    "GOW_SRT_TOPO_PLAN shader={:#x} enabled={} roots={} "
                    "dependency_edges={} reordered={} ambiguous={} cyclic={} capped={} "
                    "hw_stage={} all_shader_trial={}",
                    info.pgm_hash, auto_topology_requested, roots.size(),
                    plan.edges.size(), plan.reordered, plan.ambiguous,
                    plan.cyclic, plan.capped, static_cast<u32>(info.hw_stage),
                    all_shader_trial);
        if (plan.reordered || pass_info.gow_diag) {
            for (const auto& [dependent, prerequisite] : plan.edges) {
                LOG_WARNING(Render_Recompiler,
                            "GOW_SRT_TOPO_EDGE shader={:#x} dependent_sgpr={} "
                            "prerequisite_sgpr={}",
                            info.pgm_hash, static_cast<u32>(roots[dependent].first),
                            static_cast<u32>(roots[prerequisite].first));
            }
        }
        for (u32 position = 0; position < plan.order.size(); ++position) {
            const auto [sgpr, root] = roots[plan.order[position]];
            if (plan.reordered || pass_info.gow_diag) {
                LOG_WARNING(Render_Recompiler,
                            "GOW_SRT_TOPO_ORDER shader={:#x} position={} sgpr={}",
                            info.pgm_hash, position, static_cast<u32>(sgpr));
            }
            if (pass_info.GoWLog()) {
                LOG_WARNING(Render_Recompiler,
                            "GOW_SRT_FLATTEN_TRACE shader={:#x} event=ROOT_VISIT "
                            "sgpr={} node={}",
                            pass_info.gow_shader, static_cast<u32>(sgpr),
                            pass_info.GoWId(root));
            }
            VisitPointer(IR::Value(static_cast<u32>(sgpr)), root, pass_info, c);
        }
    } else {
        // Experimental, shader-scoped dependency-order A/B control.
        // The earlier trace proved that SGPR6's 24 child reads depend on
        // ReadConst values created by SGPR8. Visit SGPR8 first only when
        // specifically requested; retain the original loop for every other shader.
        const char* root_order_flag = std::getenv("SHADPS4_GOW_SRT_SGPR8_FIRST");
        const bool root_order_requested =
            pass_info.gow_diag && root_order_flag && std::strcmp(root_order_flag, "1") == 0;
        constexpr IR::ScalarReg prerequisite_root = static_cast<IR::ScalarReg>(8);
        auto priority_it = pass_info.srt_roots.end();
        if (root_order_requested) {
            priority_it = pass_info.srt_roots.find(prerequisite_root);
            LOG_WARNING(Render_Recompiler,
                        "GOW_SRT_ROOT_ORDER shader={:#x} enabled={} prerequisite_sgpr=8 "
                        "prerequisite_found={} mode=CONTROLLED_PROOF",
                        info.pgm_hash, root_order_requested,
                        priority_it != pass_info.srt_roots.end());
            if (priority_it != pass_info.srt_roots.end()) {
                const auto [sgpr_base, root] = *priority_it;
                if (pass_info.GoWLog()) {
                    LOG_WARNING(Render_Recompiler,
                                "GOW_SRT_FLATTEN_TRACE shader={:#x} event=ROOT_VISIT "
                                "sgpr={} node={}",
                                pass_info.gow_shader, static_cast<u32>(sgpr_base),
                                pass_info.GoWId(root));
                }
                VisitPointer(IR::Value(static_cast<u32>(sgpr_base)), root, pass_info, c);
            }
        }
        for (const auto& [sgpr_base, root] : pass_info.srt_roots) {
            if (root_order_requested && priority_it != pass_info.srt_roots.end() &&
                sgpr_base == prerequisite_root) {
                continue; // Already visited once, with all offset writes retained.
            }
            if (pass_info.GoWLog()) {
                LOG_WARNING(Render_Recompiler,
                            "GOW_SRT_FLATTEN_TRACE shader={:#x} event=ROOT_VISIT "
                            "sgpr={} node={}",
                            pass_info.gow_shader, static_cast<u32>(sgpr_base),
                            pass_info.GoWId(root));
            }
            VisitPointer(IR::Value(static_cast<u32>(sgpr_base)), root, pass_info, c);
        }

    }

    c.ret();
    c.ready();

    info.srt_info.walker_func_size =
        c.getCurr() - reinterpret_cast<const u8*>(info.srt_info.walker_func);

    if (EmulatorSettings.IsDumpShaders()) {
        DumpSrtProgram(info, reinterpret_cast<const u8*>(info.srt_info.walker_func),
                       info.srt_info.walker_func_size);
    }

    info.srt_info.flattened_bufsize_dw = pass_info.dst_off_dw;
}

static bool IsReadConstSource(const IR::Value base) {
    auto* inst = base.TryInst();
    if (!inst) {
        return false;
    }
    return inst->GetOpcode() == IR::Opcode::GetUserData ||
           inst->GetOpcode() == IR::Opcode::ReadConst ||
           inst->GetOpcode() == IR::Opcode::ReadConstBuffer;
}

void SimplifyReadConstAddressAdd(IR::Inst& inst) {
    // This handles the following pattern by combining the addition with the offset
    // s_add_u32    s12, s2, <offset>
    // s_addc_u32   s13, s3, 0
    // s_load_dword s16, s[12:13], 0x0

    if (inst.GetOpcode() != IR::Opcode::ReadConst) {
        return;
    }
    IR::Inst* addr = inst.Arg(0).Inst();
    IR::Inst* hi = addr->Arg(1).TryInst();
    IR::Inst* lo = addr->Arg(0).TryInst();
    if (!hi || !lo) {
        return;
    }

    if (lo->GetOpcode() != IR::Opcode::IAdd32 || hi->GetOpcode() != IR::Opcode::IAdd32) {
        return;
    }

    IR::Value add_offset;
    IR::Value base;
    if (base = lo->Arg(0); IsReadConstSource(base)) {
        add_offset = lo->Arg(1);
    } else if (base = lo->Arg(1); IsReadConstSource(base)) {
        add_offset = lo->Arg(0);
    } else {
        return;
    }

    IR::Inst* sel = hi->Arg(1).TryInst();
    if (!sel || sel->GetOpcode() != IR::Opcode::SelectU32) {
        return;
    }

    IR::Value sel_true = sel->Arg(1);
    IR::Value sel_false = sel->Arg(2);
    if (!sel_true.IsImmediate() || !sel_false.IsImmediate() || sel_true.U32() != 1 ||
        sel_false.U32() != 0) {
        return;
    }

    addr->SetArg(0, base);
    addr->SetArg(1, hi->Arg(0));
    for (auto [user, operand] : addr->Uses()) {
        ASSERT(user->GetOpcode() == IR::Opcode::ReadConst && operand == 0);
        IR::IREmitter ir{*user->GetParent(), IR::Block::InstructionList::s_iterator_to(*user)};
        IR::U32 offset = IR::U32{user->Arg(1)};
        IR::U32 dw_add_offset =
            add_offset.IsImmediate()
                ? ir.Imm32(add_offset.U32() >> 2u)
                : IR::U32{ir.ShiftRightLogical(IR::U32{add_offset}, ir.Imm32(2u))};
        if (offset.IsImmediate() && dw_add_offset.IsImmediate()) {
            user->SetArg(1, ir.Imm32(offset.U32() + dw_add_offset.U32()));
        } else {
            user->SetArg(1, ir.IAdd(offset, dw_add_offset));
        }
    }
}

} // Anonymous namespace

void FlattenExtendedUserdataPass(IR::Program& program) {
    auto& post_order = program.post_order_blocks;
    PassInfo pass_info;
    const char* gow_enabled = std::getenv("SHADPS4_GOW_SRT_FLATTEN_TRACE");
    pass_info.gow_diag = gow_enabled && std::strcmp(gow_enabled, "1") == 0 &&
                         program.info.pgm_hash == 0x7f710602ULL &&
                         program.info.hw_stage == Shader::HwStage::Fragment;
    pass_info.gow_shader = program.info.pgm_hash;
    u32 gow_buffer_candidates = 0;
    u32 gow_sharp_sources = 0;
    if (pass_info.gow_diag) {
        LOG_WARNING(Render_Recompiler,
                    "GOW_SRT_FLATTEN_BEGIN shader={:#x} blocks={} "
                    "mode=READ_ONLY max_events=1024",
                    pass_info.gow_shader, post_order.size());
    }

    // traverse at end and assign offsets to duplicate readconsts, using
    // vn_to_inst as the source
    boost::container::small_vector<IR::Inst*, 32> all_readconsts, visited;
    std::queue<IR::Inst*> queue;

    for (auto it = post_order.rbegin(); it != post_order.rend(); it++) {
        IR::Block* block{*it};
        for (IR::Inst& inst : *block) {
            if (inst.GetOpcode() != IR::Opcode::ReadConst &&
                inst.GetOpcode() != IR::Opcode::ReadConstBuffer) {
                continue;
            }

            if (inst.GetOpcode() == IR::Opcode::ReadConstBuffer) {
                // Only flatten ReadConstBuffer if it was marked as a sharp source in the
                // resource discovery pass.
                auto inst_info = inst.Flags<IR::BufferInstInfo>();
                if (pass_info.gow_diag) {
                    ++gow_buffer_candidates;
                    gow_sharp_sources += static_cast<bool>(inst_info.sharp_source);
                    if (pass_info.GoWLog()) {
                        const auto off = inst.Arg(1);
                        const auto* off_inst = off.TryInst();
                        LOG_WARNING(Render_Recompiler,
                                    "GOW_SRT_FLATTEN_TRACE shader={:#x} event=DISCOVER "
                                    "node={} kind=ReadConstBuffer sharp_source={} "
                                    "flatbuf={} index_opcode={}",
                                    pass_info.gow_shader, pass_info.GoWId(&inst),
                                    static_cast<bool>(inst_info.sharp_source),
                                    static_cast<u32>(inst_info.flatbuf_off_dw),
                                    off_inst ? IR::NameOf(off_inst->GetOpcode()) : "Immediate");
                    }
                }
                if (!inst_info.sharp_source) {
                    continue;
                }
            }

            SimplifyReadConstAddressAdd(inst);

            all_readconsts.push_back(&inst);
            if (pass_info.GoWLog()) {
                const auto off = inst.Arg(1);
                const auto* off_inst = off.TryInst();
                LOG_WARNING(Render_Recompiler,
                            "GOW_SRT_FLATTEN_TRACE shader={:#x} event=CANDIDATE_INCLUDED "
                            "node={} opcode={} index_opcode={}",
                            pass_info.gow_shader, pass_info.GoWId(&inst),
                            IR::NameOf(inst.GetOpcode()),
                            off_inst ? IR::NameOf(off_inst->GetOpcode()) : "Immediate");
            }

            auto offset = inst.Arg(1);
            if (offset.IsImmediate()) {
                continue;
            }

            queue.push(offset.Inst());
            while (!queue.empty()) {
                IR::Inst* inst{queue.front()};
                queue.pop();

                if (inst->GetOpcode() == IR::Opcode::ReadConstBuffer) {
                    auto buffer_inst_info = inst->Flags<IR::BufferInstInfo>();
                    if (!buffer_inst_info.sharp_source.Value()) {
                        all_readconsts.push_back(inst);
                        auto offset = inst->Arg(1);
                        if (!offset.IsImmediate()) {
                            auto arg_inst = offset.Inst();
                            if (std::ranges::find(visited, arg_inst) == visited.end()) {
                                visited.push_back(arg_inst);
                                queue.push(arg_inst);
                            }
                        }
                    }
                    continue;
                }
                if (inst->GetOpcode() == IR::Opcode::GetUserData ||
                    inst->GetOpcode() == IR::Opcode::ReadConst ||
                    !IsAllowedOffsetInstruction(inst)) {
                    continue;
                }
                for (size_t arg = inst->NumArgs(); arg--;) {
                    const IR::Value arg_value = inst->Arg(arg);
                    if (arg_value.IsImmediate()) {
                        continue;
                    }
                    IR::Inst* arg_inst = arg_value.Inst();
                    if (std::ranges::find(visited, arg_inst) == visited.end()) {
                        visited.push_back(arg_inst);
                        queue.push(arg_inst);
                    }
                }
            }
        }
    }

    for (auto inst : all_readconsts) {
        IR::Inst* canonical = pass_info.DeduplicateInstruction(inst);
        if (canonical != inst) {
            // This is a duplicate of a readconst we've already visited.
            if (pass_info.GoWLog()) {
                LOG_WARNING(Render_Recompiler,
                            "GOW_SRT_FLATTEN_TRACE shader={:#x} event=GVN_DUPLICATE "
                            "node={} canonical={}",
                            pass_info.gow_shader, pass_info.GoWId(inst),
                            pass_info.GoWId(canonical));
            }
            continue;
        }

        IR::Inst* base = inst->Arg(0).Inst();
        if (auto* root = base->Arg(0).TryInst();
            root && root->GetOpcode() == IR::Opcode::ReadFirstLane) {
            if (pass_info.GoWLog()) {
                LOG_WARNING(Render_Recompiler,
                            "GOW_SRT_FLATTEN_TRACE shader={:#x} event=READFIRSTLANE_SKIPPED "
                            "node={} root={}", pass_info.gow_shader,
                            pass_info.GoWId(inst), pass_info.GoWId(root));
            }
            continue;
        }
        ASSERT_MSG(IsReadConstSource(base->Arg(0)), "ReadConst base low not from constant memory");
        ASSERT_MSG(IsReadConstSource(base->Arg(1)), "ReadConst base high not from constant memory");

        IR::Inst* ptr_lo = base->Arg(0).Inst();
        ptr_lo = pass_info.DeduplicateInstruction(ptr_lo);

        auto ptr_uses_kv = pass_info.pointer_uses.try_emplace(ptr_lo, PassInfo::PtrUserList{});
        PassInfo::PtrUserList& user_list = ptr_uses_kv.first->second;

        user_list[inst->Arg(1)] = inst;
        if (pass_info.GoWLog()) {
            const IR::Value idx = inst->Arg(1);
            const IR::Inst* idx_inst = idx.TryInst();
            LOG_WARNING(Render_Recompiler,
                        "GOW_SRT_FLATTEN_TRACE shader={:#x} event=USE_MAPPED "
                        "node={} parent={} parent_opcode={} index_opcode={}",
                        pass_info.gow_shader, pass_info.GoWId(inst),
                        pass_info.GoWId(ptr_lo), IR::NameOf(ptr_lo->GetOpcode()),
                        idx_inst ? IR::NameOf(idx_inst->GetOpcode()) : "Immediate");
        }

        if (ptr_lo->GetOpcode() == IR::Opcode::GetUserData) {
            IR::ScalarReg ud_reg = ptr_lo->Arg(0).ScalarReg();
            pass_info.srt_roots[ud_reg] = ptr_lo;
        }
    }

    GenerateSrtProgram(program.info, pass_info);

    // Assign offsets to duplicate readconsts.
    u32 gow_resolved_sharps = 0;
    u32 gow_unresolved_sharps = 0;
    for (IR::Inst* readconst : all_readconsts) {
        ASSERT(pass_info.vn_to_inst.contains(pass_info.gvn_table.GetValueNumber(readconst)));
        IR::Inst* original = pass_info.DeduplicateInstruction(readconst);
        SetFlatbufOffset(readconst, GetFlatbufOffset(original));
        if (pass_info.gow_diag) {
            const auto opcode = readconst->GetOpcode();
            const u32 assigned = GetFlatbufOffset(readconst);
            if (opcode == IR::Opcode::ReadConstBuffer &&
                readconst->Flags<IR::BufferInstInfo>().sharp_source) {
                if (assigned) {
                    ++gow_resolved_sharps;
                } else {
                    ++gow_unresolved_sharps;
                }
            }
            if (pass_info.GoWLog()) {
                LOG_WARNING(Render_Recompiler,
                            "GOW_SRT_FLATTEN_TRACE shader={:#x} event=FINAL_OFFSET "
                            "node={} canonical={} opcode={} offset={}",
                            pass_info.gow_shader, pass_info.GoWId(readconst),
                            pass_info.GoWId(original),
                            IR::NameOf(opcode), assigned);
            }
        }
    }

    if (pass_info.gow_diag) {
        LOG_WARNING(Render_Recompiler,
                    "GOW_SRT_FLATTEN_END shader={:#x} candidates={} "
                    "readconstbuffer_seen={} marked_sharp={} ptr_nodes={} "
                    "roots={} resolved_sharps={} unresolved_sharps={} "
                    "trace_events={} trace_capped={} result=CAPTURED",
                    pass_info.gow_shader, all_readconsts.size(),
                    gow_buffer_candidates, gow_sharp_sources,
                    pass_info.pointer_uses.size(), pass_info.srt_roots.size(),
                    gow_resolved_sharps, gow_unresolved_sharps,
                    pass_info.gow_events, pass_info.gow_events >= 1024);
    }
    program.info.RefreshFlatBuf();
}

} // namespace Shader::Optimization

#else

namespace Shader {

PFN_SrtWalker RegisterWalkerCode(const u8* ptr, size_t size) {
    UNREACHABLE_MSG("RegisterWalkerCode unimplemented for target architecture.");
}

namespace Optimization {

void FlattenExtendedUserdataPass(IR::Program& program) {
    UNREACHABLE_MSG("FlattenExtendedUserdataPass unimplemented for target architecture.");
}

} // namespace Optimization

} // namespace Shader

#endif
