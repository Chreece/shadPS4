#!/usr/bin/env python3
"""Bounded descriptor-pointer specialization; never select a GPU lane on the host."""
from pathlib import Path
import hashlib,json,difflib,sys
FILES=[
'src/shader_recompiler/info.h',
'src/shader_recompiler/ir/passes/resource_discover_pass.cpp',
'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp',
'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp']
NEW={
'src/shader_recompiler/indirect_sample_table.h':'indirect_sample_table.h',
'src/shader_recompiler/ir/passes/indirect_sample_plan.h':'indirect_sample_plan.h'}
EXPECTED={
'src/shader_recompiler/info.h':'fb1d82048e73bfa29394dfd4a2c261b7c71133dee864c7e087007e153c38e43c',
'src/shader_recompiler/ir/passes/resource_discover_pass.cpp':'12aa06766ad3bfb79d564f5256b658c5ee58a67bde06fb9660d3de9c4e1f89a2',
'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp':'41651a9b94838c3062500614073056480220c5b34f166e4a3a93a3b837298b90',
'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp':'0cdfd5e70b279c7c28384b2358ca3e0d23edf0e9942831cb74e2a811ec2b9d56'}
IMPLEMENTATION=r'''
// Same mapped guest-memory/coherence path used by existing scalar descriptor
// reads. The range is checked before touching an indirect pointee. No host lane
// is chosen, and unavailable records never become synthetic valid descriptors.
static bool ReadMappedIndirectDescriptor(u64 address, void* destination, u64 bytes) {
    auto* memory = Core::Memory::Instance();
    if (!IndirectSample::Range(address, 0, bytes) ||
        !memory->IsValidMapping(address, bytes)) return false;
    std::memcpy(destination, reinterpret_cast<const void*>(address), bytes);
    return true;
}

IndirectSample::Snapshot Info::ReadIndirectSample(const IndirectSample::Source& source) const {
    return IndirectSample::Read(source, user_data, ReadMappedIndirectDescriptor);
}

bool Info::IndirectSamplesCompatible() const {
    if (indirect_sample_count > indirect_samples.size()) return false;
    for (u32 i = 0; i < indirect_sample_count; ++i) {
        if (ReadIndirectSample(indirect_samples[i].source) != indirect_samples[i].compiled)
            return false;
    }
    return true;
}
'''
SPLIT=r'''
// A GPU-selected record contains a pointer to a texture/sampler structure.
// Enumerate only a complete small table proved by its actual scalar V# extent;
// preserve the original selector and predicate each normal typed sample on GPU.
static void SplitIndirectRawSamples(
    IR::Program& program, std::unordered_map<IR::Inst*, IR::Value>& guards) {
    std::size_t resources = 0;
    for (const auto* block : program.blocks)
        for (const auto& inst : block->Instructions()) resources += IsImageInstruction(inst);
    const std::size_t capacity = std::min<std::size_t>(NUM_IMAGES, NUM_SAMPLERS);
    std::size_t budget = resources < capacity ? capacity - resources : 0;
    auto& info = program.info;
    for (auto* block : program.blocks) {
        for (auto it = block->begin(); it != block->end();) {
            IR::Inst& inst = *it++;
            const auto plan = IndirectSamplePlan::Analyze(inst);
            if (!plan) continue;
            const IndirectSample::Dependency* state = nullptr;
            for (u32 i = 0; i < info.indirect_sample_count; ++i) {
                if (info.indirect_samples[i].source == plan->source) {
                    state = &info.indirect_samples[i];
                    break;
                }
            }
            if (!state) {
                if (info.indirect_sample_count == info.indirect_samples.size()) continue;
                auto& added = info.indirect_samples[info.indirect_sample_count++];
                added.source = plan->source;
                added.compiled = info.ReadIndirectSample(plan->source);
                state = &added;
            }
            const auto& snapshot = state->compiled;
            if (snapshot.status != IndirectSample::Status::Ready || snapshot.count == 0 ||
                snapshot.count - 1u > budget) {
                AmdGpu::Buffer header{};
                std::memcpy(&header, snapshot.buffer_words.data(), sizeof(header));
                LOG_INFO(Render_Recompiler,
                         "IndirectRawSampleDeferred: shader={:#x} reason={} entries={} "
                         "record_bytes={} buffer_stride={} buffer_records={} compiler_limit={}",
                         info.pgm_hash, static_cast<u32>(snapshot.status), snapshot.count,
                         plan->source.record_bytes, static_cast<u32>(header.stride),
                         header.num_records, IndirectSample::MaxEntries);
                continue;
            }
            const auto point = IR::Block::InstructionList::s_iterator_to(inst);
            IR::IREmitter ir{*block, point};
            IR::Value result = ir.CompositeConstruct(ir.Imm32(0.0f), ir.Imm32(0.0f),
                                                     ir.Imm32(0.0f), ir.Imm32(0.0f));
            for (u32 side = 0; side < snapshot.count; ++side) {
                const auto& d = snapshot.descriptors[side];
                const auto low = ir.CompositeConstruct(ir.Imm32(d[0]), ir.Imm32(d[1]),
                                                       ir.Imm32(d[2]), ir.Imm32(d[3]));
                const auto high = ir.CompositeConstruct(ir.Imm32(d[4]), ir.Imm32(d[5]),
                                                        ir.Imm32(d[6]), ir.Imm32(d[7]));
                const auto sampler = ir.CompositeConstruct(ir.Imm32(d[8]), ir.Imm32(d[9]),
                                                           ir.Imm32(d[10]), ir.Imm32(d[11]));
                const auto handle = ir.ImageHandle(low, high);
                const auto selected = ir.IEqual(IR::U32{plan->selector}, ir.Imm32(side));
                auto* clone = &*block->PrependNewInst(point, inst);
                clone->SetArg(0, handle);
                clone->SetArg(1, sampler);
                guards.emplace(clone, selected);
                result = SelectSideResult(ir, selected, IR::Value{clone}, result);
            }
            budget -= snapshot.count - 1u;
            LOG_INFO(Render_Recompiler,
                     "IndirectRawSample: shader={:#x} entries={} record_bytes={} "
                     "image_word={} sampler_word={} original_gpu_selector=true",
                     info.pgm_hash, snapshot.count, plan->source.record_bytes,
                     plan->source.image_word, plan->source.sampler_word);
            inst.ReplaceUsesWithAndRemove(result);
        }
    }
}
'''
def sha(b):return hashlib.sha256(b).hexdigest()
def once(s,a,b):
 if s.count(a)!=1:raise ValueError('CONTEXT_EXPECTED_ONCE:'+a[:120])
 return s.replace(a,b,1)
