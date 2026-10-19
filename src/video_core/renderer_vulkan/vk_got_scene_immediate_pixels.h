// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Diagnostic-only: capture ACTUAL GPU attachment pixels immediately after
// the scene draw that generated fragments and passed samples. The later menu
// image captured at +85s was all zero but does not establish whether that
// draw wrote zero or whether a later clear/compositor discarded the output.
//
// Opt-in, five captures maximum, exact guest VA and image type required.
// Deliberately no host GPU read while the rendering scope is active:
// Runtime::DownloadImage ends it, handles transfer barriers and the following
// guest draw starts a new LOAD scope. The extra render-pass boundary can
// affect driver behavior, so this is not a production graphics fix.
#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <span>
#include <string>
#include <utility>

#include <fmt/format.h>

#include "common/logging/log.h"
#include "common/path_util.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/texture_cache/texture_cache.h"

namespace Vulkan::Diagnostics {

inline bool GotSceneImmediatePixelsEnabled() {
    static const bool enabled=[] {
        const char* const env=std::getenv("SHADPS4_GOT_SCENE_IMMEDIATE_PIXELS");
        return env && env[0]=='1' && env[1]=='\0';
    }();
    return enabled;
}

struct GotImmediateGpuSnapshot {
    VideoCore::Buffer staging;
    std::filesystem::path raw_path;
    u64 guest_address{};
    u64 saved_bytes{};
    u64 allocated_bytes{};
    u32 vs{},fs{},indices{},attachment{};
    vk::Format format{};
    bool depth{};

