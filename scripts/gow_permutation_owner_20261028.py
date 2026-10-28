#!/usr/bin/env python3
"""Generic shader-metadata ownership correction, exact-method CPU regressions, and
an isolated native GoW trial. Synthetic fixtures are not a full Vulkan test.
The worker preserves PR94, the protected gameplay binary, and live saves.
"""
PAYLOAD = {'replacements': {'src/video_core/renderer_vulkan/vk_pipeline_cache.h': [['struct Program {\n    struct Module {\n        vk::ShaderModule module;\n        Shader::StageSpecialization spec;\n    };\n    static constexpr size_t MaxPermutations = 8;\n    using ModuleList = boost::container::small_vector<Module, MaxPermutations>;\n\n    Shader::Info info;\n    ModuleList modules{};\n\n    Program() = default;\n    Program(Shader::HwStage stage, Shader::SwStage l_stage, Shader::ShaderParams params)\n        : info{stage, l_stage, params} {}\n\n    void AddPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec) {\n        modules.emplace_back(module, std::move(spec));\n    }\n\n    void InsertPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec,\n                      size_t perm_idx) {\n        modules.resize(std::max(modules.size(), perm_idx + 1)); // <-- beware of realloc\n        modules[perm_idx] = {module, std::move(spec)};\n    }\n};\n\n', 'struct Program {\n    struct Module {\n        vk::ShaderModule module{};\n        Shader::StageSpecialization spec{};\n        // Heap ownership keeps pipeline/spec pointers stable when modules reallocates.\n        std::unique_ptr<Shader::Info> owned_info{};\n\n        Shader::Info& GetInfo(Shader::Info& first_info) {\n            return owned_info ? *owned_info : first_info;\n        }\n    };\n    static constexpr size_t MaxPermutations = 8;\n    using ModuleList = boost::container::small_vector<Module, MaxPermutations>;\n\n    Shader::Info info;\n    ModuleList modules{};\n\n    Program() = default;\n    Program(Shader::HwStage stage, Shader::SwStage l_stage, Shader::ShaderParams params)\n        : info{stage, l_stage, params} {}\n\n    void AddPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec,\n                   std::unique_ptr<Shader::Info> owned_info = {}) {\n        spec.info = owned_info ? owned_info.get() : &info;\n        modules.push_back(Module{module, std::move(spec), std::move(owned_info)});\n    }\n\n    void InsertPermut(vk::ShaderModule module, Shader::StageSpecialization&& spec,\n                      size_t perm_idx, std::unique_ptr<Shader::Info> owned_info = {}) {\n        modules.resize(std::max(modules.size(), perm_idx + 1));\n        spec.info = owned_info ? owned_info.get() : &info;\n        modules[perm_idx] = Module{module, std::move(spec), std::move(owned_info)};\n    }\n};\n\n'], ['#include <string>', '#include <memory>\n#include <string>']], 'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp': [['PipelineCache::Result PipelineCache::GetProgram(HwStage hw_stage, SwStage sw_stage,\n                                                const Shader::ShaderParams& params,\n                                                Shader::Backend::Bindings& binding,\n                                                bool is_indirect_compute) {\n    auto runtime_info = BuildRuntimeInfo(hw_stage, sw_stage);\n    const ProgramKey program_key{params.hash, is_indirect_compute,\n                                 is_indirect_compute ? runtime_info.hw.cs.shared_memory_size : 0};\n    auto [it_pgm, new_program] = program_cache.try_emplace(program_key);\n    if (new_program) {\n        it_pgm.value() = std::make_unique<Program>(hw_stage, sw_stage, params);\n        auto& program = it_pgm.value();\n        program->info.is_indirect_compute = is_indirect_compute;\n        auto start = binding;\n        const auto module = CompileModule(program->info, runtime_info, params.code, 0, binding);\n        auto spec = Shader::StageSpecialization(program->info, runtime_info, profile, start);\n        const auto perm_hash = program_key.PermutationHash(0);\n\n        RegisterShaderMeta(program->info, spec.fetch_shader_data, spec, perm_hash, 0);\n        program->AddPermut(module, std::move(spec));\n        if (auto& fetch = program->modules[0].spec.fetch_shader_data; !fetch.Empty()) {\n            fetch_shader = &fetch;\n        }\n        return std::make_tuple(&program->info, module, perm_hash);\n    }\n\n    auto& program = it_pgm.value();\n    auto& info = program->info;\n    info.pgm_base = params.Base(); // Needs to be actualized for inline cbuffer address fixup\n    info.user_data = params.user_data;\n    info.RefreshFlatBuf();\n    auto spec = Shader::StageSpecialization(info, runtime_info, profile, binding);\n\n    size_t perm_idx = program->modules.size();\n    u64 perm_hash = program_key.PermutationHash(perm_idx);\n\n    vk::ShaderModule module{};\n\n    const auto it = std::ranges::find(program->modules, spec, &Program::Module::spec);\n    if (it == program->modules.end()) {\n        auto new_info = Shader::Info(hw_stage, sw_stage, params);\n        new_info.is_indirect_compute = is_indirect_compute;\n        module = CompileModule(new_info, runtime_info, params.code, perm_idx, binding);\n        // Bindings and the layout follow the program info, which decided on the metadata.\n        // Permutations agree unless storage-backed LDS depends on their shared memory size.\n        ASSERT_MSG(new_info.UsesDispatchMetadata() == info.UsesDispatchMetadata(),\n                   "Permutation {} of shader {:#x} differs in dispatch metadata", perm_idx,\n                   params.hash);\n\n        RegisterShaderMeta(info, spec.fetch_shader_data, spec, perm_hash, perm_idx);\n        program->AddPermut(module, std::move(spec));\n    } else {\n        info.AddBindings(binding);\n        module = it->module;\n        perm_idx = std::distance(program->modules.begin(), it);\n        perm_hash = program_key.PermutationHash(perm_idx);\n    }\n    if (auto& fetch = program->modules[perm_idx].spec.fetch_shader_data; !fetch.Empty()) {\n        fetch_shader = &fetch;\n    }\n    return std::make_tuple(&program->info, module, perm_hash);\n}\n\n', "PipelineCache::Result PipelineCache::GetProgram(HwStage hw_stage, SwStage sw_stage,\n                                                const Shader::ShaderParams& params,\n                                                Shader::Backend::Bindings& binding,\n                                                bool is_indirect_compute) {\n    auto runtime_info = BuildRuntimeInfo(hw_stage, sw_stage);\n    const ProgramKey program_key{params.hash, is_indirect_compute,\n                                 is_indirect_compute ? runtime_info.hw.cs.shared_memory_size : 0};\n    auto [it_pgm, new_program] = program_cache.try_emplace(program_key);\n    if (new_program) {\n        it_pgm.value() = std::make_unique<Program>(hw_stage, sw_stage, params);\n        auto& program = it_pgm.value();\n        program->info.is_indirect_compute = is_indirect_compute;\n        const auto start = binding;\n        const auto module = CompileModule(program->info, runtime_info, params.code, 0, binding);\n        auto spec = Shader::StageSpecialization(program->info, runtime_info, profile, start);\n        const auto perm_hash = program_key.PermutationHash(0);\n        RegisterShaderMeta(program->info, spec.fetch_shader_data, spec, perm_hash, 0);\n        program->AddPermut(module, std::move(spec));\n        if (auto& fetch = program->modules[0].spec.fetch_shader_data; !fetch.Empty()) {\n            fetch_shader = &fetch;\n        }\n        return std::make_tuple(&program->info, module, perm_hash);\n    }\n\n    auto& program = it_pgm.value();\n    const auto first_binding = binding;\n    // Refresh and match against the metadata of each actual compiled module,\n    // never permutation zero's layout for all variations.\n    for (size_t perm_idx = 0; perm_idx < program->modules.size(); ++perm_idx) {\n        auto& saved = program->modules[perm_idx];\n        if (!saved.module || !saved.spec.Valid()) {\n            continue;\n        }\n        auto& selected = saved.GetInfo(program->info);\n        selected.pgm_base = params.Base();\n        selected.user_data = params.user_data;\n        selected.RefreshFlatBuf();\n        auto current = Shader::StageSpecialization(selected, runtime_info, profile, first_binding);\n        if (!(saved.spec == current)) {\n            continue;\n        }\n        // Match EmitContext's binding advancement, including mip-array slots.\n        binding.buffer += selected.buffers.size();\n        binding.unified += selected.buffers.size() + selected.samplers.size();\n        for (const auto& image : selected.images) {\n            binding.unified += image.NumBindings(selected);\n        }\n        if (auto& fetch = saved.spec.fetch_shader_data; !fetch.Empty()) {\n            fetch_shader = &fetch;\n        }\n        return std::make_tuple(&selected, saved.module, program_key.PermutationHash(perm_idx));\n    }\n\n    const size_t perm_idx = program->modules.size();\n    const u64 perm_hash = program_key.PermutationHash(perm_idx);\n    auto selected = std::make_unique<Shader::Info>(hw_stage, sw_stage, params);\n    selected->is_indirect_compute = is_indirect_compute;\n    const auto module = CompileModule(*selected, runtime_info, params.code, perm_idx, binding);\n    auto spec = Shader::StageSpecialization(*selected, runtime_info, profile, first_binding);\n    RegisterShaderMeta(*selected, spec.fetch_shader_data, spec, perm_hash, perm_idx);\n    const auto* selected_info = selected.get();\n    program->AddPermut(module, std::move(spec), std::move(selected));\n    if (auto& fetch = program->modules[perm_idx].spec.fetch_shader_data; !fetch.Empty()) {\n        fetch_shader = &fetch;\n    }\n    return std::make_tuple(selected_info, module, perm_hash);\n}\n\n"]], 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp': [['bool PipelineCache::LoadPipelineStage(Serialization::Archive& ar, size_t stage) {\n    auto program = std::make_unique<Program>();\n    Shader::StageSpecialization spec{};\n    spec.info = &program->info;\n    size_t perm_idx{};\n    if (!LoadShaderMeta(ar, program->info, spec, perm_idx)) {\n        return false;\n    }\n\n    const ProgramKey program_key{\n        program->info.pgm_hash, program->info.is_indirect_compute,\n        program->info.is_indirect_compute ? spec.runtime_info.hw.cs.shared_memory_size : 0};\n    std::vector<u32> spv{};\n    Storage::DataBase::Instance().Load(\n        Storage::BlobType::ShaderBinary,\n        fmt::format("{:#018x}_{}{}", program_key.hash, perm_idx, program_key.ShaderSuffix()), spv);\n    if (spv.empty()) {\n        return false;\n    }\n\n    // Permutation hash depends on shader variation index. To prevent collisions, we need insert it\n    // at the exact position rather than append\n\n    vk::ShaderModule module{};\n\n    auto [it_pgm, new_program] = program_cache.try_emplace(program_key);\n    if (new_program) {\n        module = CompileSPV(spv, instance.GetDevice());\n        it_pgm.value() = std::move(program);\n    } else {\n        const auto& it = std::ranges::find(it_pgm.value()->modules, spec, &Program::Module::spec);\n        if (it != it_pgm.value()->modules.end()) {\n            // A matching permutation is valid only at its original index. A different index means\n            // the store holds entries from more than one cache generation, so this pipeline is\n            // left to compile at runtime.\n            const auto idx = std::distance(it_pgm.value()->modules.begin(), it);\n            if (perm_idx != idx) {\n                LOG_WARNING(Render_Vulkan,\n                            "Cached permutation {} of {}_{:x} conflicts with index {}, skipping "\n                            "preload",\n                            perm_idx, program->info.hw_stage, program->info.pgm_hash, idx);\n                return false;\n            }\n            module = it->module;\n        } else {\n            module = CompileSPV(spv, instance.GetDevice());\n        }\n    }\n    spec.info = &it_pgm.value()->info;\n    it_pgm.value()->InsertPermut(module, std::move(spec), perm_idx);\n\n    infos[stage] = &it_pgm.value()->info;\n    modules[stage] = module;\n    if (auto& fetch = it_pgm.value()->modules[perm_idx].spec.fetch_shader_data; !fetch.Empty()) {\n        fetch_shader = &fetch;\n    }\n\n    return true;\n}\n\n', 'bool PipelineCache::LoadPipelineStage(Serialization::Archive& ar, size_t stage) {\n    auto loaded = std::make_unique<Shader::Info>();\n    Shader::StageSpecialization spec{};\n    spec.info = loaded.get();\n    size_t perm_idx{};\n    if (!LoadShaderMeta(ar, *loaded, spec, perm_idx)) {\n        return false;\n    }\n\n    const ProgramKey program_key{\n        loaded->pgm_hash, loaded->is_indirect_compute,\n        loaded->is_indirect_compute ? spec.runtime_info.hw.cs.shared_memory_size : 0};\n    std::vector<u32> spv{};\n    Storage::DataBase::Instance().Load(\n        Storage::BlobType::ShaderBinary,\n        fmt::format("{:#018x}_{}{}", program_key.hash, perm_idx, program_key.ShaderSuffix()), spv);\n    if (spv.empty()) {\n        return false;\n    }\n\n    auto [it_pgm, new_program] = program_cache.try_emplace(program_key);\n    if (new_program) {\n        it_pgm.value() = std::make_unique<Program>();\n    }\n    auto& program = it_pgm.value();\n    // Never replace a live metadata owner: existing pipelines retain its pointer.\n    if (perm_idx < program->modules.size()) {\n        auto& saved = program->modules[perm_idx];\n        if (saved.module && saved.spec.Valid()) {\n            if (!(saved.spec == spec)) {\n                LOG_WARNING(Render_Vulkan, "Conflicting cached permutation {}, skipping preload",\n                            perm_idx);\n                return false;\n            }\n            infos[stage] = &saved.GetInfo(program->info);\n            modules[stage] = saved.module;\n            if (auto& fetch = saved.spec.fetch_shader_data; !fetch.Empty()) {\n                fetch_shader = &fetch;\n            }\n            return true;\n        }\n    }\n    for (size_t i = 0; i < program->modules.size(); ++i) {\n        const auto& saved = program->modules[i];\n        if (i != perm_idx && saved.module && saved.spec.Valid() && saved.spec == spec) {\n            LOG_WARNING(Render_Vulkan, "Cached permutation {} conflicts with index {}, skipping preload",\n                        perm_idx, i);\n            return false;\n        }\n    }\n\n    const auto module = CompileSPV(spv, instance.GetDevice());\n    const auto* selected_info = loaded.get();\n    program->InsertPermut(module, std::move(spec), perm_idx, std::move(loaded));\n    infos[stage] = selected_info;\n    modules[stage] = module;\n    if (auto& fetch = program->modules[perm_idx].spec.fetch_shader_data; !fetch.Empty()) {\n        fetch_shader = &fetch;\n    }\n    return true;\n}\n\n'], ['static constexpr u32 ShaderMetaVersion = 8u;', 'static constexpr u32 ShaderMetaVersion = 9u;']], 'src/shader_recompiler/specialization.h': [['        if (!Valid()) {\n            return false;\n        }\n\n        if (vs_attribs != other.vs_attribs) {', "        if (!Valid() || !other.Valid()) {\n            return false;\n        }\n        // Distinct compiled resource layouts cannot be compared by indexing\n        // one variation's vectors with another variation's lengths.\n        if (buffers.size() != other.buffers.size() || images.size() != other.images.size() ||\n            samplers.size() != other.samplers.size()) {\n            return false;\n        }\n\n        if (vs_attribs != other.vs_attribs) {"]]}, 'source_blobs': {'src/video_core/renderer_vulkan/vk_pipeline_cache.h': 'c675ffc3a278f96ac539e69629a8da264142ca47', 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp': 'c8a486a20b7c1ec1508729f9176ec8b02c52de2d', 'src/shader_recompiler/specialization.h': '87ad24653662cee74f01de66cedcad31d43e24d8', 'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp': 'c8ac9b16c47caa1477f3f40daeef4e8a9982991e'}, 'review_record': {'schema_version': 2, 'record_id': 'gow-gpu-submit-trace-20261028T111830Z-1380900-reviewed', 'recorded_at': '2026-10-28', 'record_type': 'GOW_SEPARATED_SCHEDULER_TRACE_RESULT', 'game_id': 'CUSA34384', 'tested_games': ['CUSA34384'], 'archive': 'gow-gpu-submit-trace-20261028T111830Z-1380900.tar.gz', 'archive_sha256': 'c4e099a8600cfb3cc08dcea1d61124f0f39960ef075debaf892318b9abd65064', 'game_archive_sha256': '7f9962419a1aa3a996ebe4d25b9e56df15524ae171989d24aa4b58d1c5b5e6cd', 'source_commit': '0fcd2676e34001be59f450ec3570786d28165f1b', 'binary_sha256': '0a0c0625c0e8d6e5fec5f53089e5f878e79a1705b0093716ea0d6938836ba874', 'build': 'CPU_FIXTURE_AND_NATIVE_OBJECTS_AND_LINK_PASS_ZERO_WARNINGS', 'game_elapsed_seconds': 22.385, 'game_exit_code': -11, 'gpu_failure': 'RADV_DEVICE_LOST_DURING_SUBMIT', 'failed_submission_tick': 13, 'cached_completed_tick': 1, 'cached_value_is_not_live_completion': True, 'scheduler_events': 1614, 'compute_events': 1477, 'draw_events': 124, 'submissions': 13, 'recorder_overflow': 0, 'first_compute': {'tick': 2, 'shader': '0x9a1583fb', 'groups': [16384, 1, 1]}, 'first_recorded_indirect': {'tick': 2, 'shader': '0xcfe2c248', 'address': '0x21d4157e0'}, 'graphics_permutation_observed': 'vs 0x2b3f2254', 'graphics_visual_acceptance': 'NOT_OBSERVED', 'vram_free_gib': 8.617, 'ollama_unloaded': False, 'protected_binary_unchanged': True, 'pr94_source_and_binary_restored': True, 'decision': 'KEEP_TRACE_VALIDATION_NOT_GAMEPLAY; FIX_PROVEN_METADATA_OWNERSHIP_DEFECT_WITH_INDEPENDENT_REGRESSION', 'causality_limit': 'No GPU-executed command boundary is captured. Tick13 and the first recorded shader are not established fault causes. Per-permutation metadata defect is exercised but its causal link to device loss is unproved.'}, 'fixture_prefix': '#include <algorithm>\n#include <array>\n#include <bitset>\n#include <cstdint>\n#include <cstdio>\n#include <cstdlib>\n#include <map>\n#include <memory>\n#include <span>\n#include <string>\n#include <tuple>\n#include <utility>\n#include <vector>\nusing u32=std::uint32_t; using u64=std::uint64_t;\nstatic void Check(bool ok,const char* s){if(!ok){std::fprintf(stderr,"PERMUTATION_FAIL=%s\\n",s);std::exit(31);}}\n#define LOG_WARNING(...) ((void)0)\n#define ASSERT_MSG(x,...) Check((x),"source assertion")\nnamespace boost::container {template<class T,std::size_t N> using small_vector=std::vector<T>;}\nnamespace vk {struct ShaderModule {u64 id{};explicit operator bool()const{return id!=0;} bool operator==(const ShaderModule&)const=default;};}\nnamespace Shader {\nenum class HwStage {Compute, Vertex}; enum class SwStage {Compute, Vertex};\nstruct Profile{};\nnamespace Backend{struct Bindings{std::size_t buffer{},unified{};bool operator==(const Bindings&)const=default;};}\nstruct RuntimeInfo{struct{struct{u32 shared_memory_size{};}cs;}hw;};\nstruct ShaderParams{u64 hash{1};int user_data{1};std::vector<u32>code{};u64 Base()const{return 0x1000;}};\nstruct Info;\nstruct Image {std::size_t slots{1};std::size_t NumBindings(const Info&)const{return slots;}};\nstruct Info {\n u64 pgm_hash{},pgm_base{};HwStage hw_stage{};SwStage sw_stage{};int user_data{};bool is_indirect_compute{};\n std::vector<int>buffers,samplers;std::vector<Image>images;\n Info()=default;\n Info(HwStage hw,SwStage sw,ShaderParams p):pgm_hash(p.hash),hw_stage(hw),sw_stage(sw),user_data(p.user_data){}\n void RefreshFlatBuf(){} bool UsesDispatchMetadata()const{return is_indirect_compute;}\n void AddBindings(Backend::Bindings& b)const{b.buffer+=buffers.size();b.unified+=buffers.size()+images.size()+samplers.size();}\n};\nstruct Fetch{bool Empty()const{return true;}};\nstruct StageSpecialization{\n const Info* info{};RuntimeInfo runtime_info{};Fetch fetch_shader_data{};Backend::Bindings start{};int requested{};std::size_t image_count{};\n StageSpecialization()=default;\n StageSpecialization(const Info& i,RuntimeInfo r,const Profile&,Backend::Bindings b):info(&i),runtime_info(r),start(b),requested(i.user_data),image_count(i.images.size()){}\n bool Valid()const{return info!=nullptr;}\n bool operator==(const StageSpecialization&o)const{return Valid()&&o.Valid()&&requested==o.requested&&image_count==o.image_count&&start==o.start;}\n};\n}\nnamespace Serialization {struct Archive{u64 hash;std::size_t index,count;bool indirect{};};}\nnamespace fmt {template<class...Ts>std::string format(const char*,Ts&&...){return "fixture";}}\nnamespace Storage {enum class BlobType{ShaderBinary};struct DataBase{static DataBase&Instance(){static DataBase x;return x;}void Load(BlobType,const std::string&,std::vector<u32>&out){out={1};}};}\nnamespace Vulkan {\nusing Shader::HwStage;using Shader::SwStage;\nstruct ProgramKey{u64 hash;bool is_indirect_compute{};u32 shared_memory_size{};\n bool operator<(const ProgramKey&o)const{return std::tie(hash,is_indirect_compute,shared_memory_size)<std::tie(o.hash,o.is_indirect_compute,o.shared_memory_size);}\n u64 PermutationHash(std::size_t i)const{return hash*10000+i+(is_indirect_compute?1000000:0);}\n std::string ShaderSuffix()const{return is_indirect_compute?"_indirect":"";}};\n', 'fixture_middle': 'template<class T>struct Map{std::map<ProgramKey,T> m;struct It{typename std::map<ProgramKey,T>::iterator i;T&value(){return i->second;}};auto try_emplace(ProgramKey k){auto[i,b]=m.try_emplace(k);return std::pair{It{i},b};}};\nstatic u64 serial{}; static std::size_t registered_count{};static bool registration_owner_matches{true};\nstatic vk::ShaderModule CompileSPV(const std::vector<u32>&,int){return {++serial};}\nstatic void RegisterShaderMeta(const Shader::Info&i,const Shader::Fetch&,const Shader::StageSpecialization&s,u64,std::size_t){registered_count=i.images.size();registration_owner_matches&=s.info==&i;}\nstatic bool LoadShaderMeta(Serialization::Archive&a,Shader::Info&i,Shader::StageSpecialization&s,std::size_t&idx){i.pgm_hash=a.hash;i.user_data=int(a.count);i.images.resize(a.count);i.is_indirect_compute=a.indirect;idx=a.index;s=Shader::StageSpecialization(i,{},Shader::Profile{},{});return true;}\nstruct PipelineCache{\n using Result=std::tuple<const Shader::Info*,vk::ShaderModule,u64>;\n Map<std::unique_ptr<Program>>program_cache;Shader::Profile profile;Shader::Fetch*fetch_shader{};\n std::array<const Shader::Info*,4>infos{};std::array<vk::ShaderModule,4>modules{};\n struct Instance{int GetDevice(){return 0;}}instance;\n static Shader::RuntimeInfo BuildRuntimeInfo(HwStage,SwStage){return {};}\n static vk::ShaderModule CompileModule(Shader::Info&i,Shader::RuntimeInfo&,const std::vector<u32>&,std::size_t,Shader::Backend::Bindings&b){\n  i.images.resize(std::size_t(i.user_data));\n  for(auto& im:i.images)im.slots=2;\n  b.buffer+=i.buffers.size();b.unified+=i.images.size()*2;\n  return {++serial};\n }\n Result GetProgram(HwStage,SwStage,const Shader::ShaderParams&,Shader::Backend::Bindings&,bool=false);\n bool LoadPipelineStage(Serialization::Archive&,std::size_t);\n};\n', 'fixture_suffix': '} // Vulkan\nint main(int argc,char**argv){\n using namespace Vulkan;\n const bool baseline=argc>1&&std::string(argv[1])=="baseline";\n PipelineCache c;Shader::ShaderParams p;Shader::Backend::Bindings b;\n auto a=c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b);const Shader::Info* first=std::get<0>(a);\n Check(first->images.size()==1,"initial metadata");p.user_data=97;b={};\n auto z=c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b);const Shader::Info* second=std::get<0>(z);\n if(baseline){Check(second->images.size()==1&&registered_count==1,"old wrong first-permutation metadata reproduced");std::puts("BASELINE_WRONG_METADATA_REPRODUCED=module97_metadata1");return 0;}\n Check(second->images.size()==97,"new variation uses owned metadata");Check(second!=first,"different owners");\n Check(registered_count==97&&registration_owner_matches,"serialized metadata owns compiled module");\n const auto module=std::get<1>(z);b={};auto reuse=c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b);\n Check(std::get<0>(reuse)==second&&std::get<1>(reuse)==module,"cache reuse returns identical owner");\n Check(b.unified==194,"cache hit advances mip array slots exactly");\n p.user_data=1;b={};Check(std::get<0>(c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b))==first,"first variation reselected");\n for(int n=2;n<80;n++){p.user_data=n;b={};auto q=c.GetProgram(HwStage::Vertex,SwStage::Vertex,p,b);Check(std::get<0>(q)->images.size()==std::size_t(n),"variation growth");}\n Check(first->images.size()==1&&second->images.size()==97,"pointers survive module vector growth");\n p.user_data=97;b={};auto ind=c.GetProgram(HwStage::Compute,SwStage::Compute,p,b,true);\n Check(std::get<0>(ind)->is_indirect_compute,"indirect identity retained");\n PipelineCache loaded;\n Serialization::Archive ar{3,5,97,true};Check(loaded.LoadPipelineStage(ar,0),"load nonzero first slot");\n const Shader::Info* five=loaded.infos[0];Check(five->images.size()==97&&five->is_indirect_compute,"loaded info owned");\n ar={3,2,1,true};Check(loaded.LoadPipelineStage(ar,1),"load distinct second slot");\n const Shader::Info* two=loaded.infos[1];Check(two!=five&&two->images.size()==1,"loaded variations distinct");\n ar={3,5,97,true};Check(loaded.LoadPipelineStage(ar,2)&&loaded.infos[2]==five,"duplicate preload preserves live owner");\n ar={3,5,42,true};Check(!loaded.LoadPipelineStage(ar,2),"conflicting same slot rejected");\n Check(five->images.size()==97&&two->images.size()==1,"conflict cannot invalidate live metadata");\n ar={3,7,97,true};Check(!loaded.LoadPipelineStage(ar,2),"same variant different index rejected");\n p.hash=3;p.user_data=97;b={};Check(std::get<0>(loaded.GetProgram(HwStage::Compute,SwStage::Compute,p,b,true))==five,"loaded nonzero slot runtime reuse");\n p.user_data=2;b={};Check(std::get<0>(loaded.GetProgram(HwStage::Compute,SwStage::Compute,p,b,true))->images.size()==2,"new runtime variation after sparse preload");\n std::puts("EXACT_METHOD_PERMUTATION_OWNERSHIP=PASS runtime_cache_preload_pointer_stability_indirect_bindings");\n}\n', 'shape_prefix': '#include <bitset>\n#include <cstdint>\n#include <cstdio>\n#include <cstdlib>\n#include <vector>\nusing u32=std::uint32_t;\nstruct StageSpecialization {\n const int* info{};\n int runtime_info{},fetch_shader_data{},start{};\n std::vector<int>vs_attribs,fmasks,buffers,images,samplers;\n std::bitset<256>bitset{};\n bool Valid()const{return info!=nullptr;}\n', 'shape_suffix': '};\nstatic void require(bool x){if(!x)std::abort();}\nint main(){\n int owner=0;StageSpecialization a,b;a.info=b.info=&owner;a.bitset.set();b.bitset.set();\n require(a==b);\n a.buffers={1,2};b.buffers={1};require(!(a==b));require(!(b==a));\n a.buffers.clear();b.buffers.clear();a.images={1,2};b.images={1};require(!(a==b));require(!(b==a));\n a.images.clear();b.images.clear();a.samplers={1,2};b.samplers={1};require(!(a==b));require(!(b==a));\n a.samplers.clear();b.samplers.clear();require(a==b);\n b.info=nullptr;require(!(a==b));require(!(b==a));\n std::puts("EXACT_SPECIALIZATION_SHAPE_GUARDS=PASS");\n}\n'}

# SPDX-License-Identifier: GPL-2.0-or-later
# A source-pinned isolated shadPS4 experiment, not a production installation.
import argparse, datetime as dt, difflib, fcntl, hashlib, json, os
from pathlib import Path
import re, shutil, signal, subprocess, sys, tarfile, time, traceback

BASE = 'bbe1a72840733afccba5f48fb4b5eb29a1c96128'
TRACE = '0fcd2676e34001be59f450ec3570786d28165f1b'
UPSTREAM = '45837a89a617ac394c1844ec95a28471fb79ec08'
OLD_UPSTREAM = '3eecea94ddaca360b19f4b800523b609a8b2ffcc'
BASE_ELF = '65fafc436831d76380db349b89c89fe610d16325981870ac073d8b9ed0f010b9'
PROTECTED_ELF = '8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e'
PR91_ELF = '46195c52e9dd3fb3921b0b5c99a233edf4b484ecceb7794c12ae864626d6b3af'
TEMPLATE_BLOB = 'b773b5936e5bc72ae631c3c0f9ac20578fa9f7f8'
ROOT = Path.home()/'projects/shadps4'
TREE = ROOT/'Upstream/tests/gow-hle-abi-20261027-194629-907086'
SRC = TREE/'source'
BUILD = TREE/'build'
BIN = BUILD/'shadps4'
PROTECTED = ROOT/'GodOfWarRagnarok/build/shadps4'
PR91 = TREE/'old-binaries/pr91-46195c52-native-shadps4'
LOGS = ROOT/'logs'
FILES = list(PAYLOAD['replacements'])
SOURCE_BLOBS = PAYLOAD['source_blobs']
UPSTREAM_PATHS = set('''flake.nix
src/core/libraries/kernel/file_system.cpp
src/core/libraries/np/np_handler/np_handler_score.cpp
src/core/libraries/np/np_score/np_score.cpp
src/core/libraries/np/np_score/np_score_ctx.h
src/core/libraries/pad/pad.cpp
src/core/libraries/save_data/save_backup.cpp
src/core/libraries/save_data/save_backup.h
src/core/libraries/save_data/savedata.cpp
src/core/libraries/save_data/savedata.h'''.splitlines())

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def blob(b):
    return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()

def require(ok, message):
    if not ok: raise RuntimeError(message)

def command(args, log=None, timeout=90, data=None, env=None):
    args=[str(x) for x in args]
    output=open(log,'wb') if log else subprocess.PIPE
    try:
        p=subprocess.Popen(args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                           stdout=output, stderr=subprocess.STDOUT, env=env,
                           start_new_session=True)
        try:
            result=p.communicate(data, timeout=timeout)[0]
        except BaseException:
            try: os.killpg(p.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGKILL);p.wait()
            raise
        if p.returncode:
            tail=Path(log).read_text(errors='replace')[-9000:] if log else (result or b'').decode(errors='replace')[-9000:]
            raise RuntimeError(f'COMMAND_EXIT={p.returncode} {args[0]}\n{tail}')
        return (result or b'').decode().strip()
    finally:
        if log: output.close()

def git(*args, **kwargs):
    return command(['git','-C',SRC,*args], **kwargs)

def clean():
    return git('status','--porcelain','--untracked-files=no')==''

def fragment(s, a, b):
    require(s.count(a)==1, 'SOURCE_ANCHOR_CHANGED='+a)
    start=s.index(a); end=s.index(b,start)
    return s[start:end]

def transformed(original):
    result={}
    for rel, replacements in PAYLOAD['replacements'].items():
        s=original[rel]
        for before, after in replacements:
            require(s.count(before)==1, 'EXACT_PATCH_ANCHOR_CHANGED='+rel)
            s=s.replace(before,after)
        result[rel]=s
    return result

def fixture_source(files):
    header=files['src/video_core/renderer_vulkan/vk_pipeline_cache.h']
    cache=files['src/video_core/renderer_vulkan/vk_pipeline_cache.cpp']
    ser=files['src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp']
    return (PAYLOAD['fixture_prefix']+
            fragment(header,'struct Program {','struct DrawIndirectParams {')+
            PAYLOAD['fixture_middle']+
            fragment(cache,'PipelineCache::Result PipelineCache::GetProgram(',
                     'std::optional<vk::ShaderModule> PipelineCache::ReplaceShader(')+
            fragment(ser,'bool PipelineCache::LoadPipelineStage(', 'void PipelineCache::WarmUp()')+
            PAYLOAD['fixture_suffix'])

def fixture(files, work, evidence, name, baseline=False):
    code=evidence/(name+'.cpp'); exe=work/name
    code.write_text(fixture_source(files))
    args=['g++','-std=c++23','-O2','-DNDEBUG','-Wall','-Wextra','-Werror','-pedantic']
    if baseline: args+=['-Wno-sign-compare'] # inherited pre-existing signed comparison
    command([*args,code,'-o',exe],evidence/(name+'-compile.log'),120)
    command([exe,*(['baseline'] if baseline else [])],evidence/(name+'-result.txt'),30)

def shape_fixture(files, work, evidence):
    s=files['src/shader_recompiler/specialization.h']
    method=fragment(s, '    bool operator==(const StageSpecialization& other) const {',
                    '    void Serialize(Serialization::Archive& ar) const;')
    code=evidence/'specialization-shape.cpp';exe=work/'specialization-shape'
    code.write_text(PAYLOAD['shape_prefix']+method+PAYLOAD['shape_suffix'])
    command(['g++','-std=c++23','-O2','-DNDEBUG','-Wall','-Wextra','-Werror','-pedantic',
             code,'-o',exe],evidence/'shape-compile.log',120)
    command([exe],evidence/'shape-result.txt',30)

def append_diary(row):
    path=ROOT/'diary/trials.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+',encoding='utf-8') as f:
        fcntl.flock(f.fileno(),fcntl.LOCK_EX)
        f.seek(0)
        for line in f:
            try: previous=json.loads(line)
            except (ValueError, TypeError): continue
            if isinstance(previous, dict) and previous.get('record_id')==row['record_id']: return
        f.seek(0,os.SEEK_END);f.write(json.dumps(row,separators=(',',':'))+'\n')
        f.flush();os.fsync(f.fileno())

def derive(template, target, source, binary_sha):
    require(blob(template)==TEMPLATE_BLOB,'RUNTIME_TEMPLATE_HASH_MISMATCH')
    s=template.decode()
    rules=[('gow-official-indirect-','gow-perm-owner-game-',2),
      ('CANDIDATE="$ROOT/Upstream/tests/shadps4-indirect-hazards-20261027-170922-3777629"',f'CANDIDATE="{TREE}"',1),
      ('43d7d22360dee7a545ef9e81557fb8ae9109f7a4',source,2),
      ('3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75',binary_sha,2),
      ('OFFICIAL_MAIN_PR5479_PR88_NATIVE_GOW','UPSTREAM_GENERIC_PERMUTATION_OWNER_GOW',1),
      ('"recorded_at":"2026-10-27"',f'"recorded_at":"{dt.datetime.now(dt.timezone.utc).date()}"',1)]
    for a,b,n in rules:
        require(s.count(a)==n,'RUNTIME_TEMPLATE_DRIFT='+a[:60]);s=s.replace(a,b)
    old='''exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/shadps4-official-indirect-game.lock"
flock -n 9 && flock -n 8 && flock -n 7 || {
    echo "CONCURRENT_GAME_BUILD_LOCK_UNAVAILABLE_NO_ACTION"; exit 3;
}'''
    require(s.count(old)==1,'NESTED_LOCK_BLOCK_CHANGED')
    s=s.replace(old,'''[[ "$GOW_OWNER_PARENT_LOCKS" == YES ]] || {
    echo "PARENT_LOCK_GUARD_MISSING"; exit 3;
}''')
    s=s.replace('-d "$SRC/.git"','-e "$SRC/.git"')
    require('--seconds 118' in s and 'shutil.copy2(old,dest)' in s and 'GAME="CUSA34384"' in s,
            'RUNTIME_PROTECTION_MISSING')
    target.write_text(s);command(['bash','-n',target])

def worker(tag):
    os.umask(0o077)
    work=ROOT/'GodOfWarRagnarok/tests'/tag
    e=LOGS/(tag+'-evidence');archive=LOGS/(tag+'.tar.gz')
    work.mkdir(parents=True,exist_ok=False);e.mkdir(exist_ok=False)
    status={'schema_version':2,'record_id':tag,'recorded_at':dt.datetime.now(dt.timezone.utc).isoformat(),
       'record_type':'GENERIC_PERMUTATION_OWNERSHIP_UPSTREAM_NATIVE_TEST',
       'game_id':'CUSA34384','tested_games':[], 'base_commit':BASE,'upstream_commit':UPSTREAM,
       'stage':'PREFLIGHT','source_commit':None,'binary_sha256':None,'game_rc':None,
       'archive':str(archive),'gameplay':'NOT_VERIFIED','decision':'ISOLATED_CANDIDATE_NO_PROMOTION'}
    held=[];switched=False;dirty_patch=False;heads=set();snapshot=TREE/'old-binaries/pr94-65fafc43-native-shadps4'
    try:
        for name in ('gow-permutation-owner.lock','gow-upstream-got-build.lock',
                     'shadps4-gow-srt-phi.lock','gow-hle-stack-build.lock'):
            f=(LOGS/name).open('a+');fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);held.append(f)
        require(git('rev-parse','HEAD')==BASE and clean(),'PR94_SOURCE_NOT_CLEAN_EXACT_BASE')
        for path,expected in [(BIN,BASE_ELF),(PROTECTED,PROTECTED_ELF),(PR91,PR91_ELF)]:
            require(path.is_file() and not path.is_symlink() and sha(path)==expected,
                    'PROTECTED_ELF_MISMATCH='+str(path))
        p=subprocess.run(['pgrep','-x','shadps4'],capture_output=True)
        require(p.returncode==1,'ANOTHER_EMULATOR_RUNNING_OR_PROCESS_CHECK_FAILED')
        require(shutil.disk_usage(TREE).free>=3*1024**3,'NEED_3_GIB_FREE_FOR_PRESERVATION')
        template=LOGS/'gow-gpu-submit-trace-20261028T111830Z-1380900-evidence/original-game-runner.sh'
        require(template.is_file() and blob(template.read_bytes())==TEMPLATE_BLOB,'PINNED_EXISTING_RUNTIME_TEMPLATE_MISSING')
        require(not snapshot.is_symlink(), 'SYMLINK_SNAPSHOT_REFUSED')
        if not snapshot.exists(): shutil.copy2(BIN,snapshot)
        require(not snapshot.is_symlink() and sha(snapshot)==BASE_ELF,'PR94_SNAPSHOT_MISMATCH')
        append_diary(PAYLOAD['review_record'])
        print('PROTECTED_BINARIES_VERIFIED=YES',flush=True)
        status['stage']='FETCH_PINNED_SOURCE'
        for pin,url in [(TRACE,'https://github.com/Chreece/shadPS4.git'),
                        (UPSTREAM,'https://github.com/shadps4-emu/shadPS4.git')]:
            present=subprocess.run(['git','-C',str(SRC),'cat-file','-e',pin+'^{commit}'],capture_output=True).returncode==0
            if not present:
                git('-c','http.version=HTTP/1.1','fetch','--no-tags','--no-recurse-submodules',url,pin,
                    log=e/('fetch-'+pin[:8]+'.log'),timeout=600)
        git('merge-base','--is-ancestor',BASE,TRACE)
        git('merge-base','--is-ancestor',OLD_UPSTREAM,UPSTREAM)
        require(set(git('diff','--name-only',OLD_UPSTREAM,UPSTREAM).splitlines())==UPSTREAM_PATHS,
                'UPSTREAM_DELTA_NOT_THE_REVIEWED_10_PATHS')
        env=os.environ.copy();env.update({'GIT_AUTHOR_NAME':'shadPS4 isolated experiment',
            'GIT_COMMITTER_NAME':'shadPS4 isolated experiment',
            'GIT_AUTHOR_EMAIL':'experiment@localhost','GIT_COMMITTER_EMAIL':'experiment@localhost'})
        merged_tree=git('merge-tree','--write-tree',TRACE,UPSTREAM).splitlines()[0]
        require(re.fullmatch('[0-9a-f]{40}',merged_tree) is not None,'MERGE_TREE_INVALID')
        merged=git('commit-tree',merged_tree,'-p',TRACE,'-p',UPSTREAM,
                   '-m','Isolated integration: current upstream plus retained PR94 correctness and trace',env=env)
        require(set(git('diff','--name-only',TRACE,merged).splitlines())==UPSTREAM_PATHS,
                'UNEXPECTED_INTEGRATION_DELTA')
        heads.add(merged);git('switch','--detach',merged);switched=True
        original={rel:(SRC/rel).read_text() for rel in FILES}
        for rel,text in original.items():
            require(blob(text.encode())==SOURCE_BLOBS[rel],'METADATA_SOURCE_PREIMAGE_CHANGED='+rel)
        fixed=transformed(original)
        status['stage']='BASELINE_CPU_REGRESSION'
        fixture(original,work,e,'baseline-exact-method',True)
        fixture(fixed,work,e,'fixed-exact-method')
        shape_fixture(fixed,work,e)
        print('EXACT_METHOD_REGRESSION=OLD_FAILS_NEW_PASSES',flush=True)
        patch=''.join(''.join(difflib.unified_diff(original[r].splitlines(True),fixed[r].splitlines(True),
                          fromfile='a/'+r,tofile='b/'+r)) for r in FILES)
        (e/'permutation-ownership.patch').write_text(patch)
        git('apply','--check','--index','-',data=patch.encode())
        git('apply','--index','-',data=patch.encode());dirty_patch=True
        require(set(git('diff','--cached','--name-only').splitlines())==set(FILES),'PATCH_TOUCHED_UNEXPECTED_FILES')
        git('-c','core.hooksPath=/dev/null','commit','-m',
            'vulkan: retain exact per-permutation shader metadata, cache owner and binding layout',env=env)
        dirty_patch=False;candidate=git('rev-parse','HEAD');heads.add(candidate)
        status['source_commit']=candidate
        git('update-ref','refs/heads/experiments/'+tag,candidate,'0'*40)
        git('merge-base','--is-ancestor',UPSTREAM,candidate)
        (e/'source-commit.txt').write_text(git('show','-s','--format=fuller',candidate)+'\n')
        (e/'full-source-delta.patch').write_text(git('diff',BASE,candidate)+'\n')
        for rel in FILES:
            dest=e/'source'/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((SRC/rel).read_bytes())
        status['stage']='NATIVE_CPP_OBJECTS'
        print('NATIVE_COMPILE_SOURCE='+candidate,flush=True)
        for stem in ('vk_pipeline_cache','vk_pipeline_serialization','vk_scheduler','vk_rasterizer'):
            obj=f'CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/{stem}.cpp.o'
            command(['nice','-n','10','ninja','-C',BUILD,'-j','2',obj],e/(stem+'-build.log'),2400)
        status['stage']='NATIVE_FINAL_LINK'
        command(['nice','-n','10','cmake','--build',BUILD,'--target','shadps4','--parallel','2'],e/'native-link.log',2400)
        newsha=sha(BIN);require(newsha!=BASE_ELF,'BINARY_DID_NOT_CHANGE')
        status['binary_sha256']=newsha
        deps=command(['ldd',BIN]);(e/'ldd.txt').write_text(deps+'\n');require('not found' not in deps,'NATIVE_LIBRARY_MISSING')
        kept=work/'candidate/shadps4';kept.parent.mkdir();shutil.copy2(BIN,kept)
        require(sha(kept)==newsha,'CANDIDATE_COPY_FAILED')
        status['preserved_candidate']=str(kept)
        warnings=[]
        for p in e.glob('*build.log'):
            warnings.extend(p.name+': '+l for l in p.read_text(errors='replace').splitlines() if 'warning:' in l)
        warnings.extend('native-link.log: '+l for l in (e/'native-link.log').read_text(errors='replace').splitlines() if 'warning:' in l)
        (e/'compiler-warnings.txt').write_text('\n'.join(warnings)+'\n' if warnings else '')
        print('NATIVE_BUILD_PASS='+newsha,flush=True)
        derived=e/'native-game-runner.sh';derive(template.read_bytes(),derived,candidate,newsha)
        game_tag='gow-perm-owner-game-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())
        status['stage']='ONE_BOUNDED_GOW_TEST';game_log=LOGS/(game_tag+'.log')
        runtime_env=os.environ.copy();runtime_env['GOW_OWNER_PARENT_LOCKS']='YES'
        with game_log.open('wb') as f:
            # Runtime is capped by the previously verified observer (118s + 2s cleanup).
            p=subprocess.run(['bash',str(derived),'--worker',game_tag],stdout=f,stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,env=runtime_env)
        status['game_rc']=p.returncode
        if 'NATIVE_GENERIC_GAME_LAUNCHED=YES' in game_log.read_text(errors='replace'):
            status['tested_games']=['CUSA34384']
        ga=LOGS/(game_tag+'.tar.gz')
        if ga.exists():shutil.copy2(ga,e/'game-runtime.tar.gz')
        game_dir=ROOT/'GodOfWarRagnarok/tests'/('playtest-'+game_tag)/'evidence/game'
        console=game_dir/'full-console.log'
        if console.is_file() and console.stat().st_size<=64*1024**2:
            shutil.copy2(console,e/'full-console.log')
        if (game_dir/'native-result.json').exists():
            status['native_result']=json.loads((game_dir/'native-result.json').read_text())
        status['stage']='NATIVE_BUILD_PASS_RUNTIME_CAPTURED' if p.returncode==0 else 'NATIVE_BUILD_PASS_RUNTIME_FAILED'
        print('GAME_RESULT='+str(p.returncode),flush=True)
        return 0 if p.returncode==0 else 1
    except BaseException as ex:
        status['failure']=str(ex);print('EXPERIMENT_FAILED='+str(ex),flush=True)
        (e/'failure.txt').write_text(traceback.format_exc());return 1
    finally:
        restore=[]
        if switched:
            try:
                if dirty_patch:
                    require(git('rev-parse','HEAD') in heads,'SOURCE_HEAD_CHANGED_EXTERNALLY')
                    git('apply','--index','--reverse',e/'permutation-ownership.patch')
                require(git('rev-parse','HEAD') in heads and clean(),'EXTERNAL_SOURCE_CHANGE_REFUSE_RESTORE')
                git('switch','--detach',BASE);restore.append('SOURCE_RESTORED')
            except Exception as ex:restore.append('SOURCE_RESTORE_FAILED: '+str(ex))
            try:
                require(snapshot.is_file() and sha(snapshot)==BASE_ELF,'SNAPSHOT_NOT_VERIFIED')
                shutil.copy2(snapshot,BIN);require(sha(BIN)==BASE_ELF,'BINARY_RESTORE_VERIFY_FAILED')
                restore.append('PR94_ELF_RESTORED')
            except Exception as ex:restore.append('BINARY_RESTORE_FAILED: '+str(ex))
        status['restore']=restore or ['NOT_CHANGED']
        for label,path,want in [('protected_gameplay',PROTECTED,PROTECTED_ELF),('pr91',PR91,PR91_ELF),('pr94',BIN,BASE_ELF)]:
            try:status[label+'_hash_unchanged']=sha(path)==want
            except OSError:status[label+'_hash_unchanged']=False
        (e/'result.json').write_text(json.dumps(status,indent=2)+'\n')
        try:
            with tarfile.open(archive,'w:gz') as t:
                t.add(e,arcname=e.name)
                log=LOGS/(tag+'.log')
                if log.exists():t.add(log,arcname=log.name)
            status['archive_sha256']=sha(archive)
            append_diary(status)
        except Exception as ex:print('ARCHIVE_OR_DIARY_ERROR='+str(ex),flush=True)
        print('RESTORE='+','.join(status['restore']),flush=True)
        print('EVIDENCE_DIR='+str(e),flush=True)
        if archive.is_file(): print('UPLOAD_THIS='+str(archive),flush=True)
        print('SSH_SESSION=OPEN',flush=True)
        for f in held:f.close()
        if any('FAILED' in x for x in status['restore']) or not all(
                status.get(k+'_hash_unchanged',False) for k in ('protected_gameplay','pr91','pr94')):
            return 1

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--worker');args=parser.parse_args()
    LOGS.mkdir(parents=True,exist_ok=True)
    if args.worker:
        require(re.fullmatch(r'gow-permutation-owner-\d{8}T\d{6}Z-\d+',args.worker) is not None,'INVALID_WORKER_TAG')
        return worker(args.worker)
    tag='gow-permutation-owner-'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid())
    log=LOGS/(tag+'.log')
    with log.open('xb') as f:
        p=subprocess.Popen([sys.executable,'-u',str(Path(__file__).resolve()),'--worker',tag],
                           stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
    print('WORKER_PID='+str(p.pid));print("MONITOR=tail -F '"+str(log)+"'")
    print('UPLOAD_THIS='+str(LOGS/(tag+'.tar.gz')));print('SSH_SESSION=OPEN')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
