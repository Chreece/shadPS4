// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cassert>
#include <cstdint>

namespace VideoCore {

// Partition physical Vulkan sparse-memory allocations, never the virtual guest
// buffer range. Every output segment contains whole sparse blocks and the
// union exactly covers [first_block, end_block), including large V# resources.
template <typename Emit>
constexpr void ForEachSparseAllocationSegment(std::uint64_t first_block,
                                               std::uint64_t end_block,
                                               std::uint64_t sparse_block_bytes,
                                               std::uint64_t preferred_allocation_bytes,
                                               Emit&& emit) {
    assert(first_block <= end_block);
    assert(sparse_block_bytes != 0 && preferred_allocation_bytes != 0);
    const std::uint64_t blocks_per_allocation =
        std::max<std::uint64_t>(1, preferred_allocation_bytes / sparse_block_bytes);
    while (first_block != end_block) {
        const std::uint64_t count =
            std::min(blocks_per_allocation, end_block - first_block);
        const std::uint64_t next = first_block + count;
        emit(first_block, next);
        first_block = next;
    }
}

} // namespace VideoCore
