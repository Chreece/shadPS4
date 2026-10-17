// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace Core::CpuId {

// A module is registered after its executable PT_LOAD segment is mapped.
// Lookups may occur in a synchronous CPUID/RDTSCP fault handler and must not
// use locks, allocate, or touch the dynamic module loader. The linker does
// not currently support unloading modules; published nodes remain immutable
// until emulator shutdown, as in the existing native RDPID trap registry.
class GuestExecutableRanges final {
public:
    struct Range {
        const Range* next;
        std::uintptr_t begin;
        std::uintptr_t end;
    };
    static_assert(std::atomic<const Range*>::is_always_lock_free);
    GuestExecutableRanges() noexcept = default;
    GuestExecutableRanges(const GuestExecutableRanges&) = delete;
    GuestExecutableRanges& operator=(const GuestExecutableRanges&) = delete;
    ~GuestExecutableRanges() {
        // All guest threads and signal readers must have stopped at exit.
        auto* range = head_.load(std::memory_order_relaxed);
        while (range != nullptr) {
            const auto* next = range->next;
            delete range;
            range = next;
        }
    }
    [[nodiscard]] bool Register(std::uintptr_t start, std::size_t size) {
        if (start == 0 || size == 0 ||
            size > std::numeric_limits<std::uintptr_t>::max() - start) {
            return false;
        }
        auto* range = new Range{nullptr, start, start + size};
        const Range* previous = head_.load(std::memory_order_acquire);
        do {
            range->next = previous;
        } while (!head_.compare_exchange_weak(previous, range,
                                              std::memory_order_release,
                                              std::memory_order_acquire));
        return true;
    }
    [[nodiscard]] bool Contains(std::uintptr_t ip) const noexcept {
        const Range* range = head_.load(std::memory_order_acquire);
        while (range != nullptr) {
            if (ip >= range->begin && ip < range->end) {
                return true;
            }
            range = range->next;
        }
        return false;
    }
private:
    std::atomic<const Range*> head_{nullptr};
};
} // namespace Core::CpuId
