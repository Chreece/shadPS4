#include "shader_recompiler/frontend/ordered_gds_storage_layout.h"

#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>

using namespace Shader::Gcn::OrderedGds;

template<class Callback> bool Reject(Callback&& call) {
    try {
        call();
    } catch (const std::invalid_argument&) {
        return true;
    }
    return false;
}

int main() {
    static_assert(GdsPhysicalBytes == 65536);
    static_assert(GdsPhysicalDwords == 16384);
    const std::array<PhysicalGdsDwordWrite, 0> none{};
    ValidatePhysicalGdsWrites(none);
    const std::array<PhysicalGdsDwordWrite,2> endpoints{{
        {0, 0xdeadbeefu}, {GdsPhysicalBytes - 4, 0xfedcba98u},
    }};
    ValidatePhysicalGdsWrites(endpoints);
    const std::array<PhysicalGdsDwordWrite,2> unsorted{{
        {GdsPhysicalBytes - 4, 19}, {0, 20},
    }};
    ValidatePhysicalGdsWrites(unsorted);
    const std::array<PhysicalGdsDwordWrite,2> duplicate{{
        {8, 1}, {8, 2},
    }};
    assert(Reject([&]{ValidatePhysicalGdsWrites(duplicate);}));
    const std::array<PhysicalGdsDwordWrite,1> unaligned{{{3, 7}}};
    assert(Reject([&]{ValidatePhysicalGdsWrites(unaligned);}));
    const std::array<PhysicalGdsDwordWrite,1> overflow{{{65536, 7}}};
    assert(Reject([&]{ValidatePhysicalGdsWrites(overflow);}));
    const std::array<PhysicalGdsDwordWrite,1> near_end{{{65535, 7}}};
    assert(Reject([&]{ValidatePhysicalGdsWrites(near_end);}));
    std::vector<PhysicalGdsDwordWrite> all;
    all.reserve(GdsPhysicalDwords + 1);
    for(std::uint32_t i = 0; i < GdsPhysicalDwords; ++i) {
        all.emplace_back(i * 4u, i ^ 0x5a5au);
    }
    ValidatePhysicalGdsWrites(all);
    all.emplace_back(0, 100);
    assert(Reject([&]{ValidatePhysicalGdsWrites(all);}));
    std::cout << "ORDERED_GDS_PHYSICAL_STORAGE_BOUNDS=PASS bytes=65536 dwords=16384"
                 " empty=1 endpoints=1 unsorted=1 duplicate_rejected=1"
                 " alignment=1 overflow=1 exact_capacity=1"
                 " no_guessed_gds_counter_mapping=1 NO_GPU_WORK\n";
}
