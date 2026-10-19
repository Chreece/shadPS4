#!/usr/bin/env python3
"""Execute source-exact GoW GetProgram baseline versus corrected variation selection.

Both old and new Program definitions and PipelineCache::GetProgram methods are
compiled under a small deterministic C++ test environment. Unlike the earlier
API surface test, BOTH binaries MUST compile; the original binary then
demonstrates a wrong Shader::Info-to-SPIR-V binding count, while the correction
must return the metadata owned by the matching shader permutation.

The mocked Shader::Info and Vulkan module model only a source shader with
one image having different binding counts (one vs. 97), rather than claiming
real PS4 shader output. This establishes the source-control-flow defect, NOT
that GoW's actual scene uses a second permutation. Does not build shadPS4.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

PROGRAM_FIRST = "struct Program {"
PROGRAM_LAST = "struct DrawIndirectParams {"
RUNTIME_FIRST = "PipelineCache::Result PipelineCache::GetProgram("
RUNTIME_LAST = "std::optional<vk::ShaderModule> PipelineCache::ReplaceShader("

CXX_PREFIX = r"""
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <memory>
#include <ranges>
#include <span>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

using u32 = std::uint32_t;
using u64 = std::uint64_t;
namespace vk {
struct ShaderModule {
    int id{};
    std::size_t compiled_image_slots{};
    constexpr explicit operator bool() const noexcept { return id != 0; }
    bool operator==(const ShaderModule&) const = default;
};
}
namespace Shader {
enum class HwStage { Compute };
enum class SwStage { Compute };
struct ShaderParams {
    u64 hash{};
    std::array<u32,1> user_data{};
    std::vector<u32> code{};
    u64 Base() const { return 0; }
};
struct RuntimeInfo {};
struct Profile {};
namespace Backend {
struct Bindings {
    std::size_t buffer{};
    std::size_t unified{};
};
}
namespace Gcn {
struct FetchShaderData {
    bool Empty() const { return true; }
};
}
struct Info;
struct Image {
    std::size_t slots{};
    std::size_t NumBindings(const Info&) const { return slots; }
};
struct Info {
    u64 pgm_base{};
    std::array<u32,1> user_data{};
    std::vector<int> buffers{};
    std::vector<Image> images{};
    std::vector<int> samplers{};
    Info() = default;
    Info(HwStage, SwStage, ShaderParams p)
       : pgm_base(p.Base()), user_data(p.user_data),
         images{{std::size_t(p.user_data[0])}} {}
    void RefreshFlatBuf() {}
    void AddBindings(Backend::Bindings& binding) const {
        binding.buffer += buffers.size();
        binding.unified += buffers.size() + images.size() + samplers.size();
    }
};
struct StageSpecialization {
    const Info* info{};
    u32 guest_layout{};
    Gcn::FetchShaderData fetch_shader_data{};
    StageSpecialization() = default;
    StageSpecialization(const Info& i, RuntimeInfo, const Profile&,
                        Backend::Bindings)
       : info(&i), guest_layout(i.user_data[0]) {}
    bool Valid() const { return info != nullptr; }
    bool operator==(const StageSpecialization& other) const {
        return guest_layout == other.guest_layout;
    }
};
}
namespace boost { namespace container {
template <typename T, std::size_t N>
using small_vector = std::vector<T>;
}}
namespace Vulkan {
using Shader::HwStage;
using Shader::SwStage;
"""

CXX_MIDDLE = r"""
// Emulate the existing tsl::robin_map iterator's .value() convention while
// preserving the exact source in PipelineCache::GetProgram below.
class ProgramMap {
    std::unordered_map<u64,std::unique_ptr<Program>> objects{};
public:
    struct Handle {
        std::unique_ptr<Program>* ptr{};
        std::unique_ptr<Program>& value() { return *ptr; }
    };
    auto try_emplace(u64 key) {
        auto [iter, created] = objects.try_emplace(key);
        return std::pair<Handle,bool>{Handle{&iter->second},created};
    }
};
u64 HashCombine(u64 a, std::size_t b) {
    return (a << 5) ^ (a >> 2) ^ b;
}
class PipelineCache {
public:
    using Result = std::tuple<const Shader::Info*,vk::ShaderModule,u64>;
    Result GetProgram(HwStage stage, SwStage sw_stage,
                      const Shader::ShaderParams& params,
                      Shader::Backend::Bindings& binding);
    ProgramMap program_cache{};
    Shader::Profile profile{};
    Shader::Gcn::FetchShaderData* fetch_shader{};
    std::size_t compilation_count{};
private:
    Shader::RuntimeInfo BuildRuntimeInfo(HwStage, SwStage) { return {}; }
    vk::ShaderModule CompileModule(Shader::Info& info,
                                   Shader::RuntimeInfo&,
                                   const std::span<const u32>&,
                                   std::size_t,
                                   Shader::Backend::Bindings& binding) {
        ++compilation_count;
        std::size_t slots{};
        for (const auto& image : info.images) {
            slots += image.NumBindings(info);
        }
        binding.buffer += info.buffers.size();
        binding.unified += info.buffers.size()+info.samplers.size()+slots;
        return {int(compilation_count),slots};
    }
    void RegisterShaderMeta(const Shader::Info&, const Shader::Gcn::FetchShaderData&,
                            const Shader::StageSpecialization&,u64,std::size_t) {}
};
"""

CXX_SUFFIX = r"""
} // namespace Vulkan

