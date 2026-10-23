#!/usr/bin/env python3
from __future__ import annotations
import fcntl, hashlib, importlib.util, json, os, re, shutil, stat, subprocess, sys, tarfile, tempfile, time
from pathlib import Path

ROOT=Path.home()/'projects/shadps4'
SRC=ROOT/'clean-rebase-verified/source'
BASE='04d891dc33bb1c8c89789b5ff039049901f95a41'
PARENT='975ee9812d93fb0e8094e0581640fb705c336d68'
PARENT_TREE='d921f3f51c546aa8b9dfe7e230aaefeaf0b3c446'
GEN_BASE='9f4c2da6107cb2b31578cc4b35f5e4ed85637dab'
GEN='464e32e92b7f36fede81f0d65ca0e81934051d1e'
PREV=ROOT/'logs/masked-wave-build-20261023-130845-3042481/info'
PREV_BIN='fb5d18e8342cd6701ef31831d389e99f5b9b877de1214e42f1655658d25cb7fb'
PREV_REPORT='0c0a96188e02f1c86be6f6e1e26f280e0f4bf3c06445a2e563a742d66cd82590'
PREV_MANIFEST='28c88069192f5b56689e59eef3ea2045f1280394a3cde3840913d16230823c8c'
OLD=ROOT/'scripts/shadps4-buffer-phi-launch-20261023.py'
OLD_SHA='d6c0c858a6f60d16a42248cff3589fb33cdd4f8897780eda4ad07437a771b44c'
SIRIT_BASE='fc2b42d5e57ee2f638676c0c02f7fd71934b596a'
FILES=(
'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp',
'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h',
'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp',
'src/shader_recompiler/backend/spirv/spirv_emit_context.h',
'src/shader_recompiler/guest_tsharp_array.h',
'src/shader_recompiler/ir/ir_emitter.cpp',
'src/shader_recompiler/ir/ir_emitter.h',
'src/shader_recompiler/ir/opcodes.inc',
'src/shader_recompiler/ir/passes/indexed_image_selector.h',
'src/shader_recompiler/ir/passes/resource_discover_pass.cpp',
'src/shader_recompiler/ir/passes/resource_pass.h',
'src/shader_recompiler/ir/passes/resource_patching_pass.cpp',
'src/shader_recompiler/profile.h',
'src/shader_recompiler/resource.h',
'src/video_core/renderer_vulkan/vk_instance.cpp',
'src/video_core/renderer_vulkan/vk_instance.h',
'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp',
'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp',
'src/video_core/renderer_vulkan/vk_rasterizer.cpp',
)

def env(extra=None):
    e={k:v for k,v in os.environ.items() if k not in ('GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_COMMON_DIR')}
    if extra:e.update(extra)
    return e
def run(a,check=True,capture=True,input=None,timeout=None):
    kw=dict(env=env(),check=check,input=input,timeout=timeout)
    if capture:kw.update(stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    return subprocess.run([str(x) for x in a],**kw)
def git(*a,input=None,e=None,check=True):
    r=subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','gc.auto=0','-C',str(SRC),*a],
                     env=e or env(),input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=check)
    return r.stdout
def show(rev,p): return git('show',f'{rev}:{p}')
def exists(spec): return run(['git','-C',SRC,'cat-file','-e',spec],check=False).returncode==0
def digest(p):
    if p.is_symlink() or not p.is_file(): raise RuntimeError('REGULAR_FILE_REQUIRED='+str(p))
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def one(s,a,b,n):
    c=s.count(a)
    if c!=1: raise RuntimeError(f'{n}_ANCHOR_COUNT={c}')
    return s.replace(a,b,1)
def after(s,a,x,marker,n):
    return s if marker in s else one(s,a,a+x,n)

def profile(s):
    if 'supports_sampled_image_nonuniform_indexing' not in s:
        s=after(s,'    bool supports_image_load_store_lod{};\n','    bool supports_sampled_image_nonuniform_indexing{};\n','supports_sampled_image_nonuniform_indexing','PROFILE_INDEX')
    if 'max_push_descriptors' not in s:
        s=after(s,'    bool supports_sampled_image_nonuniform_indexing{};\n','    u32 max_push_descriptors{};\n','max_push_descriptors','PROFILE_BUDGET')
    return s
