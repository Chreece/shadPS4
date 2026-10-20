// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cstdlib>
#include <optional>
#include <string_view>
#include <unordered_map>
#include "common/logging/log.h"
#include "common/types.h"
#include "video_core/renderer_vulkan/resource_audit_hash_filter.h"

namespace Vulkan::Diagnostics {

inline constexpr u32 FrameProbeDrawLimit = 4096;
inline constexpr u32 FrameProbeCount = 6;

inline std::optional<std::array<u64, 3>> ParseFrameProbeCheckpoints(std::string_view text) {
    std::array<u64, 3> checkpoints{};
    for (size_t i = 0; i < checkpoints.size(); ++i) {
        if (text.empty()) {
            return std::nullopt;
        }
        const auto [end, error] =
            std::from_chars(text.data(), text.data() + text.size(), checkpoints[i]);
        if (error != std::errc{} || checkpoints[i] > 3600 ||
            (i && checkpoints[i] <= checkpoints[i - 1])) {
            return std::nullopt;
        }
        text.remove_prefix(end - text.data());
        if (i + 1 == checkpoints.size()) {
            if (!text.empty()) {
                return std::nullopt;
            }
        } else {
            if (text.empty() || text.front() != ',') {
                return std::nullopt;
            }
            text.remove_prefix(1);
        }
    }
    return checkpoints;
}

inline const std::array<u64, 3>& FrameProbeCheckpoints() {
    static const auto checkpoints = [] {
        const std::array<u64, 3> defaults{60, 120, 210};
        const char* value = std::getenv("SHADPS4_FRAME_PROBE_CHECKPOINTS");
        if (!value) {
            return defaults;
        }
        if (const auto parsed = ParseFrameProbeCheckpoints(value)) {
            return *parsed;
        }
        LOG_WARNING(Render_Vulkan, "Invalid frame-probe checkpoints; using 60,120,210");
        return defaults;
    }();
    return checkpoints;
}

inline bool FrameProbeEnabled() {
    static const bool enabled = [] {
        const char* env = std::getenv("SHADPS4_FRAME_PROBE");
        return env && env[0] == '1' && env[1] == '\0';
    }();
    return enabled;
}

inline bool FrameProbeImagesEnabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("SHADPS4_FRAME_PROBE_IMAGES");
        return !value || std::string_view(value) != "0";
    }();
    return enabled;
}

inline u32 FrameProbeFrameLimit() {
    static const u32 limit = [] {
        const char* value = std::getenv("SHADPS4_FRAME_PROBE_FRAME_LIMIT");
        if (!value) {
            return FrameProbeCount;
        }
        const std::string_view text{value};
        u32 parsed{};
        const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), parsed);
        return error == std::errc{} && end == text.data() + text.size() && parsed > 0 &&
                       parsed <= FrameProbeCount
                   ? parsed
                   : FrameProbeCount;
    }();
    return limit;
}

inline const auto& FrameProbeTriggerFilter() {
    static const auto filter = VideoCore::Diagnostics::ParseShaderAuditHashFilter(
        std::getenv("SHADPS4_FRAME_PROBE_TRIGGER_VS_HASHES"));
    return filter;
}

inline u32 FrameProbeTriggerStableSeconds() {
    static const u32 seconds = [] {
        const char* value = std::getenv("SHADPS4_FRAME_PROBE_TRIGGER_STABLE_SECONDS");
        if (!value) {
            return 0u;
        }
        const std::string_view text{value};
        u32 parsed{};
        const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), parsed);
        return error == std::errc{} && end == text.data() + text.size() && parsed <= 120
                   ? parsed
                   : 120u;
    }();
    return seconds;
}

struct FrameProbeState {
    bool trigger_seen{};
    std::optional<u64> trigger_since;
    u32 frame{};
    u32 completed{};
    u32 draws{};
    u32 passes{};
    u32 images{};
    u32 argument_copies{};
    u32 image_events{};
    u64 bytes{};
    u64 image_full_bytes{};
    u64 image_row_bytes{};
    u32 image_row_step{8};
    u32 image_budget_skips{};
    std::unordered_map<u64, u32> argument_pairs;

    void ObserveVertexShader(u64 hash) {
        if (FrameProbeEnabled() && FrameProbeTriggerFilter().enabled &&
            FrameProbeTriggerFilter().Matches(hash)) {
            trigger_seen = true;
        }
    }

    bool ImagesEnabled() const {
        return FrameProbeImagesEnabled() && frame && frame % 2 == 0;
    }

    void Present(u64 seconds) {
        const auto& checkpoints = FrameProbeCheckpoints();
        const bool matched = trigger_seen;
        trigger_seen = false;
        if (!matched || seconds < checkpoints[std::min(completed / 2, 2u)]) {
            trigger_since.reset();
        } else if (!trigger_since) {
            trigger_since = seconds;
        }
        if (frame) {
            if (FrameProbeImagesEnabled() && frame % 2 != 0) {
                image_row_step = 8;
                while (image_row_step < 16384 &&
                       image_full_bytes / image_row_step + image_row_bytes > 96_MB) {
                    image_row_step *= 2;
                }
                LOG_INFO(Render_Vulkan,
                         "FrameProbeImagePlan: frame={} next_frame={} row_step={} "
                         "full_bytes={} row_bytes={} budget_bytes={}",
                         frame, frame + 1, image_row_step, image_full_bytes, image_row_bytes,
                         128_MB);
            }
            LOG_INFO(Render_Vulkan,
                     "FrameProbeEnd: frame={} draws={} query_limit={} passes={} images={} "
                     "image_bytes={} image_budget_skips={} argument_copies={} coverage={}",
                     frame, draws, FrameProbeDrawLimit, passes, images, bytes, image_budget_skips,
                     argument_copies,
                     draws <= FrameProbeDrawLimit ? "ALL_SUBMITTED_DRAWS" : "QUERY_LIMIT_REACHED");
            completed = frame;
            frame = 0;
        }
        if (completed >= FrameProbeFrameLimit() || seconds < checkpoints[completed / 2]) {
            return;
        }
        if (FrameProbeTriggerFilter().enabled) {
            if (!FrameProbeTriggerFilter().valid || !trigger_since ||
                seconds - *trigger_since < FrameProbeTriggerStableSeconds()) {
                return;
            }
            LOG_INFO(Render_Vulkan,
                     "FrameProbeTrigger: elapsed_s={} stable_s={} source=SUBMITTED_VERTEX_SHADER",
                     seconds, seconds - *trigger_since);
        }
        frame = completed + 1;
        draws = passes = images = argument_copies = image_events = 0;
        bytes = 0;
        image_full_bytes = image_row_bytes = 0;
        image_budget_skips = 0;
        argument_pairs.clear();
        LOG_INFO(Render_Vulkan,
                 "FrameProbeBegin: frame={} elapsed_s={} mode={} shader_filter=NONE "
                 "target_filter=NONE draw_register_gate=NONE",
                 frame, seconds, ImagesEnabled() ? "PASS_IMAGES"
                               : FrameProbeImagesEnabled() ? "INDIRECT_ARGUMENTS" : "DRAW_CENSUS");
    }

    bool CaptureArguments(u64 pair) {
        if (!frame || frame % 2 == 0 || argument_copies >= 128) {
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
