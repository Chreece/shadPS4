#!/usr/bin/env python3
"""Pinned PR94 source correction. No game/shader-specific handling."""
from __future__ import annotations
import argparse, hashlib, json, pathlib, subprocess, sys
BASE='bbe1a72840733afccba5f48fb4b5eb29a1c96128'
PREFIX='src/video_core/renderer_vulkan/'
HASHES={
 PREFIX+'vk_pipeline_cache.cpp':'c8ac9b16c47caa1477f3f40daeef4e8a9982991e',
 PREFIX+'vk_pipeline_cache.h':'c675ffc3a278f96ac539e69629a8da264142ca47',
 PREFIX+'vk_pipeline_serialization.cpp':'c8a486a20b7c1ec1508729f9176ec8b02c52de2d',
 'src/shader_recompiler/specialization.h':'87ad24653662cee74f01de66cedcad31d43e24d8',
}

def git_blob(data: bytes)->str:
 return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()

def replace_one(text:str, before:str, after:str)->str:
 if text.count(before)!=1:
  raise RuntimeError(f'PATCH_ANCHOR_MISMATCH count={text.count(before)} anchor={before[:90]!r}')
 return text.replace(before,after,1)

OLD_PROGRAM='''struct Program {
    struct Module {
        vk::ShaderModule module;
        Shader::StageSpecialization spec;
    };
    static constexpr size_t MaxPermutations = 8;
    using ModuleList = boost::container::small_vector<Module, MaxPermutations>;

    Shader::Info info;
    ModuleList modules{};

    Program() = default;
    Program(Shader::HwStage stage, Shader::SwStage l_stage, Shader::ShaderParams params)
        : info{stage, l_stage, params} {}

    void AddPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec) {
        modules.emplace_back(module, std::move(spec));
    }

    void InsertPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec,
                      size_t perm_idx) {
        modules.resize(std::max(modules.size(), perm_idx + 1)); // <-- beware of realloc
        modules[perm_idx] = {module, std::move(spec)};
    }
};'''
NEW_PROGRAM='''struct Program {
    struct Module {
        vk::ShaderModule module{};
        Shader::StageSpecialization spec{};
        std::unique_ptr<Shader::Info> owned_info{};

        Shader::Info& GetInfo(Shader::Info& first) noexcept {
            return owned_info ? *owned_info : first;
        }
        const Shader::Info& GetInfo(const Shader::Info& first) const noexcept {
            return owned_info ? *owned_info : first;
        }
    };
    static constexpr size_t MaxPermutations = 8;
    using ModuleList = boost::container::small_vector<Module, MaxPermutations>;

    Shader::Info info;
    ModuleList modules{};

    Program() = default;
    Program(Shader::HwStage stage, Shader::SwStage l_stage, Shader::ShaderParams params)
        : info{stage, l_stage, params} {}

    void AddPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec,
                   std::unique_ptr<Shader::Info> owned_info = {}) {
        spec.info = owned_info ? owned_info.get() : &info;
        modules.emplace_back(module, std::move(spec), std::move(owned_info));
    }

    void InsertPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec,
                      size_t perm_idx, std::unique_ptr<Shader::Info> owned_info = {}) {
        modules.resize(std::max(modules.size(), perm_idx + 1));
        spec.info = owned_info ? owned_info.get() : &info;
        modules[perm_idx] = {module, std::move(spec), std::move(owned_info)};
    }
};'''

