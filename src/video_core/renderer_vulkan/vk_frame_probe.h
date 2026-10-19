// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <chrono>
#include <cstdlib>
#include <unordered_map>
#include "common/logging/log.h"
#include "common/types.h"

namespace Vulkan::Diagnostics {

inline constexpr u32 FrameProbeDrawLimit = 4096;
inline constexpr u32 FrameProbeCount = 6;

inline bool FrameProbeEnabled() {
    static const bool enabled = [] {
        const char* env = std::getenv("SHADPS4_FRAME_PROBE");
        return env && env[0] == '1' && env[1] == '\0';
    }();
    return enabled;
}

struct FrameProbeState {
    u32 frame{};
    u32 completed{};
    u32 draws{};
    u32 passes{};
    u32 images{};
    u32 argument_copies{};
    u32 image_events{};
    u64 bytes{};
    std::unordered_map<u64, u32> argument_pairs;

    bool ImagesEnabled() const {
        return frame && frame % 2 == 0;
    }

    void Present(u64 seconds) {
        constexpr std::array<u64, 3> checkpoints{60, 120, 210};
        if (frame) {
            LOG_INFO(Render_Vulkan,
                     "FrameProbeEnd: frame={} draws={} query_limit={} passes={} images={} "
                     "image_bytes={} argument_copies={} coverage={}",
                     frame, draws, FrameProbeDrawLimit, passes, images, bytes, argument_copies,
                     draws <= FrameProbeDrawLimit ? "ALL_SUBMITTED_DRAWS" : "QUERY_LIMIT_REACHED");
            completed = frame;
            frame = 0;
        }
        if (completed >= FrameProbeCount || seconds < checkpoints[completed / 2]) {
            return;
        }
        frame = completed + 1;
        draws = passes = images = argument_copies = image_events = 0;
        bytes = 0;
        argument_pairs.clear();
        LOG_INFO(Render_Vulkan,
                 "FrameProbeBegin: frame={} elapsed_s={} mode={} shader_filter=NONE "
                 "target_filter=NONE draw_register_gate=NONE",
                 frame, seconds, ImagesEnabled() ? "PASS_IMAGES" : "INDIRECT_ARGUMENTS");
    }

    bool CaptureArguments(u64 pair) {
        if (!frame || ImagesEnabled() || argument_copies >= 128) {
            return false;
        }
        if (argument_pairs[pair]++ >= 2) {
            return false;
        }
        ++argument_copies;
        return true;
    }
};

inline FrameProbeState& FrameProbe() {
    static FrameProbeState state;
    return state;
}

inline void FrameProbePresent() {
    if (!FrameProbeEnabled()) {
        return;
    }
    using Clock = std::chrono::steady_clock;
    static const auto start = Clock::now();
    FrameProbe().Present(
        std::chrono::duration_cast<std::chrono::seconds>(Clock::now() - start).count());
}

} // namespace Vulkan::Diagnostics
