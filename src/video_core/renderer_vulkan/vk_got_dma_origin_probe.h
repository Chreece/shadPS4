// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Test-only readback of actual GPU DMA source and destination words after the
// original copy. This never patches guest memory or substitutes indirect draw
// arguments. A matching ZERO source and destination is evidence that the
// zero existed upstream of the draw; unequal values require deeper barriers.
#include <cstdint>
#include <cstring>
#include <memory>
#include <span>

#include "common/logging/log.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_got_indirect_producer_trace.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan::Diagnostics {

struct GotDmaGpuWordSample {
    VideoCore::Buffer target;
    std::uint64_t source_address{};
    std::uint64_t destination_address{};
    std::uint32_t source_offset{};
    std::uint32_t ordinal{};
    GotDmaWordCandidate kind{};
    bool source_is_gds{};
    bool source_alias_destination{};

    explicit GotDmaGpuWordSample(const Instance& instance)
        : target{instance,0,8,VideoCore::MemoryType::HostCached} {}
};

inline void CaptureGotDmaGpuWords(
    const Instance& instance, Scheduler& scheduler, Runtime& runtime,
    const VideoCore::Buffer* source, std::uint64_t source_offset,
    const VideoCore::Buffer* destination, std::uint64_t destination_offset,
    std::uint64_t guest_source, std::uint64_t guest_destination,
    std::uint32_t bytes, bool source_is_gds, bool destination_is_gds) {
    if (!GotDmaWordProbeEnabled()) return;
    const auto kind=SelectGotDmaWordCandidate(
        guest_source,guest_destination,bytes,source_is_gds,destination_is_gds);
    if (kind==GotDmaWordCandidate::None) return;

    static thread_local std::uint32_t gds_count{};
    static thread_local std::uint32_t guest_count{};
    auto& ordinal=(kind==GotDmaWordCandidate::GdsIndexCountCandidate)
        ? gds_count : guest_count;
    ++ordinal;
    if (!ShouldSampleGotDmaWord(ordinal)) return;
    if (!source || !destination ||
        source_offset>source->SizeBytes() ||
        destination_offset>destination->SizeBytes() ||
        source->SizeBytes()-source_offset<sizeof(std::uint32_t) ||
        destination->SizeBytes()-destination_offset<sizeof(std::uint32_t)) {
        LOG_WARNING(Render_Vulkan,
                    "GotDmaGpuWord: status=SKIPPED_UNSAFE sample={} "
                    "source_guest={:#x} destination_guest={:#x}",
                    ordinal,guest_source,guest_destination);
        return;
    }

    auto sample=std::make_unique<GotDmaGpuWordSample>(instance);
    sample->source_address=guest_source;
    sample->destination_address=guest_destination;
    sample->ordinal=ordinal;
    sample->kind=kind;
    sample->source_is_gds=source_is_gds;
    sample->source_alias_destination=(source->Handle()==destination->Handle());
    sample->source_offset=static_cast<std::uint32_t>(source_offset);

    // Append source/destination GPU transfers AFTER the original DMA copy,
    // ordered through the existing Runtime::CopyBuffer hazard/barrier path.
    const vk::BufferCopy source_copy{
        .srcOffset=source_offset,
        .dstOffset=0,
        .size=sizeof(std::uint32_t),
    };
    const vk::BufferCopy destination_copy{
        .srcOffset=destination_offset,
        .dstOffset=sizeof(std::uint32_t),
        .size=sizeof(std::uint32_t),
    };
    runtime.CopyBuffer(source,&sample->target,std::span{&source_copy,1});
    runtime.CopyBuffer(destination,&sample->target,std::span{&destination_copy,1});

    LOG_INFO(Render_Vulkan,
             "GotDmaGpuWord: status=GPU_COPY_SUBMITTED sample={} "
             "class={} source_guest={:#x} destination_guest={:#x} "
             "source_is_gds={} aliased_source={} mutation=NO",
             ordinal,static_cast<std::uint32_t>(kind),
             guest_source,guest_destination,source_is_gds,
             sample->source_alias_destination);

    scheduler.DeferPriorityOperation([sample=std::move(sample)]() mutable {
        sample->target.Invalidate(0,sample->target.SizeBytes());
        const auto raw=sample->target.mapped_data;
        if (raw.size()<2*sizeof(std::uint32_t)) {
            LOG_ERROR(Render_Vulkan,
                      "GotDmaGpuWord: status=GPU_READBACK_NOT_MAPPED sample={}",
                      sample->ordinal);
            return;
        }
        std::uint32_t source_word{},destination_word{};
        std::memcpy(&source_word,raw.data(),sizeof(source_word));
        std::memcpy(&destination_word,raw.data()+sizeof(source_word),
                    sizeof(destination_word));
        LOG_INFO(Render_Vulkan,
                 "GotDmaGpuWord: status=GPU_READBACK_PASS sample={} "
                 "class={} source_guest={:#x} destination_guest={:#x} "
                 "src_word={} dst_word={} exact_copy={} "
                 "source_is_gds={} aliased_source={} timeline_complete=YES",
                 sample->ordinal,static_cast<std::uint32_t>(sample->kind),
                 sample->source_address,sample->destination_address,
                 source_word,destination_word,source_word==destination_word,
                 sample->source_is_gds,sample->source_alias_destination);
    });
}

} // namespace Vulkan::Diagnostics
