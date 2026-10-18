// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Exact-scene GPU invocation proof. Unlike a submitted draw counter, Vulkan
// pipeline-statistics queries distinguish valid vertex invocations, clipping,
// and fragment shader invocations. Independent occlusion queries detect
// whether any samples survive to the depth/color output. No shader, resource,
// register or guest memory change. 12 draw queries maximum, opt-in only.
#include <array>
#include <bit>
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <optional>
#include <unordered_map>

#include "common/logging/log.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/renderer_vulkan/vk_got_scene_depth_ab.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_platform.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_graphics_pipeline.h"

namespace Vulkan::Diagnostics {

constexpr u32 MaxGotSceneQueryDraws=12;

inline bool GotSceneQueryEnabled() {
    static const bool enabled=[] {
        const char* e=std::getenv("SHADPS4_GOT_SCENE_GPU_QUERY");
        return e && e[0]=='1' && e[1]=='\0';
    }();
    return enabled;
}

inline GotSceneDepthABInput GuestSceneQueryInput(const AmdGpu::Regs& regs) {
    const auto& cb=regs.color_buffers[0];
    const auto& db=regs.depth_buffer;
    return GotSceneDepthABInput{
        .opted_in=GotSceneQueryEnabled(),
        .guest_color_address=cb ? cb.Address() : 0,
        .guest_depth_address=db.DepthValid() ? db.DepthAddress() : 0,
        .index_count=regs.num_indices,
        .color_write_mask=regs.color_target_mask.GetMask(0),
        .depth_test=bool(regs.depth_control.depth_enable),
        .depth_write=bool(regs.depth_control.depth_write_enable),
        .depth_clear=bool(regs.depth_render_control.depth_clear_enable),
        .raster_discard=bool(regs.clipper_control.dx_rasterization_kill),
    };
}

// Bits are defined by Vulkan, results returned in bit-order:
// IA vertices, VS invocations, clipping invocations, clipping primitives,
// fragment shader invocations. An occlusion query independently counts
// passed samples and includes effects of fragment tests/discards.
inline constexpr vk::QueryPipelineStatisticFlags GotSceneQueryStats{
    vk::QueryPipelineStatisticFlagBits::eInputAssemblyVertices |
    vk::QueryPipelineStatisticFlagBits::eVertexShaderInvocations |
    vk::QueryPipelineStatisticFlagBits::eClippingInvocations |
    vk::QueryPipelineStatisticFlagBits::eClippingPrimitives |
    vk::QueryPipelineStatisticFlagBits::eFragmentShaderInvocations};

struct GotSceneQueryState {
    explicit GotSceneQueryState(const Instance& instance)
        : device{instance.GetDevice()},
          pool{Check<"create scene occlusion query pool">(
              device.createQueryPoolUnique(vk::QueryPoolCreateInfo{
                  .queryType=vk::QueryType::eOcclusion,
                  .queryCount=MaxGotSceneQueryDraws,
              }))},
          pipeline_stats_enabled{
              bool(instance.GetPhysicalDevice().getFeatures().pipelineStatisticsQuery)} {
        if (pipeline_stats_enabled) {
            stats=Check<"create scene pipeline statistics pool">(
                device.createQueryPoolUnique(vk::QueryPoolCreateInfo{
                    .queryType=vk::QueryType::ePipelineStatistics,
                    .queryCount=MaxGotSceneQueryDraws,
                    .pipelineStatistics=GotSceneQueryStats,
                }));
        }
        LOG_INFO(Render_Vulkan,
                 "GotSceneGpuQuerySetup: pipeline_statistics_enabled={} "
                 "occlusion_enabled=YES queries={} side_effects=NONE",
                 pipeline_stats_enabled,MaxGotSceneQueryDraws);
    }

