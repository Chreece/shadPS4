// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <cstdio>
#include "shader_recompiler/ir/passes/image_phi_preflight.h"
using Shader::Optimization::CanSplitSharpImagePhi;
int main() {
    static_assert(CanSplitSharpImagePhi(2,true,true,1));
    static_assert(CanSplitSharpImagePhi(3,true,true,2));
    static_assert(!CanSplitSharpImagePhi(1,true,true,3));
    static_assert(!CanSplitSharpImagePhi(4,true,true,8));
    static_assert(!CanSplitSharpImagePhi(2,false,true,4));
    static_assert(!CanSplitSharpImagePhi(2,true,false,4));
    static_assert(!CanSplitSharpImagePhi(3,true,true,1));
    std::puts("IMAGE_PHI_PREFLIGHT=PASS alternatives=2-3 "
              "exact_predecessors=YES unsupported_paths=UNCHANGED");
    return 0;
}
