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
    BufferReadback,
    ImageReadback,
    ImageReadDemand,
    ImageBufferWrite,
    GpuWait,
    DrawPreparationFlush,
    BufferProtectNone,
    BufferProtectRead,
    BufferProtectWrite,
    ImageProtectNone,
    ImageProtectRead,
    ImageProtectWrite,
    ViewportLayout,
    DepthStencilAccess,
    Count
};
inline std::array<std::atomic<unsigned long long>, static_cast<size_t>(Event::Count)> g_counts{};

inline constexpr std::array names{"enabled",
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
                                  "buffer-readback",
                                  "image-readback",
                                  "image-read-demand",
                                  "image-buffer-write",
                                  "gpu-wait",
                                  "draw-preparation-flush",
                                  "buffer-protect-none",
                                  "buffer-protect-read",
                                  "buffer-protect-write",
                                  "image-protect-none",
                                  "image-protect-read",
                                  "image-protect-write",
                                  "viewport-layout",
                                  "depth-stencil-access"};
static_assert(names.size() == static_cast<size_t>(Event::Count));

inline unsigned long long ElapsedMs() {
    static const auto start = std::chrono::steady_clock::now();
    return static_cast<unsigned long long>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                               std::chrono::steady_clock::now() - start)
                                               .count());
}

inline void DumpTotals() {
    for (size_t index = 0; index < g_counts.size(); ++index) {
        const auto count = g_counts[index].load(std::memory_order_relaxed);
        if (count != 0) {
            std::fprintf(stderr, "GRAPHICS_DIAG ms=%llu event=%s count=%llu total=1\n", ElapsedMs(),
                         names[index], count);
        }
    }
    std::fflush(stderr);
}

inline bool Enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("SHADPS4_GRAPHICS_DIAGNOSTICS");
        const bool active = value && std::strcmp(value, "1") == 0;
        if (active) {
            std::atexit(DumpTotals);
        }
        return active;
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
