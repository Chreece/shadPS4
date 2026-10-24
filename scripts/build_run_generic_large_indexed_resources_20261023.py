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
UPSTREAM_MAIN='396c2a09a38d22eaa02aab32c1967263dceab95f'
PR_5492_REMOTE='https://github.com/johulk/shadPS4.git'
PR_5492_HEAD='9f55474ad45778f8ac08e923574f214d492997f2'
PARENT_REF=PARENT
PARENT_TREE_ACTUAL=PARENT_TREE
PARENT_MODE='exact-verified-tree'
BASE_STATUS=b''
RECOVERY_PARTS=tuple(f'patches/verified-975.patch.gz.b64.part{i:02d}' for i in range(1,9))
GEN_BASE='9f4c2da6107cb2b31578cc4b35f5e4ed85637dab'
GEN='464e32e92b7f36fede81f0d65ca0e81934051d1e'
STRIDED_SELECTOR_FINAL='d72990fbd8fe6483a921fa2c015e1d65b1e1f336'
GUEST_TSHARP_FINAL='02c22fd61253b072a9bc4017e67f9c39fa0d4fa9'
INTEGER_SAMPLER_FINAL='aab1275779c989f0624cd6dcb9582410d5534381'
PM4_BOUNDARY_SOURCE='ffd19ac832130e2165388a7110d46c0fa6f6e4e9'
RECONSTRUCTION_BASE='3965a497532edb7b18027692ab04eb97fa4c145b'
AUTOMATIC_COHERENCE_PACKAGE='e226327408b202c1c115d3a7f2dc5e405e2c6683'
SHARED_SRT_PACKAGE='8f0ccf7c3195f5ac5ef0faef16f005dd0e680eca'
NATIVE_UMIN_SOURCE='a81ff9b7cdb62efb51513fd6428218761054c42e'
NATIVE_UMIN_SIRIT='adf61937d466fbcfa103ff1e3935bd7718101fd8'
IMPLICIT_DMA_PACKAGE='0bde46afaa33b29051b9d8a2c1b31f9c0f6876d6'
CPU_ID_EXTRACTION_SOURCE='7edfa0c5b420bffeab70576e2b0da6d635bb0866'
PROVEN_SOURCE_COMMITS=(
    'a12c8c8c5c95c2acccb8c5bedc10a466fef0feba',
    NATIVE_UMIN_SOURCE,
    'bfb0d71c6b68a036fc18046398a7369c3908d0e8',
    '1a7789b7f5c633d4f4c6cbe331758d2f1d5897f7',
    '7ace685062a2c97870b61738a4b184a7882a5f6a',
    '21f5b3179dbedb26718da44c38a9b3c56909a034',
    'fb8171791b387b2980e2a3aa20c317a3022bf0a9',
    CPU_ID_EXTRACTION_SOURCE,
    'ffd19ac832130e2165388a7110d46c0fa6f6e4e9',
    '12a07a6d83007f76012f762e68fedb5954aa0f8a',
    'f94c4f09e6111c0f9ff9fd1c5125c762ae88eb07',
    '822a68d8aae409b5a29759de462eb43329e52753',
)
VULKAN_INDEXING_FEATURE_FIXES=(
    'cbbcc969156f9258633914409ca88e6458bbad2c',
    '538fc65527d96086443d14d3227eb9c5daa7397f',
)
DESCRIPTOR_SET_SCALING_FIXES=(
    '5568da00029a865a77f6bf8df6510750473cc97a',
    'c6f721296e7442062a176cacabf8579968ecfbbb',
    '6d97d6818550e1e0f296f78351db0acdf2e21518',
    'f74a01cb704cb6f25450a578613968f6dc94798b',
    '6b6d462d1d1fcbdbd488af8fbfe7eb323f7480a7',
    '29983ea0417a920db38c0b0854f610eb482050a1',
)
DYNAMIC_IMAGE_BINDING_FIXES=(
    '16ab0edbd70e1912ec3de581466788f89c11b39c',
    '9e30254c86b532eb7c5921a3268dcb2a00e7d48d',
)
STRIDED_TSHARP_FIXES=(
    'be463d8d361a23d66ec60147ed268389a58b1f22',
    '15ec6ede7034701b9c63e6e47203b6eb352c9377',
    '0dc9a1dff77d7f6148e70e47ab5c6b43da66999a',
    '5cf2cf61930223824024c058cfe72a819f56048e',
    'f336b0dbfc20d35d83642472269cac77c0299ac4',
    'b29106a5c6b6231a9ffb787f9277ef10c31e9182',
)
EXPLICIT_GRADIENT_INDEXING_FIXES=(
    'f2169f2fb9a4902b4dbb752c90c8fa037f6bb8cf',
    'a61c10ff0059b03db7437807fa4c9b17208f4f8b',
    'e6b7acce13db1c2ca3fe5189a19458290d5bc74f',
    '2718fe1101bc24eb8cd57b09f9dd22f3ac3b4e2b',
    '39d58e288e254877be1cfb307293b8f26be0a195',
    '5610f50a6239f82b70bf154c2c1ee6fbe8477def',
)
PAIRED_INDEXED_SAMPLER_FRONTEND_FIXES=(
    '0cecd34d1760fdd5720408bbd9661aaa6ea828c4',
    '20ec7bfbb7a1ee18dac78d3c9bdeed29274580c2',
    '8b75cb1197818094d2a31ca4879bc62c842b1134',
    '9b4d6ebd6523b8f28d543da0ebaa841733cfdf7f',
)
PAIRED_INDEXED_SAMPLER_BACKEND_FIXES=(
    'dd00aa320c9eb86c70584f36dce808c2483246a0',
    'ec6f257ed6852478a915d96c514f54d290c112a2',
    '3edc91cd217563acb9206772e4773c4ca1cf73d8',
    '630f51ecf3c8a7bb8afac698a435ca27e8b23fad',
    'bfa0d918e33f1364b052e953812aa662f2ad2275',
    '6ff828d3a06b2222597c89c596ff500863c4a18f',
    '34a46b656f75a7fdd215086d5be2b1c6042016b4',
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
'src/video_core/amdgpu/liverpool.cpp',
'src/video_core/amdgpu/pm4_register_write.h',
'src/video_core/renderer_vulkan/vk_compute_pipeline.cpp',
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
)

def env(extra=None):
    e={k:v for k,v in os.environ.items() if k not in ('GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_COMMON_DIR')}
    # This host is intentionally IPv4-only behind its VPN. Keep Git and
    # recursive submodule fetches on IPv4 so network selection cannot stall
    # a source-correct build before compilation begins.
    e.setdefault('GIT_CURL_IPRESOLVE','4')
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

def stage(message):
    print('STAGE='+message,flush=True)

def source_status():
    return git('status','--porcelain=v1','--untracked-files=all')

def _head_gitlink(path):
    q=run(['git','-C',SRC,'ls-tree','HEAD','--',path],check=False,timeout=30)
    if q.returncode:
        return False
    line=(q.stdout or b'').decode(errors='replace').strip()
    return line.startswith('160000 commit ')

