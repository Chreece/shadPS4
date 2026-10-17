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
[[nodiscard]] constexpr std::uint32_t GuestTsharpIndexOrNull(
    std::uint32_t gpu_index, std::uint32_t valid_entries) noexcept {
    return gpu_index<valid_entries ? gpu_index : valid_entries;
}
} // namespace Shader::Optimization
