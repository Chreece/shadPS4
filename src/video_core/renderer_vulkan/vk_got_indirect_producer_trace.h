// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Host-only address predicates for opt-in provenance diagnostics. These
// addresses come from the eight actual Vulkan GPU argument readbacks in
// GoT CUSA11456 on 2026-10-19; they are NOT patches to guest data.
#include <array>
#include <bit>
#include <cstdint>
#include <cstdlib>
#include <limits>

namespace Vulkan::Diagnostics {

inline constexpr std::array<std::uint64_t, 8> GotIndirectWatchAddresses{
    0x1501330500ULL, 0x1501330520ULL,
    0x1546ec4000ULL, 0x1548c6fd00ULL,
    0x1500030540ULL, 0x1500030560ULL,
    0x1546ec4500ULL, 0x1548cc4a00ULL,
};
inline constexpr std::uint64_t GotIndirectCommandBytes = 20;

// Bit i identifies an intersecting 20-byte argument record. Avoid overflow
// and never widen the watch to a sparse arena or whole guest memory page.
[[nodiscard]] constexpr std::uint32_t GotIndirectProducerHits(
    std::uint64_t start, std::uint64_t length) noexcept {
    if (length == 0 || start > std::numeric_limits<std::uint64_t>::max() - length) {
        return 0;
    }
    std::uint32_t hits = 0;
    for (std::uint32_t i = 0; i < GotIndirectWatchAddresses.size(); ++i) {
        const auto address = GotIndirectWatchAddresses[i];
        if ((start <= address && address - start < length) ||
            (start > address && start - address < GotIndirectCommandBytes)) {
            hits |= std::uint32_t{1} << i;
        }
    }
    return hits;
}

[[nodiscard]] inline bool GotIndirectProducerTraceEnabled() noexcept {
    static const bool enabled = [] {
        const char* env = std::getenv("SHADPS4_GOT_INDIRECT_PRODUCERS");
        return env && env[0] == '1' && env[1] == '\0';
    }();
    return enabled;
}

[[nodiscard]] constexpr bool ShouldReportIndirectProducer(std::uint32_t n) noexcept {
    return n <= 16 || std::has_single_bit(n);
}

} // namespace Vulkan::Diagnostics
