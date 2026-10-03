// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cstdint>
#include <limits>

namespace Core::Detail {
// The caller holds the mapping lock. Kept independent of MemoryManager startup
// so range/permission boundaries can be tested without reserving guest RAM.
template <typename Areas>
bool IsAccessibleRange(const Areas& areas, uintptr_t address, uint64_t size, uint32_t required) {
    if (!address || !size || size > std::numeric_limits<uintptr_t>::max() - address ||
        areas.empty()) {
        return false;
    }
    auto it = areas.upper_bound(address);
    if (it == areas.begin()) {
        return false;
    }
    --it;
    while (size) {
        if (it == areas.end()) {
            return false;
        }
        const auto& area = it->second;
        if (address < area.base || address - area.base >= area.size || !area.IsMapped() ||
            (static_cast<uint32_t>(area.prot) & required) != required) {
            return false;
        }
        const uint64_t part = std::min(size, area.size - (address - area.base));
        size -= part;
        address += part;
        ++it;
    }
    return true;
}
} // namespace Core::Detail