GET_TAIL='''    auto& program = it_pgm.value();
    const auto first_binding = binding;

    // A module must always be paired with the resource layout and SRT walker
    // that compiled it, including cached reuse and non-zero permutations.
    for (size_t perm_idx = 0; perm_idx < program->modules.size(); ++perm_idx) {
        auto& saved = program->modules[perm_idx];
        if (!saved.module || !saved.spec.Valid()) {
            continue;
        }
        auto& info = saved.GetInfo(program->info);
        info.pgm_base = params.Base();
        info.user_data = params.user_data;
        info.RefreshFlatBuf();
        auto current = Shader::StageSpecialization(info, runtime_info, profile, first_binding);
        if (!(saved.spec == current)) {
            continue;
        }

        binding.buffer += info.buffers.size();
        binding.unified += info.buffers.size() + info.samplers.size();
        for (const auto& image : info.images) {
            binding.unified += image.NumBindings(info);
        }
        if (auto& fetch = saved.spec.fetch_shader_data; !fetch.Empty()) {
            fetch_shader = &fetch;
        }
        return std::make_tuple(&info, saved.module, program_key.PermutationHash(perm_idx));
    }

    auto new_info = std::make_unique<Shader::Info>(hw_stage, sw_stage, params);
    new_info->is_indirect_compute = is_indirect_compute;
    const size_t perm_idx = program->modules.size();
    const u64 perm_hash = program_key.PermutationHash(perm_idx);
    const auto module = CompileModule(*new_info, runtime_info, params.code, perm_idx, binding);
    ASSERT_MSG(new_info->UsesDispatchMetadata() == program->info.UsesDispatchMetadata(),
               "Permutation {} of shader {:#x} differs in dispatch metadata", perm_idx,
               params.hash);
    auto spec = Shader::StageSpecialization(*new_info, runtime_info, profile, first_binding);
    RegisterShaderMeta(*new_info, spec.fetch_shader_data, spec, perm_hash, perm_idx);
    LOG_INFO(Render_Vulkan,
             "ShaderPermutationInfo: shader={:#x} permutation={} buffers={}/{} images={}/{} "
             "samplers={}/{} (selected/base)", params.hash, perm_idx, new_info->buffers.size(),
             program->info.buffers.size(), new_info->images.size(), program->info.images.size(),
             new_info->samplers.size(), program->info.samplers.size());
    Shader::Info* selected_info = new_info.get();
    program->AddPermut(module, std::move(spec), std::move(new_info));
    if (auto& fetch = program->modules[perm_idx].spec.fetch_shader_data; !fetch.Empty()) {
        fetch_shader = &fetch;
    }
    return std::make_tuple(selected_info, module, perm_hash);
}'''

OLD_LOAD_TAIL='''    vk::ShaderModule module{};

    auto [it_pgm, new_program] = program_cache.try_emplace(program_key);
    if (new_program) {
        module = CompileSPV(spv, instance.GetDevice());
        it_pgm.value() = std::move(program);
    } else {
        const auto& it = std::ranges::find(it_pgm.value()->modules, spec, &Program::Module::spec);
        if (it != it_pgm.value()->modules.end()) {
            // A matching permutation is valid only at its original index. A different index means
            // the store holds entries from more than one cache generation, so this pipeline is
            // left to compile at runtime.
            const auto idx = std::distance(it_pgm.value()->modules.begin(), it);
            if (perm_idx != idx) {
                LOG_WARNING(Render_Vulkan,
                            "Cached permutation {} of {}_{:x} conflicts with index {}, skipping "
                            "preload",
                            perm_idx, program->info.hw_stage, program->info.pgm_hash, idx);
                return false;
            }
            module = it->module;
        } else {
            module = CompileSPV(spv, instance.GetDevice());
        }
    }
    spec.info = &it_pgm.value()->info;
    it_pgm.value()->InsertPermut(module, std::move(spec), perm_idx);

    infos[stage] = &it_pgm.value()->info;
    modules[stage] = module;'''
NEW_LOAD_TAIL='''    vk::ShaderModule module{};
    Shader::Info* selected_info{};
    auto [it_pgm, new_program] = program_cache.try_emplace(program_key);
    if (new_program) {
        module = CompileSPV(spv, instance.GetDevice());
        it_pgm.value() = std::move(program);
        selected_info = &it_pgm.value()->info;
        it_pgm.value()->InsertPermut(module, std::move(spec), perm_idx);
    } else {
        auto& resident = *it_pgm.value();
        const auto it = std::ranges::find(resident.modules, spec, &Program::Module::spec);
        if (it != resident.modules.end()) {
            const auto idx = static_cast<size_t>(std::distance(resident.modules.begin(), it));
            if (perm_idx != idx) {
                LOG_WARNING(Render_Vulkan,
                            "Cached permutation {} conflicts with index {}, skipping preload",
                            perm_idx, idx);
                return false;
            }
            module = it->module;
            selected_info = &it->GetInfo(resident.info);
        } else {
            // Existing pipelines can still reference this slot's metadata.
            // Never replace its ownership with an incompatible disk record.
            if (perm_idx < resident.modules.size() &&
                (resident.modules[perm_idx].module || resident.modules[perm_idx].spec.Valid())) {
                LOG_WARNING(Render_Vulkan,
                            "Cached permutation {} has an incompatible occupied slot, skipping "
                            "preload", perm_idx);
                return false;
            }
            module = CompileSPV(spv, instance.GetDevice());
            auto owned_info = std::make_unique<Shader::Info>(std::move(program->info));
            selected_info = owned_info.get();
            resident.InsertPermut(module, std::move(spec), perm_idx, std::move(owned_info));
        }
    }
    ASSERT(selected_info != nullptr);
    infos[stage] = selected_info;
    modules[stage] = module;'''

