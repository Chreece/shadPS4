// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>
#include <xbyak/xbyak.h>
#include <xbyak/xbyak_util.h>

namespace Shader::Optimization::SrtCodegen {

inline constexpr std::uint64_t ScalarGuestAddressMask = (std::uint64_t{1} << 48) - 1;
inline constexpr std::uint64_t SBufferGuestAddressMask = (std::uint64_t{1} << 40) - 1;

// The host walker uses the same 48-bit guest virtual-address convention as
// PushPtr. Scalar S_LOAD_DWORD indices are 32-bit dword offsets; multiplication
// by four must wrap at 32 bits before addition, matching the guest IR.
[[nodiscard]] constexpr std::uint64_t GuestReadDwordAddress(std::uint32_t lo,
                                                             std::uint32_t hi,
                                                             std::uint32_t index) {
    const std::uint64_t base = std::uint64_t(lo) | (std::uint64_t(hi) << 32);
    return (base + std::uint32_t(index << 2)) & ScalarGuestAddressMask;
}

// S_BUFFER_LOAD_DWORD takes an S# four-dword buffer descriptor. Only eight
// high-address bits in dword 1 belong to the base; its stride and flags must
// never leak into address calculation. The GCN offset is in dwords.
[[nodiscard]] constexpr std::uint64_t GuestReadSBufferDwordAddress(std::uint32_t lo,
                                                                    std::uint32_t dw1,
                                                                    std::uint32_t index) {
    const std::uint64_t base =
        std::uint64_t(lo) | (std::uint64_t(dw1 & 0xffu) << 32);
    return (base + std::uint32_t(index << 2)) & SBufferGuestAddressMask;
}

// On entry RAX is the guest base address, R10D the dword index. RDI becomes
// the checked guest address, and R10D receives the actual memory word.
// The enclosing expression owns saves/restores of RDI, RAX and R11.
// mov r10d, [rdi] deliberately matches SrtWalkerSignalHandler's existing
// recoverable memory-read form, so an unmapped guest read remains fail-safe.
inline void EmitGuestReadDword(Xbyak::CodeGenerator& c,
                               std::uint64_t address_mask = ScalarGuestAddressMask) {
    using namespace Xbyak::util;
    c.shl(r10d, 2);
    c.add(rax, r10);
    c.mov(r11, address_mask);
    c.and_(rax, r11);
    c.mov(rdi, rax);
    c.mov(r10d, dword[rdi]);
}

} // namespace Shader::Optimization::SrtCodegen
