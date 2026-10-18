// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdint>
#include <iostream>
#include "video_core/buffer_cache/read_only_stream_policy.h"

using VideoCore::ReadOnlyStreamPolicy;

// Verify every flagged combination on and near the threshold boundary.
int main() {
    constexpr std::uint32_t kLegacy = ReadOnlyStreamPolicy::LegacyThreshold;
    constexpr std::uint32_t kExtended = ReadOnlyStreamPolicy::ExtendedThreshold;
    static_assert(kLegacy == 16384 && kExtended == 65536);
    const std::uint32_t values[] = {
        0, 1, kLegacy - 1, kLegacy, kLegacy + 1, kExtended - 1,
        kExtended, kExtended + 1, 1024 * 1024, 0xffffffffu
    };
    std::uint64_t n = 0;
    for (int extended = 0; extended != 2; ++extended) {
        const std::uint32_t max_size = ReadOnlyStreamPolicy::Limit(extended != 0);
        for (std::uint32_t size : values) {
            for (int is_write = 0; is_write != 2; ++is_write) {
                for (int skip = 0; skip != 2; ++skip) {
                    for (int gpu_dirty = 0; gpu_dirty != 2; ++gpu_dirty) {
                        const bool got =
                            ReadOnlyStreamPolicy::IsCandidate(size, is_write, skip, max_size)
                            && !gpu_dirty;
                        const bool expected = !is_write && !skip && !gpu_dirty &&
                                              size <= (extended ? 65536u : 16384u);
                        assert(got == expected);
                        if (!extended)
                            assert(ReadOnlyStreamPolicy::IsCandidate(size, is_write, skip,
                                                   max_size) ==
                                   (!is_write && !skip && size <= 16384u));
                        ++n;
                    }
                }
            }
        }
    }
    // Deterministic pseudo-random contract: the extended mode must change only
    // clean, eligible reads between 16 and 64 KiB. No writes, GPU-dirty
    // buffers, or IgnoreStreamBuffer requests may change behavior.
    std::uint64_t seed = 0xc7b585f84a7d2e13ULL;
    auto random = [&seed] {
        seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
        return static_cast<std::uint32_t>(seed >> 16);
    };
    for (std::uint32_t i = 0; i != 100000; ++i) {
        const auto size = random() % 262145;
        const bool write = (random() & 1) != 0;
        const bool skip = (random() & 1) != 0;
        const bool gpu_dirty = (random() & 1) != 0;
        const bool legacy =
            ReadOnlyStreamPolicy::IsCandidate(size, write, skip, kLegacy) && !gpu_dirty;
        const bool extended =
            ReadOnlyStreamPolicy::IsCandidate(size, write, skip, kExtended) && !gpu_dirty;
        assert(!legacy || extended);
        if (legacy != extended) {
            assert(size > kLegacy && size <= kExtended);
            assert(!write && !skip && !gpu_dirty);
        }
        ++n;
    }
    std::cout << "READONLY_STREAM_64K_POLICY=PASS cases=" << n << "\n";
    std::cout << "LEGACY_16K_DEFAULT_IDENTICAL=PASS\n";
    std::cout << "NO_WRITE_SKIP_OR_GPU_DIRTY_EXPANSION=PASS\n";
    std::cout << "OVERSIZED_DESCRIPTOR_UNCHANGED=PASS\n";
}
