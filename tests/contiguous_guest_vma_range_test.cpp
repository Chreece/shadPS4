// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Test real production iterator algorithm on mapped/free/reserved VMA layouts.
#include <cstdint>
#include <iostream>
#include <map>
#include <utility>
#include "core/contiguous_mapped_range.h"

struct VMA {
    std::uint64_t base{}, size{};
    bool mapped{};
    [[nodiscard]] bool IsMapped() const noexcept { return mapped; }
};
using Map = std::map<std::uint64_t, VMA>;

static std::uint64_t Clamp(const Map& areas, std::uint64_t address,
                           std::uint64_t requested_bytes) {
    if (areas.empty() || address < areas.begin()->first) return 0;
    auto it = areas.upper_bound(address);
    if (it == areas.begin()) return 0;
    --it;
    return Core::MemoryRange::ClampContiguousMappedBytes(
        address, requested_bytes, it, areas.end());
}

int main() {
    // Two actually adjacent mappings are safely treated as one byte range.
    const Map adjacent{{0x1000, {0x1000, 0x1000, true}},
                       {0x2000, {0x2000, 0x1800, true}},
                       {0x3800, {0x3800, 0x1000, true}}};
    if (Clamp(adjacent, 0x1800, 0x3000) != 0x3000) return 1;
    if (Clamp(adjacent, 0x1800, 0x5000) != 0x3800) return 2;
    if (Clamp(adjacent, 0x1800, 0x100) != 0x100) return 3;

    // A mapped VMA after an unmapped gap must NOT extend the first range.
    const Map gap{{0x1000, {0x1000, 0x1000, true}},
                  {0x5000, {0x5000, 0x2000, true}}};
    if (Clamp(gap, 0x1800, 0x5000) != 0x800) return 4;

    // A contiguous reserved/free span also terminates GPU-accessible bytes.
    const Map free_between{{0x1000, {0x1000, 0x1000, true}},
                           {0x2000, {0x2000, 0x1000, false}},
                           {0x3000, {0x3000, 0x1000, true}}};
    if (Clamp(free_between, 0x1000, 0x3000) != 0x1000) return 5;
    if (Clamp(free_between, 0x2000, 0x3000) != 0) return 6;

    // Exact observed guest raw V# base and accessible mapped byte count.
    constexpr std::uint64_t declared = 85899345900ULL;
    const Map observed{{0x150341af60ULL, {0x150341af60ULL, 1402884256ULL, true}},
                       {0x1600000000ULL, {0x1600000000ULL, 8192ULL, true}}};
    if (Clamp(observed, 0x150341af60ULL, declared) != 1402884256ULL) return 7;
    if (Clamp(observed, 0x150341af60ULL, 0) != 0) return 8;

    // Large input must not wrap to 32-bit when clamping real guest memory.
    const Map large{{0x800000000ULL, {0x800000000ULL, 5ULL << 30, true}}};
    if (Clamp(large, 0x800000000ULL, declared) != (5ULL << 30)) return 9;
    if (Clamp(large, 0x800000000ULL, 7) != 7) return 10;

    std::cout << "CONTIGUOUS_GUEST_VMA_CLAMP=PASS\n";
}
