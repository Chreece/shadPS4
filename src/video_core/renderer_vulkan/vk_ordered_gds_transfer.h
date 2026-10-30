// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstring>
#include <limits>
#include <optional>
#include <utility>
#include <span>
#include <stdexcept>
#include <vector>

#include "shader_recompiler/frontend/ordered_gds_capture_packet.h"
#include "shader_recompiler/frontend/ordered_gds_checkpoint.h"
#include "shader_recompiler/frontend/ordered_gds_device_plan.h"
#include "shader_recompiler/frontend/ordered_gds_epoch_auth.h"
#include "shader_recompiler/frontend/ordered_gds_lds_checkpoint.h"
#include "shader_recompiler/frontend/ordered_gds_storage_layout.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_staging_buffer_pool.h"

namespace Vulkan {

// Actual GPU indirect argument snapshot for an explicitly requested
// ordered-GDS capture. Must be called on the GPU submit thread; it waits
// only for this scheduler's queued work. Never use CPU Liverpool group
// register values to size an indirect dispatch's guest wave packet owner.
class OrderedGdsIndirectDispatchTransfer {
public:
    [[nodiscard]] static Shader::Gcn::OrderedGds::DispatchExtent Download(
        Runtime& runtime, Scheduler& scheduler, const VideoCore::Buffer& source,
        u64 source_offset) {
        using Shader::Gcn::OrderedGds::GpuIndirectDispatchCommand;
        constexpr u64 bytes = sizeof(GpuIndirectDispatchCommand);
        if ((source_offset & 3u) || source_offset > source.SizeBytes() ||
            bytes > source.SizeBytes() - source_offset) {
            throw std::invalid_argument("GDS indirect command GPU source range invalid");
        }
        // Transfer bytes must reflect earlier GPU-written dispatch arguments,
        // not a speculative CPU register snapshot or an untracked host read.
        const auto staging = runtime.GetStagingPool().Request(
            bytes, VideoCore::MemoryType::HostCached, alignof(GpuIndirectDispatchCommand));
        if (!staging.buffer || !staging.mapped || staging.size < bytes) {
            throw std::runtime_error("GDS indirect GPU command readback unavailable");
        }
        scheduler.EndRendering();
        const vk::BufferCopy copy{
            .srcOffset = source_offset, .dstOffset = staging.offset, .size = bytes,
        };
        runtime.CopyBuffer(&source, staging.buffer, std::span{&copy, 1});
        const vk::BufferMemoryBarrier2 to_host{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eHost,
            .dstAccessMask = vk::AccessFlagBits2::eHostRead,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = staging.buffer->Handle(),
            .offset = staging.offset,
            .size = bytes,
        };
        scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
            .bufferMemoryBarrierCount = 1, .pBufferMemoryBarriers = &to_host,
        });
        scheduler.Finish();
        staging.Invalidate();
        std::array<std::byte, sizeof(GpuIndirectDispatchCommand)> command{};
        std::memcpy(command.data(), staging.mapped, bytes);
        return Shader::Gcn::OrderedGds::DecodeGpuIndirectDispatchCommand(command);
    }
};

