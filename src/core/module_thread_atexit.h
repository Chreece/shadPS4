// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <atomic>
#include <limits>
#include "common/types.h"

namespace Core {

// Outstanding guest TLS-destructor references to one loaded module. Modules
// currently remain loaded for the whole process; any future unload path must
// check these references before stopping/unmapping the module.
class ModuleThreadAtexitRefs {
public:
    bool TryAcquire() {
        auto count = references.load(std::memory_order_relaxed);
        while (count != std::numeric_limits<u64>::max()) {
            if (references.compare_exchange_weak(count, count + 1, std::memory_order_acq_rel,
                                                 std::memory_order_relaxed)) {
                return true;
            }
        }
        return false;
    }

    bool TryRelease() {
        auto count = references.load(std::memory_order_relaxed);
        while (count != 0) {
            if (references.compare_exchange_weak(count, count - 1, std::memory_order_acq_rel,
                                                 std::memory_order_relaxed)) {
                return true;
            }
        }
        return false;
    }

    u64 Count() const {
        return references.load(std::memory_order_acquire);
    }

private:
    std::atomic<u64> references{};
};

// Resolve a module address without reading or writing the guest memory there.
// Returned storage remains valid because module unloading is not implemented.
ModuleThreadAtexitRefs* FindModuleThreadAtexitRefs(VAddr address);

} // namespace Core
