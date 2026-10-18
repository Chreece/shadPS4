// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Opt-in, read-only and bounded provenance for the fragment shaders actually
// submitting draws while the presented Ghost of Tsushima menu is white.
// No texture descriptors, image contents, pipeline keys or guest registers
// are modified. Enabled ONLY by SHADPS4_MENU_PASS_TRACE=1.
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <map>
#include <tuple>
#include <utility>
#include <vector>

#include "common/logging/log.h"
#include "shader_recompiler/info.h"
#include "video_core/renderer_vulkan/vk_graphics_pipeline.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan::Diagnostics {

inline bool MenuPassTraceEnabled() {
    static const bool enabled=[] {
        const char* flag=std::getenv("SHADPS4_MENU_PASS_TRACE");
        return flag && flag[0]=='1' && flag[1]=='\0';
    }();
    return enabled;
}

inline u64 MenuPassNowMs() {
    using namespace std::chrono;
    return static_cast<u64>(duration_cast<milliseconds>(
        steady_clock::now().time_since_epoch()).count());
}

struct MenuPassStats {
    u64 total{};
    u64 window{};
    u64 indexed{};
    u64 indirect{};
    u32 width{};
    u32 height{};
    u32 indices{};
    u32 instances{};
    u32 color_attachments{};
    u32 clears{};
    u32 depth{};
    u32 clear0{};
    bool output_enabled{};
    bool clear_on_last{};
};

inline void TraceMenuDraw(const AmdGpu::Regs& regs,
                          const GraphicsPipeline& pipeline,
                          const RenderState& state,
                          bool indexed, bool indirect) {
    if (!MenuPassTraceEnabled()) return;
    const auto stages=pipeline.GetStages();
    const auto* fs=stages[static_cast<u32>(Shader::SwStage::Fragment)];
    const auto* vs=stages[static_cast<u32>(Shader::SwStage::Vertex)];
    const u64 hash=fs ? fs->pgm_hash : 0;
    const u64 vs_hash=vs ? vs->pgm_hash : 0;
    const bool has_target=state.num_color_attachments!=0 &&
        state.color_attachments[0].image_view!=vk::ImageView{};
    const u64 address=has_target ? regs.color_buffers[0].Address() : 0;
    const bool is_clear=has_target && state.color_attachments[0].is_clear;
    const u64 now=MenuPassNowMs();

    using Key=std::pair<u64,u64>;
    static thread_local std::map<Key,MenuPassStats> statistics;
    static thread_local u64 last_summary{};
    static thread_local u64 total_draws{};
    if (last_summary==0) last_summary=now;
    ++total_draws;

    const Key key{hash,address};
    auto found=statistics.find(key);
    if (found==statistics.end()) {
        // Finite, passive bound. Do not let a guest submit indefinitely many
        // unique shader/color-target pairs into instrumentation memory.
        if (statistics.size()>=512) return;
        found=statistics.try_emplace(key).first;
    }
    auto& stat=found->second;
    ++stat.total;
    ++stat.window;
    stat.indexed+=indexed;
    stat.indirect+=indirect;
    stat.width=state.width;
    stat.height=state.height;
    stat.indices=regs.num_indices;
    stat.instances=regs.num_instances.NumInstances();
    stat.color_attachments=state.num_color_attachments;
    stat.clears+=is_clear;
    stat.depth+=state.depth_stencil_attachment.image_view!=vk::ImageView{};
    stat.output_enabled=has_target;
    stat.clear_on_last=is_clear;
    stat.clear0=has_target ? state.color_attachments[0].clear_value[0] : 0;

    // First occurrence and exponentially rare checkpoints per *exact*
    // fs/target pair. Logs actual submitted draws, not pipeline compilation.
    if (stat.total==1 || stat.total==32 || stat.total==256 ||
        (stat.total%4096)==0) {
        LOG_INFO(Render_Vulkan,
                 "GotMenuDrawTrace: mono_ms={} fs={:#x} vs={:#x} "
                 "color0={:#x} samples={} window={} fb={}x{} "
                 "indices={} instances={} indexed={} indirect={} "
                 "mrt_count={} output={} depth={} clear={} clear0={:#x}",
                 now,hash,vs_hash,address,stat.total,stat.window,
                 state.width,state.height,stat.indices,stat.instances,
                 indexed,indirect,state.num_color_attachments,has_target,
                 state.depth_stencil_attachment.image_view!=vk::ImageView{},
                 is_clear,stat.clear0);
        // This MUST inspect every MRT: in GoT the real 1920x1080 scene
        // buffer (0x1504a08000) is sampled by tone mapping but never
        // appeared as color0 in the prior 1,142-edge graph. CB1..CB7 may
        // contain the missing producer. These are read-only checks of the
        // exact submitted pipeline state after BeginRendering.
        const auto& key=pipeline.GetGraphicsKey();
        for (u32 cb=0; cb<state.num_color_attachments &&
                     cb<AmdGpu::NUM_COLOR_BUFFERS; ++cb) {
            const auto& attachment=state.color_attachments[cb];
            const bool view=attachment.image_view!=vk::ImageView{};
            const auto& color=regs.color_buffers[cb];
            const u64 guest_addr=(view && color) ? color.Address() : 0;
            const u32 guest_write_mask=regs.color_target_mask.GetMask(cb);
            const auto vk_mask=static_cast<VkColorComponentFlags>(key.write_masks[cb]);
            const u32 pipeline_mask=static_cast<u32>(vk_mask);
            const bool shader_export=(key.mrt_mask & (1u<<cb))!=0;
            const bool can_write=guest_addr!=0 && shader_export &&
                pipeline_mask!=0 && guest_write_mask!=0;
            if (!guest_addr && !shader_export) continue;
            LOG_INFO(Render_Vulkan,
                     "GotMenuMrtTargetTrace: mono_ms={} fs={:#x} vs={:#x} "
                     "attachment={} address={:#x} color0={:#x} "
                     "sh_export={} guest_write_mask={:#x} vk_write_mask={:#x} "
                     "can_write={} view={} clear={} clear_word0={:#x} "
                     "fb={}x{} samples={}",
                     now,hash,vs_hash,cb,guest_addr,address,shader_export,
                     guest_write_mask,pipeline_mask,can_write,view,
                     attachment.is_clear,attachment.clear_value[0],
                     state.width,state.height,stat.total);
        }
    }

    if (now-last_summary<10000) return;
    last_summary=now;
    struct Row {Key key; u64 window;};
    std::vector<Row> active{};
    active.reserve(statistics.size());
    for (auto& [k,value]:statistics) {
        if (value.window) active.push_back({k,value.window});
    }
    std::sort(active.begin(),active.end(),
              [](const Row& a,const Row& b) {return a.window>b.window;});
    LOG_INFO(Render_Vulkan,
             "GotMenuPassWindow: mono_ms={} submitted_total={} shader_target_pairs={} "
             "active_pairs={} reported_top={}",
             now,total_draws,statistics.size(),active.size(),
             std::min<std::size_t>(active.size(),20));
    for (std::size_t i=0;i<active.size() && i<20;++i) {
        const auto& row=active[i];
        const auto& data=statistics.at(row.key);
        LOG_INFO(Render_Vulkan,
                 "GotMenuPassTop: mono_ms={} rank={} fs={:#x} color0={:#x} "
                 "draws={} fb={}x{} indices={} instances={} color={} "
                 "depth_draws={} clear_draws={}",
                 now,i+1,row.key.first,row.key.second,row.window,
                 data.width,data.height,data.indices,data.instances,
                 data.output_enabled,data.depth,data.clears);
    }
    for (auto& [k,value]:statistics) {
        value.window=0;
        value.clears=0;
        value.depth=0;
    }
}

