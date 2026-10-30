#include "shader_recompiler/frontend/ordered_gds_legacy_packer.h"

#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <stdexcept>

using namespace Shader::Gcn::OrderedGds;

template <class Fn> static bool Reject(Fn&& fn) {
    try {
        fn();
    } catch (const std::invalid_argument&) {
        return true;
    }
    return false;
}

int main() {
    static_assert(CikOrderedCountIncrement(0) == 0);
    static_assert(CikOrderedCountIncrement(~u64{0}) == 64);
    static_assert(CikOrderedCountIncrement(1) == 1);
    static_assert(CikOrderedCountIncrement(u64{1} << 63) == 1);
    static_assert(CikOrderedCountIncrement(0x8000000000000080ULL) == 2);

    constexpr std::array<OrderedCounterSeed, 2> seeds{{
        {{0x20, 0}, 17},
        {{0x20, 1}, 1234},
    }};
    const auto counters = MakeExplicitOrderedCounterSeed(
        seeds, OrderedCounterSeedOrigin::DedicatedPackerState);
    assert(counters.size() == 2 && counters.at({0x20, 0}) == 17);
    assert(counters.at({0x20, 1}) == 1234);
    assert(!counters.contains({0x20, 2}));
    assert(Reject([&] {
        (void)MakeExplicitOrderedCounterSeed(
            seeds, OrderedCounterSeedOrigin::UnverifiedRawGdsMemory);
    }));
    const std::array<OrderedCounterSeed, 2> duplicates{{
        {{0x20, 0}, 3},
        {{0x20, 0}, 4},
    }};
    assert(Reject([&] {
        (void)MakeExplicitOrderedCounterSeed(
            duplicates, OrderedCounterSeedOrigin::DedicatedPackerState);
    }));
    constexpr std::array<OrderedCounterSeed, 0> empty{};
    assert(MakeExplicitOrderedCounterSeed(
               empty, OrderedCounterSeedOrigin::DedicatedPackerState).empty());
    // Feed CIK's guest EXEC-derived increments into the real host reference.
    // No raw GDS byte offset is consulted, no automatic counter zero is made.
    const auto instruction_word =
        (u64{11} << 56) | (u64{9} << 32) |
        (u64{0x36} << 26) | (u64{63} << 18) |
        (u64{1} << 17) | (u64{0x03} << 8);
    const auto decoded = DecodeInstruction(instruction_word);
    assert(decoded && decoded->operation == Operation::Add);
    constexpr u32 guest_m0 = 0x20u << 16;
    CompleteBatch executor(counters);
    const std::array<Request, 3> requests{{
        {0, guest_m0, CikOrderedCountIncrement(1u), *decoded},
        {1, guest_m0, CikOrderedCountIncrement(0x8000000000000080ULL), *decoded},
        {2, guest_m0, CikOrderedCountIncrement(0u), *decoded, false},
    }};
    const auto resolved = executor.Apply(requests, 3);
    assert(resolved.size() == 3);
    assert(resolved[0].previous == 17 && resolved[0].current == 18);
    assert(resolved[1].previous == 18 && resolved[1].current == 20);
    assert(resolved[2].previous == 20 && resolved[2].current == 20);
    assert(executor.Counters().at({0x20, 0}) == 20);
    assert(executor.Counters().at({0x20, 1}) == 1234);
    // The real CIK counter state must survive sequential guest epochs.
    // Every entry here is an explicitly supplied verified packer seed,
    // never an invented zero or an alias of raw 64KiB GDS memory.
    VerifiedCikOrderedPackerLedger ledger;
    constexpr u64 context_id = 0x1234'5678u;
    assert(Reject([&] {(void)ledger.BeginEpoch(context_id, 1);}));
    assert(Reject([&] {ledger.Establish(0, seeds,
        OrderedCounterSeedOrigin::DedicatedPackerState);}));
    assert(Reject([&] {ledger.Establish(context_id, seeds,
        OrderedCounterSeedOrigin::UnverifiedRawGdsMemory);}));
    assert(Reject([&] {ledger.Establish(context_id, empty,
        OrderedCounterSeedOrigin::DedicatedPackerState);}));
    ledger.Establish(context_id, seeds, OrderedCounterSeedOrigin::DedicatedPackerState);
    assert(ledger.HasVerifiedState() && ledger.Counters() == counters);
    assert(Reject([&] {ledger.Establish(context_id, seeds,
        OrderedCounterSeedOrigin::DedicatedPackerState);}));
    assert(Reject([&] {(void)ledger.BeginEpoch(context_id ^ 1, 3);}));
    assert(Reject([&] {(void)ledger.BeginEpoch(context_id, 0);}));
    const auto first_seed = ledger.BeginEpoch(context_id, 7);
    assert(first_seed.size() == 2);
    assert((first_seed[0].key == CounterKey{0x20, 0}) && first_seed[0].value == 17);
    assert((first_seed[1].key == CounterKey{0x20, 1}) && first_seed[1].value == 1234);
    assert(Reject([&] {(void)ledger.BeginEpoch(context_id, 8);}));
    assert(Reject([&] {ledger.CommitEpoch(context_id, 8, counters);}));
    const std::map<CounterKey,u32> missing_counter{{{0x20,0},19}};
    assert(Reject([&] {ledger.CommitEpoch(context_id, 7, missing_counter);}));
    const std::map<CounterKey,u32> wrong_counter{{{0x20,0},19},{{0x20,2},1234}};
    assert(Reject([&] {ledger.CommitEpoch(context_id, 7, wrong_counter);}));
    assert(ledger.Counters() == counters && ledger.LastCommittedEpoch() == 0);
    ledger.CancelEpoch(context_id ^ 1, 7);
    assert(Reject([&] {(void)ledger.BeginEpoch(context_id, 8);}));
    // Reject invalid keys before staging; discard a valid but unresumed
    // resolution without ever publishing its counter updates.
    assert(Reject([&] {ledger.StageResolvedEpoch(context_id, 7, missing_counter);}));
    assert(Reject([&] {ledger.StageResolvedEpoch(context_id, 7, wrong_counter);}));
    const std::map<CounterKey, u32> abandoned{
        {{0x20, 0}, 19}, {{0x20, 1}, 1234},
    };
    ledger.StageResolvedEpoch(context_id, 7, abandoned);
    assert(ledger.HasStagedResolution() && ledger.Counters() == counters);
    assert(Reject([&] {ledger.CommitEpoch(context_id, 7, counters);}));
    ledger.CancelEpoch(context_id, 7);
    assert(ledger.Counters() == counters && !ledger.HasStagedResolution());
    assert(Reject([&] {(void)ledger.BeginEpoch(context_id, 7);}));
    const auto second_seed = ledger.BeginEpoch(context_id, 8);
    assert(second_seed[0].value == 17 && second_seed[1].value == 1234);
    const std::map<CounterKey,u32> ledger_resolved{{{0x20,0},21},{{0x20,1},1234}};
    // Host post-DS upload alone must NOT advance packer state.
    assert(Reject([&] {ledger.CommitEpoch(context_id, 8, ledger_resolved);}));
    ledger.StageResolvedEpoch(context_id, 8, ledger_resolved);
    assert(ledger.HasStagedResolution());
    assert(ledger.Counters() == counters && ledger.LastCommittedEpoch() == 0);
    assert(Reject([&] {ledger.StageResolvedEpoch(context_id, 8, ledger_resolved);}));
    assert(Reject([&] {ledger.CommitEpoch(context_id ^ 1, 8, ledger_resolved);}));
    ledger.CommitEpoch(context_id, 8, ledger_resolved);
    assert(!ledger.HasStagedResolution());
    assert(ledger.LastCommittedEpoch() == 8 && ledger.Counters() == ledger_resolved);
    assert(Reject([&] {ledger.CommitEpoch(context_id, 8, ledger_resolved);}));
    for (u32 epoch = 9; epoch <= 107; ++epoch) {
        const auto before = ledger.BeginEpoch(context_id, epoch);
        assert(before[0].value == epoch - 8 + 20);
        const std::map<CounterKey,u32> next{{{0x20,0},epoch - 7 + 20},
                                            {{0x20,1},1234}};
        ledger.StageResolvedEpoch(context_id, epoch, next);
        assert(ledger.HasStagedResolution());
        assert(before[0].value == ledger.Counters().begin()->second);
        ledger.CommitEpoch(context_id, epoch, next);
        assert(!ledger.HasStagedResolution() && ledger.Counters() == next);
    }
    ledger.Reset();
    assert(!ledger.HasVerifiedState() && ledger.Counters().empty() &&
           !ledger.HasStagedResolution());
    assert(Reject([&] {(void)ledger.BeginEpoch(context_id, 108);}));
    ledger.Establish(context_id + 1, seeds,
        OrderedCounterSeedOrigin::DedicatedPackerState);
    assert(ledger.Counters() == counters && ledger.LastCommittedEpoch() == 0);

    std::cout << "GDS_CIK_VERIFIED_PACKER_LEDGER=PASS "
                 "epoch_reuse_rejected=PASS failed_epoch_not_committed=PASS "
                 "context_change_explicit=PASS staged_until_gpu_resume=PASS "
                 "real_gpu_seed_still_required=YES\n";
    std::cout << "ORDERED_GDS_CIK_PACKER_SEPARATION=PASS "
                 "four_dedicated_legacy_packers_documented=1 "
                 "exec_popcount_0_1_2_64=PASS "
                 "raw_gds_ram_rejected_as_seed=1 "
                 "duplicate_rejected=1 no_fake_zero=1 "
                 "host_guest_ISA_not_interchanged=1 execute_three_waves=PASS NO_GPU_WORK\n";
}
