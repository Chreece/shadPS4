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
    OcclusionQuery(const Instance& instance, Scheduler& scheduler);
    ~OcclusionQuery();
    void Control();
    void Reset();
    void Dump(VAddr address, u32 pipes);
    std::optional<u32> PrepareDraw();
    void BeginDraw(vk::CommandBuffer command, std::optional<u32> query);
    void EndDraw(vk::CommandBuffer command, std::optional<u32> query);
    void SubmitPending();
    void Drain();

private:
    struct State;
    void Queue(VAddr address, u32 pipes, bool reset);
    const Instance& instance;
    Scheduler& scheduler;
    std::shared_ptr<State> state;
    std::vector<u32> active_queries;
    u32 cursor{};
    u64 last_pending_tick{};
    bool seen_control{};
    bool control_enabled{};
    bool seen_dump{};
    bool incomplete{};
};
} // namespace Vulkan
