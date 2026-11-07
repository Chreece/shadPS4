// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <cstdint>

namespace VideoCore::OcclusionCounter {
constexpr std::uint64_t Valid = std::uint64_t{1} << 63;
constexpr std::uint64_t Mask = ~Valid;

constexpr unsigned SelectedCounter(std::uint32_t control) {
    return (control >> 3) & 0x3f;
}

constexpr unsigned ActiveCounters(std::uint32_t count_control) {
    if (count_control & 1) {
        return 0;
    }
    const unsigned events = ((count_control >> 8) | (count_control >> 12) | (count_control >> 16) |
                             (count_control >> 20)) &
                            0xf;
    const unsigned slices = ((count_control >> 24) | (count_control >> 28)) & 0xf;
    return events & slices;
}

constexpr unsigned MeasuredCounters(std::uint32_t count_control) {
    const unsigned pass = (count_control >> 8) & 0xf;
    const unsigned failures =
        ((count_control >> 12) | (count_control >> 16) | (count_control >> 20)) & 0xf;
    const unsigned both_slices = (count_control >> 24) & (count_control >> 28) & 0xf;
    return ActiveCounters(count_control) & pass & both_slices & ~failures;
}

using Totals = std::array<std::uint64_t, 4>;

constexpr void Accumulate(Totals& totals, std::uint64_t samples, unsigned counters) {
    for (unsigned i = 0; i < totals.size(); ++i) {
        if (counters & (1u << i)) {
            totals[i] = (totals[i] + samples) & Mask;
        }
    }
}

constexpr void Reset(Totals& totals, unsigned counter) {
    if (counter < totals.size()) {
        totals[counter] = 0;
    }
}

constexpr std::uint64_t Value(std::uint64_t total, unsigned pipe, unsigned pipes) {
    // Vulkan returns a device-wide count. Partition it so summing guest pipes
    // returns that count, instead of multiplying it by eight or sixteen.
    return Valid | (total / pipes + (pipe < total % pipes));
}

constexpr std::uint64_t WriteSpan(unsigned pipes) {
    // A dump writes every other qword; the final paired qword is untouched.
    return pipes == 0 ? 0 : (pipes - 1) * 16ULL + 8;
}
} // namespace VideoCore::OcclusionCounter
