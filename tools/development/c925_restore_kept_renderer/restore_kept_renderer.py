#!/usr/bin/env python3
"""Restore already-accepted PR69/PR68 semantics in the verified C925 source.
This is a source-parity repair, not a new claim of full-scene rendering recovery.
"""
from __future__ import annotations
import difflib
import hashlib
import json
from pathlib import Path
import sys

PATHS = (
    'src/video_core/renderer_vulkan/vk_rasterizer.cpp',
    'src/video_core/texture_cache/texture_cache.cpp',
)
EXPECTED = {
    PATHS[0]: '52a3f71010dc69fd9bee56f8aa4d425d93beb646ebeb4af23f71e5260ea523e4',
    PATHS[1]: '237950c841ee99dcfdc3a49d80657a4e53cc374c97ff48ba1d3ef83449f7f4f3',
}
# Exact reference-selection algorithm already merged in Chreece/shadPS4 PR69.
STENCIL = '''        // PS4 stencil compares use test_val and REPLACE writes use op_val.
        // Vulkan has only one reference per face. These are simultaneously
        // representable when the compare and write masks have no conflicting
        // overlapping bits; preserve both guest operations in that case.
        const auto stencil_reference = [&](const AmdGpu::StencilRefMask& ref,
                                           AmdGpu::CompareFunc compare,
                                           bool uses_replace) -> u32 {
            const u32 test_val = ref.stencil_test_val;
            const u32 op_val = ref.stencil_op_val;
            if (!uses_replace || stencil_clear) {
                return test_val;
            }
            if (compare == AmdGpu::CompareFunc::Always ||
                compare == AmdGpu::CompareFunc::Never) {
                return op_val;
            }
            const u32 compare_mask = ref.stencil_mask;
            const u32 write_mask = ref.stencil_write_mask;
            const u32 overlap_conflict = (test_val ^ op_val) & compare_mask & write_mask;
            if (overlap_conflict == 0) {
                const u32 reference =
                    (test_val & compare_mask) | (op_val & write_mask & ~compare_mask);
                static std::atomic<u32> exact_reference_reports{0};
                if (test_val != op_val &&
                    exact_reference_reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
                    LOG_INFO(Render_Vulkan,
                             "StencilReferenceExactMerged: test={:#x} op={:#x} "
                             "compare_mask={:#x} write_mask={:#x} reference={:#x}",
                             test_val, op_val, compare_mask, write_mask, reference);
                }
                return reference;
            }
            static std::atomic<u32> unrepresentable_reference_reports{0};
            if (unrepresentable_reference_reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
                LOG_WARNING(Render_Vulkan,
                            "StencilReferenceUnrepresentable: test={:#x} op={:#x} "
                            "compare_mask={:#x} write_mask={:#x} conflicting_bits={:#x}",
                            test_val, op_val, compare_mask, write_mask, overlap_conflict);
            }
            // Genuinely contradictory guest compare and replace bits cannot
            // be expressed by a single Vulkan reference. Preserve the prior
            // REPLACE reference rather than falsifying guest stencil tests.
            return op_val;
        };
        dynamic_state.SetStencilReferences(
            stencil_reference(front, regs.depth_control.stencil_ref_func, front_op),
            stencil_reference(back,
                              regs.depth_control.backface_enable
                                  ? regs.depth_control.stencil_bf_func
                                  : regs.depth_control.stencil_ref_func,
                              back_op));
'''
OLD_LINEAR = 'readback_linear_images{EmulatorSettings.IsReadbackLinearImagesEnabled()}'
NEW_LINEAR = '''readback_linear_images{EmulatorSettings.IsReadbackLinearImagesEnabled() ||
                             EmulatorSettings.GetReadbacksMode() == GpuReadbacksMode::Precise}'''


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def transform(original: dict[str, bytes]) -> dict[str, bytes]:
    for path, expected in EXPECTED.items():
        if path not in original or sha(original[path]) != expected:
            raise ValueError('SOURCE_PARITY_CHANGED_NO_WRITE:' + path)
    raster, texture = (original[p].decode() for p in PATHS)
    start = '        const auto ref_conflict = [](AmdGpu::CompareFunc func, const AmdGpu::StencilRefMask& ref) {'
    end = '        dynamic_state.SetStencilWriteMasks(!stencil_clear ? front.stencil_write_mask : 0U,'
    if raster.count(start) != 1 or raster.count(end) != 1:
        raise ValueError('STENCIL_CONTEXT_AMBIGUOUS')
    a, b = raster.index(start), raster.index(end)
    if a >= b or 'Stencil test requires test_val' not in raster[a:b]:
        raise ValueError('STENCIL_OLD_BLOCK_MISMATCH')
    raster = raster[:a] + STENCIL + raster[b:]
    if '#include <atomic>' not in raster:
        raise ValueError('EXISTING_ATOMIC_INCLUDE_MISSING')
    if texture.count(OLD_LINEAR) != 1:
        raise ValueError('LINEAR_READBACK_CONTEXT_MISMATCH')
    texture = texture.replace(OLD_LINEAR, NEW_LINEAR)
    if 'Stencil test requires test_val' in raster:
        raise ValueError('LEGACY_STENCIL_SELECTION_REMAINS')
    return dict(zip(PATHS, (raster.encode(), texture.encode())))


