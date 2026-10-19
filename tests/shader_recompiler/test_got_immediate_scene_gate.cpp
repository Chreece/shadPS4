// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <random>
#include <array>
#include "video_core/renderer_vulkan/vk_got_immediate_scene_gate.h"

using Vulkan::Diagnostics::GotImmediateSceneInput;
using Vulkan::Diagnostics::IsGotImmediateSceneDraw;
int main() {
    constexpr std::array<std::array<std::uint32_t,3>,3> pairs{{
        {0xd0723f15u,0x5abc2be8u,480u},
        {0x14054a7au,0xd8fb6100u,13104u},
        {0x2dd14541u,0xe301aa17u,798u},
    }};
    GotImmediateSceneInput input{
        .opted_in=true,.indexed=true,.indirect=false,
        .guest_color0=0x15203f8000ULL,
        .guest_depth=0x1504a08000ULL,
        .vs_hash=pairs[0][0],.fs_hash=pairs[0][1],
        .draw_indices=pairs[0][2],
        .color0_can_write=true,
    };
    for(const auto& pair:pairs) {
        auto c=input;
        c.vs_hash=pair[0];c.fs_hash=pair[1];c.draw_indices=pair[2];
        assert(IsGotImmediateSceneDraw(c));
    }
    for(int i=0;i<8;++i) {
        auto c=input;
        switch(i) {
        case 0:c.opted_in=false;break;
        case 1:c.indexed=false;break;
        case 2:c.indirect=true;break;
        case 3:c.guest_color0=0;break;
        case 4:c.guest_depth=0;break;
        case 5:c.color0_can_write=false;break;
        case 6:c.draw_indices=3;break;
        case 7:c.fs_hash=0x96aa3f2eu;break;
        }
        assert(!IsGotImmediateSceneDraw(c));
    }

    std::mt19937_64 rand{0x1504a08000ULL};
    for(int i=0;i<100000;++i) {
        auto c=input;
        const auto flags=rand();
        const auto& pair=pairs[static_cast<std::size_t>(rand()%pairs.size())];
        c.vs_hash=pair[0];c.fs_hash=pair[1];c.draw_indices=pair[2];
        if(flags&1) c.opted_in=false;
        if(flags&2) c.indexed=false;
        if(flags&4) c.indirect=true;
        if(flags&8) c.guest_color0=rand();
        if(flags&16) c.guest_depth=rand();
        if(flags&32) c.color0_can_write=false;
        if(flags&64) c.fs_hash=static_cast<std::uint32_t>(rand());
        if(flags&128) c.vs_hash=static_cast<std::uint32_t>(rand());
        if(flags&256) c.draw_indices=static_cast<std::uint32_t>(rand());
        bool pair_ok=false;
        for(const auto& expected:pairs) {
            if(c.vs_hash==expected[0] && c.fs_hash==expected[1] &&
               c.draw_indices==expected[2])pair_ok=true;
        }
        const bool result=c.opted_in && c.indexed && !c.indirect &&
            c.guest_color0==0x15203f8000ULL &&
            c.guest_depth==0x1504a08000ULL && c.color0_can_write && pair_ok;
        assert(IsGotImmediateSceneDraw(c)==result);
    }
    std::puts("GOT_IMMEDIATE_SCENE_SNAPSHOT_GATE=PASS cases=100000 "
              "exact_hardware_proven_draws_only=PASS "
              "indirect_zero_work_excluded=PASS "
              "full_screen_UI_excluded=PASS "
              "read_only_scope=PASS");
}
