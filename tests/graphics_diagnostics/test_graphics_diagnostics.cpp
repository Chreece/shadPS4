// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstdlib>
#include <string_view>
#include "video_core/graphics_diagnostics.h"

int main(int argc, char** argv) {
    using namespace VideoCore::GraphicsDiagnostics;
    const bool enabled = argc == 2 && std::string_view(argv[1]) == "enabled";
    if (Enabled() != enabled || ShouldSample(0)) {
        return 1;
    }
    unsigned samples = 0;
    for (unsigned count = 1; count <= 1'000'000; ++count) {
        samples += ShouldSample(count);
    }
    if (samples != 24) {
        return 2;
    }
    for (unsigned count = 1; count <= 4096; ++count) {
        Emit(Event::PixelPipe, "fixture=%u", count);
    }
    // Different template signatures must use the same event counter.
    Emit(Event::PixelPipe, "fixture=%llu", 4097ULL);
    const auto observed = g_counts[static_cast<size_t>(Event::PixelPipe)].load();
    if (observed != (enabled ? 4097 : 0)) {
        return 3;
    }
    Emit(Event::BufferImageSync, "requested=%u image-bytes=%u", 65536u, 65536u);
    Emit(Event::RawBufferImageSync, "bytes=%u", 65536u);
    if (g_counts[static_cast<size_t>(Event::BufferImageSync)].load() != (enabled ? 1 : 0) ||
        g_counts[static_cast<size_t>(Event::RawBufferImageSync)].load() != (enabled ? 1 : 0) ||
        g_counts[static_cast<size_t>(Event::PixelPipe)].load() != observed) {
        return 4;
    }
    return 0;
}