def instance_h(s):
    if 'IsSampledImageNonUniformIndexingSupported' in s:return s
    m=re.search(r'(    bool IsImageLoadStoreLodSupported\(\) const \{\n        return image_load_store_lod;\n    \}\n)',s)
    if not m: raise RuntimeError('INSTANCE_GATE_ANCHOR')
    x='''\n    bool IsSampledImageNonUniformIndexingSupported() const {\n        return bool(vk12_features.descriptorIndexing) &&\n               bool(vk12_features.shaderSampledImageArrayNonUniformIndexing) &&\n               bool(features.shaderSampledImageArrayDynamicIndexing);\n    }\n'''
    return s[:m.end()]+x+s[m.end():]
def pipeline(s):
    if 'supports_sampled_image_nonuniform_indexing =' not in s:
        s=after(s,'        .supports_image_load_store_lod = instance_.IsImageLoadStoreLodSupported(),\n',
                '        .supports_sampled_image_nonuniform_indexing =\n            instance_.IsSampledImageNonUniformIndexingSupported(),\n',
                'supports_sampled_image_nonuniform_indexing =','PIPELINE_INDEX')
    if '.max_push_descriptors =' not in s:
        s=after(s,'        .supports_sampled_image_nonuniform_indexing =\n            instance_.IsSampledImageNonUniformIndexingSupported(),\n',
                '        .max_push_descriptors = instance_.MaxPushDescriptors(),\n','.max_push_descriptors =','PIPELINE_BUDGET')
    return s
def pass_h(s):
    return s if 'has_indexed_table' in s else after(s,'    std::array<SharpReference, 2> sharps;\n',
        '    SharpReference indexed_table_sharp{};\n    IR::Value indexed_texture_id{};\n    bool has_indexed_table{};\n',
        'has_indexed_table','PASS_FIELDS')
def resource_h(s):
    if 'MaxGuestIndexedImageEntries' not in s:
        a='enum class MipStorageFallbackMode : u16 {\n    None,\n    DynamicIndex,\n    ConstantIndex,\n};\n'
        s=one(s,a,a+'\ninline constexpr u32 MaxGuestIndexedImageEntries = 15;\n','RESOURCE_MAX')
    if 'indexed_table_fetch' not in s:
        s=after(s,'    SharpFetch<AmdGpu::Image> sharp_fetch{};\n',
                '    SharpFetch<AmdGpu::Buffer> indexed_table_fetch{};\n    u8 indexed_guest_entries{};\n    bool is_guest_indexed{};\n',
                'indexed_table_fetch','RESOURCE_FIELDS')
    if 'return AmdGpu::Image::Null(false);' not in s:
        s=one(s,'    constexpr AmdGpu::Image GetSharp(const auto& info) const noexcept {\n',
              '    constexpr AmdGpu::Image GetSharp(const auto& info) const noexcept {\n        if (is_guest_indexed) {\n            return AmdGpu::Image::Null(false);\n        }\n','RESOURCE_SHARP')
    if 'return u32(indexed_guest_entries) + 1u;' not in s:
        s=one(s,'    u32 NumBindings(const auto& info) const {\n',
              '    u32 NumBindings(const auto& info) const {\n        if (is_guest_indexed) {\n            return u32(indexed_guest_entries) + 1u;\n        }\n','RESOURCE_BINDINGS')
    return s

