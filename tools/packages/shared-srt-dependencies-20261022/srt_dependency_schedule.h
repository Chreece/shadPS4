// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstddef>
#include <numeric>
#include <utility>
#include <vector>

namespace Shader::Optimization {

enum class SrtReadAttempt { Complete, Deferred, Unsupported };

struct SrtReadScheduleResult {
    std::size_t completed{};
    std::size_t pending{};
    std::size_t unsupported{};
    std::size_t sweeps{};
};

// Retry only deferred reads. A successful attempt must make its actual value
// available to subsequent attempts. Unknown/dynamic expressions are not values.
// This schedules host code generation, never guest execution or GPU polling.
template <typename Attempt>
SrtReadScheduleResult ScheduleSrtReads(std::size_t count, Attempt&& attempt) {
    std::vector<std::size_t> pending(count);
    std::iota(pending.begin(), pending.end(), std::size_t{0});
    SrtReadScheduleResult result;
    while (!pending.empty()) {
        ++result.sweeps;
        bool progressed = false;
        std::size_t retained = 0;
        for (const auto index : pending) {
            switch (attempt(index)) {
            case SrtReadAttempt::Complete:
                ++result.completed;
                progressed = true;
                break;
            case SrtReadAttempt::Deferred:
                pending[retained++] = index;
                break;
            case SrtReadAttempt::Unsupported:
                ++result.unsupported;
                break;
            }
        }
        pending.resize(retained);
        if (!progressed) {
            break;
        }
    }
    result.pending = pending.size();
    return result;
}

struct SrtRootScheduleResult {
    std::size_t rounds{};
    std::size_t new_words{};
    std::size_t pending{};
    bool invalid_progress{};
};

// Each sweep visits every root and returns the still-deferred read count.
// Progress is the monotonically increasing number of allocated flatbuffer words.
// The caller owns rollback of a sweep that produces no new words. The capacity
// is the real flatbuffer representation limit, not an arbitrary iteration cap.
template <typename Measure, typename Sweep>
SrtRootScheduleResult ScheduleSrtRoots(std::size_t capacity, Measure&& measure, Sweep&& sweep) {
    SrtRootScheduleResult result;
    const std::size_t start = measure();
    if (start > capacity) {
        result.invalid_progress = true;
        return result;
    }
    for (;;) {
        const std::size_t before = measure();
        result.pending = sweep();
        ++result.rounds;
        const std::size_t after = measure();
        if (after < before || after > capacity) {
            result.invalid_progress = true;
            return result;
        }
        result.new_words = after - start;
        if (result.pending == 0 || after == before || after == capacity) {
            return result;
        }
    }
}

} // namespace Shader::Optimization
