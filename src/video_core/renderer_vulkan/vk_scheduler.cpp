// SPDX-FileCopyrightText: Copyright 2025 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "common/assert.h"
#include "common/debug.h"
#include "common/performance_diagnostics.h"
#include "common/thread.h"
#include "imgui/renderer/texture_manager.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan {

std::mutex Scheduler::submit_mutex;

struct Scheduler::GpuProgress {
    static constexpr u32 QueriesPerPool = 4096;
    static constexpr u32 MaxPools = 8;
    struct Pool {
        vk::UniqueQueryPool handle;
        std::array<u64, QueriesPerPool> sequences{};
        std::array<u64, QueriesPerPool> ticks{};
        u32 used{};
        u64 tick{};
    };
    const Instance& instance;
    Semaphore& semaphore;
    std::mutex mutex;
    std::vector<Pool> pools;
    u32 active{};
    u64 skipped{};
    u64 retired_sequence{};
    bool supported{};

    GpuProgress(const Instance& instance, Semaphore& semaphore)
        : instance{instance}, semaphore{semaphore} {
        const auto families = instance.GetPhysicalDevice().getQueueFamilyProperties();
        supported = instance.IsHostQueryResetSupported() &&
                    families.at(instance.GetGraphicsQueueFamilyIndex()).timestampValidBits != 0;
        std::fprintf(stderr, "GRAPHICS_WORK_PROGRESS_ENABLED semaphore=%p supported=%u\n",
                     static_cast<void*>(&semaphore), unsigned(supported));
    }

    void Record(vk::CommandBuffer command, u64 sequence, u64 tick) {
        if (!supported) {
            return;
        }
        std::scoped_lock lock{mutex};
        if (pools.empty() || pools[active].used == QueriesPerPool) {
            auto reusable = std::ranges::find_if(pools, [&](const Pool& pool) {
                return pool.tick < tick && semaphore.IsFree(pool.tick);
            });
            if (reusable != pools.end()) {
                active = u32(reusable - pools.begin());
                retired_sequence =
                    std::max(retired_sequence, reusable->sequences[reusable->used - 1]);
                reusable->used = 0;
                reusable->tick = 0;
            } else if (pools.size() < MaxPools) {
                auto [result, handle] =
                    instance.GetDevice().createQueryPoolUnique(vk::QueryPoolCreateInfo{
                        .queryType = vk::QueryType::eTimestamp, .queryCount = QueriesPerPool});
                if (result != vk::Result::eSuccess) {
                    supported = false;
                    std::fprintf(stderr, "GRAPHICS_WORK_PROGRESS_DISABLED result=%d\n",
                                 int(result));
                    return;
                }
                active = u32(pools.size());
                pools.push_back(Pool{.handle = std::move(handle)});
            } else {
                ++skipped;
                return;
            }
            instance.GetDevice().resetQueryPool(*pools[active].handle, 0, QueriesPerPool);
        }
        auto& pool = pools[active];
        pool.tick = std::max(pool.tick, tick);
        pool.sequences[pool.used] = sequence;
        pool.ticks[pool.used] = tick;
        command.writeTimestamp2(vk::PipelineStageFlagBits2::eAllCommands, *pool.handle,
                                pool.used++);
    }

    void Dump(u64 requested) {
        std::scoped_lock lock{mutex};
        u64 last_complete = retired_sequence;
        std::vector<u64> pending;
        for (const auto& pool : pools) {
            if (!pool.used) {
                continue;
            }
            std::array<std::array<u64, 2>, QueriesPerPool> results{};
            const auto result = instance.GetDevice().getQueryPoolResults(
                *pool.handle, 0, pool.used, pool.used * sizeof(results[0]), results.data(),
                sizeof(results[0]),
                vk::QueryResultFlagBits::e64 | vk::QueryResultFlagBits::eWithAvailability);
            if (result != vk::Result::eSuccess && result != vk::Result::eNotReady) {
                std::fprintf(stderr, "GRAPHICS_WORK_PROGRESS_UNAVAILABLE result=%d\n", int(result));
                continue;
            }
            for (u32 i = 0; i < pool.used; ++i) {
                if (pool.ticks[i] > requested) {
                    continue;
                }
                if (results[i][1]) {
                    last_complete = std::max(last_complete, pool.sequences[i]);
                } else {
                    pending.push_back(pool.sequences[i]);
                }
            }
        }
        u64 first_pending = std::numeric_limits<u64>::max();
        for (const auto sequence : pending) {
            if (sequence > last_complete) {
                first_pending = std::min(first_pending, sequence);
            }
        }
        std::fprintf(stderr,
                     "GRAPHICS_WORK_PROGRESS semaphore=%p before-completed-seq=%llu "
                     "first-unavailable-seq=%llu skipped=%llu supported=%u\n",
                     static_cast<void*>(&semaphore), static_cast<unsigned long long>(last_complete),
                     static_cast<unsigned long long>(first_pending),
                     static_cast<unsigned long long>(skipped), unsigned(supported));
        std::fflush(stderr);
    }
};

