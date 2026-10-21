// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cassert>
#include <cstdio>
#include "video_core/texture_cache/sampler_filter_policy.h"

using namespace VideoCore;

static_assert(ImageSamplerFilter(vk::Filter::eLinear, true) == vk::Filter::eNearest);
static_assert(ImageSamplerFilter(vk::Filter::eNearest, true) == vk::Filter::eNearest);
static_assert(ImageSamplerFilter(vk::Filter::eLinear, false) == vk::Filter::eLinear);
static_assert(ImageSamplerMipmapMode(vk::SamplerMipmapMode::eLinear, true) ==
              vk::SamplerMipmapMode::eNearest);
static_assert(ImageSamplerMipmapMode(vk::SamplerMipmapMode::eLinear, false) ==
              vk::SamplerMipmapMode::eLinear);
static_assert(!SamplerAnisotropyAllowed(true, true));
static_assert(SamplerAnisotropyAllowed(true, false));

int main() {
    unsigned cases{};
    for (bool integer : {false, true}) {
        for (const auto filter : {vk::Filter::eNearest, vk::Filter::eLinear}) {
            const auto got = ImageSamplerFilter(filter, integer);
            assert(got == (integer ? vk::Filter::eNearest : filter));
            ++cases;
        }
        for (const auto mip : {vk::SamplerMipmapMode::eNearest,
                               vk::SamplerMipmapMode::eLinear}) {
            assert(ImageSamplerMipmapMode(mip, integer) ==
                   (integer ? vk::SamplerMipmapMode::eNearest : mip));
            ++cases;
        }
        for (bool requested_anisotropy : {false, true}) {
            assert(SamplerAnisotropyAllowed(requested_anisotropy, integer) ==
                   (requested_anisotropy && !integer));
            ++cases;
        }
    }
    std::printf("GENERAL_INTEGER_SAMPLER_FILTER=PASS cases=%u\n", cases);
}
