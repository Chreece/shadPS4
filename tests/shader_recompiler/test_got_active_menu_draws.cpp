// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cassert>
#include <cstdint>
#include <initializer_list>
#include <cstdio>
#include <random>

#include "video_core/renderer_vulkan/vk_got_active_menu_draws.h"

using namespace Vulkan::Diagnostics;

int main() {
    GotActiveMenuSceneDraw scene{
        .enabled=true,.indexed=true,.indirect=true,
        .color_attached=true,.depth_attached=true,
        .guest_depth_test=true,.guest_depth_write=true,
        .guest_depth_clear=false,.rasterizer_discard=false,
        .color_write_mask=15u,
        .color_address=0x15203f8000ULL,.depth_address=0x1504a08000ULL,
        .fragment_shader=0xc1123f0fu,.vertex_shader=0xc8caf263u,
        .guest_register_indices=3u,
    };
    using Kind=GotActiveMenuDrawKind;
    assert(ClassifyGotActiveMenuDraw(scene)==Kind::FrequentSceneIndirect);
    for(const std::uint32_t n:{4096u,4097u,8192u,8193u}) {
        assert(ShouldCaptureGotActiveMenuDraw(Kind::FrequentSceneIndirect,n));
    }
    for(const std::uint32_t n:{0u,1u,2u,32u,255u,256u,4095u,4098u,8191u,8194u}) {
        assert(!ShouldCaptureGotActiveMenuDraw(Kind::FrequentSceneIndirect,n));
    }
    scene.fragment_shader=0x5116e459u;
    scene.vertex_shader=0xae1cc7b7u;
    assert(ClassifyGotActiveMenuDraw(scene)==Kind::FrequentSceneIndirect);
    // The old num_indices>=128 gate incorrectly excluded these draws.
    scene.guest_register_indices=0;
    assert(ClassifyGotActiveMenuDraw(scene)==Kind::FrequentSceneIndirect);
    scene.guest_register_indices=13104;
    assert(ClassifyGotActiveMenuDraw(scene)==Kind::FrequentSceneIndirect);
    scene.fragment_shader=0xc1123f0fu;
    scene.vertex_shader=0xc8caf263u;
    scene.indirect=false;
    assert(ClassifyGotActiveMenuDraw(scene)==Kind::Unrelated);

    const std::array<std::array<std::uint32_t,2>,3> control{{
        {{0x5abc2be8u,0xd0723f15u}},
        {{0xd8fb6100u,0x14054a7au}},
        {{0xe301aa17u,0x2dd14541u}},
    }};
    for(const auto& pair:control) {
        scene.indirect=false;
        scene.fragment_shader=pair[0];
        scene.vertex_shader=pair[1];
        assert(ClassifyGotActiveMenuDraw(scene)==Kind::DirectControl);
        assert(ShouldCaptureGotActiveMenuDraw(Kind::DirectControl,1));
        assert(!ShouldCaptureGotActiveMenuDraw(Kind::DirectControl,2));
    }
    auto neg=scene;
    neg.enabled=false;assert(ClassifyGotActiveMenuDraw(neg)==Kind::Unrelated);
    neg=scene;neg.indexed=false;assert(ClassifyGotActiveMenuDraw(neg)==Kind::Unrelated);
    neg=scene;neg.guest_depth_write=false;assert(ClassifyGotActiveMenuDraw(neg)==Kind::Unrelated);
    neg=scene;neg.guest_depth_clear=true;assert(ClassifyGotActiveMenuDraw(neg)==Kind::Unrelated);
    neg=scene;neg.color_address=0x1522020000ULL;
    assert(ClassifyGotActiveMenuDraw(neg)==Kind::Unrelated);
    neg=scene;neg.depth_address=0x15203f8000ULL;
    assert(ClassifyGotActiveMenuDraw(neg)==Kind::Unrelated);

    // Deterministic fuzz: verify neither zero guest register counts nor
    // arbitrary hashes can accidentally broaden selection.
    std::mt19937_64 rng{0xc1123f0fc8caf263ULL};
    for(unsigned i=0;i<100000;++i) {
        GotActiveMenuSceneDraw v{
            .enabled=(rng()&3)!=0,.indexed=true,
            .indirect=true,.color_attached=true,.depth_attached=true,
            .guest_depth_test=true,.guest_depth_write=true,
            .guest_depth_clear=false,.rasterizer_discard=false,
            .color_write_mask=15,.color_address=0x15203f8000ULL,
            .depth_address=0x1504a08000ULL,
            .fragment_shader=0xc1123f0fu,.vertex_shader=0xc8caf263u,
            .guest_register_indices=static_cast<std::uint32_t>(rng()),
        };
        const auto changes=static_cast<std::uint32_t>(rng());
        if(changes&1u)v.indexed=false;
        if(changes&2u)v.color_attached=false;
        if(changes&4u)v.depth_attached=false;
        if(changes&8u)v.guest_depth_test=false;
        if(changes&16u)v.guest_depth_write=false;
        if(changes&32u)v.guest_depth_clear=true;
        if(changes&64u)v.rasterizer_discard=true;
        if(changes&128u)v.color_write_mask=0;
        if(changes&256u)v.color_address=rng();
        if(changes&512u)v.depth_address=rng();
        if(changes&1024u)v.fragment_shader=static_cast<std::uint32_t>(rng());
        if(changes&2048u)v.vertex_shader=static_cast<std::uint32_t>(rng());
        const bool expected=v.enabled&&v.indexed&&v.indirect&&
            v.color_attached&&v.depth_attached&&v.guest_depth_test&&
            v.guest_depth_write&&!v.guest_depth_clear&&!v.rasterizer_discard&&
            v.color_write_mask!=0&&
            v.color_address==0x15203f8000ULL&&
            v.depth_address==0x1504a08000ULL&&
            v.fragment_shader==0xc1123f0fu&&
            v.vertex_shader==0xc8caf263u;
        assert((ClassifyGotActiveMenuDraw(v)==Kind::FrequentSceneIndirect)==expected);
    }
    std::puts("GOT_ACTIVE_MENU_DRAW_SELECTION=PASS cases=100000 "
              "exact_indirect_shader_pairs=PASS "
              "guest_num_indices_ignored=PASS "
              "checkpoints_4096_8192=PASS "
              "direct_controls=PASS "
              "unrelated_pipeline_excluded=PASS");
}
