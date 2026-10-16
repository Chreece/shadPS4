// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>

namespace VideoCore {

// It is correct to coalesce mprotect over pages that already have the
// requested effective rights, but spanning a large *unchanged* gap needlessly
// re-protects pages and can force costly kernel TLB invalidation. Preserve
// batching across nearby changes, split after sixteen unchanged pages.
// Only kernel syscall grouping changes: GPU watch counts, page rights and
// readback semantics remain exactly the same on all supported platforms.
constexpr std::uint64_t MaxUnchangedProtectionGapPages = 16;

[[nodiscard]] constexpr bool ShouldFlushSparseProtectionRange(
    std::uint64_t protected_pages, std::uint64_t candidate_pages) noexcept {
    return protected_pages != 0 && candidate_pages > protected_pages &&
           candidate_pages - protected_pages > MaxUnchangedProtectionGapPages;
}

} // namespace VideoCore
