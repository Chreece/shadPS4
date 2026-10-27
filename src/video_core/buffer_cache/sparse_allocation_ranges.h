// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cassert>
#include <cstdint>

namespace VideoCore {

// A Vulkan allocation can contain any number of disjoint guest-VA gaps.
// The number of allocations therefore depends on the TOTAL newly backed
// sparse blocks, never the count of gaps in the guest virtual address space.
constexpr std::uint64_t SparseBlocksPerAllocation(std::uint64_t block_bytes,
                                                  std::uint64_t preferred_bytes) {
    assert(block_bytes > 0 && preferred_bytes > 0);
    return std::max<std::uint64_t>(1, preferred_bytes / block_bytes);
}

// For an unbacked guest-VA gap [first,end), return its exact covered pieces
// with (allocation_index, memory_offset_in_blocks) into a packed physical plan.
// packed_blocks is carried ACROSS gaps. No guest byte address or access range
// is changed; splitting only changes physical VkDeviceMemory allocations.
template <typename Emit>
constexpr void ForEachSparseAllocationSegment(std::uint64_t first_block,
                                               std::uint64_t end_block,
                                               std::uint64_t& packed_blocks,
                                               std::uint64_t blocks_per_allocation,
                                               Emit&& emit) {
    assert(first_block <= end_block && blocks_per_allocation != 0);
    while (first_block != end_block) {
        const std::uint64_t allocation_index = packed_blocks / blocks_per_allocation;
        const std::uint64_t memory_offset_blocks = packed_blocks % blocks_per_allocation;
        const std::uint64_t count = std::min(end_block - first_block,
                                              blocks_per_allocation - memory_offset_blocks);
        emit(first_block, first_block + count, allocation_index, memory_offset_blocks);
        first_block += count;
        packed_blocks += count;
    }
}

} // namespace VideoCore
