// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <span>
#include <vector>

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
// Actual VkWriteDescriptorSet counts determine pool demand. A shader may
// reference the same GPU descriptor table through multiple independently
// bound sampled-image resources; the table construction log is NOT a set
// layout count. Preserve each descriptor type and use 64-bit accumulators
// before converting the count to Vulkan's 32-bit poolSize type.
template <typename Size>
std::vector<Size> ExpandDescriptorPoolSizes(std::span<const Size> baseline,
                                             std::span<const Size> minimum) {
    std::vector<Size> sizes(baseline.begin(), baseline.end());
    for (const auto& request : minimum) {
        auto it = std::find_if(sizes.begin(), sizes.end(), [&](const Size& p) {
            return p.type == request.type;
        });
        if (it == sizes.end()) {
            sizes.push_back(request);
        } else {
            it->descriptorCount = std::max(it->descriptorCount, request.descriptorCount);
        }
    }
    return sizes;
}

template <typename Size>
std::vector<Size> ExactDescriptorPoolSizes(std::span<const Size> minimum) {
    // The Vulkan layout's actual descriptor writes, one set, no baseline
    // padding. Distinguish a driver/pool-configuration failure from a
    // genuinely under-provisioned sampled-image allocation.
    return std::vector<Size>(minimum.begin(), minimum.end());
}

template <typename Size>
bool PoolCoversDescriptorRequirements(std::span<const Size> pool,
                                       std::span<const Size> required) {
    for (const auto& needed : required) {
        const auto it = std::find_if(pool.begin(), pool.end(), [&](const Size& size) {
            return size.type == needed.type;
        });
        if (it == pool.end() || it->descriptorCount < needed.descriptorCount) {
            return false;
        }
    }
    return true;
}
} // namespace Vulkan
