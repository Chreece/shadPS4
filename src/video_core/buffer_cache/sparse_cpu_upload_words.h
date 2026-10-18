// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <bit>
#include <cstdint>
#include <utility>

namespace VideoCore::Detail {

// Iterate exactly those 64-page tracking words whose summary says at least
// one page is dirty. The caller still applies its original partial-word masks
// and performs ALL original state transitions, watcher updates and copies.
// Invoked only in the opt-in CPU-dirty upload-clear path.
template <typename Callback>
inline void ForEachCpuDirtySummaryWord(std::uint64_t summary,
                                       std::uint64_t first_word,
                                       std::uint64_t last_word,
                                       Callback&& visitor) {
    if (first_word > last_word || last_word >= 64) {
        return;
    }
    summary &= (~std::uint64_t{0} << first_word);
    if (last_word != 63) {
        summary &= ((std::uint64_t{1} << (last_word + 1)) - 1);
    }
    while (summary) {
        const unsigned index = std::countr_zero(summary);
        summary &= summary - 1;
        std::forward<Callback>(visitor)(static_cast<std::uint64_t>(index));
    }
}

} // namespace VideoCore::Detail
