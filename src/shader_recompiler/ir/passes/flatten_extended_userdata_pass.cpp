// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <limits>
#include <unordered_map>
#include <unordered_set>
#include <functional>
#include <vector>
#include <cstdlib>
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
#include "shader_recompiler/ir/passes/srt_offset_preflight.h"
#include "shader_recompiler/ir/passes/srt_proven_identity.h"
#include "shader_recompiler/ir/passes/srt_uniformity.h"
#include "shader_recompiler/ir/passes/srt_host_barrier.h"
#include "video_core/renderer_vulkan/resource_audit_hash_filter.h"
#include "shader_recompiler/ir/passes/srt_offset_codegen.h"
#include "shader_recompiler/ir/passes/srt_dependency_schedule.h"
#include "shader_recompiler/ir/passes/srt_inline_read_codegen.h"
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
                // Flat ordered containers require an irreflexive comparator.
                // In particular, an instruction must never precede itself.
                if (a_inst == b_inst) {
                    return false;
                }
                auto a_block = a_inst->GetParent();
                auto b_block = b_inst->GetParent();
                if (a_block != b_block) {
                    return std::less<IR::Block*>{}(a_block, b_block);
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
    // Multiple descriptor reads can share an SRT pointer and offset yet have
    // distinct SSA producers. A flat_map assignment silently overwrites one
    // producer and leaves its flattened slot unassigned. Preserve every use.
    using PtrUserList =
        boost::container::flat_multimap<IR::Value, Shader::IR::Inst*, PointerListCompare>;

    Optimization::SrtGvnTable gvn_table;
    // keys are GetUserData, ReadConst or ReadConstBuffer instructions that are used as pointers
    std::unordered_map<IR::Inst*, PtrUserList> pointer_uses;
    // GetUserData instructions corresponding to sgpr_base of SRT roots
    boost::container::small_flat_map<IR::ScalarReg, IR::Inst*, 1> srt_roots;

    // pick a single inst for a given value number
    std::unordered_map<u32, IR::Inst*> vn_to_inst;

    // Bumped during codegen to assign offsets to readconsts
    u16 dst_off_dw;
    // Preserve control-flow-dependent pointer reads when the native SRT JIT
    // cannot implement their offsets. Do not emit a partial native walker.
    u32 dynamic_offsets_deferred{};
    u32 pending_offsets_this_round{};
    u32 unresolved_offset_audits{};
    u32 inline_native_read_count{};
    u32 duplicate_offset_collisions{};
    u32 skipped_lane_pointer_reads{};
    u64 audit_shader_hash{};
    Shader::HwStage audit_shader_stage{};
    std::unordered_map<const IR::Inst*, bool> offset_support_cache;

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


static bool SrtPlanningAuditEnabled() {
    static const bool enabled = [] {
        const char* const flag = std::getenv("SHADPS4_SRT_SOURCE_AUDIT");
        return flag && flag[0] == '1' && flag[1] == '\0';
    }();
    return enabled;
}

static inline u16 GetFlatbufOffset(const IR::Inst* inst);

static bool SrtBarrierTraceEnabled(u64 shader_hash) {
    // A generic comma-separated list supplied by diagnostics, never a
    // game-title-specific workaround. Disabled unless explicitly requested.
    static const auto hashes = VideoCore::Diagnostics::ParseShaderAuditHashFilter(
        std::getenv("SHADPS4_SRT_TRACE_HASHES"));
    return hashes.enabled && hashes.valid && hashes.Matches(shader_hash);
}

// Run only on shader compilation, not on every GPU draw. This reports why
// missing scalar descriptor reads cannot currently be native-flattened.
static void TraceSrtHostBarriers(
    const Info& info,
    const boost::container::small_vector<IR::Inst*, 32>& all_readconsts) {
    if (!SrtBarrierTraceEnabled(info.pgm_hash)) return;
    u32 eligible=0, nonuniform=0, unsupported_base=0, examined=0, samples=0;
    std::unordered_map<u32,u32> blocker_histogram;
    for (const IR::Inst* read : all_readconsts) {
        const auto op=read->GetOpcode();
        if (op != IR::Opcode::ReadConst && op != IR::Opcode::ReadConstBuffer) continue;
        if (GetFlatbufOffset(read) != 0) continue;
        ++examined;
        const IR::Inst* const base=read->Arg(0).TryInst();
        const bool sbuffer=op == IR::Opcode::ReadConstBuffer;
        const IR::Opcode expected=sbuffer ? IR::Opcode::CompositeConstructU32x4
                                         : IR::Opcode::CompositeConstructU32x2;
        if (!base || base->GetOpcode() != expected) {
            ++unsupported_base;
            if (samples++<12) {
                LOG_INFO(Render_Recompiler,
                         "SrtHostBarrier: shader={:#x} stage={} field=base "
                         "read_op={} first_blocker={} kind=unsupported-base",
                         info.pgm_hash,info.hw_stage,IR::NameOf(op),
                         base ? IR::NameOf(base->GetOpcode()) : "missing");
            }
            continue;
        }
        const unsigned parts=sbuffer ? 3u : 2u;
        const char* field=nullptr;
        IR::Value blocked{};
        for (unsigned j=0;j<parts;++j) {
            if (!IsHostUniformSrtOffset(base->Arg(j))) {
                field=j==0 ? "address-low" :
                      j==1 ? "address-high-stride" : "record-count";
                blocked=base->Arg(j);
                break;
            }
        }
        if (field==nullptr &&
            !IsHostUniformSrtOffset(read->Arg(1))) {
            field="dword-index";
            blocked=read->Arg(1);
        }
        if (field==nullptr) {
            ++eligible;
            continue;
        }
        ++nonuniform;
        const SrtHostBarrier cause=FindSrtHostBarrier(blocked);
        const u32 key=cause.kind==SrtHostBarrierKind::UnsupportedOpcode
                          ? static_cast<u32>(cause.opcode) : 0xffffffffu;
        ++blocker_histogram[key];
        // Resolve the first actual unsupported instruction's immediate
        // surroundings for the opted-in shader only. This is compile-time
        // evidence, NOT an attempt to execute its divergent control flow.
        if (samples < 8 && cause.kind == SrtHostBarrierKind::UnsupportedOpcode) {
            std::vector<const IR::Inst*> todo;
            std::unordered_set<const IR::Inst*> seen_nodes;
            if (const auto* root=blocked.TryInst()) todo.push_back(root);
            while (!todo.empty() && seen_nodes.size() < 192) {
                const IR::Inst* node=todo.back();
                todo.pop_back();
                if (!seen_nodes.insert(node).second) continue;
                if (node->GetOpcode() == cause.opcode) {
                    const auto op_name = [&](std::size_t pos) -> std::string_view {
                        if (pos >= node->NumArgs()) return "none";
                        const IR::Value v = node->Arg(pos);
                        if (v.IsImmediate()) return "immediate";
                        const auto* child = v.TryInst();
                        return child ? IR::NameOf(child->GetOpcode()) : "missing";
                    };
                    const auto imm = [&](std::size_t pos) -> u32 {
                        if (pos >= node->NumArgs()) return 0;
                        const IR::Value v = node->Arg(pos);
                        return v.IsImmediate() && v.Type() == IR::Type::U32 ? v.U32() : 0;
                    };
                    LOG_INFO(Render_Recompiler,
                             "SrtBarrierShape: shader={:#x} stage={} field={} "
                             "blocker={} incoming={} a0={} a1={} a2={} "
                             "u32_a0={:#x} u32_a1={:#x} u32_a2={:#x}",
                             info.pgm_hash, info.hw_stage, field,
                             IR::NameOf(node->GetOpcode()), node->NumArgs(),
                             op_name(0),op_name(1),op_name(2),
                             imm(0),imm(1),imm(2));
                    break;
                }
                for (std::size_t i=node->NumArgs();i!=0;) {
                    const auto* child=node->Arg(--i).TryInst();
                    if (child && !seen_nodes.contains(child)) todo.push_back(child);
                }
            }
        }
        if (samples++<16) {
            const IR::Inst* index=read->Arg(1).TryInst();
            LOG_INFO(Render_Recompiler,
                     "SrtHostBarrier: shader={:#x} stage={} field={} "
                     "read_op={} index_op={} first_blocker={} reason={} depth={}",
                     info.pgm_hash,info.hw_stage,field,IR::NameOf(op),
                     index ? IR::NameOf(index->GetOpcode()) : "immediate",
                     cause.kind==SrtHostBarrierKind::UnsupportedOpcode
                         ? IR::NameOf(cause.opcode) : "non-opcode",
                     SrtHostBarrierKindName(cause.kind),cause.depth);
        }
    }
    LOG_INFO(Render_Recompiler,
             "SrtHostBarrierSummary: shader={:#x} stage={} examined={} "
             "eligible={} blocked={} unsupported_base={} unique_blockers={}",
             info.pgm_hash,info.hw_stage,examined,eligible,nonuniform,
             unsupported_base,blocker_histogram.size());
    for (const auto& [code,count] : blocker_histogram) {
        const char* no_opcode="non-opcode-or-immediate";
        const auto name=code==0xffffffffu
            ? std::string_view{no_opcode}
            : IR::NameOf(static_cast<IR::Opcode>(code));
        LOG_INFO(Render_Recompiler,
                 "SrtHostBarrierGroup: shader={:#x} stage={} blocker={} count={}",
                 info.pgm_hash,info.hw_stage,name,count);
    }
}

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

// Both GCN S_LOAD and S_BUFFER_LOAD are scalar, wave-uniform reads.
// A V# descriptor has four dwords, but only its 40-bit base address
// participates in scalar memory addressing. Unknown/divergent IR stays on
// the existing GPU guest-memory path. No descriptor values are synthesized.
static bool CanInlineScalarRead(const IR::Inst* inst) {
    const IR::Opcode opcode = inst->GetOpcode();
    if (opcode != IR::Opcode::ReadConst && opcode != IR::Opcode::ReadConstBuffer) {
        return false;
    }
    if (GetFlatbufOffset(inst) != 0) {
        return false;
    }
    const IR::Inst* const base = inst->Arg(0).TryInst();
    const IR::Opcode expected = opcode == IR::Opcode::ReadConst
                                    ? IR::Opcode::CompositeConstructU32x2
                                    : IR::Opcode::CompositeConstructU32x4;
    return base && base->GetOpcode() == expected &&
           IsHostUniformSrtOffset(IR::Value{const_cast<IR::Inst*>(inst)});
}

static bool ComputeOffset(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                          const IR::Value& off_dw);
static bool EmitInlineReadConst(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                PassInfo& pass_info, IR::Inst* inst);

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
        // The computed register is the runtime shift COUNT, not the left operand.
        // Preserve its value before loading the constant left operand.
        SrtCodegen::EmitConstantLeftShiftByDynamicCount(c, reg, inst->Arg(0).U32());
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


static bool EmitComputeOffsetShiftRightArithmetic32(Xbyak::CodeGenerator& c,
                                                     Xbyak::Reg32 reg, PassInfo& pass_info,
                                                     IR::Inst* inst) {
    ASSERT(!inst->AreAllArgsImmediates());
    if (inst->Arg(0).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.sar(reg, cl);
    } else if (inst->Arg(1).IsImmediate()) {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.sar(reg, inst->Arg(1).U32());
    } else {
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(1)));
        c.push(reg.cvt64());
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        c.pop(rcx);
        c.sar(reg, cl);
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
        const u32 width = inst->Arg(2).U32();
        // Bitfield width 32 preserves all bits. Never shift 1u by 32.
        if (width < 32) {
            c.and_(reg, width == 0 ? 0u : (1u << width) - 1u);
        }
    } else {
        if (!in_stack) {
            c.push(reg.cvt64());
        }
        POP_ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(2)));
        // x86 variable shift counts wrap mod 32, but BFE widths >= 32
        // require the full 32-bit mask, not a wrapped narrow mask.
        SrtCodegen::EmitLowBitMask(c, reg, edx);
        c.and_(dword[rsp], edx);
        c.pop(reg.cvt64());
    }
    return true;
}

