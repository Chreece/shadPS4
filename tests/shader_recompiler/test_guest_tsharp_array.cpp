// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cstdint>
#include <cstdio>
#include <random>
#include "shader_recompiler/guest_tsharp_array.h"
using Shader::Optimization::CompleteGuestTsharpTableEntries;
using Shader::Optimization::GuestTsharpIndexOrNull;
int main() {
    static_assert(CompleteGuestTsharpTableEntries(0,480,15)==15);
    static_assert(CompleteGuestTsharpTableEntries(32,15,15)==15);
    static_assert(CompleteGuestTsharpTableEntries(0,512,15)==0);
    static_assert(CompleteGuestTsharpTableEntries(32,16,15)==0);
    static_assert(CompleteGuestTsharpTableEntries(16,15,15)==0);
    static_assert(CompleteGuestTsharpTableEntries(0,31,15)==0);
    static_assert(CompleteGuestTsharpTableEntries(0,33,15)==0);
    static_assert(CompleteGuestTsharpTableEntries(0,0,15)==0);
    static_assert(GuestTsharpIndexOrNull(0,15)==0);
    static_assert(GuestTsharpIndexOrNull(14,15)==14);
    static_assert(GuestTsharpIndexOrNull(15,15)==15);
    static_assert(GuestTsharpIndexOrNull(0xffffffffu,15)==15);
    std::mt19937 rng{0x1ac05e77u};
    for(unsigned iter=0;iter<100000;++iter) {
        const auto count=1+rng()%15u;
        for(std::uint32_t n=0;n<count;++n) {
            if(GuestTsharpIndexOrNull(n,count)!=n)return 1;
        }
        if(GuestTsharpIndexOrNull(count,count)!=count)return 2;
        const std::uint32_t stride=iter%2?0u:32u;
        const std::uint32_t records=stride?count:count*32u;
        if(CompleteGuestTsharpTableEntries(stride,records,15)!=count)return 3;
    }
    std::puts("REAL_GUEST_TSHARP_TABLE=PASS complete=100000 "
              "no_truncation=PASS null_on_invalid_id=PASS");
    return 0;
}
