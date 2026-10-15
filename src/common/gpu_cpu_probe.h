// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

// Diagnostic-only CPU time accounting for generic renderer/resource paths.
// Disabled unless SHADPS4_CPU_TIMING_CSV points at a writable output file.
// Independent of game, shader identity, GPU configuration and render behavior.
//
// Each scope records inclusive CPU-side wall time. Nested scopes are NOT
// additive; compare categories and counts rather than summing totals.
//
// Cumulative snapshots are flushed every two seconds, including during runs
// terminated without a C++ destructor (for example bounded gameplay probes).
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <stop_token>
#include <thread>

namespace Common::GpuCpuProbe {

enum class Slot : unsigned {
    BindBuffers,
    ClampRangeSize,
    ObtainBuffer,
    EnsureResident,
    SynchronizeMemory,
    GraphicsPipeline,
    ComputePipeline,
    CompileShaderModule,
    ClampedDescriptorLog,
    Count,
};

inline constexpr std::array<const char*, static_cast<unsigned>(Slot::Count)> Names{
    "BindBuffers", "ClampRangeSize", "ObtainBuffer", "EnsureResident",
    "SynchronizeMemory", "GraphicsPipeline", "ComputePipeline",
    "CompileShaderModule", "ClampedDescriptorLog",
};

struct Counters {
    std::atomic<std::uint64_t> calls{};
    std::atomic<std::uint64_t> total_ns{};
    std::atomic<std::uint64_t> max_ns{};
    std::atomic<std::uint64_t> slow_5ms{};
    std::atomic<std::uint64_t> slow_20ms{};
};

class Recorder final {
public:
    Recorder(const Recorder&) = delete;
    Recorder& operator=(const Recorder&) = delete;

    static Recorder& Instance() {
        static Recorder recorder;
        return recorder;
    }

    bool Enabled() const {
        return output_ != nullptr;
    }

    void Record(Slot slot, std::uint64_t nanoseconds) noexcept {
        if (!Enabled()) {
            return;
        }
        auto& entry = counters_[static_cast<unsigned>(slot)];
        entry.total_ns.fetch_add(nanoseconds, std::memory_order_relaxed);
        entry.calls.fetch_add(1, std::memory_order_relaxed);
        if (nanoseconds > 5'000'000) {
            entry.slow_5ms.fetch_add(1, std::memory_order_relaxed);
        }
        if (nanoseconds > 20'000'000) {
            entry.slow_20ms.fetch_add(1, std::memory_order_relaxed);
        }
        std::uint64_t prior = entry.max_ns.load(std::memory_order_relaxed);
        while (nanoseconds > prior &&
               !entry.max_ns.compare_exchange_weak(prior, nanoseconds,
                                                   std::memory_order_relaxed)) {}
    }

    // Called by the writer thread (and only after it is joined in destructor).
    void FlushSnapshot() {
        if (!output_) {
            return;
        }
        const auto timestamp = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
        for (unsigned idx = 0; idx < counters_.size(); ++idx) {
            const auto& c = counters_[idx];
            std::fprintf(output_, "%lld,%s,%llu,%llu,%llu,%llu,%llu\n",
                         static_cast<long long>(timestamp), Names[idx],
                         static_cast<unsigned long long>(
                             c.calls.load(std::memory_order_relaxed)),
                         static_cast<unsigned long long>(
                             c.total_ns.load(std::memory_order_relaxed)),
                         static_cast<unsigned long long>(
                             c.max_ns.load(std::memory_order_relaxed)),
                         static_cast<unsigned long long>(
                             c.slow_5ms.load(std::memory_order_relaxed)),
                         static_cast<unsigned long long>(
                             c.slow_20ms.load(std::memory_order_relaxed)));
        }
        std::fflush(output_);
    }

private:
    Recorder() {
        const char* file = std::getenv("SHADPS4_CPU_TIMING_CSV");
        if (!file || !*file) {
            return;
        }
        output_ = std::fopen(file, "wb");
        if (!output_) {
            return;
        }
        std::setvbuf(output_, nullptr, _IOFBF, 131072);
        std::fputs("steady_ns,region,calls,total_ns,max_ns,slow_5ms,slow_20ms\n",
                   output_);
        std::fflush(output_);
        worker_ = std::jthread([this](std::stop_token token) {
            while (!token.stop_requested()) {
                std::this_thread::sleep_for(std::chrono::seconds(2));
                if (!token.stop_requested()) {
                    FlushSnapshot();
                }
            }
        });
    }

    ~Recorder() {
        if (worker_.joinable()) {
            worker_.request_stop();
            worker_.join();
        }
        if (output_) {
            FlushSnapshot();
            std::fclose(output_);
        }
    }

    std::FILE* output_{};
    std::array<Counters, static_cast<unsigned>(Slot::Count)> counters_{};
    std::jthread worker_{};
};

class Scope final {
public:
    explicit Scope(Slot slot) : slot_{slot}, recorder_{&Recorder::Instance()} {
        if (recorder_->Enabled()) {
            start_ = std::chrono::steady_clock::now();
        }
    }

    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;

    ~Scope() {
        if (!recorder_->Enabled()) {
            return;
        }
        const auto duration = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - start_).count();
        if (duration >= 0) {
            recorder_->Record(slot_, static_cast<std::uint64_t>(duration));
        }
    }

private:
    Slot slot_;
    Recorder* recorder_;
    std::chrono::steady_clock::time_point start_{};
};

} // namespace Common::GpuCpuProbe