static bool IsAllowedOffsetInstruction(const IR::Inst* inst) {
    switch (inst->GetOpcode()) {
    case IR::Opcode::GetUserData:
    case IR::Opcode::GetPcLo:
    case IR::Opcode::Phi:
    case IR::Opcode::BitCastU32F32:
    case IR::Opcode::ReadConst:
    case IR::Opcode::ReadConstBuffer:
    case IR::Opcode::IAdd32:
    case IR::Opcode::ISub32:
    case IR::Opcode::IMul32:
    case IR::Opcode::ShiftLeftLogical32:
    case IR::Opcode::ShiftRightLogical32:
    case IR::Opcode::ShiftRightArithmetic32:
    case IR::Opcode::ReadFirstLane:
    case IR::Opcode::FindILsb32:
    case IR::Opcode::BitwiseAnd32:
    case IR::Opcode::BitwiseOr32:
    case IR::Opcode::BitwiseXor32:
    case IR::Opcode::BitwiseNot32:
    case IR::Opcode::UMin32:
    case IR::Opcode::UMax32:
    case IR::Opcode::BitFieldUExtract:
        return true;
    default:
        return false;
    }
}

static bool ComputeOffset(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg, PassInfo& pass_info,
                          const IR::Value& off_dw) {
    ASSERT(reg != ecx && reg != edx);
    if (off_dw.IsImmediate()) {
        if (off_dw.Type() != IR::Type::U32) {
            return false;
        }
        c.mov(reg, off_dw.U32());
        return true;
    }
    auto inst = off_dw.Inst();
    if (inst->GetOpcode() == IR::Opcode::Phi ||
        inst->GetOpcode() == IR::Opcode::BitCastU32F32) {
        const auto identity = ResolveProvenSrtIdentity(off_dw);
        return identity && ComputeOffset(c, reg, pass_info, *identity);
    }
    switch (inst->GetOpcode()) {
    case IR::Opcode::GetUserData:
        c.mov(reg, ptr[rsi + (static_cast<u32>(inst->Arg(0).ScalarReg()) << 2)]);
        return true;
    case IR::Opcode::GetPcLo:
        // Program-counter values already carry their statically translated
        // low 32-bit address in IR, identical to SPIR-V EmitGetPcLo.
        if (!inst->Arg(0).IsImmediate() || inst->Arg(0).Type() != IR::Type::U32) {
            return false;
        }
        c.mov(reg, inst->Arg(0).U32());
        return true;
    case IR::Opcode::ReadConst:
    case IR::Opcode::ReadConstBuffer: {
        IR::Inst* const canonical = pass_info.DeduplicateInstruction(inst);
        if (u16 offset = GetFlatbufOffset(canonical); offset != 0) {
            c.mov(reg, ptr[rsi + (offset << 2)]);
            return true;
        }
        // Scalar S_LOAD and S_BUFFER_LOAD share the native guest-memory
        // walker only when the complete source address and index are proven
        // wave-uniform. Otherwise preserve shader-side address translation.
        if (CanInlineScalarRead(inst) && EmitInlineReadConst(c, reg, pass_info, inst)) {
            return true;
        }
        static const bool audit = [] {
            const char* const flag = std::getenv("SHADPS4_SRT_SOURCE_AUDIT");
            return flag && flag[0] == '1' && flag[1] == '\0';
        }();
        if (audit && pass_info.unresolved_offset_audits++ < 64) {
            const IR::Value index = inst->Arg(1);
            const IR::Inst* const index_inst = index.TryInst();
            const IR::Value base = inst->Arg(0);
            const IR::Inst* const base_inst = base.TryInst();
            LOG_WARNING(Render_Recompiler,
                        "SrtNativeAudit: shader={:#x} stage={} reason=unresolved_flatbuf "
                        "read_op={} same_canonical={} index_immediate={} index_value={} "
                        "index_op={} base_op={} src_flatbuf_offset={} canonical_offset={}",
                        pass_info.audit_shader_hash, pass_info.audit_shader_stage, inst->GetOpcode(),
                        canonical == inst, index.IsImmediate(),
                        index.IsImmediate() && index.Type() == IR::Type::U32
                            ? index.U32() : 0u,
                        index_inst ? index_inst->GetOpcode() : IR::Opcode::Phi,
                        base_inst ? base_inst->GetOpcode() : IR::Opcode::Phi,
                        GetFlatbufOffset(inst), GetFlatbufOffset(canonical));
        }
        return false;
    }
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
    case IR::Opcode::ShiftRightArithmetic32:
        ABORT_ON_FAILURE(EmitComputeOffsetShiftRightArithmetic32(c, reg, pass_info, inst));
        return true;
    case IR::Opcode::ReadFirstLane:
        // A lane read can be evaluated by the CPU only if the value is
        // proven wave-uniform across the entire source expression.
        if (!IsHostUniformSrtOffset(inst->Arg(0))) {
            return false;
        }
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        return true;
    case IR::Opcode::FindILsb32:
        ABORT_ON_FAILURE(ComputeOffset(c, reg, pass_info, inst->Arg(0)));
        SrtCodegen::EmitSrtFindILsb32(c, reg);
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
    default:
        LOG_ERROR(Render_Recompiler, "Unexpected instruction for offset computation, {}",
                  magic_enum::enum_name(inst->GetOpcode()));
        return false;
    }
}

