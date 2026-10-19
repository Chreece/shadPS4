// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include "shader_recompiler/ir/passes/lds_singleton_divergence.h"
using Shader::Optimization::IsVaryingLocalInvocationComponent;
static_assert(!IsVaryingLocalInvocationComponent(1,{64,1,1}));
static_assert(!IsVaryingLocalInvocationComponent(2,{64,1,1}));
static_assert(IsVaryingLocalInvocationComponent(0,{64,1,1}));
static_assert(!IsVaryingLocalInvocationComponent(0,{1,64,1}));
static_assert(IsVaryingLocalInvocationComponent(1,{1,64,1}));
static_assert(!IsVaryingLocalInvocationComponent(2,{1,64,1}));
static_assert(IsVaryingLocalInvocationComponent(3,{1,1,1}));
static_assert(!IsVaryingLocalInvocationComponent(2,{1,1,1}));
int main(){
    std::uint32_t tested=0;
    for(std::uint32_t i=0;i<100000;i++){
        const std::array<std::uint32_t,3> dims{1+(i%128),1+((i/128)%64),1+((i/8192)%13)};
        for(std::uint32_t c=0;c<4;++c){
            const bool actual=IsVaryingLocalInvocationComponent(c,dims);
            const bool expected=(c>=3 || dims[c]>1);
            assert(actual==expected);
            tested++;
        }
    }
    std::printf("GOT_LDS_SINGLETON_DIVERGENCE=PASS checks=%u "
                "singleton_axes_uniform=YES non_singleton_axes_varying=YES "
                "invalid_axes_fail_closed=YES\n",tested);
}
