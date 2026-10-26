// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "common/types.h"

namespace VideoCore {

// A readback ring slot belongs to the GPU until the scheduler's deferred
// completion callback releases it. A free slot is represented by tick zero.
// Never wait for a GPU timeline event while executing a Vulkan on-submit
// callback: the callback itself holds the global submission mutex.
[[nodiscard]] constexpr bool CanReuseGpuFaultReadbackSlot(u64 pending_tick) noexcept {
    return pending_tick == 0;
}

// The deferred scan must survive backpressure on the readback ring.
// Clearing the flag before a successful scan silently drops fault requests.
[[nodiscard]] constexpr bool RetainPendingGpuFaultScan(bool was_pending,
                                                        bool scan_enqueued) noexcept {
    return was_pending && !scan_enqueued;
}

static_assert(RetainPendingGpuFaultScan(true, false));
static_assert(!RetainPendingGpuFaultScan(true, true));
static_assert(!RetainPendingGpuFaultScan(false, false));

static_assert(CanReuseGpuFaultReadbackSlot(0));
static_assert(!CanReuseGpuFaultReadbackSlot(1));
static_assert(!CanReuseGpuFaultReadbackSlot(~u64{0}));

} // namespace VideoCore
