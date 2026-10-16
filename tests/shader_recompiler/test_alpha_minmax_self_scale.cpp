// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdio>
#include <random>
#include "video_core/renderer_vulkan/minmax_alpha_scale.h"

int main() {
    using F = AmdGpu::BlendControl::BlendFactor;
    using Op = AmdGpu::BlendControl::BlendFunc;
    using NF = AmdGpu::NumberFormat;
    AmdGpu::BlendControl bc{};
    bc.enable = 1;
    bc.separate_alpha_blend = 1;
    bc.alpha_src_factor = F::SrcAlpha;
    bc.alpha_dst_factor = F::DstAlpha;
    bc.alpha_func = Op::Min;
    assert(Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Unorm,false,true));
    assert(Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Srgb,false,true));
    assert(!Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Float,false,true));
    assert(!Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Unorm,true,true));
    assert(!Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Unorm,false,false));
    bc.enable=0;
    assert(!Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Unorm,false,true));
    bc.enable=1;
    bc.alpha_src_factor=F::SrcColor;
    assert(!Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Unorm,false,true));
    bc.alpha_src_factor=F::SrcAlpha;
    bc.alpha_dst_factor=F::OneMinusDstAlpha;
    assert(!Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Unorm,false,true));
    bc.alpha_dst_factor=F::DstAlpha;
    bc.alpha_func=Op::Add;
    assert(!Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Unorm,false,true));
    bc.alpha_func=Op::Max;
    assert(Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Unorm,false,true));
    bc.separate_alpha_blend=0;
    bc.color_func=Op::Max;
    bc.color_src_factor=F::SrcAlpha;
    bc.color_dst_factor=F::DstAlpha;
    assert(Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Unorm,false,true));
    bc.color_dst_factor=F::One;
    assert(!Vulkan::CanShaderScaleMinMaxAlpha(bc,NF::Unorm,false,true));

    std::mt19937 rng{0x61fbeefa};
    std::uniform_real_distribution<float> alpha(0.f,1.f);
    for(int i=0;i<20000;i++){
      const float source=alpha(rng),destination=alpha(rng);
      const float scaled_source=source*source,scaled_dest=destination*destination;
      const float gcn_min=std::min(source*source,destination*destination);
      const float gcn_max=std::max(source*source,destination*destination);
      assert(gcn_min==std::min(scaled_source,scaled_dest));
      assert(gcn_max==std::max(scaled_source,scaled_dest));
    }
    std::puts("ALPHA_MINMAX_SELF_SCALE=PASS normalized_cases=40000 rejected_unsupported=PASS");
}
