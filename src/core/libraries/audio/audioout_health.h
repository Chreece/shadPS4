// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
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
// OpenAL may downmix into signed 16-bit buffers when floating-point
// output is unsupported by the device. Keep measurement in normalized units
// so SDL and OpenAL diagnostics can be compared.
[[nodiscard]] inline float AudioPeakS16(const std::int16_t* samples,
                                        std::size_t count) noexcept {
    if (samples == nullptr) {
        return 0.0f;
    }
    int peak = 0;
    for (std::size_t i = 0; i < count; ++i) {
        peak = std::max(peak, std::abs(static_cast<int>(samples[i])));
    }
    return static_cast<float>(peak) / 32768.0f;
}
} // namespace Libraries::AudioOut
