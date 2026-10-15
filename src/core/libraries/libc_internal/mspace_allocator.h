// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <iterator>
#include <map>
#include <mutex>
#include <optional>
#include <utility>

namespace Libraries::LibcInternal::Mspace {

// All addresses are guest pointers into memory already mapped by sceKernelMap*.
// Keep bookkeeping on the host, but never allocate guest objects on the host heap.
class Registry {
    using Addr = std::uintptr_t;
    static constexpr std::size_t MinAlignment = 16;
    static constexpr std::size_t ReservedHeader = 4096;

    struct Arena {
        Addr begin{};
        Addr end{};
        std::map<Addr, std::size_t> available;
        std::map<Addr, std::size_t> allocated;
        std::size_t in_use{};
        std::size_t peak_use{};
    };
    mutable std::mutex mutex;
    std::map<Addr, Arena> arenas;

    static bool PowerOfTwo(std::size_t n) { return n && (n & (n - 1)) == 0; }

    static std::optional<Addr> Align(Addr address, std::size_t alignment) {
        if (!PowerOfTwo(alignment) || address > std::numeric_limits<Addr>::max() - (alignment - 1)) {
            return std::nullopt;
        }
        return (address + alignment - 1) & ~static_cast<Addr>(alignment - 1);
    }

    static std::optional<std::size_t> RoundSize(std::size_t bytes) {
        bytes = std::max<std::size_t>(bytes, MinAlignment);
        if (bytes > std::numeric_limits<std::size_t>::max() - (MinAlignment - 1)) {
            return std::nullopt;
        }
        return (bytes + MinAlignment - 1) & ~(MinAlignment - 1);
    }

    static void Release(Arena& arena, Addr address, std::size_t bytes) {
        auto after = arena.available.lower_bound(address);
        if (after != arena.available.end() && address + bytes == after->first) {
            bytes += after->second;
            after = arena.available.erase(after);
        }
        if (after != arena.available.begin()) {
            auto before = std::prev(after);
            if (before->first + before->second == address) {
                address = before->first;
                bytes += before->second;
                arena.available.erase(before);
            }
        }
        arena.available.emplace(address, bytes);
    }

    static void* Allocate(Arena& arena, std::size_t bytes, std::size_t alignment) {
        const auto size = RoundSize(bytes);
        if (!size || !PowerOfTwo(alignment)) {
            return nullptr;
        }
        alignment = std::max(alignment, MinAlignment);
        for (auto it = arena.available.begin(); it != arena.available.end(); ++it) {
            const auto aligned = Align(it->first, alignment);
            if (!aligned || *aligned < it->first) {
                continue;
            }
            const std::size_t skipped = *aligned - it->first;
            if (skipped > it->second || *size > it->second - skipped) {
                continue;
            }
            const Addr old_begin = it->first;
            const std::size_t old_size = it->second;
            arena.available.erase(it);
            if (skipped) {
                arena.available.emplace(old_begin, skipped);
            }
            const std::size_t tail = old_size - skipped - *size;
            if (tail) {
                arena.available.emplace(*aligned + *size, tail);
            }
            arena.allocated.emplace(*aligned, *size);
            arena.in_use += *size;
            arena.peak_use = std::max(arena.peak_use, arena.in_use);
            return reinterpret_cast<void*>(*aligned);
        }
        return nullptr;
    }

    static bool Deallocate(Arena& arena, void* pointer) {
        if (!pointer) {
            return true;
        }
        const Addr address = reinterpret_cast<Addr>(pointer);
        auto found = arena.allocated.find(address);
        if (found == arena.allocated.end()) {
            return false;
        }
        const auto bytes = found->second;
        arena.allocated.erase(found);
        arena.in_use -= bytes;
        Release(arena, address, bytes);
        return true;
    }

public:
    void* Create(void* base, std::size_t capacity) {
        if (!base || capacity <= ReservedHeader) {
            return nullptr;
        }
        const Addr start = reinterpret_cast<Addr>(base);
        if (start > std::numeric_limits<Addr>::max() - capacity) {
            return nullptr;
        }
        const Addr end = start + capacity;
        const auto data = Align(start + ReservedHeader, MinAlignment);
        if (!data || *data >= end) {
            return nullptr;
        }
        std::scoped_lock lock(mutex);
        auto next = arenas.lower_bound(start);
        if ((next != arenas.end() && end > next->second.begin) ||
            (next != arenas.begin() && std::prev(next)->second.end > start)) {
            return nullptr;
        }
        Arena arena;
        arena.begin = start;
        arena.end = end;
        arena.available.emplace(*data, end - *data);
        arenas.emplace(start, std::move(arena));
        return base;  // Opaque guest-address handle; no host pointers escape into guest state.
    }

