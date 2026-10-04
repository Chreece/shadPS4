// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <span>
#include "common/types.h"

namespace Libraries::Ngs2::Hle {

struct DirectFilter {
    std::array<float, 5> coefficients{1, 0, 0, 0, 0};
    u8 bypass_mask{};

    struct Delay {
        float x1{}, x2{}, y1{}, y2{};
    };
    using History = std::array<Delay, 8>;

    bool Valid() const {
        return std::ranges::all_of(coefficients, [](float c) { return std::isfinite(c); });
    }

    bool Process(std::span<float> audio, u32 channels, History& history) const {
        if (!channels || channels > history.size() || audio.size() % channels || !Valid())
            return false;
        for (u32 channel = 0; channel < channels; ++channel) {
            auto& h = history[channel];
            if (bypass_mask & (1u << channel)) {
                h = {};
                continue;
            }
            for (size_t position = channel; position < audio.size(); position += channels) {
                const float input = audio[position];
                const float output = coefficients[0] * input + coefficients[1] * h.x1 +
                                     coefficients[2] * h.x2 + coefficients[3] * h.y1 +
                                     coefficients[4] * h.y2;
                if (!std::isfinite(output)) {
                    history = {};
                    std::ranges::fill(audio, 0);
                    return false;
                }
                h = {input, h.x1, output, h.y1};
                audio[position] = output;
            }
        }
        return true;
    }
};

} // namespace Libraries::Ngs2::Hle
