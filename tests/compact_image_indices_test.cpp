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
    // Check that the three Vulkan descriptor array regions are disjoint,
    // that ordinary 2D uses its unchanged full guest index, and that the
    // compact typed arrays consume only the genuine selected source slot.
    constexpr std::uint32_t Image2DBindings = GuestEntries + 1u;
    constexpr std::uint32_t Image3DBindings = VolumeCap + 1u;
    constexpr std::uint32_t ImageCubeBindings = CubeCap + 1u;
    constexpr std::uint32_t Offset3D = Image2DBindings;
    constexpr std::uint32_t OffsetCube = Offset3D + Image3DBindings;
    constexpr std::uint32_t TotalImageBindings = OffsetCube + ImageCubeBindings;
    if (Image2DBindings != 27652u || TotalImageBindings != 28038u) return 13;
    for (std::uint32_t i = 0; i < 35u; ++i) {
        const std::uint32_t guest_id = 1u + 13u * i;
        const std::uint32_t remapped_slot = Offset3D + Bounded(map[guest_id], VolumeCap);
        if (remapped_slot != Offset3D + i || remapped_slot < Image2DBindings ||
            remapped_slot >= OffsetCube) return 14;
    }
    for (std::uint32_t i = 0; i < 4u; ++i) {
        const std::uint32_t guest_id = 10001u + 11u * i;
        const std::uint32_t remapped_slot = OffsetCube + Bounded(map[guest_id], CubeCap);
        if (remapped_slot != OffsetCube + i || remapped_slot < OffsetCube ||
            remapped_slot >= TotalImageBindings) return 15;
    }
    // Invalid type entries can only name each type's final null descriptor.
    if (Offset3D + Bounded(Invalid, VolumeCap) != Offset3D + VolumeCap) return 16;
    if (OffsetCube + Bounded(Invalid, CubeCap) != OffsetCube + CubeCap) return 17;
    return 0;
}
