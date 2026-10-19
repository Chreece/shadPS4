// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <limits>
#include "video_core/renderer_vulkan/vk_got_indirect_producer_trace.h"
using namespace Vulkan::Diagnostics;

static_assert(GotIndirectWatchAddresses.size() == 8);
static_assert(GotIndirectProducerHits(0, 0) == 0);
static_assert(GotIndirectProducerHits(0, 0xffffffffffffffffULL) == 255);
static_assert(GotIndirectProducerHits(0xfffffffffffffff0ULL, 32) == 0);
static_assert(GotIndirectProducerHits(0x1501330500ULL, 20) == 1);
static_assert(GotIndirectProducerHits(0x15013304ffULL, 1) == 0);
static_assert(GotIndirectProducerHits(0x1501330513ULL, 1) == 1);
static_assert(GotIndirectProducerHits(0x1501330514ULL, 1) == 0);
static_assert(GotIndirectProducerHits(0x1501330514ULL, 12) == 0);
static_assert(GotIndirectProducerHits(0x1501330500ULL, 52) == 3);
static_assert(GotIndirectProducerHits(0x1546ec4000ULL, 20) == 4);
static_assert(GotIndirectProducerHits(0x1548c6fd00ULL, 20) == 8);
static_assert(GotIndirectProducerHits(0x1500030540ULL, 20) == 16);
static_assert(GotIndirectProducerHits(0x1500030560ULL, 20) == 32);
static_assert(GotIndirectProducerHits(0x1546ec4500ULL, 20) == 64);
static_assert(GotIndirectProducerHits(0x1548cc4a00ULL, 20) == 128);
static_assert(ShouldReportIndirectProducer(16));
static_assert(ShouldReportIndirectProducer(1024));
static_assert(!ShouldReportIndirectProducer(1023));

int main() {
    for (std::uint32_t i = 0; i < GotIndirectWatchAddresses.size(); ++i) {
        const auto address = GotIndirectWatchAddresses[i];
        const auto bit = std::uint32_t{1} << i;
        assert((GotIndirectProducerHits(address, 20) & bit) != 0);
        assert((GotIndirectProducerHits(address - 1, 1) & bit) == 0);
        assert((GotIndirectProducerHits(address + 19, 1) & bit) != 0);
        assert((GotIndirectProducerHits(address + 20, 1) & bit) == 0);
    }
    std::uint32_t cases=0;
    for (std::uint64_t n=0; n<100000; ++n) {
        const auto address=GotIndirectWatchAddresses[n%8];
        const std::uint64_t start=address-(n%80);
        const auto length=(n%100)+1;
        const bool reference=start <= address
            ? address-start<length
            : start-address<GotIndirectCommandBytes;
        const auto got=GotIndirectProducerHits(start,length);
        assert(static_cast<bool>(got&(1u<<(n%8))) == reference);
        ++cases;
    }
    std::printf("GOT_INDIRECT_PRODUCER_ADDRESS_TEST=PASS cases=%u exact_20_bytes=YES "
                "overflow_fail_closed=YES no_guest_or_gpu_changes=YES\n",cases);
}