int main() {
    Vulkan::PipelineCache cache;
    auto make = [](u32 slots) {
        Shader::ShaderParams p{};
        p.hash = 0x123456789abcULL;
        p.user_data[0] = slots;
        return p;
    };
    Shader::Backend::Bindings initial{};
    const auto [first_info,first_module,first_hash] =
        cache.GetProgram(Shader::HwStage::Compute, Shader::SwStage::Compute,
                         make(1), initial);
    assert(first_info && first_module.compiled_image_slots==1);
    assert(first_info->images.front().NumBindings(*first_info)==1);
    assert(cache.compilation_count==1);

    Shader::Backend::Bindings second_binding{};
    const auto [second_info,second_module,second_hash] =
        cache.GetProgram(Shader::HwStage::Compute, Shader::SwStage::Compute,
                         make(97), second_binding);
    assert(second_info && second_module.compiled_image_slots==97);
    assert(cache.compilation_count==2);
    const auto second_actual=second_info->images.front().NumBindings(*second_info);
    const bool second_mismatch=second_actual!=second_module.compiled_image_slots;

    Shader::Backend::Bindings reused_binding{};
    const auto [reused_info,reused_module,reused_hash] =
        cache.GetProgram(Shader::HwStage::Compute, Shader::SwStage::Compute,
                         make(97), reused_binding);
    assert(reused_info && reused_module.id==second_module.id);
    assert(cache.compilation_count==2);
    const auto reused_actual=reused_info->images.front().NumBindings(*reused_info);
    const bool reuse_mismatch=reused_actual!=reused_module.compiled_image_slots;
    const bool binding_count_mismatch=reused_binding.unified!=
                                      reused_module.compiled_image_slots;

    std::cout<<"SECOND_VARIANT_METADATA_MISMATCH="<<second_mismatch<<"\n";
    std::cout<<"REUSED_VARIANT_METADATA_MISMATCH="<<reuse_mismatch<<"\n";
    std::cout<<"REUSED_BINDING_COUNT_MISMATCH="<<binding_count_mismatch<<"\n";
    std::cout<<"COMPILED_VARIANTS="<<cache.compilation_count<<"\n";
    std::cout<<"SECOND_SPIRV_IMAGE_SLOTS="<<second_module.compiled_image_slots<<"\n";
    std::cout<<"SECOND_INFO_IMAGE_SLOTS="<<second_actual<<"\n";
    std::cout<<"REUSED_BINDING_UNIFIED="<<reused_binding.unified<<"\n";
}
"""

def file_bytes(path:Path)->bytes:
    if not path.is_file() or path.is_symlink():
        raise AssertionError(f"UNSAFE_OR_MISSING_FILE={path}")
    return path.read_bytes()

def segment(source:bytes,first:str,last:str)->str:
    text=source.decode("utf8")
    if text.count(first)!=1 or text.count(last)<1:
        raise AssertionError(f"SOURCE_BOUNDARY_NOT_UNIQUE={first}")
    a=text.index(first)
    b=text.index(last,a+len(first))
    return text[a:b]

def compile_replay(header:bytes, cache:bytes)->str:
    return CXX_PREFIX + segment(header,PROGRAM_FIRST,PROGRAM_LAST) + (
        CXX_MIDDLE + "\n" + segment(cache,RUNTIME_FIRST,RUNTIME_LAST) +
        CXX_SUFFIX)

def parse_lines(out:str):
    pairs={}
    for line in out.splitlines():
        if "=" in line:
            key,value=line.strip().split("=",1)
            pairs[key]=value
    return pairs

def main():
    p=argparse.ArgumentParser()
    for arg in ("old-header","old-cache","new-header","new-cache"):
        p.add_argument("--"+arg,type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    cxx=shutil.which("g++") or shutil.which("clang++")
    if not cxx:raise AssertionError("CXX_COMPILER_NOT_INSTALLED")
    files={key:Path(getattr(a,key.replace("-","_"))) for key in (
        "old-header","old-cache","new-header","new-cache")}
    data={key:file_bytes(file) for key,file in files.items()}
    a.out.mkdir(parents=True,exist_ok=True)
    observed={}
    with tempfile.TemporaryDirectory(prefix=".gow-perm-runtime-",dir=a.out) as tmp:
        folder=Path(tmp)
        for label,prefix in (("BASELINE","old"),("CORRECTED","new")):
            cpp=folder/f"{prefix}.cpp"
            binfile=folder/f"{prefix}.bin"
            cpp.write_text(compile_replay(data[prefix+"-header"],data[prefix+"-cache"]))
            built=subprocess.run(
                [cxx,"-std=c++20","-O0","-Wall","-Wextra","-Werror",
                 str(cpp),"-o",str(binfile)],
                capture_output=True,text=True,timeout=50)
            if built.returncode!=0:
                raise AssertionError(label+"_FAILED_TO_COMPILE:\n"+built.stderr[:5500])
            run=subprocess.run([str(binfile)],capture_output=True,text=True,timeout=10)
            if run.returncode!=0:
                raise AssertionError(label+"_RUNTIME_MOCK_CRASH:\n"+run.stderr[:1500])
            observed[label]=parse_lines(run.stdout)
    expected={
        "BASELINE":{"SECOND_VARIANT_METADATA_MISMATCH":"1",
                    "REUSED_VARIANT_METADATA_MISMATCH":"1",
                    "REUSED_BINDING_COUNT_MISMATCH":"1",
                    "COMPILED_VARIANTS":"2",
                    "SECOND_SPIRV_IMAGE_SLOTS":"97",
                    "SECOND_INFO_IMAGE_SLOTS":"1",
                    "REUSED_BINDING_UNIFIED":"1"},
        "CORRECTED":{"SECOND_VARIANT_METADATA_MISMATCH":"0",
                     "REUSED_VARIANT_METADATA_MISMATCH":"0",
                     "REUSED_BINDING_COUNT_MISMATCH":"0",
                     "COMPILED_VARIANTS":"2",
                     "SECOND_SPIRV_IMAGE_SLOTS":"97",
                     "SECOND_INFO_IMAGE_SLOTS":"97",
                     "REUSED_BINDING_UNIFIED":"97"},
    }
    for label,subset in expected.items():
        for key,value in subset.items():
            if observed[label].get(key)!=value:
                raise AssertionError("SEMANTIC_TEST_UNEXPECTED_RESULT "+label+
                                     " "+key+" got="+str(observed[label].get(key))+
                                     " expected="+value)
    result={
        "test":"GoW actual Program struct and GetProgram control-flow semantic replay",
        "old_and_new_compile":True,
        "baseline_runtime_demonstrates_wrong_metadata":True,
        "candidate_runtime_returns_correct_owned_metadata":True,
        "model_scope":"Mock shader resource layout and Vulkan object; real GoW source control flow",
        "not_proven":"Actual GoW game shader compiles a second variation in the failing scene",
        "emulator_full_build":False,"gameplay":False,
        "source_sha256":{key:hashlib.sha256(buf).hexdigest() for key,buf in data.items()},
        "observed":observed,
    }
    report=a.out/"gow-permutation-behavior-proof.json"
    report.write_text(json.dumps(result,indent=2)+"\n")
    print("BOTH_REAL_SOURCE_RUNTIME_VARIANTS_COMPILE=PASS")
    print("BASELINE_SECOND_PERMUTATION_WRONG_RESOURCE_INFO=PROVEN_IN_NATIVE_REPLAY")
    print("CANDIDATE_SECOND_PERMUTATION_OWNS_MATCHING_INFO=PASS")
    print("CACHED_BINDINGS_AND_PERMUTATION_REUSE=PASS")
    print("FULL_EMULATOR_COMPILE=NOT_YET_RUN")
    print("GO_W_GAMEPLAY_SHADER_MULTIPERMUTATION_RELEVANCE=NOT_YET_VERIFIED")
    print("GAME_SAVES_SOURCE_BINARY_SSH_UNTOUCHED=YES")
    print("UPLOAD_REPORT="+str(report))

if __name__=="__main__":
    main()
