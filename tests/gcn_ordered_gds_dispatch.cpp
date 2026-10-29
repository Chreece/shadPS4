#include "shader_recompiler/frontend/ordered_gds_dispatch.h"

#include <cassert>
#include <cstdint>
#include <iostream>
#include <limits>
#include <set>

using namespace Shader::Gcn::OrderedGds;

int main() {
    const auto basic = DispatchLayout::Create({1, 1, 1}, {64, 1, 1});
    assert(basic && basic->TotalSlots() == 1 && basic->WavesPerGroup() == 1);
    assert(basic->LogicalSlot({0, 0, 0}, 0) == 0);
    assert(!basic->LogicalSlot({0, 0, 0}, 1));
    assert(!basic->Unflatten(1));

    const auto irregular = DispatchLayout::Create({4, 3, 2}, {5, 7, 3});
    assert(irregular && irregular->WavesPerGroup() == 2 &&
           irregular->TotalSlots() == 48);
    std::set<std::uint64_t> slots;
    for (std::uint32_t z = 0; z < 2; ++z) {
        for (std::uint32_t y = 0; y < 3; ++y) {
            for (std::uint32_t x = 0; x < 4; ++x) {
                for (std::uint32_t wave = 0; wave < 2; ++wave) {
                    const auto key = irregular->LogicalSlot({x, y, z}, wave);
                    assert(key && slots.insert(*key).second);
                    const auto inverse = irregular->Unflatten(*key);
                    assert(inverse && inverse->group.x == x &&
                           inverse->group.y == y && inverse->group.z == z &&
                           inverse->wave_in_group == wave);
                }
            }
        }
    }
    assert(slots.size() == irregular->TotalSlots());
    assert(irregular->LogicalSlot({0, 1, 0}, 0) == 8);
    assert(!irregular->LogicalSlot({4, 0, 0}, 0));
    assert(!irregular->LogicalSlot({0, 3, 0}, 0));
    assert(!irregular->LogicalSlot({0, 0, 2}, 0));

    const auto host_wave32 = DispatchLayout::Create({4, 1, 1}, {65, 1, 1}, 32);
    assert(host_wave32 && host_wave32->WavesPerGroup() == 3);
    const auto guest_wave64 = DispatchLayout::Create({4, 1, 1}, {65, 1, 1}, 64);
    assert(guest_wave64 && guest_wave64->WavesPerGroup() == 2);
    assert(!DispatchLayout::Create({0, 1, 1}, {64, 1, 1}));
    assert(!DispatchLayout::Create({1, 1, 1}, {0, 1, 1}));
    assert(!DispatchLayout::Create({1, 1, 1}, {1, 1, 1}, 0));
    assert(!DispatchLayout::Create({1, 1, 1}, {1, 1, 1}, 64, 0));
    assert(!DispatchLayout::Create({10, 10, 10}, {64, 1, 1}, 64, 999));
    assert(!DispatchLayout::Create({std::numeric_limits<std::uint32_t>::max(),
                                    std::numeric_limits<std::uint32_t>::max(),
                                    std::numeric_limits<std::uint32_t>::max()},
                                   {64, 1, 1}));

    std::cout << "ORDERED_GDS_LOGICAL_CAPTURE_SLOTS=PASS cases=invalid,3d,ceil,overflow,"
                 "wave32_vs_wave64,roundtrip; NOT_WAVE_CREATION_ORDER NO_GPU_WORK\n";
}
