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
    u64 valid_bytes{};
    u32 width{}, height{};
    vk::Format pixel_format{};
    bool is_depth{};

    GotMenuStageReadback(const Instance& instance, const std::filesystem::path& path,
                         u64 guest_addr, u64 staging_bytes, u64 output_bytes,
                         u32 w, u32 h, vk::Format format, bool depth)
        : buffer{instance, 0, staging_bytes, VideoCore::MemoryType::HostCached},
          raw_path{path}, guest_address{guest_addr}, byte_size{staging_bytes},
          valid_bytes{output_bytes}, width{w}, height{h},
          pixel_format{format}, is_depth{depth} {}
};

inline bool MaybeCaptureGotMenuStages(const Instance& instance, Scheduler& scheduler,
                                      Runtime& runtime, VideoCore::TextureCache& textures) {
    static const bool enabled=[] {
        const char* const flag=std::getenv("SHADPS4_GOT_MENU_STAGE_PIXELS");
        return flag && flag[0]=='1' && flag[1]=='\0';
    }();
    if (!enabled) return false;

    // Prior captures triggered while the Sucker Punch logo was still on
    // screen, then compared its GPU pixels with a LATER, white menu
    // screenshot. That was not a valid same-frame diagnosis. Defer the
    // one-shot probe until the menu is visible (60s after first frame).
    using Clock=std::chrono::steady_clock;
    static const auto first_frame=Clock::now();
    static std::atomic<bool> executed{false};
    const auto elapsed=std::chrono::duration_cast<std::chrono::seconds>(
        Clock::now()-first_frame).count();
    if (executed.load(std::memory_order_relaxed) || elapsed<60) return false;
    if (executed.exchange(true,std::memory_order_acq_rel)) return false;
    LOG_INFO(Render_Vulkan,
             "GotMenuSynchronizedCapture: since_first_frame_s={} "
             "sync_guest_and_host_same_frame=YES stage=BEFORE_HOST_POSTPROCESS",
             elapsed);

    struct Spec {const char* name;u64 address;vk::Format expected_format;};
    // Confirmed from the 20:22 source graph:
    // - 0x1522020000: actual 1920x1080 RGBA16_FLOAT scene HDR buffer
    // - 0x15203f8000: separate RGBA8 scene/color buffer (NOT RGBA16F)
    // The prior "scene_rgba16f" address was wrong, hiding valid scene data.
    constexpr std::array<Spec,7> targets{{
        {"scene_rgba16f",0x1522020000ull,vk::Format::eR16G16B16A16Sfloat},
        {"scene_rgba8",0x15203f8000ull,vk::Format::eR8G8B8A8Unorm},
        {"scene_depth32",0x1504a08000ull,vk::Format::eD32SfloatS8Uint},
        {"depth_blur32",0x1525478000ull,vk::Format::eR32Sfloat},
        {"composed_rgba8",0x1504210000ull,vk::Format::eR8G8B8A8Unorm},
        {"video_frame_a",0x1505578000ull,vk::Format::eUndefined},
        {"video_frame_b",0x1505d70000ull,vk::Format::eUndefined},
    }};
    const auto target_dir=Common::FS::GetUserPath(Common::FS::PathType::ShaderDir) /
                          "got-menu-probes";
    std::filesystem::create_directories(target_dir);

    u32 captured{};
    for (const auto& probe:targets) {
        // The image must already contain valid GPU-produced data. Never
        // create a new image, refresh from stale CPU pages or force a view.
        // Find by exact guest address + expected VkFormat and dimensions,
        // NOT width*height*bpp. PS4 tiling pads guest allocations, and that
        // old lookup silently rejected real, GPU-resident scene textures.
        const auto id=textures.FindGpuImageForStageProbe(
            probe.address,probe.expected_format,1920u,1080u);
        if (!id) {
            LOG_INFO(Render_Vulkan,
                     "GotMenuStagePixels: stage={} guest={:#x} "
                     "result=NO_MATCHING_GPU_VALID_CACHE_IMAGE "
                     "expected_format={}",
                     probe.name,probe.address,vk::to_string(probe.expected_format));
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
        case vk::Format::eD32SfloatS8Uint:
        case vk::Format::eR32Sfloat:
        case vk::Format::eR8G8B8A8Unorm:
        case vk::Format::eR8G8B8A8Srgb:
        case vk::Format::eB8G8R8A8Unorm:
        case vk::Format::eB8G8R8A8Srgb: bytes_per_pixel=4;break;
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
        // Runtime::DownloadImage computes vk::blockSize(D32SfloatS8Uint)
        // as EIGHT bytes per texel, even for a depth-only aspect copy which
        // produces FOUR bytes per pixel. Allocate the larger amount for the
        // existing GPU barrier/range accounting; save only actual D32 depth.
        const u64 gpu_buffer_bytes =
            format==vk::Format::eD32SfloatS8Uint ? data_bytes*2u : data_bytes;
        const auto path=target_dir / (std::string(probe.name)+".raw");
        // Preserve the full depth/stencil staging buffer: the device may
        // pack a D32S8 depth-only copy differently from vk::blockSize's
        // conservative 8-byte accounting. Do not silently truncate evidence.
        const u64 saved_bytes =
            format==vk::Format::eD32SfloatS8Uint ? gpu_buffer_bytes : data_bytes;
        auto readback=std::make_unique<GotMenuStageReadback>(
            instance,path,probe.address,gpu_buffer_bytes,saved_bytes,
            width,height,format,depth);
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
                 "guest={:#x} format={} depth={} bytes={} gpu_staging_bytes={}",
                 probe.name,probe.address,vk::to_string(format),depth,
                 data_bytes,gpu_buffer_bytes);
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
                       static_cast<std::streamsize>(snapshot->valid_bytes));
            data.close();
            std::ofstream meta(snapshot->raw_path.string()+".txt");
            meta<<"guest_address="<<std::hex<<snapshot->guest_address<<std::dec
                <<"\nwidth="<<snapshot->width<<"\nheight="<<snapshot->height
                <<"\nbytes="<<snapshot->valid_bytes
                <<"\ngpu_buffer_bytes="<<snapshot->byte_size
                <<"\nformat="<<static_cast<u32>(snapshot->pixel_format)
                <<"\ndepth="<<snapshot->is_depth<<"\n";
            meta.close();
            LOG_INFO(Render_Vulkan,
                     "GotMenuStagePixels: stage_path={} result=SAVED bytes={}",
                     snapshot->raw_path.string(),snapshot->valid_bytes);
        });
    }
    LOG_INFO(Render_Vulkan,
             "GotMenuStagePixels: one_shot=YES submitted={} eligible=7 "
             "rendering_unchanged=YES synchronized_capture=YES",
             captured);
    return captured>0;
}

