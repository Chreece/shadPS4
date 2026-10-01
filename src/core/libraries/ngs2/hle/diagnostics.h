// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include <chrono>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <mutex>

namespace Libraries::Ngs2::Diagnostics {
// Fixed storage and a process-wide output bound, including concurrent systems.
// Callers serialize Budget access; Record supplies that lock.
class Budget {
public:
    bool Rearm() {
        if (rearmed)
            return false;
        entries = {};
        failures = {};
        emitted = ordinary_emitted = 0;
        rearmed = true;
        return true;
    }

    bool Take(std::uint64_t key, std::uint32_t interval = 0, bool failure = false) {
        // Reserve the last 256 lines for failures. Ordinary request/state churn
        // must not hide the first bad waveform, even late in a session.
        if (emitted >= 2048 || (!failure && ordinary_emitted >= 1792))
            return false;
        auto& bucket = failure ? failures : entries[(key >> 32) % entries.size()];
        for (auto& entry : bucket) {
            if (!entry.used || entry.key == key) {
                entry.used = true;
                entry.key = key;
                if (entry.count == UINT32_MAX)
                    return false;
                const auto n = ++entry.count;
                if (n <= 4 || (interval ? n % interval == 0 : (n & (n - 1)) == 0)) {
                    ++emitted;
                    ordinary_emitted += !failure;
                    return true;
                }
                return false;
            }
        }
        return false;
    }

private:
    struct Entry {
        std::uint64_t key{};
        std::uint32_t count{};
        bool used{};
    };
    std::array<std::array<Entry, 64>, 8> entries{};
    std::array<Entry, 64> failures{};
    unsigned emitted{};
    unsigned ordinary_emitted{};
    bool rearmed{};
};

inline bool Enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("SHADPS4_NGS2_DIAGNOSTICS");
        return value && std::strcmp(value, "1") == 0;
    }();
    return enabled;
}

inline void RecordV(bool failure, std::uint64_t key, std::uint32_t interval, const char* format,
                    va_list arguments) {
    if (!Enabled())
        return;
    static std::mutex mutex;
    static Budget budget;
    static const auto start = std::chrono::steady_clock::now();
    static auto next_check = start;
    static bool rearmed = false;
    static const std::filesystem::path trigger = [] {
        const char* value = std::getenv("SHADPS4_NGS2_DIAGNOSTICS_TRIGGER");
        return std::filesystem::path{value ? value : ""};
    }();
    const std::lock_guard lock{mutex};
    const auto now = std::chrono::steady_clock::now();
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - start).count();
    if (!rearmed && !trigger.empty() && now >= next_check) {
        next_check = now + std::chrono::seconds{1};
        std::error_code error;
        if (std::filesystem::is_regular_file(trigger, error) && budget.Rearm()) {
            rearmed = true;
            std::fprintf(stderr, "NGS2_DIAG ms=%lld capture-rearmed window=2\n",
                         static_cast<long long>(elapsed));
            std::fflush(stderr);
        }
    }
    if (!budget.Take(key, interval, failure))
        return;
    std::array<char, 480> message{};
    std::vsnprintf(message.data(), message.size(), format, arguments);
    std::fprintf(stderr, "NGS2_DIAG ms=%lld %s\n", static_cast<long long>(elapsed), message.data());
}
inline void Record(std::uint64_t key, std::uint32_t interval, const char* format, ...) {
    va_list arguments;
    va_start(arguments, format);
    RecordV(false, key, interval, format, arguments);
    va_end(arguments);
}
inline void RecordFailure(std::uint64_t key, const char* format, ...) {
    va_list arguments;
    va_start(arguments, format);
    RecordV(true, key, 0, format, arguments);
    va_end(arguments);
}
} // namespace Libraries::Ngs2::Diagnostics
