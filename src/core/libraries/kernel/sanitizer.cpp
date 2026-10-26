// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "common/logging/log.h"
#include "core/libraries/kernel/sanitizer.h"
#include "core/libraries/libs.h"

namespace Libraries::Kernel {

// These queries return optional guest allocator replacements, not status codes.
// Guest AddressSanitizer is disabled (sceKernelIsAddressSanitizerEnabled returns
// false), so there is no replacement to expose. Host compiler sanitizers must not
// change this answer. See documents/KERNEL_RUNTIME_HOOKS.md for the scope and
// public reference; loading a guest sanitizer runtime is not implemented here.
void* PS4_SYSV_ABI sceKernelGetSanitizerMallocReplaceExternal() {
    LOG_DEBUG(Lib_Kernel, "Guest sanitizer malloc replacement unavailable");
    return nullptr;
}

void* PS4_SYSV_ABI sceKernelGetSanitizerNewReplaceExternal() {
    LOG_DEBUG(Lib_Kernel, "Guest sanitizer new replacement unavailable");
    return nullptr;
}

void RegisterSanitizer(Core::Loader::SymbolsResolver* sym) {
    LIB_FUNCTION("py6L8jiVAN8", "libkernel", 1, "libkernel",
                 sceKernelGetSanitizerMallocReplaceExternal);
    LIB_FUNCTION("bnZxYgAFeA0", "libkernel", 1, "libkernel",
                 sceKernelGetSanitizerNewReplaceExternal);
}

} // namespace Libraries::Kernel
