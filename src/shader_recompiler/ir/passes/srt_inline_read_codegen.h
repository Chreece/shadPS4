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

// Scalar SMEM S_BUFFER_LOAD uses four SGPR descriptor dwords, but only
// base (dw0 + dw1[7:0]), stride (dw1[29:16]), and NUM_RECORDS (dw2).
// GCN3 ISA 7.2.3: clamp byte OFFSET when stride == 0 and offset >=
// NUM_RECORDS; otherwise clamp only when offset > stride * NUM_RECORDS.
// All resulting guest addresses are dword-aligned.
[[nodiscard]] constexpr std::uint64_t GuestReadSBufferDwordAddress(std::uint32_t lo,
                                                                    std::uint32_t dw1,
                                                                    std::uint32_t num_records,
                                                                    std::uint32_t index) {
    const std::uint64_t base =
        std::uint64_t(lo) | (std::uint64_t(dw1 & 0xffu) << 32);
    const std::uint64_t stride = (dw1 >> 16) & 0x3fffu;
    const std::uint64_t maximum = stride == 0 ? num_records : stride * num_records;
    const std::uint64_t offset = std::uint32_t(index << 2);
    const std::uint64_t clamped =
        stride == 0 ? (offset >= maximum ? maximum : offset)
                    : (offset > maximum ? maximum : offset);
    return (base + (clamped & ~std::uint64_t{3})) & SBufferGuestAddressMask;
}

// On entry R10D holds the offset in dwords; R11D has stride and R9D has
// NUM_RECORDS. Preserve R9/R11 in the caller. Convert to bytes with guest
// 32-bit wrapping, apply the GCN scalar clamping rule with 64-bit product,
// then return a dword-aligned index for EmitGuestReadDword.
inline void EmitSBufferClampDwordIndex(Xbyak::CodeGenerator& c) {
    using namespace Xbyak::util;
    Xbyak::Label stride_nonzero, done;
    c.shl(r10d, 2);
    c.test(r11d, r11d);
    c.jnz(stride_nonzero);
    c.cmp(r10d, r9d);
    c.jb(done);
    c.mov(r10d, r9d);
    c.jmp(done);
    c.L(stride_nonzero);
    c.imul(r9, r11);
    c.cmp(r10, r9);
    c.jbe(done);
    c.mov(r10d, r9d);
    c.L(done);
    c.shr(r10d, 2);
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
