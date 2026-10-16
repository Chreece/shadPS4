// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include "video_core/amdgpu/regs_color.h"

namespace Vulkan {

// GCN MIN/MAX blends multiply both operands by their respective factors;
// Vulkan ignores these factors. A normalized alpha target self-scaled by
// SrcAlpha and DstAlpha has the exact GCN result
//     min/max(src.a * src.a, dst.a * dst.a).
// The fragment shader may write src.a^2; Vulkan MIN/MAX then consumes both
// operands directly with ONE blend factors. This cannot be extended blindly
// to SrcColor, Src1Alpha, or unrelated factors.
//
// A masked-out source alpha is not eligible: PS4 supplies special default
// blending inputs which cannot be reconstructed by squaring the shader value.
[[nodiscard]] constexpr bool CanShaderScaleMinMaxAlpha(
    const AmdGpu::BlendControl& bc, AmdGpu::NumberFormat format,
    bool blend_bypass, bool shader_exports_alpha) {
    using F = AmdGpu::BlendControl::BlendFactor;
    using Op = AmdGpu::BlendControl::BlendFunc;
    const auto op = bc.separate_alpha_blend ? bc.alpha_func : bc.color_func;
    const auto source = bc.separate_alpha_blend ? bc.alpha_src_factor : bc.color_src_factor;
    const auto destination =
        bc.separate_alpha_blend ? bc.alpha_dst_factor : bc.color_dst_factor;
    return bc.enable && !blend_bypass && shader_exports_alpha &&
           (format == AmdGpu::NumberFormat::Unorm ||
            format == AmdGpu::NumberFormat::Srgb) &&
           (op == Op::Min || op == Op::Max) &&
           source == F::SrcAlpha && destination == F::DstAlpha;
}

} // namespace Vulkan
