// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// A native x86-64 guest ABI boundary can enter HLE with RSP % 16 == 0
// instead of the expected 8; nested host calls must still see normal alignment.
#include <cstdint>
#include <cstdio>

extern "C" std::uint64_t CallGuestMisaligned(std::uint64_t (*fn)(std::uint64_t),
                                               std::uint64_t value);

extern "C" __attribute__((noinline)) std::uint64_t HostNestedCall(std::uint64_t value) {
    std::uintptr_t sp;
    asm volatile("movq %%rsp, %0" : "=r"(sp));
    return value ^ (sp & 0xf);
}

extern "C" __attribute__((noinline)) std::uint64_t UnsafeGuestEntry(std::uint64_t value) {
    const auto result = HostNestedCall(value);
    asm volatile("" ::: "memory");
    return result;
}

extern "C" __attribute__((noinline, force_align_arg_pointer))
std::uint64_t AlignedHleGuestEntry(std::uint64_t value) {
    const auto result = HostNestedCall(value);
    asm volatile("" ::: "memory");
    return result;
}

int main() {
    constexpr std::uint64_t input = 0x1200;
    // For the intentionally wrong ABI entry, GCC's normal nested call
    // inherits the erroneous stack alignment. The test must detect this.
    const auto unsafe = CallGuestMisaligned(UnsafeGuestEntry, input);
    const auto corrected = CallGuestMisaligned(AlignedHleGuestEntry, input);
    if (unsafe != input) {
        std::fprintf(stderr, "Unexpected unprotected baseline: %#llx\n",
                     static_cast<unsigned long long>(unsafe));
        return 1;
    }
    if (corrected != (input ^ 8)) {
        std::fprintf(stderr, "Guest/HLE alignment not repaired: %#llx\n",
                     static_cast<unsigned long long>(corrected));
        return 2;
    }
    std::puts("NATIVE_GUEST_TO_HLE_ALIGNMENT=PASS");
    std::puts("NESTED_HOST_CALL_STACK_ALIGNMENT=PASS");
    return 0;
}
