#!/usr/bin/env python3
"""Inject only bounded draw-provenance diagnostics into an exact C925 rasterizer.
Not a game/shader-specific renderer workaround. No change to guest rendering.
"""
from __future__ import annotations
import hashlib, pathlib, sys
SOURCE_SHA='ebed2441ceda3d6b3e6a6ebe1afe7469ec35f9d8a99c3b7f0feb1a3fb533b4a0'
MARK='PROBE_FRAGMENT_DRAW_V1'
PROBE=r'''// PROBE_FRAGMENT_DRAW_V1: instrumentation ONLY, never changes draw semantics.
// Bounded per-hash reporting avoids logging every draw in heavy scenes.
static void RecordFragmentDraw(const GraphicsPipeline* pipeline, const AmdGpu::Regs& regs,
                               bool indirect, bool indexed, u32 requested) {
    const auto& key = pipeline->GetGraphicsKey();
    constexpr u32 fs_index = static_cast<u32>(Shader::SwStage::Fragment);
    const bool fragment_enabled = key.stage_hashes[fs_index] != 0;
    const auto* fs = fragment_enabled ? &pipeline->GetStage(Shader::SwStage::Fragment) : nullptr;
    const u64 program = fs ? fs->pgm_hash : 0;
    static thread_local std::unordered_map<u64, u32> seen;
    if (seen.size() > 4096u && seen.find(program) == seen.end()) {
        return;
    }
    const u32 observed = ++seen[program];
    if (observed > 4u && !std::has_single_bit(observed)) {
        return;
    }
    const u32 mask0 = static_cast<u32>(static_cast<VkColorComponentFlags>(key.write_masks[0]));
    LOG_INFO(Render_Vulkan,
             "FRAGMENT_DRAW_PROBE: shader={:#x} count={} indirect={} indexed={} "
             "requested={} instances={} mrt={:#x} mask0={:#x} "
             "color_control={} fs_images={} fs_samplers={}",
             program, observed, indirect, indexed, requested,
             regs.num_instances.NumInstances(), key.mrt_mask, mask0,
             static_cast<u32>(regs.color_control.mode),
             fs ? fs->images.size() : 0u, fs ? fs->samplers.size() : 0u);
}
'''
ANCHOR='    pipeline->BindResources(set_writes, push_data);\n    UpdateDynamicState(pipeline, is_indexed);'

def transform(original:bytes)->bytes:
    if hashlib.sha256(original).hexdigest()!=SOURCE_SHA:
        raise RuntimeError('C925_RASTERIZER_HASH_CHANGED_REFUSE')
    s=original.decode()
    if MARK in s:raise RuntimeError('PROBE_ALREADY_APPLIED')
    if s.count('namespace Vulkan {')!=1 or s.count(ANCHOR)!=2:
        raise RuntimeError('EXACT_RASTERIZER_DRAW_ANCHOR_CHANGED_REFUSE')
    s=s.replace('#include <atomic>', '#include <atomic>\n#include <bit>\n#include <unordered_map>',1)
    s=s.replace('namespace Vulkan {', 'namespace Vulkan {\n\n'+PROBE,1)
    first='    pipeline->BindResources(set_writes, push_data);\n    RecordFragmentDraw(pipeline, regs, false, is_indexed, regs.num_indices);\n    UpdateDynamicState(pipeline, is_indexed);'
    second='    pipeline->BindResources(set_writes, push_data);\n    RecordFragmentDraw(pipeline, regs, true, is_indexed, max_count);\n    UpdateDynamicState(pipeline, is_indexed);'
    s=s.replace(ANCHOR,first,1).replace(ANCHOR,second,1)
    if s.count(MARK)!=1 or s.count('    RecordFragmentDraw(pipeline, regs,')!=2:
        raise RuntimeError('PROBE_INJECTION_FAILED')
    return s.encode()

if __name__=='__main__':
 src=pathlib.Path(sys.argv[1]);target=pathlib.Path(sys.argv[2]);target.write_bytes(transform(src.read_bytes()))
 print('DRAW_PROBE_SOURCE_TRANSFORM=PASS')