// Device-buffer transfer for ordered-GDS capture and resume packets.
// Does NOT write a shader packet, establish guest wave creation order or
// resume a shader. Call only from the GPU submission thread after the packet
// writer records its ShaderWrite access in the Vulkan runtime resource tracker.
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

    // Read checkpoint register files from actual GPU memory, with the same
    // finite timeline synchronization as the 64-byte capture packets.
    // Requires an independently validated layout and an authoritative GPU
    // writer: a host-synthesized zeroed buffer is NOT a guest checkpoint.
    [[nodiscard]] static std::vector<std::byte> DownloadCheckpoint(
        Runtime& runtime, Scheduler& scheduler, const VideoCore::Buffer& source,
        u64 source_offset, const Shader::Gcn::OrderedGds::GcnCheckpointLayout& layout) {
        using Shader::Gcn::OrderedGds::GcnCheckpointLayout;
        using Shader::Gcn::OrderedGds::GpuGcnWaveCheckpoint;
        const auto verified = GcnCheckpointLayout::Require(
            layout.waves, layout.scalar_words, layout.vector_registers, 0);
        if (verified.total_bytes != layout.total_bytes ||
            verified.stride_bytes != layout.stride_bytes) {
            throw std::invalid_argument("inconsistent guest checkpoint buffer layout");
        }
        const u64 bytes = verified.total_bytes;
        if ((source_offset & 3u) != 0 || source_offset > source.SizeBytes() ||
            bytes > source.SizeBytes() - source_offset) {
            throw std::invalid_argument("guest checkpoint readback range invalid");
        }
        const auto staging = runtime.GetStagingPool().Request(
            bytes, VideoCore::MemoryType::HostCached, alignof(GpuGcnWaveCheckpoint));
        if (!staging.buffer || !staging.mapped || staging.size < bytes) {
            throw std::runtime_error("guest checkpoint GPU readback unavailable");
        }
        const vk::BufferCopy copy{
            .srcOffset = source_offset,
            .dstOffset = staging.offset,
            .size = bytes,
        };
        runtime.CopyBuffer(&source, staging.buffer, std::span{&copy, 1});
        const vk::BufferMemoryBarrier2 to_host{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eHost,
            .dstAccessMask = vk::AccessFlagBits2::eHostRead,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = staging.buffer->Handle(),
            .offset = staging.offset,
            .size = bytes,
        };
        scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
            .bufferMemoryBarrierCount = 1,
            .pBufferMemoryBarriers = &to_host,
        });
        scheduler.Finish();
        staging.Invalidate();
        std::vector<std::byte> checkpoint(bytes);
        std::memcpy(checkpoint.data(), staging.mapped, bytes);
        return checkpoint;
    }

    // Upload independently validated POST-DS guest register bytes to a
    // separate device buffer for the eventual GCN continuation pass.
    // The host resolver patches only decoded VDST/EXEC-active lanes using
    // the actual verified ordered-count previous value; it never infers a
    // result from raw GDS memory or Vulkan scheduling order. Remaining GCN
    // registers, PC and inactive lanes stay identical to GPU capture.
    // The pre-DS source snapshot and resume destination MUST NOT alias.
    static void UploadCheckpoint(
        Runtime& runtime, Scheduler& scheduler, VideoCore::Buffer& destination,
        u64 destination_offset, std::span<const std::byte> state_bytes,
        const Shader::Gcn::OrderedGds::GcnCheckpointLayout& layout) {
        using Shader::Gcn::OrderedGds::GcnCheckpointLayout;
        using Shader::Gcn::OrderedGds::GpuGcnWaveCheckpoint;
        const auto verified = GcnCheckpointLayout::Require(
            layout.waves, layout.scalar_words, layout.vector_registers, 0);
        if (verified.stride_bytes != layout.stride_bytes ||
            verified.total_bytes != layout.total_bytes ||
            state_bytes.size() != layout.total_bytes ||
            (destination_offset & 3u) != 0 ||
            destination_offset > destination.SizeBytes() ||
            verified.total_bytes > destination.SizeBytes() - destination_offset) {
            throw std::invalid_argument("guest checkpoint resume upload range invalid");
        }
        const u64 bytes = verified.total_bytes;
        const auto staging = runtime.GetStagingPool().Request(
            bytes, VideoCore::MemoryType::HostUncached, alignof(GpuGcnWaveCheckpoint));
        if (!staging.buffer || !staging.mapped || staging.size < bytes) {
            throw std::runtime_error("guest register-state resume upload unavailable");
        }
        std::memcpy(staging.mapped, state_bytes.data(), bytes);
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
        if ((source_offset & 3u) != 0 || source_offset > source.SizeBytes() ||
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
        if ((destination_offset & 3u) != 0 ||
            destination_offset > destination.SizeBytes() ||
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


// Real, independent VMA/Vulkan storage owned by a *future* guest compute
// dispatch orchestrator. The constructor validates every guest-controlled
// extent and the TOTAL budget BEFORE any device allocation. A complete
// resource owner must outlive capture submission, host readback/resolution,
// and the final resumed GPU pass; destroying it early is a caller error.
// These buffers are not guest GDS RAM, not the 4 CIK packer counters, and
// never borrow a streaming ring that can wrap between passes.
//
// This class provides safe storage, NOT an authenticated GPU packet writer,
// wave ordering, LDS quiescence, or shader continuation. The ordered GDS
// SPIR-V execution gate stays active until all those are implemented.
class OrderedGdsEpochDeviceBuffers {
public:
    using Plan = Shader::Gcn::OrderedGds::GcnOrderedGdsDevicePlan;
    using Extent = Shader::Gcn::OrderedGds::DispatchExtent;

    explicit OrderedGdsEpochDeviceBuffers(
        const Instance& instance, Extent groups, Extent local_threads,
        u32 allocated_vgprs, u32 guest_shared_lds_bytes)
        : plan{Plan::Require(groups, local_threads, allocated_vgprs,
                             guest_shared_lds_bytes)},
          capture_auth{instance, 0, plan.epoch_auth_bytes,
                       VideoCore::MemoryType::DeviceLocal,
                       "CIK GDS Trusted Dispatch Wave Tokens"},
          capture_packets{instance, 0, plan.packet_bytes,
                          VideoCore::MemoryType::DeviceLocal,
                          "CIK GDS Capture Packets"},
          resume_packets{instance, 0, plan.packet_bytes,
                         VideoCore::MemoryType::DeviceLocal,
                         "CIK GDS Resume Packets"},
          capture_registers{instance, 0, plan.register_layout.total_bytes,
                            VideoCore::MemoryType::DeviceLocal,
                            "CIK GDS Live Guest Register Checkpoint"},
          resume_registers{instance, 0, plan.register_layout.total_bytes,
                           VideoCore::MemoryType::DeviceLocal,
                           "CIK GDS Resumed Guest Register State"} {
        if (plan.lds_layout) {
            // Truly zero-LDS shaders do not allocate dummy buffers.
            capture_lds.emplace(instance, 0, plan.lds_layout->total_bytes,
                                VideoCore::MemoryType::DeviceLocal,
                                "CIK GDS Workgroup LDS Checkpoint");
            resume_lds.emplace(instance, 0, plan.lds_layout->total_bytes,
                               VideoCore::MemoryType::DeviceLocal,
                               "CIK GDS Resumed Workgroup LDS");
            persistent_lds.emplace(instance, 0, plan.persistent_lds_bytes,
                                   VideoCore::MemoryType::DeviceLocal,
                                   "CIK GDS Persistent Per-Workgroup LDS");
        }
    }

    OrderedGdsEpochDeviceBuffers(const OrderedGdsEpochDeviceBuffers&) = delete;
    OrderedGdsEpochDeviceBuffers& operator=(const OrderedGdsEpochDeviceBuffers&) = delete;
    OrderedGdsEpochDeviceBuffers(OrderedGdsEpochDeviceBuffers&&) = delete;
    OrderedGdsEpochDeviceBuffers& operator=(OrderedGdsEpochDeviceBuffers&&) = delete;

    // Initialize once before any guest LDS writes. Runtime::FillBuffer
    // tracks transfer-write hazards; BindBuffers later inserts a compute
    // dependency. This must NOT run again at the post-DS continuation,
    // otherwise genuine guest LDS written before the ordered site is lost.
    void PreparePersistentLds(Runtime& runtime) {
        if (!persistent_lds) {
            ASSERT_MSG(plan.persistent_lds_bytes == 0,
                       "nonzero persistent LDS plan has no backing");
            return;
        }
        if (persistent_lds_initialized_) {
            return;
        }
        const u64 bytes = plan.persistent_lds_bytes;
        if (!bytes || (bytes & 3u) || persistent_lds->SizeBytes() != bytes) {
            throw std::invalid_argument("invalid persistent GDS LDS backing allocation");
        }
        runtime.FillBuffer(&*persistent_lds, 0, bytes, 0);
        persistent_lds_initialized_ = true;
    }
    [[nodiscard]] bool PersistentLdsInitialized() const noexcept {
        return persistent_lds_initialized_;
    }

    // Source of authority is the host scheduler, not M0 or Vulkan arrival.
    // Upload once per epoch with UNIQUE, real per-wave continuation tokens.
    // The shader may copy these but still cannot assert a valid checkpoint
    // until all register/LDS state and true second-pass resume are proven.
    void PrepareEpochAuthority(Runtime& runtime, Scheduler& scheduler,
                               u32 epoch, std::span<const u64> tokens_by_slot) {
        // Invalidate the PREVIOUS host authorization before any argument,
        // staging or Vulkan copy can fail. Never rebind old tokens after an
        // unsuccessful epoch replacement (even if device RAM is unchanged).
        authenticated_epoch_ = 0;
        authority_.clear();
        using Shader::Gcn::OrderedGds::RequireGcnEpochAuth;
        auto checked = RequireGcnEpochAuth(plan.waves, epoch, tokens_by_slot);
        const u64 bytes = u64(checked.size()) *
                          sizeof(Shader::Gcn::OrderedGds::GpuGcnOrderedEpochAuth);
        ASSERT_MSG(bytes == plan.epoch_auth_bytes && bytes == capture_auth.SizeBytes(),
                   "CIK trusted dispatch wave authentication size mismatch");
        const auto staging = runtime.GetStagingPool().Request(
            bytes, VideoCore::MemoryType::HostUncached,
            alignof(Shader::Gcn::OrderedGds::GpuGcnOrderedEpochAuth));
        if (!staging.buffer || !staging.mapped || staging.size < bytes) {
            throw std::runtime_error("CIK guest epoch authority upload unavailable");
        }
        std::memcpy(staging.mapped, checked.data(), bytes);
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
            .srcOffset = staging.offset, .dstOffset = 0, .size = bytes,
        };
        runtime.CopyBuffer(staging.buffer, &capture_auth, std::span{&copy, 1});
        const vk::BufferMemoryBarrier2 copy_to_compute{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eComputeShader,
            .dstAccessMask = vk::AccessFlagBits2::eShaderStorageRead,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = capture_auth.Handle(),
            .offset = 0,
            .size = bytes,
        };
        scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
            .bufferMemoryBarrierCount = 1,
            .pBufferMemoryBarriers = &copy_to_compute,
        });
        authority_ = std::move(checked);
        authenticated_epoch_ = epoch;
    }
    [[nodiscard]] std::span<const Shader::Gcn::OrderedGds::GpuGcnOrderedEpochAuth>
    CaptureAuthority() const noexcept { return authority_; }
    [[nodiscard]] u32 AuthorizedEpoch() const noexcept { return authenticated_epoch_; }

    const Plan plan;
    VideoCore::Buffer capture_auth;
    VideoCore::Buffer capture_packets;
    VideoCore::Buffer resume_packets;
    VideoCore::Buffer capture_registers;
    VideoCore::Buffer resume_registers;
    std::optional<VideoCore::Buffer> capture_lds;
    std::optional<VideoCore::Buffer> resume_lds;
    std::optional<VideoCore::Buffer> persistent_lds;

private:
    std::vector<Shader::Gcn::OrderedGds::GpuGcnOrderedEpochAuth> authority_;
    u32 authenticated_epoch_{};
    bool persistent_lds_initialized_{};
};