// Evaluate genuine wave-uniform scalar S_LOAD and S_BUFFER_LOAD from the
// original guest pointer, using the existing fault-recoverable SRT walker.
// Nested scalar dependencies are supported; divergent and cyclic expressions
// remain on the GPU path without guessed values.
static bool EmitInlineReadConst(Xbyak::CodeGenerator& c, Xbyak::Reg32 reg,
                                PassInfo& pass_info, IR::Inst* inst) {
    const bool is_sbuffer = inst->GetOpcode() == IR::Opcode::ReadConstBuffer;
    ASSERT(inst->GetOpcode() == IR::Opcode::ReadConst || is_sbuffer);
    IR::Inst* const base = inst->Arg(0).TryInst();
    const IR::Opcode expected = is_sbuffer ? IR::Opcode::CompositeConstructU32x4
                                          : IR::Opcode::CompositeConstructU32x2;
    if (!base || base->GetOpcode() != expected ||
        !IsHostUniformSrtOffset(IR::Value{inst})) {
        return false;
    }

    const std::size_t checkpoint = c.getSize();
    c.push(rdi); // original SGPR-table pointer / caller's current table
    c.push(rax);
    c.push(r11);
    c.push(r9);  // scratch NUM_RECORDS for bounds-safe scalar buffer reads

    if (!ComputeOffset(c, reg, pass_info, base->Arg(0))) {
        c.setSize(checkpoint);
        return false;
    }
    c.mov(eax, reg); // low 32 bits of the guest virtual address

    if (!ComputeOffset(c, reg, pass_info, base->Arg(1))) {
        c.setSize(checkpoint);
        return false;
    }
    if (is_sbuffer) {
        // GCN V# stride is bits [29:16] in dword 1 (14 bits).
        // Keep the stride for bounds clamping before extracting only
        // the low address eight bits from the same dword.
        c.mov(r11d, reg);
        c.shr(r11d, 16);
        c.and_(r11d, 0x3fffu);
        c.and_(reg, 0xffu);
    }
    c.shl(reg.cvt64(), 32);
    c.or_(rax, reg.cvt64());

    if (is_sbuffer) {
        // Buffer SMEM uses NUM_RECORDS from descriptor dword 2 for bounds.
        // Metadata dword 3 does not affect the memory operation.
        if (!ComputeOffset(c, reg, pass_info, base->Arg(2))) {
            c.setSize(checkpoint);
            return false;
        }
        c.mov(r9d, reg);
    }
    // GCN SMRD IR index is in dwords; compute offset with 32-bit wrapping.
    if (!ComputeOffset(c, reg, pass_info, inst->Arg(1))) {
        c.setSize(checkpoint);
        return false;
    }
    c.mov(r10d, reg);
    if (is_sbuffer) {
        SrtCodegen::EmitSBufferClampDwordIndex(c);
    }
    SrtCodegen::EmitGuestReadDword(
        c, is_sbuffer ? SrtCodegen::SBufferGuestAddressMask
                      : SrtCodegen::ScalarGuestAddressMask); // returns guest dword in r10d
    c.mov(reg, r10d);

    c.pop(r9);
    c.pop(r11);
    c.pop(rax);
    c.pop(rdi);
    ++pass_info.inline_native_read_count;
    return true;
}

