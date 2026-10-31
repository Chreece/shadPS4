// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdint>
#include <iostream>
#include <vector>
#include "video_core/renderer_vulkan/vk_descriptor_batch_plan.h"

enum class Result { Success, OutOfPool, Fragmented, DeviceLost };
struct FakePool {
    std::uint32_t remaining;
    std::uint32_t images_per_set;
    std::vector<std::uint32_t> attempted;
    Result Allocate(std::uint32_t n) {
        attempted.push_back(n);
        if (std::uint64_t{n} * images_per_set > remaining) return Result::OutOfPool;
        remaining -= n * images_per_set;
        return Result::Success;
    }
};
std::uint32_t Try(FakePool& pool, Result& r) {
    return Vulkan::TryAllocateDescriptorBatch([&](std::uint32_t n){return pool.Allocate(n);},
        r, Result::Success, Result::OutOfPool, Result::Fragmented);
}
int main() {
    static_assert(Vulkan::DescriptorAllocationBatchSizes[0] == 32);
    static_assert(Vulkan::DescriptorAllocationBatchSizes.back() == 1);
    FakePool new_pool{8192, 827, {}};
    Result r=Result::OutOfPool;
    assert(Try(new_pool,r)==8 && r==Result::Success);
    assert(new_pool.attempted==std::vector<std::uint32_t>({32,16,8}));
    assert(new_pool.remaining==1576);
    new_pool.attempted.clear();
    assert(Try(new_pool,r)==1 && new_pool.remaining==749);
    new_pool.attempted.clear();
    assert(Try(new_pool,r)==0 && r==Result::OutOfPool && new_pool.remaining==749);
    FakePool rotated_pool{8192, 827, {}};
    assert(Try(rotated_pool,r)==8 && r==Result::Success);
    // A fragmented pool can still accept a smaller batch.
    std::uint32_t attempts=0;
    auto count=Vulkan::TryAllocateDescriptorBatch([&](std::uint32_t n) {
        ++attempts;
        return n>2?Result::Fragmented:Result::Success;
    },r,Result::Success,Result::OutOfPool,Result::Fragmented);
    assert(count==2 && attempts==5);
    // A true device fault is not retried or disguised as pool exhaustion.
    attempts=0;
    count=Vulkan::TryAllocateDescriptorBatch([&](std::uint32_t) {
        ++attempts;return Result::DeviceLost;
    },r,Result::Success,Result::OutOfPool,Result::Fragmented);
    assert(count==0 && attempts==1 && r==Result::DeviceLost);
    // Even one oversized set must fail rather than fabricate a descriptor.
    FakePool oversized{8192,8193,{}};
    assert(Try(oversized,r)==0 && oversized.remaining==8192);
    std::cout << "DESCRIPTOR_BATCH_ADAPTIVE_GPU_ARRAY=PASS\n";
    std::cout << "DESCRIPTOR_POOL_ROLLOVER_AND_FRAGMENTATION=PASS\n";
    std::cout << "UNEXPECTED_VULKAN_ERROR_FAIL_CLOSED=PASS\n";
}
