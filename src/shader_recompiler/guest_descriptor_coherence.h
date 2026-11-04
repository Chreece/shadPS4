// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>
#include <limits>
#include <optional>

namespace Shader::Optimization {

// Exact minimum guest-table prefix needed to read each represented T# or S#.
// Unlike the V# declared length, this bounds coherence to the descriptors
// we will actually inspect, preserving mapping checks and sparse residency.
constexpr std::optional<std::uint64_t> GuestDescriptorPrefixBytes(
    std::uint64_t entries, std::uint64_t record_stride, std::uint64_t descriptor_offset,
    std::uint64_t descriptor_size) noexcept {
    if (entries == 0)
        return std::uint64_t{0};
    if (record_stride == 0 || descriptor_size == 0 || descriptor_offset > record_stride ||
        descriptor_size > record_stride - descriptor_offset)
        return std::nullopt;
    const auto tail_bytes = descriptor_offset + descriptor_size;
    const auto prefix_records = entries - 1;
    if (prefix_records > (std::numeric_limits<std::uint64_t>::max() - tail_bytes) / record_stride)
        return std::nullopt;
    return prefix_records * record_stride + tail_bytes;
}

} // namespace Shader::Optimization
