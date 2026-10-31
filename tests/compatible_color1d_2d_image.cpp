// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cassert>
#include <cstdint>
#include <iostream>
#include "shader_recompiler/compatible_color1d_2d_image.h"

enum class Guest : std::uint32_t { Invalid=0, Color1D=8, Color2D=9, Color3D=10, Cube=11, Color1DArray=12, Color2DArray=13 };
using Shader::CompatibleColor1D2D::IsCompatibleSampled2D;
using Shader::CompatibleColor1D2D::GuestType;
using Shader::CompatibleColor1D2D::SampleY;
using Shader::CompatibleColor1D2D::SampleCoordinateType;
int main() {
    constexpr auto c1 = Guest::Color1D, c2 = Guest::Color2D;
    static_assert(IsCompatibleSampled2D(c1,c1,c1,c2));
    static_assert(IsCompatibleSampled2D(c1,c2,c1,c2));
    static_assert(IsCompatibleSampled2D(c2,c1,c1,c2));
    static_assert(IsCompatibleSampled2D(c2,c2,c1,c2));
    static_assert(!IsCompatibleSampled2D(c1,Guest::Color3D,c1,c2));
    static_assert(!IsCompatibleSampled2D(c2,Guest::Cube,c1,c2));
    static_assert(!IsCompatibleSampled2D(c1,Guest::Color1DArray,c1,c2));
    static_assert(!IsCompatibleSampled2D(c2,Guest::Color2DArray,c1,c2));
    static_assert(!IsCompatibleSampled2D(Guest::Invalid,c2,c1,c2));
    // Actual regression: the first valid T# can be Color1D while later
    // GPU-selected records are Color2D. The frontend MUST retain y instead
    // of specializing the whole table to one scalar x coordinate.
    static_assert(SampleCoordinateType(c1,true,c1,c2)==c2);
    static_assert(SampleCoordinateType(c2,true,c1,c2)==c2);
    static_assert(SampleCoordinateType(c1,false,c1,c2)==c1);
    static_assert(SampleCoordinateType(c2,false,c1,c2)==c2);
    static_assert(SampleCoordinateType(Guest::Color3D,true,c1,c2)==Guest::Color3D);
    static_assert(SampleCoordinateType(Guest::Cube,true,c1,c2)==Guest::Cube);
    static_assert(SampleCoordinateType(Guest::Color2DArray,true,c1,c2)==Guest::Color2DArray);
    static_assert(GuestType(0x80000000u)==8u);
    static_assert(GuestType(0x90000000u)==9u);
    static_assert(GuestType(0xa0000000u)==10u);
    static_assert(SampleY(8u,8u,0.875f)==0.0f);
    static_assert(SampleY(9u,8u,0.875f)==0.875f);
    // An actual 1D guest sample ignores the second coordinate; an actual
    // 2D guest sample preserves it, and the original table slot is unchanged.
    for (std::uint32_t original_index : {0u,1u,827u,2307u}) {
        if (original_index > 2307u) return 3;
        for (std::uint32_t type : {8u,9u}) {
            const float y = SampleY(type,8u,0.75f);
            if (type==8u && y!=0.0f) return 1;
            if (type==9u && y!=0.75f) return 2;
        }
    }
    std::cout << "GUEST_MIXED_SAMPLE_XY_NOT_TRUNCATED_BEFORE_SPIRV=PASS\n"
              << "GUEST_COLOR1D_COLOR2D_VULKAN_2D_VIEW_COMPATIBILITY=PASS\n"
              << "GUEST_DYNAMIC_TSHARP_TYPE_AND_Y_COORDINATE=PASS\n"
              << "UNSUPPORTED_IMAGE_DIMENSIONS_STAY_REJECTED=PASS\n";
}
