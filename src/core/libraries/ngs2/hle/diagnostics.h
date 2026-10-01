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
#include <mutex>

namespace Libraries::Ngs2::Diagnostics {
// Fixed storage and a process-wide output bound, including concurrent systems.
// Callers serialize Budget access; Record supplies that lock.
class Budget {
public:
    bool Take(std::uint64_t key, std::uint32_t interval = 0) {
        if (emitted >= 2048)
            return false;
        for (auto& entry : entries[(key >> 32) % entries.size()]) {
            if (!entry.used || entry.key == key) {
                entry.used = true;
                entry.key = key;
                if (entry.count == UINT32_MAX)
                    return false;
                const auto n = ++entry.count;
                if (n <= 4 || (interval ? n % interval == 0 : (n & (n - 1)) == 0)) {
                    ++emitted;
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
    unsigned emitted{};
};

inline bool Enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("SHADPS4_NGS2_DIAGNOSTICS");
        return value && std::strcmp(value, "1") == 0;
    }();
    return enabled;
}

inline void Record(std::uint64_t key, std::uint32_t interval, const char* format, ...) {
    if (!Enabled())
        return;
    static std::mutex mutex;
    static Budget budget;
    static const auto start = std::chrono::steady_clock::now();
    const std::lock_guard lock{mutex};
    if (!budget.Take(key, interval))
        return;
    std::array<char, 480> message{};
    va_list arguments;
    va_start(arguments, format);
    std::vsnprintf(message.data(), message.size(), format, arguments);
    va_end(arguments);
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                             std::chrono::steady_clock::now() - start)
                             .count();
    std::fprintf(stderr, "NGS2_DIAG ms=%lld %s\n", static_cast<long long>(elapsed), message.data());
}
} // namespace Libraries::Ngs2::Diagnostics
