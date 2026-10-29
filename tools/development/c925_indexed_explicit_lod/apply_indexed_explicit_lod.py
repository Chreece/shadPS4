#!/usr/bin/env python3
"""Exact-source C925 extension: bounded GPU-indexed 2D SAMPLE_L / SAMPLE_LZ.
No array/depth/gather/type inference and no changes to accepted renderer state.
"""
from __future__ import annotations
import difflib,hashlib,json,re
from pathlib import Path
import sys

PATHS=(
 'src/shader_recompiler/ir/passes/indexed_sample_eligibility.h',
 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp',
 'src/shader_recompiler/ir/ir_emitter.h',
 'src/shader_recompiler/ir/ir_emitter.cpp',
 'src/shader_recompiler/ir/opcodes.inc',
 'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h',
 'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp',
 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp')
# Filled from the uploaded, reconstructed accepted candidate, not from fork main.
EXPECTED={'src/shader_recompiler/ir/passes/indexed_sample_eligibility.h': '40b27dab45cf2fd4d3b5d000bfd811c6e6bcfe0cda3a60770981b20f4e8fb90d', 'src/shader_recompiler/ir/passes/resource_patching_pass.cpp': '8f470e1de979d79a8546c87f0c363779b343a9acc7f43a0427ecd4cec2c7517c', 'src/shader_recompiler/ir/ir_emitter.h': '581983684c58c9208e848c5fe595902757615cb88ef6b5d4aa3246aba8a038be', 'src/shader_recompiler/ir/ir_emitter.cpp': 'ce9426fd202f1bf38cefd3bdf7f4766558a3d0e477c0396d77a309acb0b70270', 'src/shader_recompiler/ir/opcodes.inc': 'aab9647184b2499935e576168939feffa6f5cacf5b80eda30580fde301fd032d', 'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h': '7bd1902cc737edf7f73bcfadce3cf921049a7ede75c76ca1e3feb4e77ea889bc', 'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp': '7895466b9021f48aad7cc8ab4475decda14b7f1a3c213e7bd3ddb2562a3facab', 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp': 'a69068d49ed2716d7d8635845b8eefc915aafa71537495e564f8a05dfc40302e'}

EMITTER=r'''
Id EmitImageSampleExplicitLodIndexed(
    EmitContext& ctx, IR::Inst*, u32 handle, Id coords, Id lod,
    const IR::Value& offset, Id gpu_texture_id) {
    const auto& texture = ctx.images[handle & 0xFFFFu];
    ASSERT(texture.is_guest_indexed && texture.image_array_size >= 2u);
    ASSERT(!texture.is_guest_mixed_indexed);
    ASSERT(texture.view_type == AmdGpu::ImageType::Color2D);
    ASSERT(ctx.profile.supports_sampled_image_nonuniform_indexing);

    // Keep the original per-invocation index. Only out-of-range IDs select
    // the existing final null slot; never substitute a host-chosen texture.
    const Id valid = ctx.OpULessThan(
        ctx.U1[1], gpu_texture_id, ctx.ConstU32(texture.image_array_size - 1u));
    const Id bounded = ctx.OpSelect(
        ctx.U32[1], valid, gpu_texture_id, ctx.ConstU32(texture.image_array_size - 1u));
    ctx.Decorate(bounded, spv::Decoration::NonUniform);
    const Id pointer_type =
        ctx.TypePointer(spv::StorageClass::UniformConstant, texture.image_type);
    const Id pointer =
        ctx.OpAccessChain(pointer_type, texture.id, std::array{bounded});
    ctx.Decorate(pointer, spv::Decoration::NonUniform);
    const Id image = ctx.OpLoad(texture.image_type, pointer);
    ctx.Decorate(image, spv::Decoration::NonUniform);
    const Id sampler = LoadIndexedGuestSampler(
        ctx, handle, bounded, texture.image_array_size);
    const Id sampled = ctx.OpSampledImage(texture.sampled_type, image, sampler);
    ctx.Decorate(sampled, spv::Decoration::NonUniform);
    const Id fixed_coords = FixImageCoords<true>(ctx, coords, texture.view_type);
    ImageOperands operands;
    // SAMPLE_L preserves its real GPU LOD operand. SAMPLE_LZ reaches this
    // same operation with the opcode-defined literal LOD 0 from IR lowering.
    operands.Add(spv::ImageOperandsMask::Lod, lod);
    operands.AddOffset(ctx, texture.view_type, offset);
    const Id value = ctx.OpImageSampleExplicitLod(
        texture.data_types->Get(4), sampled, fixed_coords,
        operands.mask, operands.operands);
    return texture.is_integer ? ctx.OpBitcast(ctx.F32[4], value) : value;
}
'''


def sha(data):return hashlib.sha256(data).hexdigest()
def once(text,old,new,label):
    if text.count(old)!=1:raise ValueError('CONTEXT_MISMATCH:'+label)
    return text.replace(old,new,1)


def transform(data):
    for path in PATHS:
        if sha(data[path])!=EXPECTED[path]:raise ValueError('SOURCE_CHANGED:'+path)
    out={path:data[path].decode() for path in PATHS}
    path=PATHS[0]
    out[path]=once(out[path],'''    return !flags.is_depth && !flags.is_array && !flags.is_gather &&
           !flags.has_lod && !flags.force_level0 &&
           !flags.has_lod_clamp && !flags.is_r128 && !flags.is_unnormalized;''',
'''    const bool common = !flags.is_depth && !flags.is_array && !flags.is_gather &&
                        !flags.has_lod_clamp && !flags.is_r128 && !flags.is_unnormalized;
    if (!common) return false;
    if (flags.has_lod || flags.force_level0) {
        // Explicit LOD and opcode-defined level zero have a dedicated indexed
        // IR/SPIR-V operation. Do not combine Lod with Bias or Grad operands.
        return !(flags.has_lod && flags.force_level0) &&
               !flags.has_bias && !flags.has_derivatives;
    }
    return true;''',path)
    path=PATHS[1];s=out[path]
    s=once(s,'''                    info.hw_stage == HwStage::Fragment &&
                    entries <= profile.max_guest_mixed_indexed_image_entries;''',
'''                    info.hw_stage == HwStage::Fragment &&
                    !inst_info.has_lod && !inst_info.force_level0 &&
                    entries <= profile.max_guest_mixed_indexed_image_entries;''',path+'mixed')
    s=once(s,'''        if (explicit_lod) {
            return ir.ImageSampleExplicitLod(handle, coords, lod, offset, inst_info);
        }''',
'''        if (explicit_lod) {
            if (image_res.is_guest_indexed) {
                // INDEXED_EXPLICIT_LOD_V1: only complete, valid, nonzero tables
                // admitted by PatchImageSharp may reach this indexed path.
                static thread_local std::unordered_map<u64, u32> reports;
                if ((reports.size() < 128u || reports.contains(info.pgm_hash)) &&
                    reports[info.pgm_hash]++ < 3u) {
                    LOG_INFO(Render_Recompiler,
                             "IndexedExplicitLodBound: shader={:#x} stage={} "
                             "mode={} entries={} record_stride={} indexed_sampler={}",
                             info.pgm_hash, static_cast<u32>(info.hw_stage),
                             inst_info.force_level0 ? "LZ" : "L",
                             image_res.indexed_guest_entries,
                             image_res.indexed_record_stride_bytes,
                             sampler_res.is_guest_indexed);
                }
                return ir.ImageSampleExplicitLodIndexed(
                    handle, coords, lod, offset, IR::U32{inst.Arg(1)}, inst_info);
            }
            return ir.ImageSampleExplicitLod(handle, coords, lod, offset, inst_info);
        }''',path+'lowering')
    # Rejection reports expose original opcode flags. These bounded records
    # distinguish new eligible but empty tables from modes still unsupported.
    s=once(s,'"stride={} offset={} sample_mode_ok={} sampler_mode_ok={} "',
        '"stride={} offset={} sample_mode_ok={} sampler_mode_ok={} "\n                         "sample_flags={:#x} "',path+'reject format')
    s=once(s,'                         sampled_eligible, sampler_eligible,\n',
        '                         sampled_eligible, sampler_eligible, inst_info.raw & 0xffffu,\n',path+'reject args')
    out[path]=s
    path=PATHS[2];anchor='    [[nodiscard]] Value ImageSampleDrefImplicitLod('
    pos=out[path].index(anchor)
    out[path]=out[path][:pos]+'''    [[nodiscard]] Value ImageSampleExplicitLodIndexed(
        const Value& handle, const Value& coords, const F32& lod,
        const Value& offset, const U32& gpu_texture_id, TextureInstInfo info);

'''+out[path][pos:]
    path=PATHS[3];anchor='Value IREmitter::ImageSampleDrefImplicitLod('
    pos=out[path].index(anchor)
    out[path]=out[path][:pos]+'''Value IREmitter::ImageSampleExplicitLodIndexed(
    const Value& handle, const Value& coords, const F32& lod,
    const Value& offset, const U32& gpu_texture_id, TextureInstInfo info) {
    return Inst(Opcode::ImageSampleExplicitLodIndexed, Flags{info}, handle, coords,
                lod, offset, gpu_texture_id);
}

'''+out[path][pos:]
    path=PATHS[4];anchor=next(x for x in out[path].splitlines(True) if x.startswith('OPCODE(ImageSampleExplicitLod,'))
    out[path]=once(out[path],anchor,anchor+'OPCODE(ImageSampleExplicitLodIndexed,                       F32x4,          Opaque,         Opaque,         F32,            Opaque,         U32,            )\n',path)
    path=PATHS[5];anchor='Id EmitImageSampleDrefImplicitLod('
    pos=out[path].index(anchor)
    out[path]=out[path][:pos]+'''Id EmitImageSampleExplicitLodIndexed(EmitContext& ctx, IR::Inst* inst, u32 handle,
                                    Id coords, Id lod, const IR::Value& offset,
                                    Id gpu_texture_id);
'''+out[path][pos:]
    path=PATHS[6];anchor='Id EmitImageSampleDrefImplicitLod('
    pos=out[path].index(anchor);out[path]=out[path][:pos]+EMITTER+'\n'+out[path][pos:]
    path=PATHS[7]
    for name in ('ShaderBinaryVersion','ShaderMetaVersion'):
        out[path]=once(out[path],f'{name} = 0x49545301u;',f'{name} = 0x494c4401u;',path+name)
    return {p:s.encode() for p,s in out.items()}


def apply(root,evidence):
    before={p:(root/p).read_bytes() for p in PATHS}
    after=transform(before)
    evidence.mkdir(parents=True,exist_ok=True)
    manifest={};diff=[]
    for p in PATHS:
        for label,values in [('before',before),('after',after)]:
            q=evidence/('source-'+label)/p;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(values[p])
        manifest[p]={'before':sha(before[p]),'after':sha(after[p])}
        diff.extend(difflib.unified_diff(before[p].decode().splitlines(True),after[p].decode().splitlines(True),'a/'+p,'b/'+p))
    (evidence/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (evidence/'indexed-explicit-lod.patch').write_text(''.join(diff))
    for p in PATHS:
        dest=root/p;temp=dest.with_name(dest.name+'.indexed-lod-pending')
        temp.write_bytes(after[p]);temp.replace(dest)
    print('INDEXED_EXPLICIT_LOD_SOURCE=APPLIED',flush=True)
    return manifest

if __name__=='__main__':apply(Path(sys.argv[1]),Path(sys.argv[2]))
