// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
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
