// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string_view>
#include "common/debug.h"
#include "common/path_util.h"
#include "common/scope_exit.h"
#include "core/debug_state.h"
#include "core/emulator_settings.h"
#include "core/memory.h"
#include "shader_recompiler/compact_image_indices.h"
#include "shader_recompiler/frontend/ordered_gds_capture_coverage.h"
#include "shader_recompiler/frontend/ordered_gds_resume_state.h"
#include "shader_recompiler/frontend/ordered_gds_state.h"
#include "shader_recompiler/guest_descriptor_coherence.h"
#include "shader_recompiler/guest_tsharp_array.h"
#include "shader_recompiler/resource.h"
#include "shader_recompiler/runtime_info.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/buffer_cache/buffer_cache.h"
#include "video_core/graphics_diagnostics.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"
#include "video_core/renderer_vulkan/vk_indirect_read_sync.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_occlusion_query.h"
#include "video_core/renderer_vulkan/vk_ordered_gds_transfer.h"
#include "video_core/renderer_vulkan/vk_pipeline_cache.h"
#include "video_core/renderer_vulkan/vk_rasterizer.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_shader_hle.h"
#include "video_core/texture_cache/image_view.h"
#include "video_core/texture_cache/texture_cache.h"

#include <vulkan/vulkan_format_traits.hpp>

namespace Vulkan {

// An explicitly opted-in, one-shot GPU capture probe; absolutely not a
// per-game shader override, and never active during normal gameplay.
static bool IsPr117OneShotCaptureProbe() {
    const char* const value = std::getenv("SHADPS4_PR117_CAPTURE_ONCE");
    return value && std::string_view{value} == "1";
}

static Shader::PushData MakeUserData(const AmdGpu::Regs& regs) {
    // TODO(roamic): Add support for multiple viewports and geometry shaders when ViewportIndex
    // is encountered and implemented in the recompiler.
    Shader::PushData push_data{};
    push_data.xoffset = regs.viewport_control.xoffset_enable ? regs.viewports[0].xoffset : 0.f;
    push_data.xscale = regs.viewport_control.xscale_enable ? regs.viewports[0].xscale : 1.f;
    push_data.yoffset = regs.viewport_control.yoffset_enable ? regs.viewports[0].yoffset : 0.f;
    push_data.yscale = regs.viewport_control.yscale_enable ? regs.viewports[0].yscale : 1.f;
    return push_data;
}

Rasterizer::Rasterizer(const Instance& instance_, Scheduler& scheduler_, Runtime& runtime_,
                       AmdGpu::Liverpool* liverpool_)
    : instance{instance_}, scheduler{scheduler_}, runtime{runtime_}, page_manager{this},
      buffer_cache{instance, scheduler, runtime, liverpool_, texture_cache, page_manager},
      texture_cache{instance, scheduler, runtime, liverpool_, buffer_cache, page_manager},
      liverpool{liverpool_}, memory{Core::Memory::Instance()},
      pipeline_cache{instance, scheduler, liverpool, buffer_cache.GetSparsePageShift(),
                     buffer_cache.GetBdaPageTableBuffer()->SizeBytes() / sizeof(u64)},
      host_markers_enabled{EmulatorSettings.IsVkHostMarkersEnabled()},
      guest_markers_enabled{EmulatorSettings.IsVkGuestMarkersEnabled()},
      submit_draw_interval{EmulatorSettings.GetSubmitDrawInterval()} {
    if (!EmulatorSettings.IsNullGPU()) {
        liverpool->BindRasterizer(this);
    }
    memory->SetRasterizer(this);

    scheduler.SetSubmitCallback([this](Vulkan::SubmitInfo& info) {
        runtime.FlushBarriers();
        buffer_cache.SubmitPendingArenaBinds(info);
    });
}

Rasterizer::~Rasterizer() = default;

Rasterizer::OrderedGdsCaptureBinding::OrderedGdsCaptureBinding(
    Rasterizer& rasterizer_, const OrderedGdsEpochDeviceBuffers& epoch_)
    : rasterizer{rasterizer_}, epoch{epoch_} {
    ASSERT_MSG(!rasterizer.active_ordered_gds_capture_,
               "nested or stale GCN ordered GDS capture binding");
    ASSERT_MSG(epoch.AuthorizedEpoch() != 0 && epoch.CaptureAuthority().size() == epoch.plan.waves,
               "real guest epoch tokens must be supplied before capture binding");
    ASSERT_MSG(epoch.capture_registers.Handle() != VK_NULL_HANDLE &&
                   epoch.capture_packets.Handle() != VK_NULL_HANDLE &&
                   epoch.capture_registers.Handle() != epoch.resume_registers.Handle() &&
                   epoch.capture_registers.Handle() != epoch.capture_packets.Handle() &&
                   epoch.capture_packets.Handle() != epoch.resume_packets.Handle() &&
                   epoch.capture_registers.SizeBytes() == epoch.plan.register_layout.total_bytes &&
                   epoch.capture_packets.SizeBytes() == epoch.plan.packet_bytes,
               "invalid independent GCN packet/register checkpoint owner");
    rasterizer.active_ordered_gds_capture_ = &epoch;
}

Rasterizer::OrderedGdsCaptureBinding::~OrderedGdsCaptureBinding() {
    ASSERT_MSG(rasterizer.active_ordered_gds_capture_ == &epoch,
               "GCN ordered GDS capture scope identity changed");
    rasterizer.active_ordered_gds_capture_ = nullptr;
}

OcclusionQuery& Rasterizer::GetOcclusionQuery() {
    if (!occlusion) {
        occlusion = std::make_unique<OcclusionQuery>(instance, scheduler);
    }
    return *occlusion;
}

void Rasterizer::SubmitPendingQueries() {
    if (occlusion) {
        occlusion->SubmitPending();
    }
}

bool Rasterizer::FilterDraw() {
    const auto& regs = liverpool->regs;
    if (regs.color_control.mode == AmdGpu::ColorControl::OperationMode::EliminateFastClear) {
        // Clears the render target if FCE is launched before any draws
        EliminateFastClear();
        return false;
    }
    if (regs.color_control.mode == AmdGpu::ColorControl::OperationMode::FmaskDecompress) {
        // TODO: check for a valid MRT1 to promote the draw to the resolve pass.
        LOG_TRACE(Render_Vulkan, "FMask decompression pass skipped");
        ScopedMarkerInsert("FmaskDecompress");
        EliminateFastClear();
        return false;
    }
    if (regs.color_control.mode == AmdGpu::ColorControl::OperationMode::Resolve) {
        LOG_TRACE(Render_Vulkan, "Resolve pass");
        Resolve();
        return false;
    }
    if (regs.primitive_type == AmdGpu::PrimitiveType::None) {
        LOG_TRACE(Render_Vulkan, "Primitive type 'None' skipped");
        ScopedMarkerInsert("PrimitiveTypeNone");
        return false;
    }

    const bool cb_disabled =
        regs.color_control.mode == AmdGpu::ColorControl::OperationMode::Disable;
    const auto depth_copy =
        regs.depth_render_override.force_z_dirty && regs.depth_render_override.force_z_valid &&
        regs.depth_buffer.DepthValid() && regs.depth_buffer.DepthWriteValid() &&
        regs.depth_buffer.DepthAddress() != regs.depth_buffer.DepthWriteAddress();
    const auto stencil_copy =
        regs.depth_render_override.force_stencil_dirty &&
        regs.depth_render_override.force_stencil_valid && regs.depth_buffer.StencilValid() &&
        regs.depth_buffer.StencilWriteValid() &&
        regs.depth_buffer.StencilAddress() != regs.depth_buffer.StencilWriteAddress();
    if (cb_disabled && (depth_copy || stencil_copy)) {
        // Games may disable color buffer and enable force depth/stencil dirty and valid to
        // do a copy from one depth-stencil surface to another, without a pixel shader.
        // We need to detect this case and perform the copy, otherwise it will have no effect.
        LOG_TRACE(Render_Vulkan, "Performing depth-stencil override copy");
        DepthStencilCopy(depth_copy, stencil_copy);
        return false;
    }

    return true;
}

void Rasterizer::PrepareRenderState(const GraphicsPipeline* pipeline) {
    // Prefetch render targets to handle overlaps with bound textures (e.g. mipgen)
    const auto& key = pipeline->GetGraphicsKey();
    const auto& regs = liverpool->regs;
    if (regs.color_control.degamma_enable) {
        LOG_WARNING(Render_Vulkan, "Color buffers require gamma correction");
    }

    const bool skip_cb_binding =
        regs.color_control.mode == AmdGpu::ColorControl::OperationMode::Disable;
    for (s32 cb = 0; cb < std::bit_width(key.mrt_mask); ++cb) {
        auto& [image_id, desc] = cb_descs[cb];
        const auto& col_buf = regs.color_buffers[cb];
        const u32 target_mask = regs.color_target_mask.GetMask(cb);
        if (skip_cb_binding || !col_buf || !target_mask || (key.mrt_mask & (1 << cb)) == 0) {
            image_id = {};
            continue;
        }
        const auto& hint = liverpool->last_cb_extent[cb];
        std::construct_at(&desc, col_buf, hint);
        image_id = bound_images.emplace_back(texture_cache.FindImage(desc));
        auto& image = texture_cache.GetImage(image_id);
        image.binding.is_target = 1u;
    }

    if ((regs.depth_control.depth_enable && regs.depth_buffer.DepthValid()) ||
        (regs.depth_control.stencil_enable && regs.depth_buffer.StencilValid())) {
        const auto htile_address = regs.depth_htile_data_base.GetAddress();
        const auto& hint = liverpool->last_db_extent;
        auto& [image_id, desc] = db_desc;
        std::construct_at(&desc, regs.depth_buffer, regs.depth_view, regs.depth_control,
                          htile_address, hint);
        image_id = bound_images.emplace_back(texture_cache.FindImage(desc));
        auto& image = texture_cache.GetImage(image_id);
        image.binding.is_target = 1u;
    } else {
        db_desc.image_id = {};
    }
}

static std::pair<u32, u32> GetDrawOffsets(const AmdGpu::Regs& regs, const Shader::Info& info,
                                          const Shader::Gcn::FetchShaderData& fetch_shader) {
    u32 vertex_offset = regs.index_offset;
    u32 instance_offset = 0;
    if (!fetch_shader.Empty()) {
        if (vertex_offset == 0 && fetch_shader.vertex_offset_sgpr != -1) {
            vertex_offset = info.user_data[fetch_shader.vertex_offset_sgpr];
        }
        if (fetch_shader.instance_offset_sgpr != -1) {
            instance_offset = info.user_data[fetch_shader.instance_offset_sgpr];
        }
    }
    return {vertex_offset, instance_offset};
}

void Rasterizer::EliminateFastClear() {
    auto& col_buf = liverpool->regs.color_buffers[0];
    if (!col_buf || !col_buf.info.fast_clear) {
        return;
    }
    VideoCore::TextureCache::ImageDesc desc(col_buf, liverpool->last_cb_extent[0]);
    const auto image_id = texture_cache.FindImage(desc);
    const auto& image_view = texture_cache.FindRenderTarget(image_id, desc);
    if (!texture_cache.IsMetaCleared(col_buf.CmaskAddress(), col_buf.view.slice_start)) {
        return;
    }
    for (u32 slice = col_buf.view.slice_start; slice <= col_buf.view.slice_max; ++slice) {
        texture_cache.TouchMeta(col_buf.CmaskAddress(), slice, false);
    }
    auto& image = texture_cache.GetImage(image_id);
    const auto clear_value = LiverpoolToVK::ColorBufferClearValue(col_buf);

    ScopeMarkerBegin(fmt::format("EliminateFastClear:MRT={:#x}:M={:#x}", col_buf.Address(),
                                 col_buf.CmaskAddress()));
    runtime.ClearImage(&image, desc.view_info.range, clear_value);
    texture_cache.TrackGpuImage(image_id);
    ScopeMarkerEnd();
}

void Rasterizer::Draw(bool is_indexed, u32 index_offset) {
    RENDERER_TRACE;

    FlushPeriodically();
    scheduler.PopPendingOperations();

    if (!FilterDraw()) {
        return;
    }

    const auto& regs = liverpool->regs;
    const GraphicsPipeline* pipeline = pipeline_cache.GetGraphicsPipeline();
    if (!pipeline) {
        return;
    }

    const u64 preparation_tick = scheduler.CurrentTick();
    PrepareRenderState(pipeline);
    if (!BindResources(pipeline)) {
        return;
    }
    const auto state = BeginRendering(pipeline);

    const auto vertex_buffers = PrepareVertexBuffers(pipeline);
    std::optional<IndexBufferBinding> index_buffer;
    if (is_indexed) {
        index_buffer = PrepareIndexBuffer(index_offset);
    }

    if (needs_barrier) {
        runtime.FlushBarriers();
    }

    pipeline->BindResources(set_writes, push_data);
    UpdateDynamicState(pipeline, is_indexed);
    const auto query = occlusion ? occlusion->PrepareDraw(liverpool->regs.depth_count_control)
                                  : std::nullopt;
    scheduler.BeginRendering(state);

    const auto& vs_info = pipeline->GetStage(Shader::SwStage::Vertex);
    const auto& fetch_shader = pipeline->GetFetchShader();
    const auto [vertex_offset, instance_offset] = GetDrawOffsets(regs, vs_info, fetch_shader);

    const auto cmdbuf = scheduler.CommandBuffer();
    BindVertexBuffers(cmdbuf, vertex_buffers);
    if (index_buffer) {
        BindIndexBuffer(cmdbuf, *index_buffer);
    }
    if (scheduler.CurrentTick() != preparation_tick) {
        VideoCore::GraphicsDiagnostics::Emit(
            VideoCore::GraphicsDiagnostics::Event::DrawPreparationFlush,
            "begin-tick=%llu draw-tick=%llu indexed=%u",
            static_cast<unsigned long long>(preparation_tick),
            static_cast<unsigned long long>(scheduler.CurrentTick()),
            static_cast<unsigned>(is_indexed));
    }
    for (const auto* stage : pipeline->GetStages()) {
        if (stage) {
            scheduler.RecordWork("kind=shader stage=%u shader=%llx flatbuf-words=%zu",
                                 static_cast<unsigned>(stage->hw_stage),
                                 static_cast<unsigned long long>(stage->pgm_hash),
                                 stage->flattened_ud_buf.size());
        }
    }
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eGraphics, pipeline->Handle());
    if (occlusion) {
        occlusion->BeginDraw(cmdbuf, query);
    }

    scheduler.RecordWork("kind=draw indexed=%u vertices=%u instances=%u",
                         static_cast<unsigned>(is_indexed), regs.num_indices,
                         regs.num_instances.NumInstances());
    if (is_indexed) {
        cmdbuf.drawIndexed(regs.num_indices, regs.num_instances.NumInstances(), 0,
                           s32(vertex_offset), instance_offset);
    } else {
        cmdbuf.draw(regs.num_indices, regs.num_instances.NumInstances(), vertex_offset,
                    instance_offset);
    }
    if (occlusion) {
        occlusion->EndDraw(cmdbuf, query);
    }
    DebugState.IncDrawCall();

    ResetBindings(false);
}

void Rasterizer::DrawIndirect(bool is_indexed, VAddr arg_address, u32 offset, u32 stride,
                              u32 max_count, VAddr count_address, u16 vertex_sgpr_offset,
                              u16 instance_sgpr_offset) {
    RENDERER_TRACE;

    FlushPeriodically();
    scheduler.PopPendingOperations();

    if (!FilterDraw()) {
        return;
    }

    const DrawIndirectParams params = {
        .vertex_sgpr_offset = vertex_sgpr_offset,
        .instance_sgpr_offset = instance_sgpr_offset,
        .is_indirect = true,
    };
    const GraphicsPipeline* pipeline = pipeline_cache.GetGraphicsPipeline(params);
    if (!pipeline) {
        return;
    }

    const u64 preparation_tick = scheduler.CurrentTick();
    PrepareRenderState(pipeline);
    if (!BindResources(pipeline)) {
        return;
    }
    const auto state = BeginRendering(pipeline);

    const auto vertex_buffers = PrepareVertexBuffers(pipeline);
    std::optional<IndexBufferBinding> index_buffer;
    if (is_indexed) {
        index_buffer = PrepareIndexBuffer(0, true);
    }

    const auto size = stride * max_count;
    const auto [buffer, base] = buffer_cache.ObtainBuffer(arg_address + offset, size);
    needs_barrier |= runtime.IsBufferAccessed(buffer, base, size);
    bound_buffers.emplace_back(buffer, base, size, vk::AccessFlagBits2::eIndirectCommandRead);

    const VideoCore::Buffer* count_buffer;
    u64 count_offset;
    if (count_address != 0) {
        std::tie(count_buffer, count_offset) = buffer_cache.ObtainBuffer(count_address, 4);
        needs_barrier |= runtime.IsBufferAccessed(count_buffer, count_offset, 4);
        bound_buffers.emplace_back(count_buffer, count_offset, 4,
                                   vk::AccessFlagBits2::eIndirectCommandRead);
    }

    if (needs_barrier) {
        runtime.FlushBarriers();
    }

    pipeline->BindResources(set_writes, push_data);
    UpdateDynamicState(pipeline, is_indexed);
    const auto query = occlusion ? occlusion->PrepareDraw(liverpool->regs.depth_count_control)
                                  : std::nullopt;
    scheduler.BeginRendering(state);

    const auto cmdbuf = scheduler.CommandBuffer();
    BindVertexBuffers(cmdbuf, vertex_buffers);
    if (index_buffer) {
        BindIndexBuffer(cmdbuf, *index_buffer);
    }
    if (scheduler.CurrentTick() != preparation_tick) {
        VideoCore::GraphicsDiagnostics::Emit(
            VideoCore::GraphicsDiagnostics::Event::DrawPreparationFlush,
            "begin-tick=%llu draw-tick=%llu indexed=%u",
            static_cast<unsigned long long>(preparation_tick),
            static_cast<unsigned long long>(scheduler.CurrentTick()),
            static_cast<unsigned>(is_indexed));
    }
    for (const auto* stage : pipeline->GetStages()) {
        if (stage) {
            scheduler.RecordWork("kind=shader stage=%u shader=%llx flatbuf-words=%zu",
                                 static_cast<unsigned>(stage->hw_stage),
                                 static_cast<unsigned long long>(stage->pgm_hash),
                                 stage->flattened_ud_buf.size());
        }
    }
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eGraphics, pipeline->Handle());
    if (occlusion) {
        occlusion->BeginDraw(cmdbuf, query);
    }

    scheduler.RecordWork("kind=draw-indirect indexed=%u address=%llx count-address=%llx "
                         "max-count=%u stride=%u",
                         static_cast<unsigned>(is_indexed),
                         static_cast<unsigned long long>(arg_address + offset),
                         static_cast<unsigned long long>(count_address), max_count, stride);
    if (is_indexed) {
        ASSERT(sizeof(VkDrawIndexedIndirectCommand) == stride);

        if (count_address != 0) {
            cmdbuf.drawIndexedIndirectCount(buffer->Handle(), base, count_buffer->Handle(),
                                            count_offset, max_count, stride);
        } else {
            cmdbuf.drawIndexedIndirect(buffer->Handle(), base, max_count, stride);
        }
        DebugState.IncDrawCall();
    } else {
        ASSERT(sizeof(VkDrawIndirectCommand) == stride);

        if (count_address != 0) {
            cmdbuf.drawIndirectCount(buffer->Handle(), base, count_buffer->Handle(), count_offset,
                                     max_count, stride);
        } else {
            cmdbuf.drawIndirect(buffer->Handle(), base, max_count, stride);
        }
        DebugState.IncDrawCall();
    }

    if (occlusion) {
        occlusion->EndDraw(cmdbuf, query);
    }
    // Vulkan consumes these at DRAW_INDIRECT, not in a fragment or
    // compute shader. Preserve the producer read for a later GPU writer.
    constexpr auto indirect_read = vk::AccessFlagBits2::eIndirectCommandRead;
    runtime.AccessBuffer(buffer, base, stride * max_count, BufferAccessStage(indirect_read, false),
                         indirect_read);
    if (count_address != 0) {
        runtime.AccessBuffer(count_buffer, count_offset, sizeof(u32),
                             BufferAccessStage(indirect_read, false), indirect_read);
    }
    ResetBindings(false);
}