void Scheduler::RecordGpuProgress(u64 sequence, const char* format) const {
    if (gpu_progress && !std::string_view{format}.starts_with("kind=shader") &&
        !std::string_view{format}.starts_with("kind=texture-")) {
        gpu_progress->Record(CommandBuffer(), sequence, CurrentTick());
    }
}

Scheduler::Scheduler(const Instance& instance)
    : instance{instance}, work_semaphore{instance}, command_pool{instance, &work_semaphore} {
#if TRACY_GPU_ENABLED
    profiler_scope = reinterpret_cast<tracy::VkCtxScope*>(std::malloc(sizeof(tracy::VkCtxScope)));
#endif
    const char* progress = std::getenv("SHADPS4_GPU_PROGRESS");
    if (progress && std::string_view{progress} == "1" &&
        VideoCore::GraphicsDiagnostics::Enabled() && !Common::PerformanceDiagnostics::Enabled()) {
        gpu_progress = std::make_unique<GpuProgress>(instance, work_semaphore);
        work_semaphore.SetWaitDiagnostic([this](u64 requested) { gpu_progress->Dump(requested); });
    }
    BeginSession();
    priority_pending_ops_thread =
        std::jthread(std::bind_front(&Scheduler::PriorityPendingOpsThread, this));
}

Scheduler::~Scheduler() {
#if TRACY_GPU_ENABLED
    std::free(profiler_scope);
#endif
}

static vk::AttachmentStoreOp DepthStencilStoreOp(vk::ImageLayout layout,
                                                 vk::ImageAspectFlagBits aspect) {
    switch (layout) {
    case vk::ImageLayout::eDepthStencilReadOnlyOptimal:
    case vk::ImageLayout::eReadOnlyOptimal:
        return vk::AttachmentStoreOp::eNone;
    case vk::ImageLayout::eDepthReadOnlyOptimal:
    case vk::ImageLayout::eDepthReadOnlyStencilAttachmentOptimal:
        return aspect == vk::ImageAspectFlagBits::eDepth ? vk::AttachmentStoreOp::eNone
                                                         : vk::AttachmentStoreOp::eStore;
    case vk::ImageLayout::eStencilReadOnlyOptimal:
    case vk::ImageLayout::eDepthAttachmentStencilReadOnlyOptimal:
        return aspect == vk::ImageAspectFlagBits::eStencil ? vk::AttachmentStoreOp::eNone
                                                           : vk::AttachmentStoreOp::eStore;
    default:
        return vk::AttachmentStoreOp::eStore;
    }
}

