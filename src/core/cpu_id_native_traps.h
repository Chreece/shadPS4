// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

// Emulator-owned CPU identity fault metadata for instructions too short for
// a five-byte JMP. Guest modules are patched before execution; an in-place
// UD2 (two bytes) triggers the existing Linux SIGILL dispatch. The signal
// path must never allocate, acquire mutexes, or call a JIT decoder.
//
// Entries are append-only and published with release/acquire ordering.
// Module reloads at the same address prefer the latest entry. Entries live
// until process exit, avoiding reclamation races with asynchronous signals.
#include <atomic>
#include <new>

#include "core/cpu_id.h"

namespace Core::NativeCpuId {

struct TrapEntry {
    uintptr_t address;
    CpuIdInstruction instruction;
    u32 destination;
    u32 length;
    TrapEntry* next;
};

class TrapRegistry final {
public:
    static_assert(std::atomic<TrapEntry*>::is_always_lock_free,
                  "Signal handling requires a lock-free guest trap registry");

    static bool Register(uintptr_t address, CpuIdInstruction instruction, u32 destination,
                         u32 length) noexcept {
        if (!address || length < 2 || length > 15 || destination > 15) {
            return false;
        }
        auto* entry = new (std::nothrow) TrapEntry{
            address, instruction, destination, length, nullptr};
        if (!entry) {
            return false;
        }
        // Publish only after all metadata is initialized, so a concurrent
        // signal handler can traverse the immutable list safely.
        TrapEntry* prior = head.load(std::memory_order_relaxed);
        do {
            entry->next = prior;
        } while (!head.compare_exchange_weak(
            prior, entry, std::memory_order_release, std::memory_order_relaxed));
        return true;
    }

    [[nodiscard]] static const TrapEntry* Lookup(uintptr_t address) noexcept {
        for (auto* entry = head.load(std::memory_order_acquire); entry; entry = entry->next) {
            if (entry->address == address) {
                return entry;
            }
        }
        return nullptr;
    }

private:
    inline static std::atomic<TrapEntry*> head{};
};

} // namespace Core::NativeCpuId