    bool Destroy(void* handle) {
        std::scoped_lock lock(mutex);
        return arenas.erase(reinterpret_cast<Addr>(handle)) == 1;
    }

    void* Malloc(void* handle, std::size_t bytes, std::size_t alignment = MinAlignment) {
        std::scoped_lock lock(mutex);
        auto a = arenas.find(reinterpret_cast<Addr>(handle));
        return a == arenas.end() ? nullptr : Allocate(a->second, bytes, alignment);
    }

    bool Free(void* handle, void* pointer) {
        std::scoped_lock lock(mutex);
        auto a = arenas.find(reinterpret_cast<Addr>(handle));
        return a != arenas.end() && Deallocate(a->second, pointer);
    }

    void* Calloc(void* handle, std::size_t count, std::size_t size) {
        if (size && count > std::numeric_limits<std::size_t>::max() / size) {
            return nullptr;
        }
        const std::size_t bytes = count * size;
        void* p = Malloc(handle, bytes);
        if (p) {
            std::memset(p, 0, bytes);
        }
        return p;
    }

    void* Realloc(void* handle, void* pointer, std::size_t new_size,
                  std::size_t alignment = MinAlignment) {
        std::scoped_lock lock(mutex);
        auto a = arenas.find(reinterpret_cast<Addr>(handle));
        if (a == arenas.end()) {
            return nullptr;
        }
        Arena& arena = a->second;
        if (!pointer) {
            return Allocate(arena, new_size, alignment);
        }
        if (!new_size) {
            Deallocate(arena, pointer);
            return nullptr;
        }
        const Addr address = reinterpret_cast<Addr>(pointer);
        const auto current = arena.allocated.find(address);
        if (current == arena.allocated.end()) {
            return nullptr;
        }
        const std::size_t old_size = current->second;
        const auto rounded = RoundSize(new_size);
        if (!rounded || !PowerOfTwo(alignment)) {
            return nullptr;
        }
        alignment = std::max(alignment, MinAlignment);
        if (address % alignment == 0) {
            if (*rounded <= old_size) {
                const std::size_t remaining = old_size - *rounded;
                current->second = *rounded;
                arena.in_use -= remaining;
                if (remaining) {
                    Release(arena, address + *rounded, remaining);
                }
                return pointer;
            }
            auto tail = arena.available.find(address + old_size);
            if (tail != arena.available.end() && tail->second >= *rounded - old_size) {
                const std::size_t consume = *rounded - old_size;
                const std::size_t rest = tail->second - consume;
                arena.available.erase(tail);
                if (rest) {
                    arena.available.emplace(address + *rounded, rest);
                }
                current->second = *rounded;
                arena.in_use += consume;
                arena.peak_use = std::max(arena.peak_use, arena.in_use);
                return pointer;
            }
        }
        void* replacement = Allocate(arena, new_size, alignment);
        if (!replacement) {
            return nullptr; // retain old allocation on failure
        }
        std::memcpy(replacement, pointer, std::min(old_size, new_size));
        Deallocate(arena, pointer);
        return replacement;
    }

    std::size_t UsableSize(void* pointer) const {
        std::scoped_lock lock(mutex);
        const Addr address = reinterpret_cast<Addr>(pointer);
        for (const auto& [_, arena] : arenas) {
            const auto entry = arena.allocated.find(address);
            if (entry != arena.allocated.end()) {
                return entry->second;
            }
        }
        return 0;
    }

    struct Stats {
        std::uint64_t max_system_size{};
        std::uint64_t current_system_size{};
        std::uint64_t max_inuse_size{};
        std::uint64_t current_inuse_size{};
    };

    std::optional<Stats> GetStats(void* handle) const {
        std::scoped_lock lock(mutex);
        const auto a = arenas.find(reinterpret_cast<Addr>(handle));
        if (a == arenas.end()) {
            return std::nullopt;
        }
        const auto& arena = a->second;
        const auto capacity = static_cast<std::uint64_t>(arena.end - arena.begin);
        return Stats{capacity, capacity, arena.peak_use, arena.in_use};
    }

    bool IsHeapEmpty(void* handle) const {
        std::scoped_lock lock(mutex);
        const auto a = arenas.find(reinterpret_cast<Addr>(handle));
        return a != arenas.end() && a->second.allocated.empty();
    }
};

} // namespace Libraries::LibcInternal::Mspace
