// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Portable, deterministic CPU identity tests. This file has no Vulkan,
// Linux/DynamoRIO dependencies and can build under GCC, Clang or MSVC.
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <random>

#include "core/cpu_id_guest.h"

namespace {
constexpr std::uint32_t GuestCpuCount = 8;
constexpr std::uint32_t GuestFeaturesEcx = 0x3ed8220b;
constexpr std::uint32_t GuestFeaturesEdx = 0x178bfbff;
constexpr std::uint32_t GuestExtendedFeaturesEcx = 0x154837ff;
constexpr std::uint32_t GuestExtendedFeaturesEdx = 0x2fd3fbff;

// Independently preserved pre-refactor guest CPU identity algorithm.
// Never call QueryGuestCpuid from this oracle: this is a behavior parity test.
std::array<std::uint32_t, 4> BaselineGuestCpuid(
    std::uint32_t leaf, std::uint32_t subleaf, std::uint32_t cpu,
    std::uint32_t host_max_basic, std::uint32_t host_max_extended,
    std::array<std::uint32_t, 4> host_result) {
    const std::uint32_t apic_id = GuestCpuCount - 1 - cpu;
    auto result = host_result;
    if (leaf > host_max_basic && (leaf < 0x80000000 || leaf > host_max_extended)) {
        result = {};
    }
    auto& [eax, ebx, ecx, edx] = result;
    switch (leaf) {
    case 0:
        eax = std::max(host_max_basic, 0x1fu);
        break;
    case 1:
        ebx = (ebx & 0xffff) | (GuestCpuCount << 16) | (apic_id << 24);
        ecx &= GuestFeaturesEcx;
        edx = (edx & GuestFeaturesEdx) | (1u << 28);
        break;
    case 4:
    case 0x8000001d: {
        constexpr std::array<std::array<std::uint32_t, 4>, 3> caches{{
            {0x00000121, 0x01c0003f, 0x0000003f, 0},
            {0x00000122, 0x0040003f, 0x000000ff, 0},
            {0x0000c143, 0x03c0003f, 0x000007ff, 2},
        }};
        result = subleaf < caches.size() ? caches[subleaf] : std::array<std::uint32_t, 4>{};
        if (leaf == 4 && eax != 0) {
            eax |= (GuestCpuCount - 1) << 26;
        }
        break;
    }
    case 7:
        result = {0, subleaf == 0 ? ebx & (1u << 3) : 0, 0, 0};
        break;
    case 0xb:
    case 0x1f:
        eax = subleaf == 1 ? 3 : 0;
        ebx = subleaf == 0 ? 1 : subleaf == 1 ? GuestCpuCount : 0;
        ecx = (subleaf & 0xff) | (subleaf < 2 ? (subleaf + 1) << 8 : 0);
        edx = apic_id;
        break;
    case 0x1a:
        result = {};
        break;
    case 0x80000000:
        eax = std::max(host_max_extended, 0x8000001eu);
        break;
    case 0x80000001:
        ecx &= GuestExtendedFeaturesEcx;
        ecx |= (1u << 6) | (1u << 22);
        edx &= GuestExtendedFeaturesEdx;
        break;
    case 0x80000005:
        result = {0xff08ff08, 0xff28ff20, 0x20080140, 0x20020140};
        break;
    case 0x80000006:
        result = {0x21000000, 0x42004200, 0x08008140, 0};
        break;
    case 0x80000008:
        ebx = 0;
        ecx = (ecx & ~0xf0ffu) | (3u << 12) | (GuestCpuCount - 1);
        edx = 0;
        break;
    case 0x8000001e:
        result = {apic_id, apic_id, 0, 0};
        break;
    case 0x80000026:
        result = {};
        break;
    default:
        break;
    }
    return result;
}

void SemanticChecks() {
    constexpr std::array<std::uint32_t, 4> host{0x12345678,0xffffffff,0xffffffff,0xffffffff};
    namespace Identity = Core::CpuIdentity;
    const auto run = [&](std::uint32_t leaf, std::uint32_t sub, std::uint32_t cpu=0) {
        return Identity::QueryGuestCpuid(leaf,sub,cpu,0x16,0x80000008,host);
    };
    assert((run(1,0)[1] >> 16 & 0xff) == 8);
    assert((run(7,0)[1] & ~(1u << 3)) == 0);
    assert(run(7,1)[1] == 0);
    assert((run(0x1a,0) == std::array<std::uint32_t,4>{}));
    assert((run(0x80000026,0) == std::array<std::uint32_t,4>{}));
    assert(run(0xb,0,0)[3] == 7);
    assert(run(0xb,0,7)[3] == 0);
    assert(run(0xb,1)[1] == 8);
    assert((run(4,0)[0] >> 26 & 7) == 7);
    assert((run(4,9) == std::array<std::uint32_t,4>{}));
    assert(run(0x8000001e,0,3)[0] == 4);
    assert(run(0x80000005,0)[0] == 0xff08ff08);
    assert(run(0x80000006,0)[0] == 0x21000000);
}
} // namespace

int main() {
    SemanticChecks();
    const std::array<std::uint32_t, 22> leaves{
        0u,1u,2u,4u,7u,0xbu,0x1au,0x1fu,0xd,
        0x80000000u,0x80000001u,0x80000005u,0x80000006u,0x80000008u,
        0x8000001du,0x8000001eu,0x80000026u,0x7fffffffu,
        0xffffffffu,0x15u,0x10u,0x80000007u
    };
    std::mt19937_64 rng{0x5ad5d05ac14f23b1ULL};
    std::uint64_t cases{};
    for (auto cpu=0u; cpu<8; ++cpu) {
        for (const auto leaf:leaves) {
            for (auto subleaf=0u; subleaf<8; ++subleaf) {
                for (auto scenario=0u; scenario<32; ++scenario) {
                    const std::uint32_t basic = scenario % 2 ? 0x16u : 7u;
                    const std::uint32_t ext = scenario % 3 ? 0x80000026u : 0x80000007u;
                    std::array<std::uint32_t,4> host{};
                    for (auto& reg:host) reg=static_cast<std::uint32_t>(rng());
                    const auto actual=Core::CpuIdentity::QueryGuestCpuid(
                        leaf,subleaf,cpu,basic,ext,host);
                    const auto expected=BaselineGuestCpuid(
                        leaf,subleaf,cpu,basic,ext,host);
                    if (actual!=expected) {
                        std::fprintf(stderr,
                            "CPU identity mismatch leaf=%08x sub=%u cpu=%u\n",
                            leaf,subleaf,cpu);
                        return 1;
                    }
                    ++cases;
                }
            }
        }
    }
    std::printf("PORTABLE_GUEST_CPUID=PASS cases=%llu\n",
                static_cast<unsigned long long>(cases));
}
