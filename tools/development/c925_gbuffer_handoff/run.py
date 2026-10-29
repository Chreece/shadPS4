#!/usr/bin/env python3
"""GoT menu: trace actual GPU G-buffer producer -> sampled-image consumer addresses.

One renderer object on pinned working C925 baseline; strict checkpoint/rollback,
same 118+2 second game/profile and conditional 8-GiB Ollama guard. Diagnostic
only: DOES NOT modify shader outputs, material IDs, bound T#/S#, or draw calls.
"""
from __future__ import annotations
import hashlib, re, sys
from pathlib import Path

P=Path.home()/"projects/shadps4"
BASE=P/"GhostOfTsushima/experiments/c925-pr95-scalar-bounds-20261028T112756671630889Z"
RUNNER=P/"scripts/got-texture-overlap.Xah6oZUt/run.py"
SRC=BASE/"source/src/video_core/renderer_vulkan/vk_rasterizer.cpp"
OBJ=BASE/"build/CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o"
PROVEN="89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec"
PRODUCER_MARK="GBUFFER_TARGET_PRODUCED"
CONSUMER_MARK="GBUFFER_TEXTURE_READ"
INDEXED_MARK="GBUFFER_INDEXED_TEXTURE"

def verify(ok,why):
 if not ok:raise RuntimeError(why)