void Rasterizer::DispatchDirect() {
    RENDERER_TRACE;

    FlushPeriodically();
    scheduler.PopPendingOperations();

    const auto& cs_program = liverpool->GetCsRegs();
    const ComputePipeline* pipeline = pipeline_cache.GetComputePipeline();
    if (!pipeline) {
        return;
    }

    const auto& cs = pipeline->GetStage(Shader::SwStage::Compute);
    if (cs.uses_ordered_gds) {
        auto owner = AllocateOrderedGdsEpochBuffers(
            {cs_program.dim_x, cs_program.dim_y, cs_program.dim_z},
            {cs_program.num_thread_x.full, cs_program.num_thread_y.full,
             cs_program.num_thread_z.full},
            cs_program.NumVgprs(), cs_program.SharedMemSize());
        ExecuteOrderedGdsDispatch(std::move(owner));
        return;
    }
    if (ExecuteShaderHLE(cs, liverpool->regs, cs_program, *this)) {
        return;
    }

    if (!BindResources(pipeline)) {
        return;
    }

    if (needs_barrier) {
        runtime.FlushBarriers();
    }

    scheduler.EndRendering();
    pipeline->BindResources(set_writes, push_data);

    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eCompute, pipeline->Handle());
    scheduler.RecordWork("kind=dispatch shader=%llx groups=%u,%u,%u threads=%u,%u,%u",
                         static_cast<unsigned long long>(cs.pgm_hash), cs_program.dim_x,
                         cs_program.dim_y, cs_program.dim_z, cs_program.num_thread_x.full,
                         cs_program.num_thread_y.full, cs_program.num_thread_z.full);
    cmdbuf.dispatch(cs_program.dim_x, cs_program.dim_y, cs_program.dim_z);
    DebugState.IncDispatch();

    ResetBindings(true);
}

void Rasterizer::DispatchIndirect(VAddr address, u32 offset, u32 size) {
    RENDERER_TRACE;

    FlushPeriodically();
    scheduler.PopPendingOperations();

    const auto& cs_program = liverpool->GetCsRegs();
    const ComputePipeline* pipeline = pipeline_cache.GetComputePipeline();
    if (!pipeline) {
        return;
    }

    const auto& cs = pipeline->GetStage(Shader::SwStage::Compute);
    if (cs.uses_ordered_gds) {
        auto owner = AllocateOrderedGdsEpochBuffersFromGpuIndirect(
            address, offset, size,
            {cs_program.num_thread_x.full, cs_program.num_thread_y.full,
             cs_program.num_thread_z.full},
            cs_program.NumVgprs(), cs_program.SharedMemSize());
        ExecuteOrderedGdsDispatch(std::move(owner));
        return;
    }

    if (!BindResources(pipeline)) {
        return;
    }

    const auto [buffer, base] = buffer_cache.ObtainBuffer(address + offset, size);
    needs_barrier |= runtime.IsBufferAccessed(buffer, base, size);
    bound_buffers.emplace_back(buffer, base, size, vk::AccessFlagBits2::eIndirectCommandRead);

    if (needs_barrier) {
        runtime.FlushBarriers();
    }

    scheduler.EndRendering();
    pipeline->BindResources(set_writes, push_data);

    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eCompute, pipeline->Handle());
    scheduler.RecordWork("kind=dispatch-indirect shader=%llx address=%llx bytes=%u",
                         static_cast<unsigned long long>(cs.pgm_hash),
                         static_cast<unsigned long long>(address + offset), size);
    cmdbuf.dispatchIndirect(buffer->Handle(), base);
    DebugState.IncDispatch();

    // A future write must wait for the indirect command processor to
    // FINISH reading these arguments, not merely for shader execution.
    constexpr auto indirect_read = vk::AccessFlagBits2::eIndirectCommandRead;
    runtime.AccessBuffer(buffer, base, sizeof(VkDispatchIndirectCommand),
                         BufferAccessStage(indirect_read, true), indirect_read);
    ResetBindings(true);
}

void Rasterizer::FlushPeriodically() {
    if (submit_draw_interval == 0) {
        return;
    }
    if (const u64 tick = scheduler.CurrentTick(); tick != last_submit_tick) {
        last_submit_tick = tick;
        draws_since_submit = 0;
    }
    if (++draws_since_submit >= submit_draw_interval) {
        scheduler.Flush();
    }
}

std::unique_ptr<OrderedGdsEpochDeviceBuffers> Rasterizer::AllocateOrderedGdsEpochBuffers(
    Shader::Gcn::OrderedGds::DispatchExtent groups,
    Shader::Gcn::OrderedGds::DispatchExtent local_threads, u32 allocated_vgprs,
    u32 guest_shared_lds_bytes) {
    // The constructor rejects unbounded, partial-wave or unsupported LDS
    // dimensions before it creates any GPU buffers. The caller owns these
    // resources through the eventual second pass; no implicit game dispatch.
    return std::make_unique<OrderedGdsEpochDeviceBuffers>(instance, groups, local_threads,
                                                          allocated_vgprs, guest_shared_lds_bytes);
}

std::unique_ptr<OrderedGdsEpochDeviceBuffers>
Rasterizer::AllocateOrderedGdsEpochBuffersFromGpuIndirect(
    VAddr address, u32 offset, u32 available_bytes,
    Shader::Gcn::OrderedGds::DispatchExtent local_threads, u32 allocated_vgprs,
    u32 guest_shared_lds_bytes) {
    using Shader::Gcn::OrderedGds::GpuIndirectDispatchCommand;
    constexpr u32 bytes = sizeof(GpuIndirectDispatchCommand);
    if (active_ordered_gds_capture_ || available_bytes < bytes ||
        ((address | VAddr(offset)) & 3u) != 0 ||
        address > std::numeric_limits<VAddr>::max() - offset) {
        throw std::invalid_argument("indirect ordered GDS capture source invalid");
    }
    // ObtainBuffer returns the actual backing GPU buffer+offset. The source
    // can have been written by a prior GPU dispatch: only synchronized GPU
    // readback may establish the required guest wave-slot count.
    auto [buffer, base] = buffer_cache.ObtainBuffer(address + offset, bytes);
    needs_barrier |= runtime.IsBufferAccessed(buffer, base, bytes, false);
    if (needs_barrier) {
        runtime.FlushBarriers();
    }
    const auto actual =
        OrderedGdsIndirectDispatchTransfer::Download(runtime, scheduler, *buffer, base);
    // Plan::Require checks full-wave64 topology, total wave/LDS storage and
    // aggregate VRAM budget before any capture GPU allocation is created.
    return AllocateOrderedGdsEpochBuffers(actual, local_threads, allocated_vgprs,
                                          guest_shared_lds_bytes);
}

u32 Rasterizer::AuthorizeNewOrderedGdsCaptureEpoch(OrderedGdsEpochDeviceBuffers& owner) {
    // Host *identity* tickets are owned by this Rasterizer instance, not by
    // per-dispatch temporary vectors or Vulkan invocation-arrival ordering.
    // The validated owner carries exact wave count and independent SSBOs.
    // Actual guest LDS is device-local for the whole split-dispatch owner.
    // Clear it only on first use, never on each GDS resolution epoch.
    owner.PreparePersistentLds(runtime);
    const auto issued = ordered_gds_epoch_issuer_.Issue(owner.plan.waves);
    owner.PrepareEpochAuthority(runtime, scheduler, issued.epoch, issued.tokens_by_slot);
    return issued.epoch;
}

void Rasterizer::ExecuteOrderedGdsDispatch(std::unique_ptr<OrderedGdsEpochDeviceBuffers> owned) {
    using namespace Shader::Gcn::OrderedGds;
    SCOPE_EXIT {
        scheduler.DeferOperation([keep_alive = std::move(owned)] {});
    };
    auto& owner = *owned;
    const auto* pipeline = pipeline_cache.GetComputePipeline();
    ASSERT(pipeline);
    const auto& stage = pipeline->GetStage(Shader::SwStage::Compute);
    const auto& cs_program = liverpool->GetCsRegs();
    const bool ordered_append = cs_program.IsTgSizeEnabled() && cs_program.IsOrderedAppendEnabled();
    const bool ordered_append_per_group = cs_program.IsOrderedAppendPerGroup();
    owner.PrepareContinuationState(instance, runtime, stage.ordered_gds_state_words);
    std::vector<u32> sites;
    for (const auto& site : stage.ordered_gds_sites) {
        sites.push_back(site.pc);
    }
    std::vector<GpuResumePacket> resumes(owner.plan.waves);
    for (auto& resume : resumes) {
        resume.site_pc = InitialResumeSite;
    }
    std::optional<VerifiedGuestCreationOrder> creation;
    std::vector<bool> completed(owner.plan.waves);
    u32 phase{};
    while (true) {
        const u32 epoch = AuthorizeNewOrderedGdsCaptureEpoch(owner);
        const auto authority = owner.CaptureAuthority();
        for (u32 slot = 0; slot < owner.plan.waves; ++slot) {
            auto& resume = resumes[slot];
            resume.tag = ResumePacketTag;
            resume.epoch = epoch;
            resume.logical_slot = slot;
            resume.token_lo = authority[slot].token_lo;
            resume.token_hi = authority[slot].token_hi;
        }
        UploadOrderedGdsPackets(owner.resume_packets, 0, resumes);
        const auto captured = DispatchOriginalGcnOrderedCaptureDirect(owner, epoch);
        bool all_completed = true;
        for (const auto& packet : captured) {
            const bool terminal = IsCanonicalGcnTerminalBypass(packet, epoch);
            if (completed.at(packet.logical_slot) && !terminal) {
                throw std::runtime_error("completed ordered GDS wave unexpectedly resumed");
            }
            completed[packet.logical_slot] = terminal;
            all_completed &= terminal;
        }
        if (all_completed) {
            LOG_INFO(Render_Vulkan, "Ordered GDS dispatch completed: phases={}, waves={}",
                     phase + 1, owner.plan.waves);
            return;
        }
        if (IsPr117OneShotCaptureProbe()) {
            throw std::runtime_error(
                "PR117_REAL_GCN_CAPTURE_COMPLETED_STOP_BEFORE_GUEST_CONTINUATION");
        }
        if (!creation) {
            creation = VerifiedGuestCreationOrder::FromCompleteCikComputeM0(captured, epoch);
            if (!creation) {
                creation = VerifiedGuestCreationOrder::FromCikComputeDispatch(
                    owner.plan.waves, owner.plan.waves_per_group, ordered_append,
                    ordered_append_per_group);
            }
            if (!creation) {
                throw std::runtime_error(
                    "ordered GDS first phase has no complete CIK creation order");
            }
        }
        std::vector<CounterKey> keys;
        for (const auto& packet : captured) {
            if (packet.flags == CaptureBypassFlags) {
                continue;
            }
            if (!creation->MatchesCikComputeM0(packet.logical_slot, packet.runtime_m0)) {
                throw std::runtime_error(fmt::format(
                    "ordered GDS creation identity mismatch: phase={}, waves={}, slot={}, "
                    "rank={}, m0={:#x}",
                    phase, owner.plan.waves, packet.logical_slot,
                    creation->RankForSlot(packet.logical_slot), packet.runtime_m0));
            }
            keys.push_back(
                CompleteBatch::KeyFromM0(packet.runtime_m0, packet.encoded_control >> 2 & 63u));
        }
        const auto snapshot = SnapshotOrderedGdsDwords();
        const auto seeds = MakeCikGdsCounterSeed(snapshot, keys);
        auto counters = MakeExplicitOrderedCounterSeed(
            seeds, OrderedCounterSeedOrigin::SynchronizedCikGdsMemory);
        SparseOrderedGdsEpoch host{owner.plan.waves, std::move(counters), sites};
        StageGpuCaptures(host, epoch, captured, *creation);
        resumes = ResolveGpuCaptures(host, epoch, *creation);
        RequireResolvedGcnResumeHandoff(resumes, captured, *creation, epoch);
        std::vector<std::pair<u32, u32>> updates;
        for (const auto& [key, value] : host.CounterSnapshot()) {
            updates.emplace_back(key.ByteOffset(), value);
        }
        // Publish the ordered instruction before its continuation can read GDS.
        CommitOrderedGdsDwords(updates);
        if (++phase == 65536) {
            throw std::runtime_error("ordered GDS dispatch exceeded bounded continuation phases");
        }
    }
}

std::vector<Shader::Gcn::OrderedGds::GpuCapturePacket>
Rasterizer::DispatchOriginalGcnOrderedCaptureDirect(OrderedGdsEpochDeviceBuffers& owner,
                                                    u32 expected_epoch) {
    return DispatchOriginalGcnOrderedCaptureImpl(owner, expected_epoch, false, 0, 0, 0);
}

std::vector<Shader::Gcn::OrderedGds::GpuCapturePacket>
Rasterizer::DispatchOriginalGcnOrderedCaptureIndirect(OrderedGdsEpochDeviceBuffers& owner,
                                                      u32 expected_epoch, VAddr address, u32 offset,
                                                      u32 available_bytes) {
    return DispatchOriginalGcnOrderedCaptureImpl(owner, expected_epoch, true, address, offset,
                                                 available_bytes);
}

