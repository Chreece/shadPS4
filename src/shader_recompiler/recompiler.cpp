// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "common/logging/classes.h"
#include "core/libraries/kernel/memory.h"
#include "core/memory.h"
#include "shader_recompiler/frontend/control_flow_graph.h"
#include "shader_recompiler/frontend/decode.h"
#include "shader_recompiler/frontend/structured_control_flow.h"
#include "shader_recompiler/frontend/translate/translate.h"
#include "shader_recompiler/ir/passes/ir_passes.h"
#include "shader_recompiler/ir/post_order.h"
#include "shader_recompiler/ir/program.h"
#include "shader_recompiler/profile.h"
#include "shader_recompiler/recompiler.h"

namespace Shader {

static bool BufferTableRangeMapped(Core::MemoryManager& memory, u64 address, u64 size,
                                   bool cpu_read) {
    if (address == 0 || size == 0 || address >= (u64{1} << 40) || size > (u64{1} << 40) - address) {
        return false;
    }
    const auto end = address + size;
    while (address < end) {
        Libraries::Kernel::OrbisVirtualQueryInfo mapping{};
        if (memory.VirtualQuery(address, 0, &mapping) != 0 || !mapping.is_committed ||
            mapping.start > address || mapping.end <= address ||
            (cpu_read && (mapping.protection & 1) == 0)) {
            return false;
        }
        address = mapping.end;
    }
    return true;
}

void Info::RefreshBufferTableSpans() {
    if (buffer_table_span_count == 0) {
        return;
    }
    flattened_ud_buf.resize(srt_info.flattened_bufsize_dw +
                            buffer_table_span_count * BufferTableSpanWords);
    auto* memory = Core::Memory::Instance();
    for (u32 i = 0; i < buffer_table_span_count; ++i) {
        const auto& layout = buffer_table_spans[i];
        auto& ranges = buffer_table_ranges[i];
        ranges.clear();
        AmdGpu::Buffer source{};
        AmdGpu::Buffer span{};
        u32 records{};
        if (layout.source.FetchChecked(flattened_ud_buf.data(), flattened_ud_buf.size(), &source) &&
            source.Valid() && layout.record_stride != 0) {
            const u64 bytes =
                source.stride ? u64(source.stride) * source.num_records : source.num_records;
            if (source.base_address && bytes && bytes % layout.record_stride == 0 &&
                bytes / layout.record_stride <= MaxBufferTableRecords &&
                BufferTableRangeMapped(*memory, source.base_address, bytes, true)) {
                const auto data = std::span{reinterpret_cast<const u8*>(source.base_address),
                                            static_cast<size_t>(bytes)};
                if (BuildBufferTableSpan(data, layout, span, &ranges) &&
                    (span.num_records == 0 ||
                     BufferTableRangeMapped(*memory, span.base_address, span.num_records, false))) {
                    records = bytes / layout.record_stride;
                } else {
                    span = {};
                    ranges.clear();
                }
            }
        }
        std::ranges::sort(ranges, {}, &BufferTableRange::address);
        size_t output{};
        for (const auto range : ranges) {
            if (output && range.address <= ranges[output - 1].address + ranges[output - 1].size) {
                auto& previous = ranges[output - 1];
                previous.size = static_cast<u32>(
                    std::max(previous.address + previous.size, range.address + range.size) -
                    previous.address);
            } else {
                ranges[output++] = range;
            }
        }
        ranges.resize(output);
        std::fill_n(flattened_ud_buf.data() + layout.metadata_offset, BufferTableSpanWords, 0);
        std::memcpy(flattened_ud_buf.data() + layout.metadata_offset, &span, sizeof(span));
        flattened_ud_buf[layout.metadata_offset + 4] = records;
    }
}

IR::BlockList GenerateBlocks(const IR::AbstractSyntaxList& syntax_list) {
    size_t num_syntax_blocks{};
    for (const auto& [_, type] : syntax_list) {
        if (type == IR::AbstractSyntaxNode::Type::Block) {
            ++num_syntax_blocks;
        }
    }
    IR::BlockList blocks{};
    blocks.reserve(num_syntax_blocks);
    for (const auto& [data, type] : syntax_list) {
        if (type == IR::AbstractSyntaxNode::Type::Block) {
            blocks.push_back(data.block);
        }
    }
    return blocks;
}

void EmitControlFlowGraph(IR::Program& program, Pools& pools, Gcn::CFG& cfg,
                          RuntimeInfo& runtime_info, const Profile& profile) {
    Gcn::Translator translator{program.info, runtime_info, profile};
    for (auto& block : cfg) {
        const u32 start = block.begin_index;
        const u32 size = block.end_index - start + 1;
        auto* ir_block = pools.block_pool.Create(pools.inst_pool);
        ir_block->cfg_block = &block;
        block.ir_block = ir_block;
        translator.Translate(ir_block, block.begin, block.cond,
                             std::span{program.ins_list}.subspan(start, size));
        program.blocks.push_back(ir_block);
    }
    translator.EmitPrologue(program.blocks.front());
    ASSERT_MSG(!program.info.translation_failed, "Shader translation has failed");
    for (auto& block : cfg) {
        auto* ir_block = block.ir_block;
        if (block.branch_true) {
            auto* true_block = block.branch_true->ir_block;
            ir_block->AddBranch(true_block);
        }
        if (block.branch_false) {
            auto* false_block = block.branch_false->ir_block;
            ir_block->AddBranch(false_block);
        }
    }
    program.post_order_blocks = Shader::IR::PostOrder(program.blocks.front());
}

IR::Program TranslateProgram(const std::span<const u32>& code, Pools& pools, Info& info,
                             RuntimeInfo& runtime_info, const Profile& profile) {
    // Ensure first instruction is expected.
    constexpr u32 token_mov_vcchi = 0xBEEB03FF;
    if (code[0] != token_mov_vcchi) {
        LOG_WARNING(Render_Recompiler, "First instruction is not s_mov_b32 vcc_hi, #imm");
    }

    Gcn::GcnCodeSlice slice(code.data(), code.data() + code.size());
    Gcn::GcnDecodeContext decoder;

    // Decode and save instructions
    IR::Program program{info};
    program.ins_list.reserve(code.size());
    while (!slice.atEnd()) {
        program.ins_list.emplace_back(decoder.decodeInstruction(slice));
    }

    // Clear any previous pooled data.
    pools.ReleaseContents();

    // Create control flow graph
    Common::ObjectPool<Gcn::Block> gcn_block_pool{64};
    Gcn::CFG cfg{gcn_block_pool, program.ins_list};
    EmitControlFlowGraph(program, pools, cfg, runtime_info, profile);

    // On NVIDIA GPUs HW interpolation of clip distance values seems broken, and we need to emulate
    // it with expensive discard in PS.
    Shader::InjectClipDistanceAttributes(program, runtime_info);

    // Run optimization passes on unstructured graph
    if (!profile.support_float64) {
        Shader::Optimization::LowerFp64ToFp32(program);
    }
    Shader::Optimization::SsaRewritePass(program);
    Shader::Optimization::ConstantPropagationPass(program.post_order_blocks);
    Shader::Optimization::ReadLaneEliminationPass(program);
    if (info.sw_stage == SwStage::TessellationControl) {
        Shader::Optimization::TessellationPreprocess(program, runtime_info);
        Shader::Optimization::HullShaderTransform(program, runtime_info);
    } else if (info.sw_stage == SwStage::TessellationEval) {
        Shader::Optimization::TessellationPreprocess(program, runtime_info);
        Shader::Optimization::DomainShaderTransform(program, runtime_info);
    }
    Shader::Optimization::RingAccessElimination(program, runtime_info);
    Shader::IR::DumpProgram(program, info, "pre-res-discover.");
    auto resources = Shader::Optimization::ResourceDiscoverPass(program, profile);
    Shader::Optimization::FlattenExtendedUserdataPass(program);
    auto image_bounds = Shader::Optimization::ExpandBoundedPointerImages(program, resources);
    Shader::Optimization::ExpandBoundedBufferTables(program, resources, profile, image_bounds);
    if (!image_bounds.empty()) {
        Shader::Optimization::RecordBoundedImageCounts(info, image_bounds);
        info.bounds_srt_info = info.srt_info;
        info.srt_bound_count = info.bounded_image_count_count;
        for (u32 i = 0; i < info.srt_bound_count; ++i) {
            auto& bound = info.srt_bounds[i];
            bound.fetch = info.bounded_image_counts[i];
            bound.fetch.FetchChecked(info.flattened_ud_buf.data(), info.flattened_ud_buf.size(),
                                     &bound.value);
        }
        info.srt_info = {};
        for (auto* block : program.blocks) {
            for (auto& inst : block->Instructions()) {
                if (inst.GetOpcode() == IR::Opcode::ReadConst) {
                    inst.SetFlags(0u);
                } else if (inst.GetOpcode() == IR::Opcode::ReadConstBuffer) {
                    auto flags = inst.Flags<IR::BufferInstInfo>();
                    flags.flatbuf_off_dw.Assign(0u);
                    inst.SetFlags(flags);
                }
            }
        }
        resources = Shader::Optimization::ResourceDiscoverPass(program, profile);
        Shader::Optimization::FlattenExtendedUserdataPass(program);
    }
    Shader::Optimization::RecordBoundedImageCounts(info, image_bounds);
    Shader::IR::DumpProgram(program, info, "pre-res-patch.");
    Shader::Optimization::ResourcePatchingPass(program.info, resources, profile);
    Shader::Optimization::LowerBufferFormatToRaw(program);
    Shader::Optimization::SharedMemorySimplifyPass(program, profile);
    Shader::Optimization::SharedMemoryToStoragePass(program, runtime_info, profile);
    Shader::Optimization::LowerUserClipPlanes(program, runtime_info);
    Shader::Optimization::PhiSimplificationPass(program);
    Shader::Optimization::InverseBallotEliminationPass(program);
    Shader::IR::DumpProgram(program, info, "pre-lower-phi.");

    // Prepare for structurization by clearing flow graph and lowering phis
    for (auto* ir_block : program.blocks) {
        ir_block->imm_predecessors.clear();
        ir_block->imm_successors.clear();
        ir_block->ssa_state.Reset();
    }
    Shader::Optimization::LowerPhisToRegsPass(program);

    // Structurize control flow graph and create program.
    program.syntax_list = Shader::Gcn::BuildASL(pools, cfg, info);
    program.blocks = GenerateBlocks(program.syntax_list);
    program.post_order_blocks = Shader::IR::PostOrder(program.syntax_list.front().data.block);

    // Run optimization passes on structured graph
    Shader::Optimization::SsaRepairPass(program);
    Shader::Optimization::SsaRewritePass(program);
    Shader::Optimization::SharedMemoryBarrierPass(program, runtime_info, profile);
    Shader::Optimization::DeadCodeEliminationPass(program);
    Shader::Optimization::LowerWave64BallotPass(program, runtime_info, profile);
    Shader::Optimization::LowerHardwareIntrinsics(program, profile);
    Shader::Optimization::ConstantPropagationPass(program.post_order_blocks);
    Shader::Optimization::DeadCodeEliminationPass(program);
    Shader::Optimization::CollectShaderInfoPass(program, profile);
    Shader::IR::DumpProgram(program, info);

    return program;
}

} // namespace Shader