def fixture(restored: dict[str, bytes]) -> str:
    # Compile the actual restored lambda, not a separately rewritten formula.
    raster = restored[PATHS[0]].decode()
    a = raster.index('        const auto stencil_reference = [&]')
    b = raster.index('        dynamic_state.SetStencilReferences(', a)
    choose = raster[a:b]
    texture = restored[PATHS[1]].decode()
    if texture.count(NEW_LINEAR) != 1:
        raise ValueError('MISSING_PRECISE_IMAGE_INVARIANT')
    expr = NEW_LINEAR.split('{', 1)[1].rsplit('}', 1)[0]
    return r'''#include <atomic>
#include <initializer_list>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
using u32=std::uint32_t;
namespace AmdGpu {
enum class CompareFunc { Never, Less, Equal, LessEqual, Greater, NotEqual, GreaterEqual, Always };
struct StencilRefMask { u32 stencil_test_val, stencil_op_val, stencil_mask, stencil_write_mask; };
}
template<class... Args> void consume(Args&&...) {}
#define LOG_INFO(category,...) consume(__VA_ARGS__)
#define LOG_WARNING(category,...) consume(__VA_ARGS__)
u32 reference(AmdGpu::StencilRefMask input,AmdGpu::CompareFunc compare,bool replace,bool stencil_clear) {
''' + choose + r'''
    return stencil_reference(input,compare,replace);
}
enum class GpuReadbacksMode { Disabled, Relaxed, Precise };
struct Settings {
    bool saved; GpuReadbacksMode mode;
    bool IsReadbackLinearImagesEnabled() const { return saved; }
    GpuReadbacksMode GetReadbacksMode() const { return mode; }
};
bool linear(Settings EmulatorSettings) { return ''' + expr + r'''; }
#define CHECK(x) do { ++checks; if(!(x)) { std::fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); return 1; } } while(0)
int main() {
    unsigned checks=0;
    using C=AmdGpu::CompareFunc;
    // The earlier native-proven read-only comparison: old C925 returned zero.
    CHECK(reference({0x20,0,0x20,0},C::Equal,true,false)==0x20);
    CHECK(reference({0x20,0,0x20,0},C::Equal,false,false)==0x20);
    CHECK(reference({0x20,0,0x20,0},C::Equal,true,true)==0x20);
    CHECK(reference({0x20,0,0x20,0},C::Always,true,false)==0);
    CHECK(reference({0x20,0,0x20,0},C::Never,true,false)==0);
    CHECK(reference({0x20,0,0x20,0x20},C::Equal,true,false)==0);
    std::uint32_t seed=0x3c75c5d9u;
    const auto random=[&] { seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed; };
    for(unsigned i=0;i<500000;++i) {
        AmdGpu::StencilRefMask r{random()&255u,random()&255u,random()&255u,random()&255u};
        const auto compare=static_cast<C>(random()%8);
        const bool replace=(random()&1u)!=0, clear=(random()&7u)==0;
        const u32 out=reference(r,compare,replace,clear);
        if(!replace||clear) { CHECK(out==r.stencil_test_val); }
        else if(compare==C::Always||compare==C::Never) { CHECK(out==r.stencil_op_val); }
        else if(((r.stencil_test_val^r.stencil_op_val)&r.stencil_mask&r.stencil_write_mask)==0) {
            CHECK((out&r.stencil_mask)==(r.stencil_test_val&r.stencil_mask));
            CHECK((out&r.stencil_write_mask)==(r.stencil_op_val&r.stencil_write_mask));
        } else { CHECK(out==r.stencil_op_val); }
    }
    for(bool saved:{false,true}) {
        for(auto mode:{GpuReadbacksMode::Disabled,GpuReadbacksMode::Relaxed,GpuReadbacksMode::Precise}) {
            CHECK(linear({saved,mode})==(saved||mode==GpuReadbacksMode::Precise));
        }
    }
    std::printf("RESTORED_RENDERER_INVARIANTS=PASS checks=%u\n",checks);
}
'''


def apply(root: Path, evidence: Path) -> dict:
    original = {path: (root/path).read_bytes() for path in PATHS}
    restored = transform(original)
    test = fixture(restored)
    evidence.mkdir(parents=True, exist_ok=True)
    patch = []
    manifest = {}
    for path in PATHS:
        for label, data in (('before', original[path]), ('after', restored[path])):
            backup=evidence/('source-'+label)/path
            backup.parent.mkdir(parents=True,exist_ok=True)
            backup.write_bytes(data)
        patch.extend(difflib.unified_diff(original[path].decode().splitlines(True),
                     restored[path].decode().splitlines(True), 'a/'+path, 'b/'+path))
        manifest[path]={'before':sha(original[path]), 'after':sha(restored[path])}
    (evidence/'accepted-renderer-restoration.patch').write_text(''.join(patch))
    (evidence/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (evidence/'restored_renderer_fixture.cpp').write_text(test)
    for path,data in restored.items():
        destination=root/path
        pending=destination.with_name(destination.name+'.kept-renderer-pending')
        pending.write_bytes(data)
        pending.replace(destination)
    print('SOURCE_RESTORATION=PR69_STENCIL_AND_PR68_PRECISE_LINEAR_IMAGES',flush=True)
    return manifest

if __name__=='__main__':
    apply(Path(sys.argv[1]),Path(sys.argv[2]))
