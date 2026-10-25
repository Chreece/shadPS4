#!/usr/bin/env python3
"""Fix exact source-recognized GPU-indexed material table layouts.

All transforms are prepared in memory and written only if *all* anchors match.
Never change descriptor contents, synthesize handles, or specialize shader IDs.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1])
header = root / 'src/shader_recompiler/ir/passes/indexed_image_selector.h'
discovery = root / 'src/shader_recompiler/ir/passes/resource_discover_pass.cpp'
serializer = root / 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
sources = {p: p.read_text() for p in (header, discovery, serializer)}

def replace_one(source, old, new, why):
    n=source.count(old)
    if n!=1:
        raise RuntimeError(f'{why}: expected one original source pattern; actual={n}')
    return source.replace(old,new,1)

h=sources[header]
old='''    const auto* const add = shift->Arg(0).TryInst();
    if (!add || add->GetOpcode() != IR::Opcode::IAdd32) return {};
    const auto a = add->Arg(0);
    const auto b = add->Arg(1);
    if (a.IsImmediate() == b.IsImmediate()) return {};
    const auto literal = a.IsImmediate() ? a : b;
    const auto expression = a.IsImmediate() ? b : a;
    if (literal.Type() != IR::Type::U32) return {};
    const std::uint32_t prefix = literal.U32();
    if (prefix % 4u != 0u) return {};
    const auto* const mul = expression.TryInst();
    if (!mul || mul->GetOpcode() != IR::Opcode::IMul32) return {};'''
new='''    // Both GCN layouts have identical descriptor-word indices:
    // ((gpu_record * stride + byte_prefix) >> 2) + word
    // ((gpu_record * stride) >> 2) + prefix_word + word
    // Previous code only recognized the first and discarded real strided
    // material tables when the byte prefix was absent inside the shift.
    // Keep the original GPU record selector, never CPU-evaluate it.
    auto scaled = shift->Arg(0);
    std::uint32_t prefix_bytes = 0;
    if (const auto* add = scaled.TryInst();
        add && add->GetOpcode() == IR::Opcode::IAdd32) {
        const auto a = add->Arg(0);
        const auto b = add->Arg(1);
        if (a.IsImmediate() == b.IsImmediate()) return {};
        const auto literal = a.IsImmediate() ? a : b;
        scaled = a.IsImmediate() ? b : a;
        if (literal.Type() != IR::Type::U32) return {};
        prefix_bytes = literal.U32();
    }
    if (prefix_bytes % 4u != 0u) return {};
    const auto* const mul = scaled.TryInst();
    if (!mul || mul->GetOpcode() != IR::Opcode::IMul32) return {};'''
h=replace_one(h,old,new,'strided index source shapes')
h=replace_one(h,
'''    const std::uint64_t offset =
        std::uint64_t(prefix / 4u) + normalized.addend;''',
'''    const std::uint64_t offset =
        std::uint64_t(prefix_bytes / 4u) + normalized.addend;''',
'normalized post-shift word offset')

s=sources[discovery]
s=replace_one(s,'#include <algorithm>\n','#include <algorithm>\n#include <atomic>\n', 'bounded source diagnostics')
old='''    if(AreContiguousIndexedSharpWords(norm)) {
        const auto* selector=static_cast<const IR::Inst*>(norm[0].selector);
        const auto id=IdentifyTsharpSelector(selector);
        if(!id.source) return out;
        out.selector=static_cast<const IR::Inst*>(id.source);
        out.descriptor_stride_words=id.descriptor_stride_words;
        out.gpu_texture_id_indexed=id.normalized;
        out.contiguous=true;
        return out;
    }
    std::array<StridedIndexedSharpWord,8> strided{};
    for(std::size_t i=0;i<strided.size();++i) {
        strided[i]=AnalyzeStridedIndexedSharpWord(out.words[i]->Arg(1));
    }
    if(!AreContiguousStridedSharpWords(strided)) return out;
    out.selector=static_cast<const IR::Inst*>(strided[0].gpu_record_id);
    out.record_stride_bytes=strided[0].record_stride_bytes;
    out.tsharp_offset_bytes=strided[0].offset_dwords*4u;
    out.descriptor_stride_words=out.record_stride_bytes/4u;
    out.gpu_texture_id_indexed=true;
    out.contiguous=true;
    return out;'''
new='''    // A strided index also looks "contiguous" when the shader computes
    // ((gpu_record * stride) >> 2) + word. The older matcher handled it as
    // an ordinary packed word index and rejected the GPU material table.
    // Prefer the stricter, complete eight-word strided pattern first.
    std::array<StridedIndexedSharpWord,8> strided{};
    for(std::size_t i=0;i<strided.size();++i) {
        strided[i]=AnalyzeStridedIndexedSharpWord(out.words[i]->Arg(1));
    }
    if(AreContiguousStridedSharpWords(strided)) {
        out.selector=static_cast<const IR::Inst*>(strided[0].gpu_record_id);
        out.record_stride_bytes=strided[0].record_stride_bytes;
        out.tsharp_offset_bytes=strided[0].offset_dwords*4u;
        out.descriptor_stride_words=out.record_stride_bytes/4u;
        out.gpu_texture_id_indexed=true;
        out.contiguous=true;
        return out;
    }
    if(AreContiguousIndexedSharpWords(norm)) {
        const auto* selector=static_cast<const IR::Inst*>(norm[0].selector);
        const auto id=IdentifyTsharpSelector(selector);
        if(!id.source) return out;
        out.selector=static_cast<const IR::Inst*>(id.source);
        out.descriptor_stride_words=id.descriptor_stride_words;
        out.gpu_texture_id_indexed=id.normalized;
        out.contiguous=true;
        return out;
    }
    return out;'''
s=replace_one(s,old,new,'indexed T# recognition priority')
s=replace_one(s,
'''                            usage.has_indexed_table=true;
''',
'''                            usage.has_indexed_table=true;
                            if (table.record_stride_bytes != 0u) {
                                static std::atomic<u32> reported{0};
                                if (reported.fetch_add(1, std::memory_order_relaxed) < 48u) {
                                    LOG_INFO(Render_Recompiler,
                                             "IndexedStridedSource: shader={:#x} stride={} "
                                             "offset={} gpu_index_op={}",
                                             program.info.pgm_hash, table.record_stride_bytes,
                                             table.tsharp_offset_bytes,
                                             static_cast<u32>(table.selector->GetOpcode()));
                                }
                            }
''', 'bounded strided source acceptance evidence')

v=sources[serializer]
v=replace_one(v,'static constexpr u32 ShaderBinaryVersion = 0xfu;',
'''// Recompile shader resources for exact GCN GPU-indexed material record IDs.
static constexpr u32 ShaderBinaryVersion = 0x15u;''','distinct shader binary version')
if 'static constexpr u32 ShaderMetaVersion = 0xau;' not in v:
    raise RuntimeError('Existing metadata version did not match the tested source')

sources[header]=h;sources[discovery]=s;sources[serializer]=v
for p,text in sources.items():
    p.write_text(text)
    print('PATCHED',p.relative_to(root),flush=True)
