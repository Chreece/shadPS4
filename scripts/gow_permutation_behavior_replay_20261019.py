#!/usr/bin/env python3
"""Replay original and corrected GoW GetProgram and Program source with a C++20 Vulkan mock.
Does not edit shadPS4 sources, shader caches, or the protected executable.
"""
import hashlib
import pathlib
import subprocess
import sys

def run(cmd):
    print('+', ' '.join(map(str, cmd)), flush=True)
    subprocess.run(cmd, check=True)

def git_file(repo, ref, path):
    return subprocess.run(['git','-C',str(repo),'show',f'{ref}:{path}'],
                          check=True, text=True, capture_output=True).stdout

def extract_braced(source, marker):
    begin = source.find(marker)
    if begin < 0:
        raise RuntimeError(f'Marker missing: {marker}')
    brace = begin + marker.rfind('{') if '{' in marker else source.find('{', begin+len(marker))
    if brace < 0:
        raise RuntimeError(f'Brace missing: {marker}')
    depth = 0
    for pos in range(brace, len(source)):
        if source[pos] == '{':
            depth += 1
        elif source[pos] == '}':
            depth -= 1
            if depth == 0:
                return source[begin:pos+1]
    raise RuntimeError(f'Unterminated source block: {marker}')

MOCK = r'''
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <memory>
#include <ranges>
#include <stdexcept>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>
using u64 = std::uint64_t;
namespace vk {
struct ShaderModule {
    int id = 0;
    explicit operator bool() const { return id != 0; }
};
}
namespace Shader {
enum class HwStage { Vertex };
enum class SwStage { Vertex };
struct ShaderParams {
    u64 hash = 0xCAFE;
    u64 user_data = 1;
    std::vector<std::uint32_t> code{0x10203040};
    u64 Base() const { return 0x1000; }
};
namespace Backend { struct Bindings { std::size_t buffer = 0, unified = 0; }; }
struct Info;
struct Image {
    std::size_t slots = 0;
    std::size_t NumBindings(const Info&) const { return slots; }
};
struct RuntimeInfo {};
struct Profile {};
struct FetchData { bool Empty() const { return true; } };
struct Info {
    u64 pgm_base = 0, user_data = 0;
    std::vector<int> buffers{}, samplers{};
    std::vector<Image> images{};
    Info() = default;
    Info(HwStage, SwStage, const ShaderParams& p)
        : pgm_base(p.Base()), user_data(p.user_data), images{{static_cast<std::size_t>(p.user_data)}} {}
    void RefreshFlatBuf() {}
    // Original image-count behavior differs from array/mip slot accounting.
    void AddBindings(Backend::Bindings& b) const {
        b.buffer += buffers.size();
        b.unified += buffers.size() + samplers.size() + images.size();
    }
};
struct StageSpecialization {
    bool valid = false;
    u64 variant = 0;
    FetchData fetch_shader_data{};
    StageSpecialization() = default;
    StageSpecialization(Info& i, const RuntimeInfo&, const Profile&, const Backend::Bindings&)
        : valid(true), variant(i.user_data) {}
    bool Valid() const { return valid; }
    bool operator==(const StageSpecialization& other) const {
        return valid == other.valid && variant == other.variant;
    }
};
}
using Shader::HwStage;
using Shader::SwStage;
namespace Vulkan {
// SOURCE_PROGRAM_STRUCT
struct ProgramCacheMock {
    struct Entry { std::unique_ptr<Program> ptr{}; };
    std::unordered_map<u64, Entry> entries;
    struct Iterator {
        Entry* entry;
        std::unique_ptr<Program>& value() { return entry->ptr; }
    };
    std::pair<Iterator, bool> try_emplace(u64 hash) {
        auto [it, added] = entries.try_emplace(hash);
        return {Iterator{&it->second}, added};
    }
};
u64 HashCombine(u64 a, u64 b) { return (a * 1315423911ULL) ^ b; }
struct PipelineCache {
    using Result = std::tuple<const Shader::Info*, vk::ShaderModule, u64>;
    ProgramCacheMock program_cache{};
    Shader::Profile profile{};
    Shader::FetchData* fetch_shader = nullptr;
    std::unordered_map<int, std::size_t> actual_module_slots{};
    int modules_compiled = 0;
    Shader::RuntimeInfo BuildRuntimeInfo(HwStage, SwStage) { return {}; }
    vk::ShaderModule CompileModule(Shader::Info& i, Shader::RuntimeInfo&,
                                   const std::vector<std::uint32_t>&, std::size_t,
                                   Shader::Backend::Bindings& b) {
        std::size_t count = i.buffers.size() + i.samplers.size();
        for (auto& img : i.images) count += img.NumBindings(i);
        b.unified += count;
        b.buffer += i.buffers.size();
        int id = ++modules_compiled;
        actual_module_slots[id] = count;
        return {id};
    }
    void RegisterShaderMeta(Shader::Info&, const Shader::FetchData&,
                            const Shader::StageSpecialization&, u64, std::size_t) {}
    Result GetProgram(HwStage, SwStage, const Shader::ShaderParams&,
                      Shader::Backend::Bindings&);
};
// SOURCE_GETPROGRAM_FUNCTION
}
static std::size_t slots(const Shader::Info& info) {
    std::size_t total = info.buffers.size() + info.samplers.size();
    for (const auto& image : info.images) total += image.NumBindings(info);
    return total;
}
int main() {
    Vulkan::PipelineCache c;
    Shader::ShaderParams p;
    Shader::Backend::Bindings b1{};
    auto [i1, m1, h1] = c.GetProgram(HwStage::Vertex, SwStage::Vertex, p, b1);
    const auto first_ok = (slots(*i1) == 1 && c.actual_module_slots.at(m1.id) == 1);
    p.user_data = 97;
    Shader::Backend::Bindings b2{};
    auto [i2, m2, h2] = c.GetProgram(HwStage::Vertex, SwStage::Vertex, p, b2);
    const auto meta2 = slots(*i2);
    const auto actual2 = c.actual_module_slots.at(m2.id);
    Shader::Backend::Bindings b3{};
    auto [i3, m3, h3] = c.GetProgram(HwStage::Vertex, SwStage::Vertex, p, b3);
    const auto meta3 = slots(*i3);
    const auto reuse_delta = b3.unified;
    const auto no_recompile = c.modules_compiled == 2 && m2.id == m3.id && h2 == h3;
    std::cout << "FIRST_METADATA=" << slots(*i1) << " FIRST_ACTUAL=" << c.actual_module_slots.at(m1.id) << "\n";
    std::cout << "SECOND_METADATA=" << meta2 << " SECOND_ACTUAL=" << actual2 << "\n";
    std::cout << "REUSE_METADATA=" << meta3 << " REUSE_BINDING_SLOTS=" << reuse_delta << "\n";
    std::cout << "SAME_CACHED_MODULE=" << (no_recompile ? "YES" : "NO") << "\n";
    if (!first_ok || actual2 != 97 || !no_recompile || b2.unified != 97) return 4;
#if EXPECT_FIXED
    if (meta2 != 97 || meta3 != 97 || reuse_delta != 97) return 5;
    std::cout << "CORRECTED_BEHAVIOR=PASS\n";
#else
    if (meta2 != 1 || meta3 != 1 || reuse_delta != 1) return 6;
    std::cout << "BASELINE_DEFECT_REPRODUCED=PASS\n";
#endif
    return 0;
}
'''