void Scheduler::BeginRendering(const RenderState& new_state) {
    if (is_rendering && render_state == new_state) {
        return;
    }
    EndRendering();
    is_rendering = true;
    render_state = new_state;

    std::array<vk::RenderingAttachmentInfo, 8> color_attachments;
    for (u32 i = 0; i < render_state.num_color_attachments; ++i) {
        const auto& cb = render_state.color_attachments[i];
        color_attachments[i] = vk::RenderingAttachmentInfo{
            .imageView = cb.image_view,
            .imageLayout = cb.image_layout,
            .loadOp = cb.is_clear ? vk::AttachmentLoadOp::eClear : vk::AttachmentLoadOp::eLoad,
            .storeOp = vk::AttachmentStoreOp::eStore,
            .clearValue = vk::ClearValue{.color = vk::ClearColorValue{.uint32 = cb.clear_value}},
        };
    }

    const auto& db = render_state.depth_stencil_attachment;
    const vk::RenderingAttachmentInfo depth_attachment = {
        .imageView = db.image_view,
        .imageLayout = db.image_layout,
        .loadOp = db.depth_clear ? vk::AttachmentLoadOp::eClear : vk::AttachmentLoadOp::eLoad,
        .storeOp = DepthStencilStoreOp(db.image_layout, vk::ImageAspectFlagBits::eDepth),
        .clearValue =
            vk::ClearValue{.depthStencil = vk::ClearDepthStencilValue{.depth = std::bit_cast<float>(
                                                                          db.clear_value[0])}},
    };
    const vk::RenderingAttachmentInfo stencil_attachment = {
        .imageView = db.image_view,
        .imageLayout = db.image_layout,
        .loadOp = db.stencil_clear ? vk::AttachmentLoadOp::eClear : vk::AttachmentLoadOp::eLoad,
        .storeOp = DepthStencilStoreOp(db.image_layout, vk::ImageAspectFlagBits::eStencil),
        .clearValue = vk::ClearValue{.depthStencil =
                                         vk::ClearDepthStencilValue{.stencil = db.clear_value[1]}},
    };

    const vk::RenderingInfo rendering_info = {
        .renderArea =
            {
                .offset = {0, 0},
                .extent = {render_state.width, render_state.height},
            },
        .layerCount = render_state.num_layers,
        .colorAttachmentCount = render_state.num_color_attachments,
        .pColorAttachments = color_attachments.data(),
        .pDepthAttachment = db.has_depth ? &depth_attachment : nullptr,
        .pStencilAttachment = db.has_stencil ? &stencil_attachment : nullptr,
    };

    CommandBuffer().beginRendering(rendering_info);
}

void Scheduler::EndRendering() {
    if (!is_rendering) {
        return;
    }
    is_rendering = false;
    CommandBuffer().endRendering();
}

vk::CommandBuffer Scheduler::UploadCommandBuffer() {
    auto& upload_cmdbuf = sessions.back().upload;
    if (upload_cmdbuf) {
        return upload_cmdbuf;
    }
    const vk::CommandBufferBeginInfo begin_info = {
        .flags = vk::CommandBufferUsageFlagBits::eOneTimeSubmit,
    };
    upload_cmdbuf = command_pool.Commit();
    Check(upload_cmdbuf.begin(begin_info));
    return upload_cmdbuf;
}

void Scheduler::Flush(SubmitInfo& info) {
    // When flushing, we only send data to the driver; no waiting is necessary.
    SubmitExecution(info);
}

void Scheduler::Flush() {
    SubmitInfo info{};
    Flush(info);
}

void Scheduler::Finish() {
    Common::PerformanceDiagnostics::Scope perf{Common::PerformanceDiagnostics::Region::GpuWait};
    // When finishing, we need to wait for the submission to have executed on the device.
    const u64 presubmit_tick = CurrentTick();
    SubmitInfo info{};
    SubmitExecution(info);
    Wait(presubmit_tick);
}

void Scheduler::Wait(u64 tick) {
    if (tick >= work_semaphore.CurrentTick()) {
        // Make sure we are not waiting for the current tick without signalling
        SubmitInfo info{};
        Flush(info);
    }
    work_semaphore.Wait(tick);
}

void Scheduler::PopPendingOperations() {
    if (pending_ops_empty.load(std::memory_order_relaxed)) {
        return;
    }
    std::unique_lock lk(pending_ops_mutex);
    work_semaphore.Refresh();
    while (!pending_ops.empty() && work_semaphore.IsFree(pending_ops.front().gpu_tick)) {
        pending_ops.front().callback();
        pending_ops.pop();
    }
    pending_ops_empty.store(pending_ops.empty(), std::memory_order_relaxed);
}

