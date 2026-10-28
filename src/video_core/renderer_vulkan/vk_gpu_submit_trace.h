// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Passive, bounded GPU-work evidence. The key is the owning Scheduler address:
// per-scheduler timeline ticks are NOT interchangeable. No Vulkan calls here.
#include <array>
#include <cstddef>
#include <cstdint>
#include <mutex>

namespace Vulkan::GpuTrace {

enum class Kind : std::uint32_t {
    ComputeDirect = 0,
    ComputeIndirect = 1,
    QueueSubmit = 2,
    Draw = 3,
    DrawIndirect = 4,
};

struct Event {
    std::uint64_t sequence{};
    std::uint64_t tick{};
    std::uint64_t shader{};
    std::uint64_t source{};
    std::uint64_t completed_cached{};
    std::uint32_t arg0{};
    std::uint32_t arg1{};
    std::uint32_t arg2{};
    Kind kind{};
};

constexpr std::size_t MaxSchedulers = 8;
constexpr std::size_t FirstComputeSize = 64;
constexpr std::size_t FirstDrawSize = 32;
constexpr std::size_t FirstSubmitSize = 32;
constexpr std::size_t RecentSize = 128;

struct Snapshot {
    std::uintptr_t scheduler_id{};
    std::uint64_t events{};
    std::uint64_t compute_events{};
    std::uint64_t draw_events{};
    std::uint64_t submit_events{};
    std::uint64_t first_compute_count{};
    std::uint64_t first_draw_count{};
    std::uint64_t first_submit_count{};
    std::uint64_t last_cached_completion{};
    std::array<Event, FirstComputeSize> first_compute{};
    std::array<Event, FirstDrawSize> first_draw{};
    std::array<Event, FirstSubmitSize> first_submit{};
    std::array<Event, RecentSize> recent{};
};

class Recorder {
public:
    void Record(std::uintptr_t scheduler, std::uint64_t tick, Kind kind,
                std::uint64_t shader, std::uint64_t source,
                std::uint32_t arg0 = 0, std::uint32_t arg1 = 0,
                std::uint32_t arg2 = 0, std::uint64_t completed_cached = 0) {
        if (scheduler == 0) {
            return;
        }
        std::scoped_lock lock{mutex_};
        Snapshot* selected = nullptr;
        Snapshot* empty = nullptr;
        for (auto& slot : slots_) {
            if (slot.scheduler_id == scheduler) {
                selected = &slot;
                break;
            }
            if (!empty && slot.scheduler_id == 0) {
                empty = &slot;
            }
        }
        if (!selected) {
            selected = empty;
            if (!selected) {
                ++overflow_scheduler_events_;
                return;
            }
            selected->scheduler_id = scheduler;
        }
        auto& s = *selected;
        const Event event{s.events, tick, shader, source, completed_cached,
                          arg0, arg1, arg2, kind};
        s.recent[s.events % RecentSize] = event;
        ++s.events;
        switch (kind) {
        case Kind::ComputeDirect:
        case Kind::ComputeIndirect:
            if (s.first_compute_count < FirstComputeSize) {
                s.first_compute[s.first_compute_count++] = event;
            }
            ++s.compute_events;
            break;
        case Kind::Draw:
        case Kind::DrawIndirect:
            if (s.first_draw_count < FirstDrawSize) {
                s.first_draw[s.first_draw_count++] = event;
            }
            ++s.draw_events;
            break;
        case Kind::QueueSubmit:
            if (s.first_submit_count < FirstSubmitSize) {
                s.first_submit[s.first_submit_count++] = event;
            }
            ++s.submit_events;
            s.last_cached_completion = completed_cached;
            break;
        }
    }

    [[nodiscard]] Snapshot Capture(std::uintptr_t scheduler) const {
        std::scoped_lock lock{mutex_};
        for (const auto& slot : slots_) {
            if (slot.scheduler_id == scheduler) {
                return slot;
            }
        }
        return {};
    }

    [[nodiscard]] std::uint64_t OverflowEvents() const {
        std::scoped_lock lock{mutex_};
        return overflow_scheduler_events_;
    }

private:
    mutable std::mutex mutex_;
    std::array<Snapshot, MaxSchedulers> slots_{};
    std::uint64_t overflow_scheduler_events_{};
};

// One shared recorder across rasterizer.cpp and scheduler.cpp, not one per TU.
inline Recorder recorder{};

} // namespace Vulkan::GpuTrace
