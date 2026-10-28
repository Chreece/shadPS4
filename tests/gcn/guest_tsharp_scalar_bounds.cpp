// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Standalone fixture: g++ -std=c++20 -O2 -Wall -Wextra -Werror -Isrc
// tests/gcn/guest_tsharp_scalar_bounds.cpp -o /tmp/test-scalar-bounds
#include "shader_recompiler/guest_tsharp_array.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
using Shader::Optimization::CompleteGuestTsharpTableEntries;
using Shader::Optimization::CompleteGuestTsharpRecordEntries;
using Shader::Optimization::GuestTsharpIndexOrNull;

// Descriptor stride only determines bounded bytes, not T# record spacing.
static_assert(CompleteGuestTsharpTableEntries(0, 32, 1) == 1);
static_assert(CompleteGuestTsharpTableEntries(32, 1, 1) == 1);
static_assert(CompleteGuestTsharpTableEntries(8, 4, 1) == 1);
static_assert(CompleteGuestTsharpTableEntries(8, 8, 2) == 2);
static_assert(CompleteGuestTsharpTableEntries(8, 0, 100) == 0);
static_assert(CompleteGuestTsharpTableEntries(8, 3, 100) == 0);
static_assert(CompleteGuestTsharpTableEntries(8, 8, 1) == 0);
static_assert(CompleteGuestTsharpTableEntries(8, UINT32_MAX, 200000000) == 0);
static_assert(CompleteGuestTsharpTableEntries(0, 64, 2) == 2);
static_assert(CompleteGuestTsharpTableEntries(1, 32, 1) == 1);

// Material record = 536 bytes; scalar buffer descriptor stride = 8 bytes.
static_assert(CompleteGuestTsharpRecordEntries(8, 67, 536, 144, 100) == 1);
static_assert(CompleteGuestTsharpRecordEntries(8, 134, 536, 228, 100) == 2);
static_assert(CompleteGuestTsharpRecordEntries(8, 67, 536, 400, 100) == 1);
static_assert(CompleteGuestTsharpRecordEntries(0, 536, 536, 400, 100) == 1);
static_assert(CompleteGuestTsharpRecordEntries(536, 2, 536, 144, 100) == 2);
static_assert(CompleteGuestTsharpRecordEntries(8, 0, 536, 144, 100) == 0);
static_assert(CompleteGuestTsharpRecordEntries(8, 66, 536, 144, 100) == 0);
static_assert(CompleteGuestTsharpRecordEntries(8, 68, 536, 144, 100) == 0);
static_assert(CompleteGuestTsharpRecordEntries(8, 134, 536, 228, 1) == 0);
static_assert(CompleteGuestTsharpRecordEntries(8, 67, 536, 508, 100) == 0);
static_assert(CompleteGuestTsharpRecordEntries(8, 67, 536, 505, 100) == 0);
static_assert(CompleteGuestTsharpRecordEntries(8, 67, 531, 144, 100) == 0);
static_assert(CompleteGuestTsharpRecordEntries(8, 67, 28, 0, 100) == 0);
static_assert(CompleteGuestTsharpRecordEntries(16383, UINT32_MAX, 536, 144, 65536) == 0);
static_assert(GuestTsharpIndexOrNull(1, 1) == 1);
static_assert(GuestTsharpIndexOrNull(0, 1) == 0);
int main() {
    assert(CompleteGuestTsharpRecordEntries(8, 0, 536, 144, 100) == 0);
    std::puts("GUEST_TSHARP_SCALAR_BOUNDS=PASS");
}
