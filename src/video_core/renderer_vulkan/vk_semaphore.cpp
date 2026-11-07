// SPDX-FileCopyrightText: Copyright 2020 yuzu Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <chrono>
#include "video_core/gpu_work_diagnostics.h"
#include "video_core/graphics_diagnostics.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_semaphore.h"

#include "common/assert.h"

namespace Vulkan {

constexpr u64 WAIT_TIMEOUT = 1'000'000'000;

Semaphore::Semaphore(const Instance& instance_) : instance{instance_} {
    const vk::StructureChain semaphore_chain = {
        vk::SemaphoreCreateInfo{},
        vk::SemaphoreTypeCreateInfo{
            .semaphoreType = vk::SemaphoreType::eTimeline,
            .initialValue = 0,
        },
    };
    auto [semaphore_result, sem] =
        instance.GetDevice().createSemaphoreUnique(semaphore_chain.get());
    ASSERT_MSG(semaphore_result == vk::Result::eSuccess, "Failed to create master semaphore: {}",
               vk::to_string(semaphore_result));
    semaphore = std::move(sem);
}

Semaphore::~Semaphore() = default;

void Semaphore::Refresh() {
    u64 this_tick{};
    u64 counter{};
    do {
        this_tick = gpu_tick.load(std::memory_order_acquire);
        auto [counter_result, cntr] = instance.GetDevice().getSemaphoreCounterValue(*semaphore);
        ASSERT_MSG(counter_result == vk::Result::eSuccess,
                   "Failed to get master semaphore value: {}", vk::to_string(counter_result));
        counter = cntr;
        if (counter < this_tick) {
            return;
        }
    } while (!gpu_tick.compare_exchange_weak(this_tick, counter, std::memory_order_release,
                                             std::memory_order_relaxed));
}

void Semaphore::Wait(u64 tick) {
    // No need to wait if the GPU is ahead of the tick
    if (IsFree(tick)) {
        return;
    }
    // Update the GPU tick and try again
    Refresh();
    if (IsFree(tick)) {
        return;
    }

    // If none of the above is hit, fallback to a regular wait
    const vk::SemaphoreWaitInfo wait_info = {
        .semaphoreCount = 1,
        .pSemaphores = &semaphore.get(),
        .pValues = &tick,
    };

    const auto began = std::chrono::steady_clock::now();
    unsigned long long timeouts = 0;
    for (;;) {
        const auto result = instance.GetDevice().waitSemaphores(&wait_info, WAIT_TIMEOUT);
        if (result != vk::Result::eSuccess && result != vk::Result::eTimeout) {
            if (wait_diagnostic) {
                wait_diagnostic(tick);
            }
            VideoCore::GpuWorkDiagnostics::Dump("wait-error", this, KnownGpuTick(), tick);
            UNREACHABLE_MSG("Failed to wait for master semaphore: {}", vk::to_string(result));
        }
        const auto elapsed =
            static_cast<unsigned long long>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                                std::chrono::steady_clock::now() - began)
                                                .count());
        if (result == vk::Result::eTimeout) {
            Refresh();
            ++timeouts;
            if (timeouts == 1) {
                if (wait_diagnostic) {
                    wait_diagnostic(tick);
                }
                VideoCore::GpuWorkDiagnostics::Dump("wait-timeout", this, KnownGpuTick(), tick);
            }
        }
        if (result == vk::Result::eTimeout || elapsed >= 1000) {
            VideoCore::GraphicsDiagnostics::Emit(
                VideoCore::GraphicsDiagnostics::Event::GpuWait,
                "semaphore=%p requested=%llu known=%llu elapsed_ms=%llu timeouts=%llu complete=%d",
                static_cast<void*>(this), static_cast<unsigned long long>(tick),
                static_cast<unsigned long long>(KnownGpuTick()), elapsed, timeouts,
                result == vk::Result::eSuccess);
        }
        if (result == vk::Result::eSuccess) {
            break;
        }
    }
    Refresh();
}

} // namespace Vulkan