def patch(s):
 if PRODUCER_MARK in s or CONSUMER_MARK in s:
  raise RuntimeError("GBUFFER_TRACE_ALREADY_PATCHED")
 namespace="namespace Vulkan {"
 verify(s.count(namespace)==1,"VULKAN_NAMESPACE_EXPECTED_ONE")
 inject=r'''
// Transient GPU evidence only: track attachment addresses written in MRT
// passes and recognize those actual images at subsequent sample bindings.
// No per-game, shader-hash, texture-index or color-output changes.
static thread_local std::unordered_set<u64> g_gbuffer_targets;
static thread_local std::unordered_set<u64> g_gbuffer_sample_reports;
static thread_local std::unordered_set<u64> g_gbuffer_producer_reports;
'''
 s=s.replace(namespace,namespace+inject,1)
 if "#include <unordered_set>" not in s:
  find="#include <unordered_map>"
  verify(s.count(find)==1,"EXISTING_UNORDERED_MAP_INCLUDE_CHANGED")
  s=s.replace(find,find+"\n#include <unordered_set>",1)

 begin="void Rasterizer::PrepareRenderState("
 finish="static std::pair<u32, u32> GetDrawOffsets("
 verify(begin in s and finish in s,"PRODUCER_FUNCTION_BOUNDARY_CHANGED")
 a=s.index(begin);b=s.index(finish,a);body=s[a:b]
 anchor="        image.binding.is_target = 1u;"
 # The source also marks a depth target as is_target later. Instrument
 # only the first (color-target loop) occurrence, never the depth target.
 verify(body.count(anchor)>=1,"PRODUCER_BINDING_ANCHOR_CHANGED")
 depth_start=body.find("if ((regs.depth_control.depth_enable")
 if depth_start>=0:
  verify(body.index(anchor)<depth_start,"COLOR_PRODUCER_NOT_BEFORE_DEPTH_TARGET")
 producer=r'''
        // A genuine multi-render-target fragment pass is a potential G-buffer
        // producer. Retain its guest address until a later texture bind.
        if (std::popcount(static_cast<u32>(key.mrt_mask)) > 1u) {
            const u64 guest = image.info.guest_address;
            if (guest && g_gbuffer_targets.size() < 16384u) {
                g_gbuffer_targets.insert(guest);
                const u64 record_key = (u64(pipeline->GetStage(Shader::SwStage::Fragment).pgm_hash)
                                       << 32) ^ (u64(cb) << 56) ^ (guest >> 6);
                if (g_gbuffer_producer_reports.size() < 2400u &&
                    g_gbuffer_producer_reports.insert(record_key).second) {
                    LOG_INFO(Render_Vulkan,
                             "GBUFFER_TARGET_PRODUCED shader={:#x} rt={} guest={:#x} "
                             "width={} height={} bits={} format={}",
                             pipeline->GetStage(Shader::SwStage::Fragment).pgm_hash,
                             cb, guest, image.info.size.width, image.info.size.height,
                             image.info.num_bits,
                             static_cast<u32>(image.info.pixel_format));
                }
            }
        }
'''
 body=body.replace(anchor,anchor+producer,1);s=s[:a]+body+s[b:]

 begin="void Rasterizer::BindTextures("
 finish="RenderState Rasterizer::BeginRendering("
 verify(begin in s and finish in s,"SAMPLE_BINDING_FUNCTION_BOUNDARY_CHANGED")
 a=s.index(begin);b=s.index(finish,a);body=s[a:b]
 indexed="slot.image_id = texture_cache.FindImage(desc);\n                auto* image = &texture_cache.GetImage(slot.image_id);"
 verify(body.count(indexed)==1,"INDEXED_MATERIAL_BINDING_ANCHOR_CHANGED")
 indexed_probe=r'''
                if (stage.hw_stage == Shader::HwStage::Fragment && count <= 65u) {
                    const bool from_mrt =
                        g_gbuffer_targets.contains(image->info.guest_address);
                    const u64 record_key = (u64(stage.pgm_hash) << 32) ^
                                           (u64(i) << 43) ^
                                           (u64(source.base_address) >> 4);
                    if (g_gbuffer_sample_reports.size() < 3200u &&
                        g_gbuffer_sample_reports.insert(record_key).second) {
                        LOG_INFO(Render_Vulkan,
                                 "GBUFFER_INDEXED_TEXTURE shader={:#x} index={} total={} "
                                 "table={:#x} raw={:#x} guest={:#x} width={} height={} "
                                 "bits={} producer_match={} gpu_modified={} dirty={}",
                                 stage.pgm_hash, i, count - 1u,
                                 source.base_address, raw.Address(),
                                 image->info.guest_address,
                                 image->info.size.width, image->info.size.height,
                                 image->info.num_bits, from_mrt,
                                 True(image->flags & VideoCore::ImageFlagBits::GpuModified),
                                 True(image->flags & VideoCore::ImageFlagBits::Dirty));
                    }
                }
'''
 body=body.replace(indexed,indexed+indexed_probe,1)
 simple="image_id = texture_cache.FindImage(desc);\n            auto* image = &texture_cache.GetImage(image_id);"
 verify(body.count(simple)==1,"UNINDEXED_SAMPLE_BINDING_ANCHOR_CHANGED")
 simple_probe=r'''
            if (stage.hw_stage == Shader::HwStage::Fragment &&
                !g_gbuffer_targets.empty()) {
                const u64 guest = image->info.guest_address;
                const bool from_mrt = g_gbuffer_targets.contains(guest);
                const u64 record_key = (u64(stage.pgm_hash) << 32) ^ (guest >> 4) ^
                                       0x9e3779b97f4a7c15ull;
                if (g_gbuffer_sample_reports.size() < 3200u &&
                    g_gbuffer_sample_reports.insert(record_key).second) {
                    LOG_INFO(Render_Vulkan,
                             "GBUFFER_TEXTURE_READ shader={:#x} guest={:#x} "
                             "width={} height={} bits={} producer_match={} "
                             "gpu_modified={} dirty={}",
                             stage.pgm_hash, guest,
                             image->info.size.width, image->info.size.height,
                             image->info.num_bits, from_mrt,
                             True(image->flags & VideoCore::ImageFlagBits::GpuModified),
                             True(image->flags & VideoCore::ImageFlagBits::Dirty));
                }
            }
'''
 body=body.replace(simple,simple+simple_probe,1)
 s=s[:a]+body+s[b:]
 verify(s.count(PRODUCER_MARK)==1 and s.count(CONSUMER_MARK)==1 and
        s.count(INDEXED_MARK)==1,"TRACE_MARKERS_MISSING_OR_DUPLICATED")
 return s

