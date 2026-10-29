// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>
#include <stdexcept>
#include <utility>
#include <vector>

// Physical guest GDS byte-range safety. CounterKey -> byte offset is NOT
// decoded here; the real GCN/PM4 mapping must be independently proven before
// a host executor can use the values as physical counter locations.
namespace Shader::Gcn::OrderedGds {

inline constexpr std::uint32_t GdsPhysicalBytes = 64u * 1024u;
inline constexpr std::uint32_t GdsDwordBytes = sizeof(std::uint32_t);
inline constexpr std::uint32_t GdsPhysicalDwords = GdsPhysicalBytes / GdsDwordBytes;
using PhysicalGdsDwordWrite = std::pair<std::uint32_t, std::uint32_t>; // Byte offset, value.

inline void ValidatePhysicalGdsWrites(std::span<const PhysicalGdsDwordWrite> writes) {
    if (writes.size() > GdsPhysicalDwords) {
        throw std::invalid_argument("too many 32-bit guest GDS updates");
    }
    std::vector<std::uint32_t> seen;
    seen.reserve(writes.size());
    for (const auto& [byte_offset, value] : writes) {
        (void)value;
        if (byte_offset % GdsDwordBytes ||
            byte_offset > GdsPhysicalBytes - GdsDwordBytes) {
            throw std::invalid_argument("guest GDS counter offset unaligned or out of range");
        }
        seen.push_back(byte_offset);
    }
    std::sort(seen.begin(), seen.end());
    if (std::adjacent_find(seen.begin(), seen.end()) != seen.end()) {
        throw std::invalid_argument("guest GDS physical counter written twice in one phase");
    }
}

} // namespace Shader::Gcn::OrderedGds
