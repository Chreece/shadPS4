// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <span>
#include "diagnostics.h"

namespace Libraries::Ngs2::Diagnostics {
inline bool LfeEnabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("SHADPS4_NGS2_LFE_DIAGNOSTICS");
        return value && std::strcmp(value, "1") == 0;
    }();
    return enabled;
}

inline bool LfeControlTake(std::uint64_t voice, unsigned event) {
    static Budget budget;
    // Spread voice keys across buckets; event distinguishes matrix/transaction logs.
    return LfeEnabled() && budget.Take(((voice & 7) << 32) | (voice << 8) | event);
}

// Independent of ordinary control tracing, so command churn cannot exhaust
// the routing capture. Fixed process-wide output bound; no audio is recorded.
inline void LfeRecord(const char* format, ...) {
    if (!LfeEnabled())
        return;
    static std::mutex mutex;
    static unsigned lines{};
    const std::lock_guard lock{mutex};
    if (lines >= 8192)
        return;
    std::array<char, 1024> message{};
    va_list arguments;
    va_start(arguments, format);
    std::vsnprintf(message.data(), message.size(), format, arguments);
    va_end(arguments);
    std::fprintf(stderr, "NGS2_LFE %s\n", message.data());
    if (++lines == 8192)
        std::fprintf(stderr, "NGS2_LFE capture-limit-reached lines=8192\n");
}

struct LfeWindow {
    std::array<float, 8> peaks{};
    std::array<std::uint64_t, 8> nonzero{};
    std::uint64_t frames{};
    std::uint64_t nonfinite{};
    unsigned channels{};

    bool Observe(std::span<const float> samples, unsigned count) {
        if (!count || count > 8 || samples.size() % count)
            return false;
        if (channels != count)
            *this = {};
        channels = count;
        frames += samples.size() / count;
        for (size_t i = 0; i < samples.size(); ++i) {
            if (!std::isfinite(samples[i])) {
                ++nonfinite;
                continue;
            }
            const auto channel = i % count;
            peaks[channel] = std::max(peaks[channel], std::abs(samples[i]));
            nonzero[channel] += samples[i] != 0;
        }
        return true;
    }
};
inline void ReportLfeWindow(std::uint64_t system, std::uint64_t grain, unsigned output,
                            const LfeWindow& window, bool final = false) {
    const auto& p = window.peaks;
    LfeRecord(
        "output system=%llu grain=%llu output=%u channels=%u frames=%llu "
        "peaks=%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g "
        "lfe-nonzero=%llu nonfinite=%llu final=%u",
        static_cast<unsigned long long>(system), static_cast<unsigned long long>(grain), output,
        window.channels, static_cast<unsigned long long>(window.frames),
        p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7],
        static_cast<unsigned long long>(window.channels >= 6 ? window.nonzero[3] : 0),
        static_cast<unsigned long long>(window.nonfinite), final ? 1u : 0u);
}
} // namespace Libraries::Ngs2::Diagnostics