def unit():
 # Validate transform and fail closed on variants, without assuming shader IDs.
 mini='''#include <unordered_map>
namespace Vulkan {
void Rasterizer::PrepareRenderState(const GraphicsPipeline* pipeline) {
    const auto& key = pipeline->GetGraphicsKey();
    for (s32 cb=0; cb<8; ++cb) {
        auto& image=texture_cache.GetImage(image_id);
        image.binding.is_target = 1u;
    }
}
static std::pair<u32, u32> GetDrawOffsets(const AmdGpu::Regs&, const Shader::Info&, const int&) { return {}; }
void Rasterizer::BindTextures(const Shader::Info& stage, Shader::Backend::Bindings& binding) {
    if (image_desc.is_guest_indexed) {
        slot.image_id = texture_cache.FindImage(desc);
                auto* image = &texture_cache.GetImage(slot.image_id);
    }
    image_id = texture_cache.FindImage(desc);
            auto* image = &texture_cache.GetImage(image_id);
}
RenderState Rasterizer::BeginRendering(const GraphicsPipeline* pipeline) { return {}; }
}
'''
 result=patch(mini)
 verify(result.count(PRODUCER_MARK)==1 and result.count(CONSUMER_MARK)==1 and
        result.count(INDEXED_MARK)==1,"TRACE_SOURCE_UNIT_FAILED")
 try:patch(result)
 except RuntimeError as exc:
  verify(str(exc)=="GBUFFER_TRACE_ALREADY_PATCHED","DOUBLE_PATCH_WRONG_ERROR")
 else:raise RuntimeError("DOUBLE_PATCH_WAS_ACCEPTED")
 print("G_BUFFER_MATERIAL_HANDOFF_SOURCE_FIXTURE=PASS",flush=True)

def git_blob(raw):
 return hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()

def main():
 unit()
 verify(SRC.is_file() and not SRC.is_symlink(),"C925_RENDERER_SOURCE_MISSING")
 actual=SRC.read_text()
 changed=patch(actual)
 verify(changed!=actual,"NO_RENDERER_TRACE_ADDED")
 print("ACTUAL_C925_RENDERER_PRODUCER_CONSUMER_ANCHORS=PASS",flush=True)
 verify(RUNNER.is_file() and not RUNNER.is_symlink(),"KNOWN_REVERSIBLE_RUNNER_MISSING")
 source=RUNNER.read_text()
 for required in ("def run(work):","RETAIN_CANDIDATE_RESTORE_BASELINE",
                  "PROVEN_ELF_HASH_CHANGED","texture-overlap-fix-"):
  verify(required in source,"KNOWN_RUNNER_FUNCTIONAL_GATE_MISSING:"+required)
 source=source.replace("texture-overlap-fix-","gpu-material-handoff-")
 source=source.replace("Componentwise mip/layer image-containment and incompatible-overlap correction",
                       "Trace genuine multi-RT material targets and fragment texture consumers")
 source=source.replace("https://github.com/shadps4-emu/shadPS4/pull/5457",
                       "https://github.com/Chreece/shadPS4")
 source=source.replace("a/src/video_core/texture_cache/texture_cache.cpp",
                       "a/src/video_core/renderer_vulkan/vk_rasterizer.cpp")
 source=source.replace("b/src/video_core/texture_cache/texture_cache.cpp",
                       "b/src/video_core/renderer_vulkan/vk_rasterizer.cpp")
 source=source.replace("STAGE=COMPILE_TEXTURE_CACHE_OBJECT_AND_RELINK",
                       "STAGE=COMPILE_RASTERIZER_OBJECT_AND_RELINK")
 source=source.replace("STAGE=MENU_120_SECONDS_NO_INPUT",
                       "STAGE=G_BUFFER_TRACE_MENU_120_SECONDS_NO_INPUT")
 namespace={"__name__":"c925_material_handoff","__file__":str(RUNNER)}
 exec(compile(source,str(RUNNER),"exec"),namespace)
 verify(callable(namespace.get("run")) and namespace.get("BASE_SHA")==PROVEN,
        "BASELINE_RUNNER_IDENTITY_CHANGED")
 verify(namespace["SRC"].name=="texture_cache.cpp" and
        namespace["OBJ"].name=="texture_cache.cpp.o",
        "EXPECTED_ONE_OBJECT_RUNNER_SHAPE_CHANGED")
 namespace["SRC"]=SRC
 namespace["OBJ"]=OBJ
 namespace["patch"]=patch
 namespace["unit"]=unit
 print("BUILD=ONE_EXISTING_RASTERIZER_OBJECT_AND_RELINK",flush=True)
 print("GPU=MIN_8_GIB_OLLAMA_CONDITIONAL_UNLOAD_RESTORE",flush=True)
 print("MENU=120_SECONDS_NO_INPUT; NO_FAKE_SHADERS_OR_DRAW_HACKS",flush=True)
 print("RESTORE=EXACT_SOURCE_OBJECT_ELF",flush=True)
 return namespace["run"](RUNNER.parent)

if __name__=="__main__":
 sys.exit(main())
