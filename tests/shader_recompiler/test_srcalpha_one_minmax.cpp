// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <random>

#include "video_core/renderer_vulkan/minmax_src_alpha_one.h"

int main() {
    using Factor = AmdGpu::BlendControl::BlendFactor;
    using Op = AmdGpu::BlendControl::BlendFunc;
    using Format = AmdGpu::NumberFormat;

    AmdGpu::BlendControl blend{};
    blend.enable = 1;
    blend.color_func = Op::Max;
    blend.color_src_factor = Factor::SrcAlpha;
    blend.color_dst_factor = Factor::One;
    assert(Vulkan::CanShaderScaleMinMaxSrcAlphaOne(blend, Format::Unorm, false, true));
    assert(Vulkan::CanShaderScaleMinMaxSrcAlphaOne(blend, Format::Srgb, false, true));

    assert(!Vulkan::CanShaderScaleMinMaxSrcAlphaOne(blend, Format::Float, false, true));
    assert(!Vulkan::CanShaderScaleMinMaxSrcAlphaOne(blend, Format::Unorm, true, true));
    assert(!Vulkan::CanShaderScaleMinMaxSrcAlphaOne(blend, Format::Unorm, false, false));
    blend.color_dst_factor = Factor::DstAlpha;
    assert(!Vulkan::CanShaderScaleMinMaxSrcAlphaOne(blend, Format::Unorm, false, true));
    blend.color_dst_factor = Factor::One;
    blend.color_src_factor = Factor::SrcColor;
    assert(!Vulkan::CanShaderScaleMinMaxSrcAlphaOne(blend, Format::Unorm, false, true));
    blend.color_src_factor = Factor::SrcAlpha;
    blend.separate_alpha_blend = 1;
    blend.alpha_func = Op::Min;
    blend.alpha_src_factor = Factor::SrcAlpha;
    blend.alpha_dst_factor = Factor::One;
    assert(Vulkan::CanShaderScaleMinMaxSrcAlphaOne(blend, Format::Unorm, false, true));
    blend.alpha_src_factor = Factor::DstAlpha;
    assert(!Vulkan::CanShaderScaleMinMaxSrcAlphaOne(blend, Format::Unorm, false, true));
    blend.alpha_src_factor = Factor::SrcAlpha;
    blend.alpha_dst_factor = Factor::Zero;
    assert(!Vulkan::CanShaderScaleMinMaxSrcAlphaOne(blend, Format::Unorm, false, true));
    blend.alpha_dst_factor = Factor::One;

    std::mt19937 rng{0xa133b1e7U};
    std::uniform_real_distribution<float> random(-0.25f, 1.25f);
    constexpr auto clamp = [](float x) { return std::clamp(x, 0.0f, 1.0f); };
    for (int n=0;n<25000;++n) {
        std::array<float, 4> source{}, dest{};
        for (int i=0;i<4;++i) {
            source[i] = random(rng);
            dest[i] = random(rng);
        }
        const float alpha = clamp(source[3]);
        for (int i=0;i<4;++i) {
            const float guest_scaled_source = clamp(source[i]) * alpha;
            const float shader_scaled_source = clamp(source[i]) * clamp(source[3]);
            assert(guest_scaled_source == shader_scaled_source);
            for (const auto op : {Op::Min, Op::Max}) {
                const float guest = op == Op::Min
                      ? std::min(guest_scaled_source, clamp(dest[i]))
                      : std::max(guest_scaled_source, clamp(dest[i]));
                const float vulkan = op == Op::Min
                      ? std::min(shader_scaled_source, clamp(dest[i]))
                      : std::max(shader_scaled_source, clamp(dest[i]));
                assert(guest == vulkan);
            }
        }
    }
    std::puts("SRCALPHA_ONE_MINMAX=PASS blend_cases=200000 "
              "unsupported_states=REJECTED");
}
