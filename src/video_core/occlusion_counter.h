// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <cstdint>

namespace VideoCore::OcclusionCounter {
constexpr std::uint64_t Valid = std::uint64_t{1} << 63;
constexpr std::uint64_t Mask = ~Valid;

constexpr std::uint64_t Value(std::uint64_t total, unsigned pipe, unsigned pipes) {
    // Vulkan returns a device-wide count. Partition it so summing guest pipes
    // returns that count, instead of multiplying it by eight or sixteen.
    return Valid | (total / pipes + (pipe < total % pipes));
}

constexpr std::uint64_t WriteSpan(unsigned pipes) {
    // A dump writes every other qword; the final paired qword is untouched.
    return pipes == 0 ? 0 : (pipes - 1) * 16ULL + 8;
}
} // namespace VideoCore::OcclusionCounter
