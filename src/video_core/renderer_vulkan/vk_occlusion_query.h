// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <memory>
#include <optional>
#include <vector>
#include "common/types.h"
#include "video_core/renderer_vulkan/vk_platform.h"

namespace Vulkan {
class Instance;
class Scheduler;

// Query/readback portion adapted from cuesta4's upstream PR #4610.
class OcclusionQuery {
public:
    struct DrawQuery {
        u32 slot;
        unsigned counters;
    };
    OcclusionQuery(const Instance& instance, Scheduler& scheduler);
    ~OcclusionQuery();
    void Control(u32 control, u32 high, u32 count_control);
    void Reset();
    void Dump(VAddr address, u32 pipes);
    std::optional<bool> EvaluateZpass(VAddr address, u32 pipes, bool wait);
    std::optional<DrawQuery> PrepareDraw(u32 count_control);
    void BeginDraw(vk::CommandBuffer command, std::optional<DrawQuery> query);
    void EndDraw(vk::CommandBuffer command, std::optional<DrawQuery> query);
    void SubmitPending();
    void Drain();

private:
    struct State;
    void Queue(VAddr address, u32 pipes, bool reset);
    const Instance& instance;
    Scheduler& scheduler;
    std::shared_ptr<State> state;
    std::vector<DrawQuery> active_queries;
    u32 cursor{};
    u64 last_pending_tick{};
    unsigned selected_counter{};
    unsigned incomplete{};
    std::optional<u32> last_count_control;
};
} // namespace Vulkan
