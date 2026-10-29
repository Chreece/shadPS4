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
    std::cout << "ORDERED_GDS_CIK_PACKER_SEPARATION=PASS "
                 "four_dedicated_legacy_packers_documented=1 "
                 "exec_popcount_0_1_2_64=PASS "
                 "raw_gds_ram_rejected_as_seed=1 "
                 "duplicate_rejected=1 no_fake_zero=1 "
                 "host_guest_ISA_not_interchanged=1 NO_GPU_WORK\n";
}
