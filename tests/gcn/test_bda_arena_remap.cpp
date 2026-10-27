// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <cstdint>
#include <random>
#include <vector>
#include <gtest/gtest.h>

#include "video_core/buffer_cache/bda_arena_remap.h"

namespace {
using VideoCore::BdaTableByteOffset;
using VideoCore::ForEachReboundBdaBlock;
using u64 = std::uint64_t;

TEST(BdaArenaRemap, VirtualAddressesChangeWhenMergedBufferChangesBase) {
    constexpr u64 block_size = 0x10000;
    constexpr u64 guest_start = 0x200000000ULL;
    constexpr u64 new_base = 0x710000000000ULL;
    std::vector<std::pair<u64, u64>> entries;
    ForEachReboundBdaBlock(guest_start, new_base, block_size, guest_start / block_size + 3,
                            guest_start / block_size + 6,
                            [&](u64 block, u64 bda) { entries.emplace_back(block, bda); });
    ASSERT_EQ(entries.size(), 3u);
    EXPECT_EQ(entries[0].second, new_base + block_size * 3);
    EXPECT_EQ(entries[1].second, new_base + block_size * 4);
    EXPECT_EQ(entries[2].second, new_base + block_size * 5);
    EXPECT_EQ(BdaTableByteOffset(entries[0].first),
              (guest_start / block_size + 3) * sizeof(u64));
}

TEST(BdaArenaRemap, NoncontiguousResidentRangesDoNotPopulateHoles) {
    constexpr u64 block_size = 0x4000;
    constexpr u64 base_guest = 0x400000000ULL;
    constexpr u64 base_block = base_guest / block_size;
    constexpr u64 base_device = 0x100000000000ULL;
    std::array<u64, 32> table{};
    const auto record = [&](u64 global_block, u64 bda) {
        const auto index = global_block - base_block;
        ASSERT_LT(index, table.size());
        table[index] = bda;
    };
    ForEachReboundBdaBlock(base_guest, base_device, block_size,
                            base_block + 1, base_block + 4, record);
    ForEachReboundBdaBlock(base_guest, base_device, block_size,
                            base_block + 10, base_block + 12, record);
    EXPECT_EQ(table[0], 0u);
    EXPECT_EQ(table[1], base_device + block_size);
    EXPECT_EQ(table[3], base_device + block_size * 3);
    EXPECT_EQ(table[4], 0u);
    EXPECT_EQ(table[9], 0u);
    EXPECT_EQ(table[10], base_device + block_size * 10);
    EXPECT_EQ(table[11], base_device + block_size * 11);
    EXPECT_EQ(table[12], 0u);
}

TEST(BdaArenaRemap, RepeatedMigrationsRepointExistingEntries) {
    constexpr u64 size = 0x10000;
    constexpr u64 start = 0x300000000ULL;
    constexpr u64 first = start / size;
    std::array<u64, 5> table{};
    for (const u64 arena_device_base : {0x100000000000ULL, 0x500000000000ULL}) {
        ForEachReboundBdaBlock(start, arena_device_base, size, first, first + table.size(),
                                [&](u64 block, u64 addr) { table[block - first] = addr; });
        EXPECT_EQ(table[0], arena_device_base);
        EXPECT_EQ(table[4], arena_device_base + 4 * size);
    }
}

TEST(BdaArenaRemap, EmptyResidentRangeDoesNotWrite) {
    unsigned writes = 0;
    ForEachReboundBdaBlock(0, 0x100000ULL, 0x10000, 50, 50,
                           [&](u64, u64) { ++writes; });
    EXPECT_EQ(writes, 0u);
}

TEST(BdaArenaRemap, GuestGlobalBlocksDoNotUseLocalArenaPageIndex) {
    constexpr u64 arena_guest_base = 0x1200000000ULL;
    constexpr u64 block_size = 0x4000;
    constexpr u64 first = arena_guest_base / block_size + 10;
    constexpr u64 base_device = 0x630000000000ULL;
    ForEachReboundBdaBlock(arena_guest_base, base_device, block_size, first, first + 1,
                           [&](u64 global_block, u64 address) {
        EXPECT_EQ(global_block, first);
        EXPECT_EQ(address, base_device + block_size * 10);
        EXPECT_EQ(BdaTableByteOffset(global_block), first * sizeof(u64));
    });
}

TEST(BdaArenaRemap, SeededResidentIntervalsHaveMonotonicCorrectAddresses) {
    std::mt19937_64 random{0xBDA20261027ULL};
    for (int trial = 0; trial < 3000; ++trial) {
        const u64 block_size = (random() & 1) ? 0x4000 : 0x10000;
        const u64 base_block = (random() % 0x100000) + 0x20000;
        const u64 base_guest = base_block * block_size;
        const u64 base_device = 0x100000000000ULL + (random() & 0xfffff000ULL);
        const u64 first = base_block + random() % 100;
        const u64 end = first + 1 + random() % 150;
        u64 count = 0;
        ForEachReboundBdaBlock(base_guest, base_device, block_size, first, end,
                               [&](u64 index, u64 gpu_address) {
            EXPECT_EQ(index, first + count);
            EXPECT_EQ(gpu_address, base_device + (index - base_block) * block_size);
            EXPECT_EQ(BdaTableByteOffset(index), index * sizeof(u64));
            ++count;
        });
        EXPECT_EQ(count, end - first);
    }
}

} // namespace