HELPER=r'''
struct IndexedImageTableSource {
    const IR::Inst* buffer{}; const IR::Inst* selector{};
    u32 descriptor_stride_words{1}; bool gpu_texture_id_indexed{};
    std::array<const IR::Inst*, 8> words{}; std::array<u32, 8> addends{}; bool contiguous{};
};
static IndexedImageTableSource AnalyzeIndexedImageTable(const IR::Inst& sample) {
    IndexedImageTableSource out{};
    if (sample.GetOpcode()!=IR::Opcode::ImageSampleRaw) return out;
    const auto* image=sample.Arg(0).TryInst();
    if (!image || image->GetOpcode()!=IR::Opcode::ImageHandle) return out;
    const auto* low=image->Arg(0).TryInst(); const auto* high=image->Arg(1).TryInst();
    if (!low || !high || low->NumArgs()!=4 || high->NumArgs()!=4) return out;
    std::array<IndexedSharpWord,8> norm{};
    for(std::size_t i=0;i<8;++i) {
        const auto* read=(i<4?low->Arg(i):high->Arg(i-4)).TryInst();
        if(!read || read->GetOpcode()!=IR::Opcode::ReadConstBuffer) return out;
        const auto* buffer=read->Arg(0).TryInst();
        if(!buffer || buffer->GetOpcode()!=IR::Opcode::CompositeConstructU32x4) return out;
        if(i==0) out.buffer=buffer; else if(out.buffer!=buffer) return out;
        out.words[i]=read; norm[i]=AnalyzeIndexedSharpWord(read->Arg(1));
        if(!norm[i].valid) return out; out.addends[i]=norm[i].addend;
    }
    const auto* selector=static_cast<const IR::Inst*>(norm[0].selector);
    if(!selector || !AreContiguousIndexedSharpWords(norm)) return out;
    const auto id=IdentifyTsharpSelector(selector); if(!id.source) return out;
    out.selector=static_cast<const IR::Inst*>(id.source);
    out.descriptor_stride_words=id.descriptor_stride_words;
    out.gpu_texture_id_indexed=id.normalized; out.contiguous=true; return out;
}
'''
LOOP=r'''                if (inst.GetOpcode()==IR::Opcode::ImageSampleRaw &&
                    profile.supports_sampled_image_nonuniform_indexing) {
                    const auto table=AnalyzeIndexedImageTable(inst);
                    if(table.contiguous && table.gpu_texture_id_indexed &&
                       table.descriptor_stride_words==8u) {
                        auto& usage=sharp_usages.back(); bool bindable=true;
                        for(std::size_t dw=0;dw<4;++dw) {
                            const IR::Value v=table.buffer->Arg(dw);
                            if(!v.IsImmediate()) {
                                const auto* p=v.TryInst(); bindable &= p && IsSharpSource(p);
                            }
                            usage.indexed_table_sharp.dwords[dw]=v;
                        }
                        if(bindable) {
                            usage.indexed_table_sharp.num_dwords=4;
                            MarkReadConstBufferSharpSources(usage.indexed_table_sharp);
                            usage.indexed_texture_id=IR::Value{const_cast<IR::Inst*>(table.selector)};
                            usage.has_indexed_table=true;
                        }
                    }
                }
'''
def discover(s):
    if 'indexed_image_selector.h' not in s:
        s=after(s,'#include "shader_recompiler/ir/passes/ir_passes.h"\n','#include "shader_recompiler/ir/passes/indexed_image_selector.h"\n','indexed_image_selector.h','DISCOVER_INCLUDE')
    if 'struct IndexedImageTableSource' not in s:
        a='ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {'
        s=one(s,a,HELPER+'\n'+a,'DISCOVER_HELPER')
    if 'usage.has_indexed_table=true;' not in s and 'usage.has_indexed_table = true;' not in s:
        a='                DiscoverImageSharp(*block, inst, sharp_usages);\n'
        s=one(s,a,a+LOOP,'DISCOVER_LOOP')
    return s

