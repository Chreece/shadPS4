// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>
#include <limits>

namespace AmdGpu::BufferSize {

// An AMD GCN buffer descriptor has a 14-bit STRIDE and a 32-bit
// NUM_RECORDS. For a nonzero STRIDE the byte extent can be much larger
// than 4 GiB; never let a u32 API silently wrap it to a smaller range.
// For zero STRIDE, NUM_RECORDS is already a byte count.
constexpr std::uint64_t DeclaredBytes(std::uint64_t stride,
                                      std::uint32_t num_records) noexcept {
    return stride == 0 ? static_cast<std::uint64_t>(num_records)
                       : stride * static_cast<std::uint64_t>(num_records);
}

constexpr bool FitsU32(std::uint64_t mapped_bytes) noexcept {
    return mapped_bytes <= std::numeric_limits<std::uint32_t>::max();
}

static_assert(DeclaredBytes(20, 0xffffffffu) == 85899345900ULL);
static_assert(DeclaredBytes(0, 0xffffffffu) == 4294967295ULL);
static_assert(!FitsU32(DeclaredBytes(20, 0xffffffffu)));

} // namespace AmdGpu::BufferSize
