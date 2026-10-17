// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cstdint>
#include <cstdio>
#include <random>

// Host-side reference for GCN scalar S_BUFFER_LOAD byte-offset clamping
// (GCN3 ISA section 7.2.3); no GPU or game files required.
constexpr std::uint64_t kMask = (std::uint64_t{1} << 40) - 1;
constexpr std::uint64_t Compute(std::uint32_t lo, std::uint32_t dw1,
                                std::uint32_t num_records, std::uint32_t index) {
    const std::uint64_t base = (std::uint64_t(dw1 & 0xffu) << 32) | lo;
    const std::uint64_t stride = (dw1 >> 16) & 0x3fffu;
    const std::uint64_t limit = stride ? stride * num_records : num_records;
    const std::uint64_t bytes = std::uint32_t(index << 2);
    const std::uint64_t clamped = stride == 0
        ? (bytes >= limit ? limit : bytes)
        : (bytes > limit ? limit : bytes);
    return (base + (clamped & ~std::uint64_t{3})) & kMask;
}
int main() {
    struct Case {std::uint32_t lo, dw1, records, idx; std::uint64_t expected;};
    constexpr std::array<Case, 14> cases{{
        {0, 0, 0, 0, 0},
        {4, 0, 100, 1, 8},
        {0xffffffffu, 0xffu, 0xffffffffu, 0, kMask},
        {0xffffffffu, 0xffu, 0xffffffffu, 1, 3},
        {0, 0xffffff00u, 0, 0, 0},
        {0, 0xffffffffu, 0, 0, std::uint64_t{0xff} << 32},
        {0, 0, 0xffffffffu, 0x40000000u, 0},
        {0, 0x1234abcd, 0xffffffffu, 0, std::uint64_t{0xcd} << 32},
        {0, 0, 0xffffffffu, 0x3fffffffu, 0xfffffffcu},
        {0, 0, 16, 4, 16},        // stride zero: equal boundary clamps
        {0, 0, 15, 4, 12},        // stride zero: unaligned limit rounds down
        {0, 16u<<16, 4, 20, 64}, // stride 16: clamped at stride*num_records
        {0, 16u<<16, 4, 16, 64}, // stride 16: equal limit not clamped
        {0, 16u<<16, 1, 5, 16},  // stride 16: over limit clamps
    }};
    for (auto [lo, dw1, nr, idx, expected] : cases) {
        if (Compute(lo,dw1,nr,idx) != expected) {
            std::fprintf(stderr, "SBUFFER_BOUNDS=FAIL boundary hi=%x nr=%u idx=%x\n",dw1,nr,idx);
            return 1;
        }
    }
    std::mt19937 gen{0x51a876ef};
    for (int i = 0; i < 100000; ++i) {
        const std::uint32_t lo=gen(), hi=gen(), records=gen(), idx=gen();
        const std::uint64_t base = (std::uint64_t(hi & 0xffu) << 32) | lo;
        const std::uint64_t stride = (hi >> 16) & 0x3fffu;
        const std::uint64_t limit = stride ? stride * records : records;
        const std::uint64_t offset = (std::uint64_t(idx) * 4u) & 0xffffffffu;
        const std::uint64_t actual = stride == 0
            ? (offset >= limit ? limit : offset)
            : (offset > limit ? limit : offset);
        const std::uint64_t expected = (base + (actual & ~std::uint64_t{3})) & kMask;
        if (Compute(lo,hi,records,idx) != expected) {
            std::fprintf(stderr, "SBUFFER_BOUNDS=FAIL random=%d\n", i);
            return 1;
        }
        // Flags outside address bits [7:0] and stride [29:16] are ignored.
        if (Compute(lo,hi ^ 0xc000ff00u,records,idx) != expected) {
            std::fprintf(stderr, "SBUFFER_BOUNDS=FAIL ignored-flags=%d\n", i);
            return 1;
        }
    }
    std::puts("SBUFFER_BOUNDS=PASS boundary=14 random=100000 flags=100000");
    return 0;
}
