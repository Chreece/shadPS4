#!/usr/bin/env python3
"""Native source-exact GoW shader-permutation ownership regression.

Compiles the actual Program struct extracted from old/current GoW
vk_pipeline_cache.h with tiny mocks for Vulkan/Shader types. Requires
the old source to FAIL at an unsupported owned permutation, and the
fixed code to compile and PASS ownership, relocation, and cache holes.
Checks actual GetProgram + LoadPipelineStage paths and unchanged
shader-cache schema. No shadPS4 build, game, GPU, saves or service edits.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

PROGRAM_BEGIN = "struct Program {"
PROGRAM_END = "struct DrawIndirectParams {"
PFX = r"""
#include <algorithm>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <utility>
#include <vector>
namespace vk {
struct ShaderModule {
    int id = 0;
    explicit operator bool() const { return id != 0; }
    bool operator==(const ShaderModule&) const = default;
};
}
namespace Shader {
enum class HwStage { Compute };
enum class SwStage { Compute };
struct ShaderParams { int resource_layout = 0; };
struct Info {
    int resource_layout = 0;
    Info() = default;
    Info(HwStage, SwStage, ShaderParams params)
      : resource_layout(params.resource_layout) {}
};
struct StageSpecialization {
    Info const* info = nullptr;
    int layout = 0;
    bool Valid() const { return info != nullptr; }
};
}
namespace boost { namespace container {
template <class T, std::size_t N>
using small_vector = std::vector<T>;
}}
"""
SUFFIX = r"""
int main() {
    Program program(Shader::HwStage::Compute, Shader::SwStage::Compute, {1});
    assert(program.info.resource_layout == 1);
    Shader::StageSpecialization first{&program.info, 1};
    program.AddPermut({101}, std::move(first));
    assert(&program.modules[0].GetInfo(program.info) == &program.info);
    assert(program.modules[0].spec.info == &program.info);
    assert(program.modules[0].GetInfo(program.info).resource_layout == 1);

    // A different SPIR-V layout MUST outlive its temporary compiler object.
    auto own = std::make_unique<Shader::Info>();
    own->resource_layout = 97;
    const auto* original_ptr = own.get();
    Shader::StageSpecialization second{own.get(), 97};
    program.AddPermut({202}, std::move(second), std::move(own));
    assert(!own);
    assert(program.modules[1].spec.info == original_ptr);
    assert(&program.modules[1].GetInfo(program.info) == original_ptr);
    assert(program.modules[1].GetInfo(program.info).resource_layout == 97);

    // Caches may load a permutation at a noncontiguous index; growing the
    // container must not invalidate an owned Info pointer.
    auto separate = std::make_unique<Shader::Info>();
    separate->resource_layout = 3;
    const auto* third_ptr = separate.get();
    Shader::StageSpecialization third{separate.get(), 3};
    program.InsertPermut({303}, std::move(third), 6, std::move(separate));
    assert(program.modules.size() == 7);
    assert(program.modules[2].spec.Valid() == false);
    assert(program.modules[6].spec.info == third_ptr);
    assert(&program.modules[6].GetInfo(program.info) == third_ptr);
    assert(program.modules[1].spec.info == original_ptr);
    assert(program.modules[1].GetInfo(program.info).resource_layout == 97);
    assert(program.modules[6].GetInfo(program.info).resource_layout == 3);
    return 0;
}
"""

def checked_source(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"UNSAFE_OR_MISSING_SOURCE={path}")
    return path.read_text()

def mock_translation_unit(header: str) -> str:
    a = header.find(PROGRAM_BEGIN)
    b = header.find(PROGRAM_END, a)
    if a < 0 or b <= a or header.count(PROGRAM_BEGIN) != 1:
        raise AssertionError("PROGRAM_STRUCT_CANNOT_BE_EXTRACTED_UNIQUELY")
    return PFX + "\n" + header[a:b] + "\n" + SUFFIX

def between(src: str, begin: str, end: str) -> str:
    a = src.index(begin)
    b = src.index(end, a+len(begin))
    return src[a:b]

def check_runtime_source(cache_cpp: str, serialization_cpp: str):
    active = between(cache_cpp, "PipelineCache::Result PipelineCache::GetProgram(",
                     "std::optional<vk::ShaderModule> PipelineCache::ReplaceShader(")
    load = between(serialization_cpp, "bool PipelineCache::LoadPipelineStage(",
                   "void PipelineCache::WarmUp()")
    keys = {
        "uses_saved_info": "saved.GetInfo(program->info)" in active,
        "matches_each_variation": "for (size_t perm_idx = 0; perm_idx < program->modules.size(); ++perm_idx)" in active,
        "constructs_owned_info": "auto new_info = std::make_unique<Shader::Info>" in active,
        "stores_owned_info": "std::move(spec), std::move(new_info)" in active,
        "registers_real_info": "RegisterShaderMeta(*new_info," in active,
        "returns_selected_info": "return std::make_tuple(selected_info, module, perm_hash);" in active,
        "counts_actual_image_array_slots": "image.NumBindings(info)" in active,
        "load_owns_serialized_info": "spec.info = own_info.get();" in load,
        "load_selects_metadata": "infos[stage] = selected_info;" in load,
        "load_rejects_incompatible_index": "resident_program.modules[perm_idx].spec.Valid()" in load,
    }
    return keys

def versions(src: str):
    import re
    return {name: int(re.search(rf"\b{name}\s*=\s*(\d+)u;", src).group(1))
            for name in ("ShaderBinaryVersion","ShaderMetaVersion","PipelineKeyVersion")}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-header",type=Path,required=True)
    ap.add_argument("--old-serialization",type=Path,required=True)
    ap.add_argument("--candidate-header",type=Path,required=True)
    ap.add_argument("--candidate-cache",type=Path,required=True)
    ap.add_argument("--candidate-serialization",type=Path,required=True)
    ap.add_argument("--logs",type=Path,required=True)
    args=ap.parse_args()
    old=checked_source(args.old_header)
    pre_serial=checked_source(args.old_serialization)
    new=checked_source(args.candidate_header)
    runtime=checked_source(args.candidate_cache)
    serialization=checked_source(args.candidate_serialization)
    a=versions(pre_serial);b=versions(serialization)
    if a!=b or a!={"ShaderBinaryVersion":9,"ShaderMetaVersion":6,"PipelineKeyVersion":3}:
        raise AssertionError(f"SHADER_CACHE_SCHEMA_CHANGED {a} => {b}")
    checks=check_runtime_source(runtime,serialization)
    if not all(checks.values()):
        raise AssertionError("LIVE_SHADER_PERMUTATION_CONTRACT_FAILURE="+json.dumps(checks))
    cxx=shutil.which("g++") or shutil.which("clang++")
    if not cxx:raise RuntimeError("CXX_COMPILER_NOT_AVAILABLE")
    args.logs.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".gow-perm-mock-",dir=args.logs) as d:
        root=Path(d)
        results={}
        for label,source in (("BASELINE",old),("CORRECTED",new)):
            file=root/(label.lower()+".cpp")
            binary=root/(label.lower())
            file.write_text(mock_translation_unit(source))
            build=subprocess.run([cxx,"-std=c++20","-O0","-Wall","-Wextra","-Werror",
                                  str(file),"-o",str(binary)],
                                  text=True,capture_output=True,timeout=35)
            results[label]={"compilation_rc":build.returncode,
                            "stderr_excerpt":build.stderr[:3500]}
            if label=="BASELINE":
                if build.returncode==0:
                    raise AssertionError("BASELINE_TEST_DID_NOT_FAIL")
                if "GetInfo" not in build.stderr and "AddPermut" not in build.stderr:
                    raise AssertionError("BASELINE_FAILED_FOR_UNRELATED_REASON="+build.stderr[:800])
            else:
                if build.returncode:
                    raise AssertionError("FIXED_PROGRAM_COMPILE_FAILED="+build.stderr[:1700])
                run=subprocess.run([str(binary)],text=True,capture_output=True,timeout=10)
                results[label]["native_result"]=run.returncode
                if run.returncode:
                    raise AssertionError("OWNED_INFO_MOCK_RUN_FAILED="+run.stderr)
    path=args.logs/"gow-permutation-info-native-proof.json"
    report={"test":"source-exact C++ Program ownership regression",
            "old_fails_at_owned_metadata":True,
            "corrected_compiles_and_runs":True,
            "same_cache_serialization_versions":a,
            "live_path_checks":checks,"compile_results":results,
            "source_hashes":{str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (args.old_header,args.old_serialization,args.candidate_header,
                          args.candidate_cache,args.candidate_serialization)},
            "full_emulator_build":False,"gameplay_test":False,
            "practical_improvement_proven":False}
    path.write_text(json.dumps(report,indent=2)+"\n")
    print("OLD_GOW_PROGRAM_NATIVE_TEST=EXPECTED_FAIL")
    print("CORRECTED_GOW_PROGRAM_NATIVE_TEST=PASS")
    print("CACHE_FORMAT_UNCHANGED=PASS versions="+json.dumps(a,sort_keys=True))
    print("PERMUTATION_RUNTIME_AND_LOAD_CONTRACT=PASS checks="+str(len(checks)))
    print("NO_GAME_NO_EMULATOR_BUILD_NO_SOURCE_MUTATION=YES")
    print("REPORT="+str(path))

if __name__=="__main__":
    main()
