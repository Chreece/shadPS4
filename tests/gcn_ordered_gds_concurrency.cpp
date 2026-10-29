#include "shader_recompiler/frontend/ordered_gds.h"

#include <array>
#include <atomic>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <map>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>

using namespace Shader::Gcn::OrderedGds;

// The snapshot returned by the collector must never be a reference to its
// mutable map. The resolver can write that map while another thread reads it.
static_assert(!std::is_reference_v<
              decltype(std::declval<const CompleteWaveCollector&>().Counters())>);


struct ObserverArgs {
    CompleteWaveCollector* collector;
    const std::atomic<bool>* observing;
    std::atomic<bool>* started;
    std::atomic<u32>* samples;
    CounterKey key;
    u32 initial;
    u32 waves;
};

static void ObserveCounters(const ObserverArgs* args) {
    args->started->store(true, std::memory_order_release);
    while (args->observing->load(std::memory_order_acquire)) {
        const auto snapshot = args->collector->Counters();
        const auto it = snapshot.find(args->key);
        assert(it != snapshot.end());
        assert(it->second == args->initial || it->second == args->initial + args->waves);
        args->samples->fetch_add(1, std::memory_order_relaxed);
        std::this_thread::yield();
    }
}

struct ProducerArgs {
    CompleteWaveCollector* collector;
    Descriptor instruction;
    u32 slot;
    u32 waves;
};

static void SubmitWaves(const ProducerArgs* args) {
    // Do not equate host arrival order with guest creation order.
    for (u32 wave = args->slot; wave < args->waves; wave += 4) {
        args->collector->Submit({
            .creation_sequence = wave,
            .runtime_m0 = u32{0x20} << 16,
            .payload = 1,
            .instruction = args->instruction,
        });
    }
}

int main() {
    const u64 encoded = (u64{11} << 56) | (u64{9} << 32) |
                        (u64{0x36} << 26) | (u64{63} << 18) |
                        (u64{1} << 17) | (u64{0x03} << 8);
    const auto decoded = DecodeInstruction(encoded);
    assert(decoded && decoded->operation == Operation::Add);

    constexpr u32 kWaves = 256;
    constexpr u32 kInitial = 100;
    constexpr CounterKey kCounter{0x20, 0};
    for (u32 run = 0; run < 32; ++run) {
        CompleteWaveCollector collector(kWaves, {{kCounter, kInitial}});
        std::atomic<bool> observing{true};
        std::atomic<bool> started{false};
        std::atomic<u32> samples{0};

        ObserverArgs observer_args{&collector, &observing, &started, &samples,
                                   kCounter, kInitial, kWaves};
        std::thread observer(&ObserveCounters, &observer_args);

        while (!started.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }
        const auto initial_snapshot = collector.Counters();
        assert(initial_snapshot.at(kCounter) == kInitial);
        std::array<ProducerArgs, 4> producer_args{};
        std::vector<std::thread> submitters;
        for (u32 slot = 0; slot < producer_args.size(); ++slot) {
            producer_args[slot] = ProducerArgs{&collector, *decoded, slot, kWaves};
            submitters.emplace_back(&SubmitWaves, &producer_args[slot]);
        }
        for (auto& submitter : submitters) {
            submitter.join();
        }
        assert(collector.Complete());
        const auto completed = collector.Resolve();
        assert(completed.size() == kWaves);
        assert(completed.front().previous == kInitial);
        assert(completed.back().current == kInitial + kWaves);
        assert(collector.Counters().at(kCounter) == kInitial + kWaves);
        observing.store(false, std::memory_order_release);
        observer.join();
        assert(samples.load(std::memory_order_relaxed) > 0);
        // A pre-resolve reader owns its snapshot; no dangling iterator,
        // stale view, or post-return mutation is permitted.
        assert(initial_snapshot.at(kCounter) == kInitial);
    }
    std::cout << "ORDERED_GDS_CONCURRENT_SNAPSHOT=PASS runs=32 waves=256 "
                 "producers=4 snapshot_copy=1 no_gpu_work=1\n";
}
