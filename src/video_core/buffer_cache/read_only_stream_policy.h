// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdint>

namespace VideoCore {

// The 64-KiB read-only stream path previously progressed GoW compute shader
// workloads. Keep the legacy 16-KiB default while testing this general Vulkan
// buffer path on a modern graphics-correct baseline. No game-ID conditions.
struct ReadOnlyStreamPolicy {
    static constexpr std::uint32_t LegacyThreshold = 16u * 1024u;
    static constexpr std::uint32_t ExtendedThreshold = 64u * 1024u;

    static constexpr std::uint32_t Limit(bool extended) noexcept {
        return extended ? ExtendedThreshold : LegacyThreshold;
    }

    // The host-backed stream buffer must never handle a GPU-writable resource
    // or a caller explicitly requesting the original sparse buffer.
    static constexpr bool IsCandidate(std::uint32_t size, bool is_written,
                                      bool ignore_stream, std::uint32_t threshold) noexcept {
        return size > 0 && size <= threshold && !is_written && !ignore_stream;
    }
};
static_assert(ReadOnlyStreamPolicy::Limit(false) == 16u * 1024u);
static_assert(ReadOnlyStreamPolicy::Limit(true) == 64u * 1024u);
static_assert(ReadOnlyStreamPolicy::IsCandidate(64u * 1024u, false, false,
                                                ReadOnlyStreamPolicy::ExtendedThreshold));
static_assert(!ReadOnlyStreamPolicy::IsCandidate(64u * 1024u, true, false,
                                                 ReadOnlyStreamPolicy::ExtendedThreshold));
} // namespace VideoCore
