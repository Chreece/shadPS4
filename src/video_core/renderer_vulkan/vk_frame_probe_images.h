// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <bit>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include "common/path_util.h"
#include "shader_recompiler/info.h"
#include "shader_recompiler/targeted_shader_dump.h"
#include "shader_recompiler/vertex_output_feedback.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_frame_probe.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan::Diagnostics {

inline void CaptureFrameProbeScalars(const Instance& instance, Scheduler& scheduler,
                                     Runtime& runtime, const VideoCore::Buffer& source, u64 offset,
                                     const Shader::Info& stage) {
    const auto frame = FrameProbe().frame;
    if (!FrameProbeEnabled() || !frame) {
        return;
    }
    static thread_local u32 last_frame{};
    static thread_local u32 captures{};
    static thread_local u64 captured_bytes{};
    static thread_local bool limit_reported{};
    if (last_frame != frame) {
        captures = 0;
        captured_bytes = 0;
        limit_reported = false;
        last_frame = frame;
    }
    const auto bytes = stage.flattened_ud_buf.size() * sizeof(u32);
    if (!bytes || offset > source.SizeBytes() || bytes > source.SizeBytes() - offset) {
        return;
    }
    if (captures >= 4096 || bytes > 256_KB || bytes > 16_MB - captured_bytes) {
        if (!limit_reported) {
            LOG_WARNING(Render_Vulkan,
                        "FrameProbeScalarLimit: frame={} after_draw={} captures={} bytes={} "
                        "coverage=INCOMPLETE", frame, FrameProbe().draws, captures, captured_bytes);
            limit_reported = true;
        }
        return;
    }
    const u32 sequence = ++captures;
    captured_bytes += bytes;
    auto snapshot =
        std::make_unique<VideoCore::Buffer>(instance, 0, bytes, VideoCore::MemoryType::HostCached);
    const vk::BufferCopy copy{offset, 0, bytes};
    runtime.CopyBuffer(&source, snapshot.get(), std::span{&copy, 1});
    scheduler.DeferPriorityOperation([snapshot = std::move(snapshot), cpu = stage.flattened_ud_buf,
                                      origins = stage.flattened_ud_sources, frame,
                                      draw = FrameProbe().draws, shader = stage.pgm_hash,
                                      bytes, sequence]() mutable {
        snapshot->Invalidate(0, bytes);
        if (Shader::Diagnostics::VertexOutputFeedbackEnabled()) {
            const auto dir = Common::FS::GetUserPath(Common::FS::PathType::ShaderDir) / "frame-probes";
            std::error_code error;
            std::filesystem::create_directories(dir, error);
            if (!error) {
                const auto path = dir / fmt::format("f{}-d{}-{:#x}-s{}.flatbuf.bin", frame, draw,
                                                    shader, sequence);
                std::ofstream output(path, std::ios::binary);
                output.write(reinterpret_cast<const char*>(snapshot->mapped_data.data()), bytes);
                std::ofstream addresses(path.string() + ".origins.bin", std::ios::binary);
                addresses.write(reinterpret_cast<const char*>(origins.data()),
                                origins.size() * sizeof(origins[0]));
            }
        }
        u32 differences{}, reported{};
        for (size_t i = 0; i < cpu.size(); ++i) {
            u32 gpu{};
            std::memcpy(&gpu, snapshot->mapped_data.data() + i * sizeof(u32), sizeof(u32));
            if (gpu == cpu[i]) {
                continue;
            }
            ++differences;
            if (reported++ < 32) {
                LOG_INFO(Render_Vulkan,
                         "FrameProbeScalarValue: frame={} after_draw={} shader={:#x} "
                         "slot={} guest={:#x} cpu={:#x} gpu={:#x}",
                         frame, draw, shader, i, i < origins.size() ? origins[i] : 0, cpu[i], gpu);
            }
        }
        LOG_INFO(Render_Vulkan,
                 "FrameProbeScalarReadback: frame={} after_draw={} shader={:#x} "
                 "sequence={} words={} differences={} status=COMPLETE",
                 frame, draw, shader, sequence, cpu.size(), differences);
    });
}

