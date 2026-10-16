// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cassert>
#include <cstdint>
#include <cstdio>
#include "video_core/buffer_cache/sparse_arena_policy.h"

int main() {
    using namespace VideoCore;
    constexpr std::uint64_t VA = std::uint64_t{1} << 40;
    constexpr std::uint64_t max_window = std::uint64_t{1} << 32;
    constexpr std::uint64_t block = std::uint64_t{1} << 16;
    std::uint64_t cases = 0;
    for (const std::uint64_t limit : {max_window - 4, max_window,
                                      max_window * 2, max_window - block}) {
        const auto page = SparseArenaPageSize(limit);
        assert(page <= limit);
        assert(std::has_single_bit(page));
        const auto rounded_limit = limit / block * block;
        for (const std::uint64_t base : {std::uint64_t{0}, page - block, page,
                                         page * 2 - block, VA - page}) {
            for (const std::uint64_t size : {block, block * 2, page / 2,
                                             page - block, page}) {
                if (base + size > VA || size > rounded_limit) {
                    continue;
                }
                const auto w = ChooseSparseArenaWindow(
                    base, size, page, rounded_limit, block, VA);
                assert(w.size >= size);
                assert(w.size <= limit);
                assert(w.base <= base);
                assert(base + size <= w.base + w.size);
                assert(w.base % block == 0);
                assert(w.size % block == 0);
                ++cases;
            }
        }
        // An unrepresentable request fails; it must never be silently shrunk.
        assert(ChooseSparseArenaWindow(0, rounded_limit + block, page,
                                       rounded_limit, block, VA).size == 0);
        ++cases;
    }
    std::printf("GENERAL_VULKAN_SPARSE_ARENA_LIMIT=PASS cases=%llu\n",
                static_cast<unsigned long long>(cases));
}
