// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <random>
#include <initializer_list>
#include <utility>
#include "video_core/renderer_vulkan/vk_got_scene_depth_clip_trial.h"

using Vulkan::Diagnostics::GotSceneDepthClipTrialInput;
using Vulkan::Diagnostics::ShouldTestGotSceneDepthClip;

int main() {
    GotSceneDepthClipTrialInput input{
        .opted_in=true,
        .depth_clip_extension_enabled=true,
        .depth_clamp_feature_enabled=true,
        .has_color_attachment=true,
        .has_depth_attachment=true,
        .vertex_shader=0x73fe0871u,
        .fragment_shader=0x4516d5e8u,
    };
    assert(ShouldTestGotSceneDepthClip(input));
    const std::uint32_t known_vs[]={
        0x73fe0871u,0x73fe0871u,0xf3022914u,
        0x20b6ed83u,0x20b6ed83u};
    const std::uint32_t known_fs[]={
        0x4516d5e8u,0x5127d09du,0x090be5c2u,
        0x85eb5e04u,0x326f181du};
    for(unsigned i=0;i<5;++i) {
        input.vertex_shader=known_vs[i];
        input.fragment_shader=known_fs[i];
        assert(ShouldTestGotSceneDepthClip(input));
    }
    input.vertex_shader=0x73fe0871u;
    input.fragment_shader=0x4516d5e8u;
    for(unsigned i=0;i<5;++i) {
        auto v=input;
        switch(i) {
        case 0:v.opted_in=false;break;
        case 1:v.depth_clip_extension_enabled=false;break;
        case 2:v.depth_clamp_feature_enabled=false;break;
        case 3:v.has_color_attachment=false;break;
        case 4:v.has_depth_attachment=false;break;
        }
        assert(!ShouldTestGotSceneDepthClip(v));
    }
    // Explicitly exclude the three scene shader pairs which ALREADY
    // produced fragment invocations and passed samples, the intentional
    // zero-index indirect draws and final fullscreen compositor.
    for(const auto& pair:{
            std::pair{0xd0723f15u,0x5abc2be8u},
            std::pair{0x14054a7au,0xd8fb6100u},
            std::pair{0x2dd14541u,0xe301aa17u},
            std::pair{0xae1cc7b7u,0x5116e459u},
            std::pair{0x0a894bb5u,0x96aa3f2eu}
        }) {
        auto v=input;
        v.vertex_shader=pair.first;
        v.fragment_shader=pair.second;
        assert(!ShouldTestGotSceneDepthClip(v));
    }
    std::mt19937 rng{0x73fe0871u};
    for(unsigned i=0;i<100000;++i) {
        auto v=input;
        const std::uint32_t changes=rng();
        if(changes&1u)v.opted_in=false;
        if(changes&2u)v.depth_clip_extension_enabled=false;
        if(changes&4u)v.depth_clamp_feature_enabled=false;
        if(changes&8u)v.has_color_attachment=false;
        if(changes&16u)v.has_depth_attachment=false;
        if(changes&32u)v.vertex_shader=rng();
        if(changes&64u)v.fragment_shader=rng();
        const bool enabled=v.opted_in&&v.depth_clip_extension_enabled&&
            v.depth_clamp_feature_enabled&&v.has_color_attachment&&
            v.has_depth_attachment;
        bool correct_pair=false;
        for(unsigned k=0;k<5;++k) {
            if(v.vertex_shader==known_vs[k]&&v.fragment_shader==known_fs[k])
                correct_pair=true;
        }
        assert(ShouldTestGotSceneDepthClip(v)==(enabled&&correct_pair));
    }
    std::puts("GOT_SCENE_DEPTH_CLIP_SCOPE=PASS cases=100000 "
              "five_failing_pairs_only=PASS "
              "successful_geometry_excluded=PASS "
              "indirect_zero_draws_excluded=PASS "
              "host_features_required=PASS "
              "default_disabled=PASS");
}