ELIG=r'''    if (resource.has_indexed_table && profile.supports_sampled_image_nonuniform_indexing &&
        !is_written && !inst_info.is_depth && !inst_info.is_array && !inst_info.is_gather &&
        !inst_info.has_derivatives && !inst_info.has_lod && !inst_info.force_level0 &&
        !inst_info.has_lod_clamp && !inst_info.is_r128 && !inst_info.is_unnormalized) {
        const auto table_fetch=__FETCH__;
        AmdGpu::Buffer table{};
        if(table_fetch.Fetch(info.flattened_ud_buf.data(),&table) && table.Valid() && table.base_address!=0) {
            const u32 entries=CompleteGuestTsharpTableEntries(static_cast<u32>(table.stride),
                table.num_records,MaxGuestIndexedImageEntries);
            if(entries!=0u) {
                u32 image_used=0; for(const auto& old:info.images) image_used+=old.NumBindings(info);
                const u32 new_images=entries+1u;
                const u32 push_used=static_cast<u32>(info.buffers.size()+info.samplers.size())+image_used;
                if(image_used+new_images<=NUM_IMAGES &&
                   push_used+new_images+1u<=profile.max_push_descriptors) {
                    image_res.indexed_table_fetch=table_fetch;
                    image_res.indexed_guest_entries=static_cast<u8>(entries);
                    image_res.is_guest_indexed=true;
                }
            }
        }
    }

'''
def patching(s):
    if 'guest_tsharp_array.h' not in s:
        s=after(s,'#include "shader_recompiler/resource.h"\n','#include "shader_recompiler/guest_tsharp_array.h"\n','guest_tsharp_array.h','PATCH_INCLUDE')
    if 'desc.is_guest_indexed == existing.is_guest_indexed' not in s:
        a=s.find('u32 Add(const ImageResource& desc)'), b=s.find('u32 Add(const SamplerResource& desc)',a)
        if a<0 or b<0: raise RuntimeError('PATCH_DEDUP_FUNCTION')
        p=s.find('desc.post_op == existing.post_op',a,b)
        if p<0: raise RuntimeError('PATCH_DEDUP_ANCHOR')
        p+=len('desc.post_op == existing.post_op')
        x=' &&\n                   desc.is_guest_indexed == existing.is_guest_indexed &&\n                   desc.indexed_guest_entries == existing.indexed_guest_entries &&\n                   desc.indexed_table_fetch == existing.indexed_table_fetch'
        s=s[:p]+x+s[p:]
    if 'image_res.indexed_table_fetch=table_fetch;' not in s and 'image_res.indexed_table_fetch = table_fetch;' not in s:
        use_info=bool(re.search(r'ConstructSharpFetch\s*\(\s*const SharpReference&\s+sharp\s*,\s*const Info&\s+info',s))
        call='ConstructSharpFetch<AmdGpu::Buffer>(resource.indexed_table_sharp, info)' if use_info else 'ConstructSharpFetch<AmdGpu::Buffer>(resource.indexed_table_sharp)'
        s=one(s,'    auto image = image_res.GetSharp(info);\n',ELIG.replace('__FETCH__',call)+'    auto image = image_res.GetSharp(info);\n','PATCH_ELIG')
    if 'inst.SetArg(1, resource.indexed_texture_id);' not in s:
        a='        inst.SetArg(0, ir.Imm32(image_binding | sampler_binding << 16));\n'
        s=one(s,a,a+'        if (image_res.is_guest_indexed) {\n            inst.SetArg(1, resource.indexed_texture_id);\n        }\n','PATCH_ID')
    if 'ImageSampleImplicitLodIndexed' not in s:
        a='        return ir.ImageSampleImplicitLod(handle, coords, bias, offset, inst_info);\n'
        x='        if (image_res.is_guest_indexed) {\n            return ir.ImageSampleImplicitLodIndexed(handle, coords, bias, offset, IR::U32{inst.Arg(1)}, inst_info);\n        }\n'
        s=one(s,a,x+a,'PATCH_LOWER')
    return s
def opcodes(s):
    if 'OPCODE(ImageSampleImplicitLodIndexed' in s:return s
    a='OPCODE(ImageSampleImplicitLod,                              F32x4,          Opaque,         F32x4,          F32,            Opaque,                         )\n'
    return after(s,a,'OPCODE(ImageSampleImplicitLodIndexed,                       F32x4,          Opaque,         F32x4,          F32,            Opaque,         U32,            )\n','OPCODE(ImageSampleImplicitLodIndexed','OPCODE')
def spirv_decl(s):
    if 'EmitImageSampleImplicitLodIndexed' in s:return s
    m=re.search(r'(Id EmitImageSampleImplicitLod\(EmitContext& ctx, IR::Inst\* inst, u32 handle, Id coords, Id bias,\n\s+const IR::Value& offset\);\n)',s)
    if not m:raise RuntimeError('SPIRV_DECL')
    x='Id EmitImageSampleImplicitLodIndexed(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords,\n                                     Id bias, const IR::Value& offset, Id gpu_texture_id);\n'
    return s[:m.end()]+x+s[m.end():]
def spirv_context(s):
    if 'image_desc.is_guest_indexed' not in s:
        a='        if (mip_fallback_mode == MipStorageFallbackMode::DynamicIndex) {\n            pointee_type = TypeArray(pointee_type, ConstU32(num_bindings));\n        }\n'
        b='        if (mip_fallback_mode == MipStorageFallbackMode::DynamicIndex ||\n            image_desc.is_guest_indexed) {\n            pointee_type = TypeArray(pointee_type, ConstU32(num_bindings));\n        }\n        if (image_desc.is_guest_indexed) {\n            ASSERT(profile.supports_sampled_image_nonuniform_indexing);\n            AddCapability(spv::Capability::ShaderNonUniform);\n            AddCapability(spv::Capability::SampledImageArrayDynamicIndexing);\n            AddCapability(spv::Capability::SampledImageArrayNonUniformIndexing);\n        }\n'
        s=one(s,a,b,'SPIRV_ARRAY')
    if '.is_guest_indexed = image_desc.is_guest_indexed' not in s:
        s=after(s,'            .mip_fallback_mode = mip_fallback_mode,\n','            .image_array_size = num_bindings,\n            .is_guest_indexed = image_desc.is_guest_indexed,\n','.is_guest_indexed = image_desc.is_guest_indexed','SPIRV_META')
    return s
