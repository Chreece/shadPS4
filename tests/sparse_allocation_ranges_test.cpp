// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <utility>
#include <vector>
#include "video_core/buffer_cache/sparse_allocation_ranges.h"

using GuestGap = std::pair<std::uint64_t, std::uint64_t>;
struct PhysicalSegment {
    std::uint64_t start, end, allocation, offset;
};

static bool Verify(const std::vector<GuestGap>& gaps, std::uint64_t block_size,
                   std::uint64_t target_size, std::uint64_t expected_allocations) {
    using VideoCore::ForEachSparseAllocationSegment;
    using VideoCore::SparseBlocksPerAllocation;
    const auto capacity = SparseBlocksPerAllocation(block_size, target_size);
    std::uint64_t total_blocks = 0, packed = 0;
    std::vector<PhysicalSegment> plan;
    for (const auto& [first, end] : gaps) {
        if (end < first) return false;
        total_blocks += end - first;
        ForEachSparseAllocationSegment(first, end, packed, capacity,
            [&](auto start, auto stop, auto allocation, auto offset) {
                plan.push_back({start, stop, allocation, offset});
            });
    }
    if (packed != total_blocks) return false;
    const auto computed_allocations = total_blocks == 0 ? 0 : 1 + (total_blocks - 1) / capacity;
    if (computed_allocations != expected_allocations) return false;
    // Every guest gap has exact contiguous coverage, no additional pages.
    std::size_t next = 0;
    std::uint64_t expected_packed_offset = 0;
    for (const auto& [first, end] : gaps) {
        std::uint64_t cursor = first;
        while (cursor < end) {
            if (next == plan.size()) return false;
            const auto part = plan[next++];
            if (part.start != cursor || part.end <= part.start || part.end > end ||
                part.allocation != expected_packed_offset / capacity ||
                part.offset != expected_packed_offset % capacity ||
                part.offset + (part.end - part.start) > capacity) return false;
            cursor = part.end;
            expected_packed_offset += part.end - part.start;
        }
    }
    if (next != plan.size() || expected_packed_offset != total_blocks) return false;
    // Check the number and exact sizes of the underlying VkDeviceMemory blocks.
    for (std::uint64_t allocation = 0; allocation < computed_allocations; ++allocation) {
        std::uint64_t blocks = 0;
        for (const auto& part : plan) if (part.allocation == allocation)
            blocks += part.end - part.start;
        if (blocks != std::min(capacity, total_blocks - allocation * capacity)) return false;
    }
    return true;
}

int main() {
    constexpr std::uint64_t MiB = 1024ULL * 1024ULL;
    constexpr std::uint64_t GiB = 1024ULL * MiB;
    if (!Verify({}, 16*1024, 128*MiB, 0)) return 1;
    if (!Verify({{0,0},{30,31}}, 16*1024, 128*MiB, 1)) return 2;
    if (!Verify({{0,GiB/(16*1024)}}, 16*1024, 128*MiB, 8)) return 3;
    if (!Verify({{3,3+1402884256ULL/(16*1024)+1}}, 16*1024, 128*MiB, 11)) return 4;
    if (!Verify({{0,3*GiB/(64*1024)}}, 64*1024, 128*MiB, 24)) return 5;
    if (!Verify({{100,104}}, 256*MiB, 128*MiB, 4)) return 6;
    if (!Verify({{(1ULL<<32)-4,(1ULL<<32)+6}}, 16*1024,128*MiB,1)) return 7;
    // Thousands of tiny separated gaps still get ONE packed allocation.
    std::vector<GuestGap> gaps;
    for (std::uint64_t n=0;n<5514;n++) gaps.push_back({n*5, n*5+1});
    if (!Verify(gaps,16*1024,128*MiB,1)) return 8;
    // Gap boundaries must share the same allocation until it fills.
    if (!Verify({{0,8191},{10000,10010}},16*1024,128*MiB,2)) return 9;
    if (!Verify({{3,8193},{9000,9002},{20000,20017}},16*1024,128*MiB,2)) return 10;
    std::cout << "PACKED_SPARSE_ALLOCATION_EXACT_COVERAGE=PASS\n";
}