// Capture the HOST postprocessing output of the SAME exact frame as the
// 60-second guest-stage snapshots. This is the only valid way to measure
// the apparent whitening separately from guest shader output. A root-window
// screenshot taken 20-30s later compared different game scenes and cannot
// establish the gain. One transfer copy, no host PP/shader changes.
inline void CaptureGotMenuHostPostprocess(
    const Instance& instance, Scheduler& scheduler, vk::CommandBuffer cmdbuf,
    vk::Image frame_image, u32 width, u32 height, vk::Format format,
    float gamma, bool hdr, bool srgb_input) {
    const bool supported_format=
        format==vk::Format::eR8G8B8A8Unorm ||
        format==vk::Format::eB8G8R8A8Unorm;
    if (!frame_image || !supported_format ||
        width!=1920 || height!=1080) {
        LOG_INFO(Render_Vulkan,
                 "GotMenuHostPP: result=UNSUPPORTED_CAPTURE "
                 "format={} extent={}x{} gamma={} hdr={} srgb_input={}",
                 vk::to_string(format),width,height,gamma,hdr,srgb_input);
        return;
    }
    const auto path=Common::FS::GetUserPath(Common::FS::PathType::ShaderDir)/
        "got-menu-probes"/"host_postprocess.raw";
    const u64 bytes=u64(width)*u64(height)*4u;
    auto snapshot=std::make_unique<GotMenuStageReadback>(
        instance,path,0,bytes,bytes,width,height,format,false);
    const vk::ImageSubresourceRange subresource{
        .aspectMask=vk::ImageAspectFlagBits::eColor,
        .baseMipLevel=0,.levelCount=1,
        .baseArrayLayer=0,.layerCount=1,
    };
    const vk::ImageMemoryBarrier2 enter{
        .srcStageMask=vk::PipelineStageFlagBits2::eColorAttachmentOutput,
        .srcAccessMask=vk::AccessFlagBits2::eColorAttachmentWrite,
        .dstStageMask=vk::PipelineStageFlagBits2::eCopy,
        .dstAccessMask=vk::AccessFlagBits2::eTransferRead,
        .oldLayout=vk::ImageLayout::eGeneral,
        .newLayout=vk::ImageLayout::eTransferSrcOptimal,
        .image=frame_image,
        .subresourceRange=subresource,
    };
    cmdbuf.pipelineBarrier2(vk::DependencyInfo{
        .imageMemoryBarrierCount=1,
        .pImageMemoryBarriers=&enter,
    });
    const vk::BufferImageCopy copy{
        .bufferOffset=0,.bufferRowLength=0,.bufferImageHeight=0,
        .imageSubresource{
            .aspectMask=vk::ImageAspectFlagBits::eColor,
            .mipLevel=0,.baseArrayLayer=0,.layerCount=1,
        },
        .imageOffset={0,0,0},.imageExtent={width,height,1},
    };
    cmdbuf.copyImageToBuffer(frame_image,vk::ImageLayout::eTransferSrcOptimal,
                             snapshot->buffer.Handle(),std::array{copy});
    const vk::ImageMemoryBarrier2 restore{
        .srcStageMask=vk::PipelineStageFlagBits2::eCopy,
        .srcAccessMask=vk::AccessFlagBits2::eTransferRead,
        .dstStageMask=vk::PipelineStageFlagBits2::eFragmentShader,
        .dstAccessMask=vk::AccessFlagBits2::eShaderRead,
        .oldLayout=vk::ImageLayout::eTransferSrcOptimal,
        .newLayout=vk::ImageLayout::eGeneral,
        .image=frame_image,
        .subresourceRange=subresource,
    };
    cmdbuf.pipelineBarrier2(vk::DependencyInfo{
        .imageMemoryBarrierCount=1,
        .pImageMemoryBarriers=&restore,
    });
    LOG_INFO(Render_Vulkan,
             "GotMenuHostPP: result=READBACK_SUBMITTED bytes={} "
             "format={} gamma={} hdr={} srgb_input={} same_frame=YES",
             bytes,vk::to_string(format),gamma,hdr,srgb_input);
    scheduler.DeferPriorityOperation(
        [snapshot=std::move(snapshot),gamma,hdr,srgb_input]() mutable {
            snapshot->buffer.Invalidate(0,snapshot->valid_bytes);
            if (snapshot->buffer.mapped_data.size() < snapshot->valid_bytes) {
                LOG_WARNING(Render_Vulkan,
                            "GotMenuHostPP: result=UNMAPPED_HOST_BUFFER");
                return;
            }
            std::ofstream raw(snapshot->raw_path,std::ios::binary);
            if (!raw) {
                LOG_WARNING(Render_Vulkan,
                            "GotMenuHostPP: result=CANNOT_SAVE");
                return;
            }
            raw.write(reinterpret_cast<const char*>(
                          snapshot->buffer.mapped_data.data()),
                      static_cast<std::streamsize>(snapshot->valid_bytes));
            raw.close();
            std::ofstream meta(snapshot->raw_path.string()+".txt");
            meta<<"width="<<snapshot->width
                <<"\nheight="<<snapshot->height
                <<"\nbytes="<<snapshot->valid_bytes
                <<"\nformat="<<static_cast<u32>(snapshot->pixel_format)
                <<"\ngamma="<<gamma
                <<"\nhdr="<<hdr
                <<"\nsrgb_input="<<srgb_input
                <<"\n";
            meta.close();
            LOG_INFO(Render_Vulkan,
                     "GotMenuHostPP: result=SAVED bytes={} gamma={}",
                     snapshot->valid_bytes,gamma);
        });
}

} // namespace Vulkan::Diagnostics
