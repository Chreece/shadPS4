// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <span>

namespace AmdGpu::PM4 {

// Write consecutive SET_SH_REG payload words. The hardware register window
// contains both shared SH registers and a queue-local compute-program view.
// Packets can cross the beginning or end of that queue-local subrange.
// Validate the entire packet *before* modifying either destination.
inline bool WriteShaderRegisters(std::span<std::uint32_t> shared,
                                 std::span<std::uint32_t> queue_local,
                                 std::size_t shared_base,
                                 std::size_t queue_first,
                                 std::size_t first,
                                 std::span<const std::uint32_t> values) {
    if (shared_base > shared.size() || first > shared.size() - shared_base ||
        values.size() > shared.size() - shared_base - first) {
        return false;
    }

    std::size_t written = 0;
    while (written < values.size()) {
        const std::size_t offset = first + written;
        const bool local = offset >= queue_first &&
                           offset - queue_first < queue_local.size();
        if (local) {
            const std::size_t local_offset = offset - queue_first;
            const std::size_t count =
                std::min(values.size() - written, queue_local.size() - local_offset);
            std::copy_n(values.data() + written, count, queue_local.data() + local_offset);
            written += count;
        } else {
            const std::size_t count = offset < queue_first
                ? std::min(values.size() - written, queue_first - offset)
                : values.size() - written;
            std::copy_n(values.data() + written, count, shared.data() + shared_base + offset);
            written += count;
        }
    }
    return true;
}

} // namespace AmdGpu::PM4
