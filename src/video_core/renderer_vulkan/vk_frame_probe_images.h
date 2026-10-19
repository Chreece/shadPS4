// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <filesystem>
#include <fstream>
#include <memory>
#include <vector>
#include "common/path_util.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_frame_probe.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan::Diagnostics {

struct FrameProbeImage {
    vk::Image image;
    vk::ImageLayout layout;
    vk::Format format;
    vk::ImageAspectFlags barrier_aspects;
    u64 guest{};
    u32 width{}, height{}, level{}, layer{}, attachment{};
    bool depth{};
};

inline void CaptureFrameProbePass(const Instance& instance, Scheduler& scheduler,
                                  const RenderState& render_state,
                                  std::vector<FrameProbeImage> targets, u32 frame, u32 last_draw) {
    auto& probe = FrameProbe();
    const u32 pass = probe.passes++;
    LOG_INFO(Render_Vulkan,
             "FrameProbePassEnd: frame={} pass={} last_draw={} size={}x{} targets={}", frame, pass,
             last_draw, render_state.width, render_state.height, targets.size());
    if (!probe.ImagesEnabled() || !render_state.num_color_attachments || render_state.width < 640 ||
        render_state.height < 360) {
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
        u32 bpp{};
        switch (target.format) {
        case vk::Format::eR16G16B16A16Sfloat:
            bpp = 8;
            break;
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
            bpp = 4;
            break;
        default:
            break;
        }
        if (!bpp || !target.width || !target.height ||
            target.layout == vk::ImageLayout::eUndefined) {
            LOG_INFO(Render_Vulkan, "FrameProbeImageSkip: frame={} pass={} attachment={} format={}",
                     frame, pass, target.attachment, u32(target.format));
            continue;
        }
        constexpr u32 row_step = 8;
        const u32 rows = (target.height + row_step - 1) / row_step;
        const u64 bytes = u64(target.width) * rows * bpp;
        if (probe.images >= 96 || bytes > 64_MB - probe.bytes) {
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
            [buffer = std::move(buffer), path, target, bytes, rows, frame, pass, last_draw]() {
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
                         << "\nheight=" << target.height << "\nrows=" << rows << "\nrow_step=8"
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
