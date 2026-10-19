// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cstdint>

namespace Libraries::AudioOut::Detail {

// The default PS4 HLE behavior is left unchanged. In the opt-in Audio3D
// experiment, advance the observed output-device clock on empty ticks too,
// modeling the hardware output advancing even when no guest PCM arrived.
// This corrects possible guest scheduling feedback from a frozen output
// timestamp, without synthesizing/altering any PCM or changing wakeups.
constexpr std::uint64_t OutputTimestampAtTick(std::uint64_t previous,
                                              std::uint64_t tick_us,
                                              bool guest_buffer_was_ready,
                                              bool model_silent_output_clock) {
    if (!guest_buffer_was_ready && !model_silent_output_clock) {
        return previous;
    }
    return std::max(previous, tick_us);
}

} // namespace Libraries::AudioOut::Detail
