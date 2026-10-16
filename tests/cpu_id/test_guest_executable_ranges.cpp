// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <limits>
#include <thread>
#include "core/cpu_id_guest_ranges.h"

using Core::CpuId::GuestExecutableRanges;
static bool Check(bool value, const char* message) {
    if (!value) std::fprintf(stderr, "CPU_GUEST_RANGES=FAIL %s\n", message);
    return value;
}
int main() {
    GuestExecutableRanges ranges;
    if (!Check(!ranges.Contains(0x400000), "empty table") ||
        !Check(!ranges.Register(0, 16), "zero address") ||
        !Check(!ranges.Register(0x400000, 0), "empty segment") ||
        !Check(!ranges.Register(std::numeric_limits<std::uintptr_t>::max()-3, 8),
               "overflow accepted") ||
        !Check(ranges.Register(0x400000, 0x1000), "guest ELF") ||
        !Check(ranges.Register(0x800000000, 0x2000), "system ELF") ||
        !Check(ranges.Contains(0x400000), "inclusive start") ||
        !Check(ranges.Contains(0x400fff), "inclusive last byte") ||
        !Check(!ranges.Contains(0x401000), "exclusive end") ||
        !Check(!ranges.Contains(0x7fff00000000ULL), "host DSO misclassified") ||
        !Check(ranges.Contains(0x800001000), "guest system lib") ||
        !Check(ranges.Register(0x400000, 0x1000), "reloaded range")) return 1;

    constexpr unsigned Writers = 4, N = 100;
    std::array<std::thread, Writers> pool;
    std::atomic<unsigned> completed{0};
    std::atomic<bool> failed{false};
    for (unsigned w = 0; w < Writers; ++w) {
        pool[w] = std::thread([&, w] {
            for (unsigned i = 0; i < N; ++i) {
                std::uintptr_t base = 0x100000000ULL + w*0x1000000ULL + i*0x1000ULL;
                if (!ranges.Register(base, 0x800)) { failed.store(true); return; }
                completed.fetch_add(1);
            }
        });
    }
    while (completed.load() < Writers*N && !failed.load()) {
        (void)ranges.Contains(0x7fff00000000ULL);
    }
    for (auto& writer : pool) writer.join();
    if (!Check(!failed.load() && completed.load()==Writers*N, "parallel publish"))
        return 1;
    for (unsigned w=0; w<Writers; ++w) {
        for (unsigned i=0; i<N; ++i) {
            std::uintptr_t base=0x100000000ULL+w*0x1000000ULL+i*0x1000ULL;
            if (!Check(ranges.Contains(base+0x7ff), "published last byte") ||
                !Check(!ranges.Contains(base+0x800), "published exclusive end"))
                return 1;
        }
    }
    std::puts("CPU_GUEST_RANGES=PASS 402 registered, 400 concurrent, host-separated");
    return 0;
}
