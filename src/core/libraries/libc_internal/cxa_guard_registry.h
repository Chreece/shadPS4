// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <thread>
#include <unordered_map>

namespace Libraries::LibcInternal::CxaGuard {

// Itanium C++ ABI: first guard byte is set after successful initialization.
// Per-guard host bookkeeping provides the 'initialization in progress' state,
// without guessing PS4-libc's representation of the other seven guard bytes.
class Registry {
    std::mutex mutex;
    std::condition_variable cv;
    std::unordered_map<std::uintptr_t, std::thread::id> in_progress;

public:
    int Acquire(std::uint64_t* guard) {
        if (!guard) {
            return 0;
        }
        const auto key = reinterpret_cast<std::uintptr_t>(guard);
        auto* initialized = reinterpret_cast<std::uint8_t*>(guard);
        std::unique_lock lock(mutex);
        for (;;) {
            const auto it = in_progress.find(key);
            if (it == in_progress.end()) {
                break;
            }
            if (it->second == std::this_thread::get_id()) {
                return 0; // Recursive initialization is undefined; avoid deadlock.
            }
            cv.wait(lock);
        }
        if (std::atomic_ref<std::uint8_t>(*initialized).load(std::memory_order_acquire)) {
            return 0;
        }
        in_progress.emplace(key, std::this_thread::get_id());
        return 1;
    }

    void Release(std::uint64_t* guard) {
        if (!guard) {
            return;
        }
        auto* initialized = reinterpret_cast<std::uint8_t*>(guard);
        {
            std::scoped_lock lock(mutex);
            std::atomic_ref<std::uint8_t>(*initialized).store(1, std::memory_order_release);
            in_progress.erase(reinterpret_cast<std::uintptr_t>(guard));
        }
        cv.notify_all();
    }

    void Abort(std::uint64_t* guard) {
        if (!guard) {
            return;
        }
        {
            std::scoped_lock lock(mutex);
            in_progress.erase(reinterpret_cast<std::uintptr_t>(guard));
        }
        cv.notify_all();
    }
};

} // namespace Libraries::LibcInternal::CxaGuard