def main():
    if len(sys.argv) != 5:
        raise SystemExit('Usage: replay.py REPO OLD_REF FIX_REF OUTPUT_DIR')
    repo, old_ref, fix_ref, out_dir = pathlib.Path(sys.argv[1]),sys.argv[2],sys.argv[3],pathlib.Path(sys.argv[4])
    out_dir.mkdir(parents=True, exist_ok=True)
    for label, ref, fixed in [('baseline',old_ref,False),('corrected',fix_ref,True)]:
        header = git_file(repo,ref,'src/video_core/renderer_vulkan/vk_pipeline_cache.h')
        implementation = git_file(repo,ref,'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp')
        program = extract_braced(header,'struct Program {')+';'
        method = extract_braced(implementation,'PipelineCache::Result PipelineCache::GetProgram(')
        if 'boost::container::small_vector<Module, MaxPermutations>' not in program:
            raise RuntimeError(f'{label}: Program container changed, refusing mock substitution')
        if 'program_cache.try_emplace(params.hash)' not in method:
            raise RuntimeError(f'{label}: GetProgram control flow changed')
        if fixed and ('saved.GetInfo(program->info)' not in method or 'std::move(new_info)' not in method):
            raise RuntimeError('Corrected branch missing expected ownership logic')
        if not fixed and 'info.AddBindings(binding)' not in method:
            raise RuntimeError('Baseline missing expected cache-hit logic')
        (out_dir/f'{label}-actual-GetProgram.cpp.txt').write_text(method+'\n')
        (out_dir/f'{label}-actual-Program.h.txt').write_text(program+'\n')
        print(f'{label.upper()}_GETPROGRAM_SHA256={hashlib.sha256(method.encode()).hexdigest()}',flush=True)
        print(f'{label.upper()}_PROGRAM_SHA256={hashlib.sha256(program.encode()).hexdigest()}',flush=True)
        adapted = program.replace('boost::container::small_vector<Module, MaxPermutations>',
                                  'std::vector<Module>')
        src = MOCK.replace('// SOURCE_PROGRAM_STRUCT',adapted)
        src = src.replace('// SOURCE_GETPROGRAM_FUNCTION',method)
        source_file = out_dir/f'{label}.cpp'
        source_file.write_text(f'#define EXPECT_FIXED {int(fixed)}\n'+src)
        exe = out_dir/label
        run(['g++','-std=c++20','-O0','-g','-Wall','-Wextra','-pedantic','-o',str(exe),str(source_file)])
        run([str(exe)])
    print('BOTH_ACTUAL_GETPROGRAM_SOURCES_COMPILE=PASS')
    print('OLD_VS_FIXED_BEHAVIOR=PASS')
    print('MOCK_SCOPE=GetProgram_control_flow_plus_Program_struct_not_full_emulator')

if __name__ == '__main__':
    try:
        main()
    except (Exception,subprocess.CalledProcessError) as err:
        print(f'REPLAY_FAILED={err}',file=sys.stderr)
        raise SystemExit(1)