def cache(s):
    for k in ('ShaderBinaryVersion','ShaderMetaVersion'):
        m=re.search(rf'(static constexpr u32 {k}\s*=\s*)(0x[0-9A-Fa-f]+|\d+)(u?;)',s)
        if not m:raise RuntimeError('CACHE_VERSION='+k)
        s=s[:m.start()]+m.group(1)+hex(int(m.group(2),0)+1)+'u;'+s[m.end():]
    return s

RBIND=r'''        if (image_desc.is_guest_indexed) {
            const u32 count=image_desc.NumBindings(stage);
            ASSERT(count>=2 && count<=Shader::MaxGuestIndexedImageEntries+1);
            ASSERT(num_images+count<=image_bindings.size());
            AmdGpu::Buffer source{};
            const bool fetched=image_desc.indexed_table_fetch.Fetch(stage.flattened_ud_buf.data(),&source);
            const u64 bytes=fetched?(source.stride==0?static_cast<u64>(source.num_records):static_cast<u64>(source.stride)*source.num_records):0u;
            const bool complete=fetched && source.Valid() && source.base_address!=0 &&
                Shader::Optimization::CompleteGuestTsharpTableEntries(static_cast<u32>(source.stride),
                    source.num_records,Shader::MaxGuestIndexedImageEntries)==count-1u &&
                bytes==u64(count-1)*sizeof(AmdGpu::Image) && memory->IsValidMapping(source.base_address,bytes);
            u64 estimated=0; constexpr u64 MaxEstimatedIndexedImageBytes=4ull<<30;
            const u32 max_dim=instance.GetPhysicalDevice().getProperties().limits.maxImageDimension2D;
            for(u32 i=0;i<count;++i) {
                auto& slot=image_bindings[num_images++]; slot.image_id={};
                if(!complete || i+1==count) continue;
                const u64 addr=source.base_address+u64(i)*sizeof(AmdGpu::Image);
                if(!memory->IsValidMapping(addr,sizeof(AmdGpu::Image))) continue;
                AmdGpu::Image raw{}; std::memcpy(&raw,reinterpret_cast<const void*>(addr),sizeof(raw));
                if(!raw.Valid() || raw.GetType()!=AmdGpu::ImageType::Color2D || raw.Address()==0 ||
                   !memory->IsValidGpuMapping(raw.Address(),0)) continue;
                const auto fmt=raw.GetDataFmt(); const auto nfmt=raw.GetNumberFmt();
                if(!magic_enum::enum_contains(fmt) || !magic_enum::enum_contains(nfmt) ||
                   fmt==AmdGpu::DataFormat::FormatInvalid || AmdGpu::IsInteger(nfmt) ||
                   raw.width>=max_dim || raw.height>=max_dim || raw.base_level>raw.last_level ||
                   raw.pitch<raw.width) continue;
                const u64 est=(u64(raw.width)+1u)*(u64(raw.height)+1u)*4u;
                if(est>MaxEstimatedIndexedImageBytes-estimated) continue; estimated+=est;
                auto& desc=slot.desc; std::construct_at(&desc,raw,image_desc);
                slot.image_id=texture_cache.FindImage(desc); auto* image=&texture_cache.GetImage(slot.image_id);
                if(desc.type==VideoCore::TextureCache::BindingType::Texture) {
                    if(auto depth=texture_cache.GetAssociatedDepth(*image)) {
                        slot.image_id=depth; image=&texture_cache.GetImage(slot.image_id);
                    }
                }
                image->binding.is_bound=1u;
            }
            image_descriptor_array_sizes.push_back(count); continue;
        }

'''
def raster(s):
    if '#include <cstring>' not in s:s=after(s,'#include "core/memory.h"\n','#include <cstring>\n','#include <cstring>','RASTER_CSTR')
    if 'guest_tsharp_array.h' not in s:s=after(s,'#include <cstring>\n','#include "shader_recompiler/guest_tsharp_array.h"\n','guest_tsharp_array.h','RASTER_HDR')
    if 'MaxEstimatedIndexedImageBytes' not in s:
        a='    for (const auto& image_desc : stage.images) {\n'; s=one(s,a,a+RBIND,'RASTER_LOOP')
    a='        bool is_storage = desc.type == VideoCore::TextureCache::BindingType::Storage;\n'
    if a in s:s=s.replace(a,'        bool is_storage = image_id && desc.type == VideoCore::TextureCache::BindingType::Storage;\n',1)
    elif 'bool is_storage = image_id && desc.type' not in s:raise RuntimeError('RASTER_NULL_GUARD')
    return s

