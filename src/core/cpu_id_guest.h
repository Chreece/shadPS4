// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <algorithm>
#include <array>
#include <cstdint>

namespace Core::CpuIdentity {

// Portable guest CPU identity: no host OS calls, fault handlers, translator,
// game identifiers or GPU-specific behavior. Any platform's instruction
// interception backend can use the same guest-visible CPU feature policy.
inline constexpr std::uint32_t GuestCpuCount = 8;
inline constexpr std::uint32_t GuestFeaturesEcx = 0x3ed8220b;
inline constexpr std::uint32_t GuestFeaturesEdx = 0x178bfbff;
inline constexpr std::uint32_t GuestExtendedFeaturesEcx = 0x154837ff;
inline constexpr std::uint32_t GuestExtendedFeaturesEdx = 0x2fd3fbff;

[[nodiscard]] constexpr std::array<std::uint32_t, 4> QueryGuestCpuid(
    std::uint32_t leaf, std::uint32_t subleaf, std::uint32_t cpu,
    std::uint32_t host_max_basic, std::uint32_t host_max_extended,
    std::array<std::uint32_t, 4> host_result) noexcept {
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
        // BMI1 is the only Jaguar structured extended feature (AMD BKDG 48751).
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
        // Jaguar reserves EBX and EDX (AMD BKDG 48751, CPUID Fn8000_0008).
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

} // namespace Core::CpuIdentity
