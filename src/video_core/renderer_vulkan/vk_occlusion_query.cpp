// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <atomic>
#include <condition_variable>
#include <cstring>
#include <mutex>

#include "common/logging/log.h"
#include "core/memory.h"
#include "video_core/graphics_diagnostics.h"
#include "video_core/occlusion_counter.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_occlusion_query.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan {
namespace Counter = VideoCore::OcclusionCounter;
namespace Diagnostics = VideoCore::GraphicsDiagnostics;
static constexpr u32 PoolSize = 4096;

struct OcclusionQuery::State {
    vk::Device device;
    vk::UniqueQueryPool pool;
    std::array<std::atomic<bool>, PoolSize> busy{};
    std::mutex host_query_mutex;
    std::mutex pending_mutex;
    std::condition_variable pending_cv;
    u32 pending{};
    // Accessed only by the scheduler's FIFO priority callback thread.
    Counter::Totals totals{};
    u64 unsupported_total{};
};

OcclusionQuery::OcclusionQuery(const Instance& instance_, Scheduler& scheduler_)
    : instance{instance_}, scheduler{scheduler_}, state{std::make_shared<State>()} {
    state->device = instance.GetDevice();
    state->pool = Check(state->device.createQueryPoolUnique({
        .queryType = vk::QueryType::eOcclusion,
        .queryCount = PoolSize,
    }));
    LOG_INFO(Render_Vulkan, "Occlusion query writeback enabled (precise={})",
             instance.IsPreciseOcclusionSupported());
}

OcclusionQuery::~OcclusionQuery() {
    // Queue any undumped slots, then finish before dependent caches are destroyed.
    if (!active_queries.empty()) {
        Queue(0, 0, true);
    }
    Drain();
}

void OcclusionQuery::Control(u32 control, u32 high, u32 count_control) {
    // PIXEL_PIPE_STAT_CONTROL selects the counter to dump/reset. Repeated
    // selections must not toggle counting; DB_COUNT_CONTROL controls each draw.
    selected_counter = Counter::SelectedCounter(control);
    Diagnostics::Emit(Diagnostics::Event::QueryControl,
                      "control=%08x high=%08x counter=%u count-control=%08x", control, high,
                      selected_counter, count_control);
}

void OcclusionQuery::Reset() {
    // Reset on the same GPU-completion timeline as older counter snapshots.
    Queue(0, 0, true);
}

std::optional<OcclusionQuery::DrawQuery> OcclusionQuery::PrepareDraw(u32 count_control) {
    const unsigned counters = Counter::MeasuredCounters(count_control);
    const unsigned unmeasured = Counter::ActiveCounters(count_control) & ~counters;
    incomplete |= unmeasured;
    if (last_count_control != count_control) {
        last_count_control = count_control;
        Diagnostics::Emit(Diagnostics::Event::QueryCountState,
                          "count-control=%08x measured-mask=%x unmeasured-mask=%x", count_control,
                          counters, unmeasured);
    }
    if (!counters) {
        return std::nullopt;
    }
    for (u32 attempt = 0; attempt < PoolSize; ++attempt) {
        const u32 slot = cursor++ % PoolSize;
        if (state->busy[slot].exchange(true, std::memory_order_acquire)) {
            continue;
        }
        if (instance.IsHostQueryResetSupported()) {
            std::scoped_lock lock{state->host_query_mutex};
            state->device.resetQueryPool(*state->pool, slot, 1);
        } else {
            scheduler.EndRendering();
            scheduler.CommandBuffer().resetQueryPool(*state->pool, slot, 1);
        }
        return DrawQuery{slot, counters};
    }
    // Bounded exhaustion must not turn an unknown result into invisible geometry.
    incomplete |= counters;
    return std::nullopt;
}

void OcclusionQuery::BeginDraw(vk::CommandBuffer command, std::optional<DrawQuery> query) {
    if (query) {
        const vk::QueryControlFlags flags = instance.IsPreciseOcclusionSupported()
                                                ? vk::QueryControlFlagBits::ePrecise
                                                : vk::QueryControlFlags{};
        command.beginQuery(*state->pool, query->slot, flags);
    }
}

void OcclusionQuery::EndDraw(vk::CommandBuffer command, std::optional<DrawQuery> query) {
    if (query) {
        command.endQuery(*state->pool, query->slot);
        active_queries.push_back(*query);
    }
}

void OcclusionQuery::Dump(VAddr address, u32 pipes) {
    Queue(address, pipes, false);
}

