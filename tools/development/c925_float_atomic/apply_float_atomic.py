#!/usr/bin/env python3
"""Exact-source C925 correction: typed uint view + one side effect per float atomic."""
from __future__ import annotations
import difflib, hashlib, json, pathlib, re, sys

FILES = (
 'src/shader_recompiler/resource.h',
 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp',
 'src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp',
 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp',
)
EXPECTED = {'src/shader_recompiler/resource.h': '669eaffcb9743a3ed00a9d14bb8fa70de87f44497bae9f923746bab35ba337f8', 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp': 'f9616b3f42ad31271b6b295a01458706108b13beda995434f360f8ba7c82a76b', 'src/shader_recompiler/backend/spirv/emit_spirv_atomic.cpp': 'a10561ad30084f072cbdf9ffcc27a49fc018ffc68adf0b1a253f5da707c9f072', 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp': 'a8a6067d65e8683f29b5cd87100b223c39508fba84cea186e6baa1b6f39f5044'}

BRANCH_HELPER = r'''
// FLOAT_ATOMIC_SINGLE_SIDE_EFFECT_V1
// Selecting a result does not select a memory side effect. Emit the two
// alternatives in disjoint blocks, preserving the old-value return of the
// one atomic that actually executes. The inherited sign-bit ordering is
// unchanged; this is not a new NaN policy for guest floating atomics.
template <typename NegativeAtomic, typename PositiveAtomic>
Id FloatAtomicBySign(EmitContext& ctx, Id value, NegativeAtomic negative_atomic,
                    PositiveAtomic positive_atomic) {
    const Id bits = ctx.OpBitcast(ctx.U32[1], value);
    const Id negative = ctx.OpINotEqual(
        ctx.U1[1],
        ctx.OpBitFieldUExtract(ctx.U32[1], bits, ctx.ConstU32(31u), ctx.ConstU32(1u)),
        ctx.u32_zero_value);
    const Id negative_label = ctx.OpLabel();
    const Id positive_label = ctx.OpLabel();
    const Id merge = ctx.OpLabel();
    ctx.OpSelectionMerge(merge, spv::SelectionControlMask::MaskNone);
    ctx.OpBranchConditional(negative, negative_label, positive_label);
    ctx.AddLabel(negative_label);
    const Id negative_old = negative_atomic(bits);
    const Id negative_end = ctx.last_label;
    ctx.OpBranch(merge);
    ctx.AddLabel(positive_label);
    const Id positive_old = positive_atomic(bits);
    const Id positive_end = ctx.last_label;
    ctx.OpBranch(merge);
    ctx.AddLabel(merge);
    const Id old_bits = ctx.OpPhi(ctx.U32[1], negative_old, negative_end,
                                  positive_old, positive_end);
    return ctx.OpBitcast(ctx.F32[1], old_bits);
}
'''

VIEW_HELPER = r'''
    // Only the emulated float-atomic descriptor is reinterpreted. Normal
    // float reads/writes of the same guest image keep their own descriptor.
    // Both SPIR-V declaration and Vulkan image-view creation call GetSharp,
    // so the integer pointer and integer image view cannot disagree.
    constexpr AmdGpu::Image AtomicAccessSharp(AmdGpu::Image image) const noexcept {
        if (!is_atomic_float_emulated) {
            return image;
        }
        if (image.base_address == 0u) {
            // A typed null descriptor still needs a legal atomic image type.
            image.data_format = static_cast<u64>(AmdGpu::DataFormat::Format32);
        }
        if (image.GetDataFmt() == AmdGpu::DataFormat::Format32) {
            image.num_format = static_cast<u64>(AmdGpu::NumberFormat::Uint);
        }
        return image;
    }
'''

def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def once(s,old,new,label):
    if s.count(old)!=1: raise ValueError(f'CONTEXT_MISMATCH:{label}:count={s.count(old)}')
    return s.replace(old,new,1)

def function_range(s,name):
    a=s.index('Id '+name+'(');start=s.index('{',a);level=0
    for i in range(start,len(s)):
        if s[i]=='{':level+=1
        elif s[i]=='}':
            level-=1
            if not level:return a,i+1
    raise ValueError('FUNCTION_END_MISSING:'+name)

