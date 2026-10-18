// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <random>
#include "video_core/renderer_vulkan/vk_got_scene_depth_ab.h"

using Vulkan::Diagnostics::GotSceneDepthABInput;
using Vulkan::Diagnostics::ShouldForceGotSceneCullNone;

int main() {
    GotSceneDepthABInput original{
        .opted_in=true,.guest_color_address=0x15203f8000ULL,
        .guest_depth_address=0x1504a08000ULL,.index_count=13104,
        .color_write_mask=0xf,.depth_test=true,.depth_write=true,
        .depth_clear=false,.raster_discard=false,
    };
    assert(ShouldForceGotSceneCullNone(original));
    for (unsigned bit=0;bit<8;++bit) {
        auto item=original;
        switch (bit) {
        case 0:item.opted_in=false;break;
        case 1:item.guest_color_address=0x1522020000ULL;break;
        case 2:item.guest_depth_address=0x1504210000ULL;break;
        case 3:item.index_count=3;break;
        case 4:item.color_write_mask=0;break;
        case 5:item.depth_test=false;break;
        case 6:item.depth_write=false;break;
        case 7:item.raster_discard=true;break;
        }
        assert(!ShouldForceGotSceneCullNone(item));
    }
    {
        auto v=original;v.depth_clear=true;
        assert(!ShouldForceGotSceneCullNone(v));
    }
    {
        auto v=original;v.index_count=128;
        assert(ShouldForceGotSceneCullNone(v));
    }
    std::mt19937_64 rng(0x1504a08000ULL);
    for(int n=0;n<100000;++n) {
        auto v=original;
        const auto changes=rng();
        if(changes&1) v.opted_in=false;
        if(changes&2) v.guest_color_address=rng();
        if(changes&4) v.guest_depth_address=rng();
        if(changes&8) v.index_count=static_cast<std::uint32_t>(rng()%128u);
        if(changes&16) v.color_write_mask=0;
        if(changes&32) v.depth_test=false;
        if(changes&64) v.depth_write=false;
        if(changes&128) v.depth_clear=true;
        if(changes&256) v.raster_discard=true;
        const bool expected=
            v.opted_in &&
            v.guest_color_address==original.guest_color_address &&
            v.guest_depth_address==original.guest_depth_address &&
            v.index_count>=128 && v.color_write_mask!=0 &&
            v.depth_test && v.depth_write && !v.depth_clear &&
            !v.raster_discard;
        assert(ShouldForceGotSceneCullNone(v)==expected);
    }
    std::puts("GOT_SCENE_RASTER_AB=PASS cases=100000 exact_depth_color_pair=PASS "
              "no_ui_or_fullscreen=PASS no_shadows=PASS disabled_default_safe=PASS");
}
