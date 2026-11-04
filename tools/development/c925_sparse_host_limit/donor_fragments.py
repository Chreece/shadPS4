# Reviewed method bodies from PR109 pinned patch, for local test reproduction.
PLAN = r'''// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cstdint>
#include <optional>

namespace VideoCore::SparseArena {
struct Window {
    std::uint64_t address{};
    std::uint64_t size{};

    constexpr bool Contains(std::uint64_t start, std::uint64_t bytes) const noexcept {
        return start >= address && start - address <= size && bytes <= size - (start - address);
    }
};

// Plan an aligned host view, not a truncation of the requested guest range.
// Sparse blocks at an old 4-GiB boundary may be shared by overlapping views.
[[nodiscard]] constexpr std::optional<Window> Plan(
    std::uint64_t first_block, std::uint64_t last_block, std::uint32_t block_shift,
    std::uint64_t max_buffer_size, std::uint64_t address_space_size,
    std::uint64_t preferred_size) noexcept {
    if (block_shift >= 63 || address_space_size == 0 || preferred_size == 0 ||
        (preferred_size & (preferred_size - 1)) != 0) {
        return std::nullopt;
    }
    const auto block_size = std::uint64_t{1} << block_shift;
    if ((address_space_size & (block_size - 1)) != 0 || preferred_size < block_size ||
        first_block > last_block || last_block >= (address_space_size >> block_shift)) {
        return std::nullopt;
    }
    const auto limit = std::min(max_buffer_size, address_space_size) & ~(block_size - 1);
    const auto start = first_block << block_shift;
    const auto end = (last_block + 1) << block_shift;
    const auto required = end - start;
    if (required > limit) {
        return std::nullopt;
    }
    const auto size = std::min(limit, std::max(required, preferred_size));
    auto base = start & ~(preferred_size - 1);
    if (end - base > size) {
        base = end - size;
    }
    if (size > address_space_size - base) {
        base = address_space_size - size;
    }
    return Window{base, size};
}
} // namespace VideoCore::SparseArena
'''
BIND = r'''void BufferCache::BindBackingToArena(const Buffer* arena, const Backing& backing) {
    const u64 arena_first = arena->cpu_addr >> block_shift;
    const u64 arena_end = (arena->cpu_addr + arena->size_bytes) >> block_shift;
    const u64 first = std::max(arena_first, backing.start);
    const u64 end = std::min(arena_end, backing.end);
    if (first >= end) {
        return;
    }
    BindsForArena(arena)->binds.push_back(vk::SparseMemoryBind{
        .resourceOffset = (first - arena_first) << block_shift,
        .size = (end - first) << block_shift,
        .memory = backing.memory,
        .memoryOffset = (backing.offset + first - backing.start) << block_shift,
    });
}'''
SYNC = r'''void BufferCache::SynchronizeSparseAliases() {
    if (!has_sparse_aliases) {
        return;
    }
    // The access tracker is keyed by VkBuffer. Overlapping sparse views alias
    // the same backing, so make prior accesses visible across buffer identities.
    // This is a command-buffer dependency, not a host/device-idle wait.
    scheduler.EndRendering();
    const vk::MemoryBarrier2 barrier{
        .srcStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .srcAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eAllCommands,
        .dstAccessMask = vk::AccessFlagBits2::eMemoryRead | vk::AccessFlagBits2::eMemoryWrite,
    };
    scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
        .memoryBarrierCount = 1,
        .pMemoryBarriers = &barrier,
    });
}'''
GET = r'''const Buffer* BufferCache::GetArena(u64 first_block, u64 last_block) {
    const auto window = SparseArena::Plan(first_block, last_block, block_shift, max_buffer_size,
                                         u64{1} << ADDRESS_SPACE_BITS, ARENA_PAGE_SIZE);
    ASSERT_MSG(window.has_value(),
               "Sparse request blocks {}..{} cannot fit host buffer limit {} (block shift {})",
               first_block, last_block, max_buffer_size, block_shift);
    const u64 address = first_block << block_shift;
    const u64 size = (last_block - first_block + 1) << block_shift;
    const u64 first_page = address >> ARENA_PAGE_BITS;
    const auto contains = [address, size](const Buffer* arena) {
        return arena && SparseArena::Window{arena->cpu_addr, arena->size_bytes}.Contains(address,
                                                                                       size);
    };
    if (const auto* hint = address_space[first_page]; contains(hint)) {
        SynchronizeSparseAliases();
        return hint;
    }
    for (const auto& arena : arenas) {
        if (contains(&arena)) {
            address_space[first_page] = &arena;
            SynchronizeSparseAliases();
            return &arena;
        }
    }

    const u64 window_end = window->address + window->size;
    const bool overlaps = std::ranges::any_of(arenas, [&](const Buffer& arena) {
        return window->address < arena.cpu_addr + arena.size_bytes &&
               arena.cpu_addr < window_end;
    });
    ASSERT_MSG(!overlaps || instance.IsSparseResidencyAliasedSupported(),
               "Overlapping guest buffer views require sparseResidencyAliased on this host");
    const auto* arena =
        &arenas.emplace_back(instance, window->address, window->size, MemoryType::Sparse);
    has_sparse_aliases |= overlaps;
    // Existing pages and all later allocations are bound to every overlapping
    // view. No guest bytes are removed, copied to a substitute, or zero-filled.
    resident_ranges.ForEachInRange(window->address >> block_shift, window_end >> block_shift,
                                   [&](const Backing& backing) { BindBackingToArena(arena, backing); });
    for (u64 page = window->address >> ARENA_PAGE_BITS;
         page <= (window_end - 1) >> ARENA_PAGE_BITS; ++page) {
        address_space[page] = arena;
    }
    LOG_INFO(Render_Vulkan,
             "SparseArenaWindow: guest={:#x} bytes={} host_base={:#x} host_bytes={} max={} alias={}",
             address, size, window->address, window->size, max_buffer_size, overlaps);
    SynchronizeSparseAliases();
    return arena;
}'''