// GPU-side transport of per-workgroup LDS state. Both directions require
// a compiler-proven quiescent checkpoint in the same guest epoch as the wave
// register snapshots. This does NOT manufacture a workgroup barrier or
// reconstruct LDS values from individual waves.
class OrderedGdsLdsStateTransfer {
public:
    using Layout = Shader::Gcn::OrderedGds::GcnLdsCheckpointLayout;

    [[nodiscard]] static std::vector<std::byte> Download(
        Runtime& runtime, Scheduler& scheduler, const VideoCore::Buffer& source,
        u64 source_offset, const Layout& layout, u32 epoch) {
        const auto valid = Layout::Require(
            layout.workgroups, layout.waves_per_group, layout.lds_bytes);
        if (valid.total_bytes != layout.total_bytes ||
            valid.stride_bytes != layout.stride_bytes ||
            (source_offset & 3u) ||
            source_offset > source.SizeBytes() ||
            valid.total_bytes > source.SizeBytes() - source_offset) {
            throw std::invalid_argument("ordered GDS LDS device capture range invalid");
        }
        const u64 bytes = valid.total_bytes;
        const auto staging = runtime.GetStagingPool().Request(
            bytes, VideoCore::MemoryType::HostCached,
            alignof(Shader::Gcn::OrderedGds::GpuGcnWorkgroupLdsCheckpoint));
        if (!staging.buffer || !staging.mapped || staging.size < bytes) {
            throw std::runtime_error("ordered GDS LDS readback unavailable");
        }
        const vk::BufferCopy copy{
            .srcOffset = source_offset,
            .dstOffset = staging.offset,
            .size = bytes,
        };
        runtime.CopyBuffer(&source, staging.buffer, std::span{&copy, 1});
        const vk::BufferMemoryBarrier2 to_host{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eHost,
            .dstAccessMask = vk::AccessFlagBits2::eHostRead,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = staging.buffer->Handle(),
            .offset = staging.offset,
            .size = bytes,
        };
        scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
            .bufferMemoryBarrierCount = 1,
            .pBufferMemoryBarriers = &to_host,
        });
        scheduler.Finish();
        staging.Invalidate();
        std::vector<std::byte> state(bytes);
        std::memcpy(state.data(), staging.mapped, bytes);
        Shader::Gcn::OrderedGds::RequireCompleteGcnLdsCheckpoints(state, valid, epoch);
        return state;
    }

    static void Upload(
        Runtime& runtime, Scheduler& scheduler, VideoCore::Buffer& destination,
        u64 destination_offset, std::span<const std::byte> state,
        const Layout& layout, u32 epoch) {
        const auto valid = Layout::Require(
            layout.workgroups, layout.waves_per_group, layout.lds_bytes);
        Shader::Gcn::OrderedGds::RequireCompleteGcnLdsCheckpoints(state, valid, epoch);
        if (valid.total_bytes != layout.total_bytes ||
            valid.stride_bytes != layout.stride_bytes ||
            (destination_offset & 3u) ||
            destination_offset > destination.SizeBytes() ||
            valid.total_bytes > destination.SizeBytes() - destination_offset) {
            throw std::invalid_argument("ordered GDS LDS device resume range invalid");
        }
        const u64 bytes = valid.total_bytes;
        const auto staging = runtime.GetStagingPool().Request(
            bytes, VideoCore::MemoryType::HostUncached,
            alignof(Shader::Gcn::OrderedGds::GpuGcnWorkgroupLdsCheckpoint));
        if (!staging.buffer || !staging.mapped || staging.size < bytes) {
            throw std::runtime_error("ordered GDS LDS upload unavailable");
        }
        std::memcpy(staging.mapped, state.data(), bytes);
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

// Transport for ordinary 64 KiB guest GDS memory used by data-share and
// PM4 operations. These bytes are authoritative for *RAM* after prior GPU
// writes, but are NOT proven to alias legacy CIK's dedicated ordered-count
// packer registers. Do not seed an OrderedGds::CompleteBatch from this
// snapshot without an independently verified hardware mapping. In
// particular, physical GDS byte addresses are not CounterKey identities.
class OrderedGdsStorageTransfer {
public:
    static constexpr u64 GdsBytes = Shader::Gcn::OrderedGds::GdsPhysicalBytes;
    static constexpr u32 DwordBytes = Shader::Gcn::OrderedGds::GdsDwordBytes;
    static constexpr u32 GdsDwords = Shader::Gcn::OrderedGds::GdsPhysicalDwords;
    using DwordWrite = Shader::Gcn::OrderedGds::PhysicalGdsDwordWrite;

    [[nodiscard]] static std::vector<u32> Snapshot(Runtime& runtime, Scheduler& scheduler,
                                                    const VideoCore::Buffer& gds) {
        RequireGdsSize(gds);
        const auto staging = runtime.GetStagingPool().Request(
            GdsBytes, VideoCore::MemoryType::HostCached, alignof(u32));
        if (!staging.buffer || !staging.mapped || staging.size < GdsBytes) {
            throw std::runtime_error("guest GDS snapshot staging unavailable");
        }
        const vk::BufferCopy copy{
            .srcOffset = 0,
            .dstOffset = staging.offset,
            .size = GdsBytes,
        };
        runtime.CopyBuffer(&gds, staging.buffer, std::span{&copy, 1});
        const vk::BufferMemoryBarrier2 to_host{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eHost,
            .dstAccessMask = vk::AccessFlagBits2::eHostRead,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = staging.buffer->Handle(),
            .offset = staging.offset,
            .size = GdsBytes,
        };
        scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
            .bufferMemoryBarrierCount = 1,
            .pBufferMemoryBarriers = &to_host,
        });
        scheduler.Finish();
        staging.Invalidate();

        std::vector<u32> dwords(GdsDwords);
        std::memcpy(dwords.data(), staging.mapped, GdsBytes);
        return dwords;
    }

    // Stage ONLY changed dwords, never write an entire stale 64 KiB copy
    // back over independent guest counters or other PM4 GDS updates.
    // Caller must ensure exclusive ownership of this ordered dispatch phase
    // and independently verify CounterKey -> physical GDS byte offsets.
    static void Apply(Runtime& runtime, Scheduler& scheduler,
                      const VideoCore::Buffer& gds, std::span<const DwordWrite> updates) {
        RequireGdsSize(gds);
        Shader::Gcn::OrderedGds::ValidatePhysicalGdsWrites(updates);
        if (updates.empty()) {
            return;
        }
        const u64 data_bytes = updates.size() * DwordBytes;
        const auto staging = runtime.GetStagingPool().Request(
            data_bytes, VideoCore::MemoryType::HostUncached, alignof(u32));
        if (!staging.buffer || !staging.mapped || staging.size < data_bytes) {
            throw std::runtime_error("guest GDS counter update staging unavailable");
        }
        std::vector<vk::BufferCopy> copies;
        copies.reserve(updates.size());
        for (std::size_t i = 0; i < updates.size(); ++i) {
            const auto [byte_offset, value] = updates[i];
            std::memcpy(staging.mapped + i * DwordBytes, &value, DwordBytes);
            copies.push_back({
                .srcOffset = staging.offset + i * DwordBytes,
                .dstOffset = byte_offset,
                .size = DwordBytes,
            });
        }
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
            .size = data_bytes,
        };
        scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
            .bufferMemoryBarrierCount = 1,
            .pBufferMemoryBarriers = &host_to_copy,
        });
        runtime.CopyBuffer(staging.buffer, &gds, copies);
        // A later compute shader observes the resolved GDS writes on the
        // same Vulkan queue. This does not resume its other guest registers.
        const vk::BufferMemoryBarrier2 copy_to_compute{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eComputeShader,
            .dstAccessMask = vk::AccessFlagBits2::eShaderStorageRead |
                             vk::AccessFlagBits2::eShaderStorageWrite,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = gds.Handle(),
            .offset = 0,
            .size = GdsBytes,
        };
        scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
            .bufferMemoryBarrierCount = 1,
            .pBufferMemoryBarriers = &copy_to_compute,
        });
    }

private:
    static void RequireGdsSize(const VideoCore::Buffer& gds) {
        if (gds.SizeBytes() != GdsBytes) {
            throw std::invalid_argument("expected exact 64 KiB guest GDS backing storage");
        }
    }
};

} // namespace Vulkan