std::vector<Shader::Gcn::OrderedGds::GpuCapturePacket>
Rasterizer::DispatchOriginalGcnOrderedCaptureImpl(OrderedGdsEpochDeviceBuffers& owner,
                                                  u32 expected_epoch, bool indirect, VAddr address,
                                                  u32 offset, u32 available_bytes) {
    using namespace Shader::Gcn::OrderedGds;

    // This API deliberately does NOT replace DispatchDirect(). The original
    // guest shader executes its pre-DS memory writes here; replaying the
    // shader from PC=0 would double those side effects. The epoch owner must
    // survive until an authentic post-DS continuation finishes.
    if (!expected_epoch || owner.AuthorizedEpoch() != expected_epoch ||
        owner.CaptureAuthority().size() != owner.plan.waves || active_ordered_gds_capture_) {
        throw std::invalid_argument("unowned or overlapping original-GCN capture dispatch");
    }
    scheduler.PopPendingOperations();
    const auto& cs_program = liverpool->GetCsRegs();
    DispatchExtent groups = owner.groups;
    const DispatchExtent local{cs_program.num_thread_x.full, cs_program.num_thread_y.full,
                               cs_program.num_thread_z.full};
    const VideoCore::Buffer* indirect_command_buffer{};
    u64 indirect_command_offset{};
    if (indirect) {
        constexpr u32 CommandBytes = sizeof(GpuIndirectDispatchCommand);
        if (available_bytes < CommandBytes || ((address | VAddr(offset)) & 3u) != 0 ||
            address > std::numeric_limits<VAddr>::max() - offset) {
            throw std::invalid_argument("original GCN GPU indirect command invalid");
        }
        auto [buffer, base] = buffer_cache.ObtainBuffer(address + offset, CommandBytes);
        needs_barrier |= runtime.IsBufferAccessed(buffer, base, CommandBytes, false);
        if (needs_barrier) {
            runtime.FlushBarriers();
        }
        groups = OrderedGdsIndirectDispatchTransfer::Download(runtime, scheduler, *buffer, base);
        indirect_command_buffer = buffer;
        indirect_command_offset = base;
    }
    if (owner.groups.x != groups.x || owner.groups.y != groups.y || owner.groups.z != groups.z ||
        owner.local_threads.x != local.x || owner.local_threads.y != local.y ||
        owner.local_threads.z != local.z) {
        throw std::invalid_argument(
            "CIK capture owner does not match original guest 3D dispatch dimensions");
    }
    // The full plan must be recomputed from LIVE guest registers before
    // touching GPU memory, not inferred from a stale shader hash or an old
    // indirect command. The constructor already enforces the VRAM bound.
    const auto required = GcnOrderedGdsDevicePlan::Require(groups, local, cs_program.NumVgprs(),
                                                           cs_program.SharedMemSize());
    if (owner.plan.waves != required.waves ||
        owner.plan.epoch_auth_bytes != required.epoch_auth_bytes ||
        owner.plan.packet_bytes != required.packet_bytes ||
        owner.plan.register_layout.total_bytes != required.register_layout.total_bytes ||
        owner.plan.combined_device_bytes != required.combined_device_bytes) {
        throw std::invalid_argument("stale original GCN capture buffer plan");
    }

    const ComputePipeline* pipeline = pipeline_cache.GetComputePipeline();
    if (!pipeline) {
        throw std::runtime_error("original-GCN capture pipeline not compiled");
    }
    const auto& stage = pipeline->GetStage(Shader::SwStage::Compute);
    if (!stage.uses_ordered_gds || stage.hw_stage != Shader::HwStage::Compute ||
        stage.ordered_gds_sites.empty()) {
        throw std::invalid_argument("capture pipeline has no decoded original GCN DS site");
    }
    std::vector<VerifiedGcnOrderedSite> sites;
    sites.reserve(stage.ordered_gds_sites.size());
    for (const auto& site : stage.ordered_gds_sites) {
        sites.push_back({site.pc, site.destination_vgpr, site.encoded_control, site.source_vgpr});
    }

    // A missing GPU writer must remain invalid after readback. Clear only
    // epoch-local packet and register buffers; NEVER clear persistent guest
    // LDS after its initial allocation or a post-DS attempt.
    runtime.FillBuffer(&owner.capture_packets, 0, owner.plan.packet_bytes, 0);
    runtime.FillBuffer(&owner.capture_registers, 0, owner.plan.register_layout.total_bytes, 0);
    {
        OrderedGdsCaptureBinding live_scope{*this, owner};
        if (!BindResources(pipeline)) {
            throw std::runtime_error("original guest GCN capture resources unavailable");
        }
        if (needs_barrier) {
            runtime.FlushBarriers();
        }
        scheduler.EndRendering();
        pipeline->BindResources(set_writes, push_data);
        const auto command = scheduler.CommandBuffer();
        command.bindPipeline(vk::PipelineBindPoint::eCompute, pipeline->Handle());
        if (indirect) {
            ASSERT_MSG(indirect_command_buffer, "missing real GPU indirect GCN command");
            command.dispatchIndirect(indirect_command_buffer->Handle(), indirect_command_offset);
            // The GDS first-pass path must not bypass the same universal
            // Vulkan indirect read/write hazard as ordinary compute.
            constexpr auto indirect_read = vk::AccessFlagBits2::eIndirectCommandRead;
            runtime.AccessBuffer(indirect_command_buffer, indirect_command_offset,
                                 sizeof(VkDispatchIndirectCommand),
                                 BufferAccessStage(indirect_read, true), indirect_read);
        } else {
            command.dispatch(groups.x, groups.y, groups.z);
        }
        DebugState.IncDispatch();
        ResetBindings(true);
    }
    // These synchronized copies are from real GPU-written device buffers.
    // They do not derive guest ranks from Vulkan physical invocation order.
    const auto captured = DownloadOrderedGdsPackets(owner.capture_packets, 0, owner.plan.waves);
    std::vector<std::byte> state;
    if (std::ranges::any_of(captured, [&](const auto& packet) {
            return !IsCanonicalGcnTerminalBypass(packet, expected_epoch);
        })) {
        state = OrderedGdsPacketTransfer::DownloadCheckpoint(
            runtime, scheduler, owner.capture_registers, 0, owner.plan.register_layout);
    }
    const auto save_evidence = [&] {
        const auto& layout = owner.plan.register_layout;
        if (state.empty()) {
            state = OrderedGdsPacketTransfer::DownloadCheckpoint(
                runtime, scheduler, owner.capture_registers, 0, layout);
        }
        const u32 saved_waves = std::min(owner.plan.waves, 32u);
        const std::array<u32, 12> header{0x32445347u,
                                         2u,
                                         expected_epoch,
                                         owner.plan.waves,
                                         saved_waves,
                                         u32(layout.stride_bytes),
                                         layout.scalar_words,
                                         layout.vector_registers,
                                         groups.x,
                                         groups.y,
                                         groups.z,
                                         u32(sizeof(GpuCapturePacket))};
        const auto path = Common::FS::GetUserPath(Common::FS::PathType::LogDir) /
                          fmt::format("ordered-gds-{:016x}-{}.bin", stage.pgm_hash, expected_epoch);
        std::ofstream evidence{path, std::ios::binary};
        evidence.write(reinterpret_cast<const char*>(header.data()), sizeof(header));
        evidence.write(reinterpret_cast<const char*>(captured.data()),
                       captured.size() * sizeof(GpuCapturePacket));
        evidence.write(reinterpret_cast<const char*>(state.data()),
                       saved_waves * layout.stride_bytes);
        evidence.close();
        if (!evidence) {
            throw std::runtime_error("failed to save ordered GDS GPU evidence");
        }
        LOG_WARNING(Render_Vulkan,
                    "ORDERED_GDS_GPU_EVIDENCE file={} packets={} checkpoint_waves={}; "
                    "raw bytes are not a validated continuation",
                    path.string(), captured.size(), saved_waves);
    };
    const bool capture_requested = IsPr117OneShotCaptureProbe();
    if (capture_requested) {
        save_evidence();
    }
    try {
        RequireCaptureTokenAuthority(captured, owner.CaptureAuthority(), expected_epoch);
        RequireCompleteGcnWaveCheckpoints(state, captured, owner.plan.register_layout,
                                          expected_epoch, sites);
        if (owner.plan.lds_layout && !owner.HasPersistentLdsBinding(expected_epoch)) {
            throw std::invalid_argument(
                "original GCN guest LDS capture did not bind persistent SSBO");
        }
    } catch (const std::invalid_argument& error) {
        LOG_ERROR(Render_Vulkan, "Ordered GDS rejected epoch={} shader={:016x}: {}", expected_epoch,
                  stage.pgm_hash, error.what());
        for (u32 slot = 0; slot < std::min(owner.plan.waves, 32u); ++slot) {
            const auto& packet = captured[slot];
            LOG_ERROR(Render_Vulkan,
                      "Ordered GDS raw packet index={} slot={} tag={:08x} epoch={} "
                      "flags={:08x} site={:08x} m0={:08x} exec={:08x}{:08x} "
                      "token={:08x}{:08x}",
                      slot, packet.logical_slot, packet.tag, packet.epoch, packet.flags,
                      packet.site_pc, packet.runtime_m0, packet.exec_hi, packet.exec_lo,
                      packet.token_hi, packet.token_lo);
        }
        try {
            if (!capture_requested) {
                save_evidence();
            }
            const auto gpu_resume = OrderedGdsPacketTransfer::Download<GpuResumePacket>(
                runtime, scheduler, owner.resume_packets, 0, owner.plan.waves);
            const auto gpu_authority = OrderedGdsPacketTransfer::Download<GpuGcnOrderedEpochAuth>(
                runtime, scheduler, owner.capture_auth, 0, owner.plan.waves);
            const auto authority = owner.CaptureAuthority();
            const std::array<u32, 12> header{0x49445347u,
                                             2u,
                                             expected_epoch,
                                             owner.plan.waves,
                                             u32(sizeof(GpuGcnOrderedEpochAuth)),
                                             u32(sizeof(GpuResumePacket))};
            const auto path =
                Common::FS::GetUserPath(Common::FS::PathType::LogDir) /
                fmt::format("ordered-gds-{:016x}-{}.inputs.bin", stage.pgm_hash, expected_epoch);
            std::ofstream evidence{path, std::ios::binary};
            evidence.write(reinterpret_cast<const char*>(header.data()), sizeof(header));
            evidence.write(reinterpret_cast<const char*>(authority.data()), authority.size_bytes());
            evidence.write(reinterpret_cast<const char*>(gpu_authority.data()),
                           gpu_authority.size() * sizeof(GpuGcnOrderedEpochAuth));
            evidence.write(reinterpret_cast<const char*>(gpu_resume.data()),
                           gpu_resume.size() * sizeof(GpuResumePacket));
            evidence.close();
            if (!evidence) {
                throw std::runtime_error("failed to save ordered GDS input evidence");
            }
            LOG_WARNING(Render_Vulkan, "Ordered GDS host/device authority and GPU resume bytes: {}",
                        path.string());
        } catch (const std::exception& diagnostic_error) {
            LOG_ERROR(Render_Vulkan, "Ordered GDS evidence unavailable: {}",
                      diagnostic_error.what());
        }
        throw;
    }
    LOG_WARNING(Render_Vulkan,
                "PR117 original guest GCN GPU first pass completed: epoch={}, "
                "waves={}, sites={}; captured phase ready for ordered resolution",
                expected_epoch, captured.size(), sites.size());
    return captured;
}

std::vector<Shader::Gcn::OrderedGds::GpuCapturePacket> Rasterizer::DownloadOrderedGdsPackets(
    const VideoCore::Buffer& device_capture, u64 source_offset, std::size_t packet_count) {
    return OrderedGdsPacketTransfer::Download(runtime, scheduler, device_capture, source_offset,
                                              packet_count);
}

void Rasterizer::UploadOrderedGdsPackets(
    VideoCore::Buffer& device_resume, u64 destination_offset,
    std::span<const Shader::Gcn::OrderedGds::GpuResumePacket> packets) {
    OrderedGdsPacketTransfer::Upload(runtime, scheduler, device_resume, destination_offset,
                                     packets);
}

std::map<Shader::Gcn::OrderedGds::CounterKey, u32> Rasterizer::ResolveVerifiedOrderedGdsEpoch(
    const VideoCore::Buffer& device_capture, u64 capture_offset, VideoCore::Buffer& device_resume,
    u64 resume_offset, u32 epoch,
    const Shader::Gcn::OrderedGds::VerifiedGuestCreationOrder& creation,
    std::span<const u32> verified_sites,
    std::span<const Shader::Gcn::OrderedGds::OrderedCounterSeed> seed,
    Shader::Gcn::OrderedGds::OrderedCounterSeedOrigin seed_origin) {
    using namespace Shader::Gcn::OrderedGds;

    // Validate counter provenance before reading packets or uploading resume state.
    auto counters = MakeExplicitOrderedCounterSeed(seed, seed_origin);
    SparseOrderedGdsEpoch host{creation.Size(), std::move(counters), verified_sites};

    // A genuine shader checkpoint must already have written exactly one
    // capture/bypass packet per guest-created wave. Neither the staging
    // arrival order nor the packet logical slot establishes wave rank.
    const auto packets = DownloadOrderedGdsPackets(device_capture, capture_offset, creation.Size());
    StageGpuCaptures(host, epoch, packets, creation);
    const auto resume = ResolveGpuCaptures(host, epoch, creation);
    UploadOrderedGdsPackets(device_resume, resume_offset,
                            std::span<const GpuResumePacket>{resume.data(), resume.size()});

    // Upload prepares a continuation; it does not commit guest GDS changes.
    return host.CounterSnapshot();
}

void Rasterizer::EstablishVerifiedCikPackerState(
    u64 context_identity, std::span<const Shader::Gcn::OrderedGds::OrderedCounterSeed> state,
    Shader::Gcn::OrderedGds::OrderedCounterSeedOrigin origin) {
    cik_ordered_packer_ledger_.Establish(context_identity, state, origin);
}

void Rasterizer::ResetVerifiedCikPackerState() noexcept {
    cik_ordered_packer_ledger_.Reset();
}

std::map<Shader::Gcn::OrderedGds::CounterKey, u32>
Rasterizer::ResolveOwnedCikComputeOrderedGdsFromLedger(
    OrderedGdsEpochDeviceBuffers& owned, u32 epoch, u64 context_identity,
    std::span<const Shader::Gcn::OrderedGds::VerifiedGcnOrderedSite> verified_sites) {
    using namespace Shader::Gcn::OrderedGds;
    if (owned.AuthorizedEpoch() != epoch) {
        throw std::invalid_argument("CIK ledger resolution has no authenticated GPU epoch");
    }
    // The ledger must be re-established from synchronized GDS for this dispatch.
    // Failure leaves ledger values intact and burns the epoch.
    const auto seed = cik_ordered_packer_ledger_.BeginEpoch(context_identity, epoch);
    try {
        auto result = ResolveOwnedCikComputeOrderedGdsEpoch(
            owned, epoch, verified_sites, seed, OrderedCounterSeedOrigin::DedicatedPackerState);
        // Actual shaders have NOT re-entered and consumed these buffers.
        // Staging is safe, but publishing counters here would let a later
        // dispatch observe changes before the GCN continuation completes.
        // This epoch stays pending until an authenticated resumed GPU pass
        // invokes CommitEpoch. No such pass is currently enabled.
        cik_ordered_packer_ledger_.StageResolvedEpoch(context_identity, epoch, result);
        return result;
    } catch (...) {
        cik_ordered_packer_ledger_.CancelEpoch(context_identity, epoch);
        throw;
    }
}

std::map<Shader::Gcn::OrderedGds::CounterKey, u32>
Rasterizer::ResolveOwnedCikComputeOrderedGdsEpoch(
    OrderedGdsEpochDeviceBuffers& owned, u32 epoch,
    std::span<const Shader::Gcn::OrderedGds::VerifiedGcnOrderedSite> verified_sites,
    std::span<const Shader::Gcn::OrderedGds::OrderedCounterSeed> seed,
    Shader::Gcn::OrderedGds::OrderedCounterSeedOrigin seed_origin) {
    // One immutable bounded plan determines all lengths and resources.
    // For a genuinely zero-LDS shader the legacy bridge signature still
    // requires buffer references; its checked_lds==null branch neither
    // touches nor compares them. Use existing packet resources here WITHOUT
    // allocating any fake LDS storage. Live LDS uses independent buffers.
    const auto& plan = owned.plan;
    if (owned.AuthorizedEpoch() != epoch || owned.CaptureAuthority().size() != plan.waves) {
        throw std::invalid_argument(
            "CIK resolved epoch missing externally supplied wave continuation identities");
    }
    const auto& lds_source = owned.capture_lds ? *owned.capture_lds : owned.capture_packets;
    auto& lds_destination = owned.resume_lds ? *owned.resume_lds : owned.resume_packets;
    const auto lds_layout =
        plan.lds_layout.value_or(Shader::Gcn::OrderedGds::GcnLdsCheckpointLayout{});
    return ResolveCompleteCikComputeOrderedGdsEpoch(
        owned.capture_packets, 0, owned.capture_registers, 0, plan.register_layout, lds_source, 0,
        lds_layout, plan.lds_layout ? plan.lds_layout->lds_bytes : 0, lds_destination, 0,
        owned.resume_registers, 0, owned.resume_packets, 0, epoch, plan.waves, verified_sites, seed,
        seed_origin, owned.CaptureAuthority(),
        owned.HasPersistentLdsBinding(epoch) && owned.persistent_lds ? &*owned.persistent_lds
                                                                     : nullptr);
}

std::map<Shader::Gcn::OrderedGds::CounterKey, u32>
Rasterizer::ResolveCompleteCikComputeOrderedGdsEpoch(
    const VideoCore::Buffer& device_capture, u64 capture_offset,
    const VideoCore::Buffer& device_checkpoints, u64 checkpoint_offset,
    const Shader::Gcn::OrderedGds::GcnCheckpointLayout& checkpoint_layout,
    const VideoCore::Buffer& device_lds, u64 lds_capture_offset,
    const Shader::Gcn::OrderedGds::GcnLdsCheckpointLayout& lds_layout,
    u32 verified_shared_lds_bytes, VideoCore::Buffer& device_resume_lds, u64 resume_lds_offset,
    VideoCore::Buffer& device_resume_checkpoints, u64 resume_checkpoint_offset,
    VideoCore::Buffer& device_resume, u64 resume_offset, u32 epoch, std::size_t guest_created_waves,
    std::span<const Shader::Gcn::OrderedGds::VerifiedGcnOrderedSite> verified_sites,
    std::span<const Shader::Gcn::OrderedGds::OrderedCounterSeed> seed,
    Shader::Gcn::OrderedGds::OrderedCounterSeedOrigin seed_origin,
    std::span<const Shader::Gcn::OrderedGds::GpuGcnOrderedEpochAuth> authority,
    const VideoCore::Buffer* persistent_guest_lds) {
    using namespace Shader::Gcn::OrderedGds;

    // A real GPU checkpoint writer must have captured complete live guest
    // M0/EXEC/site values. A test-fixture wave rank or Vulkan slot is NOT an
    // acceptable substitute. No packer value may be synthesized from GDS RAM.
    auto counters = MakeExplicitOrderedCounterSeed(seed, seed_origin);
    // Guard before allocating, reading or creating an unbounded epoch.
    constexpr std::size_t CikMaximumLogicalWaves = 1u << 13;
    if (guest_created_waves == 0 || guest_created_waves > CikMaximumLogicalWaves ||
        checkpoint_layout.waves != guest_created_waves ||
        checkpoint_layout.scalar_words != LiveCheckpointCoverage::ScalarCount) {
        // The real translator captures *all* GCN S0..S103 at the
        // instruction site, not a guessed SGPR allocation subset.
        throw std::invalid_argument("unsupported CIK checkpoint/wave/SGPR layout");
    }
    // Zero-LDS compute shaders must not require a fictitious shared-memory
    // buffer or barrier. Nonzero-LDS shaders STILL require a complete,
    // authenticated workgroup snapshot before the host resolves counters.
    const auto checked_lds =
        RequireOptionalGcnLdsCheckpoint(lds_layout, verified_shared_lds_bytes, guest_created_waves);
    if (!checked_lds && (lds_capture_offset || resume_lds_offset)) {
        throw std::invalid_argument("zero-LDS guest cannot provide LDS offsets");
    }
    if (persistent_guest_lds) {
        // The first pass executes ALL original guest LDS loads/stores on a
        // dedicated SSBO that outlives the workgroup and is reused by the
        // post-DS shader. No quiescence snapshot or invented LDS header is
        // needed: complete GPU dispatch completion is established by the
        // subsequent packet + register readbacks.
        if (!checked_lds || persistent_guest_lds->Handle() == VK_NULL_HANDLE ||
            persistent_guest_lds->SizeBytes() !=
                u64(checked_lds->workgroups) * checked_lds->lds_bytes) {
            throw std::invalid_argument("GDS persistent LDS backing size invalid");
        }
    }

    // All *active* device buffers must remain disjoint until the resumed
    // dispatch consumes their contents. Zero-LDS paths don't consume the
    // nominal LDS arguments and must not require dummy resource allocation.
    // No shader capture producer is enabled yet; the backend stays gated.
    const std::array handles{
        device_capture.Handle(), device_checkpoints.Handle(),        device_lds.Handle(),
        device_resume.Handle(),  device_resume_checkpoints.Handle(), device_resume_lds.Handle(),
    };
    for (std::size_t i = 0; i < handles.size(); ++i) {
        if (!checked_lds && (i == 2 || i == 5)) {
            continue;
        }
        for (std::size_t j = i + 1; j < handles.size(); ++j) {
            if (!checked_lds && (j == 2 || j == 5)) {
                continue;
            }
            if (handles[i] == handles[j]) {
                throw std::invalid_argument("ordered GDS GPU checkpoint buffers alias");
            }
        }
    }
    if (persistent_guest_lds) {
        for (const auto handle : handles) {
            if (handle == persistent_guest_lds->Handle()) {
                throw std::invalid_argument("persistent LDS aliases GDS checkpoint buffers");
            }
        }
    }
    if (checked_lds && !persistent_guest_lds &&
        ((lds_capture_offset & 3u) || (resume_lds_offset & 3u) ||
         lds_capture_offset > device_lds.SizeBytes() ||
         checked_lds->total_bytes > device_lds.SizeBytes() - lds_capture_offset ||
         resume_lds_offset > device_resume_lds.SizeBytes() ||
         checked_lds->total_bytes > device_resume_lds.SizeBytes() - resume_lds_offset)) {
        throw std::invalid_argument("ordered GDS LDS source/resume ranges invalid");
    }
    // Check every destination capacity BEFORE resolving or recording writes.
    const u64 resume_bytes = OrderedGdsPacketTransfer::CheckedPacketBytes(guest_created_waves);
    if (resume_offset > device_resume.SizeBytes() ||
        resume_bytes > device_resume.SizeBytes() - resume_offset ||
        resume_checkpoint_offset > device_resume_checkpoints.SizeBytes() ||
        checkpoint_layout.total_bytes >
            device_resume_checkpoints.SizeBytes() - resume_checkpoint_offset) {
        throw std::invalid_argument("ordered GDS resume device buffer too small");
    }
    const auto captures =
        DownloadOrderedGdsPackets(device_capture, capture_offset, guest_created_waves);
    // Never accept a plausible packet until its token matches the actual
    // per-wave scheduler assignment for this specific epoch.
    RequireCaptureTokenAuthority(captures, authority, epoch);
    const auto order = VerifiedGuestCreationOrder::FromCompleteCikComputeM0(captures, epoch);
    if (!order) {
        // Includes mixed bypasses and nonzero crawler increments: never
        // pretend physical GPU arrival order is guest wave creation order.
        throw std::invalid_argument("unverified CIK M0 logical wave order");
    }
    // The independent GPU state-buffer readback must agree with EVERY live
    // capture's epoch, M0, EXEC, site, token and source logical slot. Reject
    // a missing/stale SGPR/VGPR checkpoint BEFORE resolving any counter.
    const auto state = OrderedGdsPacketTransfer::DownloadCheckpoint(
        runtime, scheduler, device_checkpoints, checkpoint_offset, checkpoint_layout);
    RequireCompleteGcnWaveCheckpoints(state, captures, checkpoint_layout, epoch, verified_sites);
    // A workgroup with actual shared LDS requires a compiler-proven
    // quiescent barrier. Zero-LDS workgroups have no state to transfer.
    std::vector<std::byte> lds_state;
    if (checked_lds && !persistent_guest_lds) {
        lds_state = OrderedGdsLdsStateTransfer::Download(runtime, scheduler, device_lds,
                                                         lds_capture_offset, *checked_lds, epoch);
    }
    std::vector<u32> site_pcs;
    site_pcs.reserve(verified_sites.size());
    for (const auto& site : verified_sites) {
        site_pcs.push_back(site.site_pc);
    }
    SparseOrderedGdsEpoch host{guest_created_waves, std::move(counters), site_pcs};
    StageGpuCaptures(host, epoch, captures, *order);
    const auto resume = ResolveGpuCaptures(host, epoch, *order);
    // The CPU-resolved result must still refer to the same GPU wave and
    // independently decoded counter index; never feed a stale token/PC/rank
    // into an eventual guest shader continuation.
    RequireResolvedGcnResumeHandoff(resume, captures, *order, epoch);
    // This is a true POST-DS guest register snapshot. The old ordered CIK
    // counter value comes exclusively from the host-resolved result and
    // is written to the DECODED destination VGPR of EXEC-active lanes.
    // The original GPU snapshot remains immutable; other registers/PC/LDS
    // and inactive lanes are preserved. A future guest continuation shader
    // must restore this state without re-running earlier GCN instructions.
    const auto post_ds_state = MaterializeGcnPostOrderedCountState(
        state, captures, resume, checkpoint_layout, epoch, verified_sites, *order);
    OrderedGdsPacketTransfer::UploadCheckpoint(
        runtime, scheduler, device_resume_checkpoints, resume_checkpoint_offset,
        std::span<const std::byte>{post_ds_state.data(), post_ds_state.size()}, checkpoint_layout);
    if (checked_lds && !persistent_guest_lds) {
        OrderedGdsLdsStateTransfer::Upload(
            runtime, scheduler, device_resume_lds, resume_lds_offset,
            std::span<const std::byte>{lds_state.data(), lds_state.size()}, *checked_lds, epoch);
    }
    // When guest LDS was lowered to persistent storage, its authoritative
    // original GPU bytes remain in the very same device buffer; never
    // overwrite them with an unverified CPU snapshot or reset on resume.
    UploadOrderedGdsPackets(device_resume, resume_offset,
                            std::span<const GpuResumePacket>{resume.data(), resume.size()});
    return host.CounterSnapshot();
}