def validate_source_status(status):
    # Composition reads immutable Git objects and builds in an independent
    # fresh clone. Allow only known local state that cannot affect that source:
    # deleted tests (tests are disabled), dirty submodule worktrees whose
    # top-level gitlink itself is unchanged, and the local fresh-owner marker.
    # Indexed/staged submodule changes or ordinary source edits remain fatal.
    bad=[]
    for raw in status.decode(errors='replace').splitlines():
        if not raw:
            continue
        code=raw[:2]
        path=raw[3:]
        if code.strip()=='D' and (path=='tests' or path.startswith('tests/')):
            continue
        if code=='??' and path=='.ghost-fresh-owner.json':
            continue
        if code==' M' and path.startswith('externals/') and _head_gitlink(path):
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
                '    u32 max_guest_indexed_image_entries{15};\n',
                'max_guest_indexed_image_entries','PROFILE_IMAGE_LIMIT')
    if 'max_guest_indexed_sampler_entries' not in s:
        anchor='    u32 max_guest_indexed_image_entries{15};\n'
        if anchor not in s:
            raise RuntimeError('PROFILE_SAMPLER_LIMIT_ANCHOR')
        s=after(s,anchor,
                '    u32 max_guest_indexed_sampler_entries{15};\n',
                'max_guest_indexed_sampler_entries','PROFILE_SAMPLER_LIMIT')
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
        # VkPhysicalDeviceFeatures declares sampled-image dynamic indexing
        # after the storage-image feature block and before shaderClipDistance.
        # C++ designated initializers must follow declaration order.
        anchor='                .shaderStorageImageMultisample = features.shaderStorageImageMultisample,\n'
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
    constexpr u32 type_reserve=Shader::NUM_IMAGES+32u;
    constexpr u32 total_reserve=
        Shader::NUM_IMAGES+Shader::NUM_BUFFERS+Shader::NUM_SAMPLERS+32u;
    const u32 type_hard=std::min(
        limits.maxDescriptorSetSampledImages,
        limits.maxPerStageDescriptorSampledImages);
    const u32 type_real=type_hard>type_reserve+1u
        ? std::min(Shader::MaxGuestIndexedImageEntries,type_hard-type_reserve-1u)
        : 0u;
    const u32 total_real=limits.maxPerStageResources>total_reserve+1u
        ? std::min(Shader::MaxGuestIndexedImageEntries,
                   limits.maxPerStageResources-total_reserve-1u)
        : 0u;
    return std::min(type_real,total_real);
}
static u32 GuestIndexedSamplerLimit(const Instance& instance) {
    const auto limits=instance.GetPhysicalDevice().getProperties().limits;
    constexpr u32 type_reserve=Shader::NUM_SAMPLERS+32u;
    constexpr u32 total_reserve=
        Shader::NUM_IMAGES+Shader::NUM_BUFFERS+Shader::NUM_SAMPLERS+32u;
    const u32 type_hard=std::min(
        limits.maxDescriptorSetSamplers,
        limits.maxPerStageDescriptorSamplers);
    const u32 type_real=type_hard>type_reserve+1u
        ? std::min(Shader::MaxGuestIndexedImageEntries,type_hard-type_reserve-1u)
        : 0u;

    // Strided material records bind a sampled-image array and a sampler array
    // of the same count (including one null/default slot each). Reserve total
    // per-stage resources for BOTH arrays, not each one independently.
    const u32 pair_real=limits.maxPerStageResources>total_reserve+2u
        ? std::min(
            Shader::MaxGuestIndexedImageEntries,
            (limits.maxPerStageResources-total_reserve)/2u-1u)
        : 0u;
    return std::min(type_real,pair_real);
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
                '        .max_guest_indexed_image_entries = GuestIndexedImageLimit(instance),\n',
                '.max_guest_indexed_image_entries =','PIPELINE_IMAGE_LIMIT_PROFILE')
    if '.max_guest_indexed_sampler_entries =' not in s:
        anchor='        .max_guest_indexed_image_entries = GuestIndexedImageLimit(instance),\n'
        if anchor not in s:
            raise RuntimeError('PIPELINE_SAMPLER_LIMIT_ANCHOR')
        s=after(s,anchor,
                '        .max_guest_indexed_sampler_entries = GuestIndexedSamplerLimit(instance),\n',
                '.max_guest_indexed_sampler_entries =','PIPELINE_SAMPLER_LIMIT_PROFILE')
    if 'supports_shader_image_gather_extended =' not in s:
        s=after(s,'        .supports_image_load_store_lod = instance_.IsImageLoadStoreLodSupported(),\n',
                '        .supports_shader_image_gather_extended =\n'
                '            instance_.IsShaderImageGatherExtendedSupported(),\n',
                'supports_shader_image_gather_extended =','PIPELINE_DYNAMIC_OFFSET')
    if 'supports_sampled_image_nonuniform_indexing =' not in s:
        anchor=(
            '        .supports_shader_image_gather_extended =\n'
            '            instance_.IsShaderImageGatherExtendedSupported(),\n'
            if 'supports_shader_image_gather_extended =' in s
            else
            '        .supports_image_load_store_lod = instance_.IsImageLoadStoreLodSupported(),\n'
        )
        s=after(s,anchor,
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
    if 'has_indexed_table' not in s:
        s=after(
            s,
            '    std::array<SharpReference, 2> sharps;\n',
            '    SharpReference indexed_table_sharp{};\n'
            '    IR::Value indexed_texture_id{};\n'
            '    u32 indexed_record_stride_bytes{};\n'
            '    u32 indexed_descriptor_offset_bytes{};\n'
            '    u32 indexed_sampler_offset_bytes{};\n'
            '    bool has_indexed_sampler_table{};\n'
            '    bool has_indexed_table{};\n',
            'has_indexed_table','PASS_FIELDS')
    else:
        anchor='    IR::Value indexed_texture_id{};\n'
        if 'indexed_record_stride_bytes' not in s:
            s=after(
                s,anchor,
                '    u32 indexed_record_stride_bytes{};\n'
                '    u32 indexed_descriptor_offset_bytes{};\n',
                'indexed_record_stride_bytes','PASS_STRIDED_FIELDS')
        if 'indexed_sampler_offset_bytes' not in s:
            anchor='    u32 indexed_descriptor_offset_bytes{};\n'
            if anchor not in s:
                raise RuntimeError('PASS_INDEXED_SAMPLER_OFFSET_ANCHOR')
            s=s.replace(
                anchor,
                anchor+'    u32 indexed_sampler_offset_bytes{};\n',
                1)
        if 'has_indexed_sampler_table' not in s:
            anchor='    u32 indexed_sampler_offset_bytes{};\n'
            if anchor not in s:
                raise RuntimeError('PASS_INDEXED_SAMPLER_FLAG_ANCHOR')
            s=s.replace(
                anchor,
                anchor+'    bool has_indexed_sampler_table{};\n',
                1)
    for marker in (
        'indexed_record_stride_bytes',
        'indexed_descriptor_offset_bytes',
        'indexed_sampler_offset_bytes',
        'has_indexed_sampler_table',
        'has_indexed_table',
    ):
        if marker not in s:
            raise RuntimeError('PASS_INDEXED_FIELD_MISSING='+marker)
    return s

def resource_h(s):
    max_re=re.compile(r'inline constexpr u32 MaxGuestIndexedImageEntries\s*=\s*\d+\s*;')
    m=max_re.search(s)
    if m:
        s=s[:m.start()]+'inline constexpr u32 MaxGuestIndexedImageEntries = 32768;'+s[m.end():]
    else:
        a='enum class MipStorageFallbackMode : u16 {\n    None,\n    DynamicIndex,\n    ConstantIndex,\n};\n'
        s=one(
            s,a,
            a+'\ninline constexpr u32 MaxGuestIndexedImageEntries = 32768;\n',
            'RESOURCE_MAX')
    if 'LegacyGuestIndexedImageEntries' not in s:
        anchor='inline constexpr u32 MaxGuestIndexedImageEntries = 32768;\n'
        if anchor not in s:
            raise RuntimeError('RESOURCE_LEGACY_LIMIT_ANCHOR')
        s=s.replace(
            anchor,
            'inline constexpr u32 LegacyGuestIndexedImageEntries = 15;\n'+anchor,
            1)
    if 'inline constexpr u32 MaxGuestIndexedImageEntries = 32768;' not in s:
        raise RuntimeError('RESOURCE_MAX_LIMIT_NOT_NORMALIZED')

    start=s.find('struct ImageResource {')
    end=s.find('using ImageResourceList',start)
    if start<0 or end<0:
        raise RuntimeError('IMAGE_RESOURCE_STRUCT_NOT_FOUND')
    body=s[start:end]

    image_anchor='    SharpFetch<AmdGpu::Image> sharp_fetch{};\n'
    if body.count(image_anchor)!=1:
        raise RuntimeError('RESOURCE_FIELDS_ANCHOR_COUNT='+str(body.count(image_anchor)))
    body=body.replace('    u8 indexed_guest_entries{};','    u32 indexed_guest_entries{};')
    ordered_image_fields=(
        ('    SharpFetch<AmdGpu::Buffer> indexed_table_fetch{};\n',
         'RESOURCE_INDEXED_TABLE_FETCH'),
        ('    u32 indexed_guest_entries{};\n',
         'RESOURCE_INDEXED_GUEST_ENTRIES'),
        ('    u32 indexed_record_stride_bytes{};\n',
         'RESOURCE_INDEXED_RECORD_STRIDE'),
        ('    u32 indexed_descriptor_offset_bytes{};\n',
         'RESOURCE_INDEXED_DESCRIPTOR_OFFSET'),
        ('    bool is_guest_indexed{};\n',
         'RESOURCE_INDEXED_FLAG'),
    )
    insert_at=body.find(image_anchor)+len(image_anchor)
    for declaration,label in ordered_image_fields:
        if declaration not in body:
            body=body[:insert_at]+declaration+body[insert_at:]
            insert_at+=len(declaration)
        else:
            pos=body.find(declaration)
            insert_at=max(insert_at,pos+len(declaration))
    for declaration,label in ordered_image_fields:
        if body.count(declaration)!=1:
            raise RuntimeError(label+'_DECL_COUNT='+str(body.count(declaration)))

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
    sampler_start=s.find('struct SamplerResource {')
    sampler_end=s.find('using SamplerResourceList',sampler_start)
    if sampler_start<0 or sampler_end<0:
        raise RuntimeError('SAMPLER_RESOURCE_STRUCT_NOT_FOUND')
    sampler=s[sampler_start:sampler_end]
    field_anchor='    SharpFetch<AmdGpu::Sampler> sharp_fetch{};\n'
    if sampler.count(field_anchor)!=1:
        raise RuntimeError('SAMPLER_INDEXED_FIELDS_ANCHOR_COUNT='+str(sampler.count(field_anchor)))
    ordered_fields=(
        ('    SharpFetch<AmdGpu::Buffer> indexed_table_fetch{};\n',
         'SAMPLER_INDEXED_TABLE_FETCH'),
        ('    u32 indexed_guest_entries{};\n',
         'SAMPLER_INDEXED_GUEST_ENTRIES'),
        ('    u32 indexed_record_stride_bytes{};\n',
         'SAMPLER_INDEXED_RECORD_STRIDE'),
        ('    u32 indexed_image_offset_bytes{};\n',
         'SAMPLER_INDEXED_IMAGE_OFFSET'),
        ('    u32 indexed_sampler_offset_bytes{};\n',
         'SAMPLER_INDEXED_SAMPLER_OFFSET'),
        ('    bool is_guest_indexed{};\n',
         'SAMPLER_INDEXED_FLAG'),
    )
    insert_at=sampler.find(field_anchor)+len(field_anchor)
    for declaration,label in ordered_fields:
        if declaration not in sampler:
            sampler=sampler[:insert_at]+declaration+sampler[insert_at:]
            insert_at+=len(declaration)
        else:
            pos=sampler.find(declaration)
            insert_at=max(insert_at,pos+len(declaration))
    for declaration,label in ordered_fields:
        if sampler.count(declaration)!=1:
            raise RuntimeError(label+'_DECL_COUNT='+str(sampler.count(declaration)))
    get_anchor='    constexpr AmdGpu::Sampler GetSharp(const auto& info) const noexcept {\n'
    if 'if (is_guest_indexed && indexed_record_stride_bytes != 0u)' not in sampler:
        if sampler.count(get_anchor)!=1:
            raise RuntimeError('SAMPLER_INDEXED_GETSHARP_ANCHOR')
        sampler=sampler.replace(
            get_anchor,
            get_anchor+'        if (is_guest_indexed && indexed_record_stride_bytes != 0u) {\n'
                       '            return AmdGpu::Sampler{};\n'
                       '        }\n',
            1)
    if 'u32 NumBindings(const auto& info) const' not in sampler:
        close=sampler.rfind('};')
        if close<0:
            raise RuntimeError('SAMPLER_INDEXED_NUM_BINDINGS_END')
        num='''    u32 NumBindings(const auto& info) const {
        return is_guest_indexed ? indexed_guest_entries + 1u : 1u;
    }
'''
        sampler=sampler[:close]+num+sampler[close:]
    s=s[:sampler_start]+sampler+s[sampler_end:]
    return s

HELPER=r'''
struct IndexedImageTableSource {
    const IR::Inst* buffer{};
    const IR::Inst* selector{};
    u32 descriptor_stride_words{1};
    u32 record_stride_bytes{};
    u32 tsharp_offset_bytes{};
    bool gpu_texture_id_indexed{};
    std::array<const IR::Inst*, 8> words{};
    std::array<u32, 8> addends{};
    bool contiguous{};
};
static IndexedImageTableSource AnalyzeIndexedImageTable(const IR::Inst& sample) {
    IndexedImageTableSource out{};
    if (sample.GetOpcode()!=IR::Opcode::ImageSampleRaw) return out;
    const auto* image=sample.Arg(0).TryInst();
    if (!image || image->GetOpcode()!=IR::Opcode::ImageHandle) return out;
    const auto* low=image->Arg(0).TryInst();
    const auto* high=image->Arg(1).TryInst();
    if (!low || !high || low->NumArgs()!=4 || high->NumArgs()!=4) return out;
    std::array<IndexedSharpWord,8> norm{};
    for(std::size_t i=0;i<8;++i) {
        const auto* read=(i<4?low->Arg(i):high->Arg(i-4)).TryInst();
        if(!read || read->GetOpcode()!=IR::Opcode::ReadConstBuffer) return out;
        const auto* buffer=read->Arg(0).TryInst();
        if(!buffer || buffer->GetOpcode()!=IR::Opcode::CompositeConstructU32x4) return out;
        if(i==0) {
            out.buffer=buffer;
        } else if(out.buffer!=buffer) {
            bool identical=true;
            for(u32 dw=0;dw<4;++dw) {
                identical &= out.buffer->Arg(dw)==buffer->Arg(dw);
            }
            if(!identical) return out;
        }
        out.words[i]=read;
        norm[i]=AnalyzeIndexedSharpWord(read->Arg(1));
        if(!norm[i].valid) return out;
        out.addends[i]=norm[i].addend;
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
    return out;
}

struct IndexedSamplerTableSource {
    u32 offset_bytes{};
    bool valid{};
};

static IndexedSamplerTableSource AnalyzeIndexedSamplerTable(
    const IR::Inst& sample, const IndexedImageTableSource& image) {
    if (sample.GetOpcode()!=IR::Opcode::ImageSampleRaw ||
        !image.contiguous || image.record_stride_bytes==0 ||
        !image.selector || !image.buffer) return {};
    const auto* sampler=sample.Arg(1).TryInst();
    if (!sampler || sampler->GetOpcode()!=IR::Opcode::CompositeConstructU32x4 ||
        sampler->NumArgs()!=4u) return {};
    std::array<StridedIndexedSharpWord,4> words{};
    for(u32 i=0;i<4;++i) {
        const auto* read=sampler->Arg(i).TryInst();
        if(!read || read->GetOpcode()!=IR::Opcode::ReadConstBuffer) return {};
        const auto* buffer=read->Arg(0).TryInst();
        if(!buffer || buffer->GetOpcode()!=IR::Opcode::CompositeConstructU32x4 ||
           buffer->NumArgs()!=4u) return {};
        for(u32 j=0;j<4;++j) {
            if(buffer->Arg(j)!=image.buffer->Arg(j)) return {};
        }
        words[i]=AnalyzeStridedIndexedSharpWord(read->Arg(1));
    }
    if(!AreContiguousStridedSharpWords(words) ||
       words[0].gpu_record_id!=image.selector ||
       words[0].record_stride_bytes!=image.record_stride_bytes ||
       u64(words[0].offset_dwords)*4u+sizeof(AmdGpu::Sampler)>image.record_stride_bytes) {
        return {};
    }
    return {words[0].offset_dwords*4u,true};
}
'''
LOOP=r'''                if (inst.GetOpcode()==IR::Opcode::ImageSampleRaw &&
                    profile.supports_sampled_image_nonuniform_indexing) {
                    const auto table=AnalyzeIndexedImageTable(inst);
                    if(table.contiguous && table.gpu_texture_id_indexed &&
                       (table.descriptor_stride_words==8u ||
                        table.record_stride_bytes!=0u)) {
                        auto& usage=sharp_usages.back(); bool bindable=true;
                        for(std::size_t dw=0;dw<4;++dw) {
                            const IR::Value v=table.buffer->Arg(dw);
                            if(!v.IsImmediate()) {
                                const auto* p=v.TryInst(); bindable &= p && IsSharpSource(p);
                            }
                            usage.indexed_table_sharp.dwords[dw]=v;
                        }
                        const auto sampler=table.record_stride_bytes
                            ? AnalyzeIndexedSamplerTable(inst,table)
                            : IndexedSamplerTableSource{};
                        if(bindable && (table.record_stride_bytes==0u || sampler.valid)) {
                            usage.indexed_sampler_offset_bytes=sampler.offset_bytes;
                            usage.has_indexed_sampler_table=sampler.valid;
                            usage.indexed_table_sharp.num_dwords=4;
                            MarkReadConstBufferSharpSources(usage.indexed_table_sharp, program, inst);
                            usage.indexed_texture_id=IR::Value{const_cast<IR::Inst*>(table.selector)};
                            usage.indexed_record_stride_bytes=table.record_stride_bytes;
                            usage.indexed_descriptor_offset_bytes=table.tsharp_offset_bytes;
                            usage.has_indexed_table=true;
                        }
                    }
                }
'''
def discover(s):
    if 'indexed_image_selector.h' not in s:
        s=after(
            s,
            '#include "shader_recompiler/ir/passes/ir_passes.h"\n',
            '#include "shader_recompiler/ir/passes/indexed_image_selector.h"\n',
            'indexed_image_selector.h','DISCOVER_INCLUDE')

    helper_start=s.find('struct IndexedImageTableSource {')
    if helper_start>=0:
        fn_start=s.find('static IndexedImageTableSource AnalyzeIndexedImageTable(',helper_start)
        if fn_start<0:
            raise RuntimeError('DISCOVER_INDEXED_ANALYZER_START_MISSING')
        brace=s.find('{',fn_start)
        if brace<0:
            raise RuntimeError('DISCOVER_INDEXED_ANALYZER_BRACE_MISSING')
        depth=0
        fn_end=None
        for i in range(brace,len(s)):
            depth += (s[i]=='{')-(s[i]=='}')
            if depth==0:
                fn_end=i+1
                break
        if fn_end is None:
            raise RuntimeError('DISCOVER_INDEXED_ANALYZER_UNBALANCED')

        # If an older paired sampler analyzer already follows, remove it too;
        # HELPER below supplies the canonical current version exactly once.
        sampler_start=s.find('struct IndexedSamplerTableSource {',fn_end)
        resource_pass=s.find('ResourceDiscoveryList ResourceDiscoverPass',fn_end)
        replace_end=fn_end
        if sampler_start>=0 and resource_pass>=0 and sampler_start<resource_pass:
            sampler_fn=s.find('static IndexedSamplerTableSource AnalyzeIndexedSamplerTable(',sampler_start)
            if sampler_fn<0:
                raise RuntimeError('DISCOVER_SAMPLER_ANALYZER_START_MISSING')
            brace=s.find('{',sampler_fn)
            depth=0
            sampler_end=None
            for i in range(brace,len(s)):
                depth += (s[i]=='{')-(s[i]=='}')
                if depth==0:
                    sampler_end=i+1
                    break
            if sampler_end is None:
                raise RuntimeError('DISCOVER_SAMPLER_ANALYZER_UNBALANCED')
            replace_end=sampler_end
        s=s[:helper_start]+HELPER+s[replace_end:]
    else:
        a='ResourceDiscoveryList ResourceDiscoverPass(IR::Program& program, const Profile& profile) {'
        s=one(s,a,HELPER+'\n'+a,'DISCOVER_HELPER')

    loop=LOOP
    if 'void MarkReadConstBufferSharpSources(const SharpReference& sharp)' in s:
        loop=loop.replace(
            'MarkReadConstBufferSharpSources(usage.indexed_table_sharp, program, inst);',
            'MarkReadConstBufferSharpSources(usage.indexed_table_sharp);')
    elif ('MarkReadConstBufferSharpSources(usage.indexed_table_sharp, program, inst);'
          not in s and 'void MarkReadConstBufferSharpSources(' not in s):
        raise RuntimeError('DISCOVER_MARK_API_UNKNOWN')

    # Normalize the entire custom indexed discovery block in ResourceDiscoverPass.
    rp=s.find('ResourceDiscoveryList ResourceDiscoverPass(')
    if rp<0:
        raise RuntimeError('RESOURCE_DISCOVER_PASS_NOT_FOUND')
    rp_end=s.find('\n    return sharp_usages;',rp)
    if rp_end<0:
        raise RuntimeError('RESOURCE_DISCOVER_PASS_END_NOT_FOUND')
    body=s[rp:rp_end]
    custom_start=body.find(
        '                if (inst.GetOpcode()==IR::Opcode::ImageSampleRaw &&')
    if custom_start<0:
        custom_start=body.find(
            '                if (inst.GetOpcode() == IR::Opcode::ImageSampleRaw &&')
    if custom_start>=0:
        brace=body.find('{',custom_start)
        depth=0
        custom_end=None
        for i in range(brace,len(body)):
            depth += (body[i]=='{')-(body[i]=='}')
            if depth==0:
                custom_end=i+1
                break
        if custom_end is None:
            raise RuntimeError('DISCOVER_INDEXED_LOOP_UNBALANCED')
        body=body[:custom_start]+loop.rstrip('\n')+body[custom_end:]
    else:
        anchors=(
            '                DiscoverImageSharp(*block, inst, sharp_usages, program);\n',
            '                DiscoverImageSharp(*block, inst, sharp_usages);\n',
        )
        selected=None
        for anchor in anchors:
            if body.count(anchor)==1:
                selected=anchor
                break
        if selected is None:
            raise RuntimeError(
                'DISCOVER_LOOP_API_NOT_ADAPTED old_count='+str(body.count(anchors[0]))+
                ' new_count='+str(body.count(anchors[1])))
        body=body.replace(selected,selected+loop,1)
    s=s[:rp]+body+s[rp_end:]

    for marker in (
        'AnalyzeStridedIndexedSharpWord',
        'AnalyzeIndexedSamplerTable',
        'usage.indexed_record_stride_bytes=table.record_stride_bytes;',
        'usage.indexed_descriptor_offset_bytes=table.tsharp_offset_bytes;',
        'usage.indexed_sampler_offset_bytes=sampler.offset_bytes;',
        'usage.has_indexed_sampler_table=sampler.valid;',
    ):
        if marker not in s:
            raise RuntimeError('DISCOVER_INDEXED_NORMALIZATION_MISSING='+marker)
    return s

ELIG=r'''    if (resource.has_indexed_table &&
        profile.supports_sampled_image_nonuniform_indexing &&
        profile.supports_sampler_array_nonuniform_indexing &&
        (resource.indexed_record_stride_bytes==0u ||
         (resource.has_indexed_sampler_table &&
          resource.sharps[1].post_op==SharpFetchPostOp::None)) &&
        !is_written && !inst_info.is_depth && !inst_info.is_array && !inst_info.is_gather &&
        !inst_info.has_lod && !inst_info.force_level0 &&
        !inst_info.has_lod_clamp && !inst_info.is_r128 && !inst_info.is_unnormalized) {
        const auto table_fetch=__FETCH__;
        AmdGpu::Buffer table{};
        if(table_fetch.Fetch(info.flattened_ud_buf.data(),&table) && table.Valid() && table.base_address!=0) {
            const u32 record_stride=resource.indexed_record_stride_bytes;
            const u32 record_offset=resource.indexed_descriptor_offset_bytes;
            const u32 indexed_limit=
                std::min(profile.max_guest_indexed_image_entries,
                         profile.max_guest_indexed_sampler_entries);
            const u32 entries=record_stride!=0u
                ? CompleteGuestTsharpRecordEntries(
                    static_cast<u32>(table.stride),static_cast<u32>(table.num_records),
                    record_stride,record_offset,indexed_limit)
                : CompleteGuestTsharpTableEntries(
                    static_cast<u32>(table.stride),static_cast<u32>(table.num_records),
                    indexed_limit);
            if(entries!=0u) {
                image_res.indexed_table_fetch=table_fetch;
                image_res.indexed_guest_entries=entries;
                image_res.indexed_record_stride_bytes=record_stride;
                image_res.indexed_descriptor_offset_bytes=record_offset;
                image_res.is_guest_indexed=true;
            }
        }
    }

'''
def patching(s):
    if 'guest_tsharp_array.h' not in s:
        s=after(s,'#include "shader_recompiler/resource.h"\n','#include "shader_recompiler/guest_tsharp_array.h"\n','guest_tsharp_array.h','PATCH_INCLUDE')
    # Normalize image-resource dedup so packed and strided tables are distinct.
    a=s.find('u32 Add(const ImageResource& desc)')
    b=s.find('u32 Add(const SamplerResource& desc)',a)
    if a<0 or b<0:
        raise RuntimeError('PATCH_DEDUP_FUNCTION')
    image_add=s[a:b]
    if 'desc.is_guest_indexed == existing.is_guest_indexed' not in image_add:
        p=image_add.find('desc.post_op == existing.post_op')
        if p<0:
            raise RuntimeError('PATCH_DEDUP_ANCHOR')
        p+=len('desc.post_op == existing.post_op')
        x=' &&\n                   desc.is_guest_indexed == existing.is_guest_indexed &&\n' \
          '                   desc.indexed_guest_entries == existing.indexed_guest_entries &&\n' \
          '                   desc.indexed_record_stride_bytes == existing.indexed_record_stride_bytes &&\n' \
          '                   desc.indexed_descriptor_offset_bytes == existing.indexed_descriptor_offset_bytes &&\n' \
          '                   desc.indexed_table_fetch == existing.indexed_table_fetch'
        image_add=image_add[:p]+x+image_add[p:]
    else:
        if 'desc.indexed_record_stride_bytes == existing.indexed_record_stride_bytes' not in image_add:
            anchor='desc.indexed_guest_entries == existing.indexed_guest_entries &&'
            if anchor not in image_add:
                raise RuntimeError('PATCH_STRIDED_DEDUP_ANCHOR')
            image_add=image_add.replace(
                anchor,
                anchor+'\n                   desc.indexed_record_stride_bytes == existing.indexed_record_stride_bytes &&\n'
                       '                   desc.indexed_descriptor_offset_bytes == existing.indexed_descriptor_offset_bytes &&',
                1)
    s=s[:a]+image_add+s[b:]

    # Normalize the entire custom indexed-image eligibility block, even when
    # the verified parent already contains the earlier packed-only form.
    use_info=bool(re.search(
        r'ConstructSharpFetch\s*\(\s*const SharpReference&\s+sharp\s*,\s*const Info&\s+info',
        s))
    call=('ConstructSharpFetch<AmdGpu::Buffer>(resource.indexed_table_sharp, info)'
          if use_info else
          'ConstructSharpFetch<AmdGpu::Buffer>(resource.indexed_table_sharp)')
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
    indexed_start=body.find('    if (resource.has_indexed_table')
    normalized=ELIG.replace('__FETCH__',call)
    if indexed_start>=0:
        anchor_pos=body.find(anchor,indexed_start)
        if anchor_pos<0:
            raise RuntimeError('PATCH_EXISTING_INDEXED_BLOCK_END_MISSING')
        body=body[:indexed_start]+normalized+body[anchor_pos:]
    else:
        body=body.replace(anchor,normalized+anchor,1)
    s=s[:fn_start]+body+s[fn_end:]
    if 'image_res.indexed_record_stride_bytes=record_stride;' not in s[fn_start:fn_start+len(body)]:
        raise RuntimeError('PATCH_STRIDED_ELIG_NOT_NORMALIZED')
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
    # Numeric format and indexed-table layout are part of Vulkan sampler identity.
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
    if 'desc.is_guest_indexed == existing.is_guest_indexed' not in add_body:
        anchor='desc.is_integer == existing.is_integer'
        if add_body.count(anchor)!=1:
            raise RuntimeError('SAMPLER_INDEXED_DEDUP_ANCHOR')
        add_body=add_body.replace(
            anchor,
            anchor+' &&\n'
                   '                   desc.is_guest_indexed == existing.is_guest_indexed &&\n'
                   '                   desc.indexed_guest_entries == existing.indexed_guest_entries &&\n'
                   '                   desc.indexed_record_stride_bytes == existing.indexed_record_stride_bytes &&\n'
                   '                   desc.indexed_image_offset_bytes == existing.indexed_image_offset_bytes &&\n'
                   '                   desc.indexed_sampler_offset_bytes == existing.indexed_sampler_offset_bytes &&\n'
                   '                   desc.indexed_table_fetch == existing.indexed_table_fetch',
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

    fn_start=s.find('void PatchImageSharp(')
    fn_end=s.find('\nstatic IR::Value IsMbcntWithExec',fn_start)
    if fn_end<0:
        fn_end=s.find('\nstatic ',fn_start+1)
    body=s[fn_start:fn_end]
    if 'sampler_res.is_guest_indexed = true;' not in body:
        old='''        const u32 sampler_binding = descriptors.Add(SamplerResource{
'''
        if old in body:
            init_start=body.find(old)
            init_end=body.find('        });\n',init_start)
            if init_end<0:
                raise RuntimeError('PATCH_SAMPLER_AGGREGATE_END')
            init_end+=len('        });\n')
            aggregate=body[init_start:init_end]
            inner=aggregate.replace(
                '        const u32 sampler_binding = descriptors.Add(SamplerResource{',
                '        SamplerResource sampler_res{',1)
            inner=inner.replace('        });\n','        };\n',1)
            extra='''        if (image_res.is_guest_indexed) {
            if (image_res.indexed_record_stride_bytes != 0u) {
                ASSERT(resource.has_indexed_sampler_table);
            }
            sampler_res.is_guest_indexed = true;
            sampler_res.indexed_table_fetch = image_res.indexed_table_fetch;
            sampler_res.indexed_guest_entries = image_res.indexed_guest_entries;
            sampler_res.indexed_record_stride_bytes =
                image_res.indexed_record_stride_bytes;
            sampler_res.indexed_image_offset_bytes =
                image_res.indexed_descriptor_offset_bytes;
            sampler_res.indexed_sampler_offset_bytes =
                resource.indexed_sampler_offset_bytes;
        }
        const u32 sampler_binding = descriptors.Add(sampler_res);
'''
            body=body[:init_start]+inner+extra+body[init_end:]
        else:
            raise RuntimeError('PATCH_SAMPLER_AGGREGATE_START')
        s=s[:fn_start]+body+s[fn_end:]
    if ('sampler_res.indexed_sampler_offset_bytes' not in s[fn_start:fn_start+len(body)] or
        'sampler_res.indexed_image_offset_bytes' not in s[fn_start:fn_start+len(body)]):
        raise RuntimeError('PATCH_INDEXED_SAMPLER_NORMALIZATION_MISSING')

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
    if 'ir.ImageGradientIndexed(' not in s:
        fn_start=s.find('void PatchImageSampleArgs(')
        fn_end=s.find('\nvoid PatchImageArgs(',fn_start)
        if fn_start<0 or fn_end<0:
            raise RuntimeError('PATCH_GRADIENT_FUNCTION_RANGE')
        body=s[fn_start:fn_end]
        anchor='''        if (inst_info.has_derivatives) {
            return ir.ImageGradient(handle, coords, derivatives_dx, derivatives_dy, offset,
                                    lod_clamp, inst_info);
        }'''
        if anchor not in body:
            raise RuntimeError('PATCH_GRADIENT_INDEXED_ANCHOR')
        repl='''        if (inst_info.has_derivatives) {
            if (image_res.is_guest_indexed) {
                return ir.ImageGradientIndexed(
                    handle, coords, derivatives_dx, derivatives_dy, offset,
                    IR::U32{inst.Arg(1)}, inst_info);
            }
            return ir.ImageGradient(handle, coords, derivatives_dx, derivatives_dy, offset,
                                    lod_clamp, inst_info);
        }'''
        body=body.replace(anchor,repl,1)
        s=s[:fn_start]+body+s[fn_end:]
    return s
def opcodes(s):
    if 'OPCODE(ImageSampleImplicitLodIndexed' not in s:
        a='OPCODE(ImageSampleImplicitLod,                              F32x4,          Opaque,         F32x4,          F32,            Opaque,                         )\n'
        s=after(s,a,'OPCODE(ImageSampleImplicitLodIndexed,                       F32x4,          Opaque,         F32x4,          F32,            Opaque,         U32,            )\n','OPCODE(ImageSampleImplicitLodIndexed','OPCODE')
    if 'OPCODE(ImageGradientIndexed' not in s:
        a='OPCODE(ImageGradient,                                       F32x4,          Opaque,         Opaque,         Opaque,         Opaque,         Opaque,         F32,          )\n'
        s=after(s,a,'OPCODE(ImageGradientIndexed,                                F32x4,          Opaque,         Opaque,         Opaque,         Opaque,         Opaque,         U32,          )\n','OPCODE(ImageGradientIndexed','OPCODE_GRADIENT')
    return s
def spirv_decl(s):
    if 'EmitImageSampleImplicitLodIndexed' not in s:
        m=re.search(
            r'(Id EmitImageSampleImplicitLod\(EmitContext& ctx, IR::Inst\* inst, u32 handle, Id coords, Id bias,\n\s+const IR::Value& offset\);\n)',
            s)
        if not m:
            raise RuntimeError('SPIRV_DECL')
        x=('Id EmitImageSampleImplicitLodIndexed(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords,\n'
           '                                     Id bias, const IR::Value& offset, Id gpu_texture_id);\n')
        s=s[:m.end()]+x+s[m.end():]
    if 'EmitImageGradientIndexed' not in s:
        m=re.search(
            r'(Id EmitImageQueryLod\(EmitContext& ctx, IR::Inst\* inst, u32 handle, Id coords\);\n)',
            s)
        if not m:
            raise RuntimeError('SPIRV_GRADIENT_DECL_ANCHOR')
        x=('Id EmitImageGradientIndexed(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords,\n'
           '                            Id derivatives_dx, Id derivatives_dy,\n'
           '                            const IR::Value& offset, Id gpu_texture_id);\n')
        s=s[:m.end()]+x+s[m.end():]
    return s

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

    sampler_start=body.find('    sampler_type = TypeSampler();')
    if sampler_start<0:
        raise RuntimeError('SPIRV_SAMPLER_LOOP_START')
    sampler_end=body.rfind('\n}')
    if sampler_end<sampler_start:
        raise RuntimeError('SPIRV_SAMPLER_LOOP_END')
    sampler_block='''    sampler_type = TypeSampler();
    sampler_pointer_type = TypePointer(spv::StorageClass::UniformConstant, sampler_type);
    for (const auto& samp_desc : info.samplers) {
        const u32 count = samp_desc.NumBindings(info);
        Id pointer_type = sampler_pointer_type;
        if (samp_desc.is_guest_indexed) {
            ASSERT(profile.supports_sampler_array_nonuniform_indexing);
            ASSERT(count >= 2u);
            AddCapability(spv::Capability::ShaderNonUniform);
            AddCapability(spv::Capability::SampledImageArrayNonUniformIndexing);
            const Id array_type = TypeArray(sampler_type, ConstU32(count));
            pointer_type = TypePointer(spv::StorageClass::UniformConstant, array_type);
        }
        const Id id{AddGlobalVariable(pointer_type, spv::StorageClass::UniformConstant)};
        Decorate(id, spv::Decoration::Binding, binding.unified);
        binding.unified += count;
        Decorate(id, spv::Decoration::DescriptorSet, 0U);
        Name(id, fmt::format("{}_{}{}", hw_stage, "samp", samplers.size()));
        samplers.push_back(id);
        sampler_array_sizes.push_back(count);
        interfaces.push_back(id);
    }'''
    body=body[:sampler_start]+sampler_block+body[sampler_end:]
    for marker in (
        'samp_desc.NumBindings(info)',
        'sampler_array_sizes.push_back(count)',
        'SampledImageArrayNonUniformIndexing',
    ):
        if marker not in body:
            raise RuntimeError('SPIRV_INDEXED_SAMPLER_DECL_MISSING='+marker)

    s=s[:fn_start]+body+s[fn_end:]
    return s

def spirv_context_h(s):
    struct_name=None
    for candidate in ('TextureDefinition','ImageDefinition'):
        marker='    struct '+candidate+' {'
        if marker in s:
            struct_name=candidate
            break
    if struct_name is None:
        raise RuntimeError('SPIRV_TEXTURE_DEFINITION_STRUCT_NOT_FOUND')
    start=s.find('    struct '+struct_name+' {')
    end=s.find('    };',start)
    if end<0:
        raise RuntimeError('SPIRV_TEXTURE_DEFINITION_STRUCT_END_NOT_FOUND')
    end+=7
    body=s[start:end]
    if 'u32 image_array_size{1};' not in body:
        anchor='        MipStorageFallbackMode mip_fallback_mode{};\n'
        if body.count(anchor)!=1:
            raise RuntimeError('SPIRV_TEXTURE_DEFINITION_ANCHOR_COUNT='+str(body.count(anchor)))
        body=body.replace(
            anchor,
            anchor+'        u32 image_array_size{1};\n'
                   '        bool is_guest_indexed{};\n',
            1)
    s=s[:start]+body+s[end:]
    if 'sampler_array_sizes' not in s:
        anchor='    boost::container::small_vector<Id, 4> samplers;\n'
        if anchor not in s:
            raise RuntimeError('SPIRV_SAMPLER_ARRAY_SIZES_ANCHOR')
        s=s.replace(
            anchor,
            anchor+'    boost::container::small_vector<u32, 4> sampler_array_sizes;\n',
            1)
    for marker in ('image_array_size{1}','is_guest_indexed{}','sampler_array_sizes'):
        if marker not in s:
            raise RuntimeError('SPIRV_CONTEXT_HEADER_NORMALIZATION_MISSING='+marker)
    return s

def ir_emitter_h(s):
    if 'ImageSampleImplicitLodIndexed(' not in s:
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
        s=s[:m.end()]+decl+s[m.end():]
    if 'ImageGradientIndexed(' not in s:
        marker='    [[nodiscard]] Value ImageGradient('
        start=s.find(marker)
        if start<0:
            raise RuntimeError('IR_EMITTER_GRADIENT_DECL_START')
        end=s.find(';\n',start)
        if end<0:
            raise RuntimeError('IR_EMITTER_GRADIENT_DECL_END')
        end+=2
        decl=(
            '    [[nodiscard]] Value ImageGradientIndexed(const Value& handle, const Value& coords,\n'
            '                                             const Value& derivatives_dx, const Value& derivatives_dy,\n'
            '                                             const Value& offset, const U32& gpu_texture_id,\n'
            '                                             TextureInstInfo info);\n'
        )
        s=s[:end]+decl+s[end:]
    return s

def ir_emitter_cpp(s):
    if 'IREmitter::ImageSampleImplicitLodIndexed(' not in s:
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
        s=s[:nxt]+impl+s[nxt:]
    if 'IREmitter::ImageGradientIndexed(' not in s:
        marker='Value IREmitter::ImageGradient('
        start=s.find(marker)
        if start<0:
            raise RuntimeError('IR_EMITTER_GRADIENT_IMPL_START')
        nxt=s.find('\nValue IREmitter::ImageRead(',start)
        if nxt<0:
            raise RuntimeError('IR_EMITTER_GRADIENT_IMPL_END')
        impl=(
            '\nValue IREmitter::ImageGradientIndexed(const Value& handle, const Value& coords,\n'
            '                                      const Value& derivatives_dx, const Value& derivatives_dy,\n'
            '                                      const Value& offset, const U32& gpu_texture_id,\n'
            '                                      TextureInstInfo info) {\n'
            '    return Inst(Opcode::ImageGradientIndexed, Flags{info}, handle, coords,\n'
            '                derivatives_dx, derivatives_dy, offset, gpu_texture_id);\n'
            '}\n'
        )
        s=s[:nxt]+impl+s[nxt:]
    return s

def emit_spirv_image(s):
    if 'Id EmitImageSampleImplicitLodIndexed(' not in s:
        return _insert_indexed_spirv_sample(s)
    return _finish_indexed_spirv_image(s)

def _insert_indexed_spirv_sample(s):
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
    s=s[:nxt]+impl+s[nxt:]
    return _finish_indexed_spirv_image(s)

def _finish_indexed_spirv_image(s):
    if 'Id EmitImageGradientIndexed(' not in s:
        marker='Id EmitImageGradient(EmitContext& ctx'
        start=s.find(marker)
        if start<0:
            raise RuntimeError('SPIRV_GRADIENT_BASE_NOT_FOUND')
        nxt=s.find('\nId EmitImageRead(',start)
        if nxt<0:
            raise RuntimeError('SPIRV_GRADIENT_INSERT_POINT')
        impl=r'''
Id EmitImageGradientIndexed(EmitContext& ctx, IR::Inst* inst, u32 handle, Id coords,
                            Id derivatives_dx, Id derivatives_dy,
                            const IR::Value& offset, Id gpu_texture_id) {
    const auto& texture = ctx.images[handle & 0xffffu];
    ASSERT(texture.is_guest_indexed && texture.image_array_size >= 2u);
    ASSERT(ctx.profile.supports_sampled_image_nonuniform_indexing);
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
    const Id sampler = ctx.OpLoad(ctx.sampler_type, ctx.samplers[handle >> 16]);
    const Id sampled = ctx.OpSampledImage(texture.sampled_type, image, sampler);
    ctx.Decorate(sampled, spv::Decoration::NonUniform);
    const Id fixed_coords = FixImageCoords<true>(ctx, coords, texture.view_type);
    ImageOperands operands;
    operands.AddDerivatives(ctx, texture.view_type, derivatives_dx, derivatives_dy);
    operands.AddOffset(ctx, texture.view_type, offset);
    const Id raw = ctx.OpImageSampleExplicitLod(
        texture.data_types->Get(4), sampled, fixed_coords,
        operands.mask, operands.operands);
    return texture.is_integer ? ctx.OpBitcast(ctx.F32[4], raw) : raw;
}
'''
        s=s[:nxt]+impl+s[nxt:]
    if 'static Id LoadIndexedGuestSampler(' not in s:
        marker='Id EmitImageSampleImplicitLodIndexed('
        pos=s.find(marker)
        if pos<0:
            raise RuntimeError('SPIRV_INDEXED_SAMPLER_HELPER_INSERT')
        helper=r'''static Id LoadIndexedGuestSampler(EmitContext& ctx, u32 handle, Id bounded,
                                  u32 expected_count) {
    const u32 sampler_index = handle >> 16;
    ASSERT(sampler_index < ctx.samplers.size());
    const u32 sampler_count = ctx.sampler_array_sizes[sampler_index];
    if (sampler_count == 1u) {
        return ctx.OpLoad(ctx.sampler_type, ctx.samplers[sampler_index]);
    }
    ASSERT(ctx.profile.supports_sampler_array_nonuniform_indexing);
    ASSERT(sampler_count == expected_count);
    const Id pointer_type =
        ctx.TypePointer(spv::StorageClass::UniformConstant, ctx.sampler_type);
    const Id pointer =
        ctx.OpAccessChain(pointer_type, ctx.samplers[sampler_index], std::array{bounded});
    ctx.Decorate(pointer, spv::Decoration::NonUniform);
    const Id value = ctx.OpLoad(ctx.sampler_type, pointer);
    ctx.Decorate(value, spv::Decoration::NonUniform);
    return value;
}

'''
        s=s[:pos]+helper+s[pos:]

    for marker in ('Id EmitImageSampleImplicitLodIndexed(',
                   'Id EmitImageGradientIndexed('):
        start=s.find(marker)
        if start<0:
            raise RuntimeError('SPIRV_INDEXED_SAMPLER_TARGET_MISSING='+marker)
        next_id=s.find('\nId ',start+3)
        if next_id<0:
            next_id=len(s)
        body=s[start:next_id]
        body,count=re.subn(
            r'const Id sampler\s*=\s*ctx\.OpLoad\(ctx\.sampler_type,\s*ctx\.samplers\[handle >> 16\]\);',
            'const Id sampler = LoadIndexedGuestSampler(\n'
            '        ctx, handle, bounded, texture.image_array_size);',
            body,count=1)
        if count==0 and 'LoadIndexedGuestSampler(' not in body:
            raise RuntimeError('SPIRV_INDEXED_SAMPLER_LOAD_NOT_ADAPTED='+marker)
        s=s[:start]+body+s[next_id:]
    if 'ctx.sampler_array_sizes[sampler_index]' not in s:
        raise RuntimeError('SPIRV_INDEXED_SAMPLER_HELPER_INCOMPLETE')

    return s

def cache(s):
    # Cache ABI versions are bumped exactly once after every deterministic
    # graphics/resource transform has been applied.
    return s

RBIND=r'''        if (image_desc.is_guest_indexed) {
            const u32 count=image_desc.NumBindings(stage);
            ASSERT(count>=2 && count<=Shader::MaxGuestIndexedImageEntries+1);
            ASSERT(num_images+count<=image_bindings.size());
            AmdGpu::Buffer source{};
            const bool fetched=image_desc.indexed_table_fetch.Fetch(stage.flattened_ud_buf.data(),&source);
            const u64 bytes=fetched?(source.stride==0?static_cast<u64>(source.num_records):static_cast<u64>(source.stride)*source.num_records):0u;
            const u32 record_stride=image_desc.indexed_record_stride_bytes;
            const u32 descriptor_offset=image_desc.indexed_descriptor_offset_bytes;
            const u32 represented_entries=fetched && source.Valid()
                ? (record_stride
                    ? Shader::Optimization::CompleteGuestTsharpRecordEntries(
                        static_cast<u32>(source.stride),
                        static_cast<u32>(source.num_records),
                        record_stride,descriptor_offset,
                        Shader::MaxGuestIndexedImageEntries)
                    : Shader::Optimization::CompleteGuestTsharpTableEntries(
                        static_cast<u32>(source.stride),
                        static_cast<u32>(source.num_records),
                        Shader::MaxGuestIndexedImageEntries))
                : 0u;
            const u64 required_bytes=u64(count-1)*
                (record_stride ? record_stride : sizeof(AmdGpu::Image));
            const bool complete=fetched && source.Valid() && source.base_address!=0 &&
                represented_entries==count-1u && bytes==required_bytes &&
                memory->IsValidMapping(source.base_address,bytes);
            u64 estimated=0; constexpr u64 MaxEstimatedIndexedImageBytes=4ull<<30;
            const u32 max_dim=instance.GetPhysicalDevice().getProperties().limits.maxImageDimension2D;
            for(u32 i=0;i<count;++i) {
                auto& slot=image_bindings[num_images++]; slot.image_id={};
                if(!complete || i+1==count) continue;
                const u64 addr=source.base_address+
                    u64(i)*(record_stride ? record_stride : sizeof(AmdGpu::Image))+
                    descriptor_offset;
                if(!memory->IsValidMapping(addr,sizeof(AmdGpu::Image))) continue;
                AmdGpu::Image raw{}; std::memcpy(&raw,reinterpret_cast<const void*>(addr),sizeof(raw));
                if(!raw.Valid() || raw.GetType()!=AmdGpu::ImageType::Color2D || raw.Address()==0 ||
                   !memory->IsValidGpuMapping(raw.Address(),0)) continue;
                const auto fmt=raw.GetDataFmt(); const auto nfmt=raw.GetNumberFmt();
                if(!magic_enum::enum_contains(fmt) || !magic_enum::enum_contains(nfmt) ||
                   fmt==AmdGpu::DataFormat::FormatInvalid ||
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
def rasterizer_h(s):
    if '#include <vector>' not in s:
        first_include=s.find('#include ')
        if first_include<0:
            raise RuntimeError('RASTERIZER_VECTOR_INCLUDE_ANCHOR')
        s=s[:first_include]+'#include <vector>\n'+s[first_include:]
    s=s.replace(
        '    std::array<ImageBinding, Shader::NUM_IMAGES> image_bindings;\n',
        '    std::vector<ImageBinding> image_bindings;\n',
        1)
    s=s.replace(
        '    boost::container::static_vector<vk::DescriptorImageInfo, Shader::NUM_IMAGES> image_infos;\n',
        '    std::vector<vk::DescriptorImageInfo> image_infos;\n',
        1)
    for marker in (
        'std::vector<ImageBinding> image_bindings;',
        'std::vector<vk::DescriptorImageInfo> image_infos;',
    ):
        if marker not in s:
            raise RuntimeError('RASTERIZER_DYNAMIC_IMAGE_STORAGE_MISSING='+marker)
    return s

def raster(s):
    if '#include <cstring>' not in s:
        s=after(s,'#include "core/memory.h"\n','#include <cstring>\n','#include <cstring>','RASTER_CSTR')
    if 'guest_tsharp_array.h' not in s:
        s=after(s,'#include <cstring>\n','#include "shader_recompiler/guest_tsharp_array.h"\n','guest_tsharp_array.h','RASTER_HDR')

    bind_start=s.find('bool Rasterizer::BindResources(const Pipeline* pipeline)')
    if bind_start<0:
        raise RuntimeError('RASTER_BIND_RESOURCES_NOT_FOUND')
    bind_end=s.find('\nvoid Rasterizer::BindVertexBuffers',bind_start)
    if bind_end<0:
        raise RuntimeError('RASTER_BIND_RESOURCES_END_NOT_FOUND')
    bind_body=s[bind_start:bind_end]
    if 'required_infos = 0;' not in bind_body:
        anchor='''    buffer_infos.clear();
    image_infos.clear();

'''
        if bind_body.count(anchor)!=1:
            raise RuntimeError('RASTER_BIND_RESERVE_ANCHOR_COUNT='+str(bind_body.count(anchor)))
        block='''    buffer_infos.clear();
    image_infos.clear();

    // Reserve across all shader stages before any VkWriteDescriptorSet points
    // into image_infos. Later vector growth must not invalidate those pointers.
    std::size_t required_infos = 0;
    std::size_t max_stage_images = 0;
    for (const auto* stage : pipeline->GetStages()) {
        if (!stage) {
            continue;
        }
        std::size_t stage_images = 0;
        for (const auto& image : stage->images) {
            stage_images += image.NumBindings(*stage);
        }
        max_stage_images = std::max(max_stage_images, stage_images);
        required_infos += stage_images + stage->samplers.size();
    }
    if (max_stage_images >
        Shader::MaxGuestIndexedImageEntries + Shader::NUM_IMAGES * 64u) {
        LOG_ERROR(Render_Vulkan, "Rejecting excessive guest image descriptor count {}",
                  max_stage_images);
        return false;
    }
    image_infos.reserve(required_infos);
    image_bindings.resize(
        std::max(max_stage_images, std::size_t{Shader::NUM_IMAGES}));

'''
        bind_body=bind_body.replace(anchor,block,1)
        s=s[:bind_start]+bind_body+s[bind_end:]

    fn_start=s.find('void Rasterizer::BindTextures(')
    if fn_start<0:
        raise RuntimeError('RASTER_BIND_TEXTURES_NOT_FOUND')
    fn_end=s.find('\nRenderState Rasterizer::BeginRendering',fn_start)
    if fn_end<0:
        fn_end=s.find('\nvoid Rasterizer::',fn_start+1)
    if fn_end<0:
        raise RuntimeError('RASTER_BIND_TEXTURES_END_NOT_FOUND')
    body=s[fn_start:fn_end]

    # Normalize the custom indexed-image binding block even when the parent
    # already contains the earlier packed-only implementation.
    custom_start=body.find('        if (image_desc.is_guest_indexed) {')
    if custom_start>=0:
        brace=body.find('{',custom_start)
        depth=0
        custom_end=None
        for i in range(brace,len(body)):
            depth += (body[i]=='{')-(body[i]=='}')
            if depth==0:
                custom_end=i+1
                break
        if custom_end is None:
            raise RuntimeError('RASTER_INDEXED_BLOCK_UNBALANCED')
        body=body[:custom_start]+RBIND+body[custom_end:]
    else:
        a='    for (const auto& image_desc : stage.images) {\n'
        if body.count(a)!=1:
            raise RuntimeError('RASTER_IMAGE_LOOP_COUNT='+str(body.count(a)))
        body=body.replace(a,a+RBIND,1)
    for marker in (
        'image_desc.indexed_record_stride_bytes',
        'CompleteGuestTsharpRecordEntries',
        'descriptor_offset',
    ):
        if marker not in body:
            raise RuntimeError('RASTER_STRIDED_BIND_NORMALIZATION_MISSING='+marker)

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
    # Descriptor image-info storage must include every element of sampler arrays.
    bind_start=s.find('bool Rasterizer::BindResources(const Pipeline* pipeline)')
    bind_end=s.find('\nvoid Rasterizer::BindVertexBuffers',bind_start)
    if bind_start<0 or bind_end<0:
        raise RuntimeError('RASTER_BIND_RESOURCES_RANGE')
    bind_body=s[bind_start:bind_end]
    old='required_infos += stage_images + stage->samplers.size();'
    if old in bind_body:
        bind_body=bind_body.replace(
            old,
            'required_infos += stage_images;\n'
            '        for (const auto& sampler : stage->samplers) {\n'
            '            required_infos += sampler.NumBindings(*stage);\n'
            '        }',
            1)
    elif 'required_infos += sampler.NumBindings(*stage);' not in bind_body:
        raise RuntimeError('RASTER_SAMPLER_RESERVE_NOT_ADAPTED')
    s=s[:bind_start]+bind_body+s[bind_end:]

    fn_start=s.find('void Rasterizer::BindTextures(')
    fn_end=s.find('\nRenderState Rasterizer::BeginRendering',fn_start)
    if fn_start<0 or fn_end<0:
        raise RuntimeError('RASTER_BIND_TEXTURES_RANGE_FOR_SAMPLER')
    body=s[fn_start:fn_end]
    loop_anchor='    for (const auto& sampler : stage.samplers) {\n'
    if 'if (sampler.is_guest_indexed)' not in body:
        if body.count(loop_anchor)!=1:
            raise RuntimeError('RASTER_INDEXED_SAMPLER_LOOP_ANCHOR='+str(body.count(loop_anchor)))
        indexed=r'''        if (sampler.is_guest_indexed) {
            const u32 count = sampler.NumBindings(stage);
            AmdGpu::Buffer source{};
            const bool fetched =
                sampler.indexed_table_fetch.Fetch(stage.flattened_ud_buf.data(), &source);
            const u32 record_stride = sampler.indexed_record_stride_bytes;
            const u32 image_offset = sampler.indexed_image_offset_bytes;
            const u32 sampler_offset = sampler.indexed_sampler_offset_bytes;
            const u64 bytes = fetched
                ? (source.stride == 0 ? static_cast<u64>(source.num_records)
                                      : static_cast<u64>(source.stride) * source.num_records)
                : 0u;
            const u32 represented_entries = fetched && source.Valid()
                ? (record_stride
                    ? Shader::Optimization::CompleteGuestTsharpRecordEntries(
                        static_cast<u32>(source.stride),
                        static_cast<u32>(source.num_records),
                        record_stride, image_offset,
                        Shader::MaxGuestIndexedImageEntries)
                    : Shader::Optimization::CompleteGuestTsharpTableEntries(
                        static_cast<u32>(source.stride),
                        static_cast<u32>(source.num_records),
                        Shader::MaxGuestIndexedImageEntries))
                : 0u;
            const u64 required_bytes = u64(count - 1u) *
                (record_stride ? record_stride : sizeof(AmdGpu::Image));
            const bool complete = fetched && source.Valid() && source.base_address &&
                represented_entries == count - 1u &&
                bytes == required_bytes &&
                memory->IsValidMapping(source.base_address, bytes);

            AmdGpu::Sampler uniform_sampler{};
            bool uniform_sampler_valid = false;
            if (record_stride == 0u) {
                uniform_sampler = sampler.GetSharp(stage);
                uniform_sampler_valid =
                    uniform_sampler.Valid() &&
                    (uniform_sampler.border_color_type.Value() != AmdGpu::BorderColor::Custom ||
                     liverpool->regs.ta_bc_base.Address() != 0);
            }

            const u32 first_sampler = static_cast<u32>(image_infos.size());
            for (u32 i = 0; i < count; ++i) {
                AmdGpu::Sampler ssharp = uniform_sampler;
                bool sampler_valid = record_stride == 0u ? uniform_sampler_valid : false;
                bool is_integer = false;
                if (complete && i + 1u < count) {
                    const u64 record_address = u64(source.base_address) +
                        u64(i) * (record_stride ? record_stride : sizeof(AmdGpu::Image));
                    const u64 image_address = record_address + image_offset;
                    if (memory->IsValidMapping(image_address, sizeof(AmdGpu::Image))) {
                        AmdGpu::Image raw_image{};
                        std::memcpy(&raw_image,
                                    reinterpret_cast<const void*>(image_address),
                                    sizeof(AmdGpu::Image));
                        if (raw_image.Valid()) {
                            is_integer = AmdGpu::IsInteger(raw_image.GetNumberFmt());
                        }
                    }

                    if (record_stride != 0u) {
                        const u64 sampler_address = record_address + sampler_offset;
                        ssharp = AmdGpu::Sampler{};
                        sampler_valid = false;
                        if (memory->IsValidMapping(sampler_address, sizeof(AmdGpu::Sampler))) {
                            std::memcpy(&ssharp,
                                        reinterpret_cast<const void*>(sampler_address),
                                        sizeof(AmdGpu::Sampler));
                            sampler_valid =
                                ssharp.Valid() &&
                                (ssharp.border_color_type.Value() != AmdGpu::BorderColor::Custom ||
                                 liverpool->regs.ta_bc_base.Address() != 0);
                        }
                    }
                } else {
                    // Explicit null sampler pairs with the explicit null image slot.
                    sampler_valid = false;
                }
                vk::Sampler vk_sampler{};
                if (sampler_valid) {
                    vk_sampler = texture_cache.GetSampler(
                        ssharp, liverpool->regs.ta_bc_base,
                        sampler.is_depth, is_integer);
                }
                image_infos.emplace_back(
                    vk_sampler, VK_NULL_HANDLE, vk::ImageLayout::eGeneral);
            }
            auto& write = set_writes[set_write_index++];
            write.dstSet = VK_NULL_HANDLE;
            write.dstBinding = binding.unified;
            write.dstArrayElement = 0;
            write.descriptorCount = count;
            write.descriptorType = vk::DescriptorType::eSampler;
            write.pImageInfo = &image_infos[first_sampler];
            binding.unified += count;
            continue;
        }
'''
        body=body.replace(loop_anchor,loop_anchor+indexed,1)
    if ('sampler.NumBindings(stage)' not in body or
        'sampler.indexed_image_offset_bytes' not in body or
        'sampler.indexed_sampler_offset_bytes' not in body or
        'AmdGpu::IsInteger(raw_image.GetNumberFmt())' not in body or
        'vk::Sampler vk_sampler{};' not in body or
        'sampler_valid = false;' not in body or
        'write.descriptorCount = count' not in body):
        raise RuntimeError('RASTER_INDEXED_SAMPLER_BINDING_INCOMPLETE')
    s=s[:fn_start]+body+s[fn_end:]

    return s

def resource_pool_h(s):
    old='vk::DescriptorSet Commit(vk::DescriptorSetLayout set_layout);'
    new='vk::DescriptorSet Commit(vk::DescriptorSetLayout set_layout, u32 sampled_image_slots = 0);'
    if old in s:
        s=s.replace(old,new,1)
    if 'sampled_image_slots = 0' not in s:
        raise RuntimeError('RESOURCE_POOL_LARGE_SET_DECL_MISSING')
    return s

def resource_pool_cpp(s):
    old='vk::DescriptorSet DescriptorHeap::Commit(vk::DescriptorSetLayout set_layout) {'
    new=('vk::DescriptorSet DescriptorHeap::Commit(vk::DescriptorSetLayout set_layout,\n'
         '                                          u32 sampled_image_slots) {')
    if old in s:
        s=s.replace(old,new,1)
    if 'u32 sampled_image_slots)' not in s:
        raise RuntimeError('RESOURCE_POOL_LARGE_SET_IMPL_SIGNATURE_MISSING')

    old_decl='    DescSetBatch desc_sets(DescriptorSetBatch);\n'
    if old_decl in s:
        repl=(
            '    // Large descriptor arrays are allocated singly to avoid 32x\n'
            '    // amplification while retaining the existing fence-owned pool lifecycle.\n'
            '    const u32 batch_count = sampled_image_slots > 1024u ? 1u : DescriptorSetBatch;\n'
            '    DescSetBatch desc_sets(batch_count);\n')
        s=s.replace(old_decl,repl,1)
    s=s.replace(
        '        .descriptorSetCount = DescriptorSetBatch,',
        '        .descriptorSetCount = batch_count,',
        1)
    for marker in ('batch_count = sampled_image_slots > 1024u ? 1u : DescriptorSetBatch',
                   '.descriptorSetCount = batch_count'):
        if marker not in s:
            raise RuntimeError('RESOURCE_POOL_LARGE_SET_POLICY_MISSING='+marker)
    return s

def pipeline_common_h(s):
    if 'u32 sampled_image_descriptors{};' in s:
        return s
    anchor='    std::array<const Shader::Info*, Shader::MaxStageTypes> stages{};\n'
    if s.count(anchor)!=1:
        raise RuntimeError('PIPELINE_COMMON_DESCRIPTOR_COUNT_ANCHOR='+str(s.count(anchor)))
    return s.replace(anchor,anchor+'    u32 sampled_image_descriptors{};\n',1)

def pipeline_common_cpp(s):
    old='const auto desc_set = desc_heap.Commit(*desc_layout);'
    new='const auto desc_set = desc_heap.Commit(*desc_layout, sampled_image_descriptors);'
    if old in s:
        s=s.replace(old,new,1)
    if new not in s:
        raise RuntimeError('PIPELINE_COMMON_LARGE_SET_COMMIT_MISSING')
    return s

def graphics_pipeline(s):
    anchor='    uses_push_descriptors = binding < instance.MaxPushDescriptors();\n'
    if s.count(anchor)!=1:
        raise RuntimeError('GRAPHICS_PIPELINE_DESCRIPTOR_COUNT_ANCHOR='+str(s.count(anchor)))
    block=(
        '    sampled_image_descriptors = 0;\n'
        '    for (const auto& desc : bindings) {\n'
        '        if (desc.descriptorType == vk::DescriptorType::eSampledImage ||\n'
        '            desc.descriptorType == vk::DescriptorType::eSampler) {\n'
        '            sampled_image_descriptors += desc.descriptorCount;\n'
        '        }\n'
        '    }\n')
    fn_start=s.find('void GraphicsPipeline::BuildDescSetLayout(')
    if fn_start<0:
        raise RuntimeError('GRAPHICS_SAMPLER_LAYOUT_FUNCTION')
    fn_end=s.find('\n}',fn_start)
    if fn_end<0:
        raise RuntimeError('GRAPHICS_SAMPLER_LAYOUT_END')
    body=s[fn_start:fn_end]
    start=body.find('        for (const auto& sampler : stage->samplers) {')
    if start<0:
        raise RuntimeError('GRAPHICS_SAMPLER_LOOP_START')
    brace=body.find('{',start)
    depth=0
    end=None
    for i in range(brace,len(body)):
        depth += (body[i]=='{')-(body[i]=='}')
        if depth==0:
            end=i+1
            break
    if end is None:
        raise RuntimeError('GRAPHICS_SAMPLER_LOOP_UNBALANCED')
    loop='''        for (const auto& sampler : stage->samplers) {
            const u32 count = sampler.NumBindings(*stage);
            bindings.push_back({
                .binding = binding,
                .descriptorType = vk::DescriptorType::eSampler,
                .descriptorCount = count,
                .stageFlags = stage_bit,
            });
            binding += count;
        }'''
    body=body[:start]+loop+body[end:]
    s=s[:fn_start]+body+s[fn_end:]
    if 'sampled_image_descriptors = 0;' not in s:
        s=s.replace(anchor,block+anchor,1)
    for marker in (
        'const u32 count = sampler.NumBindings(*stage);',
        '.descriptorCount = count',
        'sampled_image_descriptors = 0;',
    ):
        if marker not in s:
            raise RuntimeError('GRAPHICS_PIPELINE_INDEXED_DESCRIPTOR_MISSING='+marker)
    return s

def compute_pipeline(s):
    anchor='    uses_push_descriptors = binding < instance.MaxPushDescriptors();\n'
    if s.count(anchor)!=1:
        raise RuntimeError('COMPUTE_PIPELINE_DESCRIPTOR_COUNT_ANCHOR='+str(s.count(anchor)))
    block=(
        '    sampled_image_descriptors = 0;\n'
        '    for (const auto& desc : bindings) {\n'
        '        if (desc.descriptorType == vk::DescriptorType::eSampledImage ||\n'
        '            desc.descriptorType == vk::DescriptorType::eSampler) {\n'
        '            sampled_image_descriptors += desc.descriptorCount;\n'
        '        }\n'
        '    }\n')
    start=s.find('    for (const auto& sampler : info->samplers) {')
    if start<0:
        raise RuntimeError('COMPUTE_SAMPLER_LOOP_START')
    brace=s.find('{',start)
    depth=0
    end=None
    for i in range(brace,len(s)):
        depth += (s[i]=='{')-(s[i]=='}')
        if depth==0:
            end=i+1
            break
    if end is None:
        raise RuntimeError('COMPUTE_SAMPLER_LOOP_UNBALANCED')
    loop='''    for (const auto& sampler : info->samplers) {
        const u32 count = sampler.NumBindings(*info);
        bindings.push_back({
            .binding = binding,
            .descriptorType = vk::DescriptorType::eSampler,
            .descriptorCount = count,
            .stageFlags = vk::ShaderStageFlagBits::eCompute,
        });
        binding += count;
    }'''
    s=s[:start]+loop+s[end:]
    if 'sampled_image_descriptors = 0;' not in s:
        s=s.replace(anchor,block+anchor,1)
    for marker in (
        'const u32 count = sampler.NumBindings(*info);',
        '.descriptorCount = count',
        'sampled_image_descriptors = 0;',
    ):
        if marker not in s:
            raise RuntimeError('COMPUTE_PIPELINE_INDEXED_DESCRIPTOR_MISSING='+marker)
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



def liverpool_pm4_boundaries(s):
    include='#include "video_core/amdgpu/pm4_register_write.h"\n'
    if include not in s:
        anchor='#include "video_core/amdgpu/pm4_cmds.h"\n'
        if anchor not in s:
            raise RuntimeError('PM4_REGISTER_INCLUDE_ANCHOR_MISSING')
        s=s.replace(anchor,anchor+include,1)

    def replace_set_sh_reg(text,function_name,replacement):
        start=text.find(function_name)
        if start<0:
            raise RuntimeError('PM4_FUNCTION_NOT_FOUND='+function_name)
        opening=text.find('{',start)
        if opening<0:
            raise RuntimeError('PM4_FUNCTION_BRACE_MISSING='+function_name)
        depth=0
        end=None
        for i in range(opening,len(text)):
            depth += (text[i]=='{')-(text[i]=='}')
            if depth==0:
                end=i+1
                break
        if end is None:
            raise RuntimeError('PM4_FUNCTION_UNBALANCED='+function_name)
        body=text[start:end]
        case_start=body.find('        case PM4ItOpcode::SetShReg: {')
        if case_start<0:
            raise RuntimeError('PM4_SET_SH_REG_CASE_MISSING='+function_name)
        brace=body.find('{',case_start)
        depth=0
        case_end=None
        for i in range(brace,len(body)):
            depth += (body[i]=='{')-(body[i]=='}')
            if depth==0:
                case_end=i+1
                break
        if case_end is None:
            raise RuntimeError('PM4_SET_SH_REG_CASE_UNBALANCED='+function_name)
        body=body[:case_start]+replacement+body[case_end:]
        return text[:start]+body+text[end:]

    graphics=r'''        case PM4ItOpcode::SetShReg: {
            const auto* set_data = reinterpret_cast<const PM4CmdSetData*>(header);
            static_assert(sizeof(ComputeProgram) % sizeof(u32) == 0);
            auto* queue_words = reinterpret_cast<u32*>(&mapped_queues[GfxQueueId].cs_state);
            const auto* payload = reinterpret_cast<const u32*>(header + 2);
            const u32 words = count - 1;
            const bool valid = PM4::WriteShaderRegisters(
                std::span<u32>{regs.reg_array},
                std::span<u32>{queue_words, sizeof(ComputeProgram) / sizeof(u32)},
                Regs::ShRegWordOffset, 0x200, set_data->reg_offset.Value(),
                std::span<const u32>{payload, words});
            if (!valid) {
                LOG_ERROR(Render, "Ignoring out-of-range graphics SET_SH_REG offset={:#x} words={}",
                          set_data->reg_offset.Value(), words);
            }
            break;
        }'''
    compute=r'''        case PM4ItOpcode::SetShReg: {
            const auto* set_data = reinterpret_cast<const PM4CmdSetData*>(header);
            static_assert(sizeof(ComputeProgram) % sizeof(u32) == 0);
            auto* queue_words = reinterpret_cast<u32*>(&mapped_queues[vqid + 1].cs_state);
            const auto* payload = reinterpret_cast<const u32*>(header + 2);
            const u32 words = header->type3.NumWords() - 1;
            const bool valid = PM4::WriteShaderRegisters(
                std::span<u32>{regs.reg_array},
                std::span<u32>{queue_words, sizeof(ComputeProgram) / sizeof(u32)},
                Regs::ShRegWordOffset, 0x200, set_data->reg_offset.Value(),
                std::span<const u32>{payload, words});
            if (!valid) {
                LOG_ERROR(Render, "Ignoring out-of-range compute SET_SH_REG offset={:#x} words={}",
                          set_data->reg_offset.Value(), words);
            }
            break;
        }'''
    if s.count('PM4::WriteShaderRegisters(')<2:
        s=replace_set_sh_reg(s,'Liverpool::Task Liverpool::ProcessGraphics(',graphics)
        s=replace_set_sh_reg(s,'Liverpool::Task Liverpool::ProcessCompute(',compute)
    if s.count('PM4::WriteShaderRegisters(')<2:
        raise RuntimeError('PM4_REGISTER_BOUNDARY_TRANSFORM_INCOMPLETE')
    return s

TRANS={
'src/video_core/amdgpu/liverpool.cpp':liverpool_pm4_boundaries,
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
'src/video_core/renderer_vulkan/vk_rasterizer.h':rasterizer_h,
'src/video_core/renderer_vulkan/vk_resource_pool.h':resource_pool_h,
'src/video_core/renderer_vulkan/vk_resource_pool.cpp':resource_pool_cpp,
'src/video_core/renderer_vulkan/vk_pipeline_common.h':pipeline_common_h,
'src/video_core/renderer_vulkan/vk_pipeline_common.cpp':pipeline_common_cpp,
'src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp':graphics_pipeline,
'src/video_core/renderer_vulkan/vk_compute_pipeline.cpp':compute_pipeline,
'src/video_core/texture_cache/sampler.h':sampler_h,
'src/video_core/texture_cache/sampler.cpp':sampler_cpp,
'src/video_core/texture_cache/texture_cache.h':texture_cache_h,
'src/video_core/texture_cache/texture_cache.cpp':texture_cache_cpp,
}

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
    if ('const u32 indexed_limit=' not in t or
        'std::min(profile.max_guest_indexed_image_entries,' not in t or
        'profile.max_guest_indexed_sampler_entries' not in t or
        'record_stride,record_offset,indexed_limit' not in t):
        raise RuntimeError('INDEXED_IMAGE_SAMPLER_SHARED_LIMIT_NOT_APPLIED')
    pipeline_text=index_text(idx,'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp')
    if ('(limits.maxPerStageResources-total_reserve)/2u-1u' not in pipeline_text or
        'return std::min(type_real,pair_real);' not in pipeline_text):
        raise RuntimeError('PAIRED_DESCRIPTOR_TOTAL_RESOURCE_LIMIT_MISSING')
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
    for header,token in (('<algorithm>','std::max'),
                         ('<memory>','std::construct_at'),
                         ('<unordered_map>','std::unordered_map'),
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
    args=['git','-c','fetch.recurseSubmodules=false','-c','submodule.recurse=false',
          '-C',SRC,'fetch','--no-tags','--recurse-submodules=no',SOURCE_REMOTE]
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

    required=(GEN_BASE,GEN,STRIDED_SELECTOR_FINAL,GUEST_TSHARP_FINAL,INTEGER_SAMPLER_FINAL,
              PM4_BOUNDARY_SOURCE,RECONSTRUCTION_BASE,AUTOMATIC_COHERENCE_PACKAGE,
              SHARED_SRT_PACKAGE,IMPLICIT_DMA_PACKAGE,*PROVEN_SOURCE_COMMITS)
    for sha in required:
        if exists(sha+'^{commit}'):
            continue
        q=run(['git','-c','fetch.recurseSubmodules=false','-c','submodule.recurse=false',
               '-C',SRC,'fetch','--no-tags','--recurse-submodules=no',SOURCE_REMOTE,sha],
              check=False,timeout=600)
        if q.returncode or not exists(sha+'^{commit}'):
            raise RuntimeError(
                'REQUIRED_COMMIT_FETCH_FAILED='+sha+' '+
                (q.stderr or b'').decode(errors='replace')[-2000:]
            )

    if not exists(UPSTREAM_MAIN+'^{commit}'):
        q=run(['git','-c','fetch.recurseSubmodules=false','-c','submodule.recurse=false',
               '-C',SRC,'fetch','--no-tags','--recurse-submodules=no',UPSTREAM_REMOTE,UPSTREAM_MAIN],
              check=False,timeout=600)
        if q.returncode or not exists(UPSTREAM_MAIN+'^{commit}'):
            raise RuntimeError(
                'UPSTREAM_MAIN_FETCH_FAILED='+UPSTREAM_MAIN+' '+
                (q.stderr or b'').decode(errors='replace')[-2000:]
            )

    if not exists(PR_5492_HEAD+'^{commit}'):
        q=run(['git','-c','fetch.recurseSubmodules=false','-c','submodule.recurse=false',
               '-C',SRC,'fetch','--no-tags','--recurse-submodules=no',PR_5492_REMOTE,PR_5492_HEAD],
              check=False,timeout=600)
        if q.returncode or not exists(PR_5492_HEAD+'^{commit}'):
            raise RuntimeError(
                'PR_5492_FETCH_FAILED='+PR_5492_HEAD+' '+
                (q.stderr or b'').decode(errors='replace')[-2000:]
            )


PROVEN_FIX_CHECKS=(
    ('automatic_demand_coherence_policy',('src/core/emulator_settings.h',),
        ('return GpuReadbacksMode::Precise;',)),
    ('automatic_demand_coherence_backend',('src/video_core/page_manager.cpp',),
        ('needs_read_faults',)),
    ('shared_srt_dependency_schedule',
        ('src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp',
         'src/shader_recompiler/ir/passes/srt_dependency_schedule.h'),
        ('ScheduleSrtRoots(', 'srt_dependency_schedule.h')),
    ('native_masked_wave_minimum',
        ('src/shader_recompiler/backend/spirv/emit_spirv.cpp',
         'src/shader_recompiler/backend/spirv/emit_spirv_warp.cpp',
         'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp'),
        ('OpGroupNonUniformUMin',)),
    ('bounded_atomic_fault_feedback',
        ('src/shader_recompiler/backend/spirv/spirv_emit_context.cpp',
         'src/video_core/host_shaders/fault_buffer_process.comp',
         'src/video_core/buffer_cache/fault_manager.cpp'),
        ('OpAtomicOr', 'atomicCompSwap', 'IgnoreStreamBuffer')),
    ('implicit_dma_dependencies',
        ('src/video_core/buffer_cache/buffer_cache.h',
         'src/video_core/renderer_vulkan/vk_rasterizer.cpp'),
        ('ForEachDmaBuffer', 'PrepareDmaReads')),
)

PROVEN_SOURCE_POSTCHECKS=(
    ('sparse_capable_queue',
        ('src/video_core/renderer_vulkan/vk_instance.cpp',
         'src/video_core/renderer_vulkan/vk_instance.h',
         'src/video_core/buffer_cache/buffer_cache.cpp'),
        ('vk::QueueFlagBits::eSparseBinding',
         'GetSparseQueue()',
         'GetSparseQueue().bindSparse')),
    ('quit_input_capture',
        ('src/core/devtools/layer.cpp',
         'src/core/devtools/layer.h',
         'src/core/devtools/quit_dialog.h',
         'src/imgui/renderer/imgui_core.cpp',
         'src/sdl_window.cpp'),
        ('ProcessQuitEvent(', 'IsQuitInputCaptured()', 'quit_dialog.CapturesGamepad()')),
    ('cpu_identity_address',('src/core/address_space.cpp',),('SetCpuIdGuestAddressRange(',)),
    ('cpu_identity_core',('src/core/cpu_id.cpp',),
        ('GuestCpuid(', 'CurrentGuestCpu(', 'CurrentGuestCpuImpl(',
         'SelectGuestCpu(', 'RestoreHostNativeTsc()', 'GuestCpuCount = GuestCpuSlots')),
    ('cpu_identity_kernel',('src/core/libraries/kernel/process.cpp',),
        ('GuestCpuModeFromPsf(', 'CurrentGuestCpu()')),
    ('libc_mspace_bootstrap',('src/core/libraries/libc_internal/libc_internal_memory.cpp',),
        ('internal_mspace_create(', 'g_mspace_registry')),
    ('sse4a_zero_upper',('src/core/cpu_patches.cpp',),
        ('TryExecuteIllegalInstruction(', 'c.movq(xmm_dst')),
    ('scalar_reciprocal_and_guest_cpuid',('src/core/cpu_patches.cpp',),
        ('GenerateRCPSS(', 'GenerateVRCPSS(', 'GenerateRSQRTSS(',
         'GenerateVRSQRTSS(', 'scalar && vex ? 2 : 1',
         'ZYDIS_MNEMONIC_VRSQRTSS', 'InitializeCpuId();')),
    ('single_guest_affinity_authority',
        ('src/core/cpu_id.cpp', 'src/core/libraries/kernel/threads/pthread.cpp',
         'src/core/libraries/kernel/threads/thread_state.cpp'),
        ('thread->affinity_mask.load(', 'new_thread->affinity_mask.store(',
         'thread->affinity_mask.store(GetGuestCpuMask()', 'SelectGuestCpu(')),
    ('release_mem_semantics',('src/video_core/amdgpu/pm4_cmds.h',),
        ('case DataSelect::None:', 'case InterruptSelect::IrqOnly:')),
    ('legal_64bit_bitfield_insert',
        ('src/shader_recompiler/backend/spirv/emit_spirv_integer.cpp',),
        ('const Id full_width', 'ctx.ConstU32(64u)')),
    ('image_binding_storage_guard',('src/video_core/renderer_vulkan/vk_rasterizer.cpp',),
        ('desc.type == VideoCore::TextureCache::BindingType::Texture',
         'stage.images[image_index].is_written')),
    ('pm4_register_boundaries',('src/video_core/amdgpu/pm4_register_write.h',),
        ('WriteShaderRegisters(',)),
    ('fp64_phi_types',('src/shader_recompiler/backend/spirv/emit_spirv.cpp',),
        ('case IR::Type::F64:', 'case IR::Type::F64x4:')),
    ('srt_arithmetic_full_width',
        ('src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp',),
        ('c.jae(full_width)',)),
    ('permutation_owned_metadata',
        ('src/video_core/renderer_vulkan/vk_pipeline_cache.h',),
        ('std::unique_ptr<Shader::Info> own_info', 'GetInfo(Shader::Info& root_info)')),
    ('scalar_logical_bitcompare',
        ('src/shader_recompiler/frontend/translate/scalar_alu.cpp',),
        ('ir.INotEqual(result, ir.Imm64(u64(0)))', 'ir.IEqual(bit, ir.Imm64')),
    ('buffer_16bit_access',('src/video_core/renderer_vulkan/vk_instance.cpp',),
        ('uniformAndStorageBuffer16BitAccess',)),
    ('compressed_copy_hazard_64bit',
        ('src/video_core/renderer_vulkan/vk_runtime.cpp',),
        ('static u64 BufferImageCopySize', 'row_length + block[0] - 1')),
    ('ubnorm_nz_restore',('src/video_core/amdgpu/pixel_format.h',),
        ('case NumberFormat::UbnormNz:',)),
)

def _revision_texts(rev,paths):
    chunks=[]
    for path in paths:
        spec=rev+':'+path
        if exists(spec):
            chunks.append(show(rev,path).decode(errors='replace'))
    return '\n'.join(chunks)

def verify_proven_fix_base(rev):
    missing=[]
    evidence={}
    for label,paths,markers in PROVEN_FIX_CHECKS:
        text=_revision_texts(rev,paths)
        absent=[marker for marker in markers if marker not in text]
        evidence[label]={
            'paths':list(paths),
            'markers':list(markers),
            'present':not absent,
            'missing':absent,
        }
        if absent:
            missing.append(label+':'+'|'.join(absent))
    if missing:
        raise RuntimeError(
            'CURRENT_HEAD_MISSING_PROVEN_FIXES='+' || '.join(missing)
        )
    return evidence


def verify_proven_source_parent(rev):
    missing=[]
    evidence={}
    for label,paths,markers in PROVEN_SOURCE_POSTCHECKS:
        text=_revision_texts(rev,paths)
        absent=[marker for marker in markers if marker not in text]
        evidence[label]={
            'paths':list(paths),
            'markers':list(markers),
            'present':not absent,
            'missing':absent,
        }
        if absent:
            missing.append(label+':'+'|'.join(absent))
    if missing:
        raise RuntimeError('PROVEN_SOURCE_FIX_MISSING='+' || '.join(missing))
    return evidence

def _cpp_function(text,marker,label):
    start=text.find(marker)
    if start<0:
        raise RuntimeError(label+'_START_MISSING')
    brace=text.find('{',start)
    if brace<0:
        raise RuntimeError(label+'_BRACE_MISSING')
    depth=0
    for i in range(brace,len(text)):
        depth += (text[i]=='{')-(text[i]=='}')
        if depth==0:
            return text[start:i+1]
    raise RuntimeError(label+'_UNBALANCED')

def _apply_cpu_identity_overlay(idx):
    # The reconstruction base already carries an older CPU-ID implementation.
    # The later retained extraction records cpu_id.cpp/.h as newly added files,
    # so a literal add/add replay cannot work. Merge only its later generic
    # scheduler/guest-affinity/native-TSC behavior while preserving the base's
    # PS4/Jaguar CPUID feature masks.
    p='src/core/cpu_id.cpp'
    t=index_text(idx,p)
    later=show(CPU_ID_EXTRACTION_SOURCE,p).decode()

    if '#include <sched.h>' not in t:
        anchor='#include <memory>\n'
        if anchor not in t:
            raise RuntimeError('CPU_ID_SCHED_INCLUDE_ANCHOR_MISSING')
        t=t.replace(anchor,anchor+'#include <sched.h>\n',1)

    if '#include "core/linux_guest_tsc.h"' not in t:
        anchor='#include "core/cpu_id.h"\n'
        if anchor not in t:
            raise RuntimeError('CPU_ID_GENERIC_INCLUDE_ANCHOR_MISSING')
        t=t.replace(
            anchor,
            anchor+'#include "core/linux_guest_tsc.h"\n'
                   '#include "core/guest_cpu_selection.h"\n',
            1)

    if 'constexpr u32 GuestCpuCount = 8;' in t:
        t=t.replace(
            'constexpr u32 GuestCpuCount = 8;',
            'constexpr u32 GuestCpuCount = GuestCpuSlots;',
            1)

    if 'host_to_virtual' not in t:
        anchor='std::array<std::unique_ptr<Xbyak::CodeGenerator>, 2> fault_trampolines;'
        if anchor not in t:
            raise RuntimeError('CPU_ID_HOST_MAP_ANCHOR_MISSING')
        block='''// Map host scheduler CPUs onto the eight virtual PS4 CPU slots.
std::array<u8, CPU_SETSIZE> host_to_virtual{};
std::array<bool, CPU_SETSIZE> host_cpu_known{};'''
        t=t.replace(anchor,anchor+'\n'+block,1)

    later_current=_cpp_function(
        later,'u32 CurrentGuestCpuImpl() {','CPU_ID_LATER_CURRENT')
    # Keep the already-proven atomic affinity authority from the reconstruction
    # base. Duplicating it as guest_affinity_mask would let the CPU instruction
    # emulation and native pthread scheduling disagree.
    if later_current.count('thread->guest_affinity_mask.load(')!=1:
        raise RuntimeError('CPU_ID_LATER_GUEST_AFFINITY_ANCHOR_CHANGED')
    later_current=later_current.replace(
        'thread->guest_affinity_mask.load(', 'thread->affinity_mask.load(', 1)
    if 'u32 CurrentGuestCpuImpl() {' in t:
        current=_cpp_function(
            t,'u32 CurrentGuestCpuImpl() {','CPU_ID_EXISTING_CURRENT')
    else:
        current=_cpp_function(t,'u32 CurrentGuestCpu() {','CPU_ID_BASE_CURRENT')
    t=t.replace(current,later_current,1)

    exec_start=t.find('void PS4_SYSV_ABI ExecuteCpuId(')
    exec_end=t.find('\nenum class FaultInstruction',exec_start)
    if exec_start<0 or exec_end<0:
        raise RuntimeError('CPU_ID_EXECUTE_RANGE_MISSING')
    body=t[exec_start:exec_end]
    body=body.replace(
        'const u32 cpu = CurrentGuestCpu();',
        'const u32 cpu = CurrentGuestCpuImpl();',
        1)
    t=t[:exec_start]+body+t[exec_end:]

    later_init=_cpp_function(later,'void InitializeCpuId() {','CPU_ID_LATER_INIT')
    current_init=_cpp_function(t,'void InitializeCpuId() {','CPU_ID_BASE_INIT')
    t=t.replace(current_init,later_init,1)

    later_fault=_cpp_function(
        later,'void EnableCpuIdFaulting() {','CPU_ID_LATER_FAULTING')
    current_fault=_cpp_function(
        t,'void EnableCpuIdFaulting() {','CPU_ID_BASE_FAULTING')
    t=t.replace(current_fault,later_fault,1)

    if '\nu32 CurrentGuestCpu() {' not in t:
        init_pos=t.find('void InitializeCpuId() {')
        if init_pos<0:
            raise RuntimeError('CPU_ID_PUBLIC_WRAPPER_ANCHOR_MISSING')
        wrapper='''u32 CurrentGuestCpu() {
    return CurrentGuestCpuImpl();
}

'''
        t=t[:init_pos]+wrapper+t[init_pos:]

    for marker in (
        'GuestCpuCount = GuestCpuSlots',
        'SelectGuestCpu(',
        'host_to_virtual',
        'u32 CurrentGuestCpuImpl()',
        'u32 CurrentGuestCpu()',
        'RestoreHostNativeTsc()',
    ):
        if marker not in t:
            raise RuntimeError('CPU_ID_OVERLAY_MISSING='+marker)
    update_index_text(idx,p,t)

    p='src/core/cpu_id.h'
    t=index_text(idx,p)
    if 'u32 CurrentGuestCpu();' not in t:
        anchor='enum class CpuIdInstruction : u32 { Cpuid, Rdtscp, Rdpid };\n'
        if anchor not in t:
            raise RuntimeError('CPU_ID_HEADER_CURRENT_ANCHOR_MISSING')
        t=t.replace(
            anchor,
            anchor+'\n// Shared guest CPU identity for CPU instructions and kernel current-CPU.\n'
                   'u32 CurrentGuestCpu();\n',
            1)
    update_index_text(idx,p,t)
    return ['src/core/cpu_id.cpp','src/core/cpu_id.h']


def _apply_cpu_reconstruction_overlay(idx):
    # The public reconstruction already contains a generic CPUID fault path,
    # complete guest address-range registration, and native pthread affinity.
    # Retain these instead of replaying duplicate hunks or installing a second
    # unsynchronised guest-affinity field.
    checks = {
        'CMakeLists.txt': ('list(APPEND CORE src/core/cpu_id.cpp src/core/cpu_id.h)',),
        'src/core/address_space.cpp': (
            'SetCpuIdGuestAddressRange(reinterpret_cast<VAddr>(system_managed_base)',
            'reinterpret_cast<VAddr>(user_base) + user_size);',
        ),
        'src/core/thread.cpp': ('EnableCpuIdFaulting();',),
        'src/core/libraries/kernel/process.h': ('u64 GetGuestCpuMask();',),
        'src/core/libraries/kernel/threads/pthread.h': (
            'std::atomic<u64> affinity_mask',
            'int SetAffinity(u64 mask);',
        ),
        'src/core/libraries/kernel/threads/pthread.cpp': (
            'new_thread->affinity_mask.store(mask, std::memory_order_release);',
            'affinity_mask.store(mask, std::memory_order_release);',
            'thread->SetAffinity(mask)',
            'thread->affinity_mask.load(std::memory_order_acquire)',
        ),
    }
    results=[]
    for path, markers in checks.items():
        body=index_text(idx,path)
        for marker in markers:
            if marker not in body:
                raise RuntimeError('CPU_RECONSTRUCTION_INVARIANT='+path+':'+marker)
        results.append(path)

    # The retained extraction contains both scalar RCP/RSQRT repairs and
    # duplicate CPUID hooks. Keep the original upstream/Jaguar-compatible file
    # and replace only the reciprocal generator with the proven scalar-aware
    # version, while leaving unrelated SSE4a and platform patches untouched.
    p='src/core/cpu_patches.cpp'
    t=index_text(idx,p)
    newer=show(CPU_ID_EXTRACTION_SOURCE,p).decode()
    older=show(CPU_ID_EXTRACTION_SOURCE+'^',p).decode()
    marker='static void GenerateReciprocalInstruction('
    previous=_cpp_function(older,marker,'CPU_RCP_PREVIOUS')
    current=_cpp_function(t,marker,'CPU_RCP_RECONSTRUCTED')
    updated=_cpp_function(newer,marker,'CPU_RCP_LATER')
    recovered=_cpp_function(show(RECONSTRUCTION_BASE,p).decode(),
                            marker,'CPU_RCP_PUBLIC_RECONSTRUCTION')
    if current!=updated:
        # The recovered public tree predates a generic Zydis absolute-address
        # helper fix. This is an audited difference from the historical parent,
        # not a licence to discard arbitrary source changes.
        if current not in (previous,recovered):
            raise RuntimeError('CPU_RCP_RECONSTRUCTION_DIVERGED_FROM_PROVEN_PARENT')
        t=t.replace(current,updated,1)

    # The old reconstruction calls this helper with an operand only. The
    # scalar-aware generator expects correct RIP/EIP absolute-address handling
    # introduced in the retained parent. Upgrade the helper and all existing
    # generic TCB callers together, rather than mixing incompatible signatures.
    helper='static Xbyak::Address ZydisToXbyakMemoryOperand('
    native_helper=_cpp_function(t,helper,'CPU_ZYDIS_RECONSTRUCTED_HELPER')
    source_helper=_cpp_function(newer,helper,'CPU_ZYDIS_PROVEN_HELPER')
    if native_helper!=source_helper:
        recovered_helper=_cpp_function(show(RECONSTRUCTION_BASE,p).decode(),
                                       helper,'CPU_ZYDIS_PUBLIC_HELPER')
        parent_helper=_cpp_function(older,helper,'CPU_ZYDIS_PARENT_HELPER')
        if native_helper not in (recovered_helper,parent_helper):
            raise RuntimeError('CPU_ZYDIS_HELPER_UNEXPECTED_VERSION')
        t=t.replace(native_helper,source_helper,1)
    old_call='ZydisToXbyakMemoryOperand(operands[1])'
    new_call='ZydisToXbyakMemoryOperand(address, instruction, operands[1])'
    if old_call in t:
        if t.count(old_call)!=3:
            raise RuntimeError('CPU_ZYDIS_TCB_CALL_COUNT_CHANGED')
        t=t.replace(old_call,new_call)
    elif t.count(new_call)<3:
        raise RuntimeError('CPU_ZYDIS_TCB_CALLS_MISSING')
    scalar_names=('GenerateRCPSS','GenerateVRCPSS',
                  'GenerateRSQRTSS','GenerateVRSQRTSS')
    for name in scalar_names:
        src_fn=_cpp_function(newer,'static void '+name+'(',name)
        if 'static void '+name+'(' not in t:
            anchor='static void GenerateEXTRQ('
            if t.count(anchor)!=1:
                raise RuntimeError('CPU_RCP_GENERATOR_INSERT_ANCHOR='+name)
            t=t.replace(anchor,src_fn+'\n\n'+anchor,1)
        elif _cpp_function(t,'static void '+name+'(',name)!=src_fn:
            raise RuntimeError('CPU_RCP_GENERATOR_CONFLICT='+name)

    packed='{ZYDIS_MNEMONIC_VRCPPS, {{FilterIntelCPU, GenerateVRCPPS, true}}},\n'
    scalar_entries=''.join(
        '    {ZYDIS_MNEMONIC_'+m+', {{FilterIntelCPU, '+f+', true}}},\n'
        for m,f in (('RCPSS','GenerateRCPSS'),('VRCPSS','GenerateVRCPSS'),
                    ('RSQRTSS','GenerateRSQRTSS'),('VRSQRTSS','GenerateVRSQRTSS'))
    )
    if 'ZYDIS_MNEMONIC_VRSQRTSS' not in t:
        if t.count(packed)!=1:
            raise RuntimeError('CPU_RCP_DISPATCH_INSERT_ANCHOR')
        t=t.replace(packed,packed+scalar_entries,1)
    elif scalar_entries not in t:
        raise RuntimeError('CPU_RCP_DISPATCH_PARTIAL')

    guarded=('#if defined(__linux__) && defined(ARCH_X86_64)')
    old_include='#if defined(__linux__)\n#include "core/cpu_id.h"\n#endif'
    new_include=guarded+'\n#include "core/cpu_id.h"\n#endif'
    if old_include in t:
        t=t.replace(old_include,new_include,1)
    elif new_include not in t:
        raise RuntimeError('CPU_PATCHES_CPUID_INCLUDE_UNRECOGNISED')
    for old,new in (
        ('#if defined(__linux__)\nstatic bool FilterCpuId(',
         guarded+'\nstatic bool FilterCpuId('),
        ('#if defined(__linux__)\n    {ZYDIS_MNEMONIC_CPUID',
         guarded+'\n    {ZYDIS_MNEMONIC_CPUID'),
        ('static void PatchesInit() {\n#if defined(__linux__)\n',
         'static void PatchesInit() {\n'+guarded+'\n'),
    ):
        if old in t:
            t=t.replace(old,new,1)
        elif new not in t:
            raise RuntimeError('CPU_PATCHES_ARCH_GUARD_MISSING='+old)
    for marker in ('scalar && vex ? 2 : 1','ZYDIS_MNEMONIC_RCPSS',
                   'ZYDIS_MNEMONIC_VRCPSS','ZYDIS_MNEMONIC_RSQRTSS',
                   'ZYDIS_MNEMONIC_VRSQRTSS','InitializeCpuId();'):
        if marker not in t:
            raise RuntimeError('CPU_PATCHES_SCALAR_MISSING='+marker)
    update_index_text(idx,p,t)
    results.append(p)

    # Upstream's CPUID callbacks are already registered. The recovered signal
    # handler must also tolerate a null siginfo_t, without a second callback.
    p='src/core/signals.cpp'
    t=index_text(idx,p)
    old='if ((sig == SIGSEGV || sig == SIGILL) && HandleCpuIdFault(raw_context, info->si_addr)) {'
    new='if (info != nullptr && (sig == SIGSEGV || sig == SIGILL) &&\n        HandleCpuIdFault(raw_context, info->si_addr)) {'
    if old in t:
        t=t.replace(old,new,1)
    elif new not in t:
        raise RuntimeError('CPU_SIGNAL_HANDLER_BASE_MISSING')
    if '#include "core/cpu_id.h"' not in t:
        raise RuntimeError('CPU_SIGNAL_HEADER_MISSING')
    update_index_text(idx,p,t)
    results.append(p)

    # Preserve PSF compatibility and API behaviour while joining CPUID,
    # affinity, and kernel current-CPU onto the same eight guest CPU slots.
    p='src/core/libraries/kernel/process.cpp'
    t=index_text(idx,p)
    newer=show(CPU_ID_EXTRACTION_SOURCE,p).decode()
    anchor='#include "core/emulator_settings.h"\n'
    if '#include "core/guest_cpu_selection.h"' not in t:
        if t.count(anchor)!=1:
            raise RuntimeError('CPU_PROCESS_GUEST_HELPER_INCLUDE_ANCHOR')
        t=t.replace(anchor,anchor+'#include "core/guest_cpu_selection.h"\n',1)
    include='#if defined(__linux__) && defined(ARCH_X86_64)\n#include "core/cpu_id.h"\n#endif\n'
    if include not in t:
        anchor='#include "core/guest_cpu_selection.h"\n'
        t=t.replace(anchor,anchor+include,1)
    for name in ('sceKernelGetCpumode','GetGuestCpuMask','sceKernelGetCurrentCpu'):
        signature=(
            ('u64 GetGuestCpuMask()' if name=='GetGuestCpuMask' else
             's32 PS4_SYSV_ABI '+name+'()')
        )
        source_fn=_cpp_function(newer,signature,name+'_LATER')
        target_fn=_cpp_function(t,signature,name+'_RECONSTRUCTED')
        if source_fn!=target_fn:
            t=t.replace(target_fn,source_fn,1)
    start='s32 PS4_SYSV_ABI sceKernelLoadStartModule('
    alignment='#if defined(__x86_64__) && (defined(__GNUC__) || defined(__clang__))\n__attribute__((force_align_arg_pointer))\n#endif\n'
    if alignment not in t:
        if t.count(start)!=1:
            raise RuntimeError('CPU_PROCESS_ALIGNMENT_ANCHOR')
        t=t.replace(start,alignment+start,1)
    module=_cpp_function(t,start,'CPU_PROCESS_MODULE')
    if 'if (moduleFileName == nullptr)' not in module:
        opening=module.find('{')
        validation=(
            '\n    if (moduleFileName == nullptr) {\n'
            '        return ORBIS_KERNEL_ERROR_EFAULT;\n'
            '    }\n'
            "    if (moduleFileName[0] == '\\0') {\n"
            '        return ORBIS_KERNEL_ERROR_EINVAL;\n'
            '    }\n'
        )
        module_fixed=module[:opening+1]+validation+module[opening+1:]
        t=t.replace(module,module_fixed,1)
    for marker in ('Core::GuestCpuModeFromPsf(', 'Core::GuestCpuMaskFromMode(',
                   'Core::CurrentGuestCpu()', 'force_align_arg_pointer',
                   'if (moduleFileName == nullptr)'):
        if marker not in t:
            raise RuntimeError('CPU_PROCESS_STRUCTURAL_MISSING='+marker)
    update_index_text(idx,p,t)
    results.append(p)

    # The very first guest pthread has no parent from whom to inherit a mask.
    # Initialise the EXISTING atomic field, not a redundant second field.
    p='src/core/libraries/kernel/threads/thread_state.cpp'
    t=index_text(idx,p)
    anchor='#include "core/libraries/kernel/posix_error.h"\n'
    if '#include "core/libraries/kernel/process.h"' not in t:
        if t.count(anchor)!=1:
            raise RuntimeError('CPU_THREAD_STATE_INCLUDE_ANCHOR')
        t=t.replace(anchor,anchor+'#include "core/libraries/kernel/process.h"\n',1)
    init='        std::construct_at(thread);\n'
    if 'thread->affinity_mask.store(GetGuestCpuMask(), std::memory_order_relaxed);' not in t:
        if t.count(init)!=1:
            raise RuntimeError('CPU_THREAD_STATE_INIT_ANCHOR')
        t=t.replace(
            init,init+
            '        // The original guest thread has no parent affinity to inherit.\n'
            '        thread->affinity_mask.store(GetGuestCpuMask(), std::memory_order_relaxed);\n',1)
    update_index_text(idx,p,t)
    results.append(p)
    return results


def _proven_commit_paths(sha):
    paths=git('diff','--name-only',sha+'^',sha).decode().splitlines()
    selected=[
        path for path in paths
        if path=='CMakeLists.txt' or path.startswith('src/')
    ]
    if sha==CPU_ID_EXTRACTION_SOURCE:
        # These paths overlap a complete, earlier public CPU/affinity
        # implementation. Replay their confirmed semantic changes below, not
        # conflicting add/add and duplicate hunks.
        structural={
            'CMakeLists.txt',
            'src/core/address_space.cpp',
            'src/core/cpu_id.cpp',
            'src/core/cpu_id.h',
            'src/core/cpu_patches.cpp',
            'src/core/libraries/kernel/process.cpp',
            'src/core/libraries/kernel/process.h',
            'src/core/libraries/kernel/threads/pthread.cpp',
            'src/core/libraries/kernel/threads/pthread.h',
            'src/core/libraries/kernel/threads/thread_state.cpp',
            'src/core/signals.cpp',
            'src/core/thread.cpp',
        }
        selected=[path for path in selected if path not in structural]
    return selected

def _strip_historical_cache_version_hunks(patch):
    # Historical retained-fix commits may combine real serialization logic
    # changes with branch-local ShaderBinaryVersion/ShaderMetaVersion bumps.
    # Preserve every semantic hunk and drop only hunks that touch those
    # numeric cache epochs. The final composer performs one current cache bump
    # relative to the actual merged parent.
    if not patch or b'@@' not in patch:
        return patch,False
    text=patch.decode(errors='replace')
    first=text.find('@@')
    if first<0:
        return patch,False
    header=text[:first]
    hunks=re.split(r'(?=^@@ )',text[first:],flags=re.M)
    kept=[]
    stripped=False
    for hunk in hunks:
        if not hunk:
            continue
        if 'ShaderBinaryVersion' in hunk or 'ShaderMetaVersion' in hunk:
            stripped=True
            continue
        kept.append(hunk)
    if not stripped:
        return patch,False
    if not kept:
        return b'',True
    return (header+''.join(kept)).encode(),True

def _apply_proven_file(idx,sha,path):
    e=env({'GIT_INDEX_FILE':str(idx)})
    patch=git('diff','--binary',sha+'^',sha,'--',path)
    stripped_cache_epoch=False
    if path=='src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp':
        patch,stripped_cache_epoch=_strip_historical_cache_version_hunks(patch)
    if not patch:
        return ('SKIPPED_STALE_CACHE_VERSION_HUNK'
                if stripped_cache_epoch else 'EMPTY')
    before=git('write-tree',e=e).decode().strip()
    q=subprocess.run(
        ['git','-c','core.hooksPath=/dev/null','-c','gc.auto=0',
         '-C',str(SRC),'apply','--3way','--cached','--whitespace=nowarn','-'],
        env=e,input=patch,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if q.returncode==0:
        return ('APPLIED_WITH_CACHE_VERSION_HUNK_STRIPPED'
                if stripped_cache_epoch else 'APPLIED')
    git('read-tree',before,e=e)
    reverse=subprocess.run(
        ['git','-c','core.hooksPath=/dev/null','-c','gc.auto=0',
         '-C',str(SRC),'apply','--reverse','--check','--cached',
         '--whitespace=nowarn','-'],
        env=e,input=patch,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if reverse.returncode==0:
        return ('ALREADY_PRESENT_WITH_CACHE_VERSION_HUNK_STRIPPED'
                if stripped_cache_epoch else 'ALREADY_PRESENT')
    raise RuntimeError(
        'PROVEN_SOURCE_APPLY_FAILED commit='+sha+' path='+path+' details='+
        (q.stderr or b'').decode(errors='replace')[-2500:]
    )


def _index_put(idx,path,data,mode='100644'):
    e=env({'GIT_INDEX_FILE':str(idx)})
    oid=git('hash-object','-w','--stdin',input=data).decode().strip()
    git('update-index','--add','--cacheinfo',f'{mode},{oid},{path}',e=e)
    return oid

def _package_module(commit,path,name):
    namespace={'__name__':name,'__file__':path}
    raw=show(commit,path)
    exec(compile(raw,path,'exec'),namespace)
    return namespace

def _apply_automatic_coherence(idx):
    script='tools/packages/automatic-coherence-20261022/patch_coherence.review.py'
    module=_package_module(AUTOMATIC_COHERENCE_PACKAGE,script,'_automatic_coherence_package')
    transform=module.get('transform_text')
    paths=module.get('EXPECTED',{})
    if not callable(transform) or not isinstance(paths,dict) or len(paths)!=3:
        raise RuntimeError('AUTOMATIC_COHERENCE_PACKAGE_API_CHANGED')
    changed=[]
    for path in paths:
        text=index_text(idx,path)
        try:
            new=transform(path,text)
        except Exception as error:
            raise RuntimeError('AUTOMATIC_COHERENCE_TRANSFORM_FAILED path='+path+' '+str(error))
        update_index_text(idx,path,new)
        changed.append(path)
    return changed

def _apply_shared_srt(idx):
    prefix='tools/packages/shared-srt-dependencies-20261022'
    script=prefix+'/patch_srt_dependencies.py'
    module=_package_module(SHARED_SRT_PACKAGE,script,'_shared_srt_package')
    transform_flat=module.get('transform_flat')
    if not callable(transform_flat):
        raise RuntimeError('SHARED_SRT_PACKAGE_API_CHANGED')
    flat='src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp'
    try:
        text=transform_flat(index_text(idx,flat))
    except Exception as error:
        raise RuntimeError('SHARED_SRT_TRANSFORM_FAILED='+str(error))
    update_index_text(idx,flat,text)
    for path,name in (
        ('src/shader_recompiler/ir/passes/srt_dependency_schedule.h',
         'srt_dependency_schedule.h'),
        ('src/shader_recompiler/ir/passes/srt_dependency_codegen.inc',
         'srt_dependency_codegen.inc'),
    ):
        _index_put(idx,path,show(SHARED_SRT_PACKAGE,prefix+'/'+name))
    return [
        flat,
        'src/shader_recompiler/ir/passes/srt_dependency_schedule.h',
        'src/shader_recompiler/ir/passes/srt_dependency_codegen.inc',
    ]

def _apply_implicit_dma_dependencies(idx):
    script='scripts/implicit_memory_dependencies_20261022.py'
    module=_package_module(IMPLICIT_DMA_PACKAGE,script,'_implicit_dma_package')
    transform=module.get('transform')
    expected=module.get('EXPECTED')
    if not callable(transform) or not isinstance(expected,dict) or len(expected)!=4:
        raise RuntimeError('IMPLICIT_DMA_PACKAGE_API_CHANGED')
    changed=[]
    for path in expected:
        text=index_text(idx,path)
        # The package transform deliberately performs only source-anchor
        # checks; its historical blob checks lived in the host workflow.
        try:
            new=transform(path,text)
        except Exception as error:
            raise RuntimeError('IMPLICIT_DMA_TRANSFORM_FAILED path='+path+' '+str(error))
        update_index_text(idx,path,new)
        changed.append(path)
    return changed

def _apply_final_native_umin_sirit(idx):
    # The sealed native-minimum package requires the immediate upstream Sirit
    # child that adds OpGroupNonUniformUMin. The source lowering itself comes
    # from the published generic UMin source commit above.
    e=env({'GIT_INDEX_FILE':str(idx)})
    git('update-index','--add','--cacheinfo',
        f'160000,{NATIVE_UMIN_SIRIT},externals/sirit',e=e)
    text=index_text(idx,'src/shader_recompiler/backend/spirv/emit_spirv_warp.cpp')
    if 'OpGroupNonUniformUMin' not in text:
        raise RuntimeError('NATIVE_UMIN_SOURCE_LOWERING_MISSING')
    return ['externals/sirit']

def _apply_atomic_fault_feedback(idx):
    # 1) Concurrent shaders set fault bits atomically. Device scope + zero
    # memory semantics is the existing relaxed SPIR-V atomic convention.
    p='src/shader_recompiler/backend/spirv/spirv_emit_context.cpp'
    t=index_text(idx,p)
    old='''    const auto fault_value{OpLoad(U32[1], fault_ptr)};
    const auto fault_value_masked{OpBitwiseOr(U32[1], fault_value, page_mask)};
    OpStore(fault_ptr, fault_value_masked);'''
    new='''    const auto fault_scope{ConstU32(static_cast<u32>(spv::Scope::Device))};
    const auto fault_semantics{u32_zero_value};
    (void)OpAtomicOr(U32[1], fault_ptr, fault_scope, fault_semantics, page_mask);'''
    if old in t:
        t=t.replace(old,new,1)
    if ('OpAtomicOr(U32[1], fault_ptr, fault_scope, fault_semantics, page_mask)' not in t and
        'OpAtomicOr(U32[1], fault_ptr, ConstU32(u32(spv::Scope::Device))' not in t):
        raise RuntimeError('ATOMIC_FAULT_RECORDING_NOT_APPLIED')
    update_index_text(idx,p,t)

    # 2) Reserve only real output slots. Overflow bits remain set for a later
    # scan; only successfully queued bits are cleared.
    p='src/video_core/host_shaders/fault_buffer_process.comp'
    t=index_text(idx,p)
    start=t.find('void main() {')
    if start<0:
        raise RuntimeError('FAULT_QUEUE_MAIN_NOT_FOUND')
    brace=t.find('{',start); depth=0; end=None
    for i in range(brace,len(t)):
        depth += (t[i]=='{')-(t[i]=='}')
        if depth==0:
            end=i+1; break
    if end is None:
        raise RuntimeError('FAULT_QUEUE_MAIN_UNBALANCED')
    body=r'''uint reserve_fault_slot() {
    for (;;) {
        const uint count = atomicOr(download_buffer32[0], 0u);
        if (count >= MAX_PAGE_FAULTS - 1u) {
            return 0u;
        }
        const uint previous = atomicCompSwap(download_buffer32[0], count, count + 1u);
        if (previous == count) {
            return count + 1u;
        }
    }
}

void main() {
    const uint id = gl_GlobalInvocationID.x;
    uint word = atomicOr(fault_buffer[id], 0u);
    const uint base_bit = id * 32u;
    uint consumed = 0u;
    while (word != 0u) {
        const uint bit = findLSB(word);
        const uint mask = 1u << bit;
        const uint store_index = reserve_fault_slot();
        if (store_index == 0u) {
            break;
        }
        const uint page = base_bit + bit;
        download_buffer[store_index] = uint64_t(page) << CACHING_PAGEBITS;
        consumed |= mask;
        word &= ~mask;
    }
    if (consumed != 0u) {
        atomicAnd(fault_buffer[id], ~consumed);
    }
}'''
    # Replace any previous helper+main tail starting at main only; helper is
    # intentionally inserted directly before main.
    helper_start=t.rfind('uint reserve_fault_slot()',0,start)
    replace_start=helper_start if helper_start>=0 else start
    t=t[:replace_start]+body+t[end:]
    for marker in ('atomicCompSwap','atomicAnd(fault_buffer[id]','MAX_PAGE_FAULTS - 1u'):
        if marker not in t:
            raise RuntimeError('BOUNDED_FAULT_QUEUE_NOT_APPLIED='+marker)
    update_index_text(idx,p,t)

    # 3) Make CPU-cleared/readback memory explicit, validate returned count,
    # and force real sparse backing for recovered pages.
    p='src/video_core/buffer_cache/fault_manager.cpp'
    t=index_text(idx,p)
    if 'download_buffer.Flush(offset, PageFaultAreaSize);' not in t:
        anchor='''    std::memset(mapped, 0, PageFaultAreaSize);
'''
        if anchor not in t:
            raise RuntimeError('FAULT_DOWNLOAD_CLEAR_ANCHOR')
        t=t.replace(anchor,anchor+'    download_buffer.Flush(offset, PageFaultAreaSize);\n',1)

    # Parser reads and writes the bitmap, not read-only.
    t=t.replace(
        '.dstAccessMask = vk::AccessFlagBits2::eShaderRead,\n        .buffer = fault_buffer.Handle(),',
        '.dstAccessMask = vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite,\n'
        '        .buffer = fault_buffer.Handle(),',
        1)

    # Add an explicit compute-write -> host-read barrier for the download area.
    old_post='''    const vk::BufferMemoryBarrier2 post_barrier = {
        .srcStageMask = vk::PipelineStageFlagBits2::eComputeShader,
        .srcAccessMask = vk::AccessFlagBits2::eShaderWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .dstAccessMask = vk::AccessFlagBits2::eShaderWrite,
        .buffer = fault_buffer.Handle(),
        .offset = 0,
        .size = fault_buffer_size,
    };'''
    new_post=old_post+'''
    const vk::BufferMemoryBarrier2 download_barrier = {
        .srcStageMask = vk::PipelineStageFlagBits2::eComputeShader,
        .srcAccessMask = vk::AccessFlagBits2::eShaderWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eHost,
        .dstAccessMask = vk::AccessFlagBits2::eHostRead,
        .buffer = download_buffer.Handle(),
        .offset = offset,
        .size = PageFaultAreaSize,
    };'''
    if ('download_barrier' not in t and
        not ('post_barriers.data()' in t and 'vk::AccessFlagBits2::eHostRead' in t)):
        if old_post not in t:
            raise RuntimeError('FAULT_POST_BARRIER_ANCHOR')
        t=t.replace(old_post,new_post,1)
    old_dep='''    cmdbuf.pipelineBarrier2(vk::DependencyInfo{
        .dependencyFlags = vk::DependencyFlagBits::eByRegion,
        .bufferMemoryBarrierCount = 1,
        .pBufferMemoryBarriers = &post_barrier,
    });'''
    new_dep='''    const std::array post_barriers{post_barrier, download_barrier};
    cmdbuf.pipelineBarrier2(vk::DependencyInfo{
        .dependencyFlags = vk::DependencyFlagBits::eByRegion,
        .bufferMemoryBarrierCount = static_cast<u32>(post_barriers.size()),
        .pBufferMemoryBarriers = post_barriers.data(),
    });'''
    if old_dep in t:
        t=t.replace(old_dep,new_dep,1)
    if 'post_barriers.data()' not in t or 'vk::AccessFlagBits2::eHostRead' not in t:
        raise RuntimeError('FAULT_HOST_READ_BARRIER_NOT_APPLIED')

    if 'download_buffer.Invalidate(offset, PageFaultAreaSize);' not in t:
        anchor='''    scheduler.DeferOperation([this, mapped, area = current_area] {
        fault_ranges.Clear();'''
        if anchor not in t:
            raise RuntimeError('FAULT_DEFER_ANCHOR')
        t=t.replace(
            anchor,
            '''    scheduler.DeferOperation([this, mapped, offset, area = current_area] {
        download_buffer.Invalidate(offset, PageFaultAreaSize);
        fault_ranges.Clear();''',
            1)

    old_count='''        const u32 fault_count = fault_buf[0];
        for (u32 i = 1; i <= fault_count; ++i) {'''
    clamped_count='''        const u32 fault_count = std::min<u64>(fault_buf[0], MaxPageFaults - 1);
        for (u32 i = 1; i <= fault_count; ++i) {'''
    new_count='''        const u32 fault_count = fault_buf[0];
        if (fault_count >= MaxPageFaults) {
            LOG_ERROR(Render_Vulkan, "Invalid fault queue count {}", fault_count);
            fault_areas[area] = 0;
            return;
        }
        for (u32 i = 1; i <= fault_count; ++i) {'''
    if old_count in t:
        t=t.replace(old_count,new_count,1)
    elif clamped_count in t:
        t=t.replace(clamped_count,new_count,1)
    if 'Invalid fault queue count' not in t:
        raise RuntimeError('FAULT_COUNT_VALIDATION_NOT_APPLIED')

    # Reconstruct the fault-recovery call against both verified generations:
    # the older public tree used ObtainBuffer(addr, size, false), while the
    # retained DMA/stream-buffer API uses ObtainBuffer(addr, size) with flags.
    # Only rewrite the single recovery callback; do not change normal reads.
    old_obtains=(
        '            (void)buffer_cache.ObtainBuffer(start, end - start);',
        '            (void)buffer_cache.ObtainBuffer(start, end - start, false);',
    )
    new_obtain='''            (void)buffer_cache.ObtainBuffer(
                start, end - start, ObtainBufferFlags::IgnoreStreamBuffer);'''
    if new_obtain not in t:
        candidates=[form for form in old_obtains if t.count(form)==1]
        if len(candidates)!=1:
            raise RuntimeError(
                'FAULT_REAL_BACKING_CALL_VERSION_UNRECOGNISED'
                +' forms='+str([t.count(form) for form in old_obtains]))
        t=t.replace(candidates[0],new_obtain,1)
    if t.count(new_obtain)!=1:
        raise RuntimeError('FAULT_REAL_BACKING_NOT_APPLIED')
    flags_api=index_text(idx,'src/video_core/buffer_cache/buffer_cache.h')
    if ('enum class ObtainBufferFlags' not in flags_api or
        'IgnoreStreamBuffer = ' not in flags_api):
        raise RuntimeError('FAULT_REAL_BACKING_FLAGS_API_MISSING')
    update_index_text(idx,p,t)

    # Initialize the device-local fault bitmap through the existing Runtime
    # tracking path immediately after FaultManager creation.
    p='src/video_core/buffer_cache/buffer_cache.cpp'
    t=index_text(idx,p)
    marker='fault_manager->GetFaultBuffer()->SizeBytes()'
    if marker not in t:
        anchor='''    fault_manager = std::make_unique<FaultManager>(instance, scheduler, *this, block_shift,
                                                   blocks_per_arena_page * NUM_ARENA_PAGES);
'''
        if anchor not in t:
            raise RuntimeError('FAULT_BITMAP_INIT_ANCHOR')
        t=t.replace(
            anchor,
            anchor+'''    auto* fault_buffer = fault_manager->GetFaultBuffer();
    runtime.FillBuffer(fault_buffer, 0u, fault_buffer->SizeBytes(), 0u);
    const vk::BufferMemoryBarrier2 fault_init_barrier = {
        .srcStageMask = vk::PipelineStageFlagBits2::eClear,
        .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .dstAccessMask = vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite,
        .buffer = fault_buffer->Handle(),
        .offset = 0,
        .size = fault_buffer->SizeBytes(),
    };
    scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
        .bufferMemoryBarrierCount = 1,
        .pBufferMemoryBarriers = &fault_init_barrier,
    });
''',1)
    if ('runtime.FillBuffer(fault_buffer' not in t and
        'runtime.FillBuffer(fault_manager->GetFaultBuffer()' not in t):
        raise RuntimeError('FAULT_BITMAP_INIT_NOT_APPLIED')
    update_index_text(idx,p,t)
    return [
        'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp',
        'src/video_core/host_shaders/fault_buffer_process.comp',
        'src/video_core/buffer_cache/fault_manager.cpp',
        'src/video_core/buffer_cache/buffer_cache.cpp',
    ]

def complete_package_source_parent(run_dir,base):
    idx=run_dir/'package-source.index'
    e=env({'GIT_INDEX_FILE':str(idx)})
    git('read-tree',base,e=e)
    changed=[]
    changed += _apply_final_native_umin_sirit(idx)
    changed += _apply_implicit_dma_dependencies(idx)
    changed += _apply_atomic_fault_feedback(idx)
    changed += _apply_shared_srt(idx)
    changed += _apply_automatic_coherence(idx)
    tree=git('write-tree',e=e).decode().strip()
    revision=commit_tree(
        tree,base,
        b'Complete retained package-only generic GoW/GoT fixes before upstream merge\n'
    )
    evidence=verify_proven_fix_base(revision)
    return revision,tree,sorted(set(changed)),evidence

def complete_proven_source_parent(run_dir,base):
    idx=run_dir/'proven-source.index'
    e=env({'GIT_INDEX_FILE':str(idx)})
    git('read-tree',base,e=e)
    results=[]
    for sha in PROVEN_SOURCE_COMMITS:
        for path in _proven_commit_paths(sha):
            results.append({
                'commit':sha,
                'path':path,
                'result':_apply_proven_file(idx,sha,path),
            })
        if sha==CPU_ID_EXTRACTION_SOURCE:
            for path in _apply_cpu_reconstruction_overlay(idx):
                results.append({
                    'commit':sha,
                    'path':path,
                    'result':'STRUCTURAL_CPU_RECONSTRUCTION_OVERLAY',
                })
            for path in _apply_cpu_identity_overlay(idx):
                results.append({
                    'commit':sha,
                    'path':path,
                    'result':'STRUCTURAL_CPU_ID_OVERLAY',
                })
    tree=git('write-tree',e=e).decode().strip()
    base_tree=git('rev-parse',base+'^{tree}').decode().strip()
    if tree==base_tree:
        revision=base
    else:
        revision=commit_tree(
            tree,base,
            b'Complete retained generic GoW/GoT source fixes before upstream merge\n'
        )
    evidence=verify_proven_source_parent(revision)
    return revision,tree,results,evidence

def recovered_parent_ref(run_dir):
    global PARENT_REF, PARENT_TREE_ACTUAL, PARENT_MODE
    if exists(PARENT+'^{commit}') and \
       git('rev-parse',PARENT+'^{tree}').decode().strip()==PARENT_TREE:
        PARENT_REF=PARENT
        PARENT_TREE_ACTUAL=PARENT_TREE
        PARENT_MODE='exact-verified-commit'
        return PARENT_REF
    if exists(PARENT_TREE+'^{tree}'):
        base=RECONSTRUCTION_BASE
        if not exists(base+'^{commit}'):
            raise RuntimeError('PUBLIC_RECONSTRUCTION_BASE_MISSING='+base)
        PARENT_REF=commit_tree(
            PARENT_TREE,base,
            b'Wrapped exact verified graphics parent tree for local composition only\n')
        PARENT_TREE_ACTUAL=PARENT_TREE
        PARENT_MODE='exact-verified-tree-wrap'
        return PARENT_REF

    # Reconstruct from a fixed public source commit instead of depending on
    # the user's checkout or pruned local-only objects.
    if not exists(RECONSTRUCTION_BASE+'^{commit}'):
        raise RuntimeError('PUBLIC_RECONSTRUCTION_BASE_MISSING='+RECONSTRUCTION_BASE)
    PARENT_REF=RECONSTRUCTION_BASE
    PARENT_TREE_ACTUAL=git('rev-parse',PARENT_REF+'^{tree}').decode().strip()
    PARENT_MODE='public-proven-fix-reconstruction-base'
    return PARENT_REF

def merge_verified_parent_with_upstream(parent):
    if not exists(parent+'^{commit}'):
        raise RuntimeError('VERIFIED_PARENT_MISSING_BEFORE_UPSTREAM_MERGE')
    if not exists(UPSTREAM_MAIN+'^{commit}'):
        raise RuntimeError('UPSTREAM_MAIN_MISSING_BEFORE_MERGE')
    if not exists(PR_5492_HEAD+'^{commit}'):
        raise RuntimeError('PR_5492_HEAD_MISSING_BEFORE_MERGE')

    q=run(['git','-C',SRC,'merge-tree','--write-tree',parent,UPSTREAM_MAIN],
          check=False,timeout=120)
    stdout=(q.stdout or b'').decode(errors='replace').strip()
    stderr=(q.stderr or b'').decode(errors='replace').strip()
    if q.returncode:
        raise RuntimeError(
            'UPSTREAM_MERGE_CONFLICT parent='+parent+
            ' upstream='+UPSTREAM_MAIN+' details='+(stdout+' '+stderr)[-6000:]
        )
    upstream_tree=stdout.splitlines()[0].strip() if stdout else ''
    if not re.fullmatch(r'[0-9a-f]{40}',upstream_tree) or not exists(upstream_tree+'^{tree}'):
        raise RuntimeError('UPSTREAM_MERGE_TREE_INVALID='+stdout[-2000:])

    upstream_msg=(b'Merge pinned upstream main into verified generic graphics parent\n\n'
                  b'No conflict resolution or game-specific preference was permitted.\n')
    upstream_merged=commit_tree(upstream_tree,parent,upstream_msg,(UPSTREAM_MAIN,))
    if git('rev-parse',upstream_merged+'^{tree}').decode().strip()!=upstream_tree:
        raise RuntimeError('UPSTREAM_MERGE_COMMIT_TREE_CHANGED')

    verified_sirit=git('rev-parse',parent+':externals/sirit').decode().strip()
    merged_sirit=git('rev-parse',upstream_merged+':externals/sirit').decode().strip()
    if merged_sirit!=verified_sirit:
        raise RuntimeError(
            'UPSTREAM_MERGE_CHANGED_VERIFIED_SIRIT expected='+
            verified_sirit+' got='+merged_sirit
        )

    # PR #5492 is still open, so merge its pinned head explicitly on top of
    # current upstream. Refuse any conflict instead of silently preferring a side.
    q=run(['git','-C',SRC,'merge-tree','--write-tree',upstream_merged,PR_5492_HEAD],
          check=False,timeout=120)
    stdout=(q.stdout or b'').decode(errors='replace').strip()
    stderr=(q.stderr or b'').decode(errors='replace').strip()
    if q.returncode:
        raise RuntimeError(
            'PR_5492_MERGE_CONFLICT parent='+upstream_merged+
            ' pr_head='+PR_5492_HEAD+' details='+(stdout+' '+stderr)[-6000:]
        )
    final_tree=stdout.splitlines()[0].strip() if stdout else ''
    if not re.fullmatch(r'[0-9a-f]{40}',final_tree) or not exists(final_tree+'^{tree}'):
        raise RuntimeError('PR_5492_MERGE_TREE_INVALID='+stdout[-2000:])

    pr_msg=(b'Merge shadPS4 PR #5492 Audio3D feeder-thread change\n\n'
            b'Pinned open PR head; no conflict resolution was permitted.\n')
    final_parent=commit_tree(final_tree,upstream_merged,pr_msg,(PR_5492_HEAD,))
    if git('rev-parse',final_parent+'^{tree}').decode().strip()!=final_tree:
        raise RuntimeError('PR_5492_MERGE_COMMIT_TREE_CHANGED')

    return final_parent,final_tree,upstream_merged,upstream_tree

def verify_prev(run_dir):
    stage('fetch_required_refs')
    ensure_required_refs()
    if not SRC.is_dir() or not (SRC/'.git').exists():
        raise RuntimeError('GOT_SOURCE_REPOSITORY_MISSING='+str(SRC))
    validate_source_status(BASE_STATUS)

    stage('select_public_reconstruction_base')
    retained_base=recovered_parent_ref(run_dir)

    stage('complete_public_proven_source_fixes')
    public_parent,public_tree,source_replay,source_evidence = \
        complete_proven_source_parent(run_dir,retained_base)

    stage('complete_package_only_proven_fixes')
    verified_parent,verified_tree,package_paths,package_evidence = \
        complete_package_source_parent(run_dir,public_parent)

    # Verify both groups before latest upstream is merged.
    source_evidence=verify_proven_source_parent(verified_parent)
    package_evidence=verify_proven_fix_base(verified_parent)

    stage('merge_latest_upstream_and_pr_5492')
    parent,parent_tree,upstream_parent,upstream_parent_tree = \
        merge_verified_parent_with_upstream(verified_parent)
    return {
        'previous_parent':parent,
        'previous_parent_tree':parent_tree,
        'verified_parent':verified_parent,
        'verified_parent_original':PARENT,
        'verified_parent_tree':verified_tree,
        'verified_parent_mode':PARENT_MODE,
        'proven_fix_evidence':package_evidence,
        'proven_source_evidence':source_evidence,
        'proven_source_replay':source_replay,
        'package_source_paths':package_paths,
        'public_source_parent':public_parent,
        'public_source_parent_tree':public_tree,
        'parent_reconstructed':verified_parent!=PARENT,
        'upstream_main':UPSTREAM_MAIN,
        'upstream_parent':upstream_parent,
        'upstream_parent_tree':upstream_parent_tree,
        'pr_5492_head':PR_5492_HEAD,
        'upstream_merge_conflicts':False,
        'pr_5492_merge_conflicts':False,
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
        'git','-c','fetch.recurseSubmodules=false','-c','submodule.recurse=false',
        '-C',work,'fetch','--no-tags','--recurse-submodules=no',str(SRC),
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
    stage('compose_generic_gpu_fix_stack')
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
    # The merged verified/upstream parent is authoritative. Existing generic
    # source files are transformed structurally; the two helper headers below
    # are seeded from pinned final blobs. No generic-base 3-way replay remains.
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
        if not fn:
            raise RuntimeError('GENERIC_BASE_TRANSFORM_MISSING='+p)
        content=fn(show(parent,p).decode()).encode()
        oid=git('hash-object','-w','--stdin',input=content).decode().strip()
        git('update-index','--add','--cacheinfo',f'100644,{oid},{p}',e=e)

    # Deterministic fixes outside the original 464e32 file set. Only files
    # explicitly registered in TRANS are changed here; everything else stays
    # at the merged verified/upstream parent until its audited replay commit.
    for p in EXTRA_FILES:
        if p=='src/video_core/amdgpu/pm4_register_write.h':
            continue
        fn=TRANS.get(p)
        if not fn:
            continue
        content=fn(index_text(idx,p)).encode()
        oid=git('hash-object','-w','--stdin',input=content).decode().strip()
        git('update-index','--add','--cacheinfo',f'100644,{oid},{p}',e=e)

    pm4_path='src/video_core/amdgpu/pm4_register_write.h'
    pm4_header=show(PM4_BOUNDARY_SOURCE,pm4_path)
    pm4_text=pm4_header.decode()
    if 'WriteShaderRegisters(' not in pm4_text:
        raise RuntimeError('PM4_REGISTER_HELPER_SOURCE_INVALID')
    oid=git('hash-object','-w','--stdin',input=pm4_header).decode().strip()
    git('update-index','--add','--cacheinfo',f'100644,{oid},{pm4_path}',e=e)
    if 'PM4::WriteShaderRegisters(' not in index_text(idx,'src/video_core/amdgpu/liverpool.cpp'):
        raise RuntimeError('PM4_REGISTER_BOUNDARY_TRANSFORM_MISSING')

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

    for p,marker in (
        ('src/video_core/texture_cache/sampler.h','bool is_integer'),
        ('src/video_core/texture_cache/sampler.cpp','ImageSamplerFilter('),
        ('src/video_core/texture_cache/texture_cache.h','bool is_depth, bool is_integer'),
        ('src/video_core/texture_cache/texture_cache.cpp','is_depth, is_integer'),
    ):
        if marker not in index_text(idx,p):
            raise RuntimeError(
                'INTEGER_SAMPLER_EXTRA_TRANSFORM_MISSING path='+p+' marker='+marker
            )

    sampler_policy_path='src/video_core/texture_cache/sampler_filter_policy.h'
    sampler_policy=show(INTEGER_SAMPLER_FINAL,sampler_policy_path)
    sampler_policy_text=sampler_policy.decode()
    for marker in ('ImageSamplerFilter','ImageSamplerMipmapMode','SamplerAnisotropyAllowed'):
        if marker not in sampler_policy_text:
            raise RuntimeError('INTEGER_SAMPLER_POLICY_MISSING='+marker)
    oid=git('hash-object','-w','--stdin',input=sampler_policy).decode().strip()
    git('update-index','--add','--cacheinfo',f'100644,{oid},{sampler_policy_path}',e=e)

    finish_generic_large(idx)

    # The composed descriptor/shader ABI invalidates binary + metadata caches
    # exactly once relative to the merged verified/upstream parent.
    cache_path='src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
    parent_cache=show(parent,cache_path).decode()
    final_cache=index_text(idx,cache_path)
    for key in ('ShaderBinaryVersion','ShaderMetaVersion'):
        rx=rf'static constexpr u32 {key}\s*=\s*(0x[0-9A-Fa-f]+|\d+)u?;'
        pm=re.search(rx,parent_cache)
        fm=re.search(rx,final_cache)
        if not pm or not fm:
            raise RuntimeError('CACHE_VERSION_PARSE_FAILED='+key)
        if int(fm.group(1),0)!=int(pm.group(1),0)+1:
            raise RuntimeError(
                'CACHE_VERSION_NOT_SINGLE_BUMP='+key+
                ' parent='+pm.group(1)+' final='+fm.group(1)
            )

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
            'SamplerAnisotropyAllowed',
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

    # Cross-layer descriptor binding contract. The same SamplerResource count
    # must drive SPIR-V bindings, Vulkan set layouts and runtime writes.
    sampler_contract={
        'src/shader_recompiler/resource.h':(
            'return is_guest_indexed ? indexed_guest_entries + 1u : 1u;',
            'indexed_image_offset_bytes',
            'indexed_sampler_offset_bytes',
        ),
        'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp':(
            'const u32 count = samp_desc.NumBindings(info);',
            'binding.unified += count;',
            'sampler_array_sizes.push_back(count)',
            'spv::Capability::SampledImageArrayNonUniformIndexing',
        ),
        'src/shader_recompiler/backend/spirv/emit_spirv_image.cpp':(
            'LoadIndexedGuestSampler',
            'sampler_count == expected_count',
        ),
        'src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp':(
            'const u32 count = sampler.NumBindings(*stage);',
            '.descriptorCount = count',
            'binding += count;',
        ),
        'src/video_core/renderer_vulkan/vk_compute_pipeline.cpp':(
            'const u32 count = sampler.NumBindings(*info);',
            '.descriptorCount = count',
            'binding += count;',
        ),
        'src/video_core/renderer_vulkan/vk_rasterizer.cpp':(
            'const u32 count = sampler.NumBindings(stage);',
            'AmdGpu::IsInteger(raw_image.GetNumberFmt())',
            'write.descriptorCount = count;',
            'binding.unified += count;',
            'sampler.indexed_image_offset_bytes',
            'sampler.indexed_sampler_offset_bytes',
            'AmdGpu::IsInteger(raw_image.GetNumberFmt())',
            'vk::Sampler vk_sampler{};',
            'sampler_valid = false;',
        ),
    }
    for path,markers in sampler_contract.items():
        text=index_text(idx,path)
        for marker in markers:
            if marker not in text:
                raise RuntimeError(
                    'INDEXED_SAMPLER_BINDING_CONTRACT_MISSING path='+path+
                    ' marker='+marker
                )
    spirv_context_text=index_text(
        idx,'src/shader_recompiler/backend/spirv/spirv_emit_context.cpp')
    if 'spv::Capability::SamplerArrayNonUniformIndexing' in spirv_context_text:
        raise RuntimeError('INVALID_SPIRV_SAMPLER_ARRAY_CAPABILITY_RETAINED')
    raster_text=index_text(idx,'src/video_core/renderer_vulkan/vk_rasterizer.cpp')
    if 'fmt==AmdGpu::DataFormat::FormatInvalid || AmdGpu::IsInteger(nfmt)' in raster_text:
        raise RuntimeError('INDEXED_INTEGER_IMAGE_REJECTION_RETAINED')

    def require_order(text, markers, label):
        positions=[text.find(marker) for marker in markers]
        if any(pos<0 for pos in positions) or positions!=sorted(positions):
            raise RuntimeError(
                label+'_ORDER_INVALID positions='+','.join(map(str,positions))
            )

    profile_text=index_text(idx,'src/shader_recompiler/profile.h')
    require_order(
        profile_text,
        (
            'u32 sparse_page_shift{};',
            'u32 max_guest_indexed_image_entries{15};',
            'u32 max_guest_indexed_sampler_entries{15};',
            'bool support_int8{};',
            'bool supports_image_load_store_lod{};',
            'bool supports_shader_image_gather_extended{};',
            'bool supports_sampled_image_nonuniform_indexing{};',
            'bool supports_sampler_array_nonuniform_indexing{};',
            'u32 max_push_descriptors{};',
            'bool supports_native_cube_calc{};',
        ),
        'PROFILE_DECLARATION')

    pipeline_text=index_text(idx,'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp')
    require_order(
        pipeline_text,
        (
            '.sparse_page_shift = sparse_page_shift,',
            '.max_guest_indexed_image_entries = GuestIndexedImageLimit(instance),',
            '.max_guest_indexed_sampler_entries = GuestIndexedSamplerLimit(instance),',
            '.support_int8 = instance.IsShaderInt8Supported(),',
            '.supports_image_load_store_lod = instance_.IsImageLoadStoreLodSupported(),',
            '.supports_shader_image_gather_extended =',
            '.supports_sampled_image_nonuniform_indexing =',
            '.supports_sampler_array_nonuniform_indexing =',
            '.max_push_descriptors = instance_.MaxPushDescriptors(),',
            '.supports_native_cube_calc = instance_.IsAmdGcnShaderSupported(),',
        ),
        'PROFILE_INITIALIZER')

    require_order(
        instance_cpp,
        (
            '.shaderImageGatherExtended = features.shaderImageGatherExtended,',
            '.shaderStorageImageMultisample = features.shaderStorageImageMultisample,',
            '.shaderSampledImageArrayDynamicIndexing =',
            '.shaderClipDistance = features.shaderClipDistance,',
        ),
        'VK_FEATURES_INITIALIZER')
    require_order(
        instance_cpp,
        (
            '.shaderInt8 = vk12_features.shaderInt8,',
            '.descriptorIndexing = vk12_features.descriptorIndexing,',
            '.shaderSampledImageArrayNonUniformIndexing =',
            '.scalarBlockLayout = vk12_features.scalarBlockLayout,',
        ),
        'VK12_FEATURES_INITIALIZER')

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
    manifest['generic_indexed_image_tables']={'source_commit':GEN,'source_base':GEN_BASE,'composed_parent':parent,'replayed_generic_commits':[],'integer_sampler_source':INTEGER_SAMPLER_FINAL,'pm4_boundary_source':PM4_BOUNDARY_SOURCE,'vulkan_indexing_feature_fixes':list(VULKAN_INDEXING_FEATURE_FIXES),'descriptor_set_scaling_fixes':list(DESCRIPTOR_SET_SCALING_FIXES),'dynamic_image_binding_fixes':list(DYNAMIC_IMAGE_BINDING_FIXES),'strided_tsharp_fixes':list(STRIDED_TSHARP_FIXES),'explicit_gradient_indexing_fixes':list(EXPLICIT_GRADIENT_INDEXING_FIXES),'paired_indexed_sampler_frontend_fixes':list(PAIRED_INDEXED_SAMPLER_FRONTEND_FIXES),'paired_indexed_sampler_backend_fixes':list(PAIRED_INDEXED_SAMPLER_BACKEND_FIXES),'exact_guest_descriptor_bytes':32,'max_guest_entries':32768,'explicit_null_slot':True,'push_descriptor_fallback_to_descriptor_set':True,'physical_descriptor_limits_checked':True,'runtime_image_offsets':'CAPABILITY_GATED_IMAGE_GATHER_EXTENDED','unsupported_forms_use_existing_path':True,'game_or_title_selectors':False,'shader_hash_selectors':False,'readback_override':False,'forced_draw_state':False,'static_texture_substitution':False,'parent_fixes_preserved':True,'original_source_untouched':True,'fresh_source_clone':True,'cache_versions_single_bump':True,'all_games_validated':False}
    mp=info/'correction.json';mp.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    work,d=fresh_paths(rev)
    cand=None;csha=''
    try:
        stage('create_fresh_source_clone')
        add_fresh_source(work,rev,temp_ref)
        if temp_ref:
            git('update-ref','-d',temp_ref,check=False)
        stage('initialize_fresh_submodules')
        submodules_at(work)
        print('FRESH_SOURCE_CLONE='+str(work),flush=True)
        print('FRESH_BUILD_DIRECTORY='+str(d),flush=True)
        print('COMPOSED_REVISION='+rev,flush=True)
        stage('configure_release_build')
        tee([
            'cmake','--fresh','-S',work,'-B',d,'-G','Ninja',
            '-DCMAKE_BUILD_TYPE=Release',
            '-DENABLE_TESTS=OFF',
            '-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=ON',
        ],info/'configure.log')
        stage('compile_shadps4')
        tee([
            'nice','-n','10','cmake','--build',d,
            '--target','shadps4','--parallel','2',
        ],info/'full-build.log')
        stage('verify_built_candidate')
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
    report={'status':'GENERIC_LARGE_INDEXED_RESOURCE_BUILD_PASS','base':BASE,'source_parent':parent,'source_parent_tree':git('rev-parse',parent+'^{tree}').decode().strip(),'verified_parent':manifest['verified_parent'],'verified_parent_tree':manifest['verified_parent_tree'],'verified_parent_mode':manifest['verified_parent_mode'],'upstream_main':UPSTREAM_MAIN,'upstream_parent':manifest['upstream_parent'],'upstream_parent_tree':manifest['upstream_parent_tree'],'pr_5492_head':PR_5492_HEAD,'generic_source_commit':GEN,'revision':rev,'tree':tree,'candidate':str(cand),'candidate_sha256':csha,'manifest_sha256':ms,'full_build':'PASS','game_started':False,'source_untouched':True,'fresh_source_clone':True,'fresh_build_from_zero':True,'legacy_builds_searched':False,'tests_enabled':False,'original_submodules_untouched':True,'automatic_coherence':'RETAINED','srt_dependencies':'RETAINED','runtime_masked_reduction':'RETAINED','buffer_phi_selection':'RETAINED','image_resource_correctness':'RETAINED','runtime_image_offsets':'CAPABILITY_GATED_IMAGE_GATHER_EXTENDED','generic_indexed_image_tables':'GPU_NONUNIFORM_COMPLETE_TSHARP_SSHARP','game_specific_options':False,'all_games_validated':False}
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
                  'upstream_main':UPSTREAM_MAIN,
                  'pr_5492_head':PR_5492_HEAD,
                  'generic_indexed_image_tables':'GPU_NONUNIFORM_COMPLETE_TSHARP_SSHARP',
                  'game_specific_options':False}
        for k,v in expected.items():
            if r.get(k)!=v:raise RuntimeError('BUILD_GATE='+k)
        verified_parent=r.get('verified_parent')
        if not verified_parent or not exists(verified_parent+'^{commit}'):
            raise RuntimeError('VERIFIED_BUILD_PARENT_MISSING')
        verified_tree=git('rev-parse',verified_parent+'^{tree}').decode().strip()
        if verified_tree!=r.get('verified_parent_tree'):
            raise RuntimeError('VERIFIED_BUILD_PARENT_TREE_MISMATCH')
        mode=r.get('verified_parent_mode','')
        if not mode.endswith('+public-source-replay'):
            raise RuntimeError('VERIFIED_BUILD_PARENT_MODE_INVALID')

        source_parent=r.get('source_parent')
        if not source_parent or not exists(source_parent+'^{commit}'):
            raise RuntimeError('BUILD_PARENT_MISSING')
        source_parent_tree=git('rev-parse',source_parent+'^{tree}').decode().strip()
        if source_parent_tree!=r.get('source_parent_tree'):
            raise RuntimeError('BUILD_PARENT_TREE_MISMATCH')
        upstream_parent=r.get('upstream_parent')
        if not upstream_parent or not exists(upstream_parent+'^{commit}'):
            raise RuntimeError('UPSTREAM_PARENT_MISSING')
        if git('rev-parse',upstream_parent+'^{tree}').decode().strip()!=r.get('upstream_parent_tree'):
            raise RuntimeError('UPSTREAM_PARENT_TREE_MISMATCH')

        parent_line=git('rev-list','--parents','-n','1',source_parent).decode().split()
        if len(parent_line)!=3 or parent_line[1]!=upstream_parent or parent_line[2]!=PR_5492_HEAD:
            raise RuntimeError('PR_5492_MERGE_PARENTAGE_MISMATCH')
        upstream_line=git('rev-list','--parents','-n','1',upstream_parent).decode().split()
        if len(upstream_line)!=3 or upstream_line[1]!=verified_parent or upstream_line[2]!=UPSTREAM_MAIN:
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
        print('PR_5492_HEAD='+PR_5492_HEAD,flush=True)
        print('VERIFIED_PARENT_TREE='+man['verified_parent_tree'],flush=True)
        print('VERIFIED_PARENT_MODE='+man['verified_parent_mode'],flush=True)
        if not args.launch:
            print('GAME_LAUNCHED=NO',flush=True)
            print('UPLOAD_THIS='+str(archive(run_dir,None)),flush=True)
            return 0
        if not OLD.is_file() or digest(OLD)!=OLD_SHA:
            raise RuntimeError('PROTECTED_LAUNCHER_MISSING_OR_CHANGED='+str(OLD))
        stage('launch_got_candidate')
        return launch(info,rev,tree,csha,cand)
    except (Exception,KeyboardInterrupt) as e:
        out=archive(run_dir,e);print('GENERIC_LARGE_INDEXED_RESOURCE_ERROR='+str(e),file=sys.stderr,flush=True);print('UPLOAD_THIS='+str(out),flush=True);return 2
if __name__=='__main__':raise SystemExit(main())
