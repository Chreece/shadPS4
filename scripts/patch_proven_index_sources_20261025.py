#!/usr/bin/env python3
"""Experimental alias-proof for GPU-indexed material descriptor table sources.

Uses existing SrtGvnTable structural equivalence only for whitelist of pure instructions,
never maps a selector to a CPU value. Applies only to exact PR52 user source.
"""
import sys,subprocess
from pathlib import Path
ROOT=Path(sys.argv[1])
p=ROOT/'src/shader_recompiler/ir/passes/resource_discover_pass.cpp'
s=p.read_text()
def hash_obj(path):
    return subprocess.check_output(['git','hash-object',str(path)],text=True).strip()
assert hash_obj(p)=='3d0df4d430336e6163923f62b80e28e22d34149c', 'This patch is ONLY for the verified PR52 source'

def replace(a,b,label):
    global s
    assert s.count(a)==1,(label,s.count(a))
    s=s.replace(a,b)

replace('#include "shader_recompiler/ir/ir_emitter.h"',
        '#include "shader_recompiler/ir/ir_emitter.h"\n#include "shader_recompiler/ir/srt_gvn_table.h"',
        'include preexisting IR emitter')

helpers='''// Two SSA producer nodes can represent an identical shader resource-table
// index even when their instruction pointers differ. The same value-number
// checker used by SRT deduplication proves identity only for whitelisted pure
// arithmetic/constant reads. Phi, lane-dependent unknowns and all other
// unrecognized instructions retain identity-only comparison.
static bool SameProvenIndexedValue(IR::Value lhs, IR::Value rhs, SrtGvnTable& gvn) {
    return lhs == rhs || gvn.GetValueNumber(lhs) == gvn.GetValueNumber(rhs);
}

static bool SameProvenIndexedInst(const void* lhs, const void* rhs, SrtGvnTable& gvn) {
    if (!lhs || !rhs) return false;
    if (lhs == rhs) return true;
    return SameProvenIndexedValue(IR::Value{const_cast<IR::Inst*>(
                                     static_cast<const IR::Inst*>(lhs))},
                                  IR::Value{const_cast<IR::Inst*>(
                                     static_cast<const IR::Inst*>(rhs))}, gvn);
}

template <std::size_t N>
static bool AreProvenContiguousStridedWords(
    const std::array<StridedIndexedSharpWord, N>& words, SrtGvnTable& gvn) {
    if (AreContiguousStridedSharpWords(words)) return true;
    const auto& first = words.front();
    if (!first.valid || !first.gpu_record_id || first.record_stride_bytes < 32u) return false;
    for (std::size_t i = 0; i < N; ++i) {
        const auto& word = words[i];
        if (!word.valid || word.record_stride_bytes != first.record_stride_bytes ||
            std::uint64_t(word.offset_dwords) != std::uint64_t(first.offset_dwords) + i ||
            !SameProvenIndexedInst(word.gpu_record_id, first.gpu_record_id, gvn)) return false;
    }
    return std::uint64_t(first.offset_dwords) + N <= first.record_stride_bytes / 4u;
}

static bool AreProvenContiguousPackedWords(
    const std::array<IndexedSharpWord, 8>& words, SrtGvnTable& gvn) {
    if (AreContiguousIndexedSharpWords(words)) return true;
    const auto& first = words.front();
    if (!first.valid || !first.selector) return false;
    for (std::size_t i = 0; i < words.size(); ++i) {
        const auto& word = words[i];
        if (!word.valid ||
            std::uint64_t(word.addend) != std::uint64_t(first.addend) + i ||
            !SameProvenIndexedInst(word.selector, first.selector, gvn)) return false;
    }
    return true;
}

'''
replace('struct IndexedImageTableSource {',helpers+'struct IndexedImageTableSource {','helpers before indexed source')
replace('    bool contiguous{};\n};\nstatic IndexedImageTableSource AnalyzeIndexedImageTable',
        '    bool contiguous{};\n    bool proven_equivalent_alias{};\n};\nstatic IndexedImageTableSource AnalyzeIndexedImageTable',
        'equivalence outcome')
replace('    IndexedImageTableSource out{};\n    if (sample.GetOpcode()!=IR::Opcode::ImageSampleRaw) return out;',
        '    IndexedImageTableSource out{};\n    SrtGvnTable gvn;\n    if (sample.GetOpcode()!=IR::Opcode::ImageSampleRaw) return out;',
        'GVN per sample')
