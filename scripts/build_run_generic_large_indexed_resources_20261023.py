#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, fcntl, gzip, hashlib, importlib.util, json, os, re, shutil, stat, subprocess, sys, tarfile, tempfile, time
from pathlib import Path

ROOT=Path.home()/'projects/shadps4'
SRC=ROOT/'GhostOfTsushima/source'
BASE='04d891dc33bb1c8c89789b5ff039049901f95a41'
PARENT='975ee9812d93fb0e8094e0581640fb705c336d68'
PARENT_TREE='d921f3f51c546aa8b9dfe7e230aaefeaf0b3c446'
RECOVERY_BASE='4bc7df9c1370b0eb10c84f385b8c2d041e791945'
RECOVERY_REF='refs/remotes/chreece/generic-large-indexed-images-20261023'
SOURCE_REMOTE='https://github.com/Chreece/shadPS4.git'
UPSTREAM_REMOTE='https://github.com/shadps4-emu/shadPS4.git'
UPSTREAM_MAIN='91e6afa69b0a64b0afaa0281b80f8fbb0cd88136'
PARENT_REF=PARENT
BASE_STATUS=b''
RECOVERY_PARTS=tuple(f'patches/verified-975.patch.gz.b64.part{i:02d}' for i in range(1,9))
GEN_BASE='9f4c2da6107cb2b31578cc4b35f5e4ed85637dab'
GEN='464e32e92b7f36fede81f0d65ca0e81934051d1e'
STRIDED_SELECTOR_FINAL='d72990fbd8fe6483a921fa2c015e1d65b1e1f336'
GUEST_TSHARP_FINAL='02c22fd61253b072a9bc4017e67f9c39fa0d4fa9'
INTEGER_SAMPLER_FINAL='aab1275779c989f0624cd6dcb9582410d5534381'
VULKAN_INDEXING_FEATURE_FIXES=(
        )
