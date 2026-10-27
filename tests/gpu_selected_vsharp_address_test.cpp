// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Independent release-mode oracle for the actual 40-bit guest V# layout.
// The real production SPIR-V/C++ code must also pass the native full build.
#include <array>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <iostream>
#include <random>
using u32 = std::uint32_t;
using u64 = std::uint64_t;
struct GuestBuffer {
    u64 base_address : 40;
    u64 padding : 8;
    u64 stride : 14;
    u64 cache_swizzle : 1;
    u64 swizzle_enable : 1;
    u32 num_records;
    u32 swizzles : 12;
    u32 num_format : 3;
    u32 data_format : 4;
    u32 element_size : 2;
    u32 index_stride : 2;
    u32 add_tid : 1;
    u32 padding1 : 3;
    u32 mtype : 3;
    u32 type : 2;
};
static_assert(sizeof(GuestBuffer) == 16);
static bool gpu_read_valid(const std::array<u32, 4>& dw, u32 index, u64& address) {
    const u64 base = (u64(dw[1] & 0xffu) << 32) | dw[0];
    const u32 stride = (dw[1] >> 16) & 0x3fffu;
    const u32 size = stride ? u32(dw[2] * stride) : dw[2];
    const bool valid = ((dw[3] >> 30) == 0) && size >= 4 &&
                       index <= ((size - 4) >> 2) && base != 0;
    address = base + u64(u32(index << 2));
    return valid;
}
int main() {
    std::mt19937_64 random(1217);
    for (std::size_t i = 0; i < 100000; ++i) {
        GuestBuffer guest{};
        guest.base_address = 1 + (random() % ((u64(1) << 40) - 1));
        guest.stride = u32(random() % 16384);
        guest.num_records = u32(random());
        guest.type = i % 7 == 0 ? 1 : 0;
        std::array<u32, 4> dw{};
        std::memcpy(dw.data(), &guest, sizeof(guest));
        const u32 index = u32(random());
        u64 actual_addr = 0;
        const bool actual = gpu_read_valid(dw, index, actual_addr);
        const u32 bytes = guest.stride ? u32(guest.num_records * guest.stride)
                                       : guest.num_records;
        const bool expected = guest.type == 0 && bytes >= 4 &&
                              index <= ((bytes - 4) >> 2) && guest.base_address != 0;
        if (actual != expected ||
            actual_addr != guest.base_address + u64(u32(index << 2))) return 3;
    }
    // The actual GoT SRT pattern: ((Phi << 3) + 0x18) >> 2.
    std::array<u32, 4> dw{0x1000, 0, 0x1000, 0};
    u64 lane0 = 0, lane13 = 0;
    if (!gpu_read_valid(dw, ((0u << 3) + 0x18u) >> 2, lane0) ||
        !gpu_read_valid(dw, ((13u << 3) + 0x18u) >> 2, lane13) ||
        lane0 != 0x1018 || lane13 != 0x1080) return 4;
    std::cout << "GPU_SELECTED_VSHARP_RELEASE_PASS=100000\n";
}
