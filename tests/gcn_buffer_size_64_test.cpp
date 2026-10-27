// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Real production-header regression: test unsigned GCN STRIDE * NUM_RECORDS,
// specifically the previously observed 0xFFFFFFEC wrapped case.
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <limits>
#include "video_core/amdgpu/buffer_size.h"

int main() {
    using AmdGpu::BufferSize::DeclaredBytes;
    using AmdGpu::BufferSize::FitsU32;
    constexpr std::uint64_t mapped = 1417430000ULL;
    if (DeclaredBytes(20, UINT32_MAX) != 85899345900ULL) return 1;
    if (static_cast<std::uint32_t>(DeclaredBytes(20, UINT32_MAX)) != 0xffffffecu) return 2;
    if (DeclaredBytes(0, UINT32_MAX) != 4294967295ULL) return 3;
    if (DeclaredBytes(20, 1) != 20) return 4;
    if (DeclaredBytes(0, 20) != 20) return 5;
    if (DeclaredBytes(4096, 1048576u) != 4294967296ULL) return 6;
    if (static_cast<std::uint32_t>(DeclaredBytes(4096, 1048576u)) != 0) return 7;
    if (!FitsU32(mapped) || FitsU32(DeclaredBytes(20, UINT32_MAX))) return 8;
    if (!FitsU32(DeclaredBytes(0, UINT32_MAX))) return 9;
    if (FitsU32(std::uint64_t{1} << 32)) return 10;
    if (DeclaredBytes(16383, UINT32_MAX) !=
        std::uint64_t{16383} * UINT32_MAX) return 11;
    // The physical mapped length is bounded by an actual guest memory map,
    // not a hardcoded game-dependent size or a wrapped descriptor.
    if (std::min(DeclaredBytes(20, UINT32_MAX), mapped) != mapped) return 12;
    std::cout << "GCN_VSHARP_64BIT_RANGE=PASS\n";
}
