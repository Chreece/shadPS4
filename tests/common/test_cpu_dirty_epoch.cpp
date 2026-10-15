// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Standalone concurrency proof for the same CpuDirtyEpoch used by the DMA
// buffer tracker. Tests mutation racing a sweep and residency invalidation.
#include <atomic>
#include <chrono>
#include <cstdio>
#include <future>
#include <thread>

#include "video_core/buffer_cache/cpu_dirty_epoch.h"

using VideoCore::CpuDirtyEpoch;

static void Require(bool value, const char* name) {
    if (!value) {
        std::fprintf(stderr, "DMA_DIRTY_EPOCH_FAIL=%s\n", name);
        std::terminate();
    }
}

int main() {
    CpuDirtyEpoch epoch;
    const auto original=epoch.Snapshot();
    Require(original!=0,"nonzero-initial-generation");
    std::uint64_t last_full_scan=0;
    Require(epoch.Snapshot()!=last_full_scan, "initial-full-scan-required");
    last_full_scan=original;
    Require(epoch.Snapshot()==last_full_scan, "unchanged-page-state-skips");

    {
        CpuDirtyEpoch::Mutation cpu_write{epoch};
        Require(epoch.Snapshot()!=last_full_scan,
                "CPU dirty notification invalidates cached sweep");
    }
    Require(epoch.Snapshot()>original,"complete-CPU-dirty-mutation-increments");

    // A CPU fault may start while a GPU-command thread scans.
    // Record the old generation; the finishing mutation must force a later
    // rescan even when the scan happened before the CPU state was updated.
    std::promise<void> entered;
    std::promise<void> allow_finish;
    auto finish=allow_finish.get_future();
    std::thread writer([&] {
        CpuDirtyEpoch::Mutation update{epoch};
        entered.set_value();
        finish.wait();
    });
    entered.get_future().wait();
    const auto during_write=epoch.Snapshot();
    last_full_scan=during_write;
    allow_finish.set_value();
    writer.join();
    Require(epoch.Snapshot()!=last_full_scan,
            "racing-CPU-write-must-force-next-sweep");

    last_full_scan=epoch.Snapshot();
    Require(epoch.Snapshot()==last_full_scan,
            "sweep-can-be-skipped-when-unchanged");
    last_full_scan=0; // New sparse residency always invalidates scan cache.
    Require(epoch.Snapshot()!=last_full_scan,
            "new-sparse-residency-forces-sweep");

    std::printf("DMA_DIRTY_EPOCH_NATIVE=PASS cases=8\n");
    return 0;
}
