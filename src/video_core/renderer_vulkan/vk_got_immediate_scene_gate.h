// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>

namespace Vulkan::Diagnostics {

// This gate is for a READ-ONLY image snapshot, never an emulation override.
// The three pairs are actual GoT direct-scene draws with nonzero Vulkan
// fragment invocation and passing-sample counts, not speculative shaders.
struct GotImmediateSceneInput {
    bool opted_in{};
    bool indexed{};
    bool indirect{};
    std::uint64_t guest_color0{};
    std::uint64_t guest_depth{};
    std::uint32_t vs_hash{};
    std::uint32_t fs_hash{};
    std::uint32_t draw_indices{};
    bool color0_can_write{};
};

[[nodiscard]] constexpr bool IsGotImmediateSceneDraw(
    const GotImmediateSceneInput& in) noexcept {
    if (!in.opted_in || !in.indexed || in.indirect ||
        in.guest_color0 != 0x15203f8000ULL ||
        in.guest_depth != 0x1504a08000ULL ||
        !in.color0_can_write) {
        return false;
    }
    return
        (in.fs_hash == 0x5abc2be8u && in.vs_hash == 0xd0723f15u &&
         in.draw_indices == 480u) ||
        (in.fs_hash == 0xd8fb6100u && in.vs_hash == 0x14054a7au &&
         in.draw_indices == 13104u) ||
        (in.fs_hash == 0xe301aa17u && in.vs_hash == 0x2dd14541u &&
         in.draw_indices == 798u);
}

} // namespace Vulkan::Diagnostics
