// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#if defined(__linux__) && defined(__x86_64__)
#include <sys/prctl.h>

namespace Core {

// Guest and host code share native pthreads. If a guest thread makes RDTSC
// globally fault, host media/audio workers spawned from it inherit the same
// restriction and may crash in their own clock instructions. Leave RDTSC
// enabled in all native threads; guest CPU identity is handled by the guest
// instruction translator, not by trapping unrelated host clock reads.
inline int RestoreHostNativeTsc() {
    return prctl(PR_SET_TSC, PR_TSC_ENABLE);
}

} // namespace Core
#endif