TRANS={
'src/shader_recompiler/profile.h':profile,
'src/video_core/renderer_vulkan/vk_instance.h':instance_h,
'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp':pipeline,
'src/shader_recompiler/ir/passes/resource_pass.h':pass_h,
'src/shader_recompiler/resource.h':resource_h,
'src/shader_recompiler/ir/passes/resource_discover_pass.cpp':discover,
'src/shader_recompiler/ir/passes/resource_patching_pass.cpp':patching,
'src/shader_recompiler/ir/opcodes.inc':opcodes,
'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h':spirv_decl,
'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp':spirv_context,
'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp':cache,
'src/video_core/renderer_vulkan/vk_rasterizer.cpp':raster,
}

def verify_prev():
    rp=PREV/'build-result.json'; mp=PREV/'correction.json'
    if digest(rp)!=PREV_REPORT or digest(mp)!=PREV_MANIFEST:raise RuntimeError('PREVIOUS_EVIDENCE_CHANGED')
    r=json.loads(rp.read_text()); m=json.loads(mp.read_text()); c=Path(r['candidate'])
    exp={'revision':PARENT,'tree':PARENT_TREE,'candidate_sha256':PREV_BIN,'full_build':'PASS','source_restored':True,'build_binary_restored':True,'sirit_restored':True}
    for k,v in exp.items():
        if r.get(k)!=v:raise RuntimeError('PREVIOUS_GATE='+k)
    if digest(c)!=PREV_BIN or git('rev-parse',PARENT+'^{tree}').decode().strip()!=PARENT_TREE:raise RuntimeError('PREVIOUS_CANDIDATE_CHANGED')
    return m

def builddir():
    out=[]
    for n in (ROOT/'clean-rebase-verified').rglob('build.ninja'):
        d=n.parent; cm=d/'CMakeCache.txt'
        if not cm.is_file() or str(SRC) not in cm.read_text(errors='replace'):continue
        q=run(['ninja','-C',d,'-t','targets','all'],check=False)
        if q.returncode or not any(x.startswith('shadps4:') for x in q.stdout.decode(errors='replace').splitlines()):continue
        x=exe(d); score=max(n.stat().st_mtime,cm.stat().st_mtime,x.stat().st_mtime if x else 0); out.append((score,d))
    if not out:raise RuntimeError('NINJA_BUILD_DIRECTORY_NOT_FOUND')
    return max(out)[1]
def exe(d):
    for p in (d/'shadps4',d/'bin/shadps4'):
        if p.is_file() and not p.is_symlink() and os.access(p,os.X_OK):return p
    a=[]
    for p in d.rglob('shadps4'):
        try:
            if p.is_file() and not p.is_symlink() and os.access(p,os.X_OK):a.append((p.stat().st_mtime,p))
        except OSError:pass
    return max(a)[1] if a else None
def submodules():
    q=run(['git','-C',SRC,'submodule','update','--init','--recursive','--force'],check=False,timeout=180)
    if q.returncode:raise RuntimeError('SUBMODULE_UPDATE_FAILED='+(q.stderr or b'').decode(errors='replace')[-3000:])
