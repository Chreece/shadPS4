// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>
#include <optional>

namespace VideoCore {

// userfaultfd's uffdio_range consists of [start, start+len). A VM interval
// from boost::icl supplies [lower, upper); upper is NOT a length.
// Reject invalid/unaligned spans rather than register outside the mapping.
struct UffdRange {
    std::uint64_t start;
    std::uint64_t len;
};

[[nodiscard]] constexpr std::optional<UffdRange> UffdRegistrationRange(
    std::uint64_t lower, std::uint64_t upper, std::uint64_t page_size) noexcept {
    if (!page_size || (page_size & (page_size - 1)) != 0 ||
        upper <= lower || (lower & (page_size - 1)) != 0 ||
        (upper & (page_size - 1)) != 0) {
        return std::nullopt;
    }
    return UffdRange{lower, upper - lower};
}

[[nodiscard]] constexpr std::optional<UffdRange> UffdFaultWakeRange(
    std::uint64_t address, std::uint64_t page_size) noexcept {
    if (!page_size || (page_size & (page_size - 1)) != 0) {
        return std::nullopt;
    }
    return UffdRange{address & ~(page_size - 1), page_size};
}

} // namespace VideoCore
