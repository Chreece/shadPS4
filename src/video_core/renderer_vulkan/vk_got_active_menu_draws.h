// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>

namespace Vulkan::Diagnostics {

// All addresses and shader hashes come from the October 19 live GPU trace.
// This deliberately does NOT use the guest's num_indices to decide whether an
// indirect draw has work. That value was 3 for the most active 5-MRT pipelines
// even though the true Vulkan argument count is stored in a separate buffer.
struct GotActiveMenuSceneDraw {
    bool enabled{};
    bool indexed{};
    bool indirect{};
    bool color_attached{};
    bool depth_attached{};
    bool guest_depth_test{};
    bool guest_depth_write{};
    bool guest_depth_clear{};
    bool rasterizer_discard{};
    std::uint32_t color_write_mask{};
    std::uint64_t color_address{};
    std::uint64_t depth_address{};
    std::uint32_t fragment_shader{};
    std::uint32_t vertex_shader{};
    std::uint32_t guest_register_indices{}; // informational; NEVER a gating condition
};

enum class GotActiveMenuDrawKind : std::uint8_t {
    Unrelated,
    DirectControl,
    FrequentSceneIndirect,
};

[[nodiscard]] constexpr GotActiveMenuDrawKind ClassifyGotActiveMenuDraw(
    const GotActiveMenuSceneDraw& input) noexcept {
    if (!input.enabled || !input.indexed || !input.color_attached ||
        !input.depth_attached || !input.guest_depth_test ||
        !input.guest_depth_write || input.guest_depth_clear ||
        input.rasterizer_discard || !input.color_write_mask ||
        input.color_address != 0x15203f8000ULL ||
        input.depth_address != 0x1504a08000ULL) {
        return GotActiveMenuDrawKind::Unrelated;
    }

    // The observed dominant scene renderers have thousands of real Vulkan
    // indirect draws, five MRTs, and the SAME depth/color targets as our
    // direct controls. The original num_indices>=128 selector missed them.
    if (input.indirect) {
        if ((input.fragment_shader == 0x5116e459u &&
             input.vertex_shader == 0xae1cc7b7u) ||
            (input.fragment_shader == 0xc1123f0fu &&
             input.vertex_shader == 0xc8caf263u)) {
            return GotActiveMenuDrawKind::FrequentSceneIndirect;
        }
        return GotActiveMenuDrawKind::Unrelated;
    }

    if ((input.fragment_shader == 0x5abc2be8u &&
         input.vertex_shader == 0xd0723f15u) ||
        (input.fragment_shader == 0xd8fb6100u &&
         input.vertex_shader == 0x14054a7au) ||
        (input.fragment_shader == 0xe301aa17u &&
         input.vertex_shader == 0x2dd14541u)) {
        return GotActiveMenuDrawKind::DirectControl;
    }
    return GotActiveMenuDrawKind::Unrelated;
}

[[nodiscard]] constexpr bool ShouldCaptureGotActiveMenuDraw(
    const GotActiveMenuDrawKind kind,
    const std::uint32_t ordinal) noexcept {
    if (kind == GotActiveMenuDrawKind::DirectControl) {
        return ordinal == 1;
    }
    // Checkpoints recorded in the October 19 trace, including the actual
    // late white-menu phase. Four per active shader pair, eight total.
    if (kind == GotActiveMenuDrawKind::FrequentSceneIndirect) {
        return ordinal == 4096 || ordinal == 4097 ||
               ordinal == 8192 || ordinal == 8193;
    }
    return false;
}

} // namespace Vulkan::Diagnostics
