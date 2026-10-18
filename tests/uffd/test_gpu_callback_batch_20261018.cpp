// Regression model for batching UFFD-origin CPU callbacks without changing
// the GPU's per-page InvalidateMemory/WakeFault ordering.
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <utility>
#include <vector>

struct Fault {
    uint64_t addr;
    int tid;
    bool gpu;
};
struct Event {
    char operation;
    uint64_t addr;
    int tid;
};
struct Emulator {
    std::vector<std::function<void()>> gpu_commands;
    std::vector<Event> events;
    int pending{0};
    int submits{0};

    void Invalidate(uint64_t addr, int tid) { events.push_back({'I', addr, tid}); }
    void Wake(uint64_t addr, int tid) { events.push_back({'W', addr, tid}); }

    void Submit(const Fault& first, std::function<void()> finish) {
        ++submits;
        gpu_commands.emplace_back([this, first, finish = std::move(finish)] {
            Invalidate(first.addr, first.tid);
            finish();
        });
    }
    void DrainGpu() {
        for (auto& callback : gpu_commands) callback();
        gpu_commands.clear();
    }
    void Execute(const std::vector<Fault>& input, bool callback_batch) {
        std::vector<Fault> bundle;
        auto flush = [&] {
            if (bundle.empty()) return;
            const auto first = bundle.front();
            pending += static_cast<int>(bundle.size());
            Submit(first, [this, faults = std::move(bundle)] {
                for (size_t i = 0; i < faults.size(); ++i) {
                    const auto fault = faults[i];
                    if (i != 0) Invalidate(fault.addr, fault.tid);
                    Wake(fault.addr, fault.tid);
                }
                pending -= static_cast<int>(faults.size());
            });
            bundle.clear();
        };
        for (const auto& f : input) {
            if (f.gpu) {
                if (callback_batch) flush();
                // This fault interrupted the GPU itself. Do NOT queue it to
                // that blocked GPU; perform the invalidation inline.
                Invalidate(f.addr, f.tid);
                Wake(f.addr, f.tid);
            } else if (callback_batch && input.size() > 1) {
                bundle.push_back(f);
            } else {
                ++pending;
                Submit(f, [this, f] { Wake(f.addr, f.tid); --pending; });
            }
        }
        flush();
    }
};
void Require(bool cond, const char* message) {
    if (!cond) { std::fprintf(stderr, "UFFD_CALLBACK_BATCH_TEST_FAIL=%s\n", message); std::abort(); }
}
void TestBundledRead() {
    Emulator e;
    e.Execute({{0x1000, 10, false}, {0x2000, 11, false},
               {0x3000, 12, false}}, true);
    Require(e.submits == 1 && e.pending == 3, "BUNDLED_GPU_QUEUE_SUBMIT_COUNT");
    e.DrainGpu();
    Require(e.pending == 0 && e.events.size() == 6, "ALL_BUNDLED_FAULTS_COMPLETED");
    for (int i = 0; i < 3; ++i) {
        Require(e.events[i*2].operation == 'I' &&
                e.events[i*2+1].operation == 'W' &&
                e.events[i*2].tid == 10 + i &&
                e.events[i*2+1].tid == 10 + i,
                "BUNDLED_PAGE_INVALIDATE_BEFORE_WAKE_AND_FIFO");
    }
}
void TestGpuInterleaving() {
    Emulator e;
    e.Execute({{0x1000, 1, false}, {0x2000, 2, false},
               {0x4000, 99, true}, {0x3000, 3, false}}, true);
    Require(e.submits == 2 && e.pending == 3,
            "FLUSH_CPU_BATCH_BEFORE_GPU_INLINE_FAULT");
    Require(e.events.size() == 2 &&
            e.events[0].tid == 99 && e.events[1].tid == 99,
            "GPU_FAULT_MUST_EXECUTE_INLINE_WITHOUT_WAITING_FOR_GPU_QUEUE");
    e.DrainGpu();
    Require(e.pending == 0 && e.events.size() == 8,
            "GPU_AND_ALL_CPU_FAULTS_COMPLETED");
    // CPU FIFO stays intact even when the GPU fault is handled immediately.
    const int tids[] = {1,2,3};
    for (int i = 0; i < 3; ++i) {
        Require(e.events[2+2*i].tid == tids[i] &&
                e.events[3+2*i].tid == tids[i],
                "CPU_FIFO_WITH_GPU_FAULT_INTERLEAVED");
    }
}
void TestDefaultModeUnchanged() {
    Emulator e;
    e.Execute({{0x1000, 10, false}, {0x2000, 11, false},
               {0x3000, 12, false}}, false);
    Require(e.submits == 3 && e.pending == 3, "DEFAULT_ONE_CALLBACK_PER_FAULT");
    e.DrainGpu();
    Require(e.pending == 0 && e.events.size() == 6,
            "DEFAULT_COMPLETION_AND_SHUTDOWN_COUNTER");
}
void TestSingleFaultAndBadPrefix() {
    Emulator e;
    e.Execute({{0x1000, 10, false}}, true);
    Require(e.submits == 1 && e.pending == 1, "SINGLE_FAULT_LEGACY_BEHAVIOR");
    e.DrainGpu();
    Require(e.pending == 0, "SINGLE_FAULT_WAKE_ON_GPU");
    // Even if a later message fails validation, prior valid CPU events are
    // dispatched before handler termination (represented as the valid prefix).
    Emulator prefix;
    prefix.Execute({{0x8000, 20, false}, {0x9000, 21, false}}, true);
    prefix.DrainGpu();
    Require(prefix.pending == 0 && prefix.events.size() == 4,
            "VALID_PREFIX_NOT_STRANDED");
}
int main() {
    TestBundledRead();
    TestGpuInterleaving();
    TestDefaultModeUnchanged();
    TestSingleFaultAndBadPrefix();
    std::puts("UFFD_GPU_CALLBACK_BATCH_CONTRACT=PASS cases=4");
}
