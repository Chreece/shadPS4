// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstdint>

namespace Vulkan::Diagnostics {

struct GotSceneDepthClipTrialInput {
    bool opted_in{};
    bool depth_clip_extension_enabled{};
    bool depth_clamp_feature_enabled{};
    bool has_color_attachment{};
    bool has_depth_attachment{};
    std::uint32_t vertex_shader{};
    std::uint32_t fragment_shader{};
};

// The physical GPU counters proved that these five exact fragment/vertex
// pipeline pairs executed nonzero VS but yielded ZERO clipped primitives.
// This test disables Z clipping only for those proven shader pairs, leaving
// depth writes, shaders, textures and all other pipelines unmodified.
[[nodiscard]] constexpr bool ShouldTestGotSceneDepthClip(
    const GotSceneDepthClipTrialInput& input) noexcept {
    if (!input.opted_in || !input.depth_clip_extension_enabled ||
        !input.depth_clamp_feature_enabled || !input.has_color_attachment ||
        !input.has_depth_attachment) {
        return false;
    }

    constexpr std::array<std::array<std::uint32_t,2>,5> failing_pairs{{
        {{0x73fe0871u,0x4516d5e8u}},
        {{0x73fe0871u,0x5127d09du}},
        {{0xf3022914u,0x090be5c2u}},
        {{0x20b6ed83u,0x85eb5e04u}},
        {{0x20b6ed83u,0x326f181du}},
    }};
    for(const auto& pair:failing_pairs) {
        if(input.vertex_shader==pair[0] &&
           input.fragment_shader==pair[1]) return true;
    }
    return false;
}

} // namespace Vulkan::Diagnostics
