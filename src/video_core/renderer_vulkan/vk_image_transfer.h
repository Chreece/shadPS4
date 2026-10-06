// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "video_core/renderer_vulkan/vk_common.h"

namespace Vulkan::ImageTransfer {

constexpr bool NeedsDepthBufferCopy(vk::Format src, vk::Format dst) {
    return (src == vk::Format::eD32Sfloat && dst == vk::Format::eD32SfloatS8Uint) ||
           (src == vk::Format::eD32SfloatS8Uint && dst == vk::Format::eD32Sfloat);
}

struct DepthBufferCopy {
    vk::BufferImageCopy source;
    vk::BufferImageCopy destination;
    vk::DeviceSize size;
};

inline DepthBufferCopy MakeDepthBufferCopy(const vk::ImageCopy& region, vk::DeviceSize offset) {
    return {
        .source = {.bufferOffset = offset,
                   .imageSubresource = region.srcSubresource,
                   .imageOffset = region.srcOffset,
                   .imageExtent = region.extent},
        .destination = {.bufferOffset = offset,
                        .imageSubresource = region.dstSubresource,
                        .imageOffset = region.dstOffset,
                        .imageExtent = region.extent},
        .size = vk::DeviceSize{4} * region.extent.width * region.extent.height *
                region.extent.depth * region.srcSubresource.layerCount,
    };
}

} // namespace Vulkan::ImageTransfer
