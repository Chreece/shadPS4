// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <limits>
#include "shader_recompiler/guest_tsharp_feedback.h"

using namespace Shader::Optimization;

static_assert(GuestTsharpFeedbackWordCount == 1025u);
static_assert(GuestTsharpFeedbackBytes == 4100u);
static_assert(GuestTsharpFeedbackClampedId(0u) == 0u);
static_assert(GuestTsharpFeedbackClampedId(32767u) == 32767u);
static_assert(GuestTsharpFeedbackClampedId(32768u) == 32768u);
static_assert(GuestTsharpFeedbackClampedId(0xffffffffu) == 32768u);
static_assert(GuestTsharpFeedbackWordIndex(32u) == 1u);
static_assert(GuestTsharpFeedbackWordBit(31u) == 0x80000000u);
static_assert(GuestTsharpFeedbackWordIndex(32767u) == 1023u);
static_assert(GuestTsharpFeedbackWordIndex(32768u) == 1024u);
static_assert(GuestTsharpFeedbackWordBit(0xffffffffu) == 1u);

int main() {
    std::array<std::uint32_t, GuestTsharpFeedbackWordCount> words{};
    constexpr std::array<std::uint32_t, 8> gpu_ids{
        0u, 1u, 31u, 32u, 27650u, 32767u, 32768u,
        std::numeric_limits<std::uint32_t>::max()};
    for (const auto id : gpu_ids) {
        words[GuestTsharpFeedbackWordIndex(id)] |=
            GuestTsharpFeedbackWordBit(id);
    }
    for (const auto id : gpu_ids) {
        assert(words[GuestTsharpFeedbackWordIndex(id)] &
               GuestTsharpFeedbackWordBit(id));
    }
    // An out-of-range ID never aliases a real guest T# descriptor.
    assert(words[1024] == 1u);
    assert((words[GuestTsharpFeedbackWordIndex(27649u)] &
            GuestTsharpFeedbackWordBit(27649u)) == 0u);
    assert(words[GuestTsharpFeedbackWordIndex(27650u)] ==
           GuestTsharpFeedbackWordBit(27650u));
    std::puts("GUEST_TSHARP_GPU_ID_FEEDBACK_HOST=PASS distinct=7 overflow=1");
}
