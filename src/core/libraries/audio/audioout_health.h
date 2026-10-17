// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <string_view>

namespace Libraries::AudioOut {

// A user-selected "None" is an intentional silent port. Unlike a failed
// host backend, it must still return a valid guest audio handle so disabling
// controller/speaker audio does not break guest initialization.
[[nodiscard]] constexpr bool IsDisabledAudioDevice(std::string_view name) noexcept {
    return name == "None";
}

// Inspect only a sparse sample of converted PCM frames for routing diagnosis.
// Every SDL output buffer contains normalized floating-point samples.  Values
// from missing or uninitialized PCM can contain NaN/Inf; ignore them rather
// than reporting them as audible.
[[nodiscard]] inline float AudioPeak(const float* samples, std::size_t count) noexcept {
    if (samples == nullptr) {
        return 0.0f;
    }
    float peak = 0.0f;
    for (std::size_t i = 0; i < count; ++i) {
        if (std::isfinite(samples[i])) {
            peak = std::max(peak, std::abs(samples[i]));
        }
    }
    return peak;
}
} // namespace Libraries::AudioOut