inline void CaptureFrameProbeBuffer(const Instance& instance, Scheduler& scheduler,
                                    Runtime& runtime, const VideoCore::Buffer& source, u64 offset,
                                    u64 bytes, const Shader::Info& stage, u32 resource,
                                    const AmdGpu::Buffer& sharp) {
    static const bool enabled = [] {
        const char* value = std::getenv("SHADPS4_FRAME_PROBE_BUFFERS");
        return value && std::strcmp(value, "1") == 0;
    }();
    const auto frame = FrameProbe().frame;
    if (!enabled || !FrameProbeEnabled() || !frame ||
        (!Shader::ShaderDumpTargets().Matches(stage.pgm_hash) &&
         !(Shader::Diagnostics::VertexOutputFeedbackEnabled() &&
           stage.hw_stage == Shader::HwStage::Vertex))) {
        return;
    }
    static thread_local u32 last_frame{};
    static thread_local u64 captured_bytes{};
    static thread_local bool limit_reported{};
    static thread_local std::unordered_set<std::string> captured;
    if (last_frame != frame) {
        captured.clear();
        captured_bytes = 0;
        limit_reported = false;
        last_frame = frame;
    }
    const auto key = fmt::format("{}-{:#x}-{}", stage.hw_stage, stage.pgm_hash, resource);
    const auto binding_key = fmt::format("{}-{}-{}-{}-{}", key, u64(sharp.base_address), bytes,
                                         u32(sharp.stride), sharp.num_records);
    if (captured.contains(binding_key)) {
        return;
    }
    if (!bytes || offset > source.SizeBytes() || bytes > source.SizeBytes() - offset) {
        return;
    }
    if (bytes > 4_MB || captured.size() >= 256 || bytes > 64_MB - captured_bytes) {
        if (!limit_reported) {
            LOG_WARNING(Render_Vulkan,
                        "FrameProbeBufferLimit: frame={} after_draw={} captures={} bytes={} "
                        "coverage=INCOMPLETE", frame, FrameProbe().draws, captured.size(),
                        captured_bytes);
            limit_reported = true;
        }
        return;
    }
    captured.insert(binding_key);
    captured_bytes += bytes;
    auto snapshot =
        std::make_unique<VideoCore::Buffer>(instance, 0, bytes, VideoCore::MemoryType::HostCached);
    const vk::BufferCopy copy{offset, 0, bytes};
    runtime.CopyBuffer(&source, snapshot.get(), std::span{&copy, 1});
    scheduler.DeferPriorityOperation([snapshot = std::move(snapshot), frame,
                                      draw = FrameProbe().draws, key, sharp, bytes]() {
        snapshot->Invalidate(0, bytes);
        const auto dir = Common::FS::GetUserPath(Common::FS::PathType::ShaderDir) / "frame-probes";
        std::error_code error;
        std::filesystem::create_directories(dir, error);
        if (error) {
            return;
        }
        const auto path = dir / fmt::format("f{}-d{}-{}.buffer.bin", frame, draw, key);
        std::ofstream output(path, std::ios::binary);
        output.write(reinterpret_cast<const char*>(snapshot->mapped_data.data()), bytes);
        output.close();
        std::ofstream metadata(path.string() + ".txt");
        metadata << fmt::format("frame={}\nafter_draw={}\nresource={}\nguest={}\nbytes={}\n"
                                "stride={}\nrecords={}\n",
                                frame, draw, key, u64(sharp.base_address), bytes, u32(sharp.stride),
                                sharp.num_records);
        metadata.close();
        LOG_INFO(Render_Vulkan,
                 "FrameProbeBuffer: frame={} after_draw={} resource={} bytes={} "
                 "status={} path={}",
                 frame, draw, key, bytes, output && metadata ? "SAVED" : "WRITE_FAILED",
                 path.string());
    });
}

