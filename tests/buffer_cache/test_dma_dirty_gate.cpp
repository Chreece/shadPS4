// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <barrier>
#include <cstdio>
#include <thread>
#include "video_core/buffer_cache/pending_dma_upload.h"

static bool Check(bool v, const char* what) {
    if (!v) std::fprintf(stderr, "DMA_DIRTY_GATE=FAIL %s\n", what);
    return v;
}

int main() {
    VideoCore::PendingDmaUpload pending;
    if (!Check(pending.Consume(), "initial resident guest memory must scan") ||
        !Check(!pending.Consume(), "clean repeated scan should skip")) return 1;

    for (int i = 0; i < 10000; ++i) {
        pending.Notify();
        if (!Check(pending.Consume(), "guest write notification disappeared") ||
            !Check(!pending.Consume(), "duplicate no-op scan not filtered")) return 1;
    }

    // A host/guest CPU write arriving while the GPU drains resident buffers
    // must remain pending for the next draw. Never mark the pass clean AFTER
    // synchronizing, which would lose a write racing the scanner.
    std::barrier arrival(2);
    std::barrier emitted(2);
    std::thread writer([&] {
        for (int i = 0; i < 2000; ++i) {
            arrival.arrive_and_wait();
            pending.Notify();
            emitted.arrive_and_wait();
        }
    });
    bool ok=true;
    for (int i=0; i<2000; ++i) {
        if (!pending.Consume()) {
            // The prior iteration's post-consumption write is guaranteed
            // visible on entry here (except after explicit final drain).
            if (i != 0) ok=false;
        }
        arrival.arrive_and_wait(); // writer races with GPU's in-progress scan
        emitted.arrive_and_wait(); // signal published before next Consume
        if (!pending.Consume()) ok=false;
        pending.Notify(); // another dirty page for next pass
    }
    writer.join();
    if (!Check(ok, "concurrent CPU write lost across DMA scan")) return 1;
    if (!Check(pending.Consume() && !pending.Consume(),
               "final pending work not consumed exactly once")) return 1;
    std::puts("DMA_DIRTY_GATE=PASS initial_scan=PASS idle_skip=PASS "
              "sequential_mark=10000 concurrent_scan=2000");
    return 0;
}