void Scheduler::BeginSession() {
    EndSession();

    auto& session = sessions.emplace_back();
    session.tick = CurrentTick();

    const vk::CommandBufferBeginInfo begin_info = {
        .flags = vk::CommandBufferUsageFlagBits::eOneTimeSubmit,
    };
    session.primary = command_pool.Commit();
    Check(session.primary.begin(begin_info));

    // Invalidate dynamic state so it gets applied to the new command buffer.
    dynamic_state.Invalidate();

#if TRACY_GPU_ENABLED
    auto* profiler_ctx = instance.GetProfilerContext();
    if (profiler_ctx) {
        static const auto scope_loc =
            GPU_SCOPE_LOCATION("Guest Frame", MarkersPalette::GpuMarkerColor);
        new (profiler_scope) tracy::VkCtxScope{profiler_ctx, &scope_loc, session.primary, true};
    }
#endif
}

void Scheduler::EndSession() {
    if (sessions.empty()) {
        return;
    }

    if (on_session) {
        on_session();
    }

    const auto& session = sessions.back();
    if (session.upload) {
        Check(session.upload.end());
    }

    EndRendering();
    const auto sequence =
        VideoCore::GpuWorkDiagnostics::Record(&work_semaphore, session.tick, "kind=session-end");
    if (sequence && gpu_progress) {
        gpu_progress->Record(session.primary, sequence, session.tick);
    }
#if TRACY_GPU_ENABLED
    auto* profiler_ctx = instance.GetProfilerContext();
    if (profiler_ctx) {
        profiler_scope->~VkCtxScope();
        TracyVkCollect(profiler_ctx, session.primary);
    }
#endif
    Check(session.primary.end());
}

void Scheduler::SubmitExecution(SubmitInfo& info) {
    Common::PerformanceDiagnostics::Scope perf{Common::PerformanceDiagnostics::Region::Submit};
    std::scoped_lock lk{submit_mutex};
    const u64 signal_value = work_semaphore.NextTick();

    if (on_submit) {
        on_submit(info);
    }

    EndSession();

    std::vector<vk::CommandBuffer> cmd_buffers;
    cmd_buffers.reserve(sessions.size() * 2);

    for (const auto& session : sessions) {
        if (session.upload) {
            cmd_buffers.push_back(session.upload);
        }
        cmd_buffers.push_back(session.primary);
    }
    sessions.clear();

    VideoCore::GpuWorkDiagnostics::Record(&work_semaphore, signal_value,
                                          "kind=submit command-buffers=%zu waits=%u",
                                          cmd_buffers.size(), info.num_wait_semas);
    const vk::Semaphore timeline = work_semaphore.Handle();
    info.AddSignal(timeline, signal_value);

    std::array<vk::PipelineStageFlags, std::tuple_size_v<decltype(info.wait_semas)>>
        wait_stage_masks;
    wait_stage_masks.fill(vk::PipelineStageFlagBits::eAllCommands);

    const vk::TimelineSemaphoreSubmitInfo timeline_si = {
        .waitSemaphoreValueCount = info.num_wait_semas,
        .pWaitSemaphoreValues = info.wait_ticks.data(),
        .signalSemaphoreValueCount = info.num_signal_semas,
        .pSignalSemaphoreValues = info.signal_ticks.data(),
    };

    const vk::SubmitInfo submit_info = {
        .pNext = &timeline_si,
        .waitSemaphoreCount = info.num_wait_semas,
        .pWaitSemaphores = info.wait_semas.data(),
        .pWaitDstStageMask = wait_stage_masks.data(),
        .commandBufferCount = static_cast<u32>(cmd_buffers.size()),
        .pCommandBuffers = cmd_buffers.data(),
        .signalSemaphoreCount = info.num_signal_semas,
        .pSignalSemaphores = info.signal_semas.data(),
    };

    ImGui::Core::TextureManager::Submit();
    auto submit_result = instance.GetGraphicsQueue().submit(submit_info, info.fence);
    ASSERT_MSG(submit_result != vk::Result::eErrorDeviceLost, "Device lost during submit");

    work_semaphore.Refresh();
    BeginSession();

    // Apply pending operations
    PopPendingOperations();
}

