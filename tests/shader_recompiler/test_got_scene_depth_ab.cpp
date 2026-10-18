// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdio>
#include <random>
#include "video_core/renderer_vulkan/vk_got_scene_depth_ab.h"
using Vulkan::Diagnostics::GotSceneDepthABInput;
using Vulkan::Diagnostics::ShouldForceGotSceneDepthAlways;

int main() {
    GotSceneDepthABInput sample{
        .opted_in=true,
        .guest_color_address=0x15203f8000ULL,
        .guest_depth_address=0x1504a08000ULL,
        .index_count=13104,
        .color_write_mask=0xf,
        .depth_test=true,
        .depth_write=true,
        .depth_clear=false,
        .raster_discard=false,
    };
    assert(ShouldForceGotSceneDepthAlways(sample));
    auto test=[&](const GotSceneDepthABInput& other){
        assert(!ShouldForceGotSceneDepthAlways(other));
    };
    { auto v=sample;v.opted_in=false;test(v); }
    { auto v=sample;v.guest_color_address=0x1522020000ULL;test(v); }
    { auto v=sample;v.guest_color_address=0x1504210000ULL;test(v); }
    { auto v=sample;v.guest_color_address=0;test(v); }
    { auto v=sample;v.guest_depth_address=0x15203f8000ULL;test(v); }
    { auto v=sample;v.guest_depth_address=0;test(v); }
    { auto v=sample;v.index_count=3;test(v); }
    { auto v=sample;v.index_count=127;test(v); }
    { auto v=sample;v.index_count=128;assert(ShouldForceGotSceneDepthAlways(v)); }
    { auto v=sample;v.index_count=480;assert(ShouldForceGotSceneDepthAlways(v)); }
    { auto v=sample;v.color_write_mask=0;test(v); }
    { auto v=sample;v.depth_test=false;test(v); }
    { auto v=sample;v.depth_write=false;test(v); }
    { auto v=sample;v.depth_clear=true;test(v); }
    { auto v=sample;v.raster_discard=true;test(v); }

    std::mt19937_64 rng{0x1504a08000ULL};
    for (unsigned n=0;n<100000;++n) {
        auto v=sample;
        const auto mask=rng();
        if(mask&1) v.opted_in=false;
        if(mask&2) v.guest_color_address=rng();
        if(mask&4) v.guest_depth_address=rng();
        if(mask&8) v.index_count=static_cast<std::uint32_t>(rng()%128);
        if(mask&16) v.color_write_mask=0;
        if(mask&32) v.depth_test=false;
        if(mask&64) v.depth_write=false;
        if(mask&128) v.depth_clear=true;
        if(mask&256) v.raster_discard=true;
        const bool expected=
            v.opted_in &&
            v.guest_color_address==sample.guest_color_address &&
            v.guest_depth_address==sample.guest_depth_address &&
            v.index_count>=128 &&
            v.color_write_mask!=0 &&
            v.depth_test && v.depth_write &&
            !v.depth_clear && !v.raster_discard;
        assert(ShouldForceGotSceneDepthAlways(v)==expected);
    }
    std::puts("GOT_SCENE_DEPTH_AB=PASS cases=100000 "
              "exact_target_only=PASS original_mode_untouched=PASS "
              "fullscreen_and_shadow_passes_excluded=PASS "
              "requires_real_depth_geometry=PASS");
}
