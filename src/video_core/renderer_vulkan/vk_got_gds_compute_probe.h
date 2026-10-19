// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Source-side evidence for zero GoT GPU indirect counts: read the real
// shader-visible GDS byte range after a submitted compute dispatch, rather
// than guessing a nonzero draw count or reading a stale host mirror.
#include <array>
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <span>

#include "common/logging/log.h"
#include "shader_recompiler/info.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_got_indirect_producer_trace.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan::Diagnostics {

[[nodiscard]] inline bool GotGdsComputeProducerProbeEnabled() noexcept {
    static const bool enabled = [] {
        const char* s = std::getenv("SHADPS4_GOT_GDS_PRODUCER_PROBE");
        return s && s[0] == '1' && s[1] == '\0';
    }();
    return enabled;
}

inline constexpr std::uint64_t GotGdsCounterWindowStart = 0x980;
inline constexpr std::uint64_t GotGdsCounterWindowBytes = 0x80;
inline constexpr std::uint32_t GotGdsCounterWords = 32;

struct GotGdsComputeSnapshot {
    VideoCore::Buffer staging;
    std::uint32_t shader{};
    std::uint32_t sequence{};
    std::uint32_t dim_x{}, dim_y{}, dim_z{};
    bool indirect{};

    explicit GotGdsComputeSnapshot(const Instance& instance)
        : staging{instance, 0, GotGdsCounterWindowBytes,
                  VideoCore::MemoryType::HostCached} {}
};

inline void CaptureGotGdsComputeProducer(
    const Instance& instance, Scheduler& scheduler, Runtime& runtime,
    const VideoCore::Buffer* gds, const Shader::Info& cs,
    bool indirect, std::uint32_t dim_x, std::uint32_t dim_y,
    std::uint32_t dim_z) {
    if (!GotGdsComputeProducerProbeEnabled()) return;
    const bool writes_gds = std::ranges::any_of(cs.buffers, [](const auto& buffer) {
        return buffer.buffer_type == Shader::BufferType::GdsBuffer && buffer.is_written;
    });
    if (!writes_gds) return;
    static thread_local std::uint32_t total_seen{};
    ++total_seen;
    if (!ShouldSampleGotGdsProducer(total_seen)) return;
    if (!gds || gds->SizeBytes() < GotGdsCounterWindowStart+GotGdsCounterWindowBytes) {
        LOG_WARNING(Render_Vulkan, "GotGdsComputeProducer: status=GDS_BUFFER_TOO_SMALL");
        return;
    }

    auto sample = std::make_unique<GotGdsComputeSnapshot>(instance);
    sample->sequence=total_seen;
    sample->shader=cs.pgm_hash;
    sample->indirect=indirect;
    sample->dim_x=dim_x;
    sample->dim_y=dim_y;
    sample->dim_z=dim_z;

    // Called only AFTER the dispatch and ResetBindings(true), so the
    // resource hazard tracker has recorded shader writes to the GDS buffer.
    // Runtime::CopyBuffer synchronizes the transfer read and leaves GDS
    // unchanged. Only the new, host-cached 128-byte staging allocation is
    // written by this optional diagnostic.
    const vk::BufferCopy copy{
        .srcOffset=GotGdsCounterWindowStart,
        .dstOffset=0,
        .size=GotGdsCounterWindowBytes,
    };
    runtime.CopyBuffer(gds,&sample->staging,std::span{&copy,1});
    LOG_INFO(Render_Vulkan,
             "GotGdsComputeProducer: status=GPU_COPY_SUBMITTED "
             "sequence={} shader={:#x} indirect={} dim_x={} dim_y={} dim_z={} "
             "source_start=0x980 bytes=128 guest_mutation=NO",
             total_seen,cs.pgm_hash,indirect,dim_x,dim_y,dim_z);

    scheduler.DeferPriorityOperation([snapshot=std::move(sample)]() mutable {
        snapshot->staging.Invalidate(0,GotGdsCounterWindowBytes);
        const auto mapped=snapshot->staging.mapped_data;
        if (mapped.size()<GotGdsCounterWindowBytes) {
            LOG_ERROR(Render_Vulkan,
                      "GotGdsComputeProducer: status=READBACK_NOT_MAPPED sequence={}",
                      snapshot->sequence);
            return;
        }
        std::array<std::uint32_t,GotGdsCounterWords> words{};
        std::memcpy(words.data(),mapped.data(),GotGdsCounterWindowBytes);
        const auto nonzero=std::count_if(words.begin(),words.end(),
                                         [](std::uint32_t v){return v!=0;});
        const auto maximum=*std::max_element(words.begin(),words.end());
        LOG_INFO(Render_Vulkan,
                 "GotGdsComputeProducer: status=GPU_READBACK_PASS "
                 "sequence={} shader={:#x} indirect={} "
                 "dim_x={} dim_y={} dim_z={} nonzero_words={} max_word={} "
                 "gds_0x980={} gds_0x984={} gds_0x988={} gds_0x98c={} "
                 "gds_0x990={} gds_0x994={} gds_0x998={} gds_0x99c={} "
                 "timeline_complete=YES",
                 snapshot->sequence,snapshot->shader,snapshot->indirect,
                 snapshot->dim_x,snapshot->dim_y,snapshot->dim_z,
                 nonzero,maximum,words[0],words[1],words[2],words[3],
                 words[4],words[5],words[6],words[7]);
    });
}

} // namespace Vulkan::Diagnostics