OLD=ROOT/'scripts/shadps4-buffer-phi-launch-20261023.py'
OLD_SHA='d6c0c858a6f60d16a42248cff3589fb33cdd4f8897780eda4ad07437a771b44c'
GEN_FILES=(
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
EXTRA_FILES=(
'src/video_core/renderer_vulkan/vk_compute_pipeline.cpp',
'src/video_core/renderer_vulkan/vk_instance.cpp',
'src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp',
'src/video_core/renderer_vulkan/vk_pipeline_common.cpp',
'src/video_core/renderer_vulkan/vk_pipeline_common.h',
'src/video_core/renderer_vulkan/vk_resource_pool.cpp',
'src/video_core/renderer_vulkan/vk_resource_pool.h',
'src/video_core/renderer_vulkan/vk_rasterizer.h',
'src/video_core/texture_cache/sampler.cpp',
'src/video_core/texture_cache/sampler.h',
'src/video_core/texture_cache/sampler_filter_policy.h',
'src/video_core/texture_cache/texture_cache.cpp',
'src/video_core/texture_cache/texture_cache.h',
)
FILES=GEN_FILES+EXTRA_FILES
PATCH_COMMITS=(
'5568da00029a865a77f6bf8df6510750473cc97a',
'c6f721296e7442062a176cacabf8579968ecfbbb',
'6d97d6818550e1e0f296f78351db0acdf2e21518',
'f74a01cb704cb6f25450a578613968f6dc94798b',
'6b6d462d1d1fcbdbd488af8fbfe7eb323f7480a7',
'29983ea0417a920db38c0b0854f610eb482050a1',
'16ab0edbd70e1912ec3de581466788f89c11b39c',
'9e30254c86b532eb7c5921a3268dcb2a00e7d48d',
'be463d8d361a23d66ec60147ed268389a58b1f22',
'15ec6ede7034701b9c63e6e47203b6eb352c9377',
'0dc9a1dff77d7f6148e70e47ab5c6b43da66999a',
'5cf2cf61930223824024c058cfe72a819f56048e',
'f336b0dbfc20d35d83642472269cac77c0299ac4',
'b29106a5c6b6231a9ffb787f9277ef10c31e9182',
'f2169f2fb9a4902b4dbb752c90c8fa037f6bb8cf',
'a61c10ff0059b03db7437807fa4c9b17208f4f8b',
'e6b7acce13db1c2ca3fe5189a19458290d5bc74f',
'2718fe1101bc24eb8cd57b09f9dd22f3ac3b4e2b',
'39d58e288e254877be1cfb307293b8f26be0a195',
'5610f50a6239f82b70bf154c2c1ee6fbe8477def',
'0cecd34d1760fdd5720408bbd9661aaa6ea828c4',
'20ec7bfbb7a1ee18dac78d3c9bdeed29274580c2',
'8b75cb1197818094d2a31ca4879bc62c842b1134',
'9b4d6ebd6523b8f28d543da0ebaa841733cfdf7f',
'dd00aa320c9eb86c70584f36dce808c2483246a0',
'ec6f257ed6852478a915d96c514f54d290c112a2',
'3edc91cd217563acb9206772e4773c4ca1cf73d8',
'630f51ecf3c8a7bb8afac698a435ca27e8b23fad',
'bfa0d918e33f1364b052e953812aa662f2ad2275',
'6ff828d3a06b2222597c89c596ff500863c4a18f',
'34a46b656f75a7fdd215086d5be2b1c6042016b4',
'cbbcc969156f9258633914409ca88e6458bbad2c',
'538fc65527d96086443d14d3227eb9c5daa7397f',
)
PATCH_PATHS=('src/shader_recompiler','src/video_core/renderer_vulkan','src/video_core/texture_cache')

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
def commit_tree(tree,parent,message,additional_parents=()):
    parents=(parent,*tuple(additional_parents))
    times=[]
    for p in parents:
        value=git('show','-s','--format=%ct',p).decode().strip()
        if not value.isdigit():
            raise RuntimeError('PARENT_COMMIT_TIME_INVALID='+p)
        times.append(int(value))
    stamp=str(max(times))+' +0000'
    ce=env({
        'GIT_AUTHOR_NAME':'shadPS4 generic composer',
        'GIT_AUTHOR_EMAIL':'composer@localhost',
        'GIT_COMMITTER_NAME':'shadPS4 generic composer',
        'GIT_COMMITTER_EMAIL':'composer@localhost',
        'GIT_AUTHOR_DATE':stamp,
        'GIT_COMMITTER_DATE':stamp,
    })
    args=['commit-tree',tree]
    for p in parents:
        args += ['-p',p]
    return git(*args,input=message,e=ce).decode().strip()
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

def source_status():
    return git('status','--porcelain=v1','--untracked-files=all')

def validate_source_status(status):
    # The user intentionally removed the local tests tree. The candidate is
    # composed from immutable Git objects and configured with ENABLE_TESTS=OFF,
    # so those deletions are irrelevant. Refuse every other pre-existing edit.
    bad=[]
    for raw in status.decode(errors='replace').splitlines():
        if not raw:
            continue
        code=raw[:2]
        path=raw[3:]
        if code.strip()=='D' and (path=='tests' or path.startswith('tests/')):
            continue
        bad.append(raw)
    if bad:
        raise RuntimeError('GOT_SOURCE_HAS_UNSUPPORTED_LOCAL_CHANGES='+' | '.join(bad[:40]))

def assert_source_unchanged():
    now=source_status()
    if now!=BASE_STATUS:
        raise RuntimeError('ORIGINAL_GOT_SOURCE_STATUS_CHANGED_DURING_BUILD')

def profile(s):
    if 'max_guest_indexed_image_entries' not in s:
        s=after(s,'    u32 sparse_page_shift{};\n',
                '    u32 max_guest_indexed_image_entries{15};\n    u32 max_guest_indexed_sampler_entries{15};\n',
                'max_guest_indexed_image_entries','PROFILE_LIMITS')
    if 'supports_shader_image_gather_extended' not in s:
        s=after(s,'    bool supports_image_load_store_lod{};\n',
                '    bool supports_shader_image_gather_extended{};\n',
                'supports_shader_image_gather_extended','PROFILE_DYNAMIC_OFFSET')
    if 'supports_sampled_image_nonuniform_indexing' not in s:
        anchor='    bool supports_shader_image_gather_extended{};\n' if 'supports_shader_image_gather_extended' in s else '    bool supports_image_load_store_lod{};\n'
        s=after(s,anchor,
                '    bool supports_sampled_image_nonuniform_indexing{};\n',
                'supports_sampled_image_nonuniform_indexing','PROFILE_INDEX')
    if 'supports_sampler_array_nonuniform_indexing' not in s:
        s=after(s,'    bool supports_sampled_image_nonuniform_indexing{};\n',
                '    bool supports_sampler_array_nonuniform_indexing{};\n',
                'supports_sampler_array_nonuniform_indexing','PROFILE_SAMPLER_INDEX')
    if 'max_push_descriptors' not in s:
        s=after(s,'    bool supports_sampler_array_nonuniform_indexing{};\n',
                '    u32 max_push_descriptors{};\n','max_push_descriptors','PROFILE_BUDGET')
    return s
def instance_h(s):
    if 'IsSampledImageNonUniformIndexingSupported' not in s:
        m=re.search(r'(    bool IsImageLoadStoreLodSupported\(\) const \{\n        return image_load_store_lod;\n    \}\n)',s)
        if not m: raise RuntimeError('INSTANCE_GATE_ANCHOR')
        x='''\n    bool IsSampledImageNonUniformIndexingSupported() const {\n        return bool(vk12_features.descriptorIndexing) &&\n               bool(vk12_features.shaderSampledImageArrayNonUniformIndexing) &&\n               bool(features.shaderSampledImageArrayDynamicIndexing);\n    }\n'''
        s=s[:m.end()]+x+s[m.end():]
    if 'IsShaderImageGatherExtendedSupported' not in s:
        marker='    bool IsSampledImageNonUniformIndexingSupported() const {\n'
        p=s.find(marker)
        if p<0: raise RuntimeError('INSTANCE_GATHER_GATE_ANCHOR')
        s=s[:p]+'''    bool IsShaderImageGatherExtendedSupported() const {
        return features.shaderImageGatherExtended;
    }

'''+s[p:]
    if 'IsSamplerArrayNonUniformIndexingSupported' not in s:
        a='''    bool IsSampledImageNonUniformIndexingSupported() const {\n'''
        p=s.find(a)
        if p<0: raise RuntimeError('INSTANCE_SAMPLER_GATE_ANCHOR')
        e=s.find('    }\n',p)
        if e<0: raise RuntimeError('INSTANCE_SAMPLER_GATE_END')
        e+=6
        s=s[:e]+'''\n    bool IsSamplerArrayNonUniformIndexingSupported() const {\n        return IsSampledImageNonUniformIndexingSupported();\n    }\n'''+s[e:]
    return s
def instance_cpp(s):
    if '.shaderSampledImageArrayDynamicIndexing =' not in s:
        anchor='                .shaderImageGatherExtended = features.shaderImageGatherExtended,\n'
        insert=(
            '                .shaderSampledImageArrayDynamicIndexing =\n'
            '                    features.shaderSampledImageArrayDynamicIndexing,\n')
        s=after(s,anchor,insert,'shaderSampledImageArrayDynamicIndexing','INSTANCE_DYNAMIC_INDEX')

    if '.descriptorIndexing = vk12_features.descriptorIndexing,' not in s:
        anchor='            .shaderInt8 = vk12_features.shaderInt8,\n'
        insert=(
            '            .descriptorIndexing = vk12_features.descriptorIndexing,\n'
            '            .shaderSampledImageArrayNonUniformIndexing =\n'
            '                vk12_features.shaderSampledImageArrayNonUniformIndexing,\n')
        s=after(s,anchor,insert,'descriptorIndexing = vk12_features.descriptorIndexing','INSTANCE_NONUNIFORM_INDEX')

    # Vulkan has no separate shaderSamplerArrayNonUniformIndexing feature in
    # this path; standalone sampler arrays use sampled-image nonuniform support.
    lines=[
        line for line in s.splitlines(True)
        if 'shaderSamplerArrayNonUniformIndexing' not in line
    ]
    s=''.join(lines)
    if 'shaderSamplerArrayNonUniformIndexing' in s:
        raise RuntimeError('INVALID_VULKAN_SAMPLER_NONUNIFORM_FEATURE_RETAINED')
    return s

def pipeline(s):
    if '#include <algorithm>' not in s:
        s=s.replace('#include <ranges>\n','#include <algorithm>\n#include <ranges>\n',1)
    if 'GuestIndexedImageLimit' not in s:
        a='''constexpr static std::array DescriptorHeapSizes = {
    vk::DescriptorPoolSize{vk::DescriptorType::eUniformBuffer, 512},
    vk::DescriptorPoolSize{vk::DescriptorType::eStorageBuffer, 8192},
    vk::DescriptorPoolSize{vk::DescriptorType::eSampledImage, 8192},
    vk::DescriptorPoolSize{vk::DescriptorType::eStorageImage, 1024},
    vk::DescriptorPoolSize{vk::DescriptorType::eSampler, 1024},
};
'''
        x=r'''
static u32 GuestIndexedImageLimit(const Instance& instance) {
    const auto limits=instance.GetPhysicalDevice().getProperties().limits;
    const u32 hard=std::min({Shader::MaxGuestIndexedImageEntries+1u,
        limits.maxDescriptorSetSampledImages,limits.maxPerStageDescriptorSampledImages,
        limits.maxPerStageResources});
    constexpr u32 reserve=Shader::NUM_IMAGES+Shader::NUM_BUFFERS+Shader::NUM_SAMPLERS+32u;
    return hard>reserve+1u?std::min(Shader::MaxGuestIndexedImageEntries,hard-reserve-1u):0u;
}
static u32 GuestIndexedSamplerLimit(const Instance& instance) {
    const auto limits=instance.GetPhysicalDevice().getProperties().limits;
    const u32 hard=std::min({Shader::MaxGuestIndexedImageEntries+1u,
        limits.maxDescriptorSetSamplers,limits.maxPerStageDescriptorSamplers,
        limits.maxPerStageResources});
    constexpr u32 reserve=Shader::NUM_IMAGES+Shader::NUM_BUFFERS+Shader::NUM_SAMPLERS+32u;
    return hard>reserve+1u?std::min(Shader::MaxGuestIndexedImageEntries,hard-reserve-1u):0u;
}
static auto RuntimeDescriptorHeapSizes(const Instance& instance) {
    auto result=DescriptorHeapSizes;
    result[2].descriptorCount=std::max(result[2].descriptorCount,
        GuestIndexedImageLimit(instance)+Shader::NUM_IMAGES+1024u);
    result[4].descriptorCount=std::max(result[4].descriptorCount,
        GuestIndexedSamplerLimit(instance)+Shader::NUM_SAMPLERS+1024u);
    return result;
}
'''
        s=one(s,a,a+x,'PIPELINE_LIMIT_HELPERS')
    s=s.replace('desc_heap{instance, scheduler.GetWorkSemaphore(), DescriptorHeapSizes}',
                'desc_heap{instance, scheduler.GetWorkSemaphore(), RuntimeDescriptorHeapSizes(instance)}')
    if '.max_guest_indexed_image_entries =' not in s:
        s=after(s,'        .sparse_page_shift = sparse_page_shift,\n',
                '        .max_guest_indexed_image_entries = GuestIndexedImageLimit(instance),\n'
                '        .max_guest_indexed_sampler_entries = GuestIndexedSamplerLimit(instance),\n',
                '.max_guest_indexed_image_entries =','PIPELINE_LIMIT_PROFILE')
    if 'supports_shader_image_gather_extended =' not in s:
        s=after(s,'        .supports_image_load_store_lod = instance_.IsImageLoadStoreLodSupported(),\n',
                '        .supports_shader_image_gather_extended =\n'
                '            instance_.IsShaderImageGatherExtendedSupported(),\n',
                'supports_shader_image_gather_extended =','PIPELINE_DYNAMIC_OFFSET')
    if 'supports_sampled_image_nonuniform_indexing =' not in s:
        s=after(s,'        .supports_image_load_store_lod = instance_.IsImageLoadStoreLodSupported(),\n',
                '        .supports_sampled_image_nonuniform_indexing =\n'
                '            instance_.IsSampledImageNonUniformIndexingSupported(),\n',
                'supports_sampled_image_nonuniform_indexing =','PIPELINE_INDEX')
    if 'supports_sampler_array_nonuniform_indexing =' not in s:
        s=after(s,'        .supports_sampled_image_nonuniform_indexing =\n'
                  '            instance_.IsSampledImageNonUniformIndexingSupported(),\n',
                '        .supports_sampler_array_nonuniform_indexing =\n'
                '            instance_.IsSamplerArrayNonUniformIndexingSupported(),\n',
                'supports_sampler_array_nonuniform_indexing =','PIPELINE_SAMPLER_INDEX')
    if '.max_push_descriptors =' not in s:
        s=after(s,'        .supports_sampler_array_nonuniform_indexing =\n'
                  '            instance_.IsSamplerArrayNonUniformIndexingSupported(),\n',
                '        .max_push_descriptors = instance_.MaxPushDescriptors(),\n',
                '.max_push_descriptors =','PIPELINE_BUDGET')
    old='''        binding.buffer += info.buffers.size();
        binding.unified += info.buffers.size() + info.samplers.size();
        for (const auto& image : info.images) {
            binding.unified += image.NumBindings(info);
        }
'''
    if old in s:
        new='''        binding.buffer += info.buffers.size();
        binding.unified += info.buffers.size();
        for (const auto& image : info.images) {
            binding.unified += image.NumBindings(info);
        }
        for (const auto& sampler : info.samplers) {
            binding.unified += sampler.NumBindings(info);
        }
'''
        s=s.replace(old,new,1)
    return s
def pass_h(s):
    return s if 'has_indexed_table' in s else after(s,'    std::array<SharpReference, 2> sharps;\n',
        '    SharpReference indexed_table_sharp{};\n    IR::Value indexed_texture_id{};\n    bool has_indexed_table{};\n',
        'has_indexed_table','PASS_FIELDS')
def resource_h(s):
    if 'MaxGuestIndexedImageEntries' not in s:
        a='enum class MipStorageFallbackMode : u16 {\n    None,\n    DynamicIndex,\n    ConstantIndex,\n};\n'
        s=one(s,a,a+'\ninline constexpr u32 LegacyGuestIndexedImageEntries = 15;\ninline constexpr u32 MaxGuestIndexedImageEntries = 32768;\n','RESOURCE_MAX')

    start=s.find('struct ImageResource {')
    end=s.find('using ImageResourceList',start)
    if start<0 or end<0:
        raise RuntimeError('IMAGE_RESOURCE_STRUCT_NOT_FOUND')
    body=s[start:end]

    if 'indexed_table_fetch' not in body:
        a='    SharpFetch<AmdGpu::Image> sharp_fetch{};\n'
        if body.count(a)!=1:
            raise RuntimeError('RESOURCE_FIELDS_ANCHOR_COUNT='+str(body.count(a)))
        body=body.replace(
            a,
            a+'    SharpFetch<AmdGpu::Buffer> indexed_table_fetch{};\n'
              '    u32 indexed_guest_entries{};\n'
              '    bool is_guest_indexed{};\n',
            1)

    body=body.replace('    u8 indexed_guest_entries{};','    u32 indexed_guest_entries{};')

    if 'return AmdGpu::Image::Null(false);' not in body:
        a='    constexpr AmdGpu::Image GetSharp(const auto& info) const noexcept {\n'
        if body.count(a)!=1:
            raise RuntimeError('RESOURCE_SHARP_IMAGE_ANCHOR_COUNT='+str(body.count(a)))
        body=body.replace(
            a,
            a+'        if (is_guest_indexed) {\n'
              '            return AmdGpu::Image::Null(false);\n'
              '        }\n',
            1)

    if 'return u32(indexed_guest_entries) + 1u;' not in body:
        a='    u32 NumBindings(const auto& info) const {\n'
        if body.count(a)!=1:
            raise RuntimeError('RESOURCE_BINDINGS_IMAGE_ANCHOR_COUNT='+str(body.count(a)))
        body=body.replace(
            a,
            a+'        if (is_guest_indexed) {\n'
              '            return u32(indexed_guest_entries) + 1u;\n'
              '        }\n',
            1)

    s=s[:start]+body+s[end:]
    if 'return AmdGpu::Image::Null(false);' not in s[start:start+len(body)]:
        raise RuntimeError('RESOURCE_IMAGE_INDEXED_SHARP_NOT_INSERTED')

    sampler_start=s.find('struct SamplerResource {')
    sampler_end=s.find('using SamplerResourceList',sampler_start)
    if sampler_start<0 or sampler_end<0:
        raise RuntimeError('SAMPLER_RESOURCE_STRUCT_NOT_FOUND')
    sampler=s[sampler_start:sampler_end]
    if 'bool is_integer{};' not in sampler:
        anchor='    bool is_depth{};\n'
        if sampler.count(anchor)!=1:
            raise RuntimeError('SAMPLER_INTEGER_FIELD_ANCHOR_COUNT='+str(sampler.count(anchor)))
        sampler=sampler.replace(
            anchor,
            anchor+'    // Integer-format images require nearest filtering and no anisotropy.\n'
                   '    bool is_integer{};\n',
            1)
    s=s[:sampler_start]+sampler+s[sampler_end:]
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
                            MarkReadConstBufferSharpSources(usage.indexed_table_sharp, program, inst);
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
        loop=LOOP
        if 'void MarkReadConstBufferSharpSources(const SharpReference& sharp)' in s:
            loop=loop.replace(
                'MarkReadConstBufferSharpSources(usage.indexed_table_sharp, program, inst);',
                'MarkReadConstBufferSharpSources(usage.indexed_table_sharp);')
        elif ('MarkReadConstBufferSharpSources(usage.indexed_table_sharp, program, inst);'
              not in s and
              'void MarkReadConstBufferSharpSources(' not in s):
            raise RuntimeError('DISCOVER_MARK_API_UNKNOWN')

        anchors=(
            '                DiscoverImageSharp(*block, inst, sharp_usages, program);\n',
            '                DiscoverImageSharp(*block, inst, sharp_usages);\n',
        )
        selected=None
        for anchor in anchors:
            if s.count(anchor)==1:
                selected=anchor
                break
        if selected is None:
            raise RuntimeError(
                'DISCOVER_LOOP_API_NOT_ADAPTED old_count='+str(s.count(anchors[0]))+
                ' new_count='+str(s.count(anchors[1])))
        s=s.replace(selected,selected+loop,1)

    valid_marks=(
        'MarkReadConstBufferSharpSources(usage.indexed_table_sharp, program, inst);',
        'MarkReadConstBufferSharpSources(usage.indexed_table_sharp);',
    )
    if not any(mark in s for mark in valid_marks):
        raise RuntimeError('DISCOVER_MARK_API_NOT_ADAPTED')
    return s

ELIG=r'''    if (resource.has_indexed_table && profile.supports_sampled_image_nonuniform_indexing &&
        !is_written && !inst_info.is_depth && !inst_info.is_array && !inst_info.is_gather &&
        !inst_info.has_lod && !inst_info.force_level0 &&
        !inst_info.has_lod_clamp && !inst_info.is_r128 && !inst_info.is_unnormalized) {
        const auto table_fetch=__FETCH__;
        AmdGpu::Buffer table{};
        if(table_fetch.Fetch(info.flattened_ud_buf.data(),&table) && table.Valid() && table.base_address!=0) {
            const u32 entries=CompleteGuestTsharpTableEntries(static_cast<u32>(table.stride),
                table.num_records,profile.max_guest_indexed_image_entries);
            if(entries!=0u) {
                image_res.indexed_table_fetch=table_fetch;
                image_res.indexed_guest_entries=entries;
                image_res.is_guest_indexed=true;
            }
        }
    }

'''
def patching(s):
    if 'guest_tsharp_array.h' not in s:
        s=after(s,'#include "shader_recompiler/resource.h"\n','#include "shader_recompiler/guest_tsharp_array.h"\n','guest_tsharp_array.h','PATCH_INCLUDE')
    if 'desc.is_guest_indexed == existing.is_guest_indexed' not in s:
        a=s.find('u32 Add(const ImageResource& desc)')
        b=s.find('u32 Add(const SamplerResource& desc)',a)
        if a<0 or b<0: raise RuntimeError('PATCH_DEDUP_FUNCTION')
        p=s.find('desc.post_op == existing.post_op',a,b)
        if p<0: raise RuntimeError('PATCH_DEDUP_ANCHOR')
        p+=len('desc.post_op == existing.post_op')
        x=' &&\n                   desc.is_guest_indexed == existing.is_guest_indexed &&\n                   desc.indexed_guest_entries == existing.indexed_guest_entries &&\n                   desc.indexed_table_fetch == existing.indexed_table_fetch'
        s=s[:p]+x+s[p:]
    if 'image_res.indexed_table_fetch=table_fetch;' not in s and 'image_res.indexed_table_fetch = table_fetch;' not in s:
        use_info=bool(re.search(r'ConstructSharpFetch\s*\(\s*const SharpReference&\s+sharp\s*,\s*const Info&\s+info',s))
        call='ConstructSharpFetch<AmdGpu::Buffer>(resource.indexed_table_sharp, info)' if use_info else 'ConstructSharpFetch<AmdGpu::Buffer>(resource.indexed_table_sharp)'
        fn_start=s.find('void PatchImageSharp(')
        if fn_start<0:
            raise RuntimeError('PATCH_IMAGE_FUNCTION_NOT_FOUND')
        fn_end=s.find('\nstatic IR::Value IsMbcntWithExec',fn_start)
        if fn_end<0:
            fn_end=s.find('\nstatic ',fn_start+1)
        if fn_end<0:
            raise RuntimeError('PATCH_IMAGE_FUNCTION_END_NOT_FOUND')
        body=s[fn_start:fn_end]
        anchor='    auto image = image_res.GetSharp(info);\n'
        if body.count(anchor)!=1:
            raise RuntimeError('PATCH_ELIG_FUNCTION_ANCHOR_COUNT='+str(body.count(anchor)))
        body=body.replace(anchor,ELIG.replace('__FETCH__',call)+anchor,1)
        s=s[:fn_start]+body+s[fn_end:]
    if 'image_res.indexed_table_fetch' not in s[s.find('void PatchImageSharp('):]:
        raise RuntimeError('PATCH_ELIG_NOT_INSERTED')
    if 'inst.SetArg(1, resource.indexed_texture_id);' not in s:
        fn_start=s.find('void PatchImageSharp(')
        fn_end=s.find('\nstatic IR::Value IsMbcntWithExec',fn_start)
        if fn_end<0:
            fn_end=s.find('\nstatic ',fn_start+1)
        if fn_start<0 or fn_end<0:
            raise RuntimeError('PATCH_ID_FUNCTION_RANGE')
        body=s[fn_start:fn_end]
        a='        inst.SetArg(0, ir.Imm32(image_binding | sampler_binding << 16));\n'
        if body.count(a)!=1:
            raise RuntimeError('PATCH_ID_FUNCTION_ANCHOR_COUNT='+str(body.count(a)))
        body=body.replace(a,a+'        if (image_res.is_guest_indexed) {\n            inst.SetArg(1, resource.indexed_texture_id);\n        }\n',1)
        s=s[:fn_start]+body+s[fn_end:]
    # Numeric image format is part of Vulkan sampler identity.
    add_start=s.find('u32 Add(const SamplerResource& desc)')
    if add_start<0:
        raise RuntimeError('SAMPLER_DESCRIPTOR_ADD_NOT_FOUND')
    add_end=s.find('\n    }',add_start)
    if add_end<0:
        raise RuntimeError('SAMPLER_DESCRIPTOR_ADD_END_NOT_FOUND')
    add_body=s[add_start:add_end]
    if 'desc.is_integer == existing.is_integer' not in add_body:
        anchor='desc.is_depth == existing.is_depth'
        if add_body.count(anchor)!=1:
            raise RuntimeError('SAMPLER_INTEGER_DEDUP_ANCHOR_COUNT='+str(add_body.count(anchor)))
        add_body=add_body.replace(
            anchor,
            anchor+' &&\n                   desc.is_integer == existing.is_integer',
            1)
        s=s[:add_start]+add_body+s[add_end:]

    fn_start=s.find('void PatchImageSharp(')
    fn_end=s.find('\nstatic IR::Value IsMbcntWithExec',fn_start)
    if fn_end<0:
        fn_end=s.find('\nstatic ',fn_start+1)
    if fn_start<0 or fn_end<0:
        raise RuntimeError('PATCH_IMAGE_INTEGER_FUNCTION_RANGE')
    body=s[fn_start:fn_end]
    if '.is_integer = AmdGpu::IsInteger(image.GetNumberFmt())' not in body:
        anchor='            .is_depth = bool(inst_info.is_depth), // true for the _C (compare) opcodes\n'
        if body.count(anchor)!=1:
            raise RuntimeError('PATCH_IMAGE_INTEGER_ANCHOR_COUNT='+str(body.count(anchor)))
        body=body.replace(
            anchor,
            anchor+'            .is_integer = AmdGpu::IsInteger(image.GetNumberFmt()),\n',
            1)
        s=s[:fn_start]+body+s[fn_end:]

    if 'ImageSampleImplicitLodIndexed' not in s:
        fn_start=s.find('void PatchImageSampleArgs(')
        if fn_start<0:
            raise RuntimeError('PATCH_LOWER_FUNCTION_NOT_FOUND')
        fn_end=s.find('\nvoid PatchImageArgs(',fn_start)
        if fn_end<0:
            raise RuntimeError('PATCH_LOWER_FUNCTION_END_NOT_FOUND')
        body=s[fn_start:fn_end]
        a='        return ir.ImageSampleImplicitLod(handle, coords, bias, offset, inst_info);\n'
        if body.count(a)!=1:
            raise RuntimeError('PATCH_LOWER_FUNCTION_ANCHOR_COUNT='+str(body.count(a)))
        x='        if (image_res.is_guest_indexed) {\n            return ir.ImageSampleImplicitLodIndexed(handle, coords, bias, offset, IR::U32{inst.Arg(1)}, inst_info);\n        }\n'
        body=body.replace(a,x+a,1)
        s=s[:fn_start]+body+s[fn_end:]
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
    fn_start=s.find('void EmitContext::DefineImagesAndSamplers()')
    if fn_start<0:
        raise RuntimeError('SPIRV_IMAGE_SAMPLER_FUNCTION_NOT_FOUND')
    fn_end=s.find('\nvoid EmitContext::DefineSharedMemory()',fn_start)
    if fn_end<0:
        raise RuntimeError('SPIRV_IMAGE_SAMPLER_FUNCTION_END_NOT_FOUND')
    body=s[fn_start:fn_end]

    if 'image_desc.is_guest_indexed' not in body:
        a='        if (mip_fallback_mode == MipStorageFallbackMode::DynamicIndex) {\n            pointee_type = TypeArray(pointee_type, ConstU32(num_bindings));\n        }\n'
        if body.count(a)!=1:
            raise RuntimeError('SPIRV_ARRAY_FUNCTION_ANCHOR_COUNT='+str(body.count(a)))
        b='        if (mip_fallback_mode == MipStorageFallbackMode::DynamicIndex ||\n            image_desc.is_guest_indexed) {\n            pointee_type = TypeArray(pointee_type, ConstU32(num_bindings));\n        }\n        if (image_desc.is_guest_indexed) {\n            ASSERT(profile.supports_sampled_image_nonuniform_indexing);\n            AddCapability(spv::Capability::ShaderNonUniform);\n            AddCapability(spv::Capability::SampledImageArrayDynamicIndexing);\n            AddCapability(spv::Capability::SampledImageArrayNonUniformIndexing);\n        }\n'
        body=body.replace(a,b,1)

    if '.is_guest_indexed = image_desc.is_guest_indexed' not in body:
        a='            .mip_fallback_mode = mip_fallback_mode,\n'
        if body.count(a)!=1:
            raise RuntimeError('SPIRV_META_FUNCTION_ANCHOR_COUNT='+str(body.count(a)))
        body=body.replace(
            a,
            a+'            .image_array_size = num_bindings,\n'
              '            .is_guest_indexed = image_desc.is_guest_indexed,\n',
            1)

    s=s[:fn_start]+body+s[fn_end:]
    return s

def spirv_context_h(s):
    start=s.find('    struct ImageDefinition {')
    if start<0:
        raise RuntimeError('SPIRV_IMAGE_DEFINITION_STRUCT_NOT_FOUND')
    end=s.find('    };',start)
    if end<0:
        raise RuntimeError('SPIRV_IMAGE_DEFINITION_STRUCT_END_NOT_FOUND')
    end+=7
    body=s[start:end]
    if 'u32 image_array_size{1};' not in body:
        anchor='        MipStorageFallbackMode mip_fallback_mode{};\n'
        if body.count(anchor)!=1:
            raise RuntimeError('SPIRV_IMAGE_DEFINITION_ANCHOR_COUNT='+str(body.count(anchor)))
        body=body.replace(
            anchor,
            anchor+'        u32 image_array_size{1};\n'
                   '        bool is_guest_indexed{};\n',
            1)
    s=s[:start]+body+s[end:]
    return s

def ir_emitter_h(s):
    if 'ImageSampleImplicitLodIndexed(' in s:
        return s
    m=re.search(
        r'(\[\[nodiscard\]\] Value ImageSampleImplicitLod\([^;]+;\n)',
        s,re.S)
    if not m:
        raise RuntimeError('IR_EMITTER_INDEXED_DECL_ANCHOR')
    decl=(
        '    [[nodiscard]] Value ImageSampleImplicitLodIndexed(\n'
        '        const Value& handle, const Value& body, const F32& bias,\n'
        '        const Value& offset, const U32& gpu_texture_id, TextureInstInfo info);\n\n'
    )
    return s[:m.end()]+decl+s[m.end():]

def ir_emitter_cpp(s):
    if 'IREmitter::ImageSampleImplicitLodIndexed(' in s:
        return s
    marker='Value IREmitter::ImageSampleImplicitLod('
    start=s.find(marker)
    if start<0:
        raise RuntimeError('IR_EMITTER_INDEXED_IMPL_START')
    nxt=s.find('\nValue IREmitter::ImageSampleExplicitLod(',start)
    if nxt<0:
        raise RuntimeError('IR_EMITTER_INDEXED_IMPL_END')
    impl=(
        '\nValue IREmitter::ImageSampleImplicitLodIndexed(\n'
        '    const Value& handle, const Value& coords, const F32& bias,\n'
        '    const Value& offset, const U32& gpu_texture_id, TextureInstInfo info) {\n'
        '    return Inst(Opcode::ImageSampleImplicitLodIndexed, Flags{info}, handle, coords,\n'
        '                bias, offset, gpu_texture_id);\n'
        '}\n'
    )
    return s[:nxt]+impl+s[nxt:]

def emit_spirv_image(s):
    if 'Id EmitImageSampleImplicitLodIndexed(' in s:
        return s
    marker='Id EmitImageSampleImplicitLod(EmitContext& ctx'
    start=s.find(marker)
    if start<0:
        raise RuntimeError('SPIRV_INDEXED_SAMPLE_BASE_NOT_FOUND')
    nxt=s.find('\nId EmitImageSampleExplicitLod(',start)
    if nxt<0:
        raise RuntimeError('SPIRV_INDEXED_SAMPLE_INSERT_POINT')
    impl=r'''
Id EmitImageSampleImplicitLodIndexed(
    EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords, Id bias,
    const IR::Value& offset, Id gpu_texture_id) {
    const auto& texture = ctx.images[handle & 0xFFFFu];
    ASSERT(texture.is_guest_indexed && texture.image_array_size >= 2u);
    ASSERT(ctx.profile.supports_sampled_image_nonuniform_indexing);

    const Id valid = ctx.OpULessThan(ctx.U1[1], gpu_texture_id,
                                     ctx.ConstU32(texture.image_array_size - 1));
    const Id bounded = ctx.OpSelect(ctx.U32[1], valid, gpu_texture_id,
                                    ctx.ConstU32(texture.image_array_size - 1));
    ctx.Decorate(bounded, spv::Decoration::NonUniform);

    const Id pointer_type =
        ctx.TypePointer(spv::StorageClass::UniformConstant, texture.image_type);
    const Id pointer = ctx.OpAccessChain(pointer_type, texture.id, std::array{bounded});
    ctx.Decorate(pointer, spv::Decoration::NonUniform);
    const Id image = ctx.OpLoad(texture.image_type, pointer);
    ctx.Decorate(image, spv::Decoration::NonUniform);

    const Id sampler = ctx.OpLoad(ctx.sampler_type, ctx.samplers[handle >> 16]);
    const Id sampled_image = ctx.OpSampledImage(texture.sampled_type, image, sampler);
    ctx.Decorate(sampled_image, spv::Decoration::NonUniform);

    const Id fixed_coords = FixImageCoords<true>(ctx, coords, texture.view_type);
    ImageOperands operands;
    operands.Add(spv::ImageOperandsMask::Bias, bias);
    operands.AddOffset(ctx, texture.view_type, offset);
    const Id sample =
        ctx.OpImageSampleImplicitLod(texture.data_types->Get(4), sampled_image,
                                     fixed_coords, operands.mask, operands.operands);
    return texture.is_integer ? ctx.OpBitcast(ctx.F32[4], sample) : sample;
}
'''
    return s[:nxt]+impl+s[nxt:]

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
    if '#include <cstring>' not in s:
        s=after(s,'#include "core/memory.h"\n','#include <cstring>\n','#include <cstring>','RASTER_CSTR')
    if 'guest_tsharp_array.h' not in s:
        s=after(s,'#include <cstring>\n','#include "shader_recompiler/guest_tsharp_array.h"\n','guest_tsharp_array.h','RASTER_HDR')

    fn_start=s.find('void Rasterizer::BindTextures(')
    if fn_start<0:
        raise RuntimeError('RASTER_BIND_TEXTURES_NOT_FOUND')
    fn_end=s.find('\nRenderState Rasterizer::BeginRendering',fn_start)
    if fn_end<0:
        fn_end=s.find('\nvoid Rasterizer::',fn_start+1)
    if fn_end<0:
        raise RuntimeError('RASTER_BIND_TEXTURES_END_NOT_FOUND')
    body=s[fn_start:fn_end]

    if 'MaxEstimatedIndexedImageBytes' not in body:
        a='    for (const auto& image_desc : stage.images) {\n'
        if body.count(a)!=1:
            raise RuntimeError('RASTER_IMAGE_LOOP_COUNT='+str(body.count(a)))
        body=body.replace(a,a+RBIND,1)

    a='        bool is_storage = desc.type == VideoCore::TextureCache::BindingType::Storage;\n'
    if a in body:
        body=body.replace(
            a,
            '        bool is_storage = image_id && desc.type == VideoCore::TextureCache::BindingType::Storage;\n',
            1)
    elif 'bool is_storage = image_id && desc.type' not in body:
        raise RuntimeError('RASTER_NULL_GUARD')

    old_call='texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base, sampler.is_depth)'
    if old_call in body:
        body=body.replace(
            old_call,
            'texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base, '
            'sampler.is_depth, sampler.is_integer)',
            1)
    elif ('texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base,' in body and
          'sampler.is_integer' not in body):
        raise RuntimeError('RASTER_INTEGER_SAMPLER_CALL_NOT_ADAPTED')

    s=s[:fn_start]+body+s[fn_end:]
    return s

def sampler_h(s):
    if 'bool is_integer' in s:
        return s
    old='''    explicit Sampler(const Vulkan::Instance& instance, const AmdGpu::Sampler& sampler,
                     const AmdGpu::BorderColorBuffer border_color_base, bool is_depth);'''
    new='''    explicit Sampler(const Vulkan::Instance& instance, const AmdGpu::Sampler& sampler,
                     const AmdGpu::BorderColorBuffer border_color_base, bool is_depth,
                     bool is_integer);'''
    return one(s,old,new,'SAMPLER_INTEGER_DECL')

def sampler_cpp(s):
    if 'sampler_filter_policy.h' not in s:
        s=after(
            s,
            '#include "video_core/texture_cache/sampler.h"\n',
            '#include "video_core/texture_cache/sampler_filter_policy.h"\n',
            'sampler_filter_policy.h','SAMPLER_FILTER_POLICY_INCLUDE')
    old_sig='''Sampler::Sampler(const Vulkan::Instance& instance, const AmdGpu::Sampler& sampler,
                 const AmdGpu::BorderColorBuffer border_color_base, const bool is_depth) {'''
    new_sig='''Sampler::Sampler(const Vulkan::Instance& instance, const AmdGpu::Sampler& sampler,
                 const AmdGpu::BorderColorBuffer border_color_base, const bool is_depth,
                 const bool is_integer) {'''
    if old_sig in s:
        s=s.replace(old_sig,new_sig,1)
    if 'const bool is_integer)' not in s:
        raise RuntimeError('SAMPLER_INTEGER_CTOR_SIGNATURE_MISSING')

    old_aniso='''    const bool anisotropy_enable = instance.IsAnisotropicFilteringSupported() &&
                                   (AmdGpu::IsAnisoFilter(sampler.xy_mag_filter) ||
                                    AmdGpu::IsAnisoFilter(sampler.xy_min_filter));'''
    if old_aniso in s:
        new_aniso='''    const bool anisotropy_enable =
        SamplerAnisotropyAllowed(
            instance.IsAnisotropicFilteringSupported() &&
                (AmdGpu::IsAnisoFilter(sampler.xy_mag_filter) ||
                 AmdGpu::IsAnisoFilter(sampler.xy_min_filter)),
            is_integer);'''
        s=s.replace(old_aniso,new_aniso,1)
    if 'SamplerAnisotropyAllowed(' not in s:
        raise RuntimeError('SAMPLER_INTEGER_ANISO_POLICY_MISSING')

    s=s.replace(
        '.magFilter = LiverpoolToVK::Filter(sampler.xy_mag_filter),',
        '.magFilter = ImageSamplerFilter(LiverpoolToVK::Filter(sampler.xy_mag_filter), is_integer),')
    s=s.replace(
        '.minFilter = LiverpoolToVK::Filter(sampler.xy_min_filter),',
        '.minFilter = ImageSamplerFilter(LiverpoolToVK::Filter(sampler.xy_min_filter), is_integer),')
    s=s.replace(
        '.mipmapMode = LiverpoolToVK::MipFilter(sampler.mip_filter),',
        '.mipmapMode =\n            ImageSamplerMipmapMode(LiverpoolToVK::MipFilter(sampler.mip_filter), is_integer),')
    for marker in ('ImageSamplerFilter(','ImageSamplerMipmapMode('):
        if marker not in s:
            raise RuntimeError('SAMPLER_INTEGER_FILTER_POLICY_MISSING='+marker)
    return s

def texture_cache_h(s):
    if 'bool is_depth, bool is_integer' in s:
        return s
    old='''    [[nodiscard]] vk::Sampler GetSampler(const AmdGpu::Sampler& sampler,
                                         AmdGpu::BorderColorBuffer border_color_base,
                                         bool is_depth);'''
    new='''    [[nodiscard]] vk::Sampler GetSampler(const AmdGpu::Sampler& sampler,
                                         AmdGpu::BorderColorBuffer border_color_base,
                                         bool is_depth, bool is_integer);'''
    return one(s,old,new,'TEXTURE_CACHE_INTEGER_DECL')

def texture_cache_cpp(s):
    old_sig='''vk::Sampler TextureCache::GetSampler(const AmdGpu::Sampler& sharp,
                                     AmdGpu::BorderColorBuffer border_color_base,
                                     const bool is_depth) {'''
    new_sig='''vk::Sampler TextureCache::GetSampler(const AmdGpu::Sampler& sharp,
                                     AmdGpu::BorderColorBuffer border_color_base,
                                     const bool is_depth, const bool is_integer) {'''
    if old_sig in s:
        s=s.replace(old_sig,new_sig,1)
    if 'const bool is_integer)' not in s:
        raise RuntimeError('TEXTURE_CACHE_INTEGER_SIGNATURE_MISSING')
    old_hash='''    const u64 hash = HashCombine(XXH3_64bits(&sharp, sizeof(sharp)), is_depth);'''
    if old_hash in s:
        s=s.replace(
            old_hash,
            '''    const u64 hash = HashCombine(
        HashCombine(XXH3_64bits(&sharp, sizeof(sharp)), is_depth), is_integer);''',
            1)
    old_insert='slot_samplers.Insert(instance, sharp, border_color_base, is_depth)'
    if old_insert in s:
        s=s.replace(
            old_insert,
            'slot_samplers.Insert(instance, sharp, border_color_base, is_depth, is_integer)',
            1)
    for marker in ('is_integer);','is_depth, is_integer)'):
        if marker not in s:
            raise RuntimeError('TEXTURE_CACHE_INTEGER_POLICY_MISSING='+marker)
    return s


TRANS={
'src/shader_recompiler/profile.h':profile,
'src/video_core/renderer_vulkan/vk_instance.h':instance_h,
'src/video_core/renderer_vulkan/vk_instance.cpp':instance_cpp,
'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp':pipeline,
'src/shader_recompiler/ir/passes/resource_pass.h':pass_h,
'src/shader_recompiler/resource.h':resource_h,
'src/shader_recompiler/ir/passes/resource_discover_pass.cpp':discover,
'src/shader_recompiler/ir/passes/resource_patching_pass.cpp':patching,
'src/shader_recompiler/ir/opcodes.inc':opcodes,
'src/shader_recompiler/backend/spirv/emit_spirv_instructions.h':spirv_decl,
'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp':spirv_context,
'src/shader_recompiler/backend/spirv/spirv_emit_context.h':spirv_context_h,
'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp':emit_spirv_image,
'src/shader_recompiler/ir/ir_emitter.h':ir_emitter_h,
'src/shader_recompiler/ir/ir_emitter.cpp':ir_emitter_cpp,
'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp':cache,
'src/video_core/renderer_vulkan/vk_rasterizer.cpp':raster,
'src/video_core/texture_cache/sampler.h':sampler_h,
'src/video_core/texture_cache/sampler.cpp':sampler_cpp,
'src/video_core/texture_cache/texture_cache.h':texture_cache_h,
'src/video_core/texture_cache/texture_cache.cpp':texture_cache_cpp,
}

def _pure_cache_version_patch(sha):
    p='src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
    diff=git('diff',sha+'^',sha,'--',p).decode(errors='replace')
    if not diff.strip():
        return False
    changed=[]
    for line in diff.splitlines():
        if not line or line.startswith(('+++','---','@@')):
            continue
        if line[0] not in '+-':
            continue
        changed.append(line[1:].strip())
    if not changed:
        return False
    version_re=re.compile(
        r'static constexpr u32 (ShaderBinaryVersion|ShaderMetaVersion|PipelineKeyVersion)\s*=\s*'
        r'(0x[0-9A-Fa-f]+|[0-9]+)u?;'
    )
    return all(version_re.fullmatch(line) for line in changed)

def _changed_patch_paths(sha):
    names=git('diff','--name-only',sha+'^',sha,'--',*PATCH_PATHS).decode().splitlines()
    return [p for p in names if p]

def apply_range_file(idx,base,head,path,label):
    patch=git('diff',base,head,'--',path)
    if not patch.strip():
        return
    e=env({'GIT_INDEX_FILE':str(idx)})
    before=git('write-tree',e=e).decode().strip()
    q=subprocess.run(
        ['git','-c','core.hooksPath=/dev/null','-c','gc.auto=0','-C',str(SRC),
         'apply','--3way','--cached','--whitespace=nowarn','-'],
        env=e,input=patch,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if q.returncode==0:
        return
    err=(q.stderr or b'').decode(errors='replace')
    git('read-tree',before,e=e)
    reverse=subprocess.run(
        ['git','-c','core.hooksPath=/dev/null','-c','gc.auto=0','-C',str(SRC),
         'apply','--reverse','--check','--cached','--whitespace=nowarn','-'],
        env=e,input=patch,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if reverse.returncode==0:
        return
    raise RuntimeError(label+'_FILE_APPLY_FAILED path='+path+' '+err[-4000:])

def apply_patch_commit(idx,sha):
    if not re.fullmatch(r'[0-9a-f]{40}',sha):
        raise RuntimeError('PATCH_COMMIT_INVALID='+sha)
    if not exists(sha+'^{commit}'):
        raise RuntimeError('PATCH_COMMIT_MISSING='+sha)

    e=env({'GIT_INDEX_FILE':str(idx)})
    commit_tree=git('write-tree',e=e).decode().strip()
    paths=_changed_patch_paths(sha)
    if not paths:
        return

    for path in paths:
        # Cache-version counters are intentionally regenerated once from the
        # final composed ABI. They carry no runtime behavior by themselves.
        if (path=='src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp' and
            _pure_cache_version_patch(sha)):
            continue

        patch=git('diff',sha+'^',sha,'--',path)
        if not patch.strip():
            continue

        file_tree=git('write-tree',e=e).decode().strip()
        q=subprocess.run(
            ['git','-c','core.hooksPath=/dev/null','-c','gc.auto=0','-C',str(SRC),
             'apply','--3way','--cached','--whitespace=nowarn','-'],
            env=e,input=patch,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if q.returncode==0:
            continue

        err=(q.stderr or b'').decode(errors='replace')
        git('read-tree',file_tree,e=e)

        # If this exact file change is already represented by the verified
        # parent or by an earlier generic replay, treat it as idempotent.
        reverse=subprocess.run(
            ['git','-c','core.hooksPath=/dev/null','-c','gc.auto=0','-C',str(SRC),
             'apply','--reverse','--check','--cached','--whitespace=nowarn','-'],
            env=e,input=patch,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if reverse.returncode==0:
            continue

        # Keep failure evidence deterministic: discard any earlier files from
        # this one commit before reporting the unresolved path.
        git('read-tree',commit_tree,e=e)
        raise RuntimeError(
            'GENERIC_PATCH_FILE_APPLY_FAILED='+sha+' path='+path+' '+err[-4000:]
        )

def index_text(idx,p):
    e=env({'GIT_INDEX_FILE':str(idx)})
    return git('show',':'+p,e=e).decode()

def update_index_text(idx,p,content):
    e=env({'GIT_INDEX_FILE':str(idx)})
    b=content.encode()
    oid=git('hash-object','-w','--stdin',input=b).decode().strip()
    git('update-index','--add','--cacheinfo',f'100644,{oid},{p}',e=e)
    return oid,b

def finish_generic_large(idx):
    # Strided T#/S# tables share a GPU record selector. Their complete table
    # must fit both sampled-image and sampler physical limits.
    p='src/shader_recompiler/ir/passes/resource_patching_pass.cpp'
    t=index_text(idx,p)
    if '#include <algorithm>' not in t:
        first_include=t.find('#include ')
        if first_include>=0:
            t=t[:first_include]+'#include <algorithm>\n'+t[first_include:]
    needle='''            const u32 entries = record_stride != 0u
                ? CompleteGuestTsharpRecordEntries(
                    static_cast<u32>(table.stride),static_cast<u32>(table.num_records),
                    record_stride,record_offset,profile.max_guest_indexed_image_entries)
                : CompleteGuestTsharpTableEntries(
                    static_cast<u32>(table.stride),static_cast<u32>(table.num_records),
                    profile.max_guest_indexed_image_entries);
'''
    if needle in t:
        repl='''            const u32 indexed_limit = record_stride != 0u
                ? std::min(profile.max_guest_indexed_image_entries,
                           profile.max_guest_indexed_sampler_entries)
                : profile.max_guest_indexed_image_entries;
            const u32 entries = record_stride != 0u
                ? CompleteGuestTsharpRecordEntries(
                    static_cast<u32>(table.stride),static_cast<u32>(table.num_records),
                    record_stride,record_offset,indexed_limit)
                : CompleteGuestTsharpTableEntries(
                    static_cast<u32>(table.stride),static_cast<u32>(table.num_records),
                    indexed_limit);
'''
        t=t.replace(needle,repl,1)
    if 'profile.max_guest_indexed_sampler_entries' not in t:
        raise RuntimeError('STRIDED_PHYSICAL_LIMIT_NOT_APPLIED')
    update_index_text(idx,p,t)

    p='src/video_core/renderer_vulkan/vk_resource_pool.h'
    t=index_text(idx,p)
    old_member='    std::span<const vk::DescriptorPoolSize> pool_sizes;'
    if old_member in t:
        t=t.replace(old_member,'    std::vector<vk::DescriptorPoolSize> pool_sizes;',1)
    if 'std::vector<vk::DescriptorPoolSize> pool_sizes;' not in t:
        raise RuntimeError('DESCRIPTOR_POOL_SIZE_OWNERSHIP_NOT_APPLIED')
    update_index_text(idx,p,t)

    p='src/video_core/renderer_vulkan/vk_resource_pool.cpp'
    t=index_text(idx,p)
    old_init='      descriptor_heap_count{descriptor_heap_count_}, pool_sizes{pool_sizes_} {'
    if old_init in t:
        t=t.replace(
            old_init,
            '      descriptor_heap_count{descriptor_heap_count_},\n'
            '      pool_sizes{pool_sizes_.begin(), pool_sizes_.end()} {',
            1)
    if 'pool_sizes{pool_sizes_.begin(), pool_sizes_.end()}' not in t:
        raise RuntimeError('DESCRIPTOR_POOL_SIZE_COPY_NOT_APPLIED')
    update_index_text(idx,p,t)

    p='src/video_core/renderer_vulkan/vk_rasterizer.cpp'
    t=index_text(idx,p)
    for header,token in (('<unordered_map>','std::unordered_map'),
                         ('<bit>','std::has_single_bit'),
                         ('<cstring>','std::memcpy')):
        if token in t and ('#include '+header) not in t:
            first_include=t.find('#include ')
            if first_include>=0:
                t=t[:first_include]+'#include '+header+'\n'+t[first_include:]
    update_index_text(idx,p,t)

    p='src/shader_recompiler/backend/spirv/emit_spirv_image.cpp'
    t=index_text(idx,p)

    # Reuse the later proven generic dynamic-offset implementation verbatim
    # instead of approximating it. It preserves signed texel displacement,
    # gates dynamic Offset on shaderImageGatherExtended, and emits the required
    # SPIR-V capability without any title/shader selector.
    latest=show(STRIDED_SELECTOR_FINAL,p).decode()
    def function_slice(text,start_marker,end_marker,label):
        start=text.find(start_marker)
        end=text.find(end_marker,start)
        if start<0 or end<0 or end<=start:
            raise RuntimeError(label+'_FUNCTION_RANGE')
        return text[start:end]
    latest_add_offset=function_slice(
        latest,
        '    void AddOffset(EmitContext& ctx, AmdGpu::ImageType image_type, const IR::Value& offset,',
        '    void AddDerivatives(EmitContext& ctx, AmdGpu::ImageType image_type,',
        'LATEST_DYNAMIC_OFFSET')
    current_start=t.find(
        '    void AddOffset(EmitContext& ctx, AmdGpu::ImageType image_type, const IR::Value& offset,')
    current_end=t.find(
        '    void AddDerivatives(EmitContext& ctx, AmdGpu::ImageType image_type,',
        current_start)
    if current_start<0 or current_end<0:
        raise RuntimeError('CURRENT_DYNAMIC_OFFSET_FUNCTION_RANGE')
    t=t[:current_start]+latest_add_offset+t[current_end:]

    for marker in (
        'ctx.profile.supports_shader_image_gather_extended',
        'spv::Capability::ImageGatherExtended',
        'ctx.OpBitcast(ctx.S32[1], raw)',
        'ctx.OpBitcast(ctx.S32[2], raw)',
        'ctx.OpBitcast(ctx.S32[3], raw)',
    ):
        if marker not in t:
            raise RuntimeError('DYNAMIC_OFFSET_LATEST_MARKER_MISSING='+marker)

    update_index_text(idx,p,t)

    # The runtime pool is sized from the actual physical limits; no opt-in
    # environment switch and no title/game selector is permitted.
    p='src/video_core/renderer_vulkan/vk_pipeline_cache.cpp'
    t=index_text(idx,p)
    if 'RuntimeDescriptorHeapSizes(instance)' not in t or 'GuestIndexedImageLimit' not in t:
        raise RuntimeError('DYNAMIC_DESCRIPTOR_POOL_LIMIT_MISSING')

    # Normalize any earlier cached implementation to a per-Instance value.
    # DescriptorHeap owns a copy of this array, so returning by value is both
    # lifetime-safe and correct when more than one Vulkan device is created.
    cached_re=re.compile(
        r'static const auto& RuntimeDescriptorHeapSizes\(const Instance& instance\) \{'
        r'.*?\n\}',
        re.S)
    m=cached_re.search(t)
    if m:
        replacement='''static auto RuntimeDescriptorHeapSizes(const Instance& instance) {
    auto result=DescriptorHeapSizes;
    result[2].descriptorCount=std::max(result[2].descriptorCount,
        GuestIndexedImageLimit(instance)+Shader::NUM_IMAGES+1024u);
    result[4].descriptorCount=std::max(result[4].descriptorCount,
        GuestIndexedSamplerLimit(instance)+Shader::NUM_SAMPLERS+1024u);
    return result;
}'''
        t=t[:m.start()]+replacement+t[m.end():]
    if 'static const auto& RuntimeDescriptorHeapSizes' in t:
        raise RuntimeError('STATIC_DESCRIPTOR_HEAP_LIMIT_CACHE_RETAINED')
    if 'static auto RuntimeDescriptorHeapSizes(const Instance& instance)' not in t:
        raise RuntimeError('PER_INSTANCE_DESCRIPTOR_HEAP_LIMIT_MISSING')
    update_index_text(idx,p,t)

    # Invalidate shader binaries/metadata for the new descriptor-array ABI.
    p='src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
    t=index_text(idx,p)
    for k in ('ShaderBinaryVersion','ShaderMetaVersion'):
        m=re.search(rf'(static constexpr u32 {k}\s*=\s*)(0x[0-9A-Fa-f]+|\d+)(u?;)',t)
        if not m: raise RuntimeError('CACHE_VERSION='+k)
        t=t[:m.start()]+m.group(1)+hex(int(m.group(2),0)+1)+'u;'+t[m.end():]
    update_index_text(idx,p,t)

    return

def ensure_required_refs():
    specs=(
        ('fix/generic-large-indexed-images-20261023',
         'refs/remotes/chreece/generic-large-indexed-images-20261023'),
        ('fix/got-strided-sampler-indexed-20261018',
         'refs/remotes/chreece/got-strided-sampler-indexed-20261018'),
        ('fix/general-vulkan-integer-sampler-20261016',
         'refs/remotes/chreece/general-vulkan-integer-sampler-20261016'),
    )

    # Always refresh the integration refs. An existing remote-tracking ref can
    # be stale even though it technically "exists", which previously allowed
    # the composer to combine an older branch snapshot with newer hard-coded
    # commit expectations.
    args=['git','-C',SRC,'fetch','--no-tags',SOURCE_REMOTE]
    args += [f'+refs/heads/{src}:{dst}' for src,dst in specs]
    q=run(args,check=False,timeout=600)
    if q.returncode:
        raise RuntimeError(
            'REQUIRED_REF_REFRESH_FAILED='+
            (q.stderr or b'').decode(errors='replace')[-4000:]
        )

    for _,dst in specs:
        if not exists(dst+'^{commit}'):
            raise RuntimeError('REQUIRED_REF_MISSING_AFTER_FETCH='+dst)

    required=(GEN_BASE,GEN,STRIDED_SELECTOR_FINAL,GUEST_TSHARP_FINAL,INTEGER_SAMPLER_FINAL,*PATCH_COMMITS)
    for sha in required:
        if exists(sha+'^{commit}'):
            continue
        q=run(['git','-C',SRC,'fetch','--no-tags',SOURCE_REMOTE,sha],
              check=False,timeout=600)
        if q.returncode or not exists(sha+'^{commit}'):
            raise RuntimeError(
                'REQUIRED_COMMIT_FETCH_FAILED='+sha+' '+
                (q.stderr or b'').decode(errors='replace')[-2000:]
            )

    if not exists(UPSTREAM_MAIN+'^{commit}'):
        q=run(['git','-C',SRC,'fetch','--no-tags',UPSTREAM_REMOTE,UPSTREAM_MAIN],
              check=False,timeout=600)
        if q.returncode or not exists(UPSTREAM_MAIN+'^{commit}'):
            raise RuntimeError(
                'UPSTREAM_MAIN_FETCH_FAILED='+UPSTREAM_MAIN+' '+
                (q.stderr or b'').decode(errors='replace')[-2000:]
            )

def recovered_parent_ref(run_dir):
    global PARENT_REF
    if exists(PARENT+'^{commit}'):
        if git('rev-parse',PARENT+'^{tree}').decode().strip()!=PARENT_TREE:
            raise RuntimeError('VERIFIED_GRAPHICS_PARENT_TREE_CHANGED')
        PARENT_REF=PARENT
        return PARENT_REF

    ensure_required_refs()

    # The commit object may have been pruned while its exact tree is still
    # present in the object database. In that case no patch replay is needed
    # at all: wrap the verified tree in a local synthetic commit.
    if exists(PARENT_TREE+'^{tree}'):
        base=RECOVERY_BASE if exists(RECOVERY_BASE+'^{commit}') else BASE
        if not exists(base+'^{commit}'):
            raise RuntimeError('NO_SYNTHETIC_PARENT_BASE_AVAILABLE')
        msg=b'Wrapped exact verified graphics parent tree for local composition only\n'
        PARENT_REF=commit_tree(PARENT_TREE,base,msg)
        if git('rev-parse',PARENT_REF+'^{tree}').decode().strip()!=PARENT_TREE:
            raise RuntimeError('EXACT_PARENT_TREE_WRAP_FAILED')
        return PARENT_REF

    encoded=b''
    for part in RECOVERY_PARTS:
        spec=f'{RECOVERY_REF}:{part}'
        if not exists(spec):
            raise RuntimeError('RECOVERY_PATCH_PART_MISSING='+part)
        encoded+=show(RECOVERY_REF,part).strip()

    try:
        patch=gzip.decompress(base64.b64decode(encoded,validate=True))
    except Exception as e:
        raise RuntimeError('RECOVERY_PATCH_DECODE_FAILED='+str(e))
    if not patch.startswith(b'diff --git '):
        raise RuntimeError('RECOVERY_PATCH_FORMAT_INVALID')

    candidates=[]
    for base in (RECOVERY_BASE,BASE):
        if base not in candidates and exists(base+'^{commit}'):
            candidates.append(base)
    if not candidates:
        raise RuntimeError('NO_RECOVERY_BASE_OBJECT_AVAILABLE')

    errors=[]
    for base in candidates:
        idx=run_dir/f'verified-parent-recovery-{base[:12]}.index'
        e=env({'GIT_INDEX_FILE':str(idx)})
        git('read-tree',base,e=e)
        q=subprocess.run(
            ['git','-c','core.hooksPath=/dev/null','-c','gc.auto=0','-C',str(SRC),
             'apply','--3way','--cached','--whitespace=nowarn','-'],
            env=e,input=patch,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        if q.returncode:
            errors.append(base+':apply:'+(q.stderr or b'').decode(errors='replace')[-800:])
            continue
        tree=git('write-tree',e=e).decode().strip()
        if tree!=PARENT_TREE:
            errors.append(base+':tree:'+tree)
            continue
        msg=b'Reconstructed verified graphics parent tree for local composition only\n'
        PARENT_REF=commit_tree(tree,base,msg)
        if git('rev-parse',PARENT_REF+'^{tree}').decode().strip()!=PARENT_TREE:
            raise RuntimeError('RECOVERED_PARENT_COMMIT_TREE_CHANGED')
        return PARENT_REF

    raise RuntimeError(
        'RECOVERED_PARENT_TREE_NOT_REPRODUCED expected='+PARENT_TREE+
        ' attempts='+' || '.join(errors)
    )

def merge_verified_parent_with_upstream(parent):
    if not exists(parent+'^{commit}'):
        raise RuntimeError('VERIFIED_PARENT_MISSING_BEFORE_UPSTREAM_MERGE')
    if not exists(UPSTREAM_MAIN+'^{commit}'):
        raise RuntimeError('UPSTREAM_MAIN_MISSING_BEFORE_MERGE')

    q=run(['git','-C',SRC,'merge-tree','--write-tree',parent,UPSTREAM_MAIN],
          check=False,timeout=120)
    stdout=(q.stdout or b'').decode(errors='replace').strip()
    stderr=(q.stderr or b'').decode(errors='replace').strip()
    if q.returncode:
        raise RuntimeError(
            'UPSTREAM_MERGE_CONFLICT parent='+parent+
            ' upstream='+UPSTREAM_MAIN+' details='+(stdout+' '+stderr)[-6000:]
        )
    tree=stdout.splitlines()[0].strip() if stdout else ''
    if not re.fullmatch(r'[0-9a-f]{40}',tree) or not exists(tree+'^{tree}'):
        raise RuntimeError('UPSTREAM_MERGE_TREE_INVALID='+stdout[-2000:])

    # A merge tree must contain both lineages. Materialize it as a deterministic
    # local merge commit so the subsequent indexed-resource commit has a stable,
    # auditable parent.
    msg=(b'Merge pinned upstream main into verified generic graphics parent\n\n'
         b'No conflict resolution or game-specific preference was permitted.\n')
    merged=commit_tree(tree,parent,msg,(UPSTREAM_MAIN,))
    if git('rev-parse',merged+'^{tree}').decode().strip()!=tree:
        raise RuntimeError('UPSTREAM_MERGE_COMMIT_TREE_CHANGED')

    verified_sirit=git('rev-parse',parent+':externals/sirit').decode().strip()
    merged_sirit=git('rev-parse',merged+':externals/sirit').decode().strip()
    if merged_sirit!=verified_sirit:
        raise RuntimeError(
            'UPSTREAM_MERGE_CHANGED_VERIFIED_SIRIT expected='+
            verified_sirit+' got='+merged_sirit
        )
    return merged,tree

def verify_prev(run_dir):
    ensure_required_refs()
    # PARENT_REF is either the original verified commit or a locally
    # reconstructed commit with the exact same verified tree.
    if not SRC.is_dir() or not (SRC/'.git').exists():
        raise RuntimeError('GOT_SOURCE_REPOSITORY_MISSING='+str(SRC))
    validate_source_status(BASE_STATUS)
    verified_parent=recovered_parent_ref(run_dir)
    if git('rev-parse',verified_parent+'^{tree}').decode().strip()!=PARENT_TREE:
        raise RuntimeError('VERIFIED_GRAPHICS_PARENT_TREE_CHANGED')
    parent,parent_tree=merge_verified_parent_with_upstream(verified_parent)
    return {
        'previous_parent':parent,
        'previous_parent_tree':parent_tree,
        'verified_parent':verified_parent,
        'verified_parent_original':PARENT,
        'verified_parent_tree':PARENT_TREE,
        'parent_reconstructed':verified_parent!=PARENT,
        'upstream_main':UPSTREAM_MAIN,
        'upstream_merge_conflicts':False,
        'previous_evidence_logs':'NOT_REQUIRED',
        'files':{},
    }

def fresh_paths(rev):
    root=ROOT/'GhostOfTsushima'
    stamp=f'{rev[:12]}-{os.getpid()}'
    work=root/'fresh-source'/stamp
    build=root/'fresh-builds'/stamp
    work.parent.mkdir(parents=True,exist_ok=True)
    build.parent.mkdir(parents=True,exist_ok=True)
    for p in (work,build):
        if p.exists():
            shutil.rmtree(p)
    return work,build

def add_fresh_source(work,rev,temp_ref):
    # Use an independent local clone instead of a linked worktree. Synthetic
    # composition commits live under a temporary non-head ref, so explicitly
    # fetch that ref into the clone before checkout; ordinary clone refspecs
    # are not required to advertise/copy it.
    q=run(['git','clone','--no-hardlinks','--no-checkout',SRC,work],
          check=False,timeout=600)
    if q.returncode:
        raise RuntimeError(
            'FRESH_SOURCE_CLONE_FAILED='+
            (q.stderr or b'').decode(errors='replace')[-3000:]
        )
    q=run([
        'git','-C',work,'fetch','--no-tags',str(SRC),
        temp_ref+':refs/remotes/composer/candidate'
    ],check=False,timeout=300)
    if q.returncode:
        raise RuntimeError(
            'FRESH_SOURCE_COMPOSER_REF_FETCH_FAILED='+
            (q.stderr or b'').decode(errors='replace')[-3000:]
        )
    fetched=run(
        ['git','-C',work,'rev-parse','refs/remotes/composer/candidate'],
        check=False,timeout=30)
    fetched_rev=(fetched.stdout or b'').decode().strip()
    if fetched.returncode or fetched_rev!=rev:
        raise RuntimeError(
            'FRESH_SOURCE_COMPOSER_REV_MISMATCH expected='+rev+
            ' got='+fetched_rev
        )
    q=run(['git','-C',work,'checkout','--detach',rev],check=False,timeout=60)
    if q.returncode:
        raise RuntimeError(
            'FRESH_SOURCE_CHECKOUT_FAILED='+
            (q.stderr or b'').decode(errors='replace')[-3000:]
        )

def remove_fresh_source(work):
    if work.exists():
        shutil.rmtree(work,ignore_errors=True)

def submodules_at(work):
    q=run(['git','-C',work,'submodule','update','--init','--recursive','--force'],
          check=False,timeout=900)
    if q.returncode:
        raise RuntimeError(
            'SUBMODULE_UPDATE_FAILED='+
            (q.stderr or b'').decode(errors='replace')[-3000:]
        )


def exe(d):
    for p in (d/'shadps4',d/'bin/shadps4'):
        if p.is_file() and not p.is_symlink() and os.access(p,os.X_OK):return p
    a=[]
    for p in d.rglob('shadps4'):
        try:
            if p.is_file() and not p.is_symlink() and os.access(p,os.X_OK):a.append((p.stat().st_mtime,p))
        except OSError:pass
    return max(a)[1] if a else None
def tee(a,p):
    with p.open('wb') as f:
        q=subprocess.Popen([str(x) for x in a],env=env(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        for b in iter(q.stdout.readline,b''):
            if not b:break
            sys.stdout.buffer.write(b);sys.stdout.buffer.flush();f.write(b);f.flush()
        rc=q.wait()
    if rc:raise RuntimeError(f'BUILD_FAILED={rc} log={p}')

def compose(run_dir,info,manifest):
    parent=manifest['previous_parent']
    if git('rev-parse','HEAD').decode().strip()!=BASE:
        raise RuntimeError('ORIGINAL_GOT_SOURCE_HEAD_CHANGED')
    assert_source_unchanged()
    for x in (parent+'^{commit}',GEN_BASE+'^{commit}',GEN+'^{commit}',STRIDED_SELECTOR_FINAL+'^{commit}'):
        if not exists(x):raise RuntimeError('OBJECT_MISSING='+x)
    names=tuple(git('diff','--name-only',GEN_BASE,GEN,'--','src').decode().splitlines())
    if set(names)!=set(GEN_FILES):
        missing=sorted(set(GEN_FILES)-set(names))
        extra=sorted(set(names)-set(GEN_FILES))
        raise RuntimeError(
            'GENERIC_CHANGE_SET_CHANGED missing='+','.join(missing)+
            ' extra='+','.join(extra)
        )
    # PARENT is always authoritative. Never replace a parent file wholesale
    # with an older generic-branch blob. For files with known API overlap,
    # transform the verified parent. For the rest, 3-way apply only GEN's
    # exact per-file delta onto PARENT.
    idx=run_dir/'index'
    e=env({'GIT_INDEX_FILE':str(idx)})
    git('read-tree',parent,e=e)
    for p in GEN_FILES:
        if p in (
            'src/shader_recompiler/ir/passes/indexed_image_selector.h',
            'src/shader_recompiler/guest_tsharp_array.h',
        ):
            continue
        fn=TRANS.get(p)
        if fn:
            content=fn(show(parent,p).decode()).encode()
            oid=git('hash-object','-w','--stdin',input=content).decode().strip()
            git('update-index','--add','--cacheinfo',f'100644,{oid},{p}',e=e)
        else:
            apply_range_file(idx,GEN_BASE,GEN,p,'GENERIC_BASE')

    # Seed the final generic indexed-selector helper directly. The three
    # historical commits that progressively built this header are superseded
    # by this exact later generic version; replaying their old intermediate
    # hunks onto the newer packed-selector base only creates false conflicts.
    selector_path='src/shader_recompiler/ir/passes/indexed_image_selector.h'
    selector=show(STRIDED_SELECTOR_FINAL,selector_path)
    selector_text=selector.decode()
    for marker in (
        'AnalyzeStridedIndexedSharpWord',
        'template <std::size_t N>',
        'GcnTextureDwordIndex',
        'static_assert(N == 4 || N == 8)',
    ):
        if marker not in selector_text:
            raise RuntimeError('STRIDED_SELECTOR_FINAL_MISSING='+marker)
    oid=git('hash-object','-w','--stdin',input=selector).decode().strip()
    git('update-index','--add','--cacheinfo',f'100644,{oid},{selector_path}',e=e)

    guest_table_path='src/shader_recompiler/guest_tsharp_array.h'
    guest_table=show(GUEST_TSHARP_FINAL,guest_table_path)
    guest_text=guest_table.decode()
    for marker in (
        'CompleteGuestTsharpTableEntries',
        'CompleteGuestTsharpRecordEntries',
        'GuestTsharpIndexOrNull',
    ):
        if marker not in guest_text:
            raise RuntimeError('FINAL_GUEST_TSHARP_HELPER_MISSING='+marker)
    oid=git('hash-object','-w','--stdin',input=guest_table).decode().strip()
    git('update-index','--add','--cacheinfo',f'100644,{oid},{guest_table_path}',e=e)

    sampler_policy_path='src/video_core/texture_cache/sampler_filter_policy.h'
    sampler_policy=show(INTEGER_SAMPLER_FINAL,sampler_policy_path)
    sampler_policy_text=sampler_policy.decode()
    for marker in ('ImageSamplerFilter','ImageSamplerMipmapMode','SamplerAnisotropyAllowed'):
        if marker not in sampler_policy_text:
            raise RuntimeError('INTEGER_SAMPLER_POLICY_MISSING='+marker)
    oid=git('hash-object','-w','--stdin',input=sampler_policy).decode().strip()
    git('update-index','--add','--cacheinfo',f'100644,{oid},{sampler_policy_path}',e=e)

    for sha in PATCH_COMMITS:
        apply_patch_commit(idx,sha)
    finish_generic_large(idx)

    # Cross-file dependency gate: the selected historical commits are
    # intentionally narrower than the old experimental branch. Verify the
    # complete generic feature chain is present before creating/building a
    # candidate so omissions fail here, not as opaque C++ errors later.
    required_markers={
        'src/shader_recompiler/ir/passes/indexed_image_selector.h':(
            'AnalyzeStridedIndexedSharpWord',
            'template <std::size_t N>',
            'GcnTextureDwordIndex',
        ),
        'src/shader_recompiler/ir/passes/resource_pass.h':(
            'has_indexed_sampler_table',
            'indexed_record_stride_bytes',
            'indexed_sampler_offset_bytes',
        ),
        'src/shader_recompiler/resource.h':(
            'is_guest_indexed',
            'indexed_guest_entries',
            'indexed_sampler',
            'is_integer',
        ),
        'src/shader_recompiler/ir/passes/resource_patching_pass.cpp':(
            'ImageGradientIndexed',
            'indexed_sampler',
            'max_guest_indexed_sampler_entries',
        ),
        'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp':(
            'supports_sampler_array_nonuniform_indexing',
            'SampledImageArrayNonUniformIndexing',
        ),
        'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp':(
            'ImageGradientIndexed',
            'ImageGatherExtended',
        ),
        'src/video_core/renderer_vulkan/vk_rasterizer.cpp':(
            'is_guest_indexed',
            'indexed_sampler',
            'NumBindings(stage)',
            'GetSampler(',
        ),
        'src/video_core/renderer_vulkan/vk_resource_pool.cpp':(
            'sampled_image_slots',
            'pool_sizes_.begin()',
        ),
        'src/video_core/renderer_vulkan/vk_resource_pool.h':(
            'std::vector<vk::DescriptorPoolSize> pool_sizes;',
        ),
        'src/video_core/texture_cache/sampler.cpp':(
            'is_integer',
            'ImageSamplerFilter',
        ),
    }
    for path,markers in required_markers.items():
        text=index_text(idx,path)
        for marker in markers:
            if marker not in text:
                raise RuntimeError(
                    'GENERIC_FEATURE_DEPENDENCY_MISSING path='+path+
                    ' marker='+marker
                )

    instance_cpp=index_text(idx,'src/video_core/renderer_vulkan/vk_instance.cpp')
    if 'shaderSamplerArrayNonUniformIndexing' in instance_cpp:
        raise RuntimeError('INVALID_VULKAN_SAMPLER_NONUNIFORM_FEATURE_RETAINED')

    tree=git('write-tree',e=e).decode().strip()
    changed=tuple(git('diff','--name-only',parent,tree,'--','src').decode().splitlines())
    allowed=set(FILES)
    extra=[p for p in changed if p not in allowed]
    if extra: raise RuntimeError('UNEXPECTED_CHANGED_PATHS='+','.join(extra))
    for p in changed:
        b=git('show',tree+':'+p)
        oid=git('rev-parse',tree+':'+p).decode().strip()
        original=git('rev-parse',f'{parent}:{p}').decode().strip() if exists(f'{parent}:{p}') else None
        manifest['files'][p]={'original_blob':original,'corrected_blob':oid,'sha256':hashlib.sha256(b).hexdigest()}
    msg=b'Complete generic GPU-indexed image and sampler tables on verified candidate\n\nPreserve complete packed and strided guest descriptor tables with GPU-side selection, physical Vulkan limits and ordinary descriptor-set fallback. No title IDs, shader-hash branches, forced readbacks, forced draws or rendering presets.\n'
    rev=commit_tree(tree,parent,msg)
    diff=git('diff',parent,rev,'--','src').decode(errors='replace')
    added='\n'.join(
        line[1:] for line in diff.splitlines()
        if line.startswith('+') and not line.startswith('+++')
    )
    for pat in (r'CUSA\d+',r'Ghost of Tsushima',r'Tsushima',
                r'pgm_hash\s*==',r'title_id\s*==',
                r'getenv\([^\n]*SHADPS4_',r'readbacks_mode\s*=',
                r'readbacksMode\s*='):
        if re.search(pat,added,re.I):
            raise RuntimeError('GAME_SPECIFIC_SOURCE_ADDITION='+pat)
    final_feature_text='\n'.join((
        index_text(idx,'src/shader_recompiler/ir/opcodes.inc'),
        index_text(idx,'src/shader_recompiler/ir/passes/resource_patching_pass.cpp'),
        index_text(idx,'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp'),
        index_text(idx,'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp'),
        index_text(idx,'src/video_core/renderer_vulkan/vk_rasterizer.cpp'),
    ))
    for marker in (
        'ImageSampleImplicitLodIndexed',
        'ImageGradientIndexed',
        'NonUniform',
        'indexed_sampler',
        'ImageGatherExtended',
    ):
        if marker not in final_feature_text:
            raise RuntimeError('INDEXED_PATH_MISSING='+marker)
    temp_ref='refs/shadps4-composer/generic-large-indexed-resource-'+rev[:12]
    git('update-ref',temp_ref,rev)
    (info/'generic-large-indexed-resource.patch').write_text(diff)
    manifest['generic_indexed_image_tables']={'source_commit':GEN,'source_base':GEN_BASE,'composed_parent':parent,'replayed_generic_commits':list(PATCH_COMMITS),'integer_sampler_source':INTEGER_SAMPLER_FINAL,'vulkan_indexing_feature_fixes':list(VULKAN_INDEXING_FEATURE_FIXES),'exact_guest_descriptor_bytes':32,'max_guest_entries':32768,'explicit_null_slot':True,'push_descriptor_fallback_to_descriptor_set':True,'physical_descriptor_limits_checked':True,'runtime_image_offsets':'CAPABILITY_GATED_IMAGE_GATHER_EXTENDED','unsupported_forms_use_existing_path':True,'game_or_title_selectors':False,'shader_hash_selectors':False,'readback_override':False,'forced_draw_state':False,'static_texture_substitution':False,'parent_fixes_preserved':True,'original_source_untouched':True,'fresh_source_clone':True,'all_games_validated':False}
    mp=info/'correction.json';mp.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    work,d=fresh_paths(rev)
    cand=None;csha=''
    try:
        add_fresh_source(work,rev,temp_ref)
        if temp_ref:
            git('update-ref','-d',temp_ref,check=False)
        submodules_at(work)
        print('FRESH_SOURCE_CLONE='+str(work),flush=True)
        print('FRESH_BUILD_DIRECTORY='+str(d),flush=True)
        print('COMPOSED_REVISION='+rev,flush=True)
        tee([
            'cmake','--fresh','-S',work,'-B',d,'-G','Ninja',
            '-DCMAKE_BUILD_TYPE=Release',
            '-DENABLE_TESTS=OFF',
            '-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=ON',
        ],info/'configure.log')
        tee([
            'nice','-n','10','cmake','--build',d,
            '--target','shadps4','--parallel','2',
        ],info/'full-build.log')
        built=exe(d)
        if not built:
            raise RuntimeError('NEW_BUILD_EXECUTABLE_NOT_FOUND')
        csha=digest(built)
        cand=ROOT/'GhostOfTsushima/candidates'/f'{rev}-{csha}'/'shadps4'
        cand.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(built,cand)
        cand.chmod(cand.stat().st_mode|stat.S_IXUSR|stat.S_IXGRP|stat.S_IXOTH)
    finally:
        # Original GoT source checkout is never checked out or reset. Remove
        # the temporary reachability ref and disposable source clone even when
        # configure/build fails. Preserve a failed build directory because the
        # compiler output there can still be useful; successful builds are
        # copied to candidates and their build directory is discarded.
        if temp_ref:
            git('update-ref','-d',temp_ref,check=False)
        remove_fresh_source(work)
        if cand is not None and cand.is_file():
            shutil.rmtree(d,ignore_errors=True)
    if git('rev-parse','HEAD').decode().strip()!=BASE:
        raise RuntimeError('ORIGINAL_GOT_SOURCE_HEAD_CHANGED_AFTER_BUILD')
    assert_source_unchanged()
    if not cand or digest(cand)!=csha:raise RuntimeError('CANDIDATE_COPY_FAILED')
    ms=digest(mp)
    report={'status':'GENERIC_LARGE_INDEXED_RESOURCE_BUILD_PASS','base':BASE,'source_parent':parent,'source_parent_tree':git('rev-parse',parent+'^{tree}').decode().strip(),'verified_parent':manifest['verified_parent'],'verified_parent_tree':PARENT_TREE,'upstream_main':UPSTREAM_MAIN,'generic_source_commit':GEN,'revision':rev,'tree':tree,'candidate':str(cand),'candidate_sha256':csha,'manifest_sha256':ms,'full_build':'PASS','game_started':False,'source_untouched':True,'fresh_source_clone':True,'fresh_build_from_zero':True,'legacy_builds_searched':False,'tests_enabled':False,'original_submodules_untouched':True,'automatic_coherence':'RETAINED','srt_dependencies':'RETAINED','runtime_masked_reduction':'RETAINED','buffer_phi_selection':'RETAINED','image_resource_correctness':'RETAINED','runtime_image_offsets':'CAPABILITY_GATED_IMAGE_GATHER_EXTENDED','generic_indexed_image_tables':'GPU_NONUNIFORM_COMPLETE_TSHARP_SSHARP','game_specific_options':False,'all_games_validated':False}
    (info/'build-result.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    return rev,tree,csha,cand

def launch(info,rev,tree,csha,cand):
    if digest(OLD)!=OLD_SHA:raise RuntimeError('PROTECTED_LAUNCHER_CHANGED_OR_MISSING')
    spec=importlib.util.spec_from_file_location('generic_indexed_runtime',OLD);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    rp=info/'build-result.json';mp=info/'correction.json';rs=digest(rp);ms=digest(mp)
    m.INFO=info;m.REV=rev;m.TREE=tree;m.PARENT=json.loads(rp.read_text())['source_parent'];m.BIN_SHA=csha;m.REPORT_SHA=rs;m.MANIFEST_SHA=ms;m.SESSION='got-generic-large-indexed-'+rev[:12]
    def verify_build():
        if digest(rp)!=rs or digest(mp)!=ms:raise RuntimeError('BUILD_EVIDENCE_CHANGED')
        r=json.loads(rp.read_text())
        expected={'status':'GENERIC_LARGE_INDEXED_RESOURCE_BUILD_PASS',
                  'generic_source_commit':GEN,'revision':rev,'tree':tree,
                  'candidate_sha256':csha,'manifest_sha256':ms,'full_build':'PASS',
                  'source_untouched':True,'fresh_source_clone':True,'fresh_build_from_zero':True,
                  'legacy_builds_searched':False,'tests_enabled':False,
                  'original_submodules_untouched':True,
                  'verified_parent_tree':PARENT_TREE,'upstream_main':UPSTREAM_MAIN,
                  'generic_indexed_image_tables':'GPU_NONUNIFORM_COMPLETE_TSHARP_SSHARP',
                  'game_specific_options':False}
        for k,v in expected.items():
            if r.get(k)!=v:raise RuntimeError('BUILD_GATE='+k)
        verified_parent=r.get('verified_parent')
        if not verified_parent or not exists(verified_parent+'^{commit}'):
            raise RuntimeError('VERIFIED_BUILD_PARENT_MISSING')
        if git('rev-parse',verified_parent+'^{tree}').decode().strip()!=PARENT_TREE:
            raise RuntimeError('VERIFIED_BUILD_PARENT_TREE_MISMATCH')

        source_parent=r.get('source_parent')
        if not source_parent or not exists(source_parent+'^{commit}'):
            raise RuntimeError('BUILD_PARENT_MISSING')
        source_parent_tree=git('rev-parse',source_parent+'^{tree}').decode().strip()
        if source_parent_tree!=r.get('source_parent_tree'):
            raise RuntimeError('BUILD_PARENT_TREE_MISMATCH')
        parent_line=git('rev-list','--parents','-n','1',source_parent).decode().split()
        if len(parent_line)!=3 or parent_line[1]!=verified_parent or parent_line[2]!=UPSTREAM_MAIN:
            raise RuntimeError('UPSTREAM_MERGE_PARENTAGE_MISMATCH')
        if (Path(r['candidate'])!=cand or digest(cand)!=csha or
            git('rev-parse',rev+'^{tree}').decode().strip()!=tree or
            git('rev-parse',rev+'^').decode().strip()!=source_parent):
            raise RuntimeError('CANDIDATE_CHANGED')
        if git('rev-parse','HEAD').decode().strip()!=BASE:
            raise RuntimeError('ORIGINAL_GOT_SOURCE_HEAD_CHANGED_AFTER_BUILD')
        assert_source_unchanged()
        man=json.loads(mp.read_text())
        for p,x in man['files'].items():
            b=x.get('corrected_blob')
            if b and git('rev-parse',rev+':'+p).decode().strip()!=b:raise RuntimeError('SOURCE_BLOB='+p)
        return r,cand
    m.verify_build=verify_build
    print('GENERIC_LARGE_INDEXED_RESOURCE_CANDIDATE='+rev+' FULLSCREEN=ON FPS=ON REBUILD=DONE',flush=True)

    # The protected runtime launcher predates the SSH-safe runner contract and
    # may terminate its own process group during cleanup. Never let it share
    # the SSH shell's process group. Fork once, create a new session in the
    # child, and keep the build/composer parent attached to the caller.
    pid=os.fork()
    if pid==0:
        try:
            os.setsid()
            rc=int(m.main())
        except BaseException:
            import traceback
            traceback.print_exc()
            rc=125
        os._exit(rc & 0xff)
    while True:
        try:
            _,status=os.waitpid(pid,0)
            break
        except InterruptedError:
            continue
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        sig=os.WTERMSIG(status)
        print('ISOLATED_RUNTIME_SIGNAL='+str(sig),file=sys.stderr,flush=True)
        return 128+sig
    return 125

def archive(run_dir,error):
    info=run_dir/'info';info.mkdir(parents=True,exist_ok=True)
    if error is not None:
        (info/'failure.txt').write_text(type(error).__name__+': '+str(error)+'\n')
    out=Path(str(run_dir)+'.tar.gz')
    with tarfile.open(out,'w:gz') as t:
        for p in run_dir.rglob('*'):
            if p.is_file() and p.name!='private-binary':t.add(p,arcname=p.relative_to(run_dir))
    return out

def main():
    global BASE, BASE_STATUS
    parser=argparse.ArgumentParser()
    parser.add_argument('--launch',action='store_true',
        help='Launch the built candidate after a successful fresh build.')
    args=parser.parse_args()
    if not SRC.is_dir() or not (SRC/'.git').exists():
        print('GOT_SOURCE_REPOSITORY_MISSING='+str(SRC),file=sys.stderr,flush=True)
        return 2
    BASE=git('rev-parse','HEAD').decode().strip()
    BASE_STATUS=source_status()
    validate_source_status(BASE_STATUS)
    (ROOT/'logs').mkdir(parents=True,exist_ok=True);run_dir=Path(tempfile.mkdtemp(prefix='generic-large-indexed-resource-'+time.strftime('%Y%m%d-%H%M%S')+'-',dir=ROOT/'logs'));info=run_dir/'info';info.mkdir()
    try:
        with (ROOT/'GhostOfTsushima/.build.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('ANOTHER_SHADPS4_BUILD_OWNS_LOCK')
            man=verify_prev(run_dir)
            rev,tree,csha,cand=compose(run_dir,info,man)
        print('BUILD_ONLY_PASS=YES',flush=True)
        print('CANDIDATE='+str(cand),flush=True)
        print('COMPOSED_REVISION='+rev,flush=True)
        print('SOURCE_PARENT='+man['previous_parent'],flush=True)
        print('UPSTREAM_MAIN='+UPSTREAM_MAIN,flush=True)
        print('VERIFIED_PARENT_TREE='+PARENT_TREE,flush=True)
        if not args.launch:
            print('GAME_LAUNCHED=NO',flush=True)
            print('UPLOAD_THIS='+str(archive(run_dir,None)),flush=True)
            return 0
        if not OLD.is_file() or digest(OLD)!=OLD_SHA:
            raise RuntimeError('PROTECTED_LAUNCHER_MISSING_OR_CHANGED='+str(OLD))
        return launch(info,rev,tree,csha,cand)
    except (Exception,KeyboardInterrupt) as e:
        out=archive(run_dir,e);print('GENERIC_LARGE_INDEXED_RESOURCE_ERROR='+str(e),file=sys.stderr,flush=True);print('UPLOAD_THIS='+str(out),flush=True);return 2
if __name__=='__main__':raise SystemExit(main())
