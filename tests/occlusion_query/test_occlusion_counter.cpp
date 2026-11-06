// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <cstdlib>
#include <iostream>
#include "video_core/occlusion_counter.h"

static void Check(bool condition) {
    if (!condition) {
        std::abort();
    }
}

int main() {
    using namespace VideoCore::OcclusionCounter;
    for (const unsigned pipes : {8, 16}) {
        for (const std::uint64_t begin : {0ULL, 1ULL, 7ULL, 15ULL, 50331647ULL}) {
            for (const std::uint64_t samples : {0ULL, 1ULL, 8ULL, 16ULL, 65535ULL}) {
                std::uint64_t guest_delta{};
                std::array<std::uint64_t, 32> result;
                result.fill(0x1122334455667788ULL);
                for (unsigned pipe = 0; pipe < pipes; ++pipe) {
                    result[2 * pipe] = Value(begin, pipe, pipes);
                    result[2 * pipe + 1] = Value(begin + samples, pipe, pipes);
                    Check((result[2 * pipe] & result[2 * pipe + 1] & Valid) != 0);
                    guest_delta += (result[2 * pipe + 1] & Mask) - (result[2 * pipe] & Mask);
                }
                Check(guest_delta == samples);
            }
        }
        Check(WriteSpan(pipes) == (2 * pipes - 1) * sizeof(std::uint64_t));
        std::uint64_t sum{};
        for (unsigned pipe = 0; pipe < pipes; ++pipe) {
            sum += Value(Mask, pipe, pipes) & Mask;
        }
        Check(sum == Mask);
    }
    Check(WriteSpan(0) == 0 && WriteSpan(1) == 8);
    std::cout
        << "PASS: zero/visible deltas, 8/16 pipes, valid bits, total conservation, write span\n";
}
