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

static_assert(CanReuseGpuFaultReadbackSlot(0));
static_assert(!CanReuseGpuFaultReadbackSlot(1));
static_assert(!CanReuseGpuFaultReadbackSlot(~u64{0}));

} // namespace VideoCore
