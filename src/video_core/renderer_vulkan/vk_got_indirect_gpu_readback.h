// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Diagnostic-only GPU readback of the EXACT Vulkan indirect argument buffer
// consumed by the two GoT draws which showed zero vertex invocations.
// Avoid stale guest CPU mirrors: copy from the actual Vulkan buffer AFTER its
// draw, using the renderer's normal transfer barriers. Never substitute draw
// arguments, touch the guest allocation, or change GPU buffer residency.
#include <algorithm>
#include <array>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <span>

#include "common/logging/log.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_got_indirect_command_proof.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan::Diagnostics {

inline bool GotIndirectGpuArgumentsEnabled() {
    static const bool enabled=[] {
        const char* env=std::getenv("SHADPS4_GOT_INDIRECT_GPU_ARGUMENTS");
        return env && env[0]=='1' && env[1]=='\0';
    }();
    return enabled;
}

struct GotIndirectReadback {
    VideoCore::Buffer target;
    u64 arg_guest{};
    u64 count_guest{};
    u32 arg_base_offset{};
    u32 stride{};
    u32 max_count{};
    u32 captured_commands{};
    u32 arg_byte_count{};
    u32 count_dst_offset{};
    u32 guest_register_indices{};
    u32 shader_fs{};
    u32 shader_vs{};
    u32 probe_id{};
    bool has_count{};

    GotIndirectReadback(const Instance& instance,u64 bytes)
        :target{instance,0,bytes,VideoCore::MemoryType::HostCached} {}
};

