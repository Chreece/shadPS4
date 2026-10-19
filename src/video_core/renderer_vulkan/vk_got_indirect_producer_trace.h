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

// The observed GDS DMA sources live at 0x980..0x9ff, but the guest
// destination addresses moved between runs. Select bounded address
// families, never the stale eight literal pointer values.
enum class GotDmaWordCandidate : std::uint8_t {
    None,
    GdsIndexCountCandidate,
    GuestInstanceCountCandidate,
};

[[nodiscard]] constexpr GotDmaWordCandidate SelectGotDmaWordCandidate(
    std::uint64_t source, std::uint64_t destination, std::uint32_t bytes,
    bool src_gds, bool dst_gds) noexcept {
    if (bytes != 4 || dst_gds) return GotDmaWordCandidate::None;
    if (src_gds) {
        if (source >= 0x980 && source < 0xa00 &&
            destination >= 0x1500000000ULL &&
            destination < 0x1502000000ULL) {
            return GotDmaWordCandidate::GdsIndexCountCandidate;
        }
    } else if ((destination & 31ULL) == 4 &&
               ((destination >= 0x1546000000ULL &&
                 destination < 0x1547000000ULL) ||
                (destination >= 0x1548000000ULL &&
                 destination < 0x1549000000ULL))) {
        return GotDmaWordCandidate::GuestInstanceCountCandidate;
    }
    return GotDmaWordCandidate::None;
}

[[nodiscard]] constexpr bool ShouldSampleGotDmaWord(
    std::uint32_t count) noexcept {
    return count != 0 && (count <= 8 ||
            (count <= 2048 && std::has_single_bit(count)));
}

[[nodiscard]] inline bool GotDmaWordProbeEnabled() noexcept {
    static const bool enabled = [] {
        const char* env = std::getenv("SHADPS4_GOT_DMA_WORD_PROBE");
        return env && env[0] == '1' && env[1] == '\0';
    }();
    return enabled;
}

// Limit synchronous capture traffic: eight initial GDS writer dispatches,
// then powers of two up to 16384. No shader-side mutation is performed.
[[nodiscard]] constexpr bool ShouldSampleGotGdsProducer(
    std::uint32_t n) noexcept {
    return n > 0 && (n <= 8 || (n <= 16384 && std::has_single_bit(n)));
}

[[nodiscard]] constexpr bool ShouldReportIndirectProducer(std::uint32_t n) noexcept {
    return n <= 16 || std::has_single_bit(n);
}

} // namespace Vulkan::Diagnostics
