#!/usr/bin/env python3
"""Observe failed GPU-indexed T# resolution; deliberately do not alter resource binding."""
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]); src = root/'src/shader_recompiler/ir/passes/resource_discover_pass.cpp'
serial = root/'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
def blob(path):
    return subprocess.check_output(['git','hash-object',str(path)], text=True).strip()
assert blob(src) == '3d0df4d430336e6163923f62b80e28e22d34149c', 'PR52 matcher source differs'
assert blob(serial) == '351e47f94cbbd0226420339d43f1db91c760bb5e', 'PR52 serializer differs'
s = src.read_text()
def replace_one(old, new, reason):
    global s
    if s.count(old) != 1:
        raise ValueError(f'{reason}: expected 1 source anchor, found {s.count(old)}')
    s = s.replace(old,new,1)

replace_one('#include <atomic>\n','#include <atomic>\n#include <string>\n#include <fmt/format.h>\n', 'diagnostic headers')

marker = 'ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {'
helper = '''// Diagnostic-only: retain GPU selector operations and their immediate operands.
// Depth and the per-shader record counts are deliberately bounded so tracing
// cannot swamp logs or change the cost of normal descriptor lookup.
static std::string DescribeIndexedValue(IR::Value value, u32 depth) {
    if (value.IsImmediate()) {
        return value.Type() == IR::Type::U32 ? fmt::format("{:#x}", value.U32()) : "imm";
    }
    const IR::Inst* inst = value.TryInst();
    if (!inst) return "unknown";
    const auto opcode = static_cast<u32>(inst->GetOpcode());
    if (depth >= 4u) return fmt::format("{}(...)" ,opcode);
    std::string result = fmt::format("{}(",opcode);
    for (u32 i = 0; i < std::min<std::size_t>(inst->NumArgs(), 2u); ++i) {
        if (i != 0u) result += ",";
        result += DescribeIndexedValue(inst->Arg(i), depth + 1u);
    }
    if (inst->NumArgs() > 2u) result += ",...";
    result += ")";
    return result;
}

static void TraceRejectedIndexedImage(const ResourceDiscovery& resource, u64 shader_hash,
                                      IR::Opcode image_opcode, bool is_sample,
                                      bool matcher_accepted, bool supported_nonuniform) {
    const auto& sharp = resource.sharps[0];
    u32 direct_reads = 0u, dynamic_reads = 0u;
    std::string selectors;
    for (u32 i = 0; i < sharp.num_dwords; ++i) {
        const IR::Inst* read = sharp.dwords[i].TryInst();
        if (!read || read->GetOpcode() != IR::Opcode::ReadConstBuffer) continue;
        ++direct_reads;
        const IR::Value index = read->Arg(1);
        if (index.IsImmediate()) continue;
        ++dynamic_reads;
    }
    if (dynamic_reads == 0u) return;
    static thread_local std::unordered_map<u64,u32> per_shader;
    if (per_shader.size() >= 96 && !per_shader.contains(shader_hash)) return;
    u32& count = per_shader[shader_hash];
    if (count++ >= 2u) return;
    // Build recursive expressions only after accepting the bounded sample.
    for (u32 i = 0; i < sharp.num_dwords; ++i) {
        if (!(i <= 2u || i == sharp.num_dwords - 1u)) continue;
        const IR::Inst* read = sharp.dwords[i].TryInst();
        if (!read || read->GetOpcode() != IR::Opcode::ReadConstBuffer) continue;
        const IR::Value index = read->Arg(1);
        if (!index.IsImmediate()) {
            selectors += fmt::format(" w{}:{}", i, DescribeIndexedValue(index, 0u));
        }
    }
    const IR::Inst* read0 = sharp.num_dwords > 0u ? sharp.dwords[0].TryInst() : nullptr;
    const std::string buffer = read0 && read0->GetOpcode() == IR::Opcode::ReadConstBuffer
        ? DescribeIndexedValue(read0->Arg(0), 0u) : "none";
    LOG_WARNING(Render_Recompiler,
                "IndexedSourceRejected: shader={:#x} opcode={} sample={} nonuniform={} "
                "match={} tsharp_words={} direct_reads={} dynamic={} "
                "buffer={} indices={}",
                shader_hash, static_cast<u32>(image_opcode), is_sample,
                supported_nonuniform, matcher_accepted, sharp.num_dwords,
                direct_reads, dynamic_reads, buffer, selectors);
}

'''
replace_one(marker,helper+marker,'bounded GPU selector evidence helper')

original = '''                    const auto table=AnalyzeIndexedImageTable(inst);
                    if(table.contiguous && table.gpu_texture_id_indexed &&'''
new = '''                    const auto table=AnalyzeIndexedImageTable(inst);
                    if(table.contiguous && table.gpu_texture_id_indexed &&'''
# Instead hook at the close of the image branch, for accepted and rejected source checks.
original_block = '''                    }
                }
            } else if (IsDataRingInstruction(inst)) {'''
new_block = '''                    }
                }
                // Capture direct GPU-indexed T# sources which remain unbound,
                // including non-sampled image operations and failed sampler
                // or table-source eligibility. Does not rewrite any shader IR.
                if (!sharp_usages.back().has_indexed_table) {
                    TraceRejectedIndexedImage(sharp_usages.back(), program.info.pgm_hash,
                                               inst.GetOpcode(),
                                               inst.GetOpcode() == IR::Opcode::ImageSampleRaw,
                                               false,
                                               profile.supports_sampled_image_nonuniform_indexing);
                }
            } else if (IsDataRingInstruction(inst)) {'''
replace_one(original_block,new_block,'rejected image path only')
replace_one('''                if (inst.GetOpcode()==IR::Opcode::ImageSampleRaw &&
                    profile.supports_sampled_image_nonuniform_indexing) {''',
            '''                bool index_pattern_match = false;
                if (inst.GetOpcode()==IR::Opcode::ImageSampleRaw &&
                    profile.supports_sampled_image_nonuniform_indexing) {''',
            'record-only matcher stage')
replace_one('''                    const auto table=AnalyzeIndexedImageTable(inst);
                    if(table.contiguous && table.gpu_texture_id_indexed &&''',
            '''                    const auto table=AnalyzeIndexedImageTable(inst);
                    index_pattern_match = table.contiguous && table.gpu_texture_id_indexed &&
                        (table.descriptor_stride_words==8u || table.record_stride_bytes!=0u);
                    if(table.contiguous && table.gpu_texture_id_indexed &&''',
            'record-only matched image-index pattern')
replace_one('''                                               false,
                                               profile.supports_sampled_image_nonuniform_indexing);''',
            '''                                               index_pattern_match,
                                               profile.supports_sampled_image_nonuniform_indexing);''',
            'call matcher outcome')

v=serial.read_text()
a='static constexpr u32 ShaderBinaryVersion = 0x15u;'
assert v.count(a) == 1, 'Missing pinned shader cache version'
v=v.replace(a,'static constexpr u32 ShaderBinaryVersion = 0x16u;',1)
assert 'IndexedSourceRejected' in s and s.count('TraceRejectedIndexedImage(') == 2
src.write_text(s)
serial.write_text(v)
print('TRACE_SOURCE_GIT_BLOB='+blob(src))
print('TRACE_SERIAL_GIT_BLOB='+blob(serial))
print('RENDERING_CODE_MODIFIED=NO')