inline void CaptureFrameProbeDispatch(const Instance& instance, Scheduler& scheduler,
                                      Runtime& runtime, const VideoCore::Buffer& source, u64 offset,
                                      u64 guest, u64 shader) {
    const auto frame = FrameProbe().frame;
    if (!FrameProbeEnabled() || !frame) {
        return;
    }
    static thread_local u32 last_frame{}, sequence{};
    if (last_frame != frame) {
        last_frame = frame;
        sequence = 0;
    }
    if (sequence >= 256) {
        return;
    }
    const u32 ordinal = ++sequence;
    constexpr u64 bytes = sizeof(vk::DispatchIndirectCommand);
    if (offset > source.SizeBytes() || bytes > source.SizeBytes() - offset) {
        return;
    }
    auto snapshot =
        std::make_unique<VideoCore::Buffer>(instance, 0, bytes, VideoCore::MemoryType::HostCached);
    const vk::BufferCopy copy{offset, 0, bytes};
    runtime.CopyBuffer(&source, snapshot.get(), std::span{&copy, 1});
    scheduler.DeferPriorityOperation([snapshot = std::move(snapshot), frame, ordinal,
                                      draw = FrameProbe().draws, guest, shader]() {
        snapshot->Invalidate(0, sizeof(vk::DispatchIndirectCommand));
        vk::DispatchIndirectCommand command{};
        std::memcpy(&command, snapshot->mapped_data.data(), sizeof(command));
        LOG_INFO(Render_Vulkan,
                 "FrameProbeDispatchArguments: frame={} after_draw={} ordinal={} "
                 "shader={:#x} guest={:#x} groups={}x{}x{} timeline_complete=YES",
                 frame, draw, ordinal, shader, guest, command.x, command.y, command.z);
    });
}

template <typename Sharp>
inline bool FrameProbeBindingChanged(const Shader::Info& stage, u32 resource, const Sharp& sharp,
                                     bool written) {
    const auto frame = FrameProbe().frame;
    if (!FrameProbeEnabled() || !frame) {
        return false;
    }
    using Words = std::array<u32, sizeof(Sharp) / sizeof(u32)>;
    using State = std::pair<Words, bool>;
    static thread_local std::unordered_map<std::string, State> bindings;
    static thread_local u32 last_frame{}, events{};
    if (last_frame != frame) {
        last_frame = frame;
        events = 0;
        bindings.clear();
    }
    const auto key = fmt::format("{}-{:#x}-{}", stage.hw_stage, stage.pgm_hash, resource);
    const State state{std::bit_cast<Words>(sharp), written};
    const auto found = bindings.find(key);
    if (found != bindings.end() && found->second == state) {
        return false;
    }
    if (events++ >= 8192) {
        if (events == 8193) {
            LOG_WARNING(Render_Vulkan,
                        "FrameProbeBindingLimit: frame={} after_draw={} descriptor_words={} "
                        "changes=8192 coverage=INCOMPLETE",
                        frame, FrameProbe().draws, Words{}.size());
        }
        return false;
    }
    bindings.insert_or_assign(key, state);
    return true;
}

inline void TraceFrameProbeBufferBinding(const Shader::Info& stage, u32 resource,
                                         const AmdGpu::Buffer& sharp, bool written) {
    if (!FrameProbeBindingChanged(stage, resource, sharp, written)) {
        return;
    }
    const auto frame = FrameProbe().frame;
    const u64 bytes = sharp.stride ? u64(sharp.stride) * sharp.num_records : sharp.num_records;
    LOG_INFO(Render_Vulkan,
             "FrameProbeBufferBinding: frame={} after_draw={} shader={:#x} stage={} "
             "resource={} guest={:#x} bytes={} stride={} records={} written={} valid={} "
             "confirmed_store=NO trace_scope=CHANGES_PER_SHADER_RESOURCE",
             frame, FrameProbe().draws, stage.pgm_hash, stage.hw_stage, resource,
             u64(sharp.base_address), bytes, u32(sharp.stride), sharp.num_records, written,
             sharp.Valid());
}

