#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile the real RegionManager/MemoryTracker with a recording PageManager.

The fake boundary records installed read/write watches. It does not emulate
Vulkan, execute a game, or validate performance. A missing fence method on the
baseline is represented by a no-op, allowing the same tests to expose the stale
CPU-read scenario before applying the candidate.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

STUBS = {
'common/types.h': r'''#pragma once
#include <algorithm>
#include <array>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <type_traits>
#include <utility>
using u8 = std::uint8_t;
using s8 = std::int8_t;
using u32 = std::uint32_t;
using s32 = std::int32_t;
using u64 = std::uint64_t;
using s64 = std::int64_t;
using VAddr = u64;
constexpr u64 operator""_KB(unsigned long long n) { return n * 1024; }
''',
'common/adaptive_mutex.h': r'''#pragma once
#include <mutex>
namespace Common { using AdaptiveMutex = std::mutex; }
''',
'core/emulator_settings.h': r'''#pragma once
#include "common/types.h"
enum GpuReadbacksMode : int { Disabled, Relaxed, Precise };
struct TestSettings {
    u32 mode{};
    u32 GetReadbacksMode() const { return mode; }
};
inline TestSettings EmulatorSettings;
''',
'video_core/page_manager.h': r'''#pragma once
#include <cstdio>
#include <cstdlib>
#include <map>
#include "video_core/buffer_cache/region_definitions.h"
namespace VideoCore {
enum class PageOp : s8 { None = 0, Track = 1, Untrack = -1 };
struct PageManager {
    std::map<VAddr, int> reads;
    std::map<VAddr, int> writes;
    u64 calls{};
    int ReadWatch(VAddr addr) const {
        auto it = reads.find(addr / BYTES_PER_PAGE);
        return it == reads.end() ? 0 : it->second;
    }
    int WriteWatch(VAddr addr) const {
        auto it = writes.find(addr / BYTES_PER_PAGE);
        return it == writes.end() ? 0 : it->second;
    }
    void UpdatePageWatchersForRegion(VAddr base, const Bounds& b,
        const RegionBits& wm, const RegionBits& rm, PageOp wo, PageOp ro) {
        ++calls;
        for (u64 p = b.start_word * PAGES_PER_WORD + b.start_page;
             p <= b.end_word * PAGES_PER_WORD + b.end_page; ++p) {
            const auto page = base / BYTES_PER_PAGE + p;
            if (wo != PageOp::None && wm.GetPage(p)) {
                auto& count = writes[page];
                count += std::to_underlying(wo);
                if (count < 0 || count > 1) {
                    std::fprintf(stderr, "FAIL: write-watch imbalance at page %llu\n",
                                 static_cast<unsigned long long>(page));
                    std::exit(2);
                }
            }
            if (ro != PageOp::None && rm.GetPage(p)) {
                auto& count = reads[page];
                count += std::to_underlying(ro);
                if (count < 0 || count > 1) {
                    std::fprintf(stderr, "FAIL: read-watch imbalance at page %llu\n",
                                 static_cast<unsigned long long>(page));
                    std::exit(2);
                }
            }
        }
    }
};
}
''',
}

