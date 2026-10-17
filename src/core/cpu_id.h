// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "common/types.h"

namespace Xbyak {
class CodeGenerator;
}

namespace Core {

enum class CpuIdInstruction : u32 { Cpuid, Rdtscp, Rdpid };

void InitializeCpuId();
void EnableCpuIdFaulting();
bool HandleCpuIdFault(void* context, void* fault_address);

// Builtin Linux x86-64 CPU identity emulation. Unlike whole-process
// DynamoRIO, this uses kernel CPUID/TSC faults and native guest-code patches.
// Only enabled by the explicit --cpu-id-mode=builtin trial.
void SetBuiltinCpuIdMode(bool enabled) noexcept;
bool IsBuiltinCpuIdMode() noexcept;
// Signal-safe guest/host PC discrimination: one registration for each mapped
// guest executable ELF segment. No whole-process code translation.
[[nodiscard]] bool RegisterGuestExecutableRange(uintptr_t start, size_t size);
void RegisterBuiltinRdpidTrap(uintptr_t guest_ip, u32 length, u32 destination);
bool HandleBuiltinRdpidTrap(void* context);
void SetCpuIdGuestAddressRange(uintptr_t begin, uintptr_t end);
void GenerateCpuIdInstruction(Xbyak::CodeGenerator& code, CpuIdInstruction instruction,
                              u32 destination = 0, bool fault_entry = false);

} // namespace Core