    vk::Device device;
    vk::UniqueQueryPool pool;
    vk::UniqueQueryPool stats{};
    bool pipeline_stats_enabled{};
    u32 next_slot{};
    std::unordered_map<u64,u32> seen_pairs;
};

struct GotSceneQueryToken {
    std::shared_ptr<GotSceneQueryState> state;
    u32 slot{},fs{},vs{},index_count{};
    bool indirect{};
    bool indexed{};
};

inline std::optional<GotSceneQueryToken> PrepareGotSceneGpuQuery(
    const Instance& instance, Scheduler& scheduler, const AmdGpu::Regs& regs,
    const GraphicsPipeline& pipeline, bool indexed, bool indirect) {
    if (!ShouldForceGotSceneDepthAlways(GuestSceneQueryInput(regs))) {
        return std::nullopt;
    }
    const auto& stages=pipeline.GetStages();
    const auto* const fragment=stages[static_cast<u32>(Shader::SwStage::Fragment)];
    const auto* const vertex=stages[static_cast<u32>(Shader::SwStage::Vertex)];
    if (!fragment || !vertex) return std::nullopt;

    const u32 fs=fragment->pgm_hash,vs=vertex->pgm_hash;
    const u64 key=(u64(fs)<<32) | vs;
    static thread_local std::shared_ptr<GotSceneQueryState> shared;
    if (!shared) {
        shared=std::make_shared<GotSceneQueryState>(instance);
        // Upload buffers are executed before the primary command buffer
        // of the same scheduler session. Reset ONCE, outside rendering.
        auto cmd=scheduler.UploadCommandBuffer();
        cmd.resetQueryPool(shared->pool.get(),0,MaxGotSceneQueryDraws);
        if (shared->pipeline_stats_enabled) {
            cmd.resetQueryPool(shared->stats.get(),0,MaxGotSceneQueryDraws);
        }
    }
    auto& seen=shared->seen_pairs[key];
    if (seen>=2 || shared->next_slot>=MaxGotSceneQueryDraws) return std::nullopt;
    ++seen;
    const u32 slot=shared->next_slot++;
    LOG_INFO(Render_Vulkan,
             "GotSceneGpuQuerySelected: slot={} fs={:#x} vs={:#x} "
             "indices={} indirect={} indexed={} exact_target=YES "
             "pipeline_stats_supported={}",
             slot,fs,vs,regs.num_indices,indirect,indexed,
             shared->pipeline_stats_enabled);
    return GotSceneQueryToken{
        shared,slot,fs,vs,regs.num_indices,indirect,indexed};
}

inline void BeginGotSceneGpuQuery(vk::CommandBuffer cmd,
                                  const std::optional<GotSceneQueryToken>& token) {
    if (!token) return;
    cmd.beginQuery(token->state->pool.get(),token->slot,{});
    if (token->state->pipeline_stats_enabled) {
        cmd.beginQuery(token->state->stats.get(),token->slot,{});
    }
}

inline void EndGotSceneGpuQuery(Scheduler& scheduler, vk::CommandBuffer cmd,
                                std::optional<GotSceneQueryToken> token) {
    if (!token) return;
    if (token->state->pipeline_stats_enabled) {
        cmd.endQuery(token->state->stats.get(),token->slot);
    }
    cmd.endQuery(token->state->pool.get(),token->slot);
    // The scheduler callback waits for the GPU's current timeline tick, not
    // the wall clock. Query retrieval has NO WAIT flag, preventing CPU stalls.
    scheduler.DeferPriorityOperation([saved=std::move(*token)]() mutable {
        auto& state=*saved.state;
        std::array<u64,1> occlusion{};
        const auto query_flags=vk::QueryResultFlags{vk::QueryResultFlagBits::e64};
        const vk::Result occlusion_result=state.device.getQueryPoolResults(
            state.pool.get(),saved.slot,1,sizeof(occlusion),occlusion.data(),
            sizeof(u64),query_flags);
        std::array<u64,5> pipeline{};
        vk::Result stats_result=vk::Result::eNotReady;
        if (state.pipeline_stats_enabled) {
            stats_result=state.device.getQueryPoolResults(
                state.stats.get(),saved.slot,1,sizeof(pipeline),pipeline.data(),
                sizeof(u64)*5,query_flags);
        }
        LOG_INFO(Render_Vulkan,
                 "GotSceneGpuInvocations: fs={:#x} vs={:#x} query={} "
                 "draw_indices={} indexed={} indirect={} "
                 "occlusion_status={} passed_samples={} "
                 "stats_supported={} stats_status={} "
                 "ia_vertices={} vs_invocations={} "
                 "clip_invocations={} clip_primitives={} "
                 "fs_invocations={} "
                 "gpu_timeline_complete=YES",
                 saved.fs,saved.vs,saved.slot,saved.index_count,
                 saved.indexed,saved.indirect,
                 vk::to_string(occlusion_result),occlusion[0],
                 state.pipeline_stats_enabled,vk::to_string(stats_result),
                 pipeline[0],pipeline[1],pipeline[2],pipeline[3],pipeline[4]);
    });
}

} // namespace Vulkan::Diagnostics