replace('                identical &= out.buffer->Arg(dw)==buffer->Arg(dw);',
        '''                if (out.buffer->Arg(dw) != buffer->Arg(dw)) {
                    const bool same = SameProvenIndexedValue(out.buffer->Arg(dw),
                                                              buffer->Arg(dw), gvn);
                    out.proven_equivalent_alias |= same;
                    identical &= same;
                }''', 'indexed image buffer identity')
replace('    if(AreContiguousStridedSharpWords(strided)) {\n        out.selector=',
        '''    if(AreProvenContiguousStridedWords(strided, gvn)) {
        out.proven_equivalent_alias |= !AreContiguousStridedSharpWords(strided);
        out.selector=''', 'strided semantic comparison')
replace('    if(AreContiguousIndexedSharpWords(norm)) {\n        const auto* selector=',
        '''    if(AreProvenContiguousPackedWords(norm, gvn)) {
        out.proven_equivalent_alias |= !AreContiguousIndexedSharpWords(norm);
        const auto* selector=''', 'packed semantic comparison')
replace('struct IndexedSamplerTableSource {\n    u32 offset_bytes{};\n    bool valid{};\n};',
        '''struct IndexedSamplerTableSource {
    u32 offset_bytes{};
    bool valid{};
    bool proven_equivalent_alias{};
};''', 'sampler identity outcome')
replace('    std::array<StridedIndexedSharpWord,4> words{};\n    for(u32 i=0;i<4;++i) {',
        '''    std::array<StridedIndexedSharpWord,4> words{};
    SrtGvnTable gvn;
    bool alias = false;
    for(u32 i=0;i<4;++i) {''', 'sampler GVN')
replace('            if(buffer->Arg(j)!=image.buffer->Arg(j)) return {};',
        '''            if (buffer->Arg(j) != image.buffer->Arg(j)) {
                if (!SameProvenIndexedValue(buffer->Arg(j), image.buffer->Arg(j), gvn))
                    return {};
                alias = true;
            }''', 'sampler source equivalence')
replace('    if(!AreContiguousStridedSharpWords(words) ||\n       words[0].gpu_record_id!=image.selector ||',
        '''    if(!AreProvenContiguousStridedWords(words, gvn) ||
       !SameProvenIndexedInst(words[0].gpu_record_id, image.selector, gvn) ||''',
        'sampler record selector equivalence')
replace('    return {words[0].offset_dwords*4u,true};',
        '''    alias |= !AreContiguousStridedSharpWords(words) ||
             words[0].gpu_record_id != image.selector;
    return {words[0].offset_dwords*4u,true,alias};''', 'sampler outcome')
replace('                            usage.has_indexed_table=true;\n                            if (table.record_stride_bytes != 0u) {',
        '''                            usage.has_indexed_table=true;
                            if (table.proven_equivalent_alias || sampler.proven_equivalent_alias) {
                                static std::atomic<u32> alias_reports{0};
                                if (alias_reports.fetch_add(1, std::memory_order_relaxed) < 48u) {
                                    LOG_INFO(Render_Recompiler,
                                             "IndexedProvenEquivalentSources: shader={:#x} "
                                             "stride={} offset={} image_alias={} sampler_alias={}",
                                             program.info.pgm_hash, table.record_stride_bytes,
                                             table.tsharp_offset_bytes,
                                             table.proven_equivalent_alias,
                                             sampler.proven_equivalent_alias);
                                }
                            }
                            if (table.record_stride_bytes != 0u) {''',
        'bounded accepted aliases log')
# Shader binaries from previous source layouts must not be reused.
# Shader metadata and any saved user settings stay unchanged.
serializer=ROOT/'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
v=serializer.read_text()
assert hash_obj(serializer)=='351e47f94cbbd0226420339d43f1db91c760bb5e', 'Expected PR52 serializer'
needle='static constexpr u32 ShaderBinaryVersion = 0x15u;'
assert v.count(needle)==1, 'Expected previous shader binary version once'
v=v.replace(needle,'static constexpr u32 ShaderBinaryVersion = 0x16u;')

# Validate every source in-memory before changing either file.
assert 'IndexedProvenEquivalentSources:' in s and 'SameProvenIndexedValue' in s
p.write_text(s)
serializer.write_text(v)
print('PATCHED',p,hash_obj(p))
print('PATCHED',serializer,hash_obj(serializer))
