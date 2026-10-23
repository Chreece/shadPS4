// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <cstdint>
namespace Shader::Optimization {
// An indexed Vulkan array must contain ALL real PS4 T# entries and one null
// entry. A partial table is not a valid emulation of a dynamic GPU index.
// A raw (stride=0) V# measures bytes; stride=32 measures T# records.
[[nodiscard]] constexpr std::uint32_t CompleteGuestTsharpTableEntries(
    std::uint32_t stride, std::uint32_t records,
    std::uint32_t max_entries) noexcept {
    if (stride!=0 && stride!=32) return 0;
    const std::uint64_t bytes=stride==0
        ? std::uint64_t(records)
        : std::uint64_t(stride)*records;
    if (bytes<32 || bytes%32 || bytes/32>max_entries) return 0;
    return static_cast<std::uint32_t>(bytes/32);
}
// PS4 material tables may embed a 32-byte T# inside a larger record.
// Require an exact complete table: a partial/prefix mapping would make a
// GPU texture index silently sample the wrong guest material.
[[nodiscard]] constexpr std::uint32_t CompleteGuestTsharpRecordEntries(
    std::uint32_t vsharp_stride, std::uint32_t records,
    std::uint32_t record_stride_bytes, std::uint32_t descriptor_offset_bytes,
    std::uint32_t max_entries) noexcept {
    if (record_stride_bytes < 32u || record_stride_bytes % 4u ||
        descriptor_offset_bytes % 4u ||
        std::uint64_t(descriptor_offset_bytes) + 32u > record_stride_bytes ||
        (vsharp_stride != 0 && vsharp_stride != record_stride_bytes)) return 0;
    const std::uint64_t full_bytes = vsharp_stride == 0
        ? std::uint64_t(records)
        : std::uint64_t(vsharp_stride) * records;
    if (full_bytes < record_stride_bytes || full_bytes % record_stride_bytes) return 0;
    const std::uint64_t entries = full_bytes / record_stride_bytes;
    if (entries == 0 || entries > max_entries) return 0;
    return static_cast<std::uint32_t>(entries);
}
[[nodiscard]] constexpr std::uint32_t GuestTsharpIndexOrNull(
    std::uint32_t gpu_index, std::uint32_t valid_entries) noexcept {
    return gpu_index<valid_entries ? gpu_index : valid_entries;
}
} // namespace Shader::Optimization
