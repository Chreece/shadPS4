// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cstdint>

namespace Core::MemoryRange {

// Clamp a guest-visible byte range to mapped, *address-contiguous* VMAs.
// A std::map iterator may advance to a mapped region with a hole in between.
// In that case its bytes do NOT extend the original GPU-accessible range.
// This is independent of guest V# width, memory type and shader/game identity.
template <typename Iterator>
[[nodiscard]] constexpr std::uint64_t ClampContiguousMappedBytes(
    std::uint64_t guest_address, std::uint64_t requested_bytes,
    Iterator vma, Iterator vma_end) noexcept {
    if (requested_bytes == 0 || vma == vma_end) {
        return 0;
    }
    const auto& first = vma->second;
    if (!first.IsMapped() || guest_address < first.base ||
        guest_address - first.base >= first.size) {
        return 0;
    }

    std::uint64_t contiguous_end = first.base + first.size;
    std::uint64_t mapped_bytes =
        std::min(requested_bytes, contiguous_end - guest_address);
    while (mapped_bytes < requested_bytes && ++vma != vma_end) {
        const auto& next = vma->second;
        if (!next.IsMapped() || next.base != contiguous_end) {
            break;
        }
        const auto needed = requested_bytes - mapped_bytes;
        mapped_bytes += std::min(needed, next.size);
        contiguous_end = next.base + next.size;
    }
    return mapped_bytes;
}

} // namespace Core::MemoryRange
