// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include "video_core/renderer_vulkan/liverpool_to_vk.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/texture_cache/sampler.h"

namespace VideoCore {
namespace {

// Integer-format images cannot use linear filtering or anisotropy in Vulkan.
// Keep normal/float sampler behavior completely unchanged.
constexpr bool SamplerAnisotropyAllowed(bool requested, bool is_integer) noexcept {
    return requested && !is_integer;
}

constexpr vk::Filter ImageSamplerFilter(vk::Filter filter, bool is_integer) noexcept {
    return is_integer ? vk::Filter::eNearest : filter;
}

constexpr vk::SamplerMipmapMode ImageSamplerMipmapMode(vk::SamplerMipmapMode mode,
                                                       bool is_integer) noexcept {
    return is_integer ? vk::SamplerMipmapMode::eNearest : mode;
}

static_assert(SamplerAnisotropyAllowed(true, false));
static_assert(!SamplerAnisotropyAllowed(true, true));
static_assert(!SamplerAnisotropyAllowed(false, false));
static_assert(ImageSamplerFilter(vk::Filter::eLinear, false) == vk::Filter::eLinear);
static_assert(ImageSamplerFilter(vk::Filter::eLinear, true) == vk::Filter::eNearest);
static_assert(ImageSamplerFilter(vk::Filter::eNearest, true) == vk::Filter::eNearest);
static_assert(ImageSamplerMipmapMode(vk::SamplerMipmapMode::eLinear, false) ==
              vk::SamplerMipmapMode::eLinear);
static_assert(ImageSamplerMipmapMode(vk::SamplerMipmapMode::eLinear, true) ==
              vk::SamplerMipmapMode::eNearest);

} // namespace

Sampler::Sampler(const Vulkan::Instance& instance, const AmdGpu::Sampler& sampler,
                 const AmdGpu::BorderColorBuffer border_color_base, const bool is_depth,
                 const bool is_integer) {
    using namespace Vulkan;
    const bool anisotropy_enable =
        SamplerAnisotropyAllowed(
            instance.IsAnisotropicFilteringSupported() &&
                (AmdGpu::IsAnisoFilter(sampler.xy_mag_filter) ||
                 AmdGpu::IsAnisoFilter(sampler.xy_min_filter)),
            is_integer);
    const float max_anisotropy =
        anisotropy_enable ? std::clamp(sampler.MaxAniso(), 1.0f, instance.MaxSamplerAnisotropy())
                          : 1.0f;
    auto border_color = LiverpoolToVK::BorderColor(sampler.border_color_type);
    if (border_color == vk::BorderColor::eFloatCustomEXT &&
        !instance.IsCustomBorderColorSupported()) {
        LOG_WARNING(Render_Vulkan, "Custom border color is not supported, falling back to black");
        border_color = vk::BorderColor::eFloatOpaqueBlack;
    }

    const auto custom_color = [&]() -> std::optional<vk::SamplerCustomBorderColorCreateInfoEXT> {
        if (border_color == vk::BorderColor::eFloatCustomEXT) {
            const auto border_color_index = sampler.border_color_ptr.Value();
            const auto border_color_buffer = border_color_base.Address<std::array<float, 4>*>();
            const auto custom_border_color_array = border_color_buffer[border_color_index];

            const vk::SamplerCustomBorderColorCreateInfoEXT ret{
                .customBorderColor =
                    vk::ClearColorValue{
                        .float32 = custom_border_color_array,
                    },
                .format = vk::Format::eR32G32B32A32Sfloat,
            };
            return ret;
        } else {
            return std::nullopt;
        }
    }();

    const vk::SamplerCreateInfo sampler_ci = {
        .pNext = custom_color ? &*custom_color : nullptr,
        .magFilter = ImageSamplerFilter(LiverpoolToVK::Filter(sampler.xy_mag_filter), is_integer),
        .minFilter = ImageSamplerFilter(LiverpoolToVK::Filter(sampler.xy_min_filter), is_integer),
        .mipmapMode =
            ImageSamplerMipmapMode(LiverpoolToVK::MipFilter(sampler.mip_filter), is_integer),
        .addressModeU = LiverpoolToVK::ClampMode(sampler.clamp_x),
        .addressModeV = LiverpoolToVK::ClampMode(sampler.clamp_y),
        .addressModeW = LiverpoolToVK::ClampMode(sampler.clamp_z),
        .mipLodBias = std::min(sampler.LodBias(), instance.MaxSamplerLodBias()),
        .anisotropyEnable = anisotropy_enable,
        .maxAnisotropy = max_anisotropy,
        // GCN compares per instruction; a plain read of a compare sampler is undefined in Vulkan.
        .compareEnable = is_depth,
        .compareOp = LiverpoolToVK::DepthCompare(sampler.depth_compare_func),
        .minLod = sampler.MinLod(),
        .maxLod = sampler.MaxLod(),
        .borderColor = border_color,
        .unnormalizedCoordinates = false, // Handled in shader due to Vulkan limitations.
    };
    auto [sampler_result, smplr] = instance.GetDevice().createSamplerUnique(sampler_ci);
    ASSERT_MSG(sampler_result == vk::Result::eSuccess, "Failed to create sampler: {}",
               vk::to_string(sampler_result));
    handle = std::move(smplr);
}

Sampler::~Sampler() = default;

} // namespace VideoCore