std::optional<bool> OcclusionQuery::EvaluateZpass(VAddr address, u32 pipes, bool wait) {
    if (address == 0 || pipes == 0 || pipes > 16) {
        Diagnostics::Emit(Diagnostics::Event::ZpassEvaluation,
                          "address=%llx pipes=%u wait=%u result=unknown reason=invalid-range",
                          static_cast<unsigned long long>(address), pipes,
                          static_cast<unsigned>(wait));
        return std::nullopt;
    }

    if (wait) {
        Drain();
    } else {
        std::scoped_lock lock{state->pending_mutex};
        if (state->pending != 0) {
            Diagnostics::Emit(Diagnostics::Event::ZpassEvaluation,
                              "address=%llx pipes=%u wait=0 result=unknown reason=pending pending=%u",
                              static_cast<unsigned long long>(address), pipes, state->pending);
            return std::nullopt;
        }
    }

    auto* memory = Core::Memory::Instance();
    const u64 result_size = u64(pipes) * sizeof(u64) * 2;
    if (!memory->IsValidMapping(address, result_size)) {
        Diagnostics::Emit(Diagnostics::Event::ZpassEvaluation,
                          "address=%llx pipes=%u wait=%u result=unknown reason=unmapped",
                          static_cast<unsigned long long>(address), pipes,
                          static_cast<unsigned>(wait));
        return std::nullopt;
    }

    const auto* results = reinterpret_cast<const u64*>(address);
    u32 changed_pipes = 0;
    u64 first_begin = 0;
    u64 first_end = 0;
    for (u32 pipe = 0; pipe < pipes; ++pipe) {
        const u64 begin = results[pipe * 2];
        const u64 end = results[pipe * 2 + 1];
        if (pipe == 0) {
            first_begin = begin;
            first_end = end;
        }
        if ((begin & end & Counter::Valid) == 0) {
            Diagnostics::Emit(
                Diagnostics::Event::ZpassEvaluation,
                "address=%llx pipes=%u wait=%u result=unknown reason=invalid-counter pipe=%u",
                static_cast<unsigned long long>(address), pipes, static_cast<unsigned>(wait), pipe);
            return std::nullopt;
        }
        changed_pipes += (begin & Counter::Mask) != (end & Counter::Mask);
    }
    const bool visible = changed_pipes != 0;
    Diagnostics::Emit(
        Diagnostics::Event::ZpassEvaluation,
        "address=%llx pipes=%u wait=%u result=%u changed-pipes=%u first-begin=%llx first-end=%llx",
        static_cast<unsigned long long>(address), pipes, static_cast<unsigned>(wait),
        static_cast<unsigned>(visible), changed_pipes,
        static_cast<unsigned long long>(first_begin & Counter::Mask),
        static_cast<unsigned long long>(first_end & Counter::Mask));
    return visible;
}

void OcclusionQuery::Queue(VAddr address, u32 pipes, bool reset) {
    auto slots = std::move(active_queries);
    active_queries.clear();
    const unsigned lost_samples = incomplete;
    const unsigned counter = selected_counter;
    incomplete = 0;
    last_pending_tick = scheduler.CurrentTick();
    {
        std::scoped_lock lock{state->pending_mutex};
        ++state->pending;
    }
    // Unlike DeferOperation, priority callbacks run outside the pending-op lock
    // and make progress even if the guest is polling a result without new draws.
    scheduler.DeferPriorityOperation([state = state, slots = std::move(slots), address, pipes,
                                      reset, lost_samples, counter] {
        Counter::Totals samples{};
        unsigned missing = lost_samples;
        {
            std::scoped_lock lock{state->host_query_mutex};
            for (const auto query : slots) {
                u64 value{};
                const auto result = state->device.getQueryPoolResults(
                    *state->pool, query.slot, 1, sizeof(value), &value, sizeof(value),
                    vk::QueryResultFlagBits::e64);
                if (result == vk::Result::eSuccess) {
                    Counter::Accumulate(samples, value, query.counters);
                } else {
                    missing |= query.counters;
                    LOG_WARNING(Render_Vulkan, "Completed occlusion query unavailable: {}",
                                vk::to_string(result));
                }
            }
        }
        for (unsigned bank = 0; bank < samples.size(); ++bank) {
            Counter::Accumulate(state->totals, samples[bank] + ((missing >> bank) & 1), 1u << bank);
        }
        if (reset) {
            Counter::Reset(state->totals, counter);
        }
        if (address && pipes && pipes <= 16) {
            const bool supported = counter < state->totals.size();
            const bool exact = supported && !(missing & (1u << counter));
            // Non-occlusion counter IDs are not implemented; never report them
            // as a successful zero-sample occlusion measurement.
            const u64 total = supported ? state->totals[counter] : ++state->unsupported_total;
            auto* memory = Core::Memory::Instance();
            if (memory->IsAccessibleRange(address, Counter::WriteSpan(pipes),
                                          Core::MemoryProt::CpuWrite)) {
                for (u32 pipe = 0; pipe < pipes; ++pipe) {
                    const u64 value = Counter::Value(total, pipe, pipes);
                    auto* destination = reinterpret_cast<void*>(address + pipe * 16);
                    if (!memory->TryWriteBacking(destination, &value, sizeof(value))) {
                        std::memcpy(destination, &value, sizeof(value));
                    }
                }
                Diagnostics::Emit(Diagnostics::Event::QueryResult,
                                  "address=%llx counter=%u queries=%u samples=%llu total=%llu "
                                  "complete=%u missing-mask=%x",
                                  static_cast<unsigned long long>(address), counter,
                                  static_cast<unsigned>(slots.size()),
                                  static_cast<unsigned long long>(supported ? samples[counter] : 0),
                                  static_cast<unsigned long long>(total),
                                  static_cast<unsigned>(exact), missing);
            } else {
                LOG_WARNING(Render_Vulkan, "Invalid occlusion result address: {:#x}", address);
            }
        }
        // No nested scheduler callback: query reads are complete, and no GPU
        // predicate-buffer copies use these slots in this implementation.
        for (const auto query : slots) {
            state->busy[query.slot].store(false, std::memory_order_release);
        }
        {
            std::scoped_lock lock{state->pending_mutex};
            --state->pending;
        }
        state->pending_cv.notify_all();
    });
}

void OcclusionQuery::SubmitPending() {
    if (last_pending_tick == scheduler.CurrentTick()) {
        scheduler.Flush();
    }
}

void OcclusionQuery::Drain() {
    {
        std::scoped_lock lock{state->pending_mutex};
        if (!state->pending) {
            return;
        }
    }
    scheduler.Finish();
    std::unique_lock lock{state->pending_mutex};
    state->pending_cv.wait(lock, [&] { return state->pending == 0; });
}
} // namespace Vulkan
