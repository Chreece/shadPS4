// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdint>

namespace Vulkan::Diagnostics {

// Explicitly experimental. One identified offscreen scene color/depth pair;
// never modify other games, postprocessing passes, shadow maps, fullscreen
// clears, or draws that do not actually write depth. Gate is pure so host
// regression tests can prove all exclusions without initializing Vulkan.
struct GotSceneDepthABInput {
    bool opted_in{};
    std::uint64_t guest_color_address{};
    std::uint64_t guest_depth_address{};
    std::uint32_t index_count{};
    std::uint32_t color_write_mask{};
    bool depth_test{};
    bool depth_write{};
    bool depth_clear{};
    bool raster_discard{};
};

[[nodiscard]] constexpr bool ShouldForceGotSceneDepthAlways(
    const GotSceneDepthABInput& input) noexcept {
    return input.opted_in &&
           input.guest_color_address == 0x15203f8000ULL &&
           input.guest_depth_address == 0x1504a08000ULL &&
           input.index_count >= 128 &&
           input.color_write_mask != 0 &&
           input.depth_test && input.depth_write &&
           !input.depth_clear && !input.raster_discard;
}

} // namespace Vulkan::Diagnostics