std::vector<u32> Rasterizer::SnapshotOrderedGdsDwords() {
    return OrderedGdsStorageTransfer::Snapshot(runtime, scheduler, *buffer_cache.GetGdsBuffer());
}

void Rasterizer::CommitOrderedGdsDwords(std::span<const std::pair<u32, u32>> updates) {
    OrderedGdsStorageTransfer::Apply(runtime, scheduler, *buffer_cache.GetGdsBuffer(), updates);
}

u64 Rasterizer::Flush() {
    const u64 current_tick = scheduler.CurrentTick();
    SubmitInfo info{};
    scheduler.Flush(info);
    return current_tick;
}

void Rasterizer::Finish() {
    scheduler.Finish();
}

void Rasterizer::OnSubmit() {
    buffer_cache.TickFrame();
    texture_cache.RunGarbageCollector();
    runtime.TickFrame();
}

void Rasterizer::OnFence() {
    if (occlusion) {
        // The caller signals a guest fence immediately after this method.
        // Complete preceding counter writes before that fence becomes visible.
        occlusion->Drain();
    }
}

bool Rasterizer::BindResources(const Pipeline* pipeline) {
    if (IsComputeImageCopy(pipeline) || IsComputeMetaClear(pipeline) ||
        IsComputeImageClear(pipeline)) {
        return false;
    }

    set_writes.clear();
    buffer_infos.clear();
    image_infos.clear();

    // Reserve across all shader stages before any VkWriteDescriptorSet points
    // into image_infos. Later vector growth must not invalidate those pointers.
    std::size_t required_infos = 0;
    std::size_t required_buffer_infos = 0;
    std::size_t required_writes = 0;
    std::size_t max_stage_images = 0;
    for (const auto* stage : pipeline->GetStages()) {
        if (!stage) {
            continue;
        }
        std::size_t stage_images = 0;
        required_buffer_infos += stage->buffers.size();
        required_writes += stage->buffers.size() + stage->samplers.size();
        for (const auto& image : stage->images) {
            stage_images += image.NumBindings(*stage) * image.NumImageArrays();
            required_writes += image.NumImageArrays();
            if (image.is_guest_indexed) {
                ++required_buffer_infos;
                ++required_writes;
            }
        }
        max_stage_images = std::max(max_stage_images, stage_images);
        required_infos += stage_images;
        for (const auto& sampler : stage->samplers) {
            required_infos += sampler.NumBindings(*stage);
        }
    }
    image_infos.reserve(required_infos);
    // Reserve capacity only. Resizing creates default-initialized descriptor
    // writes which must never reach vkCmdPushDescriptorSetKHR if a producer
    // supplies fewer records than its estimate.
    set_writes.reserve(required_writes);
    // VkWriteDescriptorSet::pBufferInfo pointers remain valid while adding
    // numeric metadata alongside regular shader buffer bindings.
    buffer_infos.reserve(required_buffer_infos);
    image_bindings.resize(std::max(max_stage_images, std::size_t{Shader::NUM_IMAGES}));

    bool uses_dma = false;
    for (const auto* stage : pipeline->GetStages()) {
        if (stage && stage->uses_dma) {
            buffer_cache.PrepareDmaSources(stage->flattened_ud_sources);
            uses_dma = true;
        }
    }

    // Bind resource buffers and textures.
    Shader::Backend::Bindings binding{};
    push_data = MakeUserData(liverpool->regs);
    for (const auto* stage : pipeline->GetStages()) {
        if (!stage) {
            continue;
        }
        BindBuffers(*stage, binding, push_data);
        BindTextures(*stage, binding);
        uses_dma |= stage->uses_dma;
    }

    if (uses_dma) {
        buffer_cache.SynchronizeDmaBuffers([this](const VideoCore::Buffer* buffer, u64 offset,
                                                 u64 size) {
            needs_barrier |= runtime.IsBufferAccessed(buffer, offset, size);
            bound_buffers.emplace_back(buffer, offset, size, vk::AccessFlagBits2::eShaderRead);
        });
    }

    // The dynamic write list must agree with the declared shader resources,
    // and each Vulkan write must provide its type-appropriate descriptor
    // data. Reject inconsistent producer metadata before it reaches RADV.
    if (set_writes.size() != required_writes) {
        LOG_ERROR(Render_Vulkan, "Descriptor write count mismatch: expected={} created={}",
                  required_writes, set_writes.size());
        return false;
    }
    for (std::size_t i = 0; i < set_writes.size(); ++i) {
        const auto& write = set_writes[i];
        const auto type = write.descriptorType;
        const bool is_buffer = type == vk::DescriptorType::eStorageBuffer;
        const bool is_image = type == vk::DescriptorType::eSampledImage ||
                              type == vk::DescriptorType::eStorageImage ||
                              type == vk::DescriptorType::eSampler;
        if (write.descriptorCount == 0 || (is_buffer && write.pBufferInfo == nullptr) ||
            (is_image && write.pImageInfo == nullptr) || (!is_buffer && !is_image)) {
            LOG_ERROR(Render_Vulkan,
                      "Invalid Vulkan descriptor write: ordinal={} binding={} type={} count={} "
                      "buffer_info={} image_info={}",
                      i, write.dstBinding, static_cast<u32>(type), write.descriptorCount,
                      static_cast<const void*>(write.pBufferInfo),
                      static_cast<const void*>(write.pImageInfo));
            return false;
        }
    }
    return true;
}

Rasterizer::VertexBufferBindings Rasterizer::PrepareVertexBuffers(
    const GraphicsPipeline* pipeline) {
    const auto& regs = liverpool->regs;
    VertexBufferBindings state;
    VertexInputs<vk::VertexInputBindingDivisorDescriptionEXT> divisors;
    VertexInputs<AmdGpu::Buffer> guest_buffers;
    pipeline->GetVertexInputs(state.attributes, state.bindings, divisors, guest_buffers,
                              regs.vgt_instance_step_rate_0, regs.vgt_instance_step_rate_1);

    if (state.bindings.empty()) {
        return state;
    }

    struct BufferRange {
        VAddr base_address;
        VAddr end_address;
        const VideoCore::Buffer* buffer;
        u64 offset;

        [[nodiscard]] size_t GetSize() const {
            return end_address - base_address;
        }
    };

    // Build list of ranges covering the requested buffers
    VertexInputs<BufferRange> ranges{};
    for (const auto& buffer : guest_buffers) {
        if (buffer.base_address != 0 && buffer.GetSize() > 0) {
            ranges.emplace_back(buffer.base_address, buffer.base_address + buffer.GetSize());
        }
    }

    // Merge connecting ranges together
    VertexInputs<BufferRange> ranges_merged{};
    if (!ranges.empty()) {
        std::ranges::sort(ranges, [](const BufferRange& lhv, const BufferRange& rhv) {
            return lhv.base_address < rhv.base_address;
        });
        ranges_merged.emplace_back(ranges[0]);
        for (auto range : ranges) {
            auto& prev_range = ranges_merged.back();
            if (prev_range.end_address < range.base_address) {
                ranges_merged.emplace_back(range);
            } else {
                prev_range.end_address = std::max(prev_range.end_address, range.end_address);
            }
        }
    }

    // Map buffers for merged ranges
    for (auto& range : ranges_merged) {
        const u64 size = memory->ClampRangeSize(range.base_address, range.GetSize());
        std::tie(range.buffer, range.offset) = buffer_cache.ObtainBuffer(range.base_address, size);
        needs_barrier |= runtime.IsBufferAccessed(range.buffer, range.offset, size);
        bound_buffers.emplace_back(range.buffer, range.offset, size,
                                   vk::AccessFlagBits2::eVertexAttributeRead);
    }

    for (const auto& buffer : guest_buffers) {
        if (buffer.base_address != 0 && buffer.GetSize() > 0) {
            const auto host_buffer_info =
                std::ranges::find_if(ranges_merged, [&](const BufferRange& range) {
                    return buffer.base_address >= range.base_address &&
                           buffer.base_address < range.end_address;
                });
            ASSERT(host_buffer_info != ranges_merged.cend());
            state.buffers.emplace_back(host_buffer_info->buffer->Handle());
            state.offsets.push_back(host_buffer_info->offset + buffer.base_address -
                                    host_buffer_info->base_address);
        } else {
            state.buffers.emplace_back(VK_NULL_HANDLE);
            state.offsets.push_back(0);
        }
        state.sizes.push_back(buffer.GetSize());
        state.strides.push_back(buffer.GetStride());
    }

    return state;
}

void Rasterizer::BindVertexBuffers(vk::CommandBuffer cmdbuf,
                                   const VertexBufferBindings& state) const {
    if (instance.IsVertexInputDynamicState()) {
        cmdbuf.setVertexInputEXT(state.bindings, state.attributes);
    }
    if (state.buffers.empty()) {
        return;
    }
    const auto num_buffers = state.buffers.size();
    if (instance.IsVertexInputDynamicState()) {
        cmdbuf.bindVertexBuffers(0, num_buffers, state.buffers.data(), state.offsets.data());
    } else {
        cmdbuf.bindVertexBuffers2(0, num_buffers, state.buffers.data(), state.offsets.data(),
                                  state.sizes.data(), state.strides.data());
    }
}

Rasterizer::IndexBufferBinding Rasterizer::PrepareIndexBuffer(u32 index_offset, bool is_indirect) {
    const auto& regs = liverpool->regs;

    // Figure out index type and size.
    const bool is_index16 = regs.index_buffer_type.index_type == AmdGpu::IndexType::Index16;
    const vk::IndexType index_type = is_index16 ? vk::IndexType::eUint16 : vk::IndexType::eUint32;
    const u32 index_size = is_index16 ? sizeof(u16) : sizeof(u32);
    const VAddr base_address = regs.index_base_address.Address<VAddr>();
    const VAddr index_address = base_address + u64(index_offset) * index_size;

    // Bind index buffer.
    const u32 num_indices =
        is_indirect ? regs.max_index_size : std::min(regs.num_indices, regs.max_index_size);
    const u64 requested_size = u64(num_indices) * index_size;
    const u64 mapped_size =
        base_address != 0 ? memory->ClampRangeSize(index_address, requested_size) : 0;
    u32 index_buffer_size = static_cast<u32>(Common::AlignDown(mapped_size, index_size));
    vk::Buffer index_buffer{};
    u64 buffer_offset{};
    if (index_buffer_size != 0) {
        const auto [buffer, offset] = buffer_cache.ObtainBuffer(index_address, index_buffer_size);
        needs_barrier |= runtime.IsBufferAccessed(buffer, offset, index_buffer_size);
        bound_buffers.emplace_back(buffer, offset, index_buffer_size,
                                   vk::AccessFlagBits2::eIndexRead);
        index_buffer = buffer->Handle();
        buffer_offset = offset;
    } else if (!instance.IsMaintenance6Supported()) {
        const auto* null_buffer = buffer_cache.GetNullIndexBuffer();
        index_buffer_size = static_cast<u32>(null_buffer->SizeBytes());
        needs_barrier |= runtime.IsBufferAccessed(null_buffer, 0, index_buffer_size);
        bound_buffers.emplace_back(null_buffer, 0, index_buffer_size,
                                   vk::AccessFlagBits2::eIndexRead);
        index_buffer = null_buffer->Handle();
    }

    return {index_buffer, buffer_offset, index_buffer_size, index_type};
}

void Rasterizer::BindIndexBuffer(vk::CommandBuffer cmdbuf, const IndexBufferBinding& state) const {
    if (instance.IsMaintenance5Supported()) {
        cmdbuf.bindIndexBuffer2KHR(state.buffer, state.offset, state.size, state.type);
    } else {
        cmdbuf.bindIndexBuffer(state.buffer, state.offset, state.type);
    }
}

void Rasterizer::ResetBindings(bool is_compute) {
    for (auto& image_id : bound_images) {
        texture_cache.GetImage(image_id).binding = {};
    }
    for (const auto [buffer, offset, size, src_access] : bound_buffers) {
        const auto dst_stage = BufferAccessStage(src_access, is_compute);
        runtime.AccessBuffer(buffer, offset, size, dst_stage, src_access);
    }
    bound_images.clear();
    bound_buffers.clear();
    needs_barrier = false;
}

bool Rasterizer::IsComputeMetaClear(const Pipeline* pipeline) {
    if (!pipeline->IsCompute()) {
        return false;
    }

    // Most of the time when a metadata is updated with a shader it gets cleared. It means
    // we can skip the whole dispatch and update the tracked state instead. Also, it is not
    // intended to be consumed and in such rare cases (e.g. HTile introspection, CRAA) we
    // will need its full emulation anyways.
    const auto& info = pipeline->GetStage(Shader::SwStage::Compute);

    // Assume if a shader reads metadata, it is a copy shader.
    for (const auto& desc : info.buffers) {
        const VAddr address = desc.GetSharp(info).base_address;
        if (!desc.IsSpecial() && !desc.is_written && texture_cache.IsMeta(address)) {
            return false;
        }
    }

    // Metadata surfaces are tiled and thus need address calculation to be written properly.
    // If a shader wants to encode HTILE, for example, from a depth image it will have to compute
    // proper tile address from dispatch invocation id. This address calculation contains an xor
    // operation so use it as a heuristic for metadata writes that are probably not clears.
    if (!info.has_bitwise_xor) {
        // Assume if a shader writes metadata without address calculation, it is a clear shader.
        for (const auto& desc : info.buffers) {
            const VAddr address = desc.GetSharp(info).base_address;
            if (!desc.IsSpecial() && desc.is_written && texture_cache.ClearMeta(address)) {
                // Assume all slices were updates
                LOG_TRACE(Render_Vulkan, "Metadata update skipped");
                return true;
            }
        }
    }
    return false;
}

bool Rasterizer::IsComputeImageCopy(const Pipeline* pipeline) {
    if (!pipeline->IsCompute()) {
        return false;
    }

    // Ensure shader only has 2 bound buffers
    const auto& cs_pgm = liverpool->GetCsRegs();
    const auto& info = pipeline->GetStage(Shader::SwStage::Compute);
    if (cs_pgm.num_thread_x.full != 64 || info.buffers.size() < 2 || info.buffers.size() > 3 ||
        !info.images.empty()) {
        return false;
    }

    // Those 2 buffers must both be formatted. One must be source and another destination.
    const auto& desc0 = info.buffers[0];
    const auto& desc1 = info.buffers[1];
    if (!desc0.is_formatted || !desc1.is_formatted || desc0.is_written == desc1.is_written) {
        return false;
    }
    if (info.buffers.size() == 3 && info.buffers[2].buffer_type != Shader::BufferType::Flatbuf) {
        return false;
    }

    // Buffers must have the same size and each thread of the dispatch must copy 1 dword of data
    const AmdGpu::Buffer buf0 = desc0.GetSharp(info);
    const AmdGpu::Buffer buf1 = desc1.GetSharp(info);
    if (buf0.GetSize() != buf1.GetSize() || cs_pgm.dim_x != (buf0.GetSize() / 256)) {
        return false;
    }

    // Find images the buffer alias
    const auto image0_id = texture_cache.FindImageFromRange(buf0.base_address, buf0.GetSize());
    if (!image0_id) {
        return false;
    }
    const auto image1_id =
        texture_cache.FindImageFromRange(buf1.base_address, buf1.GetSize(), false);
    if (!image1_id) {
        return false;
    }

    // Image copy must be valid
    VideoCore::Image& image0 = texture_cache.GetImage(image0_id);
    VideoCore::Image& image1 = texture_cache.GetImage(image1_id);
    if (image0.info.guest_size != image1.info.guest_size ||
        image0.info.pitch != image1.info.pitch || image0.info.guest_size != buf0.GetSize() ||
        image0.info.num_bits != image1.info.num_bits) {
        return false;
    }

    // Perform image copy
    VideoCore::Image& src_image = desc0.is_written ? image1 : image0;
    VideoCore::Image& dst_image = desc0.is_written ? image0 : image1;
    runtime.CopyColorAndDepth(&src_image, &dst_image);
    texture_cache.TrackGpuImage(desc0.is_written ? image0_id : image1_id);
    return true;
}