def transform(old):
 for p in FILES:
  if sha(old[p])!=EXPECTED[p]:raise ValueError('SOURCE_CHANGED_NO_WRITE:'+p)
 out={p:b.decode() for p,b in old.items()}
 p=FILES[0];s=out[p]
 s=once(s,'#include "shader_recompiler/resource.h"','#include "shader_recompiler/resource.h"\n#include "shader_recompiler/indirect_sample_table.h"')
 s=once(s,'    u32 uniform_sample_loop_count{};','    u32 uniform_sample_loop_count{};\n    std::array<IndirectSample::Dependency, 4> indirect_samples{};\n    u32 indirect_sample_count{};')
 s=once(s,'    void RefreshFlatBuf() {','    [[nodiscard]] IndirectSample::Snapshot ReadIndirectSample(const IndirectSample::Source&) const;\n    [[nodiscard]] bool IndirectSamplesCompatible() const;\n\n    void RefreshFlatBuf() {');out[p]=s
 p=FILES[1];s=out[p]
 s=once(s,'#include "common/assert.h"','#include "common/assert.h"\n#include "core/memory.h"\n#include "shader_recompiler/ir/passes/indirect_sample_plan.h"')
 s=once(s,'namespace Shader::Optimization {','namespace Shader {\n'+IMPLEMENTATION+'\n} // namespace Shader\n\nnamespace Shader::Optimization {')
 s=once(s,'ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {',SPLIT+'\nResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {')
 s=once(s,'    SplitUniformRawSamples(program, uniform_sample_guards);','    SplitUniformRawSamples(program, uniform_sample_guards);\n    SplitIndirectRawSamples(program, uniform_sample_guards);');out[p]=s
 p=FILES[2];s=out[p];s=once(s,'if (!info.UniformSampleLoopsCompatible()) {','if (!info.UniformSampleLoopsCompatible() || !info.IndirectSamplesCompatible()) {');out[p]=s
 p=FILES[3];s=out[p]
 s=once(s,'ShaderBinaryVersion = 0x55525301u','ShaderBinaryVersion = 0x49505301u');s=once(s,'ShaderMetaVersion = 0x55525301u','ShaderMetaVersion = 0x49505301u')
 s=once(s,'    if (uniform_sample_loop_count > uniform_sample_loops.size()) return false;',
 '''    if (indirect_sample_count > indirect_samples.size()) return false;
    for (u32 i = 0; i < indirect_sample_count; ++i) {
        if (indirect_samples[i].compiled.count > IndirectSample::MaxEntries ||
            static_cast<u32>(indirect_samples[i].compiled.status) >
                static_cast<u32>(IndirectSample::Status::Ready)) return false;
    }
    if (uniform_sample_loop_count > uniform_sample_loops.size()) return false;''');out[p]=s
 return {p:s.encode() for p,s in out.items()}
def apply(root,evid):
 old={p:(root/p).read_bytes() for p in FILES};new=transform(old)
 for p,name in NEW.items():
  if (root/p).exists():raise ValueError('HEADER_ALREADY_EXISTS:'+p)
  new[p]=Path(__file__).with_name(name).read_bytes()
 evid.mkdir(parents=True,exist_ok=True);manifest={};patch=[]
 for p,data in new.items():
  before=old.get(p,b'')
  if p in old:
   dest=evid/'source-before'/p;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(before)
  dest=evid/'source-after'/p;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
  manifest[p]={'before':sha(before) if p in old else None,'after':sha(data)}
  patch.extend(difflib.unified_diff(before.decode().splitlines(True),data.decode().splitlines(True),'a/'+p if p in old else '/dev/null','b/'+p))
 (evid/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 (evid/'indirect-sample.patch').write_text(''.join(patch))
 for p,data in new.items():
  dest=root/p;tmp=dest.with_name(dest.name+'.indirect-pending');tmp.write_bytes(data);tmp.replace(dest)
 print('INDIRECT_SAMPLE_PATCH=PASS',flush=True);return manifest
if __name__=='__main__':apply(Path(sys.argv[1]),Path(sys.argv[2]))
