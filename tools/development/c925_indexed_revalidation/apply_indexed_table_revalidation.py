from __future__ import annotations
import difflib, hashlib, json, pathlib, re, sys

HELPER = r'''
// A rejected or empty table is still a specialization dependency. Snapshot
// the complete bounded capacity, independently of the null image placeholder.
struct GuestIndexedTableState {
    std::uint32_t entries{};
    bool tracked{};
    constexpr bool operator==(const GuestIndexedTableState&) const = default;
};

[[nodiscard]] constexpr GuestIndexedTableState MakeGuestIndexedTableState(
    bool source_available, std::uint32_t buffer_stride, std::uint32_t records,
    std::uint32_t record_stride, std::uint32_t descriptor_offset,
    std::uint32_t entry_limit) noexcept {
    if (entry_limit == 0) return {};
    if (!source_available) return {0, true};
    const auto entries = record_stride != 0
        ? CompleteGuestTsharpRecordEntries(buffer_stride, records, record_stride,
                                          descriptor_offset, entry_limit)
        : CompleteGuestTsharpTableEntries(buffer_stride, records, entry_limit);
    return {entries, true};
}
'''

METHOD = r'''
    [[nodiscard]] Optimization::GuestIndexedTableState IndexedTableState(
        const auto& info) const noexcept {
        if (indexed_entry_limit == 0u) return {};
        AmdGpu::Buffer table{};
        const bool available = indexed_table_fetch.Fetch(info.flattened_ud_buf.data(), &table) &&
                               table.Valid() && table.base_address != 0u;
        return Optimization::MakeGuestIndexedTableState(
            available, static_cast<u32>(table.stride), table.num_records,
            indexed_record_stride_bytes, indexed_descriptor_offset_bytes, indexed_entry_limit);
    }
'''

LIMIT_BLOCK = '''            const u32 record_stride=resource.indexed_record_stride_bytes;
            const u32 record_offset=resource.indexed_descriptor_offset_bytes;
            const bool needs_indexed_sampler = record_stride != 0u &&
                                               !verified_uniform_sampler;
            const u32 indexed_limit = needs_indexed_sampler
                ? std::min(profile.max_guest_indexed_image_entries,
                           profile.max_guest_indexed_sampler_entries)
                : profile.max_guest_indexed_image_entries;
'''

def once(text: str, old: str, new: str, label: str) -> str:
    n = text.count(old)
    if n != 1:
        raise ValueError(f'CONTEXT_MISMATCH:{label}:expected=1:actual={n}')
    return text.replace(old, new, 1)