bool Rasterizer::IsComputeImageClear(const Pipeline* pipeline) {
    if (!pipeline->IsCompute()) {
        return false;
    }

    // Ensure shader only has 2 bound buffers
    const auto& cs_pgm = liverpool->GetCsRegs();
    const auto& info = pipeline->GetStage(Shader::SwStage::Compute);
    if (cs_pgm.num_thread_x.full != 64 || info.buffers.size() < 2 || info.buffers.size() > 3 ||
        !info.images.empty()) {
        return false;
    }

    // From those 2 buffers, first must hold the clear vector and second the image being cleared
    const auto& desc0 = info.buffers[0];
    const auto& desc1 = info.buffers[1];
    if (desc0.is_formatted || !desc1.is_formatted || desc0.is_written || !desc1.is_written) {
        return false;
    }
    if (info.buffers.size() == 3 && info.buffers[2].buffer_type != Shader::BufferType::Flatbuf) {
        return false;
    }

    // First buffer must have size of vec4 and second the size of a single layer
    const AmdGpu::Buffer buf0 = desc0.GetSharp(info);
    const AmdGpu::Buffer buf1 = desc1.GetSharp(info);
    const u32 buf1_bpp = AmdGpu::NumBitsPerBlock(buf1.GetDataFmt());
    if (buf0.GetSize() != 16 || (cs_pgm.dim_x * 128ULL * (buf1_bpp / 8)) != buf1.GetSize()) {
        return false;
    }

    // Find image the buffer alias
    const auto image1_id =
        texture_cache.FindImageFromRange(buf1.base_address, buf1.GetSize(), false);
    if (!image1_id) {
        return false;
    }

    // Image clear must be valid and number of component must match
    VideoCore::Image& image1 = texture_cache.GetImage(image1_id);
    if (image1.info.guest_size != buf1.GetSize() || image1.info.num_bits != buf1_bpp ||
        image1.info.props.is_depth ||
        AmdGpu::NumComponents(buf1.GetDataFmt()) != vk::componentCount(image1.info.pixel_format)) {
        return false;
    }

    // Perform image clear
    const float* values = reinterpret_cast<float*>(buf0.base_address);
    const vk::ClearValue clear = {
        .color = {.float32 = std::array<float, 4>{values[0], values[1], values[2], values[3]}},
    };
    const VideoCore::SubresourceRange range = {
        .base =
            {
                .level = 0,
                .layer = 0,
            },
        .extent = image1.info.resources,
    };
    runtime.ClearImage(&image1, range, clear);
    texture_cache.TrackGpuImage(image1_id);
    return true;
}

void Rasterizer::BindBuffers(const Shader::Info& stage, Shader::Backend::Bindings& binding,
                             Shader::PushData& push_data) {
    const u64 alignment = instance.StorageMinAlignment();
    for (const auto& desc : stage.buffers) {
        const auto src_access =
            vk::AccessFlagBits2::eShaderRead |
            (desc.is_written ? vk::AccessFlagBits2::eShaderWrite : vk::AccessFlagBits2::eNone);
        if (desc.IsSpecial()) {
            if (desc.buffer_type == Shader::BufferType::OrderedGdsState ||
                desc.buffer_type == Shader::BufferType::OrderedGdsResume) {
                ASSERT(stage.uses_ordered_gds && active_ordered_gds_capture_);
                const auto& owner = *active_ordered_gds_capture_;
                ASSERT(owner.continuation_state);
                const auto& buffer = desc.buffer_type == Shader::BufferType::OrderedGdsState
                                         ? *owner.continuation_state
                                         : owner.resume_packets;
                buffer_infos.emplace_back(buffer.Handle(), 0, buffer.SizeBytes());
                needs_barrier |=
                    runtime.IsBufferAccessed(&buffer, 0, buffer.SizeBytes(), desc.is_written);
                bound_buffers.emplace_back(&buffer, 0, buffer.SizeBytes(), src_access);
            } else if (desc.buffer_type == Shader::BufferType::OrderedGdsEpochAuth) {
                ASSERT_MSG(stage.hw_stage == Shader::HwStage::Compute && stage.uses_ordered_gds &&
                               !desc.is_written && active_ordered_gds_capture_ &&
                               active_ordered_gds_capture_->AuthorizedEpoch() != 0,
                           "ordered GDS epoch requires host-authorized capture scope");
                const auto& epoch = *active_ordered_gds_capture_;
                const auto& authority = epoch.capture_auth;
                const u64 bytes = epoch.plan.epoch_auth_bytes;
                ASSERT_MSG(authority.Handle() != VK_NULL_HANDLE &&
                               authority.Handle() != buffer_cache.GetGdsBuffer()->Handle() &&
                               authority.Handle() != epoch.capture_packets.Handle() &&
                               authority.Handle() != epoch.capture_registers.Handle() &&
                               bytes > 0 && bytes == authority.SizeBytes(),
                           "GCN trusted dispatch token SSBO must be independent");
                buffer_infos.emplace_back(authority.Handle(), 0, bytes);
                needs_barrier |= runtime.IsBufferAccessed(&authority, 0, bytes, false);
                bound_buffers.emplace_back(&authority, 0, bytes, src_access);
            } else if (desc.buffer_type == Shader::BufferType::OrderedGdsCapture) {
                // Only an explicit, owned guest capture phase may bind a
                // dedicated GPU packet buffer. No guest GDS or wraparound
                // stream alias, and no ordinary shader can opt into this.
                ASSERT_MSG(stage.hw_stage == Shader::HwStage::Compute && stage.uses_ordered_gds &&
                               desc.is_written && active_ordered_gds_capture_,
                           "ordered GDS capture packets require explicit live epoch");
                const auto& epoch = *active_ordered_gds_capture_;
                const auto& packets = epoch.capture_packets;
                const u64 bytes = epoch.plan.packet_bytes;
                ASSERT_MSG(packets.Handle() != VK_NULL_HANDLE &&
                               packets.Handle() != buffer_cache.GetGdsBuffer()->Handle() &&
                               packets.Handle() != epoch.capture_registers.Handle() &&
                               packets.Handle() != epoch.resume_packets.Handle() && bytes > 0 &&
                               bytes == packets.SizeBytes(),
                           "GCN GPU packet SSBO must be independent bounded device storage");
                buffer_infos.emplace_back(packets.Handle(), 0, bytes);
                needs_barrier |= runtime.IsBufferAccessed(&packets, 0, bytes, true);
                bound_buffers.emplace_back(&packets, 0, bytes, src_access);
            } else if (desc.buffer_type == Shader::BufferType::OrderedGdsCheckpoint) {
                // Only a caller-authenticated, explicitly scoped capture pass
                // may bind the actual live register snapshot SSBO. No capture
                // scope exists on the normal game path, so a compiler bug
                // cannot accidentally alias GDS RAM or a wrapping stream.
                ASSERT_MSG(stage.hw_stage == Shader::HwStage::Compute && stage.uses_ordered_gds &&
                               desc.is_written && active_ordered_gds_capture_,
                           "ordered GDS checkpoint requires explicit live capture epoch");
                const auto& epoch = *active_ordered_gds_capture_;
                const auto& checkpoint = epoch.capture_registers;
                const u64 bytes = epoch.plan.register_layout.total_bytes;
                ASSERT_MSG(checkpoint.Handle() != VK_NULL_HANDLE &&
                               checkpoint.Handle() != buffer_cache.GetGdsBuffer()->Handle() &&
                               checkpoint.Handle() != epoch.resume_registers.Handle() &&
                               bytes > 0 && bytes == checkpoint.SizeBytes(),
                           "GCN checkpoint SSBO must be independent bounded device storage");
                buffer_infos.emplace_back(checkpoint.Handle(), 0, bytes);
                needs_barrier |= runtime.IsBufferAccessed(&checkpoint, 0, bytes, true);
                bound_buffers.emplace_back(&checkpoint, 0, bytes, src_access);
            } else if (desc.buffer_type == Shader::BufferType::GdsBuffer) {
                const auto* gds_buf = buffer_cache.GetGdsBuffer();
                buffer_infos.emplace_back(gds_buf->Handle(), 0, gds_buf->SizeBytes());
                needs_barrier |=
                    runtime.IsBufferAccessed(gds_buf, 0, gds_buf->SizeBytes(), desc.is_written);
                bound_buffers.emplace_back(gds_buf, 0, gds_buf->SizeBytes(), src_access);
            } else if (desc.buffer_type == Shader::BufferType::Flatbuf) {
                auto& vk_buffer = buffer_cache.GetStreamBuffer();
                const u32 ubo_size = stage.flattened_ud_buf.size() * sizeof(u32);
                const u64 offset =
                    vk_buffer.Copy(stage.flattened_ud_buf.data(), ubo_size, alignment);
                buffer_infos.emplace_back(vk_buffer.Handle(), offset, ubo_size);
            } else if (desc.buffer_type == Shader::BufferType::ClipPlanes) {
                // Permutations compiled without enabled planes never read the buffer, so the
                // declared binding is satisfied with a null descriptor instead of a copy.
                if (liverpool->regs.clipper_control.user_clip_plane_enable == 0) {
                    buffer_infos.emplace_back(VK_NULL_HANDLE, 0, VK_WHOLE_SIZE);
                } else {
                    auto& vk_buffer = buffer_cache.GetStreamBuffer();
                    std::array<float, AmdGpu::NUM_CLIP_PLANES * 4> planes{};
                    for (u32 i = 0; i < AmdGpu::NUM_CLIP_PLANES; ++i) {
                        const auto& plane = liverpool->regs.clip_user_data[i];
                        planes[i * 4 + 0] = std::bit_cast<float>(plane.data_x);
                        planes[i * 4 + 1] = std::bit_cast<float>(plane.data_y);
                        planes[i * 4 + 2] = std::bit_cast<float>(plane.data_z);
                        planes[i * 4 + 3] = std::bit_cast<float>(plane.data_w);
                    }
                    const u32 ubo_size = static_cast<u32>(sizeof(planes));
                    const u64 offset = vk_buffer.Copy(planes.data(), ubo_size, alignment);
                    buffer_infos.emplace_back(vk_buffer.Handle(), offset, ubo_size);
                }
            } else if (desc.buffer_type == Shader::BufferType::BdaPagetable) {
                const auto* bda_buffer = buffer_cache.GetBdaPageTableBuffer();
                buffer_infos.emplace_back(bda_buffer->Handle(), 0, bda_buffer->SizeBytes());
                needs_barrier |= runtime.IsBufferAccessed(bda_buffer, 0, bda_buffer->SizeBytes());
                bound_buffers.emplace_back(bda_buffer, 0, bda_buffer->SizeBytes(),
                                           vk::AccessFlagBits2::eShaderRead);
            } else if (desc.buffer_type == Shader::BufferType::FaultBuffer) {
                const auto* fault_buffer = buffer_cache.GetFaultBuffer();
                buffer_infos.emplace_back(fault_buffer->Handle(), 0, fault_buffer->SizeBytes());
                needs_barrier |=
                    runtime.IsBufferAccessed(fault_buffer, 0, fault_buffer->SizeBytes(), true);
                bound_buffers.emplace_back(fault_buffer, 0, fault_buffer->SizeBytes(), src_access);
            } else if (desc.buffer_type == Shader::BufferType::SharedMemory) {
                if (stage.uses_ordered_gds) {
                    // All guest LDS ops for ordered GDS are lowered to a
                    // per-workgroup SSBO that persists across GPU passes.
                    // Do not reinitialize a temporary streaming LDS buffer
                    // when the capture or continuation binds this resource.
                    ASSERT_MSG(stage.hw_stage == Shader::HwStage::Compute &&
                                   stage.ordered_gds_persistent_lds && desc.is_written &&
                                   active_ordered_gds_capture_ &&
                                   active_ordered_gds_capture_->AuthorizedEpoch() != 0,
                               "ordered GDS persistent LDS requires authenticated capture");
                    const auto& owner = *active_ordered_gds_capture_;
                    ASSERT_MSG(owner.persistent_lds && owner.PersistentLdsInitialized() &&
                                   owner.plan.lds_layout &&
                                   owner.plan.persistent_lds_bytes ==
                                       u64(owner.plan.workgroups) *
                                           owner.plan.lds_layout->lds_bytes,
                               "ordered GDS persistent LDS extent invalid");
                    const auto& lds_buffer = *owner.persistent_lds;
                    const u64 bytes = owner.plan.persistent_lds_bytes;
                    ASSERT_MSG(bytes && bytes == lds_buffer.SizeBytes() &&
                                   lds_buffer.Handle() != VK_NULL_HANDLE &&
                                   lds_buffer.Handle() != buffer_cache.GetGdsBuffer()->Handle() &&
                                   lds_buffer.Handle() != owner.capture_packets.Handle() &&
                                   lds_buffer.Handle() != owner.resume_packets.Handle() &&
                                   lds_buffer.Handle() != owner.capture_registers.Handle() &&
                                   lds_buffer.Handle() != owner.resume_registers.Handle() &&
                                   lds_buffer.Handle() != owner.capture_lds->Handle() &&
                                   lds_buffer.Handle() != owner.resume_lds->Handle(),
                               "ordered GDS persistent LDS must not alias other GPU buffers");
                    buffer_infos.emplace_back(lds_buffer.Handle(), 0, bytes);
                    needs_barrier |= runtime.IsBufferAccessed(&lds_buffer, 0, bytes, true);
                    bound_buffers.emplace_back(&lds_buffer, 0, bytes, src_access);
                    owner.MarkPersistentLdsBound();
                } else {
                    auto& lds_buffer = buffer_cache.GetStreamBuffer();
                    const auto& cs_program = liverpool->GetCsRegs();
                    const auto lds_size = cs_program.SharedMemSize() * cs_program.NumWorkgroups();
                    const auto [data, offset] = lds_buffer.Map(lds_size, alignment);
                    std::memset(data, 0, lds_size);
                    lds_buffer.Commit();
                    buffer_infos.emplace_back(lds_buffer.Handle(), offset, lds_size);
                }
            } else {
                UNREACHABLE_MSG("Unexpected buffer type {}", u32(desc.buffer_type));
            }
        } else {
            const auto vsharp = desc.GetSharp(stage);
            if (vsharp.base_address == 0 || vsharp.GetSize() == 0) {
                buffer_infos.emplace_back(VK_NULL_HANDLE, 0, VK_WHOLE_SIZE);
            } else {
                const u64 requested_size = std::min<u64>(vsharp.GetSize(), desc.accessed_size);
                const u64 size = memory->ClampRangeSize(vsharp.base_address, requested_size);
                if (size != requested_size) {
                    LOG_DEBUG(Render, "Clamped size from {} to {} for stage {:#x}",
                              requested_size, size, stage.pgm_hash);
                }
                VideoCore::ObtainBufferFlags flags = {};
                if (requested_size != vsharp.GetSize()) {
                    flags |= VideoCore::ObtainBufferFlags::IgnoreStreamBuffer;
                }
                if (desc.is_written) {
                    flags |= VideoCore::ObtainBufferFlags::IsWritten;
                }
                if (desc.is_formatted) {
                    flags |= VideoCore::ObtainBufferFlags::IsTexelBuffer;
                }
                const auto [buffer, offset] =
                    buffer_cache.ObtainBuffer(vsharp.base_address, size, flags);
                const u64 offset_aligned = Common::AlignDown(offset, alignment);
                const u64 adjust = offset - offset_aligned;
                if (adjust % 4 != 0) {
                    LOG_WARNING(Render_Vulkan, "Buffer binding in shader {:#x} isn't dword aligned",
                                stage.pgm_hash);
                }
                push_data.AddOffset(binding.buffer, adjust);
                buffer_infos.emplace_back(buffer->Handle(), offset_aligned, size + adjust);
                bound_buffers.emplace_back(buffer, offset, size, src_access);
                needs_barrier |= runtime.IsBufferAccessed(buffer, offset, size, desc.is_written);
            }
        }

        auto& set_write = set_writes.emplace_back();
        set_write.dstSet = VK_NULL_HANDLE;
        set_write.dstBinding = binding.unified++;
        set_write.dstArrayElement = 0;
        set_write.descriptorCount = 1;
        set_write.descriptorType = vk::DescriptorType::eStorageBuffer;
        set_write.pBufferInfo = &buffer_infos.back();
        ++binding.buffer;
    }
}

