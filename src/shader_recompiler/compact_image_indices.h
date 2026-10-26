// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <cstdint>
#include <span>

namespace Shader::CompactImageIndices {

// A sparse guest T# record ID is never replaced by a CPU-selected value.
// Only a GPU-readable table maps the real selected ID to its typed ordinal.
// The last descriptor of each typed array is reserved as its null sentinel.
inline constexpr std::uint32_t Invalid = 0xFFFFFFFFu;

constexpr bool Assign(std::span<std::uint32_t> guest_to_slot, std::uint32_t guest_id,
                      std::uint32_t capacity, std::uint32_t& next_slot) noexcept {
    if (guest_id >= guest_to_slot.size() || next_slot >= capacity) {
        return false;
    }
    guest_to_slot[guest_id] = next_slot++;
    return true;
}

constexpr std::uint32_t Bounded(std::uint32_t mapped, std::uint32_t capacity) noexcept {
    return mapped < capacity ? mapped : capacity;
}

static_assert(Bounded(Invalid, 256u) == 256u);
static_assert(Bounded(Invalid, 128u) == 128u);
static_assert(Bounded(33u, 256u) == 33u);
static_assert(Bounded(257u, 256u) == 256u);

} // namespace Shader::CompactImageIndices