def transform(files: dict[str,str]) -> dict[str,str]:
    out=dict(files)
    hp='src/shader_recompiler/guest_tsharp_array.h'
    rp='src/shader_recompiler/resource.h'
    pp='src/shader_recompiler/ir/passes/resource_patching_pass.cpp'
    sp='src/shader_recompiler/specialization.h'
    vp='src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
    cp='src/video_core/renderer_vulkan/vk_pipeline_cache.cpp'
    for path,s in out.items():
        if 'GUEST_INDEXED_TABLE_REVALIDATION_V1' in s:
            raise ValueError('ALREADY_PATCHED:'+path)
    out[hp]=once(out[hp],'} // namespace Shader::Optimization',HELPER+'\n} // namespace Shader::Optimization',hp)
    s=out[rp]
    s=once(s,'#include "shader_recompiler/ir/type.h"','#include "shader_recompiler/ir/type.h"\n#include "shader_recompiler/guest_tsharp_array.h"',rp+' include')
    marker='struct ImageResource {'
    a=s.index(marker);b=s.index('using ImageResourceList',a)
    block=s[a:b]
    block=once(block,'    u32 indexed_guest_entries{};',
      '    u32 indexed_guest_entries{};\n    // Retained even when this compilation emits a null image.\n    u32 indexed_entry_limit{};',rp+' limit')
    block=once(block,'    u32 NumBindings(const auto& info) const {',METHOD+'\n    u32 NumBindings(const auto& info) const {',rp+' method')
    out[rp]=s[:a]+block+s[b:]
    s=out[pp]
    a=s.index('    u32 Add(const ImageResource& desc)');b=s.index('    u32 Add(const SamplerResource& desc)',a)
    block=s[a:b]
    block=once(block,'                   desc.indexed_guest_entries == existing.indexed_guest_entries &&',
      '                   desc.indexed_guest_entries == existing.indexed_guest_entries &&\n                   desc.indexed_entry_limit == existing.indexed_entry_limit &&',pp+' dedup')
    s=s[:a]+block+s[b:]
    s=once(s,LIMIT_BLOCK,'',pp+' move limits')
    before='''        const auto table_fetch=ConstructSharpFetch<AmdGpu::Buffer>(resource.indexed_table_sharp);
        AmdGpu::Buffer table{};'''
    moved='\n'.join(line[4:] if line.startswith('    ') else line for line in LIMIT_BLOCK.rstrip('\n').split('\n'))+'\n'
    after='''        const auto table_fetch=ConstructSharpFetch<AmdGpu::Buffer>(resource.indexed_table_sharp);
'''+moved+'''        // Keep the source and limit when the initial header is empty/invalid.
        // A later draw/dispatch must not reuse a baked null table after it changes.
        image_res.indexed_table_fetch = table_fetch;
        image_res.indexed_record_stride_bytes = record_stride;
        image_res.indexed_descriptor_offset_bytes = record_offset;
        image_res.indexed_entry_limit = indexed_limit;
        AmdGpu::Buffer table{};'''
    s=once(s,before,after,pp+' source retention')
    old='''            const u32 entries=record_stride!=0u
                ? CompleteGuestTsharpRecordEntries(
                    static_cast<u32>(table.stride),static_cast<u32>(table.num_records),
                    record_stride,record_offset,indexed_limit)
                : CompleteGuestTsharpTableEntries(
                    static_cast<u32>(table.stride),static_cast<u32>(table.num_records),
                    indexed_limit);'''
    s=once(s,old,'            const u32 entries = image_res.IndexedTableState(info).entries;',pp+' capacity')
    out[pp]=s
    s=out[sp]
    s=once(s,'    SmallVector<ImageSpecialization, 16> images;',
      '    SmallVector<ImageSpecialization, 16> images;\n    SmallVector<Optimization::GuestIndexedTableState, 16> indexed_table_states;',sp+' vector')
    anchor='        : info{&info_}, runtime_info{runtime_info_}, start{start_} {'
    s=once(s,anchor,anchor+'''
        // GUEST_INDEXED_TABLE_REVALIDATION_V1
        // GetSharp() intentionally returns a null representative for indexed
        // images. Do not let that suppress their changing capacity dependency.
        for (const auto& image : info_.images) {
            indexed_table_states.push_back(image.IndexedTableState(info_));
        }''',sp+' snapshot')
    anchor='''        if (!Valid() || !other.Valid()) {
            return false;
        }
'''
    s=once(s,anchor,anchor+'''
        // This comparison must run even when the ordinary image bit is unset.
        if (indexed_table_states != other.indexed_table_states) {
            return false;
        }
''',sp+' compare')
    out[sp]=s
    s=out[vp]
    for name in ('ShaderBinaryVersion','ShaderMetaVersion'):
        pat=rf'(static constexpr u32 {name}\s*=\s*)(?:0x[0-9a-fA-F]+|[0-9]+)u?(\s*;)'
        s,n=re.subn(pat,rf'\g<1>0x49545301u\2',s)
        if n!=1:raise ValueError('CACHE_VERSION_CONTEXT:'+name)
    s=once(s,'    spec.Write(images);','    spec.Write(images);\n    spec.Write(indexed_table_states);',vp+' writer')
    s=once(s,'    spec.Read(images);','    spec.Read(images);\n    spec.Read(indexed_table_states);',vp+' reader')
    out[vp]=s
    s=out[cp]
    # This path is the captured exact C925 per-permutation Info implementation.
    anchor='''        auto current = Shader::StageSpecialization(info, runtime_info, profile, first_binding);
        if (!(saved.spec == current)) {'''
    log='''        auto current = Shader::StageSpecialization(info, runtime_info, profile, first_binding);
        if (saved.spec.indexed_table_states != current.indexed_table_states) {
            static thread_local u32 shape_reports = 0;
            if (shape_reports < 64u) {
                const size_t count = std::min(saved.spec.indexed_table_states.size(),
                                              current.indexed_table_states.size());
                for (size_t i = 0; i < count && shape_reports < 64u; ++i) {
                    const auto& old_shape = saved.spec.indexed_table_states[i];
                    const auto& new_shape = current.indexed_table_states[i];
                    if (old_shape != new_shape) {
                        ++shape_reports;
                        LOG_INFO(Render_Vulkan,
                                 "INDEXED_TABLE_REVALIDATE shader={:#x} image={} "
                                 "compiled_entries={} current_entries={} tracked={}",
                                 info.pgm_hash, i, old_shape.entries, new_shape.entries,
                                 new_shape.tracked);
                    }
                }
            }
        }
        if (!(saved.spec == current)) {'''
    s=once(s,anchor,log,cp+' log')
    out[cp]=s
    return out

PATHS=[
 'src/shader_recompiler/guest_tsharp_array.h',
 'src/shader_recompiler/resource.h',
 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp',
 'src/shader_recompiler/specialization.h',
 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp',
 'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp']

def apply(root:pathlib.Path,evid:pathlib.Path)->None:
    original={p:(root/p).read_text() for p in PATHS}
    new=transform(original)
    # Every transformation is validated before touching any source file.
    evid.mkdir(parents=True,exist_ok=True)
    manifest={}
    diff=[]
    for p in PATHS:
        src=root/p; back=evid/'source-before'/p
        back.parent.mkdir(parents=True,exist_ok=True);back.write_bytes(src.read_bytes())
        manifest[p]={'before':hashlib.sha256(src.read_bytes()).hexdigest(),'after':hashlib.sha256(new[p].encode()).hexdigest()}
        diff.extend(difflib.unified_diff(original[p].splitlines(True),new[p].splitlines(True),'a/'+p,'b/'+p))
    (evid/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (evid/'indexed-table-revalidation.patch').write_text(''.join(diff))
    for p in PATHS:
        dest=root/p; temp=dest.with_name(dest.name+'.revalidation-pending')
        temp.write_text(new[p]);temp.replace(dest)
    print('INDEXED_TABLE_REVALIDATION_PATCH=PASS',flush=True)

if __name__=='__main__':apply(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]))
