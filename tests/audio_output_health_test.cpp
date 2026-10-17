// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cassert>
#include <cmath>
#include <limits>
#include "core/libraries/audio/audioout_health.h"

using Libraries::AudioOut::AudioPeak;
using Libraries::AudioOut::IsDisabledAudioDevice;

static_assert(IsDisabledAudioDevice("None"));
static_assert(!IsDisabledAudioDevice("Default Device"));
static_assert(!IsDisabledAudioDevice(""));
static_assert(!IsDisabledAudioDevice("None Device"));

int main() {
    constexpr std::array<float, 8> active{
        0.0f, -0.75f, 0.50f, -0.10f, 0.0f, 0.2f, 0.25f, 0.0f};
    assert(AudioPeak(active.data(), active.size()) == 0.75f);

    constexpr std::array<float, 4> silence{};
    assert(AudioPeak(silence.data(), silence.size()) == 0.0f);
    assert(AudioPeak(nullptr, 4) == 0.0f);
    assert(AudioPeak(active.data(), 0) == 0.0f);

    const std::array<float, 4> non_finite{std::numeric_limits<float>::quiet_NaN(),
        std::numeric_limits<float>::infinity(), -std::numeric_limits<float>::infinity(), -0.125f};
    assert(AudioPeak(non_finite.data(), non_finite.size()) == 0.125f);
    return 0;
}
