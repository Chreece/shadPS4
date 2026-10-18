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

static Word Summary(const Words& words, bool clear) {
    Word mask{};
    for (unsigned i = 0; i < 64; ++i) {
        if (clear ? (~words[i] != 0) : (words[i] != 0)) mask |= Word{1} << i;
    }
    return mask;
}

static Word RangeMask(unsigned index, unsigned start_word, unsigned last_word,
                      unsigned start_page, unsigned last_page) {
    Word mask = ~Word{0};
    if (index == start_word) mask &= ~Word{0} << (start_page % 64);
    if (index == last_word) mask &= ~Word{0} >> (63 - (last_page % 64));
    return mask;
}

struct Result {
    Words cpu_after{}, gpu_after{};
    Words cpu_watchers{}, gpu_watchers{};
    std::vector<Bounds> uploads{};
};

static Result Simulate(Words cpu, Words gpu, unsigned first_page,
                       unsigned last_page, bool fast) {
    const unsigned first_word = first_page / 64;
    const unsigned last_word = last_page / 64;
    const Word candidate_summary = Summary(cpu, false) | Summary(gpu, true);
    Result result{};
    unsigned open_start = 0, open_end = 0;
    auto visit = [&](unsigned index) {
        const Word mask = RangeMask(index, first_word, last_word, first_page, last_page);
        const Word cpu_dirty = cpu[index] & mask;
        const Word gpu_unprotected = ~gpu[index] & mask;
        if (fast && !(cpu_dirty | gpu_unprotected)) return;
        result.cpu_watchers[index] = cpu_dirty;
        result.gpu_watchers[index] = gpu_unprotected;
        Word remaining = cpu_dirty;
        unsigned offset = 0;
        while (remaining) {
            const unsigned zeros = std::countr_zero(remaining);
            offset += zeros;
            remaining >>= zeros;
            const unsigned ones = std::countr_one(remaining);
            const unsigned start = index * 64 + offset;
            const unsigned end = start + ones;
            if (open_end == start) open_end = end;
            else {
                if (open_end > open_start) result.uploads.emplace_back(open_start, open_end);
                open_start = start;
                open_end = end;
            }
            remaining = ones < 64 ? remaining >> ones : 0;
            offset += ones;
        }
        cpu[index] &= ~mask;
        gpu[index] |= mask;
    };
    if (fast) {
        VideoCore::Detail::ForEachCpuDirtySummaryWord(
            candidate_summary, first_word, last_word,
            [&](Word i) { visit(static_cast<unsigned>(i)); });
    } else {
        for (unsigned i = first_word; i <= last_word; ++i) visit(i);
    }
    if (open_end > open_start) result.uploads.emplace_back(open_start, open_end);
    result.cpu_after = cpu;
    result.gpu_after = gpu;
    return result;
}

static void Check(const Words& cpu, const Words& gpu, unsigned start, unsigned end) {
    const Result dense = Simulate(cpu, gpu, start, end, false);
    const Result sparse = Simulate(cpu, gpu, start, end, true);
    assert(dense.cpu_after == sparse.cpu_after);
    assert(dense.gpu_after == sparse.gpu_after);
    assert(dense.cpu_watchers == sparse.cpu_watchers);
    assert(dense.gpu_watchers == sparse.gpu_watchers);
    assert(dense.uploads == sparse.uploads);
}

int main() {
    std::mt19937_64 rng(0x7545598411);
    std::uint64_t cases{};
    for (unsigned trial = 0; trial < 200000; ++trial) {
        Words cpu{}, gpu{};
        cpu.fill(0);
        gpu.fill(~Word{0});
        for (unsigned j = 0; j < 64; ++j) {
            const unsigned kind = trial % 8;
            if (kind == 0) gpu[j] = 0;
            else if (kind == 1) gpu[j] = rng();
            else if (rng() % 17 == 0) gpu[j] &= ~(Word{1} << (rng() % 64));
            if (kind == 2) cpu[j] = ~Word{0};
            else if (rng() % 11 == 0) cpu[j] |= Word{1} << (rng() % 64);
        }
        const unsigned start = static_cast<unsigned>(rng() % 4096);
        const unsigned end = start + static_cast<unsigned>(rng() % (4096 - start));
        Check(cpu, gpu, start, end);
        ++cases;
    }
    {
        Words cpu{}, gpu{};
        cpu.fill(0); gpu.fill(0);
        Check(cpu, gpu, 0, 4095);
        ++cases;
    }
    {
        Words cpu{}, gpu{};
        cpu.fill(0); gpu.fill(~Word{0});
        Check(cpu, gpu, 0, 4095);
        ++cases;
    }
    {
        Words cpu{}, gpu{};
        cpu.fill(0); gpu.fill(~Word{0});
        cpu[0] = Word{1} << 63;
        cpu[1] = Word{1};
        const auto result = Simulate(cpu, gpu, 63, 64, true);
        assert((result.uploads == std::vector<Bounds>{{63, 65}}));
        Check(cpu, gpu, 63, 64);
        ++cases;
    }
    {
        Words cpu{}, gpu{};
        cpu.fill(0); gpu.fill(~Word{0});
        gpu[0] &= ~(Word{1} << 63);
        gpu[1] &= ~Word{1};
        const auto result = Simulate(cpu, gpu, 63, 64, true);
        assert(result.uploads.empty());
        assert(result.gpu_watchers[0] == (Word{1} << 63));
        assert(result.gpu_watchers[1] == Word{1});
        Check(cpu, gpu, 63, 64);
        ++cases;
    }
    std::cout << "GPU_WRITE_TRANSITION_NATIVE=PASS cases=" << cases << '\n'
              << "CPU_DIRTY_UPLOAD_AND_GPU_PROTECTION_MASKS_EQUAL=PASS\n"
              << "GPU_STATE_TRANSITIONS_AND_CROSS_WORD_RANGES_EQUAL=PASS\n";
}