void Rasterizer::BindTextures(const Shader::Info& stage, Shader::Backend::Bindings& binding) {
    const u32 first_image_idx = image_infos.size();
    // To emulate storing to explicit mip levels, build a descriptor array with each mip level.
    boost::container::small_vector<u32, 8> image_descriptor_array_sizes;
    boost::container::small_vector<bool, 8> image_descriptor_is_storage;
    boost::container::small_vector<std::vector<u32>, 4> image_index_maps;

    u32 num_images{};
    for (const auto& image_desc : stage.images) {
        if (image_desc.is_guest_indexed) {
            const u32 count = image_desc.NumBindings(stage);
            ASSERT(count >= 2 && count <= Shader::MaxGuestIndexedImageEntries + 1);
            const u32 extra_images = (image_desc.NumImageArrays() - 1u) * count;
            ASSERT(u64(num_images) + count + extra_images <= image_bindings.size());

            AmdGpu::Buffer source{};
            const bool fetched =
                image_desc.indexed_table_fetch.Fetch(stage.flattened_ud_buf.data(), &source);
            const u64 bytes = fetched ? (source.stride == 0
                                             ? static_cast<u64>(source.num_records)
                                             : static_cast<u64>(source.stride) * source.num_records)
                                      : 0u;
            const u32 record_stride = image_desc.indexed_record_stride_bytes;
            const u32 descriptor_offset = image_desc.indexed_descriptor_offset_bytes;
            const u32 represented_entries =
                fetched && source.Valid()
                    ? (record_stride ? Shader::Optimization::CompleteGuestTsharpRecordEntries(
                                           static_cast<u32>(source.stride),
                                           static_cast<u32>(source.num_records), record_stride,
                                           descriptor_offset, Shader::MaxGuestIndexedImageEntries)
                                     : Shader::Optimization::CompleteGuestTsharpTableEntries(
                                           static_cast<u32>(source.stride),
                                           static_cast<u32>(source.num_records),
                                           Shader::MaxGuestIndexedImageEntries))
                    : 0u;
            ASSERT_MSG(represented_entries == count - 1u,
                       "Indexed image table changed after shader specialization");
            const u64 record_bytes = record_stride ? record_stride : sizeof(AmdGpu::Image);
            const bool table_layout_valid =
                record_stride ? descriptor_offset <= record_stride &&
                                    sizeof(AmdGpu::Image) <= record_stride - descriptor_offset
                              : descriptor_offset == 0;
            const bool table_available =
                fetched && source.Valid() && source.base_address != 0 && table_layout_valid;
            // A table may have been generated by earlier GPU buffer writes.
            // Demand-reconcile only the *represented prefix* before host memcpy,
            // preserving the historical 2D binding path and every valid T# ID.
            const u32 image_entries = std::min(count - 1u, represented_entries);
            const auto image_prefix = Shader::Optimization::GuestDescriptorPrefixBytes(
                image_entries, record_bytes, descriptor_offset, sizeof(AmdGpu::Image));
            if (table_available && image_prefix && *image_prefix != 0 && *image_prefix <= bytes &&
                *image_prefix <= std::numeric_limits<u32>::max() &&
                source.base_address <= std::numeric_limits<u64>::max() - *image_prefix &&
                memory->IsValidMapping(source.base_address, *image_prefix) &&
                buffer_cache.IsRegionGpuModified(source.base_address, *image_prefix)) {
                buffer_cache.ReadMemory(source.base_address, *image_prefix, false, false, true);
                static std::atomic<u32> coherence_reports{0};
                if (coherence_reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
                    LOG_INFO(Render_Vulkan,
                             "GpuDescriptorTableCpuCoherence: kind=image stage={:#x} "
                             "addr={:#x} bytes={} entries={} stride={}",
                             stage.pgm_hash, static_cast<u64>(source.base_address), *image_prefix,
                             image_entries, record_stride);
                }
            }
            const u32 image_base = num_images;
            num_images += count + extra_images;
            std::vector<u32> numeric_metadata(count, 0u);
            for (u32 idx = image_base; idx < num_images; ++idx) {
                image_bindings[idx].image_id = {};
            }

            const auto limits = instance.GetPhysicalDevice().getProperties().limits;
            u32 bound_2d = 0, bound_3d = 0, bound_cube = 0;
            std::array<u32, 3> bound_numeric{};
            for (u32 i = 0; i + 1u < count; ++i) {
                // c925 uses independent runtime records: never discard an
                // otherwise valid 2D guest image because a later table entry
                // is missing or has a different image dimension.
                if (!table_available || i >= represented_entries) {
                    continue;
                }
                const u64 record_offset_bytes = u64(i) * record_bytes + descriptor_offset;
                if (record_offset_bytes > bytes ||
                    sizeof(AmdGpu::Image) > bytes - record_offset_bytes ||
                    source.base_address > std::numeric_limits<u64>::max() - record_offset_bytes) {
                    continue;
                }
                const u64 entry_addr = source.base_address + record_offset_bytes;
                if (entry_addr > std::numeric_limits<u64>::max() - (sizeof(AmdGpu::Image) - 1u) ||
                    !memory->IsValidMapping(entry_addr, sizeof(AmdGpu::Image))) {
                    continue;
                }
                AmdGpu::Image raw{};
                std::memcpy(&raw, reinterpret_cast<const void*>(entry_addr), sizeof(raw));
                if (!raw.Valid() || raw.Address() == 0 ||
                    !memory->IsValidGpuMapping(raw.Address(), 0)) {
                    continue;
                }

                const auto type = raw.GetViewType(image_desc.is_array);
                u32 typed_slot = 0;
                u32 max_dimension = limits.maxImageDimension2D;
                u64 layers = 1;
                switch (type) {
                case AmdGpu::ImageType::Color1D:
                    ASSERT_MSG(image_desc.is_guest_mixed_indexed,
                               "Indexed 1D image requires dimension-aware sample coordinates");
                    raw.height = 0;
                    break;
                case AmdGpu::ImageType::Color2D:
                    break;
                case AmdGpu::ImageType::Color3D:
                    ASSERT_MSG(image_desc.is_guest_mixed_indexed,
                               "Indexed image dimension requires a typed sample variant");
                    if (bound_3d >= (count - 1u)) {
                        UNREACHABLE_MSG("Indexed 3D descriptor count exceeds complete table");
                    }
                    typed_slot = 1;
                    max_dimension = limits.maxImageDimension3D;
                    layers = u64(raw.depth) + 1u;
                    if (layers > max_dimension) {
                        continue;
                    }
                    break;
                case AmdGpu::ImageType::Cube:
                    ASSERT_MSG(image_desc.is_guest_mixed_indexed,
                               "Indexed image dimension requires a typed sample variant");
                    if (bound_cube >= (count - 1u)) {
                        UNREACHABLE_MSG("Indexed cube descriptor count exceeds complete table");
                    }
                    typed_slot = 2;
                    max_dimension = limits.maxImageDimensionCube;
                    layers = (u64(raw.depth) + 1u) * 6u;
                    if (layers > limits.maxImageArrayLayers) {
                        continue;
                    }
                    break;
                default:
                    UNREACHABLE_MSG("Unsupported indexed guest image dimension {}", u32(type));
                }

                const auto fmt = raw.GetDataFmt();
                const auto nfmt = raw.GetNumberFmt();
                if (!magic_enum::enum_contains(fmt) || !magic_enum::enum_contains(nfmt) ||
                    fmt == AmdGpu::DataFormat::FormatInvalid || raw.width >= max_dimension ||
                    raw.height >= max_dimension || raw.base_level > raw.last_level ||
                    raw.pitch < raw.width) {
                    continue;
                }
                const u32 numeric = u32(AmdGpu::GetNumberClass(nfmt));
                const u32 dimensions = image_desc.is_guest_mixed_indexed ? 3u : 1u;
                const u32 slot_index = image_base + (numeric * dimensions + typed_slot) * count + i;
                auto& slot = image_bindings[slot_index];
                auto& desc = slot.desc;
                std::construct_at(&desc, raw, image_desc);
                if (type == AmdGpu::ImageType::Cube) {
                    const u32 layers = raw.NumLayers();
                    const u32 base_layer = desc.view_info.range.base.layer;
                    if (layers <= base_layer) {
                        continue;
                    }
                    desc.view_info.range.extent.layers = layers - base_layer;
                }
                slot.image_id = texture_cache.FindImage(desc);
                auto* image = &texture_cache.GetImage(slot.image_id);
                if (type == AmdGpu::ImageType::Color2D &&
                    desc.type == VideoCore::TextureCache::BindingType::Texture) {
                    if (auto depth_id = texture_cache.GetAssociatedDepth(*image)) {
                        slot.image_id = depth_id;
                        image = &texture_cache.GetImage(slot.image_id);
                    }
                }
                image->binding.is_bound = 1u;
                numeric_metadata[i] = Shader::IndexedImageMetadata::Encode(raw);
                ++bound_numeric[numeric];
                if (typed_slot == 0u) {
                    ++bound_2d;
                } else if (typed_slot == 1u) {
                    ++bound_3d;
                } else {
                    ++bound_cube;
                }
            }
            static std::atomic<u32> small_bind_reports{0};
            static std::atomic<u32> large_bind_reports{0};
            const bool large = count > 1024u;
            const bool log =
                large ? large_bind_reports.fetch_add(1u, std::memory_order_relaxed) < 24u
                      : small_bind_reports.fetch_add(1u, std::memory_order_relaxed) < 32u;
            if (log) {
                LOG_INFO(Render_Vulkan,
                         "GuestIndexedImageDimensions: stage={:#x} entries={} "
                         "bound_2d={} bound_3d={} bound_cube={} "
                         "bound_float={} bound_sint={} bound_uint={} "
                         "available={} mixed={} record_stride={}",
                         stage.pgm_hash, count - 1u, bound_2d, bound_3d, bound_cube,
                         bound_numeric[0], bound_numeric[1], bound_numeric[2], represented_entries,
                         image_desc.is_guest_mixed_indexed, record_stride);
            }
            for (u32 i = 0; i < image_desc.NumImageArrays(); ++i) {
                image_descriptor_array_sizes.push_back(count);
                image_descriptor_is_storage.push_back(false);
            }
            image_descriptor_array_sizes.push_back(0u);
            image_descriptor_is_storage.push_back(false);
            image_index_maps.emplace_back(std::move(numeric_metadata));
            continue;
        }

        const auto tsharp = image_desc.GetSharp(stage);
        const auto data_fmt = tsharp.GetDataFmt();
        const auto num_fmt = tsharp.GetNumberFmt();
        if (tsharp.Address() == 0 || data_fmt == AmdGpu::DataFormat::FormatInvalid) {
            image_bindings[num_images++].image_id = {};
            image_descriptor_array_sizes.push_back(1);
            image_descriptor_is_storage.push_back(image_desc.is_written);
            continue;
        }

        if (!memory->IsValidGpuMapping(tsharp.Address(), 0) ||
            !magic_enum::enum_contains(data_fmt) || !magic_enum::enum_contains(num_fmt)) {
            LOG_WARNING(Render_Vulkan,
                        "Rejecting invalid T# address={:#x}, pitch={}, width={}, "
                        "data_format={}, num_format={}",
                        tsharp.Address(), tsharp.pitch, tsharp.width, static_cast<u32>(data_fmt),
                        static_cast<u32>(num_fmt));
            image_bindings[num_images++].image_id = {};
            image_descriptor_array_sizes.push_back(1);
            image_descriptor_is_storage.push_back(image_desc.is_written);
            continue;
        }

        const Shader::MipStorageFallbackMode mip_fallback_mode = image_desc.mip_fallback_mode;
        const u32 num_bindings = image_desc.NumBindings(stage);

        for (auto i = 0; i < num_bindings; i++) {
            auto& [image_id, desc] = image_bindings[num_images++];
            std::construct_at(&desc, tsharp, image_desc);

            if (mip_fallback_mode == Shader::MipStorageFallbackMode::ConstantIndex) {
                ASSERT(num_bindings == 1);
                desc.view_info.range.base.level += image_desc.constant_mip_index;
                desc.view_info.range.extent.levels = 1;
            } else if (mip_fallback_mode == Shader::MipStorageFallbackMode::DynamicIndex) {
                desc.view_info.range.base.level += i;
                desc.view_info.range.extent.levels = 1;
            }

            image_id = texture_cache.FindImage(desc);
            auto* image = &texture_cache.GetImage(image_id);
            // An associated depth/stencil image is suitable for sampled stencil
            // access, but cannot provide a Vulkan storage-image view. Keep
            // storage accesses on the format-compatible cache image selected
            // by FindImage/ResolveDepthOverlap rather than redirecting them
            // to a depth/stencil attachment without STORAGE usage/features.
            if (desc.type == VideoCore::TextureCache::BindingType::Texture) {
                if (auto depth_image_id = texture_cache.GetAssociatedDepth(*image)) {
                    image_id = depth_image_id;
                    image = &texture_cache.GetImage(image_id);
                }
            }
            if (image->binding.is_bound) {
                // The image is already bound. In case if it is about to be used as storage we
                // need to force general layout on it.
                image->binding.force_general |= image_desc.is_written;
            }
            image->binding.is_bound = 1u;
        }

        image_descriptor_array_sizes.push_back(num_bindings);
        image_descriptor_is_storage.push_back(image_desc.is_written);
    }

    const u64 first_upload = runtime.ImageUploadCount();
    u64 batch_upload = first_upload;
    // Second pass to re-bind images that were updated after binding
    for (u32 i = 0; i < num_images; ++i) {
        auto& [image_id, desc] = image_bindings[i];
        bool is_storage = image_id && desc.type == VideoCore::TextureCache::BindingType::Storage;
        if (!image_id) {
            image_infos.emplace_back(VK_NULL_HANDLE, VK_NULL_HANDLE, vk::ImageLayout::eGeneral);
        } else {
            if (auto& old_image = texture_cache.GetImage(image_id);
                old_image.binding.needs_rebind) {
                old_image.binding = {};
                image_id = texture_cache.FindImage(desc);
            }

            bound_images.emplace_back(image_id);

            auto& image = texture_cache.GetImage(image_id);
            auto& image_view = texture_cache.FindTexture(image_id, desc);
            const auto binding = image.binding;

            // The image is either bound as storage in a separate descriptor or bound as render
            // target in feedback loop. Depth images are excluded because they can't be bound as
            // storage and feedback loop doesn't make sense for them
            if ((binding.force_general || binding.is_target) && !image.info.props.is_depth) {
                if (instance.IsAttachmentFeedbackLoopLayoutSupported() && image.binding.is_target) {
                    needs_barrier |= runtime.Transit(
                        &image, vk::ImageLayout::eAttachmentFeedbackLoopOptimalEXT,
                        vk::PipelineStageFlagBits2::eAllGraphics, vk::AccessFlagBits2::eShaderRead);
                } else {
                    needs_barrier |= runtime.Transit(
                        &image, vk::ImageLayout::eGeneral, vk::PipelineStageFlagBits2::eAllCommands,
                        vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite,
                        desc.view_info.range);
                }
            } else {
                if (is_storage) {
                    needs_barrier |= runtime.Transit(
                        &image, vk::ImageLayout::eGeneral, vk::PipelineStageFlagBits2::eAllCommands,
                        vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eShaderWrite,
                        desc.view_info.range);
                } else {
                    const auto new_layout = image.info.props.is_depth
                                                ? vk::ImageLayout::eDepthStencilReadOnlyOptimal
                                                : vk::ImageLayout::eShaderReadOnlyOptimal;
                    needs_barrier |= runtime.Transit(
                        &image, new_layout, vk::PipelineStageFlagBits2::eAllCommands,
                        vk::AccessFlagBits2::eShaderRead, desc.view_info.range);
                }
            }
            image.usage.storage |= is_storage;
            image.usage.texture |= !is_storage;

            image_infos.emplace_back(VK_NULL_HANDLE, *image_view.image_view,
                                     image.backing->state.layout);
        }

        constexpr u64 UploadsPerSession = 1024;
        const u64 uploads = runtime.ImageUploadCount();
        if (uploads - batch_upload >= UploadsPerSession) {
            runtime.FlushBarriers();
            scheduler.BeginSession();
            scheduler.RecordWork(
                "kind=texture-upload-session shader=%llx images=%u/%u uploads=%llu",
                static_cast<unsigned long long>(stage.pgm_hash), i + 1, num_images,
                static_cast<unsigned long long>(uploads - first_upload));
            batch_upload = uploads;
        }
    }
    scheduler.RecordWork(
        "kind=texture-bind-finish shader=%llx images=%u uploads=%llu",
        static_cast<unsigned long long>(stage.pgm_hash), num_images,
        static_cast<unsigned long long>(runtime.ImageUploadCount() - first_upload));

    u32 image_info_idx = first_image_idx;
    u32 image_binding_idx = 0;
    u32 remap_cursor = 0;
    for (u32 image_index = 0; image_index < image_descriptor_array_sizes.size(); ++image_index) {
        const u32 array_size = image_descriptor_array_sizes[image_index];
        if (array_size == 0u) {
            ASSERT(remap_cursor < image_index_maps.size());
            const auto& remap = image_index_maps[remap_cursor++];
            ASSERT(!remap.empty());
            auto& stream = buffer_cache.GetStreamBuffer();
            const u64 bytes = u64(remap.size()) * sizeof(u32);
            const u64 offset = stream.Copy(remap.data(), bytes, instance.StorageMinAlignment());
            buffer_infos.emplace_back(stream.Handle(), offset, bytes);
            auto& write = set_writes.emplace_back();
            write.dstSet = VK_NULL_HANDLE;
            write.dstBinding = binding.unified++;
            write.dstArrayElement = 0u;
            write.descriptorCount = 1u;
            write.descriptorType = vk::DescriptorType::eStorageBuffer;
            write.pBufferInfo = &buffer_infos.back();
            continue;
        }
        // The descriptor set layout is derived from Shader::ImageResource::is_written.
        // TextureCache binding types can differ after depth/stencil association,
        // fallback selection, or invalid texture handles. A descriptor write
        // must use the exact type declared by its pipeline layout.
        const bool is_storage = image_descriptor_is_storage[image_index];
        auto& set_write = set_writes.emplace_back();
        set_write.dstSet = VK_NULL_HANDLE;
        set_write.dstBinding = binding.unified;
        set_write.dstArrayElement = 0;
        set_write.descriptorCount = array_size;
        set_write.descriptorType =
            is_storage ? vk::DescriptorType::eStorageImage : vk::DescriptorType::eSampledImage;
        set_write.pImageInfo = &image_infos[image_info_idx];

        image_info_idx += array_size;
        image_binding_idx += array_size;
        binding.unified += array_size;
    }

    for (const auto& sampler : stage.samplers) {
        if (sampler.is_guest_indexed) {
            const u32 count = sampler.NumBindings(stage);
            AmdGpu::Buffer source{};
            const bool fetched =
                sampler.indexed_table_fetch.Fetch(stage.flattened_ud_buf.data(), &source);
            const u32 record_stride = sampler.indexed_record_stride_bytes;
            const u32 image_offset = sampler.indexed_image_offset_bytes;
            const u32 sampler_offset = sampler.indexed_sampler_offset_bytes;
            const u64 bytes = fetched ? (source.stride == 0
                                             ? static_cast<u64>(source.num_records)
                                             : static_cast<u64>(source.stride) * source.num_records)
                                      : 0u;
            const u32 represented_entries =
                fetched && source.Valid()
                    ? (record_stride ? Shader::Optimization::CompleteGuestTsharpRecordEntries(
                                           static_cast<u32>(source.stride),
                                           static_cast<u32>(source.num_records), record_stride,
                                           image_offset, Shader::MaxGuestIndexedImageEntries)
                                     : Shader::Optimization::CompleteGuestTsharpTableEntries(
                                           static_cast<u32>(source.stride),
                                           static_cast<u32>(source.num_records),
                                           Shader::MaxGuestIndexedImageEntries))
                    : 0u;
            const u64 record_bytes = record_stride ? record_stride : sizeof(AmdGpu::Image);
            const bool table_layout_valid =
                record_stride ? image_offset <= record_stride &&
                                    sizeof(AmdGpu::Image) <= record_stride - image_offset &&
                                    sampler_offset <= record_stride &&
                                    sizeof(AmdGpu::Sampler) <= record_stride - sampler_offset
                              : image_offset == 0;
            const bool table_available =
                fetched && source.Valid() && source.base_address != 0 && table_layout_valid;
            // A packed strided material record may contain BOTH T# and S#.
            // Make GPU-written records visible to host before reading either.
            // Uniform S# without a per-record sampler stays unchanged.
            const u32 sampler_entries = std::min(count - 1u, represented_entries);
            const auto image_span = Shader::Optimization::GuestDescriptorPrefixBytes(
                sampler_entries, record_bytes, image_offset, sizeof(AmdGpu::Image));
            const auto sampler_span =
                record_stride
                    ? Shader::Optimization::GuestDescriptorPrefixBytes(
                          sampler_entries, record_bytes, sampler_offset, sizeof(AmdGpu::Sampler))
                    : std::optional<u64>{0};
            if (table_available && sampler_entries != 0 && image_span && sampler_span) {
                const u64 prefix = std::max(*image_span, *sampler_span);
                if (prefix <= bytes && prefix <= std::numeric_limits<u32>::max() &&
                    source.base_address <= std::numeric_limits<u64>::max() - prefix &&
                    memory->IsValidMapping(source.base_address, prefix) &&
                    buffer_cache.IsRegionGpuModified(source.base_address, prefix)) {
                    buffer_cache.ReadMemory(source.base_address, prefix, false, false, true);
                    static std::atomic<u32> coherence_reports{0};
                    if (coherence_reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
                        LOG_INFO(Render_Vulkan,
                                 "GpuDescriptorTableCpuCoherence: kind=sampler stage={:#x} "
                                 "addr={:#x} bytes={} entries={} stride={}",
                                 stage.pgm_hash, static_cast<u64>(source.base_address), prefix,
                                 sampler_entries, record_stride);
                    }
                }
            }

            AmdGpu::Sampler uniform_sampler{};
            bool uniform_sampler_valid = false;
            if (record_stride == 0u) {
                uniform_sampler = sampler.GetSharp(stage);
                uniform_sampler_valid =
                    uniform_sampler.Valid() &&
                    (uniform_sampler.border_color_type.Value() != AmdGpu::BorderColor::Custom ||
                     liverpool->regs.ta_bc_base.Address() != 0);
            }

            const u32 first_sampler = static_cast<u32>(image_infos.size());
            u32 valid_indexed_samplers = 0;
            for (u32 i = 0; i < count; ++i) {
                AmdGpu::Sampler ssharp = uniform_sampler;
                bool sampler_valid = record_stride == 0u ? uniform_sampler_valid : false;
                if (table_available && i + 1u < count && i < represented_entries &&
                    u64(i) * record_bytes <= bytes) {
                    const u64 record_offset_bytes = u64(i) * record_bytes;
                    const u64 record_address = source.base_address + record_offset_bytes;
                    if (record_stride != 0u) {
                        const u64 sampler_address = record_address + sampler_offset;
                        ssharp = AmdGpu::Sampler{};
                        sampler_valid = false;
                        if (sampler_offset <= bytes - record_offset_bytes &&
                            sizeof(AmdGpu::Sampler) <=
                                bytes - record_offset_bytes - sampler_offset &&
                            sampler_address >= record_address &&
                            sampler_address <=
                                std::numeric_limits<u64>::max() - (sizeof(AmdGpu::Sampler) - 1) &&
                            memory->IsValidMapping(sampler_address, sizeof(AmdGpu::Sampler))) {
                            std::memcpy(&ssharp, reinterpret_cast<const void*>(sampler_address),
                                        sizeof(AmdGpu::Sampler));
                            sampler_valid =
                                ssharp.Valid() &&
                                (ssharp.border_color_type.Value() != AmdGpu::BorderColor::Custom ||
                                 liverpool->regs.ta_bc_base.Address() != 0);
                        }
                    }
                } else {
                    // Missing and out-of-range guest entries remain null slots.
                    sampler_valid = false;
                }
                if (sampler_valid) {
                    ASSERT_MSG(record_stride == 0u ||
                                   (!ssharp.force_unnormalized && !ssharp.force_degamma),
                               "Indexed sampler requires per-entry coordinate or gamma conversion");
                    ++valid_indexed_samplers;
                }
                // VK_DESCRIPTOR_TYPE_SAMPLER cannot bind VK_NULL_HANDLE.
                // Use actual valid guest sampler data when present. Missing
                // guest table slots use the renderer's real cached default
                // sampler, as in the earlier generic native implementation.
                const AmdGpu::Sampler resolved_guest_sampler =
                    sampler_valid ? ssharp : AmdGpu::Sampler{};
                const vk::Sampler vk_sampler =
                    texture_cache.GetSampler(resolved_guest_sampler, liverpool->regs.ta_bc_base,
                                             sampler.is_depth, sampler.is_integer);
                ASSERT_MSG(static_cast<VkSampler>(vk_sampler) != VK_NULL_HANDLE,
                           "Vulkan sampler descriptor requires a valid VkSampler");
                image_infos.emplace_back(vk_sampler, VK_NULL_HANDLE, vk::ImageLayout::eGeneral);
            }
            if (valid_indexed_samplers != count - 1 || represented_entries != count - 1) {
                static std::atomic<u32> reports{0};
                if (reports.fetch_add(1, std::memory_order_relaxed) < 24) {
                    LOG_WARNING(Render_Vulkan,
                                "Indexed sampler table: shader={:#x} slots={} available={} "
                                "valid={} bytes={} record_stride={}",
                                stage.pgm_hash, count - 1, represented_entries,
                                valid_indexed_samplers, bytes, record_stride);
                }
            }
            auto& write = set_writes.emplace_back();
            write.dstSet = VK_NULL_HANDLE;
            write.dstBinding = binding.unified;
            write.dstArrayElement = 0;
            write.descriptorCount = count;
            write.descriptorType = vk::DescriptorType::eSampler;
            write.pImageInfo = &image_infos[first_sampler];
            binding.unified += count;
            continue;
        }
        auto ssharp = sampler.GetSharp(stage);
        if (!ssharp.Valid() || (ssharp.border_color_type.Value() == AmdGpu::BorderColor::Custom &&
                                liverpool->regs.ta_bc_base.Address() == 0)) {
            LOG_WARNING(Render_Vulkan,
                        "Rejecting invalid S# max_aniso={}, filter_mode={}, mip_filter={}, "
                        "border_color_type={}, border_color_base={:#x}",
                        static_cast<u32>(ssharp.max_aniso.Value()),
                        static_cast<u32>(ssharp.filter_mode.Value()),
                        static_cast<u32>(ssharp.mip_filter.Value()),
                        static_cast<u32>(ssharp.border_color_type.Value()),
                        liverpool->regs.ta_bc_base.Address());
            ssharp = AmdGpu::Sampler{};
        }
        const auto vk_sampler = texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base,
                                                         sampler.is_depth, sampler.is_integer);
        image_infos.emplace_back(vk_sampler, VK_NULL_HANDLE, vk::ImageLayout::eGeneral);
        auto& set_write = set_writes.emplace_back();
        set_write.dstSet = VK_NULL_HANDLE;
        set_write.dstBinding = binding.unified++;
        set_write.dstArrayElement = 0;
        set_write.descriptorCount = 1;
        set_write.descriptorType = vk::DescriptorType::eSampler;
        set_write.pImageInfo = &image_infos.back();
    }
}

