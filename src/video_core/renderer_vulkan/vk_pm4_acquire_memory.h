// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "video_core/renderer_vulkan/vk_common.h"

namespace Vulkan {

// PS4 PM4 ACQUIRE_MEM makes prior guest GPU writes visible to subsequent
// graphics/compute resource accesses. Guest cache-range control bits are
// conservatively translated into an ALL_COMMANDS / global Vulkan memory
// dependency; no host wait, buffer-size guess or game-specific filtering.
//
// Caller must EndRendering() before recording this barrier. Synchronization2
// memory barriers are otherwise restricted inside dynamic rendering.
[[nodiscard]] inline vk::MemoryBarrier2 MakePm4AcquireMemoryBarrier() noexcept {
    return vk::MemoryBarrier2{
        .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .srcAccessMask = vk::AccessFlagBits2::eMemoryWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .dstAccessMask = vk::AccessFlagBits2::eMemoryRead |
                         vk::AccessFlagBits2::eMemoryWrite,
    };
}

} // namespace Vulkan
