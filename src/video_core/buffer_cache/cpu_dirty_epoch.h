// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <atomic>
#include <cstdint>

namespace VideoCore {

// Monotonic notification for changes that could make guest CPU pages stale on
// the host GPU. Does not change guest memory, page protections, or upload rules.
// The GPU-command thread may skip an identical full-residency DMA sweep when
// no CPU-dirty state has changed and all new allocations were synchronized.
//
// Notify both BEFORE and AFTER a mutation. If a DMA sweep races with marking
// CPU memory dirty, the post-mutation notification ensures a later sweep will
// observe the new dirty pages even when the first sweep was too early.
class CpuDirtyEpoch final {
public:
    [[nodiscard]] std::uint64_t Snapshot() const noexcept {
        return generation_.load(std::memory_order_acquire);
    }

    class Mutation final {
    public:
        explicit Mutation(CpuDirtyEpoch& owner) noexcept : owner_{owner} {
            owner_.Notify();
        }
        Mutation(const Mutation&) = delete;
        Mutation& operator=(const Mutation&) = delete;
        ~Mutation() {
            owner_.Notify();
        }

    private:
        CpuDirtyEpoch& owner_;
    };

private:
    void Notify() noexcept {
        generation_.fetch_add(1, std::memory_order_acq_rel);
    }
    std::atomic<std::uint64_t> generation_{1};
};

} // namespace VideoCore