inline void TraceMenuFastClear(u64 address, u32 clear0) {
    if (!MenuPassTraceEnabled()) return;
    static thread_local u32 reports{};
    ++reports;
    if (reports<=20 || (reports%512)==0) {
        LOG_INFO(Render_Vulkan,
                 "GotMenuFastClear: mono_ms={} target={:#x} "
                 "clear0={:#x} event={}",
                 MenuPassNowMs(),address,clear0,reports);
    }
}

// First few samples for every exact fragment/output/resource combination,
// then exponentially sparse refreshes. Shader-bound guest resources can
// change while the SPIR-V is cached, so do not log only at compile time.
inline bool ShouldTraceMenuSource(u64 fragment_hash, u64 target, u32 resource_index) {
    if (!MenuPassTraceEnabled() || target==0) return false;
    using Key=std::tuple<u64,u64,u32>;
    static thread_local std::map<Key,u64> observations;
    const Key key{fragment_hash,target,resource_index};
    auto it=observations.find(key);
    if (it==observations.end()) {
        if (observations.size()>=512) return false;
        it=observations.emplace(key,0).first;
    }
    const u64 seen=++it->second;
    return seen<=4 || seen==16 || seen==64 || seen==256 ||
           seen==1024 || (seen%4096)==0;
}

// This records guest addresses only. No readbacks, image probing, command
// buffer changes, descriptor replacement or synthesised textures. A source
// with no matching render-target draw can be a legitimate CPU-uploaded image;
// the graph is evidence rather than automatic blame.
inline void TraceMenuSource(u64 fs, u64 output_target, u32 slot,
                            bool guest_indexed, bool fetched,
                            u64 guest_texture_or_table, u32 source_stride,
                            u32 source_records, u32 width, u32 height,
                            u32 raw_format, u32 raw_number_format,
                            u32 raw_image_type) {
    if (!MenuPassTraceEnabled()) return;
    LOG_INFO(Render_Vulkan,
             "GotMenuSourceEdge: mono_ms={} fs={:#x} target={:#x} "
             "resource={} kind={} fetched={} source_addr={:#x} "
             "stride={} records={} dimensions={}x{} "
             "data_format={} number_format={} image_type={}",
             MenuPassNowMs(),fs,output_target,slot,
             guest_indexed ? "guest_table" : "single_texture",
             fetched,guest_texture_or_table,source_stride,
             source_records,width,height,raw_format,raw_number_format,
             raw_image_type);
}

} // namespace Vulkan::Diagnostics
