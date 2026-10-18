// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <random>
#include "shader_recompiler/strided_material_specialization.h"

using Shader::Optimization::StridedMaterialPermutationEntries;

constexpr std::uint32_t records(bool fetched, bool valid, std::uint64_t addr,
                                std::uint32_t stride, std::uint32_t count,
                                std::uint32_t tex_offset) {
    return StridedMaterialPermutationEntries(
        fetched, valid, addr, stride, count, 536u, tex_offset, 32600u);
}
static_assert(records(true,true,0x1500000000ull,8u,0u,144u)==0u);
static_assert(records(true,true,0x150063cd00ull,536u,97u,144u)==97u);
static_assert(records(true,true,0x150063cd00ull,536u,97u,228u)==97u);
static_assert(records(true,true,0x150063cd00ull,536u,97u,260u)==97u);
static_assert(records(true,true,0x150063cd00ull,536u,97u,292u)==97u);
static_assert(records(true,true,0x150063cd00ull,536u,97u,492u)==97u);
static_assert(records(true,true,0x150063cd00ull,536u,97u,144u)
           ==records(true,true,0x1501234500ull,536u,97u,144u));
static_assert(records(true,true,0,536u,97u,144u)==0u);
static_assert(records(false,true,0x150063cd00ull,536u,97u,144u)==0u);
static_assert(records(true,false,0x150063cd00ull,536u,97u,144u)==0u);
static_assert(records(true,true,0x150063cd00ull,532u,97u,144u)==0u);
static_assert(records(true,true,0x150063cd00ull,536u,0u,144u)==0u);
static_assert(records(true,true,0x150063cd00ull,536u,97u,508u)==0u);
static_assert(records(true,true,0x150063cd00ull,536u,97u,145u)==0u);
static_assert(records(true,true,0x150063cd00ull,536u,32601u,144u)==0u);
static_assert(StridedMaterialPermutationEntries(true,true,0x150063cd00ull,
                         0u,536u*97u,536u,144u,32600u)==97u);

int main() {
    // 5 + 11 resource observations from the actual GoT archive use
    // the same table pointer but different descriptor offsets.
    constexpr std::array<std::uint32_t,5> first{144u,228u,260u,292u,192u};
    constexpr std::array<std::uint32_t,11> second{
        144u,400u,324u,324u,144u,192u,228u,260u,292u,492u,364u};
    for(const auto off:first) {
        assert(records(true,true,0x1500000000ull,8u,0u,off)==0u);
        assert(records(true,true,0x150063cd00ull,536u,97u,off)==97u);
    }
    for(const auto off:second) {
        assert(records(true,true,0x1500000000ull,8u,0u,off)==0u);
        assert(records(true,true,0x150063cd00ull,536u,97u,off)==97u);
    }
    std::mt19937 rng{0x53697u};
    for(unsigned iter=0;iter<100000;++iter) {
        const std::uint32_t count=1u+(rng()%300u);
        const std::uint32_t offset=(rng()%127u)*4u;
        const std::uint64_t addr=0x1500000000ull+(rng()%4096u)*256u;
        const std::uint32_t expected=offset+32u<=536u?count:0u;
        assert(records(true,true,addr,536u,count,offset)==expected);
        assert(records(true,true,addr,8u,0u,offset)==0u);
    }
    std::puts("GOT_STRIDED_PERMUTATION=PASS cases=100000 "
              "transition_zero_to_97=PASS both_target_shaders=PASS "
              "invalid_sources_fail_closed=PASS address_independent=PASS");
}
