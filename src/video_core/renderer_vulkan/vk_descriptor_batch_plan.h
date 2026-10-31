// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <array>
#include <cstdint>

namespace Vulkan {
// Shrink the Vulkan allocation batch instead of repeating an impossible
// 32-set request in each newly created pool. This is independent of title,
// shader identity, descriptor type, and Vulkan device/vendor.
inline constexpr std::array<std::uint32_t, 6> DescriptorAllocationBatchSizes{32, 16, 8, 4, 2, 1};

template <typename Result, typename Attempt>
std::uint32_t TryAllocateDescriptorBatch(Attempt&& attempt, Result& result,
                                          Result success, Result out_of_pool,
                                          Result fragmented_pool) {
    for (const std::uint32_t count : DescriptorAllocationBatchSizes) {
        result = attempt(count);
        if (result == success) {
            return count;
        }
        if (result != out_of_pool && result != fragmented_pool) {
            return 0; // Propagate device loss/other unexpected Vulkan errors.
        }
    }
    return 0;
}
} // namespace Vulkan
