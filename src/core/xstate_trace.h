// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <csignal>
#include <ucontext.h>

namespace Core {

bool IsXstateTraceCopyFault(const ucontext_t& context);
void RecoverXstateTraceCopyFault(ucontext_t& context, const siginfo_t& info);
bool HandleXstateTrace(int& signal, siginfo_t& info, ucontext_t& context);

} // namespace Core
