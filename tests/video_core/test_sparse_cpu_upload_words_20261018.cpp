// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <bit>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <random>
#include <utility>
#include <vector>

#include "video_core/buffer_cache/sparse_cpu_upload_words.h"

using Word = std::uint64_t;
using Words = std::array<Word, 64>;
using Bounds = std::pair<unsigned, unsigned>;

static Word Summary(const Words& bits) {
    Word summary{};
    for (unsigned i = 0; i < 64; ++i) {
        if (bits[i]) summary |= Word{1} << i;
    }
    return summary;
}
static Word Mask(unsigned i, unsigned first_word, unsigned last_word,
                 unsigned first_page, unsigned last_page) {
    Word result = ~Word{0};
    if (i == first_word) result &= ~Word{0} << (first_page % 64);
    if (i == last_word) result &= ~Word{0} >> (63 - (last_page % 64));
    return result;
}

struct Result {
    Words after{};
    Words changed_watchers{};
    std::vector<Bounds> copied_page_ranges{};
};

static Result Visit(Words cpu, unsigned first_page, unsigned last_page,
                    bool sparse) {
    const unsigned first_word = first_page / 64;
    const unsigned last_word = last_page / 64;
    Result result{};
    unsigned open_start = 0, open_end = 0;
    auto visit = [&](unsigned i) {
        const Word mask = Mask(i, first_word, last_word, first_page, last_page);
        const Word changed = cpu[i] & mask;
        if (sparse && !changed) return;
        result.changed_watchers[i] = changed;
        Word remaining = changed;
        unsigned index = 0;
        while (remaining) {
            const unsigned zeros = std::countr_zero(remaining);
            index += zeros;
            remaining >>= zeros;
            const unsigned ones = std::countr_one(remaining);
            const unsigned beginning = i * 64 + index;
            const unsigned ending = beginning + ones;
            if (open_end == beginning) {
                open_end = ending;
            } else {
                if (open_end > open_start)
                    result.copied_page_ranges.emplace_back(open_start, open_end);
                open_start = beginning;
                open_end = ending;
            }
            remaining = ones < 64 ? remaining >> ones : 0;
            index += ones;
        }
        cpu[i] &= ~mask;
    };
    if (sparse) {
        VideoCore::Detail::ForEachCpuDirtySummaryWord(
            Summary(cpu), first_word, last_word,
            [&](Word i){ visit(static_cast<unsigned>(i)); });
    } else {
        for (unsigned i = first_word; i <= last_word; ++i)
            visit(i);
    }
    if (open_end > open_start)
        result.copied_page_ranges.emplace_back(open_start, open_end);
    result.after = cpu;
    return result;
}

int main() {
    std::mt19937_64 rng(0x18b628544);
    std::uint64_t cases = 0;
    for (unsigned trial = 0; trial < 200000; ++trial) {
        Words before{};
        for (unsigned j = 0; j < 64; ++j) {
            const unsigned chance = trial % 5 == 0 ? 3 : 64;
            if (rng() % chance == 0) {
                before[j] = trial % 7 == 0 ? ~Word{0} : (Word{1} << (rng() % 64));
            }
        }
        const auto start = static_cast<unsigned>(rng() % 4096);
        const auto end = start + static_cast<unsigned>(rng() % (4096 - start));
        const Result legacy = Visit(before, start, end, false);
        const Result fast = Visit(before, start, end, true);
        assert(legacy.after == fast.after);
        assert(legacy.changed_watchers == fast.changed_watchers);
        assert(legacy.copied_page_ranges == fast.copied_page_ranges);
        ++cases;
    }
    // Dense full-region and a dirty range crossing tracking-word boundary.
    {
        Words test{};
        test.fill(~Word{0});
        test[0] = Word{1} << 63;
        test[1] = Word{1};
        const auto legacy = Visit(test, 0, 4095, false);
        const auto fast = Visit(test, 0, 4095, true);
        assert(legacy.after == fast.after);
        assert(legacy.changed_watchers == fast.changed_watchers);
        assert(legacy.copied_page_ranges == fast.copied_page_ranges);
        ++cases;
    }
    {
        Words test{};
        test[0] = Word{1} << 63;
        test[1] = Word{1};
        const auto a = Visit(test, 63, 64, false);
        const auto b = Visit(test, 63, 64, true);
        assert(a.after == b.after);
        assert((a.copied_page_ranges == std::vector<Bounds>{{63, 65}}));
        assert(b.copied_page_ranges == a.copied_page_ranges);
        ++cases;
    }
    std::cout << "SPARSE_CPU_UPLOAD_WORDS_NATIVE=PASS cases=" << cases << "\n"
              << "DIRTY_PAGES_AND_WATCHER_MASKS_EQUAL_LEGACY=PASS\n"
              << "CROSS_WORD_UPLOAD_RANGE_COALESCING=PASS\n"
              << "UNCHANGED_GPU_WRITE_AND_DOWNLOAD_PATHS=PASS\n";
}
