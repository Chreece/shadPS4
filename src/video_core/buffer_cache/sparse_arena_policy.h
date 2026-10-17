// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <algorithm>
#include <bit>
#include <cstdint>

namespace VideoCore {

struct SparseArenaWindow {
    std::uint64_t base{};
    std::uint64_t size{};
};

// Vulkan 1.3 / maintenance4 maxBufferSize is the hard limit for VkBuffer,
// independent of the size of the emulated guest virtual address space.
[[nodiscard]] constexpr std::uint64_t SparseArenaPageSize(
    std::uint64_t max_buffer_size, std::uint64_t max_window = (std::uint64_t{1} << 32)) {
    return std::bit_floor(std::min(max_buffer_size, max_window));
}

// Place a contiguous, block-aligned guest request inside a single VkBuffer
// that never exceeds the host maxBufferSize. Near page boundaries the window
// may overlap previously allocated sparse buffers; only devices with sparse
// residency aliasing support can share backing between those windows.
[[nodiscard]] constexpr SparseArenaWindow ChooseSparseArenaWindow(
    std::uint64_t first, std::uint64_t requested, std::uint64_t page_size,
    std::uint64_t max_buffer_size, std::uint64_t block_size,
    std::uint64_t guest_address_space) {
    if (block_size == 0 || page_size < block_size || requested == 0 ||
        requested > max_buffer_size || requested > guest_address_space ||
        first > guest_address_space - requested) {
        return {};
    }

    const std::uint64_t size = std::max(page_size, requested);
    if (size > max_buffer_size || size % block_size != 0) {
        return {};
    }

    std::uint64_t base = (first / page_size) * page_size;
    if (first + requested > base + size) {
        const std::uint64_t slack = (size - requested) / 2;
        const std::uint64_t wanted = first - std::min(first, slack);
        base = (wanted / block_size) * block_size;
    }
    if (base > guest_address_space - size) {
        base = ((guest_address_space - size) / block_size) * block_size;
    }
    if (first < base || first + requested > base + size) {
        return {};
    }
    return {base, size};
}
static_assert(SparseArenaPageSize((std::uint64_t{1} << 32) - 4) ==
              (std::uint64_t{1} << 31));
static_assert(SparseArenaPageSize(std::uint64_t{1} << 32) ==
              (std::uint64_t{1} << 32));
static_assert(ChooseSparseArenaWindow(0x7ffffff0, 0x10000, 0x80000000,
              0xfffffffc, 0x10000, std::uint64_t{1} << 40).size == 0x80000000);

} // namespace VideoCore
