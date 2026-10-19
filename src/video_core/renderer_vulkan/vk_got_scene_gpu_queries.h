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
#include "video_core/renderer_vulkan/vk_got_active_menu_draws.h"
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
    u32 slot{},fs{},vs{},index_count{},pair_ordinal{};
    bool indirect{};
    bool indexed{};
    bool high_volume{};
};

inline std::optional<GotSceneQueryToken> PrepareGotSceneGpuQuery(
    const Instance& instance, Scheduler& scheduler, const AmdGpu::Regs& regs,
    const GraphicsPipeline& pipeline, bool indexed, bool indirect) {
    if (!GotSceneQueryEnabled()) return std::nullopt;
    const auto& stages=pipeline.GetStages();
    const auto* const fragment=stages[static_cast<u32>(Shader::SwStage::Fragment)];
    const auto* const vertex=stages[static_cast<u32>(Shader::SwStage::Vertex)];
    if (!fragment || !vertex) return std::nullopt;

    const u32 fs=fragment->pgm_hash,vs=vertex->pgm_hash;
    const auto& cb0=regs.color_buffers[0];
    const auto& db=regs.depth_buffer;
    const GotActiveMenuSceneDraw scene{
        .enabled=true,
        .indexed=indexed,
        .indirect=indirect,
        .color_attached=bool(cb0),
        .depth_attached=db.DepthValid(),
        .guest_depth_test=bool(regs.depth_control.depth_enable),
        .guest_depth_write=bool(regs.depth_control.depth_write_enable),
        .guest_depth_clear=bool(regs.depth_render_control.depth_clear_enable),
        .rasterizer_discard=bool(regs.clipper_control.dx_rasterization_kill),
        .color_write_mask=regs.color_target_mask.GetMask(0),
        .color_address=cb0 ? cb0.Address() : 0,
        .depth_address=db.DepthValid() ? db.DepthAddress() : 0,
        .fragment_shader=fs,
        .vertex_shader=vs,
        .guest_register_indices=regs.num_indices,
    };
    const auto kind=ClassifyGotActiveMenuDraw(scene);
    if (kind==GotActiveMenuDrawKind::Unrelated) return std::nullopt;

    // Count every submitted matching draw, not only previously selected
    // queries. The old >=128 guest register index gate discarded the most
    // frequent scene pipelines (guest register=3, GPU indirect args separate).
    const u64 key=(u64(fs)<<32)|vs;
    static thread_local std::unordered_map<u64,u32> observations;
    const u32 ordinal=++observations[key];
    if (!ShouldCaptureGotActiveMenuDraw(kind,ordinal)) return std::nullopt;

    static thread_local std::shared_ptr<GotSceneQueryState> shared;
    if (!shared) {
        shared=std::make_shared<GotSceneQueryState>(instance);
        // Reset once before the first rendering query, using the scheduler
        // upload command buffer which executes ahead of its primary stream.
        auto cmd=scheduler.UploadCommandBuffer();
        cmd.resetQueryPool(shared->pool.get(),0,MaxGotSceneQueryDraws);
        if (shared->pipeline_stats_enabled) {
            cmd.resetQueryPool(shared->stats.get(),0,MaxGotSceneQueryDraws);
        }
    }
    if (shared->next_slot>=MaxGotSceneQueryDraws) {
        LOG_WARNING(Render_Vulkan,
                    "GotSceneLiveQueryCheckpoint: fs={:#x} vs={:#x} "
                    "pair_ordinal={} result=QUERY_BUDGET_EXHAUSTED",
                    fs,vs,ordinal);
        return std::nullopt;
    }
    const bool high_volume=kind==GotActiveMenuDrawKind::FrequentSceneIndirect;
    const u32 slot=shared->next_slot++;
    LOG_INFO(Render_Vulkan,
             "GotSceneLiveQueryCheckpoint: fs={:#x} vs={:#x} "
             "pair_ordinal={} stage={} "
             "gpu_arguments_are_authoritative=YES count_from_guest_register={} "
             "query_slot={}",
             fs,vs,ordinal,high_volume?"active_indirect":"direct_control",
             regs.num_indices,slot);
    LOG_INFO(Render_Vulkan,
             "GotSceneGpuQuerySelected: slot={} fs={:#x} vs={:#x} "
             "indices={} indirect={} indexed={} exact_target=YES "
             "pair_ordinal={} high_volume={} "
             "pipeline_stats_supported={}",
             slot,fs,vs,regs.num_indices,indirect,indexed,ordinal,
             high_volume,shared->pipeline_stats_enabled);
    return GotSceneQueryToken{
        shared,slot,fs,vs,regs.num_indices,ordinal,indirect,indexed,high_volume};
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
                 "pair_ordinal={} high_volume={} "
                 "occlusion_status={} passed_samples={} "
                 "stats_supported={} stats_status={} "
                 "ia_vertices={} vs_invocations={} "
                 "clip_invocations={} clip_primitives={} "
                 "fs_invocations={} "
                 "gpu_timeline_complete=YES",
                 saved.fs,saved.vs,saved.slot,saved.index_count,
                 saved.indexed,saved.indirect,saved.pair_ordinal,
                 saved.high_volume,
                 vk::to_string(occlusion_result),occlusion[0],
                 state.pipeline_stats_enabled,vk::to_string(stats_result),
                 pipeline[0],pipeline[1],pipeline[2],pipeline[3],pipeline[4]);
    });
}

} // namespace Vulkan::Diagnostics
