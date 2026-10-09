// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <csignal>
#include <ucontext.h>
#include "common/types.h"

namespace Core {

void RegisterXstateTraceHostCode(u64 begin, u64 size);
void StopXstateTraceForThreadExit();

class AutomaticXstateTraceScope {
public:
    AutomaticXstateTraceScope();
    ~AutomaticXstateTraceScope();
    AutomaticXstateTraceScope(const AutomaticXstateTraceScope&) = delete;
    AutomaticXstateTraceScope& operator=(const AutomaticXstateTraceScope&) = delete;
};

class SuspendXstateTraceScope {
public:
    SuspendXstateTraceScope();
    ~SuspendXstateTraceScope();
    SuspendXstateTraceScope(const SuspendXstateTraceScope&) = delete;
    SuspendXstateTraceScope& operator=(const SuspendXstateTraceScope&) = delete;

private:
    bool traced;
};

bool IsXstateTraceCopyFault(const ucontext_t& context);
bool IsXstateTraceActive(const ucontext_t& context);
void RecoverXstateTraceCopyFault(ucontext_t& context, const siginfo_t& info);
void RecoverXstateTraceBlock(int signal, siginfo_t& info, ucontext_t& context);
bool HandleXstateTrace(int& signal, siginfo_t& info, ucontext_t& context);

} // namespace Core