RenderState Rasterizer::BeginRendering(const GraphicsPipeline* pipeline) {
    attachment_feedback_loop = false;
    const auto& regs = liverpool->regs;
    const auto& key = pipeline->GetGraphicsKey();
    RenderState state;
    state.width = instance.GetMaxFramebufferWidth();
    state.height = instance.GetMaxFramebufferHeight();
    state.num_layers = std::numeric_limits<u16>::max();
    state.num_color_attachments = std::bit_width(key.mrt_mask);
    bool has_attachment = false;
    for (auto cb = 0u; cb < state.num_color_attachments; ++cb) {
        auto& [image_id, desc] = cb_descs[cb];
        if (!image_id) {
            state.color_attachments[cb] = {};
            continue;
        }
        has_attachment = true;
        auto* image = &texture_cache.GetImage(image_id);
        if (image->binding.needs_rebind) {
            image_id = bound_images.emplace_back(texture_cache.FindImage(desc));
            image = &texture_cache.GetImage(image_id);
        }
        texture_cache.UpdateImage(image_id);
        runtime.SetBackingSamples(image, key.color_samples[cb]);
        const auto& image_view = texture_cache.FindRenderTarget(image_id, desc);
        const auto slice = image_view.info.range.base.layer;
        const auto mip = image_view.info.range.base.level;

        const auto& col_buf = regs.color_buffers[cb];
        const bool is_clear = texture_cache.IsMetaCleared(col_buf.CmaskAddress(), slice);
        texture_cache.TouchMeta(col_buf.CmaskAddress(), slice, false);

        if (image->binding.is_bound) {
            ASSERT_MSG(!image->binding.force_general,
                       "Having image both as storage and render target is unsupported");
            runtime.FlushBarriers();
            needs_barrier |=
                runtime.Transit(image,
                                instance.IsAttachmentFeedbackLoopLayoutSupported()
                                    ? vk::ImageLayout::eAttachmentFeedbackLoopOptimalEXT
                                    : vk::ImageLayout::eGeneral,
                                vk::PipelineStageFlagBits2::eColorAttachmentOutput,
                                vk::AccessFlagBits2::eColorAttachmentWrite |
                                    vk::AccessFlagBits2::eColorAttachmentRead);
            attachment_feedback_loop = true;
        } else {
            needs_barrier |= runtime.Transit(image, vk::ImageLayout::eColorAttachmentOptimal,
                                             vk::PipelineStageFlagBits2::eColorAttachmentOutput,
                                             vk::AccessFlagBits2::eColorAttachmentWrite |
                                                 vk::AccessFlagBits2::eColorAttachmentRead,
                                             desc.view_info.range);
        }

        state.width = std::min<u32>(state.width, std::max(image->info.size.width >> mip, 1u));
        state.height = std::min<u32>(state.height, std::max(image->info.size.height >> mip, 1u));
        state.num_layers = std::min<u32>(state.num_layers, image_view.info.range.extent.layers);

        const auto clear_value =
            is_clear ? LiverpoolToVK::ColorBufferClearValue(col_buf) : vk::ClearValue{};
        auto& attachment = state.color_attachments[cb];
        attachment.image_view = *image_view.image_view;
        attachment.image_layout = image->backing->state.layout;
        attachment.clear_value = clear_value.color.uint32;
        attachment.is_clear = is_clear;

        image->usage.render_target = 1u;
    }
    for (u32 cb = state.num_color_attachments; cb < state.color_attachments.size(); ++cb) {
        state.color_attachments[cb] = {};
    }

    if (auto image_id = db_desc.image_id; image_id) {
        has_attachment = true;
        auto& desc = db_desc.desc;
        const auto htile_address = regs.depth_htile_data_base.GetAddress();
        const auto& image_view = texture_cache.FindDepthTarget(image_id, desc);
        auto& image = texture_cache.GetImage(image_id);

        const auto slice = image_view.info.range.base.layer;
        const bool is_depth_clear =
            (regs.depth_render_control.depth_clear_enable && regs.depth_control.depth_enable &&
             regs.depth_control.depth_write_enable) ||
            texture_cache.IsMetaCleared(htile_address, slice);
        const bool is_stencil_clear = regs.depth_render_control.stencil_clear_enable;
        texture_cache.TouchMeta(htile_address, slice, false);
        ASSERT(desc.view_info.range.extent.levels == 1 && !image.binding.needs_rebind);

        const bool has_stencil = image.info.props.has_stencil;
        // Stencil writes can be enabled while depth writes are off.
        const bool stencil_write =
            has_stencil && regs.depth_control.stencil_enable && !desc.view_info.is_storage;
        const auto new_layout = desc.view_info.is_storage
                                    ? has_stencil ? vk::ImageLayout::eDepthStencilAttachmentOptimal
                                                  : vk::ImageLayout::eDepthAttachmentOptimal
                                : stencil_write
                                    ? vk::ImageLayout::eDepthReadOnlyStencilAttachmentOptimal
                                : has_stencil ? vk::ImageLayout::eDepthStencilReadOnlyOptimal
                                              : vk::ImageLayout::eDepthReadOnlyOptimal;
        needs_barrier |= runtime.Transit(&image, new_layout,
                                         vk::PipelineStageFlagBits2::eEarlyFragmentTests |
                                             vk::PipelineStageFlagBits2::eLateFragmentTests,
                                         vk::AccessFlagBits2::eDepthStencilAttachmentWrite |
                                             vk::AccessFlagBits2::eDepthStencilAttachmentRead,
                                         desc.view_info.range);

        state.width = std::min<u32>(state.width, image.info.size.width);
        state.height = std::min<u32>(state.height, image.info.size.height);
        state.num_layers = std::min<u32>(state.num_layers, image_view.info.range.extent.layers);

        auto& attachment = state.depth_stencil_attachment;
        attachment.image_view = *image_view.image_view;
        attachment.image_layout = image.backing->state.layout;
        attachment.clear_value = {};
        attachment.is_clear = 0;

        if (regs.depth_buffer.DepthValid()) {
            attachment.clear_value[0] = is_depth_clear ? std::bit_cast<u32>(regs.depth_clear) : 0u;
            attachment.has_depth = true;
            attachment.depth_clear = is_depth_clear;
        }
        if (regs.depth_buffer.StencilValid()) {
            attachment.clear_value[1] = is_stencil_clear ? regs.stencil_clear : 0u;
            attachment.has_stencil = true;
            attachment.stencil_clear = is_stencil_clear;
        }

        image.usage.depth_target = true;
    } else {
        state.depth_stencil_attachment = {};
    }

    // Without attachments the area stays at the max framebuffer size. Draws cannot pass the
    // scissors, so bound it by them.
    if (!has_attachment) {
        state.num_layers = 1;
        const AmdGpu::Scissor scissor = regs.GetCombinedScissor();
        if (scissor.bottom_right_x > 0 && scissor.bottom_right_y > 0) {
            state.width = std::min<u32>(state.width, scissor.bottom_right_x);
            state.height = std::min<u32>(state.height, scissor.bottom_right_y);
        }
    }

    return state;
}

void Rasterizer::Resolve() {
    const auto& mrt0_hint = liverpool->last_cb_extent[0];
    const auto& mrt1_hint = liverpool->last_cb_extent[1];
    VideoCore::TextureCache::ImageDesc mrt0_desc{liverpool->regs.color_buffers[0], mrt0_hint};
    VideoCore::TextureCache::ImageDesc mrt1_desc{liverpool->regs.color_buffers[1], mrt1_hint};
    auto& mrt0_image = texture_cache.GetImage(texture_cache.FindImage(mrt0_desc, true));
    const auto mrt1_id = texture_cache.FindImage(mrt1_desc, true);
    auto& mrt1_image = texture_cache.GetImage(mrt1_id);

    ScopeMarkerBegin(fmt::format("Resolve:MRT0={:#x}:MRT1={:#x}",
                                 liverpool->regs.color_buffers[0].Address(),
                                 liverpool->regs.color_buffers[1].Address()));
    runtime.ResolveImage(&mrt0_image, &mrt1_image, mrt0_desc.view_info.range,
                         mrt1_desc.view_info.range);
    texture_cache.TrackGpuImage(mrt1_id);
    ScopeMarkerEnd();
}

void Rasterizer::DepthStencilCopy(bool is_depth, bool is_stencil) {
    auto& regs = liverpool->regs;

    auto read_desc = VideoCore::TextureCache::ImageDesc(
        regs.depth_buffer, regs.depth_view, regs.depth_control,
        regs.depth_htile_data_base.GetAddress(), liverpool->last_db_extent, false);
    auto write_desc = VideoCore::TextureCache::ImageDesc(
        regs.depth_buffer, regs.depth_view, regs.depth_control,
        regs.depth_htile_data_base.GetAddress(), liverpool->last_db_extent, true);

    auto& read_image = texture_cache.GetImage(texture_cache.FindImage(read_desc));
    const auto write_id = texture_cache.FindImage(write_desc);
    auto& write_image = texture_cache.GetImage(write_id);
    // The copy destination is never bound as a target, so its stencil plane is linked here.
    texture_cache.AssociateStencil(write_id, write_desc.info);

    VideoCore::SubresourceRange sub_range;
    sub_range.base.layer = liverpool->regs.depth_view.slice_start;
    sub_range.extent.layers = liverpool->regs.depth_view.NumSlices() - sub_range.base.layer;

    ScopeMarkerBegin(fmt::format(
        "DepthStencilCopy:DR={:#x}:SR={:#x}:DW={:#x}:SW={:#x}", regs.depth_buffer.DepthAddress(),
        regs.depth_buffer.StencilAddress(), regs.depth_buffer.DepthWriteAddress(),
        regs.depth_buffer.StencilWriteAddress()));

    runtime.CopyDepthStencil(&read_image, &write_image, sub_range);
    texture_cache.TrackGpuImage(write_id);

    ScopeMarkerEnd();
}

void Rasterizer::FillBuffer(VAddr address, u32 num_bytes, u32 value, bool is_gds) {
    ASSERT_MSG(address % 4 == 0 && num_bytes % 4 == 0,
               "FillBuffer address and size must be a multiple of 4 bytes");
    if (!is_gds) {
        texture_cache.ClearMeta(address);
        if (!buffer_cache.IsRegionGpuModified(address, num_bytes)) {
            u32* buffer = std::bit_cast<u32*>(address);
            std::fill(buffer, buffer + (num_bytes / sizeof(u32)), value);
            return;
        }
    }
    const auto [buffer, offset] = [&] -> std::pair<const VideoCore::Buffer*, u64> {
        if (is_gds) {
            return {buffer_cache.GetGdsBuffer(), address};
        }
        return buffer_cache.ObtainBuffer(address, num_bytes,
                                         VideoCore::ObtainBufferFlags::IsWritten);
    }();
    runtime.FillBuffer(buffer, offset, num_bytes, value);
}

void Rasterizer::CopyBuffer(VAddr dst, VAddr src, u32 num_bytes, bool dst_gds, bool src_gds) {
    if (!dst_gds && !buffer_cache.IsRegionGpuModified(dst, num_bytes)) {
        if (!src_gds && !buffer_cache.IsRegionGpuModified(src, num_bytes) &&
            !texture_cache.FindImageFromRange(src, num_bytes)) {
            // Both buffers were not transferred to GPU yet. Can safely copy in host memory.
            std::memcpy(std::bit_cast<void*>(dst), std::bit_cast<void*>(src), num_bytes);
            return;
        }
    }
    const auto* gds_buffer = buffer_cache.GetGdsBuffer();
    const auto [src_buffer, src_offset] = [&] -> std::pair<const VideoCore::Buffer*, u64> {
        if (src_gds) {
            return {gds_buffer, src};
        }
        return buffer_cache.ObtainBuffer(src, num_bytes,
                                         VideoCore::ObtainBufferFlags::IsTexelBuffer);
    }();
    const auto [dst_buffer, dst_offset] = [&] -> std::pair<const VideoCore::Buffer*, u64> {
        if (dst_gds) {
            return {gds_buffer, dst};
        }
        return buffer_cache.ObtainBuffer(dst, num_bytes,
                                         VideoCore::ObtainBufferFlags::IsWritten |
                                             VideoCore::ObtainBufferFlags::IsTexelBuffer);
    }();
    const vk::BufferCopy copy = {
        .srcOffset = src_offset,
        .dstOffset = dst_offset,
        .size = num_bytes,
    };
    runtime.CopyBuffer(src_buffer, dst_buffer, std::span{&copy, 1});
}

u32 Rasterizer::ReadDataFromGds(u32 gds_offset) {
    auto* gds_buf = buffer_cache.GetGdsBuffer();
    u32 value;
    std::memcpy(&value, gds_buf->mapped_data.data() + gds_offset, sizeof(u32));
    return value;
}

bool Rasterizer::InvalidateMemory(VAddr addr, u64 size, bool assume_locks) {
    if (!IsMapped(addr, size)) {
        // Not GPU mapped memory, can skip invalidation logic entirely.
        return false;
    }
    const auto invalidate = [this, addr, size] {
        texture_cache.ReadMemory(addr, size);
        buffer_cache.InvalidateMemory(addr, size, true);
        texture_cache.InvalidateMemory(addr, size);
    };
    if (assume_locks) {
        invalidate();
    } else {
        liverpool->SendCommand<true>(invalidate);
    }
    return true;
}

