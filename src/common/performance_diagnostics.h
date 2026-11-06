// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace Common::PerformanceDiagnostics {

enum class Region {
    Draw,
    Dispatch,
    OrderedDs,
    BindBuffers,
    BindTextures,
    ShaderCompile,
    PipelineLookup,
    ImageReadback,
    BufferReadback,
    PageProtect,
    Submit,
    GpuWait,
    BufferClamp,
    Count,
};

struct Counter {
    std::atomic<std::uint64_t> calls{};
    std::atomic<std::uint64_t> inclusive_ns{};
    std::atomic<std::uint64_t> self_ns{};
    std::atomic<std::uint64_t> max_ns{};
    std::atomic<std::uint64_t> bytes{};
    std::atomic<std::uint64_t> last_ns{};
};

inline std::array<Counter, static_cast<std::size_t>(Region::Count)> counters{};
inline const auto started_at = std::chrono::steady_clock::now();

inline bool Enabled() {
    static const bool enabled = [] {
        const char* value = std::getenv("SHADPS4_PERFORMANCE_DIAGNOSTICS");
        return value && std::strcmp(value, "1") == 0;
    }();
    return enabled;
}

class Scope {
public:
    explicit Scope(Region region, std::uint64_t bytes = 0) : region{region}, bytes{bytes} {
        if (Enabled()) {
            parent = current;
            current = this;
            began = std::chrono::steady_clock::now();
        }
    }

    ~Scope() {
        if (!Enabled()) {
            return;
        }
        const auto ended = std::chrono::steady_clock::now();
        const auto elapsed = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(ended - began).count());
        current = parent;
        if (parent) {
            parent->children_ns += elapsed;
        }
        auto& counter = counters[static_cast<std::size_t>(region)];
        counter.calls.fetch_add(1, std::memory_order_relaxed);
        counter.inclusive_ns.fetch_add(elapsed, std::memory_order_relaxed);
        counter.self_ns.fetch_add(elapsed - children_ns, std::memory_order_relaxed);
        counter.bytes.fetch_add(bytes, std::memory_order_relaxed);
        counter.last_ns.store(
            std::chrono::duration_cast<std::chrono::nanoseconds>(ended - started_at).count(),
            std::memory_order_relaxed);
        auto maximum = counter.max_ns.load(std::memory_order_relaxed);
        while (maximum < elapsed &&
               !counter.max_ns.compare_exchange_weak(maximum, elapsed, std::memory_order_relaxed)) {
        }
    }

    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;

private:
    inline static thread_local Scope* current{};
    Region region;
    std::uint64_t bytes;
    std::uint64_t children_ns{};
    Scope* parent{};
    std::chrono::steady_clock::time_point began{};
};

void Start();

} // namespace Common::PerformanceDiagnostics