void Scheduler::PriorityPendingOpsThread(std::stop_token stoken) {
    Common::SetCurrentThreadName("shadPS4:GpuSchedPriorityPendingOpsRunner");

    while (!stoken.stop_requested()) {
        PendingOp op;
        {
            std::unique_lock lk(priority_pending_ops_mutex);
            priority_pending_ops_cv.wait(lk, stoken,
                                         [this] { return !priority_pending_ops.empty(); });
            if (stoken.stop_requested()) {
                break;
            }

            op = std::move(priority_pending_ops.front());
            priority_pending_ops.pop();
        }

        work_semaphore.Wait(op.gpu_tick);
        if (stoken.stop_requested()) {
            break;
        }

        op.callback();
    }
}

void DynamicState::Commit(const Instance& instance, const vk::CommandBuffer& cmdbuf) {
    if (dirty_state.viewports) {
        dirty_state.viewports = false;
        cmdbuf.setViewportWithCount(viewports);
    }
    if (dirty_state.scissors) {
        dirty_state.scissors = false;
        cmdbuf.setScissorWithCount(scissors);
    }
    if (dirty_state.depth_test_enabled) {
        dirty_state.depth_test_enabled = false;
        cmdbuf.setDepthTestEnable(depth_test_enabled);
    }
    if (dirty_state.depth_write_enabled) {
        dirty_state.depth_write_enabled = false;
        // Note that this must be set in a command buffer even if depth test is disabled.
        cmdbuf.setDepthWriteEnable(depth_write_enabled);
    }
    if (depth_test_enabled && dirty_state.depth_compare_op) {
        dirty_state.depth_compare_op = false;
        cmdbuf.setDepthCompareOp(depth_compare_op);
    }
    if (dirty_state.depth_bounds_test_enabled) {
        dirty_state.depth_bounds_test_enabled = false;
        if (instance.IsDepthBoundsSupported()) {
            cmdbuf.setDepthBoundsTestEnable(depth_bounds_test_enabled);
        }
    }
    if (depth_bounds_test_enabled && dirty_state.depth_bounds) {
        dirty_state.depth_bounds = false;
        if (instance.IsDepthBoundsSupported()) {
            cmdbuf.setDepthBounds(depth_bounds_min, depth_bounds_max);
        }
    }
    if (dirty_state.depth_bias_enabled) {
        dirty_state.depth_bias_enabled = false;
        cmdbuf.setDepthBiasEnable(depth_bias_enabled);
    }
    if (depth_bias_enabled && dirty_state.depth_bias) {
        dirty_state.depth_bias = false;
        cmdbuf.setDepthBias(depth_bias_constant, depth_bias_clamp, depth_bias_slope);
    }
    if (dirty_state.stencil_test_enabled) {
        dirty_state.stencil_test_enabled = false;
        cmdbuf.setStencilTestEnable(stencil_test_enabled);
    }
    if (stencil_test_enabled) {
        if (dirty_state.stencil_front_ops && dirty_state.stencil_back_ops &&
            stencil_front_ops == stencil_back_ops) {
            dirty_state.stencil_front_ops = false;
            dirty_state.stencil_back_ops = false;
            cmdbuf.setStencilOp(vk::StencilFaceFlagBits::eFrontAndBack, stencil_front_ops.fail_op,
                                stencil_front_ops.pass_op, stencil_front_ops.depth_fail_op,
                                stencil_front_ops.compare_op);
        } else {
            if (dirty_state.stencil_front_ops) {
                dirty_state.stencil_front_ops = false;
                cmdbuf.setStencilOp(vk::StencilFaceFlagBits::eFront, stencil_front_ops.fail_op,
                                    stencil_front_ops.pass_op, stencil_front_ops.depth_fail_op,
                                    stencil_front_ops.compare_op);
            }
            if (dirty_state.stencil_back_ops) {
                dirty_state.stencil_back_ops = false;
                cmdbuf.setStencilOp(vk::StencilFaceFlagBits::eBack, stencil_back_ops.fail_op,
                                    stencil_back_ops.pass_op, stencil_back_ops.depth_fail_op,
                                    stencil_back_ops.compare_op);
            }
        }
        if (dirty_state.stencil_front_reference && dirty_state.stencil_back_reference &&
            stencil_front_reference == stencil_back_reference) {
            dirty_state.stencil_front_reference = false;
            dirty_state.stencil_back_reference = false;
            cmdbuf.setStencilReference(vk::StencilFaceFlagBits::eFrontAndBack,
                                       stencil_front_reference);
        } else {
            if (dirty_state.stencil_front_reference) {
                dirty_state.stencil_front_reference = false;
                cmdbuf.setStencilReference(vk::StencilFaceFlagBits::eFront,
                                           stencil_front_reference);
            }
            if (dirty_state.stencil_back_reference) {
                dirty_state.stencil_back_reference = false;
                cmdbuf.setStencilReference(vk::StencilFaceFlagBits::eBack, stencil_back_reference);
            }
        }
        if (dirty_state.stencil_front_write_mask && dirty_state.stencil_back_write_mask &&
            stencil_front_write_mask == stencil_back_write_mask) {
            dirty_state.stencil_front_write_mask = false;
            dirty_state.stencil_back_write_mask = false;
            cmdbuf.setStencilWriteMask(vk::StencilFaceFlagBits::eFrontAndBack,
                                       stencil_front_write_mask);
        } else {
            if (dirty_state.stencil_front_write_mask) {
                dirty_state.stencil_front_write_mask = false;
                cmdbuf.setStencilWriteMask(vk::StencilFaceFlagBits::eFront,
                                           stencil_front_write_mask);
            }
            if (dirty_state.stencil_back_write_mask) {
                dirty_state.stencil_back_write_mask = false;
                cmdbuf.setStencilWriteMask(vk::StencilFaceFlagBits::eBack, stencil_back_write_mask);
            }
        }
        if (dirty_state.stencil_front_compare_mask && dirty_state.stencil_back_compare_mask &&
            stencil_front_compare_mask == stencil_back_compare_mask) {
            dirty_state.stencil_front_compare_mask = false;
            dirty_state.stencil_back_compare_mask = false;
            cmdbuf.setStencilCompareMask(vk::StencilFaceFlagBits::eFrontAndBack,
                                         stencil_front_compare_mask);
        } else {
            if (dirty_state.stencil_front_compare_mask) {
                dirty_state.stencil_front_compare_mask = false;
                cmdbuf.setStencilCompareMask(vk::StencilFaceFlagBits::eFront,
                                             stencil_front_compare_mask);
            }
            if (dirty_state.stencil_back_compare_mask) {
                dirty_state.stencil_back_compare_mask = false;
                cmdbuf.setStencilCompareMask(vk::StencilFaceFlagBits::eBack,
                                             stencil_back_compare_mask);
            }
        }
    }
    if (dirty_state.primitive_restart_enable) {
        dirty_state.primitive_restart_enable = false;
        cmdbuf.setPrimitiveRestartEnable(primitive_restart_enable);
    }
    if (dirty_state.rasterizer_discard_enable) {
        dirty_state.rasterizer_discard_enable = false;
        cmdbuf.setRasterizerDiscardEnable(rasterizer_discard_enable);
    }
    if (dirty_state.cull_mode) {
        dirty_state.cull_mode = false;
        cmdbuf.setCullMode(cull_mode);
    }
    if (dirty_state.front_face) {
        dirty_state.front_face = false;
        cmdbuf.setFrontFace(front_face);
    }
    if (dirty_state.blend_constants) {
        dirty_state.blend_constants = false;
        cmdbuf.setBlendConstants(blend_constants.data());
    }
    if (dirty_state.color_write_masks) {
        dirty_state.color_write_masks = false;
        if (instance.IsDynamicColorWriteMaskSupported()) {
            cmdbuf.setColorWriteMaskEXT(0, color_write_masks);
        }
    }
    if (dirty_state.line_width) {
        dirty_state.line_width = false;
        cmdbuf.setLineWidth(line_width);
    }
    if (dirty_state.feedback_loop_enabled && instance.IsAttachmentFeedbackLoopLayoutSupported()) {
        dirty_state.feedback_loop_enabled = false;
        cmdbuf.setAttachmentFeedbackLoopEnableEXT(feedback_loop_enabled
                                                      ? vk::ImageAspectFlagBits::eColor
                                                      : vk::ImageAspectFlagBits::eNone);
    }
}

} // namespace Vulkan
