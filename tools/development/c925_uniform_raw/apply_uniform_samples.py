#!/usr/bin/env python3
"""Exact-source, fail-closed raw texture-loop translation; no title/hash semantics."""
from pathlib import Path
import hashlib,json,difflib,sys

FIELDS = '''    // Dispatch-uniform bounds used by raw-descriptor loop specialization.
    // These are real SRT words, never fabricated texture allocation lengths.
    struct UniformSampleLoop {
        u32 root_sgpr{};
        u32 count_word{};
        u32 compiled_value{};
        u32 available{};
    };
    std::array<UniformSampleLoop, 16> uniform_sample_loops{};
    u32 uniform_sample_loop_count{};

'''
METHODS = '''    [[nodiscard]] std::optional<u32> ReadUniformSampleCount(u32 root, u32 word) const {
        if (root >= user_data.size() || user_data.size() - root < 2u) return {};
        const u64 base = (u64(user_data[root]) | (u64(user_data[root + 1u]) << 32u)) &
                         0xFFFFFFFFFFFFULL;
        if (base == 0u || (base & 3u) != 0u ||
            u64(word) * sizeof(u32) > 0xFFFFFFFFFFFFULL - base - 3u) return {};
        // Same guest-memory/coherence path as existing scalar SRT reads.
        return ReadUdReg<u32>(root, word);
    }

    [[nodiscard]] bool UniformSampleLoopsCompatible() const {
        if (uniform_sample_loop_count > uniform_sample_loops.size()) return false;
        for (u32 i = 0; i < uniform_sample_loop_count; ++i) {
            const auto& saved = uniform_sample_loops[i];
            const auto now = ReadUniformSampleCount(saved.root_sgpr, saved.count_word);
            if (now.has_value() != bool(saved.available) ||
                (now && *now != saved.compiled_value)) return false;
        }
        return true;
    }

'''
SPLIT = '''// Runtime counts are read only from the same unconditional root SRT source as
// the shader. Cache reuse verifies them BEFORE refreshing old expanded sources.
static void SplitUniformRawSamples(
    IR::Program& program, std::unordered_map<IR::Inst*, IR::Value>& guards) {
    std::size_t resources = 0;
    for (const auto* block : program.blocks) {
        for (const auto& inst : block->Instructions()) resources += IsImageInstruction(inst);
    }
    const std::size_t capacity = std::min<std::size_t>(NUM_IMAGES, NUM_SAMPLERS);
    std::size_t budget = resources < capacity ? capacity - resources : 0;
    auto& info = program.info;
    for (auto* block : program.blocks) {
        for (auto it = block->begin(); it != block->end();) {
            IR::Inst& inst = *it++;
            const auto plan = UniformRawSample::Analyze(inst);
            if (!plan) continue;
            const InfoPersistent::UniformSampleLoop* state = nullptr;
            for (u32 i = 0; i < info.uniform_sample_loop_count; ++i) {
                const auto& prior = info.uniform_sample_loops[i];
                if (prior.root_sgpr == plan->root_sgpr && prior.count_word == plan->count_word) {
                    state = &prior;
                    break;
                }
            }
            if (!state) {
                if (info.uniform_sample_loop_count == info.uniform_sample_loops.size()) continue;
                const auto count = info.ReadUniformSampleCount(plan->root_sgpr, plan->count_word);
                auto& added = info.uniform_sample_loops[info.uniform_sample_loop_count++];
                added = {plan->root_sgpr, plan->count_word, count.value_or(0u), count ? 1u : 0u};
                state = &added;
            }
            // Keep the dependency for empty/unsupported counts as well: a later
            // valid dispatch must not reuse an old null-descriptor permutation.
            const auto offsets = state->available
                ? UniformRawSample::Offsets(*plan, state->compiled_value) : std::nullopt;
            if (!offsets || offsets->size() - 1u > budget) {
                LOG_INFO(Render_Recompiler,
                         "UniformRawSampleDeferred: shader={:#x} bound={} available={} "
                         "root={} count_word={} expansion_limit={}",
                         info.pgm_hash, state->compiled_value, state->available,
                         plan->root_sgpr, plan->count_word, UniformRawSample::MaxAlternatives);
                continue;
            }
            const auto point = IR::Block::InstructionList::s_iterator_to(inst);
            IR::IREmitter ir{*block, point};
            IR::Value result = ir.CompositeConstruct(ir.Imm32(0.0f), ir.Imm32(0.0f),
                                                     ir.Imm32(0.0f), ir.Imm32(0.0f));
            for (u32 side = 0; side < offsets->size(); ++side) {
                std::array<IR::Value, 8> words;
                for (u32 word = 0; word < 8; ++word) {
                    words[word] = ir.ReadConst(plan->base, ir.Imm32((*offsets)[side][word]));
                }
                const auto low = ir.CompositeConstruct(words[0], words[1], words[2], words[3]);
                const auto high = ir.CompositeConstruct(words[4], words[5], words[6], words[7]);
                const auto handle = ir.ImageHandle(low, high);
                const IR::U1 selected = ir.IEqual(IR::U32{plan->selector}, ir.Imm32(plan->first + side));
                auto* branch = &*block->PrependNewInst(point, inst);
                branch->SetArg(0, handle);
                guards.emplace(branch, selected);
                result = SelectSideResult(ir, selected, IR::Value{branch}, result);
            }
            budget -= offsets->size() - 1u;
            LOG_INFO(Render_Recompiler,
                     "UniformRawSample: shader={:#x} first={} count={} root={} count_word={} "
                     "actual_uniform_bound=true gpu_guarded=true",
                     info.pgm_hash, plan->first, offsets->size(), plan->root_sgpr, plan->count_word);
            inst.ReplaceUsesWithAndRemove(result);
        }
    }
}

'''
EMITTER = '''Id EmitImageSampleExplicitLodGuarded(
    EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords, Id lod,
    const IR::Value& offset, Id guard) {
    const Id taken = ctx.OpLabel();
    const Id skipped = ctx.OpLabel();
    const Id merge = ctx.OpLabel();
    ctx.OpSelectionMerge(merge, spv::SelectionControlMask::MaskNone);
    ctx.OpBranchConditional(guard, taken, skipped);
    ctx.AddLabel(taken);
    const Id texel = EmitImageSampleExplicitLod(ctx, inst, handle, coords, lod, offset);
    const Id taken_end = ctx.last_label;
    ctx.OpBranch(merge);
    ctx.AddLabel(skipped);
    const Id zero = ctx.ConstF32(0.0f, 0.0f, 0.0f, 0.0f);
    ctx.OpBranch(merge);
    ctx.AddLabel(merge);
    return ctx.OpPhi(ctx.F32[4], texel, taken_end, zero, skipped);
}

'''
FILES = ['src/shader_recompiler/info.h',
 'src/shader_recompiler/ir/passes/resource_discover_pass.cpp',
 'src/shader_recompiler/ir/passes/resource_pass.h',
 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp',
 'src/shader_recompiler/ir/opcodes.inc','src/shader_recompiler/ir/ir_emitter.h',
 'src/shader_recompiler/ir/ir_emitter.cpp',
 'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp',
 'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h',
 'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp',
 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp']
NEW='src/shader_recompiler/ir/passes/uniform_raw_sample_plan.h'
EXPECTED = {'src/shader_recompiler/info.h': 'ef16d204d0815eb4208b4332dd5277d81f74ff19c5db78edd7783f989168a32a', 'src/shader_recompiler/ir/passes/resource_discover_pass.cpp': '7cb823f358f1c243456ad5db834097df55b0a1a38c1e8833af4a8c4ad9b3d97f', 'src/shader_recompiler/ir/passes/resource_pass.h': '0913930b9b2e946befeecd3ef01f5f2f96ff5806f560150b3bca799115ca5a74', 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp': '57026fa0e8f9e7bf9c7be2c7bd9aa0a953c33b9b432694b065f3d0192af20801', 'src/shader_recompiler/ir/opcodes.inc': '79768a7254dd61a3018c62d3d17f50700a0e5f3400bae132800c113f79f59bc9', 'src/shader_recompiler/ir/ir_emitter.h': '10c2ab32f127f871ebbabc4d4191f538e40c00680231602b5fc4dad63e1a538a', 'src/shader_recompiler/ir/ir_emitter.cpp': '8b979c5f79b0e18afb733c2f530395c2413d46b5b37cf77cbe1357d45acc17c5', 'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp': '72628d2b5e2447677296a1463d8d10e2e960d74009cb9f22b9a2085857da4677', 'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h': '32c1a30b77b3cc9a1d0cab265ce08d4a5e879caf017d6e6ab10a5045f3795f7f', 'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp': '12864f611064779142a1a8060382c561783d776406d9756a0516403691e44f89', 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp': 'c96a019cbe554712b8e5f1fbb38d7f1d3ec37718063b602c992ceeabe41eafa9'}

def once(s,old,new,label):
    if s.count(old)!=1: raise ValueError('SOURCE_CONTEXT:'+label+':'+str(s.count(old)))
    return s.replace(old,new,1)

def transform(before):
    out={p:v.decode() for p,v in before.items()}
    for p,h in EXPECTED.items():
        if hashlib.sha256(before[p]).hexdigest()!=h:raise ValueError('SOURCE_CHANGED:'+p)
    p=FILES[0];s=out[p]
    s=once(s,'#include <span>','#include <span>\n#include <array>\n#include <optional>',p)
    s=once(s,'struct InfoPersistent {','struct InfoPersistent {\n'+FIELDS,p)
    s=once(s,'    void RefreshFlatBuf() {',METHODS+'    void RefreshFlatBuf() {',p)
    out[p]=s
    p=FILES[1];s=out[p]
    s=once(s,'#include "shader_recompiler/ir/passes/finite_raw_image_plan.h"',
      '#include "shader_recompiler/ir/passes/finite_raw_image_plan.h"\n#include "shader_recompiler/ir/passes/uniform_raw_sample_plan.h"',p)
    s=once(s,'ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {',SPLIT+'ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {',p)
    s=once(s,'    ResourceDiscoveryList sharp_usages;','    std::unordered_map<IR::Inst*, IR::Value> uniform_sample_guards;\n    SplitUniformRawSamples(program, uniform_sample_guards);\n    ResourceDiscoveryList sharp_usages;',p)
    s=once(s,'                // Resource discovery must agree with PatchImageSharp:',
      '''                if (const auto guard = uniform_sample_guards.find(&inst);
                    guard != uniform_sample_guards.end()) {
                    sharp_usages.back().image_sample_guard = guard->second;
                }
                // Resource discovery must agree with PatchImageSharp:''',p)
    out[p]=s
    p=FILES[2];out[p]=once(out[p],'    IR::Value image_read_guard{};','    IR::Value image_read_guard{};\n    IR::Value image_sample_guard{};',p)
    p=FILES[3];s=out[p]
    s=once(s,'const AmdGpu::Image& image, const IR::Value& gpu_tsharp_word3) {',
       'const AmdGpu::Image& image, const IR::Value& gpu_tsharp_word3,\n                          const IR::Value& sample_guard) {',p)
    s=once(s,'            return ir.ImageSampleExplicitLod(handle, coords, lod, offset, inst_info);',
      '''            if (!sample_guard.IsEmpty()) {
                LOG_INFO(Render_Recompiler,
                         "UniformRawSampleBound: shader={:#x} binding={} view_type={} "
                         "guest_address={:#x} guarded=true",
                         info.pgm_hash, handle.U32() & 0xFFFFu,
                         static_cast<u32>(image.GetViewType(image_res.is_array)), image.Address());
                return ir.ImageSampleExplicitLodGuarded(
                    handle, coords, lod, offset, IR::U1{sample_guard}, inst_info);
            }
            return ir.ImageSampleExplicitLod(handle, coords, lod, offset, inst_info);''',p)
    s=once(s,'return PatchImageSampleArgs(inst, info, image_res, image, resource.sharps[0].dwords[3]);',
      'return PatchImageSampleArgs(inst, info, image_res, image,\n                                    resource.sharps[0].dwords[3], resource.image_sample_guard);',p)
    # Record genuinely resolved static descriptors; no blanket 'bound' success from placeholders.
    s=once(s,'    auto image = image_res.GetSharp(info);\n    if (image_res.is_atomic_float_emulated) {',
      '''    if (!resource.image_sample_guard.IsEmpty()) {
        LOG_INFO(Render_Recompiler, "UniformRawSampleSharp: shader={:#x} resolved={}",
                 info.pgm_hash, image_res.sharp_fetch.summary !=
                                    SharpFetch<AmdGpu::Image>::Summary::Invalid);
    }
    auto image = image_res.GetSharp(info);\n    if (image_res.is_atomic_float_emulated) {''',p)
    out[p]=s
    p=FILES[4];anchor=next(x for x in out[p].splitlines() if x.startswith('OPCODE(ImageSampleExplicitLod,'))
    newline='OPCODE(ImageSampleExplicitLodGuarded,                       F32x4,          Opaque,         Opaque,         F32,            Opaque,         U1,             )'
    out[p]=once(out[p],anchor,anchor+'\n'+newline,p)
    p=FILES[5];anchor='    [[nodiscard]] Value ImageSampleExplicitLodIndexed('
    out[p]=once(out[p],anchor,'''    [[nodiscard]] Value ImageSampleExplicitLodGuarded(
        const Value& handle, const Value& coords, const F32& lod, const Value& offset,
        const U1& guard, TextureInstInfo info);

'''+anchor,p)
    p=FILES[6];anchor='Value IREmitter::ImageSampleExplicitLodIndexed('
    out[p]=once(out[p],anchor,'''Value IREmitter::ImageSampleExplicitLodGuarded(
    const Value& handle, const Value& coords, const F32& lod, const Value& offset,
    const U1& guard, TextureInstInfo info) {
    return Inst(Opcode::ImageSampleExplicitLodGuarded, Flags{info}, handle, coords, lod, offset, guard);
}

'''+anchor,p)
    p=FILES[7];anchor='Id EmitImageSampleExplicitLodIndexed('
    out[p]=once(out[p],anchor,EMITTER+anchor,p)
    p=FILES[8];anchor='Id EmitImageSampleExplicitLodIndexed('
    out[p]=once(out[p],anchor,'''Id EmitImageSampleExplicitLodGuarded(
    EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords, Id lod,
    const IR::Value& offset, Id guard);
'''+anchor,p)
    p=FILES[9];anchor='        info.RefreshFlatBuf();'
    out[p]=once(out[p],anchor,'''        // A smaller/new root-count domain must be checked BEFORE the old
        // walker's expanded image pointers are refreshed.
        if (!info.UniformSampleLoopsCompatible()) {
            continue;
        }
'''+anchor,p)
    p=FILES[10];s=out[p]
    import re
    for name in ('ShaderBinaryVersion','ShaderMetaVersion'):
        s,n=re.subn(r'(static constexpr u32 '+name+r' = )0x[0-9a-fA-F]+u;',r'\g<1>0x55525301u;',s)
        if n!=1:raise ValueError('VERSION_CONTEXT:'+name)
    anchor='    info.Read(this, sizeof(Shader::InfoPersistent));'
    s=once(s,anchor,anchor+'''
    if (uniform_sample_loop_count > uniform_sample_loops.size()) return false;
    for (u32 i = 0; i < uniform_sample_loop_count; ++i) {
        if (uniform_sample_loops[i].available > 1u) return false;
    }
''',p)
    out[p]=s
    return {p:s.encode() for p,s in out.items()}

def apply(root,evid):
    root=Path(root);evid=Path(evid)
    if (root/NEW).exists():raise ValueError('ALREADY_APPLIED')
    before={p:(root/p).read_bytes() for p in FILES}
    after=transform(before);after[NEW]=Path(__file__).with_name('uniform_raw_sample_plan.h').read_bytes()
    evid.mkdir(parents=True,exist_ok=True);manifest={};patch=[]
    for p,data in after.items():
        original=before.get(p,b'')
        if p in before:
            q=evid/'source-before'/p;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(original)
        q=evid/'source-after'/p;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(data)
        manifest[p]={'before':hashlib.sha256(original).hexdigest() if p in before else None,'after':hashlib.sha256(data).hexdigest()}
        patch.extend(difflib.unified_diff(original.decode().splitlines(True),data.decode().splitlines(True),'a/'+p if p in before else '/dev/null','b/'+p))
    (evid/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (evid/'uniform-raw-samples.patch').write_text(''.join(patch))
    for p,data in after.items():
        target=root/p;tmp=target.with_name(target.name+'.uniform-pending');tmp.write_bytes(data);tmp.replace(target)
    print('UNIFORM_RAW_SAMPLE_PATCH=PASS',flush=True)
    return manifest

if __name__=='__main__':apply(sys.argv[1],sys.argv[2])