static inline bool PushPtr(Xbyak::CodeGenerator& c, PassInfo& pass_info, const IR::Value& off_dw) {
    // Failed offset generation must not leave an unmatched push or a partially
    // emitted expression in the SRT walker's executable buffer.
    const size_t checkpoint = c.getSize();
    c.push(rdi);
    if (off_dw.IsImmediate()) {
        c.mov(rdi, ptr[rdi + (off_dw.U32() << 2)]);
    } else {
        if (!ComputeOffset(c, r10d, pass_info, off_dw)) {
            c.setSize(checkpoint);
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
    if (subtree->GetOpcode() == IR::Opcode::ReadConst && subtree->Flags<u16>() == 0 ||
        subtree->GetOpcode() == IR::Opcode::ReadConstBuffer &&
            subtree->Flags<IR::BufferInstInfo>().flatbuf_off_dw == 0) {
        return;
    }

    // Native SRT pointer codegen must be structurally legal before it emits
    // any x86 instructions. Unsupported expressions remain on the guest
    // resource path rather than creating an incomplete flattened copy.
    if (!CanLowerSrtOffsetStructurally(off_dw, pass_info.offset_support_cache)) {
        ++pass_info.dynamic_offsets_deferred;
        return;
    }
    if (!PushPtr(c, pass_info, off_dw)) {
        // A pointer may depend on a descriptor emitted by a different SRT
        // root. Do not diagnose it as final until all roots have been tried.
        ++pass_info.pending_offsets_this_round;
        return;
    }
    PassInfo::PtrUserList* use_list = pass_info.GetUsesAsPointer(subtree);
    ASSERT(use_list);

    // ReadConstBuffer offsets may themselves depend on a descriptor read in
    // this SRT table. Emit already-ready reads first and retry the rest only
    // after their dependencies have received real flattened values. Never
    // synthesize an index, overwrite a guest resource, or flatten an illegal
    // expression. The previous single pass silently dropped a dependent read
    // if it was visited before its producer.
    std::vector<std::pair<IR::Value, IR::Inst*>> sources;
    sources.reserve(use_list->size());
    for (const auto& [source_offset, consumer] : *use_list) {
        sources.emplace_back(source_offset, consumer);
    }
    std::vector<unsigned char> first_deferred(sources.size(), 0);
    u32 recovered_dependencies{};
    const auto emitted = ScheduleSrtReadyReads(sources.size(), [&](std::size_t entry) {
        const auto& [src_off_dw, use] = sources[entry];
        if (GetFlatbufOffset(use) != 0) {
            return true;
        }

        // GVN-equivalent reads represent the same memory value. Share the
        // canonical offset only when that value has actually been emitted.
        IR::Inst* const canonical = pass_info.DeduplicateInstruction(use);
        if (const u16 prior = GetFlatbufOffset(canonical); prior != 0) {
            SetFlatbufOffset(use, prior);
            return true;
        }

        if (src_off_dw.IsImmediate()) {
            c.mov(r10d, ptr[rdi + (src_off_dw.U32() << 2)]);
        } else {
            if (!CanLowerSrtOffsetStructurally(src_off_dw, pass_info.offset_support_cache)) {
                return false;
            }
            const std::size_t checkpoint = c.getSize();
            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {
                c.setSize(checkpoint);
                first_deferred[entry] = 1;
                return false;
            }
            c.shl(r10d, 2);
            c.mov(r10d, r10d);
            c.mov(r10d, dword[rdi + r10]);
        }
        const u16 offset = pass_info.dst_off_dw;
        c.mov(ptr[rsi + (offset << 2)], r10d);
        SetFlatbufOffset(use, offset);
        if (canonical != use) {
            SetFlatbufOffset(canonical, offset);
        }
        ++pass_info.dst_off_dw;
        recovered_dependencies += first_deferred[entry];
        return true;
    });

    u32 unresolved_dependencies{};
    for (std::size_t i = 0; i < emitted.size(); ++i) {
        if (emitted[i]) {
            continue;
        }
        const IR::Value offset = sources[i].first;
        if (CanLowerSrtOffsetStructurally(offset, pass_info.offset_support_cache)) {
            ++unresolved_dependencies;
        } else {
            ++pass_info.dynamic_offsets_deferred;
        }
    }
    pass_info.pending_offsets_this_round += unresolved_dependencies;
    if (recovered_dependencies != 0) {
        LOG_INFO(Render_Recompiler,
                 "SrtLocalDependencyResult: recovered={} shader={:#x}",
                 recovered_dependencies, pass_info.audit_shader_hash);
    }

    // Then visit any children used as pointers
    for (const auto [src_off_dw, use] : *use_list) {
        if (pass_info.GetUsesAsPointer(use)) {
            VisitPointer(src_off_dw, use, pass_info, c);
        }
    }

    PopPtr(c);
}

static void GenerateSrtProgram(
    Info& info, PassInfo& pass_info,
    const boost::container::small_vector<IR::Inst*, 32>& all_readconsts) {
    Xbyak::CodeGenerator& c = g_srt_codegen;

    // Not every scalar descriptor chain has an SGPR-root entry in the
    // existing table planner. Unflattened but wave-uniform ReadConst reads
    // still require a walker to populate their runtime descriptor values.
    const bool has_inline_reads =
        std::ranges::any_of(all_readconsts, [](IR::Inst* read) {
            return CanInlineScalarRead(read);
        });
    // Optional, general-purpose explanation for shader descriptor reads that
    // cannot be evaluated by the CPU-side walker. These remain GPU reads;
    // this audit never changes guest operations or resource values.
    static const bool inline_audit = [] {
        const char* const value = std::getenv("SHADPS4_SRT_INLINE_AUDIT");
        return value && value[0] == '1' && value[1] == '\0';
    }();
    if (inline_audit) {
        u32 examined{}, eligible{}, not_uniform{}, wrong_base{}, already_flattened{}, samples{};
        for (IR::Inst* read : all_readconsts) {
            if (read->GetOpcode() != IR::Opcode::ReadConst &&
                read->GetOpcode() != IR::Opcode::ReadConstBuffer) {
                continue;
            }
            if (GetFlatbufOffset(read) != 0) {
                ++already_flattened;
                continue;
            }
            ++examined;
            const IR::Inst* const base = read->Arg(0).TryInst();
            const IR::Opcode expected =
                read->GetOpcode() == IR::Opcode::ReadConstBuffer
                    ? IR::Opcode::CompositeConstructU32x4
                    : IR::Opcode::CompositeConstructU32x2;
            const bool correct_base = base && base->GetOpcode() == expected;
            const bool uniform = IsHostUniformSrtOffset(IR::Value{read});
            wrong_base += !correct_base;
            not_uniform += !uniform;
            eligible += correct_base && uniform;
            if (samples++ < 3) {
                const IR::Value low = correct_base ? base->Arg(0) : IR::Value{};
                const IR::Value high = correct_base ? base->Arg(1) : IR::Value{};
                const IR::Value index = read->Arg(1);
                const IR::Inst* const low_inst = low.TryInst();
                const IR::Inst* const high_inst = high.TryInst();
                const IR::Inst* const index_inst = index.TryInst();
                LOG_INFO(Render_Recompiler,
                         "SrtInlineCandidate: shader={:#x} stage={} eligible={} "
                         "base_op={} lo_op={} hi_op={} index_op={} "
                         "lo_immediate={} hi_immediate={} index_immediate={} "
                         "index_value={} sbuffer={} address_uniform={} record_count_uniform={} index_uniform={}",
                         info.pgm_hash, info.hw_stage, correct_base && uniform,
                         base ? IR::NameOf(base->GetOpcode()) : "immediate",
                         low_inst ? IR::NameOf(low_inst->GetOpcode()) : "immediate",
                         high_inst ? IR::NameOf(high_inst->GetOpcode()) : "immediate",
                         index_inst ? IR::NameOf(index_inst->GetOpcode()) : "immediate",
                         low.IsImmediate(), high.IsImmediate(), index.IsImmediate(),
                         index.IsImmediate() && index.Type() == IR::Type::U32
                             ? index.U32() : 0u,
                         read->GetOpcode() == IR::Opcode::ReadConstBuffer,
                         correct_base &&
                             IsHostUniformSrtOffset(base->Arg(0)) &&
                             IsHostUniformSrtOffset(base->Arg(1)),
                         correct_base &&
                             (read->GetOpcode() != IR::Opcode::ReadConstBuffer ||
                              IsHostUniformSrtOffset(base->Arg(2))),
                         IsHostUniformSrtOffset(read->Arg(1)));
            }
        }
        if (examined || already_flattened) {
            LOG_INFO(Render_Recompiler,
                     "SrtInlineEligibility: shader={:#x} stage={} unflattened={} "
                     "eligible={} nonuniform={} unsupported_base={} already_flattened={}",
                     info.pgm_hash, info.hw_stage, examined, eligible, not_uniform,
                     wrong_base, already_flattened);
        }
    }

    TraceSrtHostBarriers(info, all_readconsts);

    if (pass_info.srt_roots.empty() && !has_inline_reads) {
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

    // A scalar buffer descriptor can be loaded from a different SRT table
    // than the table using it. Visit roots to a fixed point of *real*
    // flattened reads rather than treating the first root traversal as
    // globally complete. This never generates missing shader data.
    std::vector<std::pair<IR::ScalarReg, IR::Inst*>> roots;
    roots.reserve(pass_info.srt_roots.size());
    for (const auto& [sgpr_base, root] : pass_info.srt_roots) {
        roots.emplace_back(sgpr_base, root);
    }
    std::size_t max_reads{};
    for (const auto& [_, uses] : pass_info.pointer_uses) {
        max_reads += uses.size();
    }
    const auto global_result = ScheduleSrtAcrossRoots(
        roots.size(), max_reads,
        [&] {
            pass_info.pending_offsets_this_round = 0;
            pass_info.dynamic_offsets_deferred = 0;
        },
        [&](std::size_t i) {
            const auto& [sgpr_base, root] = roots[i];
            VisitPointer(IR::Value(static_cast<u32>(sgpr_base)), root, pass_info, c);
        },
        [&] { return static_cast<std::size_t>(pass_info.dst_off_dw); },
        [&] { return static_cast<std::size_t>(pass_info.pending_offsets_this_round); });

    if (global_result.recovered_after_first_round || global_result.pending) {
        LOG_INFO(Render_Recompiler,
                 "SrtGlobalDependencyResult: rounds={} cross_root_recovered={} "
                 "unresolved={} shader={:#x}",
                 global_result.rounds, global_result.recovered_after_first_round,
                 global_result.pending, info.pgm_hash);
    }
    if (global_result.pending != 0) {
        LOG_ERROR(Render_Recompiler,
                  "Failed to compute offset for SRT walker after global dependency resolution: {}",
                  global_result.pending);
    }

    // The root graph cannot represent every scalar address expression.
    // Late-lower remaining wave-uniform S_LOAD / S_BUFFER_LOAD results from
    // their real guest pointers into flatbuffer slots, with no fake SHARPs.
    // This restores real shader T#/S# source words without ever manufacturing
    // a resource or changing non-uniform shader evaluation.
    u32 late_reads{};
    for (IR::Inst* read : all_readconsts) {
        if (!CanInlineScalarRead(read)) {
            continue;
        }
        IR::Inst* const canonical = pass_info.DeduplicateInstruction(read);
        if (const u16 prior = GetFlatbufOffset(canonical); prior != 0) {
            SetFlatbufOffset(read, prior);
            continue;
        }
        if (pass_info.dst_off_dw == std::numeric_limits<u16>::max()) {
            break;
        }
        if (!EmitInlineReadConst(c, r10d, pass_info, read)) {
            continue;
        }
        const u16 flatbuf_slot = pass_info.dst_off_dw++;
        c.mov(dword[rsi + (flatbuf_slot << 2)], r10d);
        SetFlatbufOffset(read, flatbuf_slot);
        if (canonical != read) {
            SetFlatbufOffset(canonical, flatbuf_slot);
        }
        ++late_reads;
    }
    if (late_reads != 0 || pass_info.inline_native_read_count != 0) {
        LOG_INFO(Render_Recompiler,
                 "SrtInlineReadConstResult: shader={:#x} late_flattened={} "
                 "native_read_expressions={} final_flatbuf_words={}",
                 info.pgm_hash, late_reads, pass_info.inline_native_read_count,
                 pass_info.dst_off_dw);
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
    if (pass_info.dynamic_offsets_deferred != 0) {
        LOG_DEBUG(Render_Recompiler,
                  "SRT unsupported dynamic offsets retained for guest-memory evaluation: {} in shader {:#x}",
                  pass_info.dynamic_offsets_deferred, info.pgm_hash);
    }
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
    pass_info.audit_shader_hash = program.info.pgm_hash;
    pass_info.audit_shader_stage = program.info.hw_stage;

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
                // resource discovery pass
                auto inst_info = inst.Flags<IR::BufferInstInfo>();
                if (!inst_info.sharp_source) {
                    continue;
                }
            }

            SimplifyReadConstAddressAdd(inst);

            all_readconsts.push_back(&inst);

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
                if (inst->GetOpcode() == IR::Opcode::ReadFirstLane &&
                    !IsHostUniformSrtOffset(inst->Arg(0))) {
                    // Keep divergent lane accesses on the guest shader path.
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
        if (pass_info.DeduplicateInstruction(inst) != inst) {
            // This is a duplicate of a readconst we've already visited
            continue;
        }

        IR::Inst* base = inst->Arg(0).Inst();
        if (auto* low = base->Arg(0).TryInst();
            low && low->GetOpcode() == IR::Opcode::ReadFirstLane) {
            ++pass_info.skipped_lane_pointer_reads;
            if (SrtPlanningAuditEnabled() && pass_info.skipped_lane_pointer_reads <= 48) {
                const IR::Value index = inst->Arg(1);
                const IR::Inst* const index_inst = index.TryInst();
                const IR::Inst* const lane_source = low->Arg(0).TryInst();
                LOG_INFO(Render_Recompiler,
                         "SrtPlanSkip: shader={:#x} stage={} reason=lane_selected_pointer "
                         "source={} uniform={} read={} index_immediate={} index_op={}",
                         pass_info.audit_shader_hash, pass_info.audit_shader_stage,
                         lane_source ? lane_source->GetOpcode() : IR::Opcode::Phi,
                         IsHostUniformSrtOffset(low->Arg(0)), inst->GetOpcode(),
                         index.IsImmediate(),
                         index_inst ? index_inst->GetOpcode() : IR::Opcode::Phi);
            }
            continue;
        }
        ASSERT_MSG(IsReadConstSource(base->Arg(0)), "ReadConst base low not from constant memory");
        ASSERT_MSG(IsReadConstSource(base->Arg(1)), "ReadConst base high not from constant memory");

        IR::Inst* ptr_lo = base->Arg(0).Inst();
        ptr_lo = pass_info.DeduplicateInstruction(ptr_lo);

        auto ptr_uses_kv = pass_info.pointer_uses.try_emplace(ptr_lo, PassInfo::PtrUserList{});
        PassInfo::PtrUserList& user_list = ptr_uses_kv.first->second;

        // Never replace an earlier read at the same key. Both SSA results
        // require a well-defined flattened offset, even if their fetch is equal.
        const size_t existing_uses = user_list.count(inst->Arg(1));
        if (existing_uses) {
            ++pass_info.duplicate_offset_collisions;
            if (SrtPlanningAuditEnabled() && pass_info.duplicate_offset_collisions <= 48) {
                const IR::Value index = inst->Arg(1);
                const IR::Inst* const index_inst = index.TryInst();
                LOG_INFO(Render_Recompiler,
                         "SrtPlanCollision: shader={:#x} stage={} source={} "
                         "same_key_prior_uses={} index_immediate={} index_op={} read={}",
                         pass_info.audit_shader_hash, pass_info.audit_shader_stage,
                         ptr_lo->GetOpcode(), existing_uses, index.IsImmediate(),
                         index_inst ? index_inst->GetOpcode() : IR::Opcode::Phi,
                         inst->GetOpcode());
            }
        }
        user_list.emplace(inst->Arg(1), inst);

        if (ptr_lo->GetOpcode() == IR::Opcode::GetUserData) {
            IR::ScalarReg ud_reg = ptr_lo->Arg(0).ScalarReg();
            pass_info.srt_roots[ud_reg] = ptr_lo;
        }
    }

    GenerateSrtProgram(program.info, pass_info, all_readconsts);
    if (SrtPlanningAuditEnabled() &&
        (pass_info.duplicate_offset_collisions != 0 ||
         pass_info.skipped_lane_pointer_reads != 0)) {
        LOG_INFO(Render_Recompiler,
                 "SrtPlanSummary: shader={:#x} stage={} collisions={} lane_pointer_skips={}",
                 pass_info.audit_shader_hash, pass_info.audit_shader_stage,
                 pass_info.duplicate_offset_collisions,
                 pass_info.skipped_lane_pointer_reads);
    }

    // Assign offsets to duplicate readconsts
    for (IR::Inst* readconst : all_readconsts) {
        ASSERT(pass_info.vn_to_inst.contains(pass_info.gvn_table.GetValueNumber(readconst)));
        IR::Inst* original = pass_info.DeduplicateInstruction(readconst);
        SetFlatbufOffset(readconst, GetFlatbufOffset(original));
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
