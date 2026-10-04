// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <mutex>
#include "common/logging/log.h"
#include "core/libraries/kernel/thread_atexit.h"
#include "core/libraries/libs.h"
#include "core/module_thread_atexit.h"

namespace Libraries::Kernel {

namespace {
std::mutex callbacks_mutex;
ThreadAtexitCallbacks callbacks;
} // namespace

s32 PS4_SYSV_ABI _sceKernelSetThreadAtexitCount(ThreadAtexitCount callback) {
    {
        std::scoped_lock lock{callbacks_mutex};
        callbacks.count = callback;
    }
    LOG_WARNING(Lib_Kernel,
                "Thread-atexit count callback registered; module unloading remains unsupported");
    return 0;
}

s32 PS4_SYSV_ABI _sceKernelSetThreadAtexitReport(ThreadAtexitReport callback) {
    {
        std::scoped_lock lock{callbacks_mutex};
        callbacks.report = callback;
    }
    LOG_WARNING(Lib_Kernel,
                "Thread-atexit report callback registered; module unloading remains unsupported");
    return 0;
}

ThreadAtexitCallbacks GetThreadAtexitCallbacks() {
    std::scoped_lock lock{callbacks_mutex};
    return callbacks;
}

s32 PS4_SYSV_ABI _sceKernelRtldThreadAtexitIncrement(VAddr address) {
    auto* refs = Core::FindModuleThreadAtexitRefs(address);
    if (!refs || !refs->TryAcquire()) {
        LOG_ERROR(Lib_Kernel, "Cannot acquire thread-atexit module reference for {:#x}", address);
        return -1;
    }
    LOG_DEBUG(Lib_Kernel, "Thread-atexit reference acquired: address={:#x}, references={}", address,
              refs->Count());
    return 0;
}

s32 PS4_SYSV_ABI _sceKernelRtldThreadAtexitDecrement(VAddr address) {
    auto* refs = Core::FindModuleThreadAtexitRefs(address);
    if (!refs || !refs->TryRelease()) {
        LOG_WARNING(Lib_Kernel, "No thread-atexit module reference to release for {:#x}", address);
        return 0;
    }
    LOG_DEBUG(Lib_Kernel, "Thread-atexit reference released: address={:#x}, references={}", address,
              refs->Count());
    return 0;
}

void RegisterThreadAtexit(Core::Loader::SymbolsResolver* sym) {
    LIB_FUNCTION("pB-yGZ2nQ9o", "libkernel", 1, "libkernel", _sceKernelSetThreadAtexitCount);
    LIB_FUNCTION("WhCc1w3EhSI", "libkernel", 1, "libkernel", _sceKernelSetThreadAtexitReport);
    LIB_FUNCTION("Tz4RNUCBbGI", "libkernel", 1, "libkernel", _sceKernelRtldThreadAtexitIncrement);
    LIB_FUNCTION("8OnWXlgQlvo", "libkernel", 1, "libkernel", _sceKernelRtldThreadAtexitDecrement);
}

} // namespace Libraries::Kernel
