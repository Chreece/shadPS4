// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstddef>
#include <utility>
#include <vector>

namespace Shader::Optimization {

enum class SrtAttemptResult { Resolved, Retry, Deferred };

struct SrtRetryStats {
    std::size_t resolved{};
    std::size_t recovered{};
    std::size_t deferred{};
    std::size_t unresolved{};
    std::size_t passes{};
    bool capped{};
};

// Preserve initial entry order. Retry ONLY entries whose offset-codegen has
// failed, after successfully emitted entries may have made dependencies ready.
// Stop when a pass makes no progress; never invent guest resource data or
// assign an invalid flat-buffer offset. Bound passes to avoid unbounded JIT.
template <typename Entry, typename Attempt>
SrtRetryStats ResolveSrtForwardDependencies(std::vector<Entry> entries, Attempt&& attempt,
                                            std::size_t max_passes = 8) {
    SrtRetryStats stats{};
    if (max_passes == 0) {
        stats.unresolved = entries.size();
        stats.capped = !entries.empty();
        return stats;
    }
    for (std::size_t pass = 0; pass < max_passes && !entries.empty(); ++pass) {
        ++stats.passes;
        std::size_t progress = 0;
        std::vector<Entry> retry;
        retry.reserve(entries.size());
        for (const auto& entry : entries) {
            switch (attempt(entry)) {
            case SrtAttemptResult::Resolved:
                ++progress;
                ++stats.resolved;
                if (pass != 0) ++stats.recovered;
                break;
            case SrtAttemptResult::Deferred:
                ++stats.deferred;
                break;
            case SrtAttemptResult::Retry:
                retry.push_back(entry);
                break;
            }
        }
        if (retry.empty()) {
            entries.clear();
            break;
        }
        entries.swap(retry);
        if (progress == 0) break;
    }
    stats.unresolved = entries.size();
    stats.capped = stats.unresolved != 0 && stats.passes >= max_passes;
    return stats;
}

} // namespace Shader::Optimization
