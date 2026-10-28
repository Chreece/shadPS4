// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Standalone CPU-only test: g++ -std=c++23 -O2 -Wall -Wextra -Werror
// -pedantic -pthread -I. tests/gpu_submit_trace_test.cpp -o /tmp/trace-test
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>
#include "src/video_core/renderer_vulkan/vk_gpu_submit_trace.h"

using namespace Vulkan::GpuTrace;
static void Require(bool ok, const char* detail) {
    if (!ok) {
        std::fprintf(stderr, "GPU_TRACE_UNIT_FAIL=%s\n", detail);
        std::abort();
    }
}

int main() {
    Recorder r;
    constexpr std::uintptr_t presenter = 0x1111, guest = 0x2222;
    for (std::uint64_t i = 1; i <= 100; ++i) {
        r.Record(presenter, i, Kind::QueueSubmit, 0, i, 1, (i & 1) ? 0 : 2);
    }
    for (std::uint64_t i = 0; i < 1900; ++i) {
        if ((i % 100) == 0) {
            r.Record(guest, (i / 100) + 3, Kind::QueueSubmit, 0, (i / 100) + 3, 1, 0, 1, 1);
        }
        r.Record(guest, (i / 100) + 3, Kind::ComputeIndirect, 0x23e7d715,
                 0x21d4157e0ULL + i * 32ULL, 1, 12);
    }
    auto p = r.Capture(presenter);
    auto g = r.Capture(guest);
    Require(p.scheduler_id == presenter && p.events == 100, "presenter separated");
    Require(p.compute_events == 0 && p.submit_events == 100, "presenter counts");
    Require(p.first_submit_count == 32, "presenter earliest 32 submissions");
    Require(p.first_submit[0].tick == 1 && p.first_submit[31].tick == 32,
            "presenter timeline monotonic");
    Require(g.scheduler_id == guest && g.compute_events == 1900, "guest compute preserved");
    Require(g.first_compute_count == 64, "guest earliest 64 compute captured");
    Require(g.first_compute[0].tick == 3, "first guest tick");
    Require(g.first_compute[0].shader == 0x23e7d715, "first guest shader");
    Require(g.first_compute[0].source == 0x21d4157e0ULL, "first guest indirect source");
    Require(g.first_submit_count == 19 && g.submit_events == 19, "guest 19 submissions");
    Require(g.last_cached_completion == 1, "completed cached labelled");
    Require(g.recent[(g.events - 1) % RecentSize].source == 0x21d4157e0ULL + 1899ULL * 32,
            "latest ring survived wrap");
    Require(r.Capture(0x3333).scheduler_id == 0, "missing scheduler has no data");
    // Constructor can be used from more than one translation unit; this
    // model also checks bounded concurrent appends without heap allocation.
    Recorder concurrent;
    std::vector<std::thread> workers;
    for (int j = 0; j < 4; ++j) {
        workers.emplace_back([&, j] {
            for (int i = 0; i < 500; ++i) {
                concurrent.Record(guest, j * 500 + i, Kind::Draw, 0x15, 0, 3, 2);
            }
        });
    }
    for (auto& thread : workers) {
        thread.join();
    }
    const auto after = concurrent.Capture(guest);
    Require(after.events == 2000 && after.draw_events == 2000, "concurrent events consistent");
    Require(after.first_draw_count == 32, "first draw retained");
    Require(after.recent[(after.events - 1) % RecentSize].sequence == 1999,
            "concurrent last sequence");
    Recorder many;
    for (std::uintptr_t id = 1; id <= MaxSchedulers; ++id) {
        many.Record(id, 1, Kind::QueueSubmit, 0, 1);
    }
    many.Record(0xffff, 1, Kind::QueueSubmit, 0, 1);
    Require(many.OverflowEvents() == 1, "overflow is explicitly accounted");
    Require(many.Capture(0xffff).scheduler_id == 0, "overflow not conflated");
    std::puts("GPU_SUBMIT_TRACE_CPU_FIXTURE=PASS queues=8 first_compute=64 first_draw=32 first_submit=32 recent=128");
}
