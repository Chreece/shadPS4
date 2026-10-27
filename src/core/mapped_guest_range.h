// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cstdint>

namespace Core {

// Clamp a guest GPU/CPU buffer span to real, adjacent, mapped VMAs.
// A map's next iterator is not proof that its addresses are contiguous:
// the PS4 address space is assembled from several separate usable regions.
// This common helper accepts the production VMA iterator and a test map.
template <typename It>
[[nodiscard]] std::uint64_t ClampContiguousMappedRange(std::uint64_t address,
                                                        std::uint64_t requested_size,
                                                        It current, It end) {
    if (requested_size == 0 || current == end) {
        return 0;
    }
    const auto& first = current->second;
    if (!first.IsMapped() || address < first.base ||
        address - first.base >= first.size) {
        return 0;
    }

    std::uint64_t available =
        std::min(requested_size, first.size - (address - first.base));
    auto previous = current;
    while (available < requested_size && ++current != end) {
        const auto& before = previous->second;
        const auto& next = current->second;
        // Subtraction avoids address overflow near the top of the guest range.
        if (!next.IsMapped() || next.base < before.base ||
            next.base - before.base != before.size) {
            break;
        }
        available += std::min(requested_size - available, next.size);
        previous = current;
    }
    return available;
}

} // namespace Core
