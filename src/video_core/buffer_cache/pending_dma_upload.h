// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <atomic>

namespace VideoCore {

// A conservative coarse-grained notification, not a substitute for CPU/GPU
// dirty-page tracking. Only the GPU command processor consumes this flag.
// CPU fault handlers, guest mapping updates, and GPU allocation paths publish
// a notification AFTER making their dirty-page state visible.
//
// The initial true value forces the first full DMA scan. Concurrent writes
// are never hidden by finishing a scan: exchange(false) happens BEFORE the
// scan, so any new writer's Notify() remains pending for the next draw.
class PendingDmaUpload final {
public:
    void Notify() noexcept {
        dirty.store(true, std::memory_order_release);
    }

    [[nodiscard]] bool Consume() noexcept {
        return dirty.exchange(false, std::memory_order_acq_rel);
    }

private:
    std::atomic<bool> dirty{true};
};

} // namespace VideoCore