CASES = r'''#include <cstdio>
#include <cstdlib>
#include <random>
#include <string>
#include <vector>
#include "video_core/buffer_cache/memory_tracker.h"
using namespace VideoCore;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL: %s:%d: %s\n", __func__, __LINE__, #x); std::exit(1); } } while (false)
constexpr VAddr Base = 0x12000000ULL;
constexpr u64 Page = BYTES_PER_PAGE;

template<class Tracker>
u64 Fence(Tracker& t, VAddr addr, u64 size) {
    if constexpr (requires { t.ProtectGpuWrites(addr, size); }) {
        return t.ProtectGpuWrites(addr, size);
    } else {
        // The upstream baseline does not install buffer read watches at fences.
        return 0;
    }
}
struct Fixture {
    PageManager pm;
    std::unique_ptr<MemoryTracker> tracker;
    explicit Fixture(u32 mode) {
        EmulatorSettings.mode = mode;
        tracker = std::make_unique<MemoryTracker>(pm);
    }
    void GpuWrite(VAddr addr, u64 size = Page) {
        tracker->ForEachUploadRange(addr, size, true, [](u64, u64) {});
    }
    void Download(VAddr addr, u64 size = Page) {
        tracker->UnmarkRegionAsGpuModified(addr, size, false);
    }
    void CpuWrite(VAddr addr, u64 size = Page) {
        tracker->InvalidateRegion(addr, size, [&] {
            tracker->UnmarkRegionAsGpuModified(addr, size, true);
        });
    }
};
void DisabledUnchanged() {
    Fixture f(Disabled);
    f.GpuWrite(Base);
    CHECK(f.pm.ReadWatch(Base) == 0);
    CHECK(Fence(*f.tracker, Base, Page) == 0);
    CHECK(f.pm.ReadWatch(Base) == 0);
    f.CpuWrite(Base);
    CHECK(f.pm.WriteWatch(Base) == 0);
    CHECK(f.tracker->IsRegionGpuModified(Base, Page));
    std::puts("PASS disabled semantics unchanged");
}
void PreciseUnchanged() {
    Fixture f(Precise);
    f.GpuWrite(Base);
    CHECK(f.pm.ReadWatch(Base) == 1);
    CHECK(Fence(*f.tracker, Base, Page) == 0);
    f.Download(Base);
    CHECK(f.pm.ReadWatch(Base) == 0);
    f.GpuWrite(Base);
    CHECK(f.pm.ReadWatch(Base) == 1);
    f.CpuWrite(Base);
    CHECK(f.pm.ReadWatch(Base) == 0 && f.pm.WriteWatch(Base) == 0);
    std::puts("PASS precise immediate protection unchanged");
}
void FenceMakesGpuDataObservable() {
    Fixture f(Relaxed);
    u64 cpu_value = 11;
    u64 gpu_value = 42;
    f.GpuWrite(Base);
    CHECK(f.pm.ReadWatch(Base) == 0); // Not a global Precise override.
    CHECK(Fence(*f.tracker, Base, Page) == 1);
    CHECK(f.pm.ReadWatch(Base) == 1);
    // A recorded read watch routes this simulated CPU access through readback.
    if (f.pm.ReadWatch(Base)) {
        f.tracker->ForEachDownloadRange<false>(Base, Page, [&](u64, u64) {
            cpu_value = gpu_value;
        });
        f.Download(Base);
    }
    CHECK(cpu_value == 42);
    CHECK(f.pm.ReadWatch(Base) == 0);
    std::puts("PASS relaxed fence prevents simulated stale CPU read");
}
void RepeatedFenceAndRearm() {
    Fixture f(Relaxed);
    f.GpuWrite(Base, 3 * Page);
    CHECK(Fence(*f.tracker, Base, 3 * Page) == 3);
    const auto calls = f.pm.calls;
    CHECK(Fence(*f.tracker, Base, 3 * Page) == 0);
    CHECK(f.pm.calls == calls);
    f.GpuWrite(Base, 3 * Page); // A repeated bind must not drop installed watches.
    CHECK(Fence(*f.tracker, Base, 3 * Page) == 0);
    f.Download(Base + Page);
    CHECK(f.pm.ReadWatch(Base) == 1);
    CHECK(f.pm.ReadWatch(Base + Page) == 0);
    CHECK(f.pm.ReadWatch(Base + 2 * Page) == 1);
    f.GpuWrite(Base + Page);
    CHECK(f.pm.ReadWatch(Base + Page) == 0);
    CHECK(Fence(*f.tracker, Base, 3 * Page) == 1);
    f.Download(Base, 3 * Page);
    CHECK(Fence(*f.tracker, Base, 3 * Page) == 0);
    std::puts("PASS repeated fences, partial download, and rearm");
}
void DownloadBeforeFence() {
    Fixture f(Relaxed);
    f.GpuWrite(Base, 2 * Page);
    f.CpuWrite(Base); // Flushes a dirty page with no read watch installed yet.
    CHECK(f.pm.ReadWatch(Base) == 0);
    CHECK(!f.tracker->IsRegionGpuModified(Base, Page));
    CHECK(Fence(*f.tracker, Base, 2 * Page) == 1);
    CHECK(f.pm.ReadWatch(Base) == 0 && f.pm.ReadWatch(Base + Page) == 1);
    f.Download(Base + Page);
    CHECK(Fence(*f.tracker, Base, 2 * Page) == 0);
    std::puts("PASS pre-fence write-fault readback without watcher underflow");
}
void ReadOnlyAndZeroLength() {
    Fixture f(Relaxed);
    f.tracker->ForEachUploadRange(Base, Page, false, [](u64, u64) {});
    CHECK(Fence(*f.tracker, Base, Page) == 0);
    CHECK(f.pm.ReadWatch(Base) == 0 && f.pm.WriteWatch(Base) == 1);
    f.GpuWrite(Base);
    CHECK(Fence(*f.tracker, Base, 0) == 0);
    CHECK(f.pm.ReadWatch(Base) == 0);
    CHECK(Fence(*f.tracker, Base + 8 * HIGHER_PAGE_SIZE, Page) == 0);
    std::puts("PASS read-only buffers, empty and unallocated ranges");
}
void BoundariesAndHoles() {
    Fixture f(Relaxed);
    f.GpuWrite(Base + 63 * Page + 17, Page);
    f.GpuWrite(Base + HIGHER_PAGE_SIZE - 8, 16);
    CHECK(Fence(*f.tracker, Base, HIGHER_PAGE_SIZE + Page) == 4);
    CHECK(f.pm.ReadWatch(Base + 62 * Page) == 0);
    CHECK(f.pm.ReadWatch(Base + 63 * Page) == 1);
    CHECK(f.pm.ReadWatch(Base + 64 * Page) == 1);
    CHECK(f.pm.ReadWatch(Base + 65 * Page) == 0);
    CHECK(f.pm.ReadWatch(Base + HIGHER_PAGE_SIZE - Page) == 1);
    CHECK(f.pm.ReadWatch(Base + HIGHER_PAGE_SIZE) == 1);
    f.Download(Base, HIGHER_PAGE_SIZE + Page);
    CHECK(Fence(*f.tracker, Base, HIGHER_PAGE_SIZE + Page) == 0);
    f.GpuWrite(Base, HIGHER_PAGE_SIZE);
    CHECK(Fence(*f.tracker, Base, HIGHER_PAGE_SIZE) == NUM_REGION_PAGES);
    CHECK(Fence(*f.tracker, Base, HIGHER_PAGE_SIZE) == 0);
    f.Download(Base, HIGHER_PAGE_SIZE);
    std::puts("PASS word/region boundaries, holes, and full-region masks");
}
void ClearIterator() {
    Fixture f(Relaxed);
    f.GpuWrite(Base, 3 * Page);
    CHECK(Fence(*f.tracker, Base, Page) == 1);
    u64 pages{};
    f.tracker->ForEachDownloadRange<true>(Base, 3 * Page, [&](u64, u64 size) {
        pages += size / Page;
    });
    CHECK(pages == 3);
    CHECK(!f.tracker->IsRegionGpuModified(Base, 3 * Page));
    CHECK(Fence(*f.tracker, Base, 3 * Page) == 0);
    std::puts("PASS clear-on-iteration releases only installed watches");
}
void RandomTransitions(u32 mode) {
    Fixture f(mode);
    constexpr u64 Count = 130;
    std::array<bool, Count> cpu, gpu{}, read{};
    cpu.fill(true);
    // Instantiate tracking for every page without declaring a GPU write.
    f.tracker->ForEachUploadRange(Base, Count * Page, false, [](u64, u64) {});
    cpu.fill(false);
    std::mt19937 gen(0x637e + mode);
    for (unsigned step = 0; step < 10000; ++step) {
        const u64 lo = gen() % Count;
        const u64 hi = std::min(Count, lo + 1 + gen() % 71);
        const auto op = gen() % 5;
        const VAddr addr = Base + lo * Page;
        const u64 size = (hi - lo) * Page;
        if (op == 0) {
            f.GpuWrite(addr, size);
            for (u64 p = lo; p < hi; ++p) {
                cpu[p] = false; gpu[p] = true;
                if (mode == Precise) read[p] = true;
            }
        } else if (op == 1) {
            Fence(*f.tracker, addr, size);
            if (mode == Relaxed) for (u64 p = lo; p < hi; ++p) read[p] = gpu[p];
        } else if (op == 2) {
            f.Download(addr, size);
            for (u64 p = lo; p < hi; ++p) { gpu[p] = false; read[p] = false; }
        } else if (op == 3) {
            // CPU faults are resolved pagewise here, matching the fixture's callback.
            for (u64 p = lo; p < hi; ++p) {
                f.CpuWrite(Base + p * Page);
                cpu[p] = true;
                if (mode != Disabled) { gpu[p] = false; read[p] = false; }
            }
        } else {
            f.tracker->ForEachUploadRange(addr, size, false, [](u64, u64) {});
            for (u64 p = lo; p < hi; ++p) cpu[p] = false;
        }
        for (u64 p = 0; p < Count; ++p) {
            CHECK(f.pm.ReadWatch(Base + p * Page) == int(read[p]));
            CHECK(f.pm.WriteWatch(Base + p * Page) == int(!cpu[p]));
            CHECK(f.tracker->IsRegionGpuModified(Base + p * Page, Page) == gpu[p]);
            CHECK(f.tracker->IsRegionCpuModified(Base + p * Page, Page) == cpu[p]);
        }
    }
    std::printf("PASS 10000 randomized transitions mode=%u\n", mode);
}
int main() {
    DisabledUnchanged(); PreciseUnchanged(); FenceMakesGpuDataObservable();
    RepeatedFenceAndRearm(); DownloadBeforeFence(); ReadOnlyAndZeroLength();
    BoundariesAndHoles(); ClearIterator();
    RandomTransitions(Disabled); RandomTransitions(Relaxed); RandomTransitions(Precise);
    std::puts("TRACKER_TESTS=PASS cases=11 randomized_transitions=30000");
}
'''


def run(root: Path, compiler: str, sanitize: bool) -> int:
    with tempfile.TemporaryDirectory(prefix='shad-readback-unit-') as td:
        temp = Path(td)
        for name, text in STUBS.items():
            dest = temp / 'include' / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
        src = temp / 'cases.cpp'
        src.write_text(CASES)
        binary = temp / 'cases'
        command = [compiler, '-std=c++23', '-O1' if sanitize else '-O2', '-pthread',
                   '-I' + str(temp / 'include'), '-I' + str(root / 'src'), str(src), '-o', str(binary)]
        if sanitize:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-g']
        print('COMPILE=' + shlex.join(command), flush=True)
        subprocess.run(command, check=True, timeout=90)
        return subprocess.run([str(binary)], timeout=120).returncode


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    p.add_argument('--compiler', default=os.environ.get('CXX', 'g++-14'))
    p.add_argument('--sanitize', action='store_true')
    a = p.parse_args()
    return run(a.root, a.compiler, a.sanitize)


if __name__ == '__main__':
    raise SystemExit(main())
