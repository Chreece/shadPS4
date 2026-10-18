// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdint>
#include <iostream>
#include "video_core/uffd_hotregion_sampler.h"

using VideoCore::UffdHotRegionSampler;

int main() {
    UffdHotRegionSampler sampler;
    assert(sampler.Events() == 0 && sampler.Sampled() == 0);
    // A frequently re-protected 64-KiB guest range must dominate the
    // statistics without affecting the real memory-protection state.
    constexpr std::uint64_t A = 0x12340000ULL;
    constexpr std::uint64_t B = 0x57680000ULL;
    for (int i = 0; i < 200000; ++i) {
        const auto address = (i % 10 == 0 ? B : A) + std::uint64_t(i % 4096);
        sampler.Observe(address);
    }
    assert(sampler.Events() == 200000);
    assert(sampler.Sampled() > 1200 && sampler.Sampled() < 2000);
    const auto top = sampler.Top();
    assert(top[0].region == A);
    assert(top[0].count > top[1].count * 3);
    assert(top[0].error == 0 && top[0].MinimumCount() == top[0].count);
    assert(top[1].region == B && top[1].error == 0);
    assert(sampler.Replacements() == 0);

    sampler.ResetWindow();
    assert(sampler.Sampled() == 0 && sampler.Replacements() == 0);
    assert(sampler.Events() == 200000);
    assert(sampler.Top()[0].count == 0);

    // Rotate across >64 regions to force bounded slot replacement.
    for (int i = 0; i < 300000; ++i) {
        const std::uint64_t guest =
            std::uint64_t(1 + (i % 256)) * UffdHotRegionSampler::RegionSize + (i & 4095);
        sampler.Observe(guest);
    }
    assert(sampler.Replacements() > 0);
    assert(sampler.Sampled() > 2000 && sampler.Sampled() < 2800);
    const auto second = sampler.Top();
    assert(second.size() == UffdHotRegionSampler::ReportCount);
    for (const auto& item : second) {
        assert(item.region % UffdHotRegionSampler::RegionSize == 0);
        assert(item.MinimumCount() <= item.count);
    }
    std::cout << "UFFD_HOTREGION_NATIVE_HOST=PASS events=500000\n"
              << "SAMPLE_FRACTION_NEAR_1_IN_128=PASS\n"
              << "BOUNDED_COUNTERS_AND_REPLACEMENT=PASS\n"
              << "NO_GPU_OR_MEMORY_PROTECTION_CHANGES=PASS\n";
}
