// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace Core::CpuId {

// An in-emulator, signal-safe lookup for guest RDPID instructions that cannot
// be expanded into a trampoline. Registration happens while guest executable
// code is being patched; queries happen inside a SIGILL handler.
//
// Published nodes are immutable and are never removed while guest code can
// execute. The signal handler uses only lock-free atomic loads: no allocation,
// mutex, STL map lookup, logging, or calls to the host kernel.
// A reloaded guest module may reuse the same executable address. New registrations
// publish a fresh immutable node at the bucket head; signal readers see either
// the previous complete node or the new complete node without reclamation.
class RdpidTrapTable final {
public:
    struct Site {
        const Site* next;
        uintptr_t address;
        std::uint8_t size;
        std::uint8_t destination;
    };

    RdpidTrapTable() noexcept {
        for (auto& bucket : buckets_) {
            bucket.store(nullptr, std::memory_order_relaxed);
        }
    }

    RdpidTrapTable(const RdpidTrapTable&) = delete;
    RdpidTrapTable& operator=(const RdpidTrapTable&) = delete;

    ~RdpidTrapTable() {
        // The enclosing emulator is shutting down: there must be no live
        // guest execution or signal readers at this point.
        for (auto& bucket : buckets_) {
            auto* item = bucket.load(std::memory_order_relaxed);
            while (item) {
                const auto* next = item->next;
                delete item;
                item = next;
            }
        }
    }

    [[nodiscard]] bool Register(uintptr_t address, unsigned size, unsigned destination) {
        if (address == 0 || size < 4 || size > 15 || destination >= 16) {
            return false;
        }
        auto* site = new Site{nullptr, address, static_cast<std::uint8_t>(size),
                              static_cast<std::uint8_t>(destination)};
        auto& bucket = buckets_[Hash(address)];
        const Site* current = bucket.load(std::memory_order_acquire);
        do {
            site->next = current;
        } while (!bucket.compare_exchange_weak(current, site, std::memory_order_release,
                                               std::memory_order_acquire));
        return true;
    }

    [[nodiscard]] const Site* Lookup(uintptr_t address) const noexcept {
        const Site* site = buckets_[Hash(address)].load(std::memory_order_acquire);
        while (site) {
            if (site->address == address) {
                return site;
            }
            site = site->next;
        }
        return nullptr;
    }

private:
    static constexpr std::size_t BucketCount = 1 << 14;
    static_assert((BucketCount & (BucketCount - 1)) == 0);

    static constexpr std::size_t Hash(uintptr_t address) noexcept {
        return static_cast<std::size_t>((address >> 4) ^ (address >> 13) ^
                                        (address >> 23)) & (BucketCount - 1);
    }

    std::array<std::atomic<const Site*>, BucketCount> buckets_{};
};

} // namespace Core::CpuId