SPEC_GUARD_OLD='''    bool operator==(const StageSpecialization& other) const {
        if (!Valid()) {
            return false;
        }
'''
SPEC_GUARD_NEW='''    bool operator==(const StageSpecialization& other) const {
        if (!Valid() || !other.Valid()) {
            return false;
        }
        // Resource counts can differ between independently compiled variants.
        // Reject before the element-wise loops index the other layout.
        if (buffers.size() != other.buffers.size() || images.size() != other.images.size() ||
            samplers.size() != other.samplers.size()) {
            return false;
        }
'''

def get_function(text, start, end):
 a=text.index(start);b=text.index(end,a)
 return text[a:b].rstrip()

def transform(files:dict[str,str])->dict[str,str]:
 out=dict(files)
 p=PREFIX+'vk_pipeline_cache.h'
 out[p]=replace_one(out[p],'#include <string>','#include <memory>\n#include <string>')
 out[p]=replace_one(out[p],OLD_PROGRAM,NEW_PROGRAM)
 p=PREFIX+'vk_pipeline_cache.cpp'
 f=get_function(out[p],'PipelineCache::Result PipelineCache::GetProgram(', '\nstd::optional<vk::ShaderModule> PipelineCache::ReplaceShader(')
 pivot=f.index('    auto& program = it_pgm.value();',f.index('    auto& program = it_pgm.value();')+1)
 new=f[:pivot]+GET_TAIL
 out[p]=replace_one(out[p],f,new)
 p=PREFIX+'vk_pipeline_serialization.cpp'
 out[p]=replace_one(out[p],OLD_LOAD_TAIL,NEW_LOAD_TAIL)
 out[p]=replace_one(out[p],'static constexpr u32 ShaderMetaVersion = 8u;',
  '// Previous metadata could describe permutation zero instead of the saved module.\n'
  'static constexpr u32 ShaderMetaVersion = 9u;')
 p='src/shader_recompiler/specialization.h'
 out[p]=replace_one(out[p],SPEC_GUARD_OLD,SPEC_GUARD_NEW)
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--evidence',required=True)
 a=ap.parse_args();src=pathlib.Path(a.source).resolve();e=pathlib.Path(a.evidence).resolve();e.mkdir(parents=True,exist_ok=True)
 if subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip()!=BASE:
  raise RuntimeError('WRONG_BASE_SOURCE')
 if subprocess.check_output(['git','-C',str(src),'status','--porcelain','--untracked-files=no'],text=True).strip():
  raise RuntimeError('TRACKED_SOURCE_IS_DIRTY')
 old={}
 for p,want in HASHES.items():
  f=src/p
  if f.is_symlink(): raise RuntimeError('SOURCE_FILE_IS_SYMLINK '+p)
  b=f.read_bytes()
  if git_blob(b)!=want: raise RuntimeError('SOURCE_BLOB_MISMATCH '+p)
  old[p]=b.decode('utf8')
 new=transform(old)
 import difflib
 patch=''.join(''.join(difflib.unified_diff(old[p].splitlines(True),new[p].splitlines(True),fromfile='a/'+p,tofile='b/'+p)) for p in sorted(old))
 (e/'source.patch').write_text(patch)
 manifest={'base':BASE,'files':{p:{'old_blob':HASHES[p],'new_blob':git_blob(new[p].encode())} for p in old}}
 (e/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 for p in old:
  f=e/'original'/p;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(old[p])
 # The caller holds shared build locks; all anchors and source identities were checked first.
 try:
  for p in new: (src/p).write_text(new[p])
 except BaseException:
  for p in old: (src/p).write_text(old[p])
  raise
 print('PERMUTATION_METADATA_PATCH=PASS files=4')

if __name__=='__main__':
 try: main()
 except Exception as ex:
  print(f'PERMUTATION_METADATA_PATCH_FAILED={ex}',file=sys.stderr);sys.exit(1)
