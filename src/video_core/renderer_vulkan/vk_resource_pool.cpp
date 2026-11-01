// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <cstddef>
#include <limits>
#include <optional>

#include "common/assert.h"
#include "common/logging/log.h"
#include "video_core/renderer_vulkan/vk_descriptor_batch_plan.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_resource_pool.h"
#include "video_core/renderer_vulkan/vk_semaphore.h"

namespace Vulkan {

ResourcePool::ResourcePool(Semaphore* work_semaphore_, std::size_t grow_step_)
    : work_semaphore{work_semaphore_}, grow_step{grow_step_} {}

std::size_t ResourcePool::CommitResource() {
    u64 gpu_tick = work_semaphore->KnownGpuTick();
    const auto search = [this, gpu_tick](std::size_t begin,
                                         std::size_t end) -> std::optional<std::size_t> {
        for (std::size_t iterator = begin; iterator < end; ++iterator) {
            if (gpu_tick >= ticks[iterator]) {
                ticks[iterator] = work_semaphore->CurrentTick();
                return iterator;
            }
        }
        return std::nullopt;
    };

    // Try to find a free resource from the hinted position to the end.
    auto found = search(hint_iterator, ticks.size());
    if (!found) {
        // Refresh semaphore to query updated results
        work_semaphore->Refresh();
        gpu_tick = work_semaphore->KnownGpuTick();
        found = search(hint_iterator, ticks.size());
    }
    if (!found) {
        // Search from beginning to the hinted position.
        found = search(0, hint_iterator);
        if (!found) {
            // Both searches failed, the pool is full; handle it.
            const std::size_t free_resource = ManageOverflow();

            ticks[free_resource] = work_semaphore->CurrentTick();
            found = free_resource;
        }
    }

    // Free iterator is hinted to the resource after the one that's been commited.
    hint_iterator = (*found + 1) % ticks.size();
    return *found;
}

std::size_t ResourcePool::ManageOverflow() {
    const std::size_t old_capacity = ticks.size();
    ticks.resize(old_capacity + grow_step);
    Allocate(old_capacity, old_capacity + grow_step);
    return old_capacity;
}

constexpr std::size_t COMMAND_BUFFER_POOL_SIZE = 4;

CommandPool::CommandPool(const Instance& instance, Semaphore* work_semaphore)
    : ResourcePool{work_semaphore, COMMAND_BUFFER_POOL_SIZE}, instance{instance} {
    const vk::CommandPoolCreateInfo pool_create_info = {
        .flags = vk::CommandPoolCreateFlagBits::eTransient |
                 vk::CommandPoolCreateFlagBits::eResetCommandBuffer,
        .queueFamilyIndex = instance.GetGraphicsQueueFamilyIndex(),
    };
    const vk::Device device = instance.GetDevice();
    auto [pool_result, pool] = device.createCommandPoolUnique(pool_create_info);
    ASSERT_MSG(pool_result == vk::Result::eSuccess, "Failed to create command pool: {}",
               vk::to_string(pool_result));
    cmd_pool = std::move(pool);
    SetObjectName(device, *cmd_pool, "CommandPool");
}

CommandPool::~CommandPool() = default;

void CommandPool::Allocate(std::size_t begin, std::size_t end) {
    cmd_buffers.resize(end);

    const vk::CommandBufferAllocateInfo buffer_alloc_info = {
        .commandPool = *cmd_pool,
        .level = vk::CommandBufferLevel::ePrimary,
        .commandBufferCount = COMMAND_BUFFER_POOL_SIZE,
    };

    const vk::Device device = instance.GetDevice();
    const auto result =
        device.allocateCommandBuffers(&buffer_alloc_info, cmd_buffers.data() + begin);
    ASSERT(result == vk::Result::eSuccess);

    for (std::size_t i = begin; i < end; ++i) {
        SetObjectName(device, cmd_buffers[i], "CommandPool: Command Buffer {}", i);
    }
}

vk::CommandBuffer CommandPool::Commit() {
    const std::size_t index = CommitResource();
    return cmd_buffers[index];
}

DescriptorHeap::DescriptorHeap(const Instance& instance, Semaphore* work_semaphore_,
                               std::span<const vk::DescriptorPoolSize> pool_sizes_,
                               u32 descriptor_heap_count_)
    : device{instance.GetDevice()}, work_semaphore{work_semaphore_},
      descriptor_heap_count{descriptor_heap_count_}, pool_sizes{pool_sizes_} {
    CreateDescriptorPool();
}

DescriptorHeap::~DescriptorHeap() {
    device.destroyDescriptorPool(curr_pool);
    for (const auto& retired : pending_pools) {
        work_semaphore->Wait(retired.tick);
        device.destroyDescriptorPool(retired.pool);
    }
}

vk::DescriptorSet DescriptorHeap::Commit(
    vk::DescriptorSetLayout set_layout,
    std::span<const vk::WriteDescriptorSet> writes) {
    // Read the EXACT resource demand of this set from the writes constructed
    // for its Vulkan layout, not from the number of arrays or shader hashes.
    std::vector<vk::DescriptorPoolSize> required;
    for (const auto& write : writes) {
        if (write.descriptorCount == 0) {
            continue;
        }
        auto it = std::find_if(required.begin(), required.end(), [&](const auto& r) {
            return r.type == write.descriptorType;
        });
        if (it == required.end()) {
            required.push_back({write.descriptorType, write.descriptorCount});
        } else {
            const u64 total = u64(it->descriptorCount) + write.descriptorCount;
            ASSERT_MSG(total <= std::numeric_limits<u32>::max(),
                       "Descriptor set per-type requirement overflows Vulkan u32");
            it->descriptorCount = static_cast<u32>(total);
        }
    }

    const auto covers = [&](std::span<const vk::DescriptorPoolSize> capacity) {
        return PoolCoversDescriptorRequirements<vk::DescriptorPoolSize>(capacity, required);
    };
    const u64 set_key = std::bit_cast<u64>(set_layout);
    const auto retire_pool = [&] {
        // A descriptor set can outlive its CPU-side cache entry while a GPU
        // command is in flight. Retain the full pool until its timeline tick.
        pending_pools.push_back({curr_pool, work_semaphore->CurrentTick(),
                                 std::move(curr_pool_sizes), curr_pool_max_sets,
                                 curr_pool_flags});
        auto reusable = std::find_if(pending_pools.begin(), pending_pools.end(),
                                    [&](const PendingPool& candidate) {
            return work_semaphore->IsFree(candidate.tick) && covers(candidate.sizes);
        });
        if (reusable != pending_pools.end()) {
            curr_pool = reusable->pool;
            curr_pool_sizes = std::move(reusable->sizes);
            curr_pool_max_sets = reusable->max_sets;
            curr_pool_flags = reusable->flags;
            pending_pools.erase(reusable);
            const auto reset_result = device.resetDescriptorPool(curr_pool);
            ASSERT_MSG(reset_result == vk::Result::eSuccess,
                       "Unexpected error resetting descriptor pool: {}",
                       vk::to_string(reset_result));
        } else {
            CreateDescriptorPool(required);
        }
        descriptor_sets.clear();
    };

    // The base pool may fit one 827-image resource but NOT several 827-image
    // bindings in the same layout. Grow the pool only to measured requirements.
    if (!covers(curr_pool_sizes)) {
        for (const auto& need : required) {
            const auto it = std::find_if(curr_pool_sizes.begin(), curr_pool_sizes.end(),
                                         [&](const auto& p) { return p.type == need.type; });
            const u32 available = it == curr_pool_sizes.end() ? 0 : it->descriptorCount;
            if (need.descriptorCount > available) {
                LOG_WARNING(Render_Vulkan,
                            "Descriptor pool underprovisioned: type={} required={} pool={}",
                            vk::to_string(need.type), need.descriptorCount, available);
            }
        }
        retire_pool();
    }

    const auto cached = descriptor_sets.find(set_key);
    if (cached != descriptor_sets.end() && !cached->second.empty()) {
        const auto desc_set = cached->second.back();
        cached.value().pop_back();
        return desc_set;
    }

    DescSetBatch desc_sets;
    std::array<vk::DescriptorSetLayout, DescriptorSetBatch> layouts;
    layouts.fill(set_layout);
    vk::DescriptorSetAllocateInfo alloc_info = {
        .descriptorPool = curr_pool,
        .descriptorSetCount = DescriptorSetBatch,
        .pSetLayouts = layouts.data(),
    };
    const auto allocate = [&](u32 count) {
        desc_sets.resize(count);
        alloc_info.descriptorSetCount = count;
        return device.allocateDescriptorSets(&alloc_info, desc_sets.data());
    };
    vk::Result result = vk::Result::eErrorOutOfPoolMemory;
    const auto try_batch = [&] {
        return TryAllocateDescriptorBatch(
            allocate, result, vk::Result::eSuccess,
            vk::Result::eErrorOutOfPoolMemory, vk::Result::eErrorFragmentedPool);
    };
    if (try_batch() != 0) {
        const auto desc_set = desc_sets.back();
        desc_sets.pop_back();
        descriptor_sets[set_key] = std::move(desc_sets);
        return desc_set;
    }
    ASSERT_MSG(result == vk::Result::eErrorOutOfPoolMemory ||
                   result == vk::Result::eErrorFragmentedPool,
               "Unexpected error during descriptor set allocation: {}", vk::to_string(result));

    retire_pool();
    alloc_info.descriptorPool = curr_pool;
    if (try_batch() == 0) {
        ASSERT_MSG(result == vk::Result::eErrorOutOfPoolMemory ||
                       result == vk::Result::eErrorFragmentedPool,
                   "Unexpected Vulkan error allocating fresh descriptor pool: {}",
                   vk::to_string(result));
        // The observed RADV failure persists even though the driver reports
        // each requested descriptor type fitting within the fresh 8192-image
        // pool. This is NOT proof the guest needs more descriptors. Compare
        // one set allocated from a dedicated exact-layout pool, with maxSets=1
        // and no UPDATE_AFTER_BIND flag (all normal pipeline layouts here use
        // empty create flags; push descriptors never reach DescriptorHeap).
        // Never guess a shader index or fabricate a descriptor set.
        LOG_WARNING(Render_Vulkan,
                    "PR117_DESCRIPTOR_EXACT_SET_PROBE layout={:#x} previous_maxSets={} "
                    "previous_flags={} previous_error={}",
                    set_key, curr_pool_max_sets,
                    static_cast<u32>(static_cast<VkDescriptorPoolCreateFlags>(curr_pool_flags)),
                    vk::to_string(result));
        for (const auto& need : required) {
            const auto it = std::find_if(curr_pool_sizes.begin(), curr_pool_sizes.end(),
                                         [&](const auto& p) { return p.type == need.type; });
            LOG_WARNING(Render_Vulkan,
                        "PR117_DESCRIPTOR_PREVIOUS_POOL type={} required={} capacity={}",
                        vk::to_string(need.type), need.descriptorCount,
                        it == curr_pool_sizes.end() ? 0u : it->descriptorCount);
        }

        // Keep the old pool and any in-flight Vulkan sets until the GPU tick
        // completes; do not recycle a possibly unsuitable pool here.
        pending_pools.push_back({curr_pool, work_semaphore->CurrentTick(),
                                 std::move(curr_pool_sizes), curr_pool_max_sets,
                                 curr_pool_flags});
        CreateDescriptorPool(required, true);
        descriptor_sets.clear();
        alloc_info.descriptorPool = curr_pool;
        desc_sets.clear();
        desc_sets.resize(1);
        alloc_info.descriptorSetCount = 1;
        result = device.allocateDescriptorSets(&alloc_info, desc_sets.data());
        if (result != vk::Result::eSuccess) {
            for (const auto& need : required) {
                LOG_ERROR(Render_Vulkan,
                          "PR117_DESCRIPTOR_DEDICATED_POOL type={} required={} "
                          "maxSets={} flags={} result={}",
                          vk::to_string(need.type), need.descriptorCount,
                          curr_pool_max_sets,
                          static_cast<u32>(static_cast<VkDescriptorPoolCreateFlags>(curr_pool_flags)),
                          vk::to_string(result));
            }
            ASSERT_MSG(false,
                       "Exact-layout one-set Vulkan descriptor allocation failed: {}",
                       vk::to_string(result));
        }
        LOG_INFO(Render_Vulkan, "PR117_DESCRIPTOR_EXACT_SET_ALLOCATED layout={:#x}", set_key);
    }
    const auto desc_set = desc_sets.back();
    desc_sets.pop_back();
    descriptor_sets[set_key] = std::move(desc_sets);
    return desc_set;
}

void DescriptorHeap::CreateDescriptorPool(
    std::span<const vk::DescriptorPoolSize> minimum, bool dedicated_single_set) {
    // Only the exact fall-back owner uses one set and no UPDATE_AFTER_BIND
    // pool flag. Normal pipeline layouts have no update-after-bind bindings.
    // Keep the existing general-purpose pool policy unchanged elsewhere.
    const auto sizes = dedicated_single_set
        ? ExactDescriptorPoolSizes<vk::DescriptorPoolSize>(minimum)
        : ExpandDescriptorPoolSizes<vk::DescriptorPoolSize>(pool_sizes, minimum);
    const u32 max_sets = dedicated_single_set ? 1u : descriptor_heap_count;
    const vk::DescriptorPoolCreateFlags flags = dedicated_single_set
        ? vk::DescriptorPoolCreateFlags{}
        : vk::DescriptorPoolCreateFlagBits::eUpdateAfterBind;
    ASSERT_MSG(!dedicated_single_set || !sizes.empty(),
               "A dedicated Vulkan descriptor pool needs actual layout resource counts");
    const vk::DescriptorPoolCreateInfo pool_info = {
        .flags = flags,
        .maxSets = max_sets,
        .poolSizeCount = static_cast<u32>(sizes.size()),
        .pPoolSizes = sizes.data(),
    };
    auto [pool_result, pool] = device.createDescriptorPool(pool_info);
    ASSERT_MSG(pool_result == vk::Result::eSuccess, "Failed to create descriptor pool: {}",
               vk::to_string(pool_result));
    curr_pool = pool;
    curr_pool_sizes = sizes;
    curr_pool_max_sets = max_sets;
    curr_pool_flags = flags;
}

} // namespace Vulkan