inline void TraceFrameProbeImageBinding(const Shader::Info& stage, u32 resource,
                                        const AmdGpu::Image& sharp, bool written) {
    if (!FrameProbeBindingChanged(stage, resource, sharp, written)) {
        return;
    }
    const auto frame = FrameProbe().frame;
    LOG_INFO(Render_Vulkan,
             "FrameProbeImageBinding: frame={} after_draw={} shader={:#x} stage={} "
             "resource={} guest={:#x} width={} height={} data_format={} number_format={} "
             "written={} valid={} trace_scope=CHANGES_PER_SHADER_RESOURCE",
             frame, FrameProbe().draws, stage.pgm_hash, stage.hw_stage, resource, sharp.Address(),
             u32(sharp.width) + 1, u32(sharp.height) + 1, u32(sharp.GetDataFmt()),
             u32(sharp.GetNumberFmt()), written, sharp.Valid());
}

struct FrameProbeImage {
    vk::Image image;
    vk::ImageLayout layout;
    vk::Format format;
    vk::ImageAspectFlags barrier_aspects;
    u64 guest{};
    u32 width{}, height{}, level{}, layer{}, attachment{};
    bool depth{};
};

inline u32 FrameProbePixelBytes(vk::Format format) {
    switch (format) {
    case vk::Format::eR16G16B16A16Unorm:
    case vk::Format::eR16G16B16A16Sfloat:
        return 8;
    case vk::Format::eR16G16Sfloat:
    case vk::Format::eR16G16Snorm:
    case vk::Format::eR16G16Unorm:
    case vk::Format::eR32Sfloat:
    case vk::Format::eD32Sfloat:
    case vk::Format::eD32SfloatS8Uint:
    case vk::Format::eR8G8B8A8Unorm:
    case vk::Format::eR8G8B8A8Srgb:
    case vk::Format::eB8G8R8A8Unorm:
    case vk::Format::eB8G8R8A8Srgb:
        return 4;
    case vk::Format::eR8G8Unorm:
        return 2;
    default:
        return 0;
    }
}

