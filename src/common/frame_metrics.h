// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>

namespace Common::FrameMetrics {

// Lightweight, generic, opt-in emulation diagnostics. Unlike a display FPS
// overlay, guest flips and swapchain presentations are recorded separately:
// repeating the last display image must not be counted as a new game frame.
//
// SHADPS4_FRAME_METRICS_PREFIX is a FILE PREFIX for two independent streams:
//   <prefix>.guest-flips.csv       one timepoint per completed guest frame
//   <prefix>.swapchain-presents.csv one timepoint per successful host present
//
// No guest execution, shader selection, render path, timing or configuration
// is changed. Disabled by default; when enabled, timestamps are buffered and
// periodically flushed so sampling does not introduce per-frame disk sync.
enum class Event : unsigned {
    GuestFlip = 0,
    SwapchainPresent = 1,
};

class Recorder final {
public:
    Recorder(const Recorder&) = delete;
    Recorder& operator=(const Recorder&) = delete;

    static Recorder& Instance() {
        static Recorder instance;
        return instance;
    }

    void Record(Event event) {
        if (!enabled_) {
            return;
        }
        const auto idx = static_cast<unsigned>(event);
        if (!streams_[idx]) {
            return;
        }
        // Acquire the stream lock before timestamping. This keeps each
        // stream monotonic even if presentation can be called concurrently.
        std::lock_guard lock{locks_[idx]};
        const auto time = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
        std::fprintf(streams_[idx], "%lld\n", static_cast<long long>(time));
        if ((++counts_[idx] % 64) == 0) {
            std::fflush(streams_[idx]);
        }
    }

    void Flush() {
        for (unsigned idx = 0; idx < streams_.size(); ++idx) {
            std::lock_guard lock{locks_[idx]};
            if (streams_[idx]) {
                std::fflush(streams_[idx]);
            }
        }
    }

private:
    Recorder() {
        const char* const prefix = std::getenv("SHADPS4_FRAME_METRICS_PREFIX");
        if (!prefix || !*prefix) {
            return;
        }

        const std::string base{prefix};
        const std::array<std::string, 2> paths{
            base + ".guest-flips.csv",
            base + ".swapchain-presents.csv",
        };
        for (unsigned idx = 0; idx < paths.size(); ++idx) {
            streams_[idx] = std::fopen(paths[idx].c_str(), "wb");
            if (streams_[idx]) {
                // fflush every 64 frames; libc buffering avoids per-frame IO.
                std::setvbuf(streams_[idx], nullptr, _IOFBF, 131072);
                std::fputs("monotonic_us\n", streams_[idx]);
            }
        }
        enabled_ = streams_[0] != nullptr || streams_[1] != nullptr;
    }

    ~Recorder() {
        Flush();
        for (auto* stream : streams_) {
            if (stream) {
                std::fclose(stream);
            }
        }
    }

    bool enabled_{};
    std::array<std::FILE*, 2> streams_{};
    std::array<std::mutex, 2> locks_{};
    std::array<std::uint64_t, 2> counts_{};
};

inline void RecordGuestFlip() {
    Recorder::Instance().Record(Event::GuestFlip);
}

inline void RecordSwapchainPresent() {
    Recorder::Instance().Record(Event::SwapchainPresent);
}

inline void Flush() {
    Recorder::Instance().Flush();
}

} // namespace Common::FrameMetrics
