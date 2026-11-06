// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "common/types.h"

namespace Core::Loader {
class SymbolsResolver;
}

namespace Libraries::Kernel {

using ThreadAtexitCount = s32 PS4_SYSV_ABI (*)(s32 module_handle);
using ThreadAtexitReport = void PS4_SYSV_ABI (*)(s32 module_handle);

struct ThreadAtexitCallbacks {
    ThreadAtexitCount count{};
    ThreadAtexitReport report{};
};

// Public references differ between void and zero status for the setters and
// decrement. Preserve the prior zero result; void callers simply ignore it.
s32 PS4_SYSV_ABI _sceKernelSetThreadAtexitCount(ThreadAtexitCount callback);
s32 PS4_SYSV_ABI _sceKernelSetThreadAtexitReport(ThreadAtexitReport callback);
s32 PS4_SYSV_ABI _sceKernelRtldThreadAtexitIncrement(VAddr address);
s32 PS4_SYSV_ABI _sceKernelRtldThreadAtexitDecrement(VAddr address);

// For module-unload checks, not pthread-exit execution. Registration must not
// invoke either callback. Take a snapshot before entering any guest callback;
// never hold the registration mutex across guest code. No unload consumer is
// implemented yet, so registration continues to report that limitation.
ThreadAtexitCallbacks GetThreadAtexitCallbacks();

void RegisterThreadAtexit(Core::Loader::SymbolsResolver* sym);

} // namespace Libraries::Kernel