inline void CaptureFrameProbePass(const Instance& instance, Scheduler& scheduler,
                                  const RenderState& render_state,
                                  std::vector<FrameProbeImage> targets, u32 frame, u32 last_draw) {
    auto& probe = FrameProbe();
    const u32 pass = probe.passes++;
    LOG_INFO(Render_Vulkan,
             "FrameProbePassEnd: frame={} pass={} last_draw={} size={}x{} targets={} "
             "depth_present={} depth_clear={} stencil_clear={}", frame, pass,
             last_draw, render_state.width, render_state.height, targets.size(),
             render_state.depth_stencil_attachment.has_depth,
             render_state.depth_stencil_attachment.depth_clear,
             render_state.depth_stencil_attachment.stencil_clear);
    if (targets.empty() || render_state.width < 640 || render_state.height < 360) {
        return;
    }
    if (!probe.ImagesEnabled()) {
        for (const auto& target : targets) {
            if (target.layout == vk::ImageLayout::eUndefined) {
                continue;
            }
            const u64 row_bytes = u64(target.width) * FrameProbePixelBytes(target.format);
            probe.image_full_bytes += row_bytes * target.height;
            probe.image_row_bytes += row_bytes;
        }
        return;
    }
    const auto directory =
        Common::FS::GetUserPath(Common::FS::PathType::ShaderDir) / "frame-probes";
    std::error_code error;
    std::filesystem::create_directories(directory, error);
    if (error) {
        LOG_ERROR(Render_Vulkan, "FrameProbeImages: directory_error={}", error.message());
        return;
    }
    for (const auto& target : targets) {
        const u32 bpp = FrameProbePixelBytes(target.format);
        if (!bpp || !target.width || !target.height ||
            target.layout == vk::ImageLayout::eUndefined) {
            LOG_INFO(Render_Vulkan, "FrameProbeImageSkip: frame={} pass={} attachment={} format={}",
                     frame, pass, target.attachment, u32(target.format));
            continue;
        }
        const u32 row_step = probe.image_row_step;
        const u32 rows = (target.height + row_step - 1) / row_step;
        const u64 bytes = u64(target.width) * rows * bpp;
        if (bytes > 128_MB - probe.bytes) {
            ++probe.image_budget_skips;
            LOG_INFO(Render_Vulkan, "FrameProbeImageSkip: frame={} pass={} reason=BUDGET", frame,
                     pass);
            continue;
        }
        ++probe.images;
        probe.bytes += bytes;
        auto buffer = std::make_unique<VideoCore::Buffer>(instance, 0, bytes,
                                                          VideoCore::MemoryType::HostCached);
        std::vector<vk::BufferImageCopy> regions;
        regions.reserve(rows);
        for (u32 row = 0; row < rows; ++row) {
            regions.push_back(vk::BufferImageCopy{
                .bufferOffset = u64(row) * target.width * bpp,
                .imageSubresource = {target.depth ? vk::ImageAspectFlagBits::eDepth
                                                  : vk::ImageAspectFlagBits::eColor,
                                     target.level, target.layer, 1},
                .imageOffset = {0, s32(row * row_step), 0},
                .imageExtent = {target.width, 1, 1},
            });
        }
        auto barrier = vk::ImageMemoryBarrier2{
            .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
            .srcAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eCopy,
            .dstAccessMask = vk::AccessFlagBits2::eTransferRead,
            .oldLayout = target.layout,
            .newLayout = vk::ImageLayout::eTransferSrcOptimal,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = target.image,
            .subresourceRange = {target.barrier_aspects, target.level, 1, target.layer, 1},
        };
        const auto cmd = scheduler.CommandBuffer();
        cmd.pipelineBarrier2(
            vk::DependencyInfo{.imageMemoryBarrierCount = 1, .pImageMemoryBarriers = &barrier});
        cmd.copyImageToBuffer(target.image, vk::ImageLayout::eTransferSrcOptimal, buffer->Handle(),
                              regions);
        std::swap(barrier.srcStageMask, barrier.dstStageMask);
        std::swap(barrier.srcAccessMask, barrier.dstAccessMask);
        std::swap(barrier.oldLayout, barrier.newLayout);
        const vk::BufferMemoryBarrier2 host_barrier{
            .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
            .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
            .dstStageMask = vk::PipelineStageFlagBits2::eHost,
            .dstAccessMask = vk::AccessFlagBits2::eHostRead,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = buffer->Handle(),
            .offset = 0,
            .size = bytes,
        };
        cmd.pipelineBarrier2(vk::DependencyInfo{.bufferMemoryBarrierCount = 1,
                                                .pBufferMemoryBarriers = &host_barrier,
                                                .imageMemoryBarrierCount = 1,
                                                .pImageMemoryBarriers = &barrier});
        const auto path =
            directory / fmt::format("f{}-p{}-a{}.raw", frame, pass, target.attachment);
        scheduler.DeferPriorityOperation(
            [buffer = std::move(buffer), path, target, bytes, rows, frame, pass, last_draw,
             row_step]() {
                buffer->Invalidate(0, bytes);
                std::ofstream output(path, std::ios::binary);
                if (buffer->mapped_data.size() < bytes || !output) {
                    LOG_ERROR(Render_Vulkan, "FrameProbeImage: result=SAVE_FAILED path={}",
                              path.string());
                    return;
                }
                output.write(reinterpret_cast<const char*>(buffer->mapped_data.data()), bytes);
                std::ofstream metadata(path.string() + ".txt");
                metadata << "frame=" << frame << "\npass=" << pass << "\nlast_draw=" << last_draw
                         << "\nguest=" << target.guest << "\nwidth=" << target.width
                         << "\nheight=" << target.height << "\nrows=" << rows
                         << "\nrow_step=" << row_step
                         << "\nformat=" << u32(target.format) << "\ndepth=" << target.depth
                         << "\nlevel=" << target.level << "\nlayer=" << target.layer << "\n";
                output.flush();
                metadata.flush();
                if (!output || !metadata) {
                    LOG_ERROR(Render_Vulkan, "FrameProbeImage: result=WRITE_FAILED path={}",
                              path.string());
                    return;
                }
                LOG_INFO(Render_Vulkan,
                         "FrameProbeImage: result=SAVED frame={} pass={} last_draw={} path={} "
                         "bytes={} guest={:#x}",
                         frame, pass, last_draw, path.string(), bytes, target.guest);
            });
    }
}

} // namespace Vulkan::Diagnostics
