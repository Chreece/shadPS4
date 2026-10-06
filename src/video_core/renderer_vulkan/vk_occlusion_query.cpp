// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>
#include <atomic>
#include <condition_variable>
#include <cstring>
#include <mutex>

#include "common/logging/log.h"
#include "core/memory.h"
#include "video_core/occlusion_counter.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_occlusion_query.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan {
namespace Counter = VideoCore::OcclusionCounter;
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

void OcclusionQuery::Control(u32 control, u32, u32) {
    // PIXEL_PIPE_STAT_CONTROL selects the counter to dump/reset. Repeated
    // selections must not toggle counting; DB_COUNT_CONTROL controls each draw.
    selected_counter = Counter::SelectedCounter(control);
}

void OcclusionQuery::Reset() {
    // Reset on the same GPU-completion timeline as older counter snapshots.
    Queue(0, 0, true);
}

std::optional<OcclusionQuery::DrawQuery> OcclusionQuery::PrepareDraw(u32 count_control) {
    const unsigned counters = Counter::MeasuredCounters(count_control);
    const unsigned unmeasured = Counter::ActiveCounters(count_control) & ~counters;
    incomplete |= unmeasured;
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
