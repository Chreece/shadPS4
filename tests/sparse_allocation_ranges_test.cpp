// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cstdint>
#include <iostream>
#include <utility>
#include <vector>
#include "video_core/buffer_cache/sparse_allocation_ranges.h"

using Segment = std::pair<std::uint64_t, std::uint64_t>;

static bool Verify(std::uint64_t first, std::uint64_t end, std::uint64_t block_size,
                   std::uint64_t budget, std::uint64_t expected_count) {
    std::vector<Segment> segments;
    VideoCore::ForEachSparseAllocationSegment(first, end, block_size, budget,
        [&](std::uint64_t a, std::uint64_t b) { segments.emplace_back(a, b); });
    if (segments.size() != expected_count) return false;
    std::uint64_t previous = first;
    std::uint64_t covered = 0;
    for (auto [a, b] : segments) {
        if (a != previous || a >= b || (b - a) * block_size >
                (block_size > budget ? block_size : budget)) return false;
        previous = b;
        covered += b - a;
    }
    return previous == (segments.empty() ? first : end) && covered == end - first;
}

int main() {
    constexpr std::uint64_t MiB = 1024ULL * 1024ULL;
    constexpr std::uint64_t GiB = 1024ULL * MiB;
    if (!Verify(0, 0, 16*1024, 128*MiB, 0)) return 1;
    if (!Verify(0, 1, 16*1024, 128*MiB, 1)) return 2;
    if (!Verify(0, GiB/(16*1024), 16*1024, 128*MiB, 8)) return 3;
    if (!Verify(123, 123+(1402884256ULL/(16*1024))+1, 16*1024,
                128*MiB, 11)) return 4;
    if (!Verify(1000, 1000+(3*GiB/(64*1024)), 64*1024, 128*MiB, 24)) return 5;
    if (!Verify(100, 104, 256*MiB, 128*MiB, 4)) return 6;
    if (!Verify((1ULL<<32)-4, (1ULL<<32)+6, 16*1024,
                128*MiB, 1)) return 7;
    // Disjoint guest ranges stay disjoint; no gap is allocated or remapped.
    std::uint64_t total=0;
    for (Segment gap : {Segment{4,14}, Segment{500, 500+16384}}) {
        VideoCore::ForEachSparseAllocationSegment(gap.first, gap.second,
            16*1024, 128*MiB, [&](auto a, auto b) {total+=b-a;});
    }
    if (total != 10+16384) return 8;
    std::cout << "SPARSE_ALLOCATION_EXACT_COVERAGE=PASS\n";
}
