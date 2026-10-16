// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cstdint>
#include <cstdio>
#include <limits>
#include <random>
#include "video_core/uffd_range.h"

using VideoCore::UffdRegistrationRange;
using VideoCore::UffdFaultWakeRange;
static bool Check(bool value, const char* name) {
    if (!value) std::fprintf(stderr, "UFFD_RANGE=FAIL %s\n", name);
    return value;
}
static constexpr std::uint64_t P = 4096;
static_assert(UffdRegistrationRange(0x10000, 0x40000, P)->start == 0x10000);
static_assert(UffdRegistrationRange(0x10000, 0x40000, P)->len == 0x30000);
static_assert(UffdFaultWakeRange(0x12345, P)->start == 0x12000);
static_assert(UffdFaultWakeRange(0x12345, P)->len == P);
int main() {
    // A regression guard against the previous OnMap(lower, upper), which
    // would erroneously register from lower through lower+upper.
    for (const auto& span : {
             std::pair{0x100000ULL,0x180000ULL},
             std::pair{0x400000000ULL,0x500000000ULL},
             std::pair{0x10000000000ULL,0x10800000000ULL},
        }) {
        auto r=UffdRegistrationRange(span.first,span.second,P);
        if (!Check(r.has_value(), "valid VM interval rejected") ||
            !Check(r->start == span.first && r->len == span.second-span.first,
                   "kernel range length is not upper-minus-lower") ||
            !Check(r->start+r->len == span.second, "kernel range ends outside VM interval")) {
            return 1;
        }
    }
    if (!Check(!UffdRegistrationRange(0x1000,0x1000,P), "zero length accepted") ||
        !Check(!UffdRegistrationRange(0x2000,0x1000,P), "reversed interval accepted") ||
        !Check(!UffdRegistrationRange(0x1001,0x2000,P), "unaligned beginning accepted") ||
        !Check(!UffdRegistrationRange(0x1000,0x2001,P), "unaligned ending accepted") ||
        !Check(!UffdRegistrationRange(0x1000,0x2000,0), "zero page size accepted") ||
        !Check(!UffdRegistrationRange(0x1000,0x2000,3000), "non-power-of-two page size accepted")) {
        return 1;
    }
    constexpr auto max=std::numeric_limits<std::uint64_t>::max();
    if (!Check(UffdRegistrationRange(max+1ULL-2*P,max+1ULL-P,P).has_value(),
               "high canonical-interval overflow")) return 1;

    unsigned tests=0;
    std::mt19937_64 rng{0x6b0dadf060ULL};
    for (unsigned i=0;i<100000;++i) {
        // Several aligned VMAs with offsets that differ hugely from lengths.
        const auto start=(rng() % (1ULL<<29)) * P;
        const auto pages=1 + (rng()%4096);
        const auto end=start + pages*P;
        const auto reg=UffdRegistrationRange(start,end,P);
        const auto addr=start+(rng()%(pages*P));
        const auto wake=UffdFaultWakeRange(addr,P);
        if (!Check(reg && reg->start==start && reg->len==pages*P,
                   "randomized registration range mismatch") ||
            !Check(wake && wake->start<=addr &&
                   addr<wake->start+wake->len &&
                   (wake->start&(P-1))==0 &&
                   wake->len==P,
                   "randomized fault wake alignment mismatch")) return 1;
        ++tests;
    }
    std::printf("UFFD_RANGE=PASS valid_intervals=3 randomized=%u "
                "wake_alignment=PASS invalid_inputs=PASS\n",tests);
    return 0;
}