    GotImmediateGpuSnapshot(const Instance& instance,u64 allocated,
                            std::filesystem::path path)
        :staging{instance,0,allocated,VideoCore::MemoryType::HostCached},
         raw_path{std::move(path)},allocated_bytes{allocated} {}
};

inline void SnapshotGotSceneTargetAfterDraw(
    const Instance& instance, Scheduler& scheduler, Runtime& runtime,
    VideoCore::TextureCache& textures, VideoCore::ImageId image_id,
    u64 expected_address, vk::ImageLayout attachment_layout,
    u32 vs, u32 fs, u32 draw_indices, u32 attachment_index,
    u32 capture_slot, bool depth) {
    if(!GotSceneImmediatePixelsEnabled() || !image_id || capture_slot>=5) {
        return;
    }
    static thread_local std::array<bool,5> finished{};
    if(finished[capture_slot]) return;

    auto& image=textures.GetImage(image_id);
    const auto& info=image.info;
    const bool rgba8=info.pixel_format==vk::Format::eR8G8B8A8Unorm ||
                     info.pixel_format==vk::Format::eR8G8B8A8Srgb ||
                     info.pixel_format==vk::Format::eB8G8R8A8Unorm ||
                     info.pixel_format==vk::Format::eB8G8R8A8Srgb;
    const bool d32s8=info.pixel_format==vk::Format::eD32SfloatS8Uint;
    const bool acceptable=info.guest_address==expected_address &&
        info.size.width==1920 && info.size.height==1080 &&
        info.num_samples==1 && image.HasGpuValidData() &&
        (depth ? d32s8 : rgba8);
    if (!acceptable) {
        LOG_WARNING(Render_Vulkan,
                    "GotSceneImmediatePixels: capture={} fs={:#x} "
                    "vs={:#x} attachment={} result=REJECT_WRONG_IMAGE "
                    "expected={:#x} actual={:#x} host_format={} "
                    "dimensions={}x{} samples={} gpu_valid={} "
                    "depth_expected={}",
                    capture_slot,fs,vs,attachment_index,expected_address,
                    info.guest_address,vk::to_string(info.pixel_format),
                    info.size.width,info.size.height,info.num_samples,
                    image.HasGpuValidData(),depth);
        return;
    }
    finished[capture_slot]=true;
    constexpr u64 pixels=1920ULL*1080ULL;
    constexpr u64 raw_bytes=pixels*4u;
    const u64 staging_bytes=depth ? raw_bytes*2u : raw_bytes;
    const auto save_dir=Common::FS::GetUserPath(Common::FS::PathType::ShaderDir)/
                        "got-scene-immediate";
    std::filesystem::create_directories(save_dir);
    const auto path=save_dir/
        fmt::format("draw{}_fs_{:08x}_mrt{}.raw",
                    capture_slot,fs,attachment_index);
    auto snapshot=std::make_unique<GotImmediateGpuSnapshot>(
        instance,staging_bytes,path);
    snapshot->guest_address=info.guest_address;
    snapshot->saved_bytes=depth ? staging_bytes : raw_bytes;
    snapshot->vs=vs;
    snapshot->fs=fs;
    snapshot->indices=draw_indices;
    snapshot->attachment=attachment_index;
    snapshot->format=info.pixel_format;
    snapshot->depth=depth;

    const vk::BufferImageCopy copy{
        .bufferOffset=0,.bufferRowLength=0,.bufferImageHeight=0,
        .imageSubresource{
            .aspectMask=depth ? vk::ImageAspectFlagBits::eDepth
                              : vk::ImageAspectFlagBits::eColor,
            .mipLevel=0,.baseArrayLayer=0,.layerCount=1,
        },
        .imageOffset={0,0,0},.imageExtent={1920,1080,1},
    };
    runtime.DownloadImage(&image,&snapshot->staging,std::span{&copy,1});

    // Restore the actual attachment layout of the previously submitted
    // scene draw. This does not change guest color/depth contents.
    const auto stage=depth
        ? vk::PipelineStageFlagBits2::eEarlyFragmentTests |
          vk::PipelineStageFlagBits2::eLateFragmentTests
        : vk::PipelineStageFlags2{vk::PipelineStageFlagBits2::eColorAttachmentOutput};
    const auto access=depth
        ? vk::AccessFlags2{vk::AccessFlagBits2::eDepthStencilAttachmentWrite}
        : vk::AccessFlags2{vk::AccessFlagBits2::eColorAttachmentWrite};
    runtime.Transit(&image,attachment_layout,stage,access);
    runtime.FlushBarriers();

    LOG_INFO(Render_Vulkan,
             "GotSceneImmediatePixels: capture={} result=GPU_COPY_SUBMITTED "
             "fs={:#x} vs={:#x} indices={} target={:#x} "
             "attachment={} depth={} format={} bytes={} "
             "boundary=AFTER_REAL_DRAW_BEFORE_LATER_SCENE_PASSES",
             capture_slot,fs,vs,draw_indices,expected_address,
             attachment_index,depth,vk::to_string(info.pixel_format),
             snapshot->saved_bytes);

    scheduler.DeferPriorityOperation([snapshot=std::move(snapshot)]() mutable {
        auto& buffer=snapshot->staging;
        buffer.Invalidate(0,snapshot->allocated_bytes);
        if(buffer.mapped_data.size()<snapshot->saved_bytes) {
            LOG_WARNING(Render_Vulkan,
                        "GotSceneImmediatePixels: result=READBACK_NOT_MAPPED "
                        "capture={}",
                        snapshot->attachment);
            return;
        }
        const auto bytes=buffer.mapped_data.subspan(
            0,static_cast<std::size_t>(snapshot->saved_bytes));
        std::ofstream out(snapshot->raw_path,std::ios::binary);
        if (!out) {
            LOG_WARNING(Render_Vulkan,
                        "GotSceneImmediatePixels: result=FILE_WRITE_FAILED "
                        "fs={:#x} attachment={}",
                        snapshot->fs,snapshot->attachment);
            return;
        }
        out.write(reinterpret_cast<const char*>(bytes.data()),
                  static_cast<std::streamsize>(bytes.size()));
        out.close();

        u64 changed_rgb{};
        u64 changed_alpha{};
        u32 min_byte=255,max_byte{};
        u64 nonzero_bytes{};
        // Stats use every pixel for color, not sparse sampled points. Thus
        // even 1%-coverage geometry cannot be missed by sampling stride.
        constexpr std::size_t total_pixels=1920ULL*1080ULL;
        for (std::size_t i=0;i<total_pixels;++i) {
            const std::size_t pos=i*4;
            const u32 b0=bytes[pos],b1=bytes[pos+1];
            const u32 b2=bytes[pos+2],b3=bytes[pos+3];
            min_byte=std::min(min_byte,b0);
            max_byte=std::max(max_byte,b0);
            nonzero_bytes+=(b0!=0)+(b1!=0)+(b2!=0)+(b3!=0);
            changed_rgb+=(b0!=0 || b1!=0 || b2!=0);
            changed_alpha+=(b3!=0);
        }

        std::ofstream meta(snapshot->raw_path.string()+".txt");
        meta<<"source=EXACT_GPU_IMAGE_IMMEDIATELY_AFTER_DRAW\n"
            <<"guest_address="<<std::hex<<snapshot->guest_address<<std::dec
            <<"\nvs="<<std::hex<<snapshot->vs
            <<"\nfs="<<snapshot->fs<<std::dec
            <<"\nindices="<<snapshot->indices
            <<"\nattachment="<<snapshot->attachment
            <<"\nformat="<<static_cast<u32>(snapshot->format)
            <<"\nwidth=1920\nheight=1080"
            <<"\ndepth="<<snapshot->depth
            <<"\nbytes="<<snapshot->saved_bytes
            <<"\nrgb_nonzero_pixels="<<changed_rgb
            <<"\nalpha_nonzero_pixels="<<changed_alpha
            <<"\nnonzero_bytes="<<nonzero_bytes
            <<"\nmin_channel0="<<min_byte
            <<"\nmax_channel0="<<max_byte
            <<"\n";
        meta.close();
        LOG_INFO(Render_Vulkan,
                 "GotSceneImmediatePixels: result=GPU_READBACK_PASS "
                 "fs={:#x} vs={:#x} indices={} attachment={} "
                 "guest_address={:#x} depth={} "
                 "rgb_nonzero_pixels={} alpha_nonzero_pixels={} "
                 "nonzero_bytes={} total_pixels={} min_channel0={} "
                 "max_channel0={} bytes={} "
                 "gpu_timeline_complete=YES",
                 snapshot->fs,snapshot->vs,snapshot->indices,
                 snapshot->attachment,snapshot->guest_address,snapshot->depth,
                 changed_rgb,changed_alpha,nonzero_bytes,total_pixels,
                 min_byte,max_byte,snapshot->saved_bytes);
    });
}

} // namespace Vulkan::Diagnostics
