// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <mutex>

#include "common/performance_diagnostics.h"
#include "video_core/graphics_diagnostics.h"

namespace VideoCore::GpuWorkDiagnostics {

inline constexpr std::size_t Capacity = 2048;
inline constexpr std::size_t OutputLimit = 2 * 1024 * 1024;

struct Entry {
    std::uint64_t sequence{};
    std::uint64_t tick{};
    unsigned long long milliseconds{};
    const void* semaphore{};
    bool reported{};
    std::array<char, 256> fields{};
};

struct History {
    std::mutex mutex;
    std::array<Entry, Capacity> entries{};
    std::uint64_t sequence{};
    std::size_t output_bytes{};
    bool truncated{};
};

inline History& GetHistory() {
    static History history;
    return history;
}

inline void Dump(const char* reason, const void* semaphore, std::uint64_t completed,
                 std::uint64_t requested) {
    if (!GraphicsDiagnostics::Enabled() || Common::PerformanceDiagnostics::Enabled()) {
        return;
    }
    auto& history = GetHistory();
    std::scoped_lock lock{history.mutex};
    if (history.truncated) {
        return;
    }
    const auto write = [&](const char* line, int bytes) {
        if (bytes <= 0) {
            return;
        }
        if (history.output_bytes + bytes > OutputLimit) {
            if (!history.truncated) {
                std::fputs("GRAPHICS_WORK_TRUNCATED limit=2097152\n", stderr);
                history.truncated = true;
            }
            return;
        }
        std::fwrite(line, 1, bytes, stderr);
        history.output_bytes += bytes;
    };
    std::array<char, 512> line{};
    const auto oldest = history.sequence > Capacity ? history.sequence - Capacity : 0;
    int bytes = std::snprintf(
        line.data(), line.size(),
        "GRAPHICS_WORK_BEGIN reason=%s semaphore=%p completed=%llu requested=%llu "
        "recorded=%llu overwritten=%llu\n",
        reason, semaphore, static_cast<unsigned long long>(completed),
        static_cast<unsigned long long>(requested),
        static_cast<unsigned long long>(history.sequence), static_cast<unsigned long long>(oldest));
    write(line.data(), std::min(bytes, int(line.size() - 1)));
    for (auto index = oldest; index < history.sequence && !history.truncated; ++index) {
        auto& entry = history.entries[index % Capacity];
        if (entry.reported || (semaphore && (entry.semaphore != semaphore ||
                                             entry.tick <= completed || entry.tick > requested))) {
            continue;
        }
        bytes = std::snprintf(
            line.data(), line.size(), "GRAPHICS_WORK seq=%llu ms=%llu semaphore=%p tick=%llu %s\n",
            static_cast<unsigned long long>(entry.sequence), entry.milliseconds, entry.semaphore,
            static_cast<unsigned long long>(entry.tick), entry.fields.data());
        write(line.data(), std::min(bytes, int(line.size() - 1)));
        entry.reported = true;
    }
    bytes = std::snprintf(line.data(), line.size(), "GRAPHICS_WORK_END reason=%s\n", reason);
    write(line.data(), std::min(bytes, int(line.size() - 1)));
    std::fflush(stderr);
}

inline void DumpAtExit() {
    Dump("exit", nullptr, 0, std::numeric_limits<std::uint64_t>::max());
}

inline void RegisterExitDump() {
    static const bool registered = [] {
        std::atexit(DumpAtExit);
        std::at_quick_exit(DumpAtExit);
        return true;
    }();
    static_cast<void>(registered);
}

template <typename... Args>
void Record(const void* semaphore, std::uint64_t tick, const char* format, Args... args) {
    if (!GraphicsDiagnostics::Enabled() || Common::PerformanceDiagnostics::Enabled()) {
        return;
    }
    auto& history = GetHistory();
    RegisterExitDump();
    std::array<char, 256> fields{};
    if constexpr (sizeof...(Args) == 0) {
        std::snprintf(fields.data(), fields.size(), "%s", format);
    } else {
        std::snprintf(fields.data(), fields.size(), format, args...);
    }
    std::scoped_lock lock{history.mutex};
    auto& entry = history.entries[history.sequence % Capacity];
    entry = {++history.sequence, tick, GraphicsDiagnostics::ElapsedMs(), semaphore, false, fields};
}

} // namespace VideoCore::GpuWorkDiagnostics
