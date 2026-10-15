// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Independent native FPS telemetry validation, not a game-specific test.
// Compile: c++ -std=c++20 -O2 -pthread -I src
//          tests/common/test_frame_metrics.cpp -o /tmp/test_frame_metrics
// Run with a writable file prefix as argv[1].
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

#include "common/frame_metrics.h"

static std::vector<long long> ReadFrameTimes(const std::string& path) {
    std::ifstream input(path);
    std::string line;
    if (!std::getline(input, line) || line != "monotonic_us") {
        return {};
    }
    std::vector<long long> events;
    while (std::getline(input, line)) {
        if (!line.empty()) {
            events.push_back(std::stoll(line));
        }
    }
    return events;
}

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: test_frame_metrics <prefix>\n");
        return 2;
    }
#ifdef _WIN32
    if (_putenv_s("SHADPS4_FRAME_METRICS_PREFIX", argv[1]) != 0) {
        return 3;
    }
#else
    if (setenv("SHADPS4_FRAME_METRICS_PREFIX", argv[1], 1) != 0) {
        return 3;
    }
#endif

    // The first native counter records actual game flips. The second also
    // records a display refresh that reused the prior game frame.
    Common::FrameMetrics::RecordGuestFlip();
    Common::FrameMetrics::RecordSwapchainPresent();
    std::this_thread::sleep_for(std::chrono::milliseconds(12));
    Common::FrameMetrics::RecordSwapchainPresent();
    std::this_thread::sleep_for(std::chrono::milliseconds(12));
    Common::FrameMetrics::RecordGuestFlip();
    Common::FrameMetrics::Flush();

    const std::string prefix{argv[1]};
    const auto flips = ReadFrameTimes(prefix + ".guest-flips.csv");
    const auto presents = ReadFrameTimes(prefix + ".swapchain-presents.csv");
    if (flips.size() != 2 || presents.size() != 2) {
        std::fprintf(stderr, "frame stream mismatch: game=%zu display=%zu\n",
                     flips.size(), presents.size());
        return 4;
    }
    if (flips[1] - flips[0] < 20000 || presents[1] - presents[0] < 7000) {
        std::fprintf(stderr, "native frame timestamps not monotonic/precise\n");
        return 5;
    }
    // By contract, F10 overlay refreshes and re-presenting the last image
    // do not create additional frames in the guest-flip stream.
    std::printf("FRAME_METRICS_NATIVE=PASS game_flips=%zu swapchain_presents=%zu\n",
                flips.size(), presents.size());
    return 0;
}