bool Rasterizer::ReadMemory(VAddr addr, u64 size, bool assume_locks) {
    if (!IsMapped(addr, size)) {
        // Not GPU mapped memory, can skip invalidation logic entirely.
        return false;
    }
    const auto read = [this, addr, size] {
        texture_cache.ReadMemory(addr, size);
        if (buffer_cache.IsRegionGpuModified(addr, size)) {
            buffer_cache.ReadMemory(addr, size, false, false, true);
        }
    };
    if (assume_locks) {
        read();
    } else {
        liverpool->SendCommand<true>(read);
    }
    return true;
}

bool Rasterizer::IsMapped(VAddr addr, u64 size) {
    if (size == 0) {
        // There is no memory, so not mapped.
        return false;
    }
    if (static_cast<u64>(addr) > std::numeric_limits<u64>::max() - size) {
        // Memory range wrapped the address space, cannot be mapped.
        return false;
    }
    const auto range = decltype(mapped_ranges)::interval_type::right_open(addr, addr + size);

    Common::RecursiveSharedLock lock{mapped_ranges_mutex};
    return boost::icl::contains(mapped_ranges, range);
}

void Rasterizer::MapMemory(VAddr addr, u64 size) {
    {
        std::scoped_lock lock{mapped_ranges_mutex};
        mapped_ranges += decltype(mapped_ranges)::interval_type::right_open(addr, addr + size);
    }
}

void Rasterizer::RegisterMemory(VAddr addr, u64 size) {
    page_manager.OnGpuMap(addr, size);
}

void Rasterizer::UnmapMemory(VAddr addr, u64 size) {
    const auto unmap = [this, addr, size] {
        texture_cache.ReadMemory(addr, size);
        buffer_cache.InvalidateMemory(addr, size, true);
        texture_cache.UnmapMemory(addr, size);
    };
    if (std::this_thread::get_id() == GetGpuCommandProcessorThread()) {
        unmap();
    } else {
        liverpool->SendCommand<true>(unmap);
    }
    {
        std::scoped_lock lock{mapped_ranges_mutex};
        mapped_ranges -= decltype(mapped_ranges)::interval_type::right_open(addr, addr + size);
    }
}

void Rasterizer::UpdateDynamicState(const GraphicsPipeline* pipeline, const bool is_indexed) const {
    UpdateViewportScissorState();
    UpdateDepthStencilState();
    UpdatePrimitiveState(is_indexed);
    UpdateRasterizationState();
    UpdateColorBlendingState(pipeline);

    auto& dynamic_state = scheduler.GetDynamicState();
    dynamic_state.Commit(instance, scheduler.CommandBuffer());
}

void Rasterizer::UpdateViewportScissorState() const {
    const auto& regs = liverpool->regs;

    const AmdGpu::Scissor scsr = regs.GetCombinedScissor();

    boost::container::static_vector<vk::Viewport, AmdGpu::NUM_VIEWPORTS> viewports;
    boost::container::static_vector<vk::Rect2D, AmdGpu::NUM_VIEWPORTS> scissors;

    if (regs.polygon_control.enable_window_offset &&
        (regs.window_offset.window_x_offset != 0 || regs.window_offset.window_y_offset != 0)) {
        LOG_ERROR(Render_Vulkan,
                  "PA_SU_SC_MODE_CNTL.VTX_WINDOW_OFFSET_ENABLE support is not yet implemented.");
    }

    const auto& vp_ctl = regs.viewport_control;
    for (u32 i = 0; i < AmdGpu::NUM_VIEWPORTS; i++) {
        const auto& vp = regs.viewports[i];
        const auto& vp_d = regs.viewport_depths[i];
        if (vp.xscale == 0) {
            continue;
        }

        const auto zoffset = vp_ctl.zoffset_enable ? vp.zoffset : 0.f;
        const auto zscale = vp_ctl.zscale_enable ? vp.zscale : 1.f;

        vk::Viewport viewport{};

        // https://gitlab.freedesktop.org/mesa/mesa/-/blob/209a0ed/src/amd/vulkan/radv_pipeline_graphics.c#L688-689
        // https://gitlab.freedesktop.org/mesa/mesa/-/blob/209a0ed/src/amd/vulkan/radv_cmd_buffer.c#L3103-3109
        // When the clip space is ranged [-1...1], the zoffset is centered.
        // By reversing the above viewport calculations, we get the following:
        if (regs.clipper_control.clip_space == AmdGpu::ClipSpace::MinusWToW) {
            viewport.minDepth = zoffset - zscale;
            viewport.maxDepth = zoffset + zscale;
        } else {
            viewport.minDepth = zoffset;
            viewport.maxDepth = zoffset + zscale;
        }

        if (!instance.IsDepthRangeUnrestrictedSupported()) {
            // Unrestricted depth range not supported by device. Restrict to valid range.
            viewport.minDepth = std::max(viewport.minDepth, 0.f);
            viewport.maxDepth = std::min(viewport.maxDepth, 1.f);
        }

        if (regs.IsClipDisabled()) {
            // In case if clipping is disabled we patch the shader to convert vertex position
            // from screen space coordinates to NDC by defining a render space as full hardware
            // window range [0..16383, 0..16383] and setting the viewport to its size.
            viewport.x = 0.f;
            viewport.y = 0.f;
            viewport.width = float(std::min<u32>(instance.GetMaxViewportWidth(), 16_KB));
            viewport.height = float(std::min<u32>(instance.GetMaxViewportHeight(), 16_KB));
        } else {
            const auto xoffset = vp_ctl.xoffset_enable ? vp.xoffset : 0.f;
            const auto xscale = vp_ctl.xscale_enable ? vp.xscale : 1.f;
            const auto yoffset = vp_ctl.yoffset_enable ? vp.yoffset : 0.f;
            const auto yscale = vp_ctl.yscale_enable ? vp.yscale : 1.f;

            viewport.x = xoffset - xscale;
            viewport.y = yoffset - yscale;
            viewport.width = xscale * 2.0f;
            viewport.height = yscale * 2.0f;
        }

        viewports.push_back(viewport);

        auto vp_scsr = scsr;
        if (regs.mode_control.vport_scissor_enable) {
            vp_scsr.top_left_x =
                std::max(vp_scsr.top_left_x, s16(regs.viewport_scissors[i].top_left_x));
            vp_scsr.top_left_y =
                std::max(vp_scsr.top_left_y, s16(regs.viewport_scissors[i].top_left_y));
            vp_scsr.bottom_right_x =
                std::min(vp_scsr.bottom_right_x, s16(regs.viewport_scissors[i].bottom_right_x));
            vp_scsr.bottom_right_y =
                std::min(vp_scsr.bottom_right_y, s16(regs.viewport_scissors[i].bottom_right_y));
        }
        scissors.push_back({
            .offset = {vp_scsr.top_left_x, vp_scsr.top_left_y},
            .extent = {vp_scsr.GetWidth(), vp_scsr.GetHeight()},
        });
    }

    if (viewports.empty()) {
        // Vulkan requires providing at least one viewport.
        constexpr vk::Viewport empty_viewport = {
            .x = -1.0f,
            .y = -1.0f,
            .width = 1.0f,
            .height = 1.0f,
            .minDepth = 0.0f,
            .maxDepth = 1.0f,
        };
        constexpr vk::Rect2D empty_scissor = {
            .offset = {0, 0},
            .extent = {1, 1},
        };
        viewports.push_back(empty_viewport);
        scissors.push_back(empty_scissor);
    }

    auto& dynamic_state = scheduler.GetDynamicState();
    dynamic_state.SetViewports(viewports);
    dynamic_state.SetScissors(scissors);
}

void Rasterizer::UpdateDepthStencilState() const {
    const auto& regs = liverpool->regs;
    auto& dynamic_state = scheduler.GetDynamicState();

    const auto depth_test_enabled =
        regs.depth_control.depth_enable && regs.depth_buffer.DepthValid();
    dynamic_state.SetDepthTestEnabled(depth_test_enabled);
    if (depth_test_enabled) {
        dynamic_state.SetDepthWriteEnabled(regs.depth_control.depth_write_enable &&
                                           !regs.depth_render_control.depth_clear_enable);
        dynamic_state.SetDepthCompareOp(LiverpoolToVK::CompareOp(regs.depth_control.depth_func));
    }

    const auto depth_bounds_test_enabled = regs.depth_control.depth_bounds_enable;
    dynamic_state.SetDepthBoundsTestEnabled(depth_bounds_test_enabled);
    if (depth_bounds_test_enabled) {
        dynamic_state.SetDepthBounds(regs.depth_bounds_min, regs.depth_bounds_max);
    }

    const auto depth_bias_enabled = regs.polygon_control.NeedsBias();
    dynamic_state.SetDepthBiasEnabled(depth_bias_enabled);
    if (depth_bias_enabled) {
        const bool front = regs.polygon_control.enable_polygon_offset_front;
        dynamic_state.SetDepthBias(
            front ? regs.poly_offset.front_offset : regs.poly_offset.back_offset,
            regs.poly_offset.depth_bias,
            (front ? regs.poly_offset.front_scale : regs.poly_offset.back_scale) / 16.f);
    }

    const auto stencil_test_enabled =
        regs.depth_control.stencil_enable && regs.depth_buffer.StencilValid();
    dynamic_state.SetStencilTestEnabled(stencil_test_enabled);
    if (stencil_test_enabled) {
        const StencilOps front_ops{
            .fail_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_fail_front),
            .pass_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_zpass_front),
            .depth_fail_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_zfail_front),
            .compare_op = LiverpoolToVK::CompareOp(regs.depth_control.stencil_ref_func),
        };
        const StencilOps back_ops = regs.depth_control.backface_enable ? StencilOps{
            .fail_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_fail_back),
            .pass_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_zpass_back),
            .depth_fail_op = LiverpoolToVK::StencilOp(regs.stencil_control.stencil_zfail_back),
            .compare_op = LiverpoolToVK::CompareOp(regs.depth_control.stencil_bf_func),
        } : front_ops;
        dynamic_state.SetStencilOps(front_ops, back_ops);

        const bool stencil_clear = regs.depth_render_control.stencil_clear_enable;
        const auto front = regs.stencil_ref_front;
        const auto back =
            regs.depth_control.backface_enable ? regs.stencil_ref_back : regs.stencil_ref_front;
        // GCN REPLACE_OP writes DB_STENCILREFMASK.STENCILOPVAL, so a face whose stencil ops
        // include ReplaceOp takes its Vulkan reference from op_val.
        const auto& sc = regs.stencil_control;
        const auto uses_op_val = [](AmdGpu::StencilFunc fail, AmdGpu::StencilFunc zpass,
                                    AmdGpu::StencilFunc zfail) {
            return fail == AmdGpu::StencilFunc::ReplaceOp ||
                   zpass == AmdGpu::StencilFunc::ReplaceOp ||
                   zfail == AmdGpu::StencilFunc::ReplaceOp;
        };
        const bool front_op =
            uses_op_val(sc.stencil_fail_front, sc.stencil_zpass_front, sc.stencil_zfail_front);
        const bool back_op =
            regs.depth_control.backface_enable
                ? uses_op_val(sc.stencil_fail_back, sc.stencil_zpass_back, sc.stencil_zfail_back)
                : front_op;
        // PS4 stencil compares use test_val and REPLACE writes use op_val.
        // Vulkan has only one reference per face. These are simultaneously
        // representable when the compare and write masks have no conflicting
        // overlapping bits; preserve both guest operations in that case.
        const auto stencil_reference = [&](const AmdGpu::StencilRefMask& ref,
                                           AmdGpu::CompareFunc compare,
                                           bool uses_replace) -> u32 {
            const u32 test_val = ref.stencil_test_val;
            const u32 op_val = ref.stencil_op_val;
            if (!uses_replace || stencil_clear) {
                return test_val;
            }
            if (compare == AmdGpu::CompareFunc::Always ||
                compare == AmdGpu::CompareFunc::Never) {
                return op_val;
            }
            const u32 compare_mask = ref.stencil_mask;
            const u32 write_mask = ref.stencil_write_mask;
            const u32 overlap_conflict = (test_val ^ op_val) & compare_mask & write_mask;
            if (overlap_conflict == 0) {
                const u32 reference =
                    (test_val & compare_mask) | (op_val & write_mask & ~compare_mask);
                static std::atomic<u32> exact_reference_reports{0};
                if (test_val != op_val &&
                    exact_reference_reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
                    LOG_INFO(Render_Vulkan,
                             "StencilReferenceExactMerged: test={:#x} op={:#x} "
                             "compare_mask={:#x} write_mask={:#x} reference={:#x}",
                             test_val, op_val, compare_mask, write_mask, reference);
                }
                return reference;
            }
            static std::atomic<u32> unrepresentable_reference_reports{0};
            if (unrepresentable_reference_reports.fetch_add(1, std::memory_order_relaxed) < 24u) {
                LOG_WARNING(Render_Vulkan,
                            "StencilReferenceUnrepresentable: test={:#x} op={:#x} "
                            "compare_mask={:#x} write_mask={:#x} conflicting_bits={:#x}",
                            test_val, op_val, compare_mask, write_mask, overlap_conflict);
            }
            // Genuinely contradictory guest compare and replace bits cannot
            // be expressed by a single Vulkan reference. Preserve the prior
            // REPLACE reference rather than falsifying guest stencil tests.
            return op_val;
        };
        dynamic_state.SetStencilReferences(
            stencil_reference(front, regs.depth_control.stencil_ref_func, front_op),
            stencil_reference(back,
                              regs.depth_control.backface_enable
                                  ? regs.depth_control.stencil_bf_func
                                  : regs.depth_control.stencil_ref_func,
                              back_op));
        dynamic_state.SetStencilWriteMasks(!stencil_clear ? front.stencil_write_mask : 0U,
                                           !stencil_clear ? back.stencil_write_mask : 0U);
        dynamic_state.SetStencilCompareMasks(front.stencil_mask, back.stencil_mask);
    }
}

void Rasterizer::UpdatePrimitiveState(const bool is_indexed) const {
    const auto& regs = liverpool->regs;
    auto& dynamic_state = scheduler.GetDynamicState();

    const auto is_list_topology = [](const AmdGpu::PrimitiveType type) {
        const auto topology = LiverpoolToVK::PrimitiveType(type);
        return topology == vk::PrimitiveTopology::ePointList ||
               topology == vk::PrimitiveTopology::eLineList ||
               topology == vk::PrimitiveTopology::eTriangleList ||
               topology == vk::PrimitiveTopology::eLineListWithAdjacency ||
               topology == vk::PrimitiveTopology::eTriangleListWithAdjacency;
    };
    const auto is_patch_list_topology = [](const AmdGpu::PrimitiveType type) {
        // Quad and rect lists are emulated using tessellation.
        return type == AmdGpu::PrimitiveType::PatchPrimitive ||
               type == AmdGpu::PrimitiveType::QuadList || type == AmdGpu::PrimitiveType::RectList;
    };

    const auto prim_restart =
        (regs.enable_primitive_restart & 1) != 0 &&
        (instance.IsListRestartSupported() || !is_list_topology(regs.primitive_type)) &&
        (instance.IsPatchListRestartSupported() || !is_patch_list_topology(regs.primitive_type));
    ASSERT_MSG(!is_indexed || !prim_restart || regs.primitive_restart_index == 0xFFFF ||
                   regs.primitive_restart_index == 0xFFFFFFFF,
               "Primitive restart index other than -1 is not supported yet");

    const auto cull_mode = LiverpoolToVK::IsPrimitiveCulled(regs.primitive_type)
                               ? LiverpoolToVK::CullMode(regs.polygon_control.CullingMode())
                               : vk::CullModeFlagBits::eNone;
    const auto front_face = LiverpoolToVK::FrontFace(regs.polygon_control.front_face);

    dynamic_state.SetPrimitiveRestartEnabled(prim_restart);
    dynamic_state.SetRasterizerDiscardEnabled(regs.clipper_control.dx_rasterization_kill);
    dynamic_state.SetCullMode(cull_mode);
    dynamic_state.SetFrontFace(front_face);
}

void Rasterizer::UpdateRasterizationState() const {
    const auto& regs = liverpool->regs;
    auto& dynamic_state = scheduler.GetDynamicState();
    dynamic_state.SetLineWidth(regs.line_control.Width());
}

void Rasterizer::UpdateColorBlendingState(const GraphicsPipeline* pipeline) const {
    const auto& regs = liverpool->regs;
    auto& dynamic_state = scheduler.GetDynamicState();
    dynamic_state.SetBlendConstants(regs.blend_constants);
    dynamic_state.SetColorWriteMasks(pipeline->GetGraphicsKey().write_masks);
    dynamic_state.SetAttachmentFeedbackLoopEnabled(attachment_feedback_loop);
}

void Rasterizer::ScopeMarkerBegin(const std::string_view& str, bool from_guest) {
    if ((from_guest && !EmulatorSettings.IsVkGuestMarkersEnabled()) ||
        (!from_guest && !EmulatorSettings.IsVkHostMarkersEnabled())) {
        return;
    }
    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.beginDebugUtilsLabelEXT(vk::DebugUtilsLabelEXT{
        .pLabelName = str.data(),
    });
}

void Rasterizer::ScopeMarkerEnd(bool from_guest) {
    if ((from_guest && !EmulatorSettings.IsVkGuestMarkersEnabled()) ||
        (!from_guest && !EmulatorSettings.IsVkHostMarkersEnabled())) {
        return;
    }
    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.endDebugUtilsLabelEXT();
}

void Rasterizer::ScopedMarkerInsert(const std::string_view& str, bool from_guest) {
    if ((from_guest && !EmulatorSettings.IsVkGuestMarkersEnabled()) ||
        (!from_guest && !EmulatorSettings.IsVkHostMarkersEnabled())) {
        return;
    }
    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.insertDebugUtilsLabelEXT(vk::DebugUtilsLabelEXT{
        .pLabelName = str.data(),
    });
}

void Rasterizer::ScopedMarkerInsertColor(const std::string_view& str, const u32 color,
                                         bool from_guest) {
    if ((from_guest && !EmulatorSettings.IsVkGuestMarkersEnabled()) ||
        (!from_guest && !EmulatorSettings.IsVkHostMarkersEnabled())) {
        return;
    }
    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.insertDebugUtilsLabelEXT(vk::DebugUtilsLabelEXT{
        .pLabelName = str.data(),
        .color = std::array<f32, 4>(
            {(f32)((color >> 16) & 0xff) / 255.0f, (f32)((color >> 8) & 0xff) / 255.0f,
             (f32)(color & 0xff) / 255.0f, (f32)((color >> 24) & 0xff) / 255.0f})});
}

std::thread::id Rasterizer::GetGpuCommandProcessorThread() {
    return liverpool->GetGpuCommandProcessorThread();
}

#ifdef __linux__
u32 Rasterizer::GetGpuCommandProcessorThreadId() {
    return liverpool->GetGpuCommandProcessorThreadId();
}
#endif

} // namespace Vulkan