def transform(old:dict[str,bytes])->dict[str,bytes]:
    for p in FILES:
        if p not in old or sha(old[p])!=EXPECTED[p]:raise ValueError('SOURCE_CHANGED_NO_WRITE:'+p)
    out={p:b.decode() for p,b in old.items()}
    p=FILES[0];s=out[p]
    a=s.index('struct ImageResource {');b=s.index('using ImageResourceList',a);part=s[a:b]
    part=once(part,'    bool is_atomic{};','    bool is_atomic{};\n    bool is_atomic_float_emulated{};',p)
    anchor='    constexpr AmdGpu::Image GetSharp(const auto& info) const noexcept {'
    part=once(part,anchor,VIEW_HELPER+'\n'+anchor,p+' helper')
    part=once(part,'            return AmdGpu::Image::Null(is_depth);',
              '            return AtomicAccessSharp(AmdGpu::Image::Null(is_depth));',p+' null')
    part=once(part,'        return image;\n    }\n\n    constexpr bool Fetch',
              '        return AtomicAccessSharp(image);\n    }\n\n    constexpr bool Fetch',p+' typed view')
    out[p]=s[:a]+part+s[b:]
    p=FILES[1];s=out[p]
    s=once(s,'                   desc.is_depth == existing.is_depth && desc.is_r128 == existing.is_r128 &&',
        '                   desc.is_depth == existing.is_depth && desc.is_r128 == existing.is_r128 &&\n'
        '                   desc.is_atomic_float_emulated == existing.is_atomic_float_emulated &&',p+' independent aliases')
    s=once(s,'        .is_atomic = is_atomic,',
        '''        .is_atomic = is_atomic,
        .is_atomic_float_emulated = !profile.supports_image_fp32_atomic_min_max &&
            (inst.GetOpcode() == IR::Opcode::ImageAtomicFMin32 ||
             inst.GetOpcode() == IR::Opcode::ImageAtomicFMax32),''',p+' flag')
    # Restrict to PatchImageSharp; finite raw-image planning has another fetch.
    a=s.index('void PatchImageSharp('); b=s.index('static IR::Value IsMbcntWithExec',a)
    part=s[a:b]
    part=once(part,'    auto image = image_res.GetSharp(info);',
        '''    auto image = image_res.GetSharp(info);
    if (image_res.is_atomic_float_emulated) {
        ASSERT_MSG(image.GetDataFmt() == AmdGpu::DataFormat::Format32 &&
                   image.GetNumberFmt() == AmdGpu::NumberFormat::Uint,
                   "Emulated float32 image atomics require a 32-bit integer alias view");
        static thread_local u32 atomic_view_reports = 0;
        if (atomic_view_reports++ < 32u) {
            LOG_INFO(Render_Recompiler,
                     "EmulatedImageFloatAtomicU32View: shader={:#x} guest_address={:#x}",
                     info.pgm_hash, image.Address());
        }
    }''',p+' checked type')
    out[p]=s[:a]+part+s[b:]
    p=FILES[2];s=out[p]
    s=once(s,'std::pair<Id, Id> AtomicArgs(EmitContext& ctx) {',
              BRANCH_HELPER+'\nstd::pair<Id, Id> AtomicArgs(EmitContext& ctx) {',p+' helper')
    for kind,ptr in [('Image','coords'),('Buffer','address')]:
        for operation,negative,positive in [('Min','UMax','SMin'),('Max','UMin','SMax')]:
            name='Emit'+kind+'AtomicF'+operation+'32'
            a,b=function_range(s,name);block=s[a:b]
            start=block.index('    const auto u32_value = ctx.OpBitcast(')
            block=block[:start]+f'''    return FloatAtomicBySign(
        ctx, value,
        [&](Id bits) {{ return Emit{kind}Atomic{negative}32(ctx, inst, handle, {ptr}, bits); }},
        [&](Id bits) {{ return Emit{kind}Atomic{positive}32(ctx, inst, handle, {ptr}, bits); }});
}}'''
            s=s[:a]+block+s[b:]
    out[p]=s
    p=FILES[3];s=out[p]
    for name in ('ShaderBinaryVersion','ShaderMetaVersion'):
        s=once(s,f'{name} = 0x46524901u;',f'{name} = 0x46415401u;',p+name)
    out[p]=s
    return {p:s.encode() for p,s in out.items()}

def apply(root:pathlib.Path,evid:pathlib.Path):
    old={p:(root/p).read_bytes() for p in FILES};new=transform(old)
    manifest={};diff=[]
    for p in FILES:
        for label,data in [('before',old[p]),('after',new[p])]:
            q=evid/('source-'+label)/p;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(data)
        manifest[p]={'before':sha(old[p]),'after':sha(new[p])}
        diff+=list(difflib.unified_diff(old[p].decode().splitlines(True),new[p].decode().splitlines(True),'a/'+p,'b/'+p))
    (evid/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (evid/'float-atomic-source.patch').write_text(''.join(diff))
    for p,data in new.items():
        q=root/p;temp=q.with_name(q.name+'.atomic-pending');temp.write_bytes(data);temp.replace(q)
    print('FLOAT_ATOMIC_TYPED_VIEW_AND_SINGLE_SIDE_EFFECT=APPLIED',flush=True)
    return manifest
if __name__=='__main__':apply(pathlib.Path(sys.argv[1]),pathlib.Path(sys.argv[2]))
