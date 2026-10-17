// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>

namespace Shader::Optimization {

// One 32-bit bitset word covers 32 *guest* texture indices. The high
// sentinel bit records all indices beyond the supported trace window.
// The bitset does not substitute a different texture for any guest index.
inline constexpr std::uint32_t GuestTsharpFeedbackOverflowId = 32768u;
inline constexpr std::uint32_t GuestTsharpFeedbackWordCount =
    (GuestTsharpFeedbackOverflowId + 32u) / 32u;
inline constexpr std::uint32_t GuestTsharpFeedbackBytes =
    GuestTsharpFeedbackWordCount * sizeof(std::uint32_t);

[[nodiscard]] constexpr std::uint32_t GuestTsharpFeedbackClampedId(
    std::uint32_t gpu_id) noexcept {
    return gpu_id < GuestTsharpFeedbackOverflowId
        ? gpu_id : GuestTsharpFeedbackOverflowId;
}
[[nodiscard]] constexpr std::uint32_t GuestTsharpFeedbackWordIndex(
    std::uint32_t gpu_id) noexcept {
    return GuestTsharpFeedbackClampedId(gpu_id) >> 5u;
}
[[nodiscard]] constexpr std::uint32_t GuestTsharpFeedbackWordBit(
    std::uint32_t gpu_id) noexcept {
    return 1u << (GuestTsharpFeedbackClampedId(gpu_id) & 31u);
}
} // namespace Shader::Optimization
