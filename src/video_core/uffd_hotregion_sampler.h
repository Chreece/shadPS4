// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

namespace VideoCore {

// Diagnostic-only: sample about 1/128 WP events, then retain 64 most frequent
// 64-KiB guest regions using bounded SpaceSaving counters. No guest mapping,
// memory protection, GPU API, or fault ordering is changed by this class.
class UffdHotRegionSampler {
public:
    struct Entry {
        std::uint64_t region{};
        std::uint32_t count{};
        std::uint32_t error{};

        std::uint32_t MinimumCount() const noexcept {
            return count >= error ? count - error : 0;
        }
    };
    static constexpr std::size_t Capacity = 64;
    static constexpr std::size_t ReportCount = 8;
    static constexpr std::uint64_t RegionSize = 65536;

    void Observe(std::uint64_t fault_address) noexcept {
        ++events_;
        // Odd multiplicative permutation of 64-bit event serial:
        // takes a reproducible, non-contiguous 1/128 sample; no allocations.
        if ((events_ * std::uint64_t{0x9e3779b97f4a7c15} >> 57) != 0) {
            return;
        }
        ++sampled_;
        const std::uint64_t region = fault_address & ~(RegionSize - 1);
        Entry* first_empty = nullptr;
        Entry* least = &entries_[0];
        for (Entry& entry : entries_) {
            if (entry.count != 0 && entry.region == region) {
                ++entry.count;
                return;
            }
            if (entry.count == 0 && !first_empty) {
                first_empty = &entry;
            }
            if (entry.count < least->count) {
                least = &entry;
            }
        }
        if (first_empty) {
            *first_empty = Entry{region, 1, 0};
            return;
        }
        // All 64 slots occupied. A replacement is an approximate count;
        // report its lower bound as count - error, never as an exact hit count.
        const std::uint32_t prior = least->count;
        *least = Entry{region, prior + 1, prior};
        ++replacements_;
    }

    std::array<Entry, ReportCount> Top() const noexcept {
        auto sorted = entries_;
        std::sort(sorted.begin(), sorted.end(), [](const Entry& a, const Entry& b) {
            return a.count > b.count;
        });
        std::array<Entry, ReportCount> result{};
        std::copy_n(sorted.begin(), result.size(), result.begin());
        return result;
    }

    std::uint64_t Sampled() const noexcept { return sampled_; }
    std::uint64_t Events() const noexcept { return events_; }
    std::uint64_t Replacements() const noexcept { return replacements_; }

    void ResetWindow() noexcept {
        entries_.fill({});
        sampled_ = 0;
        replacements_ = 0;
        // Do not reset events_: a periodic 128-page access pattern must not
        // be sampled at precisely the same position on every window.
    }

private:
    std::array<Entry, Capacity> entries_{};
    std::uint64_t events_{};
    std::uint64_t sampled_{};
    std::uint64_t replacements_{};
};

} // namespace VideoCore
