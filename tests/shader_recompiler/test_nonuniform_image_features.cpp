// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cstdio>
#include "shader_recompiler/nonuniform_image_features.h"
using Shader::Optimization::CanUseNonuniformSampledImageArray;
int main() {
    static_assert(CanUseNonuniformSampledImageArray(true,true,true));
    static_assert(!CanUseNonuniformSampledImageArray(false,true,true));
    static_assert(!CanUseNonuniformSampledImageArray(true,false,true));
    static_assert(!CanUseNonuniformSampledImageArray(true,true,false));
    for (unsigned bits=0;bits<8;++bits) {
        if(CanUseNonuniformSampledImageArray((bits&1)!=0,(bits&2)!=0,(bits&4)!=0)
           != (bits==7u)) return 1;
    }
    std::puts("NONUNIFORM_IMAGE_FEATURES=PASS 8_combinations=PASS unsupported=REJECTED");
    return 0;
}
