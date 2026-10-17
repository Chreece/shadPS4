// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cstdint>
#include <cstdio>
#include <random>

// Mirror the pure address rules used by the x86 native scalar SRT emitter;
// Xbyak itself is exercised when the emulator is compiled against this header.
constexpr std::uint64_t kMask = (std::uint64_t{1} << 40) - 1;
constexpr std::uint64_t ComputeSBufferReadAddress(std::uint32_t lo,
                                                   std::uint32_t dw1,
                                                   std::uint32_t index) {
    return ((std::uint64_t(dw1 & 0xffu) << 32) | lo) +
           std::uint32_t(index << 2) & kMask;
}
int main() {
    struct Case {std::uint32_t lo, dw1, idx; std::uint64_t expected;};
    constexpr std::array<Case, 9> cases{{
        {0, 0, 0, 0},
        {4, 0, 1, 8},
        {0xffffffffu, 0xffu, 0, kMask},
        {0xffffffffu, 0xffu, 1, 3},
        {0, 0xffffff00u, 0, 0},
        {0, 0xffffffffu, 0, std::uint64_t{0xff} << 32},
        {0, 0, 0x40000000u, 0},
        {0, 0x1234abcd, 0, std::uint64_t{0xcd} << 32},
        {0, 0, 0x3fffffffu, 0xfffffffcu},
    }};
    for (auto [lo, hi, idx, expected] : cases) {
        if (ComputeSBufferReadAddress(lo, hi, idx) != expected) {
            std::fprintf(stderr, "SBUFFER_ADDRESS=FAIL boundary hi=%x idx=%x\n", hi, idx);
            return 1;
        }
    }
    std::mt19937 gen{0x51a876ef};
    for (int i = 0; i < 100000; ++i) {
        const std::uint32_t lo = gen(), hi = gen(), idx = gen();
        const std::uint64_t base = (std::uint64_t(hi & 0xffu) << 32) | lo;
        const std::uint64_t address =
            (base + ((std::uint64_t(idx) * 4u) & 0xffffffffu)) & kMask;
        if (ComputeSBufferReadAddress(lo, hi, idx) != address) {
            std::fprintf(stderr, "SBUFFER_ADDRESS=FAIL random=%d\n", i);
            return 1;
        }
        // Metadata bits in dword 1 are never address bits.
        if (ComputeSBufferReadAddress(lo, hi | 0xffffff00u, idx) != address) {
            std::fprintf(stderr, "SBUFFER_ADDRESS=FAIL descriptor-flags=%d\n", i);
            return 1;
        }
    }
    std::puts("SBUFFER_ADDRESS=PASS boundary=9 random=100000 metadata_isolation=100000");
    return 0;
}