def tee(a,p):
    with p.open('wb') as f:
        q=subprocess.Popen([str(x) for x in a],env=env(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        for b in iter(q.stdout.readline,b''):
            if not b:break
            sys.stdout.buffer.write(b);sys.stdout.buffer.flush();f.write(b);f.flush()
        rc=q.wait()
    if rc:raise RuntimeError(f'BUILD_FAILED={rc} log={p}')

def compose(run_dir,info,manifest):
    if git('rev-parse','HEAD').decode().strip()!=BASE or git('status','--porcelain').strip():raise RuntimeError('SOURCE_NOT_RESTORED')
    for x in (PARENT+'^{commit}',GEN_BASE+'^{commit}',GEN+'^{commit}'):
        if not exists(x):raise RuntimeError('OBJECT_MISSING='+x)
    names=tuple(git('diff','--name-only',GEN_BASE,GEN,'--','src').decode().splitlines())
    if names!=FILES:raise RuntimeError('GENERIC_CHANGE_SET_CHANGED')
    overlap=set(manifest['files']); data={}
    for p in FILES:
        if p in overlap:
            fn=TRANS.get(p)
            if not fn:raise RuntimeError('NO_SAFE_TRANSFORM='+p)
            data[p]=fn(show(PARENT,p).decode()).encode()
        else:data[p]=show(GEN,p)
    idx=run_dir/'index'; e=env({'GIT_INDEX_FILE':str(idx)}); git('read-tree',PARENT,e=e)
    for p,b in data.items():
        oid=git('hash-object','-w','--stdin',input=b).decode().strip()
        git('update-index','--add','--cacheinfo',f'100644,{oid},{p}',e=e)
        original=git('rev-parse',f'{PARENT}:{p}').decode().strip() if exists(f'{PARENT}:{p}') else None
        manifest['files'][p]={'original_blob':original,'corrected_blob':oid,'sha256':hashlib.sha256(b).hexdigest()}
    tree=git('write-tree',e=e).decode().strip()
    msg=b'Preserve GPU-indexed guest image tables on verified candidate\n\nGeneric Vulkan non-uniform T# arrays only; retain all parent fixes. No title IDs, shader hashes, readback overrides, forced draws or rendering presets.\n'
    rev=git('commit-tree',tree,'-p',PARENT,input=msg).decode().strip()
    if tuple(git('diff','--name-only',PARENT,rev,'--','src').decode().splitlines())!=FILES:raise RuntimeError('COMPOSED_CHANGE_SET_MISMATCH')
    diff=git('diff',PARENT,rev,'--',*FILES).decode(errors='replace')
    for pat in (r'CUSA\d+',r'Ghost of Tsushima',r'Tsushima',r'SHADPS4_[A-Z0-9_]*(?:AUDIT|HASH|GOT)',r'pgm_hash\s*==',r'readbacks_mode',r'readbacksMode',r'title_id\s*=='):
        if re.search(pat,diff,re.I):raise RuntimeError('GAME_SPECIFIC_SOURCE='+pat)
    if 'ImageSampleImplicitLodIndexed' not in diff or 'NonUniform' not in diff:raise RuntimeError('INDEXED_PATH_MISSING')
    git('update-ref','refs/heads/local/generic-indexed-tsharp-'+rev[:12],rev)
    (info/'generic-indexed-image.patch').write_text(diff)
    manifest['generic_indexed_image_tables']={'source_commit':GEN,'source_base':GEN_BASE,'composed_parent':PARENT,'exact_guest_descriptor_bytes':32,'max_guest_entries':15,'explicit_null_slot':True,'push_descriptor_budget_checked':True,'image_binding_budget_checked':True,'unsupported_forms_use_existing_path':True,'game_or_title_selectors':False,'shader_hash_selectors':False,'readback_override':False,'forced_draw_state':False,'static_texture_substitution':False,'all_games_validated':False}
    mp=info/'correction.json';mp.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    d=builddir(); old=exe(d)
    if not old:raise RuntimeError('BUILD_EXECUTABLE_NOT_FOUND')
    backup=run_dir/'private-binary';shutil.copy2(old,backup);oldsha=digest(old)
    restored=False;sirit=False;cand=None;csha=''
    try:
        git('checkout','--detach',rev);submodules()
        print('BUILD_DIRECTORY='+str(d),flush=True);print('COMPOSED_REVISION='+rev,flush=True)
        tee(['nice','-n','10','cmake','--build',d,'--target','shadps4','--parallel','2'],info/'full-build.log')
        built=exe(d)
        if not built:raise RuntimeError('NEW_BUILD_EXECUTABLE_NOT_FOUND')
        csha=digest(built);cand=ROOT/'clean-rebase-verified/candidates'/f'{rev}-{csha}'/'shadps4';cand.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(built,cand);cand.chmod(cand.stat().st_mode|stat.S_IXUSR|stat.S_IXGRP|stat.S_IXOTH)
    finally:
        if backup.is_file():shutil.copy2(backup,old);backup.unlink(missing_ok=True)
        try:
            git('checkout','--detach',BASE);submodules()
            sp=SRC/'externals/sirit';sirit=(not sp.exists()) or run(['git','-C',sp,'rev-parse','HEAD']).stdout.decode().strip()==SIRIT_BASE
            restored=git('rev-parse','HEAD').decode().strip()==BASE and not git('status','--porcelain').strip() and digest(old)==oldsha
        except Exception:pass
    if not restored:raise RuntimeError('SOURCE_OR_BUILD_RESTORE_FAILED')
    if not sirit:raise RuntimeError('SIRIT_RESTORE_FAILED')
    if not cand or digest(cand)!=csha:raise RuntimeError('CANDIDATE_COPY_FAILED')
    ms=digest(mp)
    report={'status':'GENERIC_INDEXED_IMAGE_BUILD_PASS','base':BASE,'source_parent':PARENT,'generic_source_commit':GEN,'revision':rev,'tree':tree,'candidate':str(cand),'candidate_sha256':csha,'manifest_sha256':ms,'full_build':'PASS','game_started':False,'source_restored':True,'build_binary_restored':True,'sirit_restored':True,'automatic_coherence':'RETAINED','srt_dependencies':'RETAINED','runtime_masked_reduction':'RETAINED','buffer_phi_selection':'RETAINED','image_resource_correctness':'RETAINED','generic_indexed_image_tables':'GPU_NONUNIFORM_COMPLETE_TSHARP','game_specific_options':False,'all_games_validated':False}
    (info/'build-result.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return rev,tree,csha,cand

def launch(info,rev,tree,csha,cand):
    if digest(OLD)!=OLD_SHA:raise RuntimeError('PROTECTED_LAUNCHER_CHANGED_OR_MISSING')
    spec=importlib.util.spec_from_file_location('generic_indexed_runtime',OLD);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    rp=info/'build-result.json';mp=info/'correction.json';rs=digest(rp);ms=digest(mp)
    m.INFO=info;m.REV=rev;m.TREE=tree;m.PARENT=PARENT;m.BIN_SHA=csha;m.REPORT_SHA=rs;m.MANIFEST_SHA=ms;m.SESSION='got-generic-indexed-'+rev[:12]
    def verify_build():
        if digest(rp)!=rs or digest(mp)!=ms:raise RuntimeError('BUILD_EVIDENCE_CHANGED')
        r=json.loads(rp.read_text())
        for k,v in {'status':'GENERIC_INDEXED_IMAGE_BUILD_PASS','source_parent':PARENT,'generic_source_commit':GEN,'revision':rev,'tree':tree,'candidate_sha256':csha,'manifest_sha256':ms,'full_build':'PASS','source_restored':True,'build_binary_restored':True,'sirit_restored':True,'generic_indexed_image_tables':'GPU_NONUNIFORM_COMPLETE_TSHARP','game_specific_options':False}.items():
            if r.get(k)!=v:raise RuntimeError('BUILD_GATE='+k)
        if Path(r['candidate'])!=cand or digest(cand)!=csha or git('rev-parse',rev+'^{tree}').decode().strip()!=tree or git('rev-parse',rev+'^').decode().strip()!=PARENT:raise RuntimeError('CANDIDATE_CHANGED')
        if git('rev-parse','HEAD').decode().strip()!=BASE or git('status','--porcelain').strip():raise RuntimeError('SOURCE_NOT_RESTORED')
        man=json.loads(mp.read_text())
        for p,x in man['files'].items():
            b=x.get('corrected_blob')
            if b and git('rev-parse',rev+':'+p).decode().strip()!=b:raise RuntimeError('SOURCE_BLOB='+p)
        return r,cand
    m.verify_build=verify_build
    print('GENERIC_INDEXED_TSHARP_CANDIDATE='+rev+' FULLSCREEN=ON FPS=ON REBUILD=DONE',flush=True)
    return int(m.main())

def archive(run_dir,error):
    info=run_dir/'info';info.mkdir(parents=True,exist_ok=True);(info/'failure.txt').write_text(type(error).__name__+': '+str(error)+'\n')
    out=Path(str(run_dir)+'.tar.gz')
    with tarfile.open(out,'w:gz') as t:
        for p in run_dir.rglob('*'):
            if p.is_file() and p.name!='private-binary':t.add(p,arcname=p.relative_to(run_dir))
    return out

def main():
    (ROOT/'logs').mkdir(parents=True,exist_ok=True);run_dir=Path(tempfile.mkdtemp(prefix='generic-indexed-image-'+time.strftime('%Y%m%d-%H%M%S')+'-',dir=ROOT/'logs'));info=run_dir/'info';info.mkdir()
    try:
        with (ROOT/'clean-rebase-verified/.build.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('ANOTHER_SHADPS4_BUILD_OWNS_LOCK')
            man=verify_prev()
            for p in PREV.iterdir():
                if p.is_file() and not p.is_symlink() and p.name not in ('build-result.json','correction.json','correction.patch') and p.stat().st_size<=16*1024*1024:shutil.copyfile(p,info/p.name)
            rev,tree,csha,cand=compose(run_dir,info,man)
        return launch(info,rev,tree,csha,cand)
    except (Exception,KeyboardInterrupt) as e:
        out=archive(run_dir,e);print('GENERIC_INDEXED_IMAGE_ERROR='+str(e),file=sys.stderr,flush=True);print('UPLOAD_THIS='+str(out),flush=True);return 2
if __name__=='__main__':raise SystemExit(main())
