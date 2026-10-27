// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cstdint>
#include <cstdlib>
#include <iterator>
#include <limits>
#include <map>
#include <iostream>
#include "core/mapped_guest_range.h"

struct Area {
    std::uint64_t base, size;
    bool mapped;
    bool IsMapped() const noexcept { return mapped; }
};
using Areas = std::map<std::uint64_t, Area>;

static void Check(const char* name, const Areas& areas, std::uint64_t address,
                  std::uint64_t request, std::uint64_t expected) {
    const auto found = areas.upper_bound(address);
    const auto it = found == areas.begin() ? areas.end() : std::prev(found);
    const auto actual =
        Core::ClampContiguousMappedRange(address, request, it, areas.end());
    if (actual != expected) {
        std::cerr << "VMA_RANGE_FAIL=" << name << " expected=" << expected
                  << " actual=" << actual << "\n";
        std::exit(1);
    }
    std::cout << "VMA_RANGE_PASS=" << name << "\n";
}

int main() {
    Check("within_single_mapping", {{0x1000,{0x1000,0x1000,true}}},0x1100,0x200,0x200);
    Check("contiguous_split_mapping", {{0x1000,{0x1000,0x1000,true}},
          {0x2000,{0x2000,0x2000,true}}},0x1800,0x2800,0x2800);
    Check("not_cross_distinct_usable_region", {{0x1000,{0x1000,0x1000,true}},
          {0x5000,{0x5000,0x1000,true}}},0x1800,0x4000,0x800);
    Check("not_cross_free_mapping", {{0x1000,{0x1000,0x1000,true}},
          {0x2000,{0x2000,0x1000,false}},
          {0x3000,{0x3000,0x1000,true}}},0x1800,0x4000,0x800);
    Check("not_cross_gap_after_two_mapped", {{0x1000,{0x1000,0x1000,true}},
          {0x2000,{0x2000,0x1000,true}},
          {0x4000,{0x4000,0x1000,true}}},0x1800,0x5000,0x1800);
    Check("initial_unmapped_is_not_mapped",{{0x1000,{0x1000,0x1000,false}}},
          0x1000,0x100,0);
    Check("zero_size",{{0x1000,{0x1000,0x1000,true}}},0x1000,0,0);
    Check("outside_start",{{0x1000,{0x1000,0x1000,true}}},0x2000,0x100,0);
    Check("request_unbounded_stops_on_gap",{{0x1000,{0x1000,0x1000,true}},
          {0x5000,{0x5000,0x1000,true}}},0x1100,
          std::numeric_limits<std::uint64_t>::max(),0xf00);
    Check("near_max_no_overflow",{{0xffffffffffffe000ULL,
          {0xffffffffffffe000ULL,0x1000,true}}},0xfffffffffffff000ULL-1,
          std::numeric_limits<std::uint64_t>::max(),1);
}
