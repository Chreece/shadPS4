// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <time.h>

namespace ScalarAB {
inline std::array<std::atomic<uint64_t>, 4> sites{};
inline std::array<std::atomic<uint64_t>, 4> hits{};
inline std::array<std::atomic<uint64_t>, 4> changes{};
static_assert(std::atomic<uint64_t>::is_always_lock_free);
static_assert(sizeof(std::atomic<uint64_t>) == sizeof(uint64_t));

inline bool Enabled() {
    static const bool value = std::getenv("SHADPS4_SCALAR_AB_DIR") != nullptr;
    return value;
}
inline bool Fixed() {
    const auto* mode = std::getenv("SHADPS4_SCALAR_AB_MODE");
    return !mode || std::strcmp(mode, "native") != 0;
}
inline bool Diagnostic() {
    static const bool value = [] {
        const auto* mode = std::getenv("SHADPS4_SCALAR_AB_MODE");
        return mode && std::strcmp(mode, "diagnostic") == 0;
    }();
    return value;
}
inline uint64_t Now() {
    timespec value{};
    clock_gettime(CLOCK_MONOTONIC, &value);
    return uint64_t(value.tv_sec) * 1000000000 + value.tv_nsec;
}
inline std::string Path(const char* name) {
    return std::string(std::getenv("SHADPS4_SCALAR_AB_DIR")) + "/" + name;
}
inline void Frame() {
    if (!Enabled()) {
        return;
    }
    static FILE* output = std::fopen(Path("frames.csv").c_str(), "w");
    static uint64_t sequence = 0;
    static uint64_t last_flush = 0;
    const auto now = Now();
    if (output) {
        std::fprintf(output, "%llu,%llu\n", (unsigned long long)now,
                     (unsigned long long)++sequence);
        if (now - last_flush >= 1000000000) {
            std::fflush(output);
            last_flush = now;
        }
    }
}
inline void Input(uint32_t type) {
    if (Enabled()) {
        if (FILE* output = std::fopen(Path("input.csv").c_str(), "a")) {
            std::fprintf(output, "%llu,%u\n", (unsigned long long)Now(), type);
            std::fclose(output);
        }
    }
}
inline std::string Control() {
    if (!Enabled()) {
        return {};
    }
    static uint64_t last_poll = 0;
    static uint64_t last_dump = 0;
    const auto now = Now();
    if (now - last_poll < 100000000) {
        return {};
    }
    last_poll = now;
    if (now - last_dump >= 1000000000) {
        static FILE* output = std::fopen(Path("counters.csv").c_str(), "w");
        if (output) {
            std::fprintf(output, "%llu", (unsigned long long)now);
            for (const auto* values : {&sites, &hits, &changes}) {
                for (const auto& value : *values) {
                    std::fprintf(output, ",%llu",
                                 (unsigned long long)value.load(std::memory_order_relaxed));
                }
            }
            std::fprintf(output, "\n");
            std::fflush(output);
        }
        last_dump = now;
    }
    const auto path = Path("control");
    if (FILE* input = std::fopen(path.c_str(), "r")) {
        char value[32]{};
        const bool valid = std::fgets(value, sizeof(value), input) != nullptr;
        std::fclose(input);
        std::remove(path.c_str());
        return valid ? std::string(value) : std::string{};
    }
    return {};
}
} // namespace ScalarAB
