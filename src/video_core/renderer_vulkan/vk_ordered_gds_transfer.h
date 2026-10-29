// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstring>
#include <limits>
#include <span>
#include <stdexcept>
#include <vector>

#include "shader_recompiler/frontend/ordered_gds_capture_packet.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_staging_buffer_pool.h"

// Device-buffer transfer for ordered-GDS capture and resume packets.
// Does NOT write a shader packet, establish guest wave creation order or
// resume a shader. Call only from the GPU submission thread after the packet
// writer records its ShaderWrite access in the Vulkan runtime resource tracker.
namespace Vulkan {

class OrderedGdsPacketTransfer {
public:
    using Capture = Shader::Gcn::OrderedGds::GpuCapturePacket;
    using Resume = Shader::Gcn::OrderedGds::GpuResumePacket;

    // Bounded to the host collector limit. No guest-controlled multiplication
    // or range can silently wrap or allocate an unbounded amount of memory.
    [[nodiscard]] static u64 CheckedPacketBytes(std::size_t packet_count) {
        if (packet_count == 0 || packet_count > 1'000'000 ||
            packet_count > std::numeric_limits<u64>::max() / sizeof(Capture)) {
            throw std::invalid_argument("ordered GDS packet count exceeds bounded limit");
        }
        return static_cast<u64>(packet_count) * sizeof(Capture);
    }

    // Lifetime is owned by the dispatch orchestrator, until both transfers
    // and the resumed compute pass are complete. No resources are created
    // unless the caller explicitly constructs a pair.
    struct DeviceBuffers {
        explicit DeviceBuffers(const Instance& instance, std::size_t packet_count)
            : capture{instance, 0, CheckedPacketBytes(packet_count),
                      VideoCore::MemoryType::DeviceLocal, "Ordered GDS Capture"},
              resume{instance, 0, CheckedPacketBytes(packet_count),
                     VideoCore::MemoryType::DeviceLocal, "Ordered GDS Resume"} {}
        VideoCore::Buffer capture;
        VideoCore::Buffer resume;
    };

    // The caller must have registered preceding GPU ShaderWrite access.
    // Runtime::CopyBuffer then adds the appropriate compute-write -> copy-read
    // dependency through its tracked buffer barriers. A post-copy barrier,
    // GPU timeline completion and VMA invalidation expose exact bytes to CPU.
    [[nodiscard]] static std::vector<Capture> Download(
        Runtime& runtime, Scheduler& scheduler, const VideoCore::Buffer& source,
        u64 source_offset, std::size_t packet_count) {
        const u64 bytes = CheckedPacketBytes(packet_count);
        if (source_offset > source.SizeBytes() ||
            bytes > source.SizeBytes() - source_offset) {
            throw std::invalid_argument("ordered GDS capture range outside device buffer");
        }
        const auto readback = runtime.GetStagingPool().Request(
            bytes, VideoCore::MemoryType::HostCached, alignof(Capture));
        if (!readback.buffer || !readback.mapped || readback.size < bytes) {
            throw std::runtime_error("ordered GDS capture readback allocation unavailable");
        }
        const vk::BufferCopy copy{
            .srcOffset = source_offset,
            .dstOffset = readback.offset,
            .size = bytes,
        };
        runtime.CopyBuffer(&source, readback.buffer, std::span{&copy, 1});
        const vk::BufferMemoryBarrier2 to_host{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eHost,
            .dstAccessMask = vk::AccessFlagBits2::eHostRead,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = readback.buffer->Handle(),
            .offset = readback.offset,
            .size = bytes,
        };
        scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
            .bufferMemoryBarrierCount = 1,
            .pBufferMemoryBarriers = &to_host,
        });
        // Wait only for scheduled GPU work, never vkDeviceWaitIdle or a
        // cross-workgroup GPU spinlock. This is synchronous by design until
        // a pipelined continuation scheduler is implemented.
        scheduler.Finish();
        readback.Invalidate();

        std::vector<Capture> packets(packet_count);
        std::memcpy(packets.data(), readback.mapped, bytes);
        return packets;
    }

    // Copy validated host GDS results to a GPU-visible storage buffer.
    // The host caller owns packet validation and the continuation lifetime.
    // An explicit CopyWrite -> ComputeRead barrier makes the data visible to
    // the following resume dispatch without relying on host queue idleness.
    static void Upload(Runtime& runtime, Scheduler& scheduler,
                       VideoCore::Buffer& destination, u64 destination_offset,
                       std::span<const Resume> packets) {
        const u64 bytes = CheckedPacketBytes(packets.size());
        if (destination_offset > destination.SizeBytes() ||
            bytes > destination.SizeBytes() - destination_offset) {
            throw std::invalid_argument("ordered GDS resume range outside device buffer");
        }
        const auto staging = runtime.GetStagingPool().Request(
            bytes, VideoCore::MemoryType::HostUncached, alignof(Resume));
        if (!staging.buffer || !staging.mapped || staging.size < bytes) {
            throw std::runtime_error("ordered GDS resume upload allocation unavailable");
        }
        std::memcpy(staging.mapped, packets.data(), bytes);
        staging.Flush();

        scheduler.EndRendering();
        const vk::BufferMemoryBarrier2 host_to_copy{
            .srcStageMask = vk::PipelineStageFlagBits2::eHost,
            .srcAccessMask = vk::AccessFlagBits2::eHostWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eCopy,
            .dstAccessMask = vk::AccessFlagBits2::eTransferRead,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = staging.buffer->Handle(),
            .offset = staging.offset,
            .size = bytes,
        };
        scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
            .bufferMemoryBarrierCount = 1,
            .pBufferMemoryBarriers = &host_to_copy,
        });
        const vk::BufferCopy copy{
            .srcOffset = staging.offset,
            .dstOffset = destination_offset,
            .size = bytes,
        };
        runtime.CopyBuffer(staging.buffer, &destination, std::span{&copy, 1});
        const vk::BufferMemoryBarrier2 copy_to_compute{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eComputeShader,
            .dstAccessMask = vk::AccessFlagBits2::eShaderStorageRead,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = destination.Handle(),
            .offset = destination_offset,
            .size = bytes,
        };
        scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
            .bufferMemoryBarrierCount = 1,
            .pBufferMemoryBarriers = &copy_to_compute,
        });
    }
};

} // namespace Vulkan
