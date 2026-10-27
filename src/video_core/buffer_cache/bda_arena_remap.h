// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <cstddef>
#include <cstdint>
#include <utility>

namespace VideoCore {

// A sparse VkBuffer's device-address base changes when an arena is replaced
// during merging. Previously resident guest blocks continue using the same
// VkDeviceMemory, but the shader-visible BDA page table must point at the new
// VkBuffer's virtual address, not at the old arena's base.
//
// Iterate the exact resident block interval and let the caller write entries
// and issue one batched Vulkan transfer for that interval. Holes remain unset.
template <class Emit>
constexpr void ForEachReboundBdaBlock(std::uint64_t arena_guest_base,
                                     std::uint64_t new_arena_device_address,
                                     std::uint64_t block_size,
                                     std::uint64_t first_block,
                                     std::uint64_t end_block,
                                     Emit&& emit) {
    for (std::uint64_t block = first_block; block < end_block; ++block) {
        const std::uint64_t guest_byte_address = block * block_size;
        const std::uint64_t offset_into_arena = guest_byte_address - arena_guest_base;
        emit(block, new_arena_device_address + offset_into_arena);
    }
}

[[nodiscard]] constexpr std::uint64_t BdaTableByteOffset(std::uint64_t global_block) {
    return global_block * sizeof(std::uint64_t);
}

} // namespace VideoCore
