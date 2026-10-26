// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Standalone test for real GPU guest image-index mapping. Deliberately builds
// without Vulkan, GPU, display, user saves, shaders or a game installation.
// Run in Release/-DNDEBUG as well: mapping must never rely on ASSERT side effects.
#include <cstdint>
#include <vector>
#include "shader_recompiler/compact_image_indices.h"

int main() {
    using Shader::CompactImageIndices::Assign;
    using Shader::CompactImageIndices::Bounded;
    using Shader::CompactImageIndices::Invalid;
    constexpr std::uint32_t GuestEntries = 27651u;
    constexpr std::uint32_t VolumeCap = 256u;
    constexpr std::uint32_t CubeCap = 128u;
    std::vector<std::uint32_t> map(GuestEntries + 1u, Invalid);
    std::uint32_t volume = 0, cube = 0;
    for (std::uint32_t n = 0; n < 35u; ++n) {
        if (!Assign(map, n * 13u + 1u, VolumeCap, volume)) return 1;
    }
    for (std::uint32_t n = 0; n < 4u; ++n) {
        if (!Assign(map, 10001u + 11u * n, CubeCap, cube)) return 2;
    }
    if (volume != 35u || cube != 4u) return 3;
    for (std::uint32_t n = 0; n < 35u; ++n) {
        if (map[n * 13u + 1u] != n) return 4;
    }
    for (std::uint32_t n = 0; n < 4u; ++n) {
        if (map[10001u + 11u * n] != n) return 5;
    }
    if (Bounded(map[GuestEntries], VolumeCap) != VolumeCap) return 6;
    if (Bounded(map[12345u], CubeCap) != CubeCap) return 7;
    if (Bounded(map[1u + 13u * 34u], VolumeCap) != 34u) return 8;
    if (Bounded(map[10001u + 11u * 3u], CubeCap) != 3u) return 9;
    std::uint32_t over = VolumeCap;
    if (Assign(map, 0u, VolumeCap, over)) return 10;
    if (Assign(map, GuestEntries + 1u, VolumeCap, over)) return 11;
    if (over != VolumeCap || map[0] != Invalid) return 12;
    return 0;
}