inline void CaptureGotSceneGpuIndirectArguments(
    const Instance& instance, Scheduler& scheduler, Runtime& runtime,
    const VideoCore::Buffer* args, u64 arg_offset, u64 arg_guest,
    u32 stride, u32 max_count, const VideoCore::Buffer* count_buffer,
    u64 count_offset, u64 count_guest, u32 guest_register_indices,
    u32 fs_hash, u32 vs_hash) {
    if (!GotIndirectGpuArgumentsEnabled()) return;

    // Only these exact scene indirect draws had zero VS/FS invocations in
    // the 22:04 GPU queries. Their shader hash and render targets have
    // already been checked at the call site.
    static thread_local u32 probed{};
    if (probed>=4) return;
    const u32 probe_id=++probed;
    const u32 limited_count=std::min(max_count,MaxGotIndirectProofCommands);
    const u64 command_bytes=u64(limited_count)*stride;
    const bool has_count=count_buffer!=nullptr && count_guest!=0;
    const u64 count_destination=command_bytes;
    const u64 bytes_to_allocate=command_bytes+(has_count?4u:0u);
    if(stride!=sizeof(GotIndexedIndirectCommand) ||
       command_bytes==0 || bytes_to_allocate>8192 ||
       !args || arg_offset>args->SizeBytes() ||
       args->SizeBytes()-arg_offset<command_bytes ||
       (has_count && (count_offset>count_buffer->SizeBytes() ||
        count_buffer->SizeBytes()-count_offset<4u))) {
        LOG_WARNING(Render_Vulkan,
                    "GotSceneIndirectGpuArgs: probe={} status=SKIPPED_UNSAFE "
                    "arg_guest={:#x} arg_offset={} stride={} max_count={} "
                    "count_guest={:#x} arg_buffer_size={}",
                    probe_id,arg_guest,arg_offset,stride,max_count,
                    count_guest,args?args->SizeBytes():0);
        return;
    }

    auto staged=std::make_unique<GotIndirectReadback>(
        instance,bytes_to_allocate);
    staged->arg_guest=arg_guest;
    staged->count_guest=count_guest;
    staged->arg_base_offset=static_cast<u32>(arg_offset);
    staged->stride=stride;
    staged->max_count=max_count;
    staged->captured_commands=limited_count;
    staged->arg_byte_count=static_cast<u32>(command_bytes);
    staged->count_dst_offset=static_cast<u32>(count_destination);
    staged->guest_register_indices=guest_register_indices;
    staged->shader_fs=fs_hash;
    staged->shader_vs=vs_hash;
    staged->probe_id=probe_id;
    staged->has_count=has_count;

    // Runtime::CopyBuffer ends rendering, handles Vulkan source/destination
    // barriers and preserves the exact GPU contents. Both calls occur after
    // the draw and after the pipeline-statistics query has ended.
    const vk::BufferCopy argument_copy{
        .srcOffset=arg_offset,
        .dstOffset=0,
        .size=command_bytes,
    };
    runtime.CopyBuffer(args,&staged->target,std::span{&argument_copy,1});
    if (has_count) {
        const vk::BufferCopy count_copy{
            .srcOffset=count_offset,
            .dstOffset=count_destination,
            .size=4,
        };
        runtime.CopyBuffer(count_buffer,&staged->target,
                           std::span{&count_copy,1});
    }
    LOG_INFO(Render_Vulkan,
             "GotSceneIndirectGpuArgs: probe={} status=COPY_SUBMITTED "
             "fs={:#x} vs={:#x} arg_guest={:#x} "
             "arg_buffer_offset={} stride={} max_count={} "
             "captured={} guest_reg_indices={} "
             "count_guest={:#x} has_gpu_count_buffer={} "
             "guest_arguments_mutated=NO gpu_transfer_bytes={}",
             probe_id,fs_hash,vs_hash,arg_guest,arg_offset,stride,max_count,
             limited_count,guest_register_indices,count_guest,has_count,
             bytes_to_allocate);

    scheduler.DeferPriorityOperation([sample=std::move(staged)]() mutable {
        sample->target.Invalidate(0,sample->target.SizeBytes());
        auto mapped=sample->target.mapped_data;
        if (mapped.size()<sample->target.SizeBytes()) {
            LOG_ERROR(Render_Vulkan,
                      "GotSceneIndirectGpuArgs: probe={} status=READBACK_NOT_MAPPED",
                      sample->probe_id);
            return;
        }
        auto all=std::span<const u8>(mapped.data(),mapped.size());
        u32 actual_count=sample->max_count;
        if(sample->has_count) {
            std::memcpy(&actual_count,all.data()+sample->count_dst_offset,
                        sizeof(actual_count));
        }
        const u32 selected_count=sample->has_count
            ? std::min(actual_count,sample->max_count):sample->max_count;
        const auto args=all.first(sample->arg_byte_count);
        const auto kind=ClassifyGotGpuIndirectCommands(
            args,sample->stride,sample->max_count,sample->has_count,actual_count);
        const char* diagnosis=
            kind==GotIndirectInterpretation::NoCommandsSelected?
                "NO_DRAWS_SELECTED_GPU_COUNT_ZERO" :
            kind==GotIndirectInterpretation::AllSelectedCommandsZero?
                "SELECTED_DRAWS_HAVE_ZERO_INDEX_OR_INSTANCE_COUNT" :
            kind==GotIndirectInterpretation::HasNonzeroDrawCommand?
                "EXECUTABLE_GPU_COMMAND_PRESENT_CHECK_QUERY_OR_SYNC" :
                "SNAPSHOT_PARTIAL_OR_INCOMPLETE";
        LOG_INFO(Render_Vulkan,
                 "GotSceneIndirectGpuArgs: probe={} status=GPU_READBACK_PASS "
                 "fs={:#x} vs={:#x} arg_guest={:#x} count_guest={:#x} "
                 "max_count={} gpu_draw_count={} selected_count={} "
                 "capture_count={} guest_reg_indices={} diagnosis={} "
                 "gpu_timeline_complete=YES",
                 sample->probe_id,sample->shader_fs,sample->shader_vs,
                 sample->arg_guest,sample->count_guest,sample->max_count,
                 actual_count,selected_count,sample->captured_commands,
                 sample->guest_register_indices,diagnosis);
        for(u32 i=0;i<sample->captured_commands;++i) {
            const auto cmd=DecodeGotIndexedIndirect(
                args,u64(i)*sample->stride);
            if(i<16 || (cmd.index_count && cmd.instance_count)) {
                LOG_INFO(Render_Vulkan,
                         "GotSceneIndirectGpuCommand: probe={} command={} "
                         "selected={} gpu_index_count={} gpu_instance_count={} "
                         "first_index={} vertex_offset={} first_instance={} "
                         "guest_reg_num_indices={}",
                         sample->probe_id,i,i<selected_count,
                         cmd.index_count,cmd.instance_count,cmd.first_index,
                         cmd.vertex_offset,cmd.first_instance,
                         sample->guest_register_indices);
            }
        }
    });
}

} // namespace Vulkan::Diagnostics
