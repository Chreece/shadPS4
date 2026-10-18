// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Read-only, one-shot GPU framebuffer stage probe. Captures actual Vulkan
// contents after the guest has rendered and BEFORE host-side FSR/present.
// No guest memory, shaders, texture descriptors or normal rendering are
// modified. Opt-in with SHADPS4_GOT_MENU_STAGE_PIXELS=1. The targets are
// exact addresses observed in the previous GoT framebuffer source graph.
#include <array>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <span>
#include <string>

#include "common/logging/log.h"
#include "common/path_util.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/texture_cache/texture_cache.h"

namespace Vulkan::Diagnostics {

struct GotMenuStageReadback {
    VideoCore::Buffer buffer;
    std::filesystem::path raw_path;
    u64 guest_address{};
    u64 byte_size{};
    u32 width{}, height{};
    vk::Format pixel_format{};
    bool is_depth{};

    GotMenuStageReadback(const Instance& instance, const std::filesystem::path& path,
                         u64 guest_addr, u64 size, u32 w, u32 h,
                         vk::Format format, bool depth)
        : buffer{instance, 0, size, VideoCore::MemoryType::HostCached},
          raw_path{path}, guest_address{guest_addr}, byte_size{size},
          width{w}, height{h}, pixel_format{format}, is_depth{depth} {}
};

inline void MaybeCaptureGotMenuStages(const Instance& instance, Scheduler& scheduler,
                                      Runtime& runtime, VideoCore::TextureCache& textures) {
    static const bool enabled=[] {
        const char* const flag=std::getenv("SHADPS4_GOT_MENU_STAGE_PIXELS");
        return flag && flag[0]=='1' && flag[1]=='\0';
    }();
    if (!enabled) return;

    using Clock=std::chrono::steady_clock;
    static const auto first_frame=Clock::now();
    static std::atomic<bool> executed{false};
    if (executed.load(std::memory_order_relaxed) ||
        Clock::now()-first_frame < std::chrono::seconds(30)) return;
    if (executed.exchange(true,std::memory_order_acq_rel)) return;

    struct Spec {const char* name;u64 address;u32 estimated_bytes_per_pixel;};
    // Scene color, scene depth, first blur and final composed (pre-present).
    constexpr std::array<Spec,4> targets{{
        {"scene_rgba16f",0x15203f8000ull,8u},
        {"scene_depth32",0x1504a08000ull,4u},
        {"depth_blur32",0x1525478000ull,4u},
        {"composed_rgba8",0x1504210000ull,4u},
    }};
    const auto target_dir=Common::FS::GetUserPath(Common::FS::PathType::ShaderDir) /
                          "got-menu-probes";
    std::filesystem::create_directories(target_dir);

    u32 captured{};
    for (const auto& probe:targets) {
        // The image must already contain valid GPU-produced data. Never
        // create a new image, refresh from stale CPU pages or force a view.
        const auto id=textures.FindImageFromRange(
            probe.address,1920ull*1080ull*probe.estimated_bytes_per_pixel,true);
        if (!id) {
            LOG_INFO(Render_Vulkan,
                     "GotMenuStagePixels: stage={} guest={:#x} "
                     "result=NO_LIVE_GPU_IMAGE",
                     probe.name,probe.address);
            continue;
        }
        auto& image=textures.GetImage(id);
        const auto& info=image.info;
        const u32 width=info.size.width,height=info.size.height;
        const auto format=info.pixel_format;
        const bool depth=bool(info.props.is_depth);
        u32 bytes_per_pixel{};
        switch (format) {
        case vk::Format::eR16G16B16A16Sfloat: bytes_per_pixel=8;break;
        case vk::Format::eD32Sfloat:
        case vk::Format::eR32Sfloat:
        case vk::Format::eR8G8B8A8Unorm:
        case vk::Format::eR8G8B8A8Srgb:
        case vk::Format::eB8G8R8A8Unorm: bytes_per_pixel=4;break;
        default: break;
        }
        if (info.guest_address!=probe.address ||
            !image.HasGpuValidData() || info.num_samples!=1 ||
            width!=1920 || height!=1080 || bytes_per_pixel==0) {
            LOG_INFO(Render_Vulkan,
                     "GotMenuStagePixels: stage={} requested={:#x} "
                     "result=UNSUITABLE_BACKING found={:#x} depth={} "
                     "vk_format={} extent={}x{} samples={} valid_gpu={}",
                     probe.name,probe.address,info.guest_address,depth,
                     vk::to_string(format),width,height,info.num_samples,
                     image.HasGpuValidData());
            continue;
        }

        const u64 data_bytes=u64(width)*height*bytes_per_pixel;
        const auto path=target_dir / (std::string(probe.name)+".raw");
        auto readback=std::make_unique<GotMenuStageReadback>(
            instance,path,probe.address,data_bytes,width,height,format,depth);
        const vk::BufferImageCopy copy{
            .bufferOffset=0,
            .bufferRowLength=0,
            .bufferImageHeight=0,
            .imageSubresource{
                .aspectMask=depth ? vk::ImageAspectFlagBits::eDepth
                                  : vk::ImageAspectFlagBits::eColor,
                .mipLevel=0,
                .baseArrayLayer=0,
                .layerCount=1,
            },
            .imageOffset={0,0,0},
            .imageExtent={width,height,1},
        };
        // Existing screenshot code uses the same Runtime::DownloadImage
        // pathway, ensuring its standard guest image transitions/barriers.
        runtime.DownloadImage(&image,&readback->buffer,std::span{&copy,1});
        runtime.Transit(&image,vk::ImageLayout::eShaderReadOnlyOptimal,
                        vk::PipelineStageFlagBits2::eFragmentShader,
                        vk::AccessFlagBits2::eShaderRead);
        runtime.FlushBarriers();

        LOG_INFO(Render_Vulkan,
                 "GotMenuStagePixels: stage={} result=GPU_READBACK_SUBMITTED "
                 "guest={:#x} format={} depth={} bytes={}",
                 probe.name,probe.address,vk::to_string(format),depth,data_bytes);
        ++captured;
        scheduler.DeferPriorityOperation([snapshot=std::move(readback)]() mutable {
            snapshot->buffer.Invalidate(0,snapshot->byte_size);
            if (snapshot->buffer.mapped_data.size() < snapshot->byte_size) {
                LOG_WARNING(Render_Vulkan,
                            "GotMenuStagePixels: stage_path={} result=UNMAPPED_BUFFER",
                            snapshot->raw_path.string());
                return;
            }
            std::ofstream data(snapshot->raw_path,std::ios::binary);
            if (!data) {
                LOG_WARNING(Render_Vulkan,
                            "GotMenuStagePixels: stage_path={} result=CANNOT_SAVE",
                            snapshot->raw_path.string());
                return;
            }
            data.write(reinterpret_cast<const char*>(
                           snapshot->buffer.mapped_data.data()),
                       static_cast<std::streamsize>(snapshot->byte_size));
            data.close();
            std::ofstream meta(snapshot->raw_path.string()+".txt");
            meta<<"guest_address="<<std::hex<<snapshot->guest_address<<std::dec
                <<"\nwidth="<<snapshot->width<<"\nheight="<<snapshot->height
                <<"\nbytes="<<snapshot->byte_size
                <<"\nformat="<<static_cast<u32>(snapshot->pixel_format)
                <<"\ndepth="<<snapshot->is_depth<<"\n";
            meta.close();
            LOG_INFO(Render_Vulkan,
                     "GotMenuStagePixels: stage_path={} result=SAVED bytes={}",
                     snapshot->raw_path.string(),snapshot->byte_size);
        });
    }
    LOG_INFO(Render_Vulkan,
             "GotMenuStagePixels: one_shot=YES submitted={} eligible=4 "
             "rendering_unchanged=YES",
             captured);
}

} // namespace Vulkan::Diagnostics
