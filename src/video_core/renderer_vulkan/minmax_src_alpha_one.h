// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include "video_core/amdgpu/regs_color.h"

namespace Vulkan {

// PS4 MIN/MAX multiplies both inputs by blend factors; Vulkan MIN/MAX
// ignores those factors. For normalized targets, this conservative case
//   MIN/MAX(src.rgba * src.alpha, dst.rgba * ONE)
// can be represented by premultiplying the fragment output (at return)
// and using ONE for Vulkan's source/destination factors.
//
// Both color and alpha equations must be MIN/MAX with SrcAlpha/ONE.
// Other factors or unexported alpha cannot use this transformation.
[[nodiscard]] constexpr bool CanShaderScaleMinMaxSrcAlphaOne(
    const AmdGpu::BlendControl& blend, AmdGpu::NumberFormat format,
    bool blend_bypass, bool exports_alpha) noexcept {
    using Op = AmdGpu::BlendControl::BlendFunc;
    using Factor = AmdGpu::BlendControl::BlendFactor;
    if (!blend.enable || blend_bypass || !exports_alpha ||
        (format != AmdGpu::NumberFormat::Unorm &&
         format != AmdGpu::NumberFormat::Srgb)) {
        return false;
    }
    const auto color = blend.color_func;
    const auto alpha = blend.separate_alpha_blend ? blend.alpha_func : color;
    const auto alpha_src =
        blend.separate_alpha_blend ? blend.alpha_src_factor : blend.color_src_factor;
    const auto alpha_dst =
        blend.separate_alpha_blend ? blend.alpha_dst_factor : blend.color_dst_factor;
    return (color == Op::Min || color == Op::Max) &&
           (alpha == Op::Min || alpha == Op::Max) &&
           blend.color_src_factor == Factor::SrcAlpha &&
           blend.color_dst_factor == Factor::One &&
           alpha_src == Factor::SrcAlpha && alpha_dst == Factor::One;
}

} // namespace Vulkan
