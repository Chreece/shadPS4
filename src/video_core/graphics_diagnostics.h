// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace VideoCore::GraphicsDiagnostics {

enum class Event {
    Startup,
    PixelPipe,
    Predication,
    CopyLayers,
    Containment,
    ConditionalExec,
    QueryControl,
    QueryResult,
    QueryCountState,
    DepthGrowth,
    DepthGrowthUninitialized,
    ImageUpload,
    ImageUploadSkipped,
    PresentImage,
    Count
};
inline std::array<std::atomic<unsigned long long>, static_cast<size_t>(Event::Count)> g_counts{};

inline unsigned long long ElapsedMs() {
    static const auto start = std::chrono::steady_clock::now();
    return static_cast<unsigned long long>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                               std::chrono::steady_clock::now() - start)
                                               .count());
}

inline bool Enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("SHADPS4_GRAPHICS_DIAGNOSTICS");
        return value && std::strcmp(value, "1") == 0;
    }();
    return enabled;
}

constexpr bool ShouldSample(unsigned long long count) {
    return count != 0 && (count <= 8 || (count & (count - 1)) == 0);
}

template <typename... Args>
void Emit(Event event, const char* format, Args... args) {
    if (!Enabled()) {
        return;
    }
    static constexpr std::array names{"enabled",
                                      "pixel-pipe",
                                      "predication",
                                      "copy-layers",
                                      "containment-miss",
                                      "conditional-exec",
                                      "query-control",
                                      "query-result",
                                      "query-count-state",
                                      "depth-growth",
                                      "depth-growth-uninitialized",
                                      "image-upload",
                                      "image-upload-skipped",
                                      "present-image"};
    static_assert(names.size() == static_cast<size_t>(Event::Count));
    const auto index = static_cast<size_t>(event);
    const auto count = g_counts[index].fetch_add(1, std::memory_order_relaxed) + 1;
    if (!ShouldSample(count)) {
        return;
    }
    char fields[768]{};
    std::snprintf(fields, sizeof(fields), format, args...);
    std::fprintf(stderr, "GRAPHICS_DIAG ms=%llu event=%s count=%llu %s\n", ElapsedMs(),
                 names[index], count, fields);
    std::fflush(stderr);
}

} // namespace VideoCore::GraphicsDiagnostics
