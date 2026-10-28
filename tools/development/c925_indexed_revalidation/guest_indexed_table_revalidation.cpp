// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "shader_recompiler/guest_tsharp_array.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <vector>
using Shader::Optimization::GuestIndexedTableState;
using Shader::Optimization::MakeGuestIndexedTableState;
using Shader::Optimization::GuestTsharpIndexOrNull;
#define CHECK(x) do { ++checks; if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); return 1; }} while (0)
constexpr auto material(std::uint32_t records, std::uint32_t offset=144,
                        std::uint32_t limit=100, bool available=true) {
    return MakeGuestIndexedTableState(available,8,records,536,offset,limit);
}
static_assert(material(0).tracked && material(0).entries==0);
static_assert(material(67).entries==1);
static_assert(material(134).entries==2);
static_assert(material(0)!=material(67));
static_assert(material(67)!=material(134));
static_assert(material(134)!=material(0));
static_assert(material(67)==material(67));
static_assert(material(67,228)==material(67,400));
static_assert(material(67,508).entries==0);
static_assert(material(67,505).entries==0);
static_assert(material(0,144,100,false).tracked);
static_assert(material(67,144,100,false).entries==0);
static_assert(!material(67,144,0).tracked);
static_assert(material(66).entries==0 && material(68).entries==0);
static_assert(material(134,144,1).entries==0);
int main() {
    unsigned checks=0;
    // This is the key used unconditionally before the old image-bound bit check.
    const std::vector<GuestIndexedTableState> empty{material(0)};
    const std::vector<GuestIndexedTableState> one{material(67)};
    const std::vector<GuestIndexedTableState> two{material(134)};
    CHECK(empty!=one); CHECK(one!=two); CHECK(two!=empty); CHECK(one==one);
    CHECK(empty==std::vector<GuestIndexedTableState>{material(0)});
    CHECK(empty!=std::vector<GuestIndexedTableState>{});
    CHECK(GuestTsharpIndexOrNull(0,0)==0);
    CHECK(GuestTsharpIndexOrNull(0,1)==0);
    CHECK(GuestTsharpIndexOrNull(1,1)==1);
    for (std::uint32_t count=0;count<=128;++count) {
        for (std::uint32_t offset: {0u,144u,228u,400u,504u}) {
            const auto state=material(count*67u,offset,128);
            CHECK(state.tracked); CHECK(state.entries==count);
        }
    }
    for (std::uint32_t count=1;count<=128;++count) {
        CHECK(material(count*67u-1u,144,128).entries==0);
        CHECK(material(count*67u+1u,144,128).entries==0);
    }
    // Preserve complete-table and capacity rules using a reference calculation.
    std::uint32_t rng=0x12345678u;
    for(unsigned i=0;i<30000;++i) {
        rng=rng*1664525u+1013904223u;const auto stride=rng&0x3fffu;
        rng=rng*1664525u+1013904223u;const auto records=rng;
        const std::uint64_t bytes=(stride==0?1ull:std::uint64_t(stride))*records;
        const auto got=MakeGuestIndexedTableState(true,stride,records,0,0,32768);
        const auto expected=(bytes>=32 && bytes%32==0 && bytes/32<=32768)
            ? static_cast<std::uint32_t>(bytes/32):0u;
        CHECK(got.tracked); CHECK(got.entries==expected);
        CHECK(MakeGuestIndexedTableState(false,stride,records,0,0,32768).entries==0);
    }
    CHECK(MakeGuestIndexedTableState(true,16383,std::numeric_limits<std::uint32_t>::max(),536,144,32768).entries==0);
    CHECK(MakeGuestIndexedTableState(true,8,4,0,0,1).entries==1);
    CHECK(MakeGuestIndexedTableState(true,0,32,0,0,1).entries==1);
    CHECK(MakeGuestIndexedTableState(true,32,1,0,0,1).entries==1);
    CHECK(MakeGuestIndexedTableState(true,8,0,0,0,32768).entries==0);
    std::printf("INDEXED_TABLE_REVALIDATION_TEST=PASS checks=%u\n",checks);
}
