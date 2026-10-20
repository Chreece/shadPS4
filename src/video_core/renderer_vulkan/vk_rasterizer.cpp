// SPDX-FileCopyrightText: Copyright 2024-2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <bit>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <type_traits>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#include "common/debug.h"
#include "common/path_util.h"
#include "core/debug_state.h"
#include "core/emulator_settings.h"
#include "core/memory.h"
#include "shader_recompiler/guest_tsharp_array.h"
#include "shader_recompiler/indexed_image_feedback.h"
#include "shader_recompiler/vertex_output_feedback.h"
#include "shader_recompiler/resource.h"
#include "shader_recompiler/runtime_info.h"
#include "shader_recompiler/targeted_shader_dump.h"
#include "video_core/amdgpu/liverpool.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/buffer_cache/buffer_cache.h"
#include "video_core/renderer_vulkan/liverpool_to_vk.h"
#include "video_core/renderer_vulkan/resource_audit_hash_filter.h"
#include "video_core/renderer_vulkan/vk_buffer_hazard_rules.h"
#include "video_core/renderer_vulkan/vk_frame_probe_images.h"
#include "video_core/renderer_vulkan/vk_got_dma_origin_probe.h"
#include "video_core/renderer_vulkan/vk_got_gds_compute_probe.h"
#include "video_core/renderer_vulkan/vk_got_indirect_gpu_readback.h"
#include "video_core/renderer_vulkan/vk_got_indirect_producer_trace.h"
#include "video_core/renderer_vulkan/vk_got_menu_pass_trace.h"
#include "video_core/renderer_vulkan/vk_got_scene_depth_ab.h"
#include "video_core/renderer_vulkan/vk_got_scene_gpu_queries.h"
#include "video_core/renderer_vulkan/vk_instance.h"
#include "video_core/renderer_vulkan/vk_pipeline_cache.h"
#include "video_core/renderer_vulkan/vk_rasterizer.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_shader_hle.h"
#include "video_core/texture_cache/image_view.h"
#include "video_core/texture_cache/texture_cache.h"

namespace Vulkan {

namespace {

template <typename T>
bool ReadIndexedDescriptor(Core::MemoryManager* memory, u64 address, bool indirect,
                           u32 pointee_offset, T& descriptor) {
    const auto readable = [&](u64 start, u64 size) {
        if (!memory->IsValidMapping(start, size))
            return false;
        while (size) {
            void* end{};
            u32 protection{};
            if (memory->QueryProtection(start, nullptr, &end, &protection) != 0 ||
                !(protection & u32(Core::MemoryProt::CpuRead)) || u64(end) <= start)
                return false;
            const u64 length = std::min(size, u64(end) - start);
            start += length;
            size -= length;
        }
        return true;
    };
    if (indirect) {
        if (!readable(address, sizeof(u64)))
            return false;
        u64 pointer{};
        std::memcpy(&pointer, reinterpret_cast<const void*>(address), sizeof(pointer));
        pointer &= 0x0000ffffffffffffull;
        if (pointer == 0)
            return false;
        address = pointer + pointee_offset;
    }
    if (!(indirect ? readable(address, sizeof(T)) : memory->IsValidMapping(address, sizeof(T))))
        return false;
    std::memcpy(&descriptor, reinterpret_cast<const void*>(address), sizeof(T));
    return true;
}

// Confirm that a shader with a watched writable descriptor actually reached
// Vulkan dispatch; this still does not prove the shader executed a store.
void TraceGotIndirectComputeDispatch(const Shader::Info& cs, bool indirect,
                                     u32 dim_x, u32 dim_y, u32 dim_z) {
    if (!Diagnostics::GotIndirectProducerTraceEnabled()) return;
    for (const auto& desc : cs.buffers) {
        if (desc.IsSpecial() || !desc.is_written) continue;
        const auto sharp = desc.GetSharp(cs);
        const u32 hits = Diagnostics::ObservedIndirectProducerHits(
            sharp.base_address, sharp.GetSize());
        if (!hits) continue;
        static thread_local u32 events{};
        const u32 observed = ++events;
        if (Diagnostics::ShouldReportIndirectProducer(observed)) {
            LOG_INFO(Render_Vulkan,
                     "GotIndirectProducer: path=COMPUTE_DISPATCH_SUBMITTED "
                     "shader={:#x} hits_mask={:#x} descriptor_base={:#x} "
                     "descriptor_bytes={} indirect={} dim_x={} dim_y={} dim_z={} "
                     "confirmed_store=NO observed={}",
                     cs.pgm_hash,hits,static_cast<u64>(sharp.base_address),
                     sharp.GetSize(),indirect,dim_x,dim_y,dim_z,observed);
        }
        break;
    }
}

struct ResourceAuditCounts {
    u64 buffer_binds{};
    u64 null_buffers{};
    u64 clamped_buffers{};
    u64 overflow_buffers{};
    u64 texture_calls{};
    u64 null_images{};
    u64 invalid_image_sharps{};
    u64 unmapped_images{};
    u64 valid_images{};
};

bool ResourceAuditEnabled() {
    static const bool enabled = [] {
        const char* const mode = std::getenv("SHADPS4_RESOURCE_AUDIT");
        return mode && mode[0] == '1' && mode[1] == '\0';
    }();
    return enabled;
}

// Opt-in generic audit of one or several shader hashes, parsed once.
// Invalid filters fail closed. No shader/resource behavior changes.
bool ResourceAuditForStage(const Shader::Info& stage) {
    if (!ResourceAuditEnabled()) {
        return false;
    }
    static const auto filter = VideoCore::Diagnostics::ParseShaderAuditHashFilter(
        std::getenv("SHADPS4_RESOURCE_AUDIT_HASH"));
    return filter.Matches(stage.pgm_hash);
}

ResourceAuditCounts& ResourceAuditFor(const Shader::Info& stage) {
    static thread_local std::unordered_map<u64, ResourceAuditCounts> by_shader;
    const u64 key = (static_cast<u64>(stage.pgm_hash) << 8) |
                    static_cast<u8>(stage.hw_stage);
    return by_shader[key];
}

void LogResourceAudit(const Shader::Info& stage, const ResourceAuditCounts& counts) {
    LOG_INFO(Render_Vulkan,
             "ResourceAuditRuntime: shader={:#x} stage={} buffer_binds={} null_buffers={} "
             "clamps={} overflow={} texture_calls={} valid_images={} null_images={} "
             "invalid_image_sharps={} unmapped_images={}",
             stage.pgm_hash, stage.hw_stage, counts.buffer_binds, counts.null_buffers,
             counts.clamped_buffers, counts.overflow_buffers, counts.texture_calls,
             counts.valid_images, counts.null_images, counts.invalid_image_sharps,
             counts.unmapped_images);
}

struct DrawAuditCounters {
    u64 attempted{};
    u64 filtered{};
    u64 missing_pipeline{};
    u64 binding_failed{};
    u64 submitted{};
    u64 indexed{};
    u64 indirect{};
    u64 color_draws{};
    u64 depth_draws{};
};

bool RenderAuditEnabled() {
    // Diagnostic only. This flag cannot change emulation, resource lifetimes,
    // descriptor values or shaders; no game titles or hashes are consulted.
    static const bool enabled = [] {
        const char* const mode = std::getenv("SHADPS4_RENDER_AUDIT");
        return mode && mode[0] == '1' && mode[1] == '\0';
    }();
    return enabled;
}

DrawAuditCounters& GetDrawAuditCounters() {
    // Draws use the GPU command-processing thread; keep counters thread-local.
    static thread_local DrawAuditCounters audit;
    return audit;
}

void LogDrawAudit(const AmdGpu::Regs& regs, const RenderState* state, bool is_indirect) {
    const auto& audit = GetDrawAuditCounters();
    u32 bound_colors = 0;
    u32 framebuffer_width = 0;
    u32 framebuffer_height = 0;
    bool bound_depth = false;
    if (state) {
        framebuffer_width = state->width;
        framebuffer_height = state->height;
        for (u32 i = 0; i < state->num_color_attachments; ++i) {
            bound_colors += state->color_attachments[i].image_view != vk::ImageView{};
        }
        bound_depth = state->depth_stencil_attachment.image_view != vk::ImageView{};
    }
    LOG_INFO(Render_Vulkan,
             "RenderAudit: attempted={} submitted={} filtered={} no_pipeline={} "
             "bind_failed={} indexed={} indirect={} color_draws={} depth_draws={} "
             "last_draw_indirect={} last_indices={} last_instances={} last_color_views={} "
             "last_has_depth={} last_depth_test={} last_fb={}x{}",
             audit.attempted, audit.submitted, audit.filtered, audit.missing_pipeline,
             audit.binding_failed, audit.indexed, audit.indirect, audit.color_draws,
             audit.depth_draws, is_indirect, regs.num_indices, regs.num_instances.NumInstances(),
             bound_colors, bound_depth,
             regs.depth_control.depth_enable && regs.depth_buffer.DepthValid(),
             framebuffer_width, framebuffer_height);
}

void CountDrawAttempt(const AmdGpu::Regs& regs, bool indirect) {
    if (!RenderAuditEnabled()) {
        return;
    }
    auto& audit = GetDrawAuditCounters();
    ++audit.attempted;
    if (audit.attempted == 1 || audit.attempted % 2048 == 0) {
        LogDrawAudit(regs, nullptr, indirect);
    }
}

void CountSubmittedDraw(const AmdGpu::Regs& regs, const RenderState& state, bool indexed,
                        bool indirect) {
    if (!RenderAuditEnabled()) {
        return;
    }
    auto& audit = GetDrawAuditCounters();
    ++audit.submitted;
    audit.indexed += indexed;
    audit.indirect += indirect;
    u32 bound_colors{};
    for (u32 i = 0; i < state.num_color_attachments; ++i) {
        bound_colors += state.color_attachments[i].image_view != vk::ImageView{};
    }
    audit.color_draws += bound_colors != 0;
    audit.depth_draws += state.depth_stencil_attachment.image_view != vk::ImageView{};
    if (audit.submitted == 1 || audit.submitted % 512 == 0) {
        LogDrawAudit(regs, &state, indirect);
    }
}

} // Anonymous namespace

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
      pipeline_cache{instance, scheduler, liverpool, buffer_cache.GetSparsePageShift()},
      host_markers_enabled{EmulatorSettings.IsVkHostMarkersEnabled()},
      guest_markers_enabled{EmulatorSettings.IsVkGuestMarkersEnabled()} {
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
    Diagnostics::TraceMenuFastClear(col_buf.Address(), clear_value.color.uint32[0]);
    runtime.ClearImage(&image, desc.view_info.range, clear_value);
    ScopeMarkerEnd();
}

void Rasterizer::Draw(bool is_indexed, u32 index_offset) {
    RENDERER_TRACE;

    CountDrawAttempt(liverpool->regs, false);
    scheduler.PopPendingOperations();

    if (!FilterDraw()) {
        if (RenderAuditEnabled()) {
            ++GetDrawAuditCounters().filtered;
        }
        return;
    }

    const auto& regs = liverpool->regs;
    const GraphicsPipeline* pipeline = pipeline_cache.GetGraphicsPipeline();
    if (!pipeline) {
        if (RenderAuditEnabled()) {
            ++GetDrawAuditCounters().missing_pipeline;
        }
        return;
    }

    PrepareRenderState(pipeline);
    if (!BindResources(pipeline)) {
        if (RenderAuditEnabled()) {
            ++GetDrawAuditCounters().binding_failed;
        }
        return;
    }
    const auto state = BeginRendering(pipeline);

    BindVertexBuffers(pipeline);
    if (is_indexed) {
        BindIndexBuffer(index_offset);
    }

    if (needs_barrier) {
        runtime.FlushBarriers();
    }

    pipeline->BindResources(set_writes, push_data);
    UpdateDynamicState(pipeline, is_indexed);
    const auto scene_gpu_query=Diagnostics::PrepareGotSceneGpuQuery(
        instance,scheduler,regs,*pipeline,is_indexed,false);
    scheduler.BeginRendering(state);

    const auto& vs_info = pipeline->GetStage(Shader::SwStage::Vertex);
    const auto& fetch_shader = pipeline->GetFetchShader();
    const auto [vertex_offset, instance_offset] = GetDrawOffsets(regs, vs_info, fetch_shader);

    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eGraphics, pipeline->Handle());
    Diagnostics::BeginGotSceneGpuQuery(cmdbuf,scene_gpu_query);

    if (is_indexed) {
        cmdbuf.drawIndexed(regs.num_indices, regs.num_instances.NumInstances(), 0,
                           s32(vertex_offset), instance_offset);
    } else {
        cmdbuf.draw(regs.num_indices, regs.num_instances.NumInstances(), vertex_offset,
                    instance_offset);
    }
    DebugState.IncDrawCall();
    Diagnostics::EndGotSceneGpuQuery(scheduler,cmdbuf,std::move(scene_gpu_query));
    ArmFrameProbePass(state);
    CountSubmittedDraw(regs, state, is_indexed, false);
    Diagnostics::TraceMenuDraw(regs, *pipeline, state, is_indexed, false);

    ResetBindings(false);
    CaptureVertexOutput();
    CaptureIndexedImageFeedback();
}

void Rasterizer::DrawIndirect(bool is_indexed, VAddr arg_address, u32 offset, u32 stride,
                              u32 max_count, VAddr count_address, u16 vertex_sgpr_offset,
                              u16 instance_sgpr_offset) {
    RENDERER_TRACE;

    CountDrawAttempt(liverpool->regs, true);
    scheduler.PopPendingOperations();

    if (!FilterDraw()) {
        if (RenderAuditEnabled()) {
            ++GetDrawAuditCounters().filtered;
        }
        return;
    }

    const DrawIndirectParams params = {
        .vertex_sgpr_offset = vertex_sgpr_offset,
        .instance_sgpr_offset = instance_sgpr_offset,
        .is_indirect = true,
    };
    const GraphicsPipeline* pipeline = pipeline_cache.GetGraphicsPipeline(params);
    if (!pipeline) {
        if (RenderAuditEnabled()) {
            ++GetDrawAuditCounters().missing_pipeline;
        }
        return;
    }

    PrepareRenderState(pipeline);
    if (!BindResources(pipeline)) {
        if (RenderAuditEnabled()) {
            ++GetDrawAuditCounters().binding_failed;
        }
        return;
    }
    const auto state = BeginRendering(pipeline);

    BindVertexBuffers(pipeline);
    if (is_indexed) {
        BindIndexBuffer(0, true);
    }

    const auto size = stride * max_count;
    // Snapshot the cache's provenance BEFORE obtaining the indirect argument:
    // a small read-only buffer may otherwise be mirrored from guest CPU
    // memory into a stream buffer. The GPU work counters proved zero work;
    // we must distinguish a genuine GPU-written zero from a stale CPU copy.
    const bool gpu_modified_before_obtain=
        buffer_cache.IsRegionGpuModified(arg_address+offset,size);
    const auto [buffer, base] = buffer_cache.ObtainBuffer(arg_address + offset, size);
    needs_barrier |= runtime.IsBufferAccessed(buffer, base, size);
    bound_buffers.emplace_back(buffer, base, size, vk::AccessFlagBits2::eIndirectCommandRead);

    const VideoCore::Buffer* count_buffer=nullptr;
    u64 count_offset{};
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
    const auto scene_gpu_query=Diagnostics::PrepareGotSceneGpuQuery(
        instance,scheduler,liverpool->regs,*pipeline,is_indexed,true);
    if (scene_gpu_query && scene_gpu_query->high_volume) {
        // Buffer::mem_type is a five-value enum; the previously referenced
        // VideoCore::BufferTypeName helper does not exist in this source tree.
        // Keep readable, bounded GPU-argument provenance without an extra API.
        const auto backing_type_name = [](VideoCore::MemoryType type) noexcept -> const char* {
            switch (type) {
            case VideoCore::MemoryType::DeviceLocal: return "DeviceLocal";
            case VideoCore::MemoryType::HostUncached: return "HostUncached";
            case VideoCore::MemoryType::HostCached: return "HostCached";
            case VideoCore::MemoryType::Stream: return "Stream";
            case VideoCore::MemoryType::Sparse: return "Sparse";
            }
            return "Unknown";
        };
        LOG_INFO(Render_Vulkan,
                 "GotIndirectArgumentSource: fs={:#x} vs={:#x} "
                 "pair_ordinal={} arg_guest={:#x} bytes={} "
                 "gpu_modified_before_obtain={} "
                 "returned_buffer_type={} returned_cpu_addr={:#x} "
                 "arena_offset={} stream_buffer={} "
                 "count_guest={:#x} count_buffer_present={} "
                 "no_argument_substitution=YES",
                 scene_gpu_query->fs,scene_gpu_query->vs,
                 scene_gpu_query->pair_ordinal,arg_address+offset,size,
                 gpu_modified_before_obtain,
                 backing_type_name(buffer->mem_type),
                 buffer->CpuAddr(),base,
                 buffer->mem_type==VideoCore::MemoryType::Stream,
                 count_address,count_address!=0);
    }
    scheduler.BeginRendering(state);

    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eGraphics, pipeline->Handle());
    Diagnostics::BeginGotSceneGpuQuery(cmdbuf,scene_gpu_query);

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

    // Capture the actual Vulkan argument bytes for BOTH highly active
    // five-MRT scene pipelines at the GPU-query draw checkpoints. In
    // particular, the scene renderer 0xc1123f0f previously escaped all
    // probes because its guest num_indices register was only 3.
    const bool prove_indirect=scene_gpu_query &&
        scene_gpu_query->high_volume && is_indexed;
    const u32 proof_fs=prove_indirect ? scene_gpu_query->fs : 0u;
    const u32 proof_vs=prove_indirect ? scene_gpu_query->vs : 0u;
    Diagnostics::EndGotSceneGpuQuery(scheduler,cmdbuf,std::move(scene_gpu_query));
    ArmFrameProbePass(state);
    if (prove_indirect) {
        Diagnostics::CaptureGotSceneGpuIndirectArguments(
            instance,scheduler,runtime,buffer,base,arg_address+offset,
            stride,max_count,count_address ? count_buffer : nullptr,
            count_offset,count_address,liverpool->regs.num_indices,
            proof_fs,proof_vs);
    }
    CountSubmittedDraw(liverpool->regs, state, is_indexed, true);
    Diagnostics::TraceMenuDraw(liverpool->regs, *pipeline, state, is_indexed, true);
    ResetBindings(false);
    CaptureVertexOutput();
    CaptureIndexedImageFeedback();
}

void Rasterizer::DispatchDirect() {
    RENDERER_TRACE;

    scheduler.PopPendingOperations();

    const auto& cs_program = liverpool->GetCsRegs();
    const ComputePipeline* pipeline = pipeline_cache.GetComputePipeline();
    if (!pipeline) {
        return;
    }

    const auto& cs = pipeline->GetStage(Shader::SwStage::Compute);
    const bool handled = ExecuteShaderHLE(cs, liverpool->regs, cs_program, *this);
    if (Diagnostics::FrameProbeEnabled() && Diagnostics::FrameProbe().frame) {
        LOG_INFO(Render_Vulkan,
                 "FrameProbeCompute: frame={} after_draw={} shader={:#x} indirect=false "
                 "groups={}x{}x{} hle={}",
                 Diagnostics::FrameProbe().frame, Diagnostics::FrameProbe().draws,
                 cs.pgm_hash, cs_program.dim_x, cs_program.dim_y, cs_program.dim_z, handled);
    }
    if (handled) {
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
    cmdbuf.dispatch(cs_program.dim_x, cs_program.dim_y, cs_program.dim_z);
    TraceGotIndirectComputeDispatch(cs, false, cs_program.dim_x, cs_program.dim_y,
                                    cs_program.dim_z);
    DebugState.IncDispatch();

    ResetBindings(true);
    CaptureIndexedImageFeedback();
    Diagnostics::CaptureGotGdsComputeProducer(
        instance,scheduler,runtime,buffer_cache.GetGdsBuffer(),cs,
        false,cs_program.dim_x,cs_program.dim_y,cs_program.dim_z);
}

void Rasterizer::DispatchIndirect(VAddr address, u32 offset, u32 size) {
    RENDERER_TRACE;

    scheduler.PopPendingOperations();

    const auto& cs_program = liverpool->GetCsRegs();
    const ComputePipeline* pipeline = pipeline_cache.GetComputePipeline();
    if (!pipeline) {
        return;
    }

    if (!BindResources(pipeline)) {
        return;
    }

    const auto [buffer, base] = buffer_cache.ObtainBuffer(address + offset, size);
    Diagnostics::CaptureFrameProbeDispatch(
        instance, scheduler, runtime, *buffer, base, address + offset,
        pipeline->GetStage(Shader::SwStage::Compute).pgm_hash);
    needs_barrier |= runtime.IsBufferAccessed(buffer, base, size);
    bound_buffers.emplace_back(buffer, base, size, vk::AccessFlagBits2::eIndirectCommandRead);

    if (needs_barrier) {
        runtime.FlushBarriers();
    }

    scheduler.EndRendering();
    pipeline->BindResources(set_writes, push_data);

    const auto cmdbuf = scheduler.CommandBuffer();
    cmdbuf.bindPipeline(vk::PipelineBindPoint::eCompute, pipeline->Handle());
    cmdbuf.dispatchIndirect(buffer->Handle(), base);
    const auto& cs = pipeline->GetStage(Shader::SwStage::Compute);
    if (Diagnostics::FrameProbeEnabled() && Diagnostics::FrameProbe().frame) {
        LOG_INFO(Render_Vulkan,
                 "FrameProbeCompute: frame={} after_draw={} shader={:#x} indirect=true args={:#x}",
                 Diagnostics::FrameProbe().frame, Diagnostics::FrameProbe().draws,
                 cs.pgm_hash, address + offset);
    }
    TraceGotIndirectComputeDispatch(cs, true, 0, 0, 0);
    DebugState.IncDispatch();

    ResetBindings(true);
    CaptureIndexedImageFeedback();
    Diagnostics::CaptureGotGdsComputeProducer(
        instance,scheduler,runtime,buffer_cache.GetGdsBuffer(),cs,
        true,0,0,0);
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

void Rasterizer::CaptureVertexOutput() {
    if (!vertex_output_feedback) {
        return;
    }
    const u64 bytes = vertex_output_feedback->SizeBytes();
    auto snapshot = std::make_unique<VideoCore::Buffer>(
        instance, 0, bytes, VideoCore::MemoryType::HostCached);
    const vk::BufferCopy copy{.srcOffset = 0, .dstOffset = 0, .size = bytes};
    runtime.CopyBuffer(vertex_output_feedback.get(), snapshot.get(), std::span{&copy, 1});
    const vk::BufferMemoryBarrier2 host_barrier = {
        .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
        .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
        .dstStageMask = vk::PipelineStageFlagBits2::eHost,
        .dstAccessMask = vk::AccessFlagBits2::eHostRead,
        .buffer = snapshot->Handle(),
        .offset = 0,
        .size = bytes,
    };
    scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
        .bufferMemoryBarrierCount = 1,
        .pBufferMemoryBarriers = &host_barrier,
    });
    scheduler.DeferPriorityOperation(
        [snapshot = std::move(snapshot), source = std::move(vertex_output_feedback),
         frame = Diagnostics::FrameProbe().frame, draw = Diagnostics::FrameProbe().draws,
         shader = vertex_output_shader, bytes]() {
            snapshot->Invalidate(0, bytes);
            const auto* data = reinterpret_cast<const u32*>(snapshot->mapped_data.data());
            const auto dir = Common::FS::GetUserPath(Common::FS::PathType::ShaderDir) / "frame-probes";
            std::error_code error;
            std::filesystem::create_directories(dir, error);
            if (error) {
                LOG_ERROR(Render_Vulkan, "VertexOutputFeedback: failed to create output directory");
                return;
            }
            const auto path = dir / fmt::format("f{}-d{}-vs-{:#x}.vertices.bin", frame, draw, shader);
            std::ofstream output(path, std::ios::binary);
            output.write(reinterpret_cast<const char*>(data), bytes);
            LOG_INFO(Render_Vulkan,
                     "VertexOutputFeedback: frame={} draw={} shader={:#x} invocations={} "
                     "samples={} saved={} guest_mutation=NO",
                     frame, draw, shader, data[1],
                     std::min(data[1], Shader::Diagnostics::VertexOutputSamples), bool(output));
        });
}

void Rasterizer::CaptureIndexedImageFeedback() {
    using namespace Shader::Diagnostics;
    for (auto& [key, feedback] : indexed_image_feedback) {
        if (!std::exchange(feedback.capture, false)) {
            continue;
        }
        const u64 bytes = feedback.buffer->SizeBytes();
        auto snapshot = std::make_unique<VideoCore::Buffer>(instance, 0, bytes,
                                                            VideoCore::MemoryType::HostCached);
        const vk::BufferCopy copy{.srcOffset = 0, .dstOffset = 0, .size = bytes};
        runtime.CopyBuffer(feedback.buffer.get(), snapshot.get(), std::span{&copy, 1});
        LOG_INFO(Render_Vulkan,
                 "IndexedImageFeedbackSubmitted: shader={:#x} stage={} binding={} bytes={}",
                 key.first, key.second, feedback.bindings, bytes);
        scheduler.DeferPriorityOperation(
            [snapshot = std::move(snapshot), source = std::move(feedback.buffer), key,
             sequence = feedback.bindings, entries = feedback.entries]() {
                ASSERT(source);
                snapshot->Invalidate(0, snapshot->SizeBytes());
                const auto* data = reinterpret_cast<const u32*>(snapshot->mapped_data.data());
                for (u32 resource = 0; resource < entries.size(); ++resource) {
                    if (!entries[resource]) {
                        continue;
                    }
                    const auto* row = data + 1u + resource * IndexedImageFeedbackWordsPerImage;
                    u32 distinct = 0;
                    std::string ids;
                    for (u32 id = 0; id < IndexedImageFeedbackOverflow; ++id) {
                        if (!(row[IndexedImageFeedbackHeaderWords + id / 32] & (1u << (id % 32)))) {
                            continue;
                        }
                        if (distinct < 48) {
                            if (!ids.empty())
                                ids += ",";
                            ids += std::to_string(id);
                        }
                        ++distinct;
                    }
                    LOG_INFO(Render_Vulkan,
                             "IndexedImageFeedback: shader={:#x} stage={} binding={} resource={} "
                             "entries={} samples={} out_of_range={} nonzero_samples={} "
                             "nonfinite_samples={} distinct_ids={} overflow={} first_48=[{}] "
                             "cumulative=NO timeline_complete=YES",
                             key.first, key.second, sequence, resource, entries[resource], row[0],
                             row[1], row[2], row[3], distinct,
                             (row[IndexedImageFeedbackWordsPerImage - 1] & 1u) != 0, ids);
                }
            });
    }
}

void Rasterizer::OnSubmit() {
    buffer_cache.TickFrame();
    texture_cache.ProcessDownloadImages();
    texture_cache.RunGarbageCollector();
    runtime.TickFrame();
}

void Rasterizer::OnFence() {
    texture_cache.ProcessDownloadImages();
}

bool Rasterizer::BindResources(const Pipeline* pipeline) {
    if (IsComputeImageCopy(pipeline) || IsComputeMetaClear(pipeline) ||
        IsComputeImageClear(pipeline)) {
        return false;
    }

    set_write_index = 0;
    set_writes.clear();
    buffer_infos.clear();
    image_infos.clear();

    // Reserve across ALL shader stages before creating VkWriteDescriptorSet
    // pointers into image_infos. Later vector reallocations would otherwise
    // leave descriptor writes pointing into freed memory.
    std::size_t required_infos = 0;
    std::size_t max_stage_images = 0;
    for (const auto* stage : pipeline->GetStages()) {
        if (!stage) continue;
        std::size_t stage_images = 0;
        for (const auto& image : stage->images) {
            stage_images += image.NumBindings(*stage);
        }
        max_stage_images = std::max(max_stage_images, stage_images);
        required_infos += stage_images;
        for (const auto& sampler : stage->samplers) {
            required_infos += sampler.NumBindings(*stage);
        }
    }
    if (max_stage_images > Shader::MaxGuestIndexedImageEntries +
                            Shader::NUM_IMAGES * 64u) {
        LOG_ERROR(Render_Vulkan, "Rejecting excessive guest image descriptor count {}",
                  max_stage_images);
        return false;
    }
    image_infos.reserve(required_infos);
    image_bindings.resize(std::max(max_stage_images,
                                   std::size_t{Shader::NUM_IMAGES}));

    bool uses_dma = false;

    // Bind resource buffers and textures.
    Shader::Backend::Bindings binding{};
    push_data = MakeUserData(liverpool->regs);
    for (const auto* stage : pipeline->GetStages()) {
        if (!stage) {
            continue;
        }
        set_writes.resize(set_writes.size() + stage->buffers.size() + stage->images.size() +
                          stage->samplers.size());
        BindBuffers(*stage, binding, push_data);
        BindTextures(*stage, binding);
        uses_dma |= stage->uses_dma;
    }

    if (uses_dma) {
        buffer_cache.SynchronizeDmaBuffers();
    }

    return true;
}

void Rasterizer::BindVertexBuffers(const GraphicsPipeline* pipeline) {
    const auto& regs = liverpool->regs;
    VertexInputs<vk::VertexInputAttributeDescription2EXT> attributes;
    VertexInputs<vk::VertexInputBindingDescription2EXT> bindings;
    VertexInputs<vk::VertexInputBindingDivisorDescriptionEXT> divisors;
    VertexInputs<AmdGpu::Buffer> guest_buffers;
    pipeline->GetVertexInputs(attributes, bindings, divisors, guest_buffers,
                              regs.vgt_instance_step_rate_0, regs.vgt_instance_step_rate_1);

    if (instance.IsVertexInputDynamicState()) {
        // Update current vertex inputs.
        const auto cmdbuf = scheduler.CommandBuffer();
        cmdbuf.setVertexInputEXT(bindings, attributes);
    }

    if (bindings.empty()) {
        // If there are no bindings, there is nothing further to do.
        return;
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

    if (Shader::Diagnostics::VertexOutputFeedbackEnabled()) {
        const auto& stage = pipeline->GetStage(Shader::SwStage::Vertex);
        for (u32 i = 0; i < guest_buffers.size(); ++i) {
            const auto& sharp = guest_buffers[i];
            const auto found = std::ranges::find_if(ranges_merged, [&](const auto& range) {
                return sharp.base_address >= range.base_address &&
                       sharp.base_address < range.end_address;
            });
            if (found != ranges_merged.end()) {
                const u64 size = std::min<u64>(sharp.GetSize(),
                                               found->end_address - sharp.base_address);
                Diagnostics::CaptureFrameProbeBuffer(
                    instance, scheduler, runtime, *found->buffer,
                    found->offset + sharp.base_address - found->base_address,
                    size, stage, 0x1000u + i, sharp);
            }
        }
    }

    // Bind vertex buffers
    VertexInputs<vk::Buffer> host_buffers;
    VertexInputs<vk::DeviceSize> host_offsets;
    VertexInputs<vk::DeviceSize> host_sizes;
    VertexInputs<vk::DeviceSize> host_strides;
    for (const auto& buffer : guest_buffers) {
        if (buffer.base_address != 0 && buffer.GetSize() > 0) {
            const auto host_buffer_info =
                std::ranges::find_if(ranges_merged, [&](const BufferRange& range) {
                    return buffer.base_address >= range.base_address &&
                           buffer.base_address < range.end_address;
                });
            ASSERT(host_buffer_info != ranges_merged.cend());
            host_buffers.emplace_back(host_buffer_info->buffer->Handle());
            host_offsets.push_back(host_buffer_info->offset + buffer.base_address -
                                   host_buffer_info->base_address);
        } else {
            host_buffers.emplace_back(VK_NULL_HANDLE);
            host_offsets.push_back(0);
        }
        host_sizes.push_back(buffer.GetSize());
        host_strides.push_back(buffer.GetStride());
    }

    const auto cmdbuf = scheduler.CommandBuffer();
    const auto num_buffers = guest_buffers.size();
    if (instance.IsVertexInputDynamicState()) {
        cmdbuf.bindVertexBuffers(0, num_buffers, host_buffers.data(), host_offsets.data());
    } else {
        cmdbuf.bindVertexBuffers2(0, num_buffers, host_buffers.data(), host_offsets.data(),
                                  host_sizes.data(), host_strides.data());
    }
}

void Rasterizer::BindIndexBuffer(u32 index_offset, bool is_indirect) {
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

    const auto cmdbuf = scheduler.CommandBuffer();
    if (instance.IsMaintenance5Supported()) {
        cmdbuf.bindIndexBuffer2KHR(index_buffer, buffer_offset, index_buffer_size, index_type);
    } else {
        cmdbuf.bindIndexBuffer(index_buffer, buffer_offset, index_type);
    }
}

void Rasterizer::ArmFrameProbePass(const RenderState& state) {
    if (!Diagnostics::FrameProbeEnabled() || !Diagnostics::FrameProbe().frame) {
        return;
    }
    auto& probe = Diagnostics::FrameProbe();
    std::vector<Diagnostics::FrameProbeImage> targets;
    const auto append = [&](const ImageBinding& binding, const RenderAttachment& attachment,
                            u32 slot, bool depth) {
        if (!binding.image_id || !attachment.image_view) {
            return;
        }
        const auto& image = texture_cache.GetImage(binding.image_id);
        if (image.backing->num_samples != 1 || image.info.type == AmdGpu::ImageType::Color3D) {
            return;
        }
        const auto base = binding.desc.view_info.range.base;
        targets.push_back({
            .image = image.GetImage(),
            .layout = attachment.image_layout,
            .format = image.backing->image.image_ci.format,
            .barrier_aspects = image.aspect_mask,
            .guest = image.info.guest_address,
            .width = std::min<u32>(state.width, std::max(1u, image.info.size.width >> base.level)),
            .height = std::min<u32>(state.height, std::max(1u, image.info.size.height >> base.level)),
            .level = base.level,
            .layer = base.layer,
            .attachment = slot,
            .depth = depth,
        });
    };
    for (u32 i = 0; i < state.num_color_attachments; ++i) {
        append(cb_descs[i], state.color_attachments[i], i, false);
    }
    if (state.depth_stencil_attachment.has_depth) {
        append(db_desc, state.depth_stencil_attachment, 8, true);
    }
    scheduler.SetRenderEndCallback(
        [this, state, targets = std::move(targets), frame = probe.frame, draw = probe.draws]() mutable {
            Diagnostics::CaptureFrameProbePass(instance, scheduler, state, std::move(targets),
                                                frame, draw);
        });
}

void Rasterizer::ResetBindings(bool is_compute) {
    for (auto& image_id : bound_images) {
        texture_cache.GetImage(image_id).binding = {};
    }
    for (const auto [buffer, offset, size, src_access] : bound_buffers) {
        // The indirect argument fetch is a DRAW_INDIRECT pipeline read, not
        // a vertex/fragment shader read. Keep the correct stage when recording
        // ranges, so GPU-generated command writes are ordered after reads.
        const auto src_stage=BufferHazards::StageForBoundBuffer(src_access,is_compute);
        runtime.AccessBuffer(buffer, offset, size, src_stage, src_access);
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
    if (Diagnostics::MenuPassTraceEnabled() &&
        Diagnostics::WatchedMenuSourceAddress() &&
        (src_image.info.guest_address==Diagnostics::WatchedMenuSourceAddress() ||
         dst_image.info.guest_address==Diagnostics::WatchedMenuSourceAddress())) {
        static thread_local u32 observations{};
        if (++observations<=8 || std::has_single_bit(observations)) {
            LOG_INFO(Render_Vulkan,
                     "GotMenuComputeImageCopy: cs={:#x} src={:#x} "
                     "dst={:#x} bytes={} src_depth={} dst_depth={} "
                     "observation={}",
                     info.pgm_hash,src_image.info.guest_address,
                     dst_image.info.guest_address,src_image.info.guest_size,
                     bool(src_image.info.props.is_depth),
                     bool(dst_image.info.props.is_depth),observations);
        }
    }
    runtime.CopyColorAndDepth(&src_image, &dst_image);
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

    // Image clear must be valid
    VideoCore::Image& image1 = texture_cache.GetImage(image1_id);
    if (image1.info.guest_size != buf1.GetSize() || image1.info.num_bits != buf1_bpp ||
        image1.info.props.is_depth) {
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
    if (Diagnostics::MenuPassTraceEnabled() &&
        image1.info.guest_address==Diagnostics::WatchedMenuSourceAddress()) {
        static thread_local u32 events{};
        if (++events<=8 || std::has_single_bit(events)) {
            LOG_INFO(Render_Vulkan,
                     "GotMenuComputeImageClear: cs={:#x} dst={:#x} "
                     "bytes={} depth={} observed={}",
                     info.pgm_hash,image1.info.guest_address,
                     image1.info.guest_size,bool(image1.info.props.is_depth),events);
        }
    }
    runtime.ClearImage(&image1, range, clear);
    return true;
}

void Rasterizer::BindBuffers(const Shader::Info& stage, Shader::Backend::Bindings& binding,
                             Shader::PushData& push_data) {
    const bool audit_resources = ResourceAuditForStage(stage);
    ResourceAuditCounts* const audit = audit_resources ? &ResourceAuditFor(stage) : nullptr;
    const u64 alignment = instance.StorageMinAlignment();
    for (const auto& desc : stage.buffers) {
        const auto src_access =
            vk::AccessFlagBits2::eShaderRead |
            (desc.is_written ? vk::AccessFlagBits2::eShaderWrite : vk::AccessFlagBits2::eNone);
        if (desc.IsSpecial()) {
            if (desc.buffer_type == Shader::BufferType::GdsBuffer) {
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
                const VideoCore::Buffer* source_buffer{};
                u32 refreshed_words{};
                u32 first_refreshed{};
                std::vector<vk::BufferCopy> copies;
                const auto flush_copies = [&] {
                    if (!copies.empty()) {
                        runtime.CopyBuffer(source_buffer, &vk_buffer, copies);
                        copies.clear();
                    }
                };
                for (u32 i = 0; i < stage.flattened_ud_sources.size(); ++i) {
                    const VAddr address = stage.flattened_ud_sources[i];
                    if (address == 0 || (address & 3) != 0 ||
                        address > (u64{1} << 40) - sizeof(u32) ||
                        !buffer_cache.IsRegionGpuModified(address, sizeof(u32))) {
                        continue;
                    }
                    if (refreshed_words++ == 0) {
                        first_refreshed = i;
                    }
                    const auto [source, source_offset] = buffer_cache.ObtainBuffer(
                        address, sizeof(u32), VideoCore::ObtainBufferFlags::IgnoreStreamBuffer);
                    if (source != source_buffer) {
                        flush_copies();
                        source_buffer = source;
                    }
                    const u64 destination = offset + i * sizeof(u32);
                    if (!copies.empty() &&
                        copies.back().srcOffset + copies.back().size == source_offset &&
                        copies.back().dstOffset + copies.back().size == destination) {
                        copies.back().size += sizeof(u32);
                    } else {
                        copies.emplace_back(source_offset, destination, sizeof(u32));
                    }
                }
                flush_copies();
                if (refreshed_words && Diagnostics::FrameProbeEnabled() &&
                    Diagnostics::FrameProbe().frame) {
                    LOG_INFO(Render_Vulkan,
                             "FrameProbeScalarRefresh: frame={} after_draw={} shader={:#x} "
                             "words={} first_slot={} first_guest={:#x} first_cpu_value={:#x}",
                             Diagnostics::FrameProbe().frame, Diagnostics::FrameProbe().draws,
                             stage.pgm_hash, refreshed_words, first_refreshed,
                             stage.flattened_ud_sources[first_refreshed],
                             stage.flattened_ud_buf[first_refreshed]);
                    Diagnostics::CaptureFrameProbeScalars(instance, scheduler, runtime, vk_buffer,
                                                          offset, stage);
                }
                if (!refreshed_words && Shader::Diagnostics::VertexOutputFeedbackEnabled() &&
                    stage.hw_stage == Shader::HwStage::Vertex) {
                    Diagnostics::CaptureFrameProbeScalars(instance, scheduler, runtime, vk_buffer,
                                                          offset, stage);
                }
                needs_barrier |= runtime.IsBufferAccessed(&vk_buffer, offset, ubo_size);
                bound_buffers.emplace_back(&vk_buffer, offset, ubo_size, src_access);
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
            } else if (desc.buffer_type == Shader::BufferType::FaultBuffer) {
                const auto* fault_buffer = buffer_cache.GetFaultBuffer();
                buffer_infos.emplace_back(fault_buffer->Handle(), 0, fault_buffer->SizeBytes());
            } else if (desc.buffer_type == Shader::BufferType::VertexOutputFeedback) {
                const auto frame = Diagnostics::FrameProbe().frame;
                if (Diagnostics::FrameProbeEnabled() && frame &&
                    Diagnostics::FrameProbe().draws < 2048) {
                    constexpr u32 bytes = Shader::Diagnostics::VertexOutputWords * sizeof(u32);
                    vertex_output_feedback = std::make_unique<VideoCore::Buffer>(
                        instance, 0, bytes, VideoCore::MemoryType::HostCached);
                    std::memset(vertex_output_feedback->mapped_data.data(), 0, bytes);
                    const u32 enabled = 1;
                    std::memcpy(vertex_output_feedback->mapped_data.data(), &enabled, sizeof(u32));
                    vertex_output_feedback->Flush(0, bytes);
                    vertex_output_shader = stage.pgm_hash;
                    const auto* buffer = vertex_output_feedback.get();
                    buffer_infos.emplace_back(buffer->Handle(), 0, bytes);
                    bound_buffers.emplace_back(buffer, 0, bytes, src_access);
                } else {
                    if (!vertex_output_disabled) {
                        vertex_output_disabled = std::make_unique<VideoCore::Buffer>(
                            instance, 0, sizeof(u32), VideoCore::MemoryType::HostCached);
                        std::memset(vertex_output_disabled->mapped_data.data(), 0, sizeof(u32));
                        vertex_output_disabled->Flush(0, sizeof(u32));
                    }
                    buffer_infos.emplace_back(vertex_output_disabled->Handle(), 0, sizeof(u32));
                }
            } else if (desc.buffer_type == Shader::BufferType::IndexedImageFeedback) {
                const auto key = std::pair{u64(stage.pgm_hash), u32(stage.hw_stage)};
                auto& feedback = indexed_image_feedback[key];
                constexpr u32 bytes =
                    (1u +
                     Shader::NUM_IMAGES * Shader::Diagnostics::IndexedImageFeedbackWordsPerImage) *
                    sizeof(u32);
                feedback.capture =
                    Shader::Diagnostics::CaptureIndexedImageFeedback(++feedback.bindings);
                u32 largest_table{};
                for (const auto& image : stage.images) {
                    if (image.is_guest_indexed) {
                        largest_table = std::max(largest_table, image.indexed_guest_entries);
                    }
                }
                if (feedback.growth_captures < 16 &&
                    largest_table >= std::max(32u, feedback.largest_table * 2u)) {
                    feedback.capture = true;
                    ++feedback.growth_captures;
                }
                if (feedback.capture) {
                    feedback.largest_table = std::max(feedback.largest_table, largest_table);
                    feedback.buffer = std::make_unique<VideoCore::Buffer>(
                        instance, 0, bytes, VideoCore::MemoryType::HostCached);
                    ASSERT(feedback.buffer->mapped_data.size() >= bytes);
                    std::memset(feedback.buffer->mapped_data.data(), 0, bytes);
                    const u32 enabled = 1;
                    std::memcpy(feedback.buffer->mapped_data.data(), &enabled, sizeof(enabled));
                    feedback.buffer->Flush(0, bytes);
                    feedback.entries.fill(0);
                    for (u32 i = 0; i < stage.images.size(); ++i) {
                        if (stage.images[i].is_guest_indexed) {
                            feedback.entries[i] = stage.images[i].indexed_guest_entries;
                        }
                    }
                    const auto* buffer = feedback.buffer.get();
                    buffer_infos.emplace_back(buffer->Handle(), 0, bytes);
                    bound_buffers.emplace_back(buffer, 0, bytes, src_access);
                } else {
                    if (!indexed_image_feedback_disabled) {
                        indexed_image_feedback_disabled = std::make_unique<VideoCore::Buffer>(
                            instance, 0, sizeof(u32), VideoCore::MemoryType::HostCached);
                        std::memset(indexed_image_feedback_disabled->mapped_data.data(), 0,
                                    sizeof(u32));
                        indexed_image_feedback_disabled->Flush(0, sizeof(u32));
                    }
                    buffer_infos.emplace_back(indexed_image_feedback_disabled->Handle(), 0,
                                              sizeof(u32));
                }
            } else if (desc.buffer_type == Shader::BufferType::SharedMemory) {
                auto& lds_buffer = buffer_cache.GetStreamBuffer();
                const auto& cs_program = liverpool->GetCsRegs();
                const auto lds_size = cs_program.SharedMemSize() * cs_program.NumWorkgroups();
                const auto [data, offset] = lds_buffer.Map(lds_size, alignment);
                std::memset(data, 0, lds_size);
                lds_buffer.Commit();
                buffer_infos.emplace_back(lds_buffer.Handle(), offset, lds_size);
            } else {
                UNREACHABLE_MSG("Unexpected buffer type {}", u32(desc.buffer_type));
            }
        } else {
            const auto vsharp = desc.GetSharp(stage);
            Diagnostics::TraceFrameProbeBufferBinding(
                stage, static_cast<u32>(&desc - stage.buffers.data()), vsharp, desc.is_written);
            if (audit && desc.table_span_index != ~0u) {
                static thread_local std::unordered_map<std::string, u32> observations;
                const auto key =
                    fmt::format("{}:{}:{}", stage.hw_stage, stage.pgm_hash, desc.table_span_index);
                if (observations.contains(key) || observations.size() < 256u) {
                    auto& observed = observations[key];
                    if (observed < 256u) {
                        ++observed;
                        if (observed == 1u || observed == 32u || observed == 256u) {
                            const auto& layout = stage.buffer_table_spans[desc.table_span_index];
                            LOG_INFO(Render_Vulkan,
                                     "BufferTableSpanBound: shader={:#x} span={} sample={} "
                                     "records={} bytes={} ranges={} written={} formatted={}",
                                     stage.pgm_hash, desc.table_span_index, observed,
                                     stage.flattened_ud_buf[layout.metadata_offset + 4],
                                     vsharp.GetSize(),
                                     stage.buffer_table_ranges[desc.table_span_index].size(),
                                     desc.is_written, desc.is_formatted);
                        }
                    }
                }
            }
            if (Diagnostics::MenuPassTraceEnabled() &&
                stage.hw_stage==Shader::HwStage::Compute && desc.is_written) {
                const u64 watched=Diagnostics::WatchedMenuSourceAddress();
                const u64 begin=static_cast<u64>(vsharp.base_address);
                const u64 length=vsharp.GetSize();
                if (watched && begin && watched>=begin &&
                    watched-begin<length) {
                    static thread_local std::unordered_map<u64,u32> counts;
                    const u32 n=++counts[stage.pgm_hash];
                    if (n<=4u || std::has_single_bit(n)) {
                        LOG_INFO(Render_Vulkan,
                                 "GotMenuComputeBufferWriter: cs={:#x} "
                                 "buffer_base={:#x} bytes={} watched={:#x} "
                                 "observations={}",
                                 stage.pgm_hash,begin,length,watched,n);
                    }
                }
            }
            if (audit) {
                ++audit->buffer_binds;
                if (vsharp.base_address == 0 || vsharp.GetSize() == 0) {
                    ++audit->null_buffers;
                }
            }
            if (vsharp.base_address == 0 || vsharp.GetSize() == 0) {
                // A descriptor can fetch successfully yet yield a null
                // Vulkan buffer (zero records, bad type, etc.). This bounded
                // opt-in audit identifies the *actual* guest field values
                // for shaders with missing world resources, rather than
                // treating every null as an unresolved flattened sharp.
                if (audit && (audit->null_buffers <= 4 ||
                              std::has_single_bit(audit->null_buffers))) {
                    AmdGpu::Buffer guest{};
                    const bool flattened =
                        desc.sharp_fetch.Fetch(stage.flattened_ud_buf.data(), &guest);
                    const u64 size64 = guest.stride == 0
                                           ? guest.num_records
                                           : static_cast<u64>(guest.stride) * guest.num_records;
                    LOG_INFO(Render_Vulkan,
                             "ResourceNullBuffer: shader={:#x} stage={} fetched={} "
                             "summary={} raw_type={} raw_valid={} raw_base={:#x} "
                             "stride={} num_records={} size_u64={} size_u32={} "
                             "written={} formatted={}",
                             stage.pgm_hash, stage.hw_stage, flattened,
                             u32(desc.sharp_fetch.summary), u32(guest.type),
                             guest.Valid(), static_cast<u64>(guest.base_address),
                             static_cast<u32>(guest.stride), guest.num_records,
                             size64, guest.GetSize(),
                             desc.is_written, desc.is_formatted);
                }
                buffer_infos.emplace_back(VK_NULL_HANDLE, 0, VK_WHOLE_SIZE);
            } else {
                const u64 size = memory->ClampRangeSize(vsharp.base_address, vsharp.GetSize());
                if (desc.is_written && Diagnostics::GotIndirectProducerTraceEnabled()) {
                    const u32 hits = Diagnostics::ObservedIndirectProducerHits(
                        vsharp.base_address, size);
                    if (hits) {
                        static thread_local u32 events{};
                        const u32 observed = ++events;
                        if (Diagnostics::ShouldReportIndirectProducer(observed)) {
                            LOG_INFO(Render_Vulkan,
                                     "GotIndirectProducer: path=WRITABLE_SHADER_BIND "
                                     "shader={:#x} stage={} hits_mask={:#x} "
                                     "descriptor_base={:#x} descriptor_bytes={} "
                                     "stride={} records={} clamped={} "
                                     "confirmed_store=NO observed={}",
                                     stage.pgm_hash,stage.hw_stage,hits,
                                     static_cast<u64>(vsharp.base_address),size,
                                     vsharp.stride,vsharp.num_records,
                                     size!=vsharp.GetSize(),observed);
                        }
                    }
                }
                if (size != vsharp.GetSize()) {
                    LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",
                              vsharp.GetSize(), size, stage.pgm_hash);
                    if (audit) {
                        ++audit->clamped_buffers;
                        const u64 full_size = vsharp.stride == 0
                                                  ? vsharp.num_records
                                                  : static_cast<u64>(vsharp.stride) *
                                                        vsharp.num_records;
                        if (full_size > std::numeric_limits<u32>::max()) {
                            ++audit->overflow_buffers;
                        }
                        if (audit->clamped_buffers <= 4 ||
                            std::has_single_bit(audit->clamped_buffers)) {
                            LOG_INFO(Render_Vulkan,
                                     "ResourceAuditClamp: shader={:#x} stage={} stride={} "
                                     "num_records={} exact_u64_size={} reported_u32_size={} "
                                     "mapped_size={} written={} formatted={} overflow={}",
                                     stage.pgm_hash, stage.hw_stage, vsharp.stride,
                                     vsharp.num_records, full_size, vsharp.GetSize(), size,
                                     desc.is_written, desc.is_formatted, full_size > std::numeric_limits<u32>::max());
                        }
                    }
                }
                VideoCore::ObtainBufferFlags flags = {};
                if (desc.is_written) {
                    flags |= VideoCore::ObtainBufferFlags::IsWritten;
                }
                if (desc.is_formatted) {
                    flags |= VideoCore::ObtainBufferFlags::IsTexelBuffer;
                }
                if (stage.uses_dma) {
                    flags |= VideoCore::ObtainBufferFlags::IgnoreStreamBuffer;
                }
                const auto [buffer, offset] = buffer_cache.ObtainBuffer(
                    vsharp.base_address, size,
                    desc.table_span_index == ~0u
                        ? flags
                        : VideoCore::ObtainBufferFlags::IgnoreStreamBuffer);
                if (!desc.is_written) {
                    Diagnostics::CaptureFrameProbeBuffer(
                        instance, scheduler, runtime, *buffer, offset, size, stage,
                        static_cast<u32>(&desc - stage.buffers.data()), vsharp);
                }
                const u64 offset_aligned = Common::AlignDown(offset, alignment);
                const u64 adjust = offset - offset_aligned;
                if (adjust % 4 != 0) {
                    LOG_WARNING(Render_Vulkan, "Buffer binding in shader {:#x} isn't dword aligned",
                                stage.pgm_hash);
                }
                push_data.AddOffset(binding.buffer, adjust);
                buffer_infos.emplace_back(buffer->Handle(), offset_aligned, size + adjust);
                if (desc.table_span_index != ~0u) {
                    flags |= VideoCore::ObtainBufferFlags::IgnoreStreamBuffer;
                    for (const auto& range : stage.buffer_table_ranges[desc.table_span_index]) {
                        (void)buffer_cache.ObtainBuffer(range.address, range.size, flags);
                        const auto range_offset = offset + range.address - vsharp.base_address;
                        bound_buffers.emplace_back(buffer, range_offset, range.size, src_access);
                        needs_barrier |= runtime.IsBufferAccessed(buffer, range_offset, range.size,
                                                                  desc.is_written);
                        if (desc.is_written) {
                            texture_cache.InvalidateMemoryFromGPU(range.address, range.size);
                        }
                    }
                } else {
                    bound_buffers.emplace_back(buffer, offset, size, src_access);
                    if (desc.is_written) {
                        texture_cache.InvalidateMemoryFromGPU(vsharp.base_address, size);
                    }
                    needs_barrier |=
                        runtime.IsBufferAccessed(buffer, offset, size, desc.is_written);
                }
            }
        }

        auto& set_write = set_writes[set_write_index++];
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
    ResourceAuditCounts* const audit =
        ResourceAuditForStage(stage) ? &ResourceAuditFor(stage) : nullptr;
    if (audit) {
        ++audit->texture_calls;
    }
    const u32 first_image_idx = image_infos.size();
    // To emulate storing to explicit mip levels, build a descriptor array with each mip level.
    boost::container::small_vector<u32, 8> image_descriptor_array_sizes;

    u32 num_images{};
    for (u32 resource_index=0; resource_index<stage.images.size(); ++resource_index) {
        const auto& image_desc=stage.images[resource_index];
        // Log the REAL guest texture sampled by each submitted fragment
        // stage and its output color buffer address. The first capture showed
        // that fs 0x96aa3f2e alone draws to GoT's presented targets; we must
        // now establish which offscreen shader generated its source.
        if (stage.hw_stage == Shader::HwStage::Fragment &&
            Diagnostics::MenuPassTraceEnabled()) {
            const auto& cb = liverpool->regs.color_buffers[0];
            const u64 output = cb ? cb.Address() : 0;
            if (Diagnostics::ShouldTraceMenuSource(stage.pgm_hash, output,
                                                   resource_index)) {
                if (image_desc.is_guest_indexed) {
                    AmdGpu::Buffer table{};
                    const bool fetched = image_desc.indexed_table_fetch.FetchChecked(
                        stage.flattened_ud_buf.data(),
                        stage.flattened_ud_buf.size(), &table);
                    Diagnostics::TraceMenuSource(
                        stage.pgm_hash, output, resource_index, true,
                        fetched && table.Valid(),
                        static_cast<u64>(table.base_address),
                        static_cast<u32>(table.stride),
                        static_cast<u32>(table.num_records),
                        0u, 0u, 0u, 0u, 0u);
                } else {
                    const auto tsharp = image_desc.GetSharp(stage);
                    Diagnostics::TraceMenuSource(
                        stage.pgm_hash, output, resource_index, false,
                        tsharp.Valid() && tsharp.Address() != 0,
                        tsharp.Address(), 0u, 0u,
                        static_cast<u32>(tsharp.width) + 1u,
                        static_cast<u32>(tsharp.height) + 1u,
                        static_cast<u32>(tsharp.data_format),
                        static_cast<u32>(tsharp.num_format),
                        static_cast<u32>(tsharp.type));
                }
            }
        }
        // Read-only guest T# census for large, rejected GPU-indexed tables.
        // Do not guess the shader's GPU index or bind a truncated array.
        // An unmapped span is never dereferenced. The opt-in scan is limited
        // to 1 MiB and at most three snapshots per shader per session.
        if (image_desc.oversized_guest_table_probe) {
            static const bool census_enabled = [] {
                const char* v = std::getenv("SHADPS4_GUEST_TSHARP_CENSUS");
                return v && v[0] == '1' && v[1] == '\0';
            }();
            if (census_enabled) {
                static thread_local std::unordered_map<u64, u32> samples;
                const u64 key = (static_cast<u64>(stage.pgm_hash) << 8) |
                                static_cast<u8>(stage.hw_stage);
                const u32 sample = ++samples[key];
                if (sample == 1 || sample == 64 || sample == 256) {
                    AmdGpu::Buffer source{};
                    const bool fetched = image_desc.indexed_table_fetch.FetchChecked(
                        stage.flattened_ud_buf.data(), stage.flattened_ud_buf.size(), &source);
                    const u64 bytes = fetched
                        ? (source.stride == 0 ? static_cast<u64>(source.num_records)
                                              : static_cast<u64>(source.stride) *
                                                    source.num_records)
                        : 0;
                    const bool size_ok = bytes >= sizeof(AmdGpu::Image) &&
                        bytes <= 1024u * 1024u && bytes % sizeof(AmdGpu::Image) == 0;
                    const bool span_mapped = fetched && source.Valid() &&
                        source.base_address && size_ok &&
                        memory->IsValidMapping(source.base_address, bytes);
                    const u32 entries = size_ok
                        ? static_cast<u32>(bytes / sizeof(AmdGpu::Image)) : 0;
                    u32 valid_shapes = 0;
                    u32 valid_2d = 0;
                    u32 usable = 0;
                    std::unordered_set<u64> distinct_addresses;
                    std::unordered_set<u64> descriptor_fingerprints;
                    u32 first_usable = entries;
                    u32 last_usable = entries;
                    if (span_mapped) {
                        for (u32 id = 0; id < entries; ++id) {
                            AmdGpu::Image image{};
                            const u64 addr = static_cast<u64>(source.base_address) +
                                             static_cast<u64>(id) * sizeof(image);
                            std::memcpy(&image, reinterpret_cast<const void*>(addr),
                                        sizeof(image));
                            if (!image.Valid()) continue;
                            ++valid_shapes;
                            if (image.GetType() != AmdGpu::ImageType::Color2D) continue;
                            ++valid_2d;
                            const auto fmt = image.GetDataFmt();
                            const auto nfmt = image.GetNumberFmt();
                            if (!image.Address() ||
                                fmt == AmdGpu::DataFormat::FormatInvalid ||
                                !magic_enum::enum_contains(fmt) ||
                                !magic_enum::enum_contains(nfmt) ||
                                AmdGpu::IsInteger(nfmt) ||
                                !memory->IsValidGpuMapping(image.Address(), 0)) continue;
                            if (usable == 0) first_usable = id;
                            last_usable = id;
                            distinct_addresses.insert(static_cast<u64>(image.Address()));
                            // Raw-descriptor fingerprint, not a texture-cache key.
                            // Only a census metric; never substitute it for a
                            // collision-free resource identity in rendering.
                            u64 fingerprint = 14695981039346656037ull;
                            const auto* raw = reinterpret_cast<const unsigned char*>(&image);
                            for (std::size_t k = 0; k < sizeof(image); ++k) {
                                fingerprint ^= raw[k];
                                fingerprint *= 1099511628211ull;
                            }
                            descriptor_fingerprints.insert(fingerprint);
                            ++usable;
                        }
                    }
                    LOG_INFO(Render_Vulkan,
                             "GuestTSharpCensus: shader={:#x} stage={} sample={} "
                             "fetched={} base={:#x} stride={} records={} bytes={} "
                             "entries={} span_mapped={} valid_shapes={} valid_2d={} "
                             "usable={} first_usable={} last_usable={} "
                             "unique_addresses={} descriptor_fingerprints={} read_only=YES",
                             stage.pgm_hash, stage.hw_stage, sample, fetched,
                             static_cast<u64>(source.base_address),
                             static_cast<u32>(source.stride), static_cast<u32>(source.num_records),
                             bytes, entries, span_mapped, valid_shapes, valid_2d,
                             usable, first_usable, last_usable,
                             distinct_addresses.size(), descriptor_fingerprints.size());
                }
            }
        }
        if (image_desc.is_guest_indexed) {
            // Real GCN T# table. Stage specialisation is allowed to declare
            // this array ONLY if all table entries fit, so a GPU id can
            // choose exactly its own guest image (or the explicit null slot).
            const u32 count=image_desc.NumBindings(stage);
            ASSERT(count >= 1 && count <= Shader::MaxGuestIndexedImageEntries + 1);
            ASSERT(num_images+count<=image_bindings.size());
            AmdGpu::Buffer source{};
            const bool fetched=image_desc.indexed_table_fetch.FetchChecked(
                stage.flattened_ud_buf.data(),stage.flattened_ud_buf.size(),&source);
            const u64 guest_size=fetched
                ? (source.stride==0 ? static_cast<u64>(source.num_records)
                                     : static_cast<u64>(source.stride)*source.num_records)
                : 0u;
            const u32 record_stride = image_desc.indexed_record_stride_bytes;
            const u32 descriptor_offset = image_desc.indexed_descriptor_offset_bytes;
            const u32 represented_entries = fetched && source.Valid()
                ? (record_stride
                    ? Shader::Optimization::CompleteGuestTsharpRecordEntries(
                        static_cast<u32>(source.stride),
                        static_cast<u32>(source.num_records),
                        record_stride,descriptor_offset,
                        Shader::MaxGuestIndexedImageEntries)
                    : Shader::Optimization::CompleteGuestTsharpTableEntries(
                        static_cast<u32>(source.stride),
                        static_cast<u32>(source.num_records),
                        Shader::MaxGuestIndexedImageEntries))
                : 0u;
            const u64 required_bytes = u64(count-1) *
                (record_stride ? record_stride : sizeof(AmdGpu::Image));
            const bool complete =
                fetched && source.Valid() && represented_entries == count - 1u &&
                guest_size == required_bytes &&
                (guest_size == 0 || (source.base_address != 0 &&
                                     memory->IsValidMapping(source.base_address, guest_size)));
            static const bool dump_tables = [] {
                const char* const env = std::getenv("SHADPS4_INDEXED_TABLE_DUMP");
                return env && env[0] == '1' && env[1] == '\0';
            }();
            if (dump_tables && complete && guest_size != 0 &&
                guest_size <= 8u * 1024u * 1024u &&
                Shader::ShaderDumpTargets().Matches(stage.pgm_hash)) {
                struct TableObservation {
                    u32 samples{};
                    u32 largest_table{};
                };
                static thread_local std::unordered_map<std::string, TableObservation> observations;
                static thread_local u32 captures{};
                static thread_local u64 captured_bytes{};
                const auto key = fmt::format("{}_{:#018x}.resource{}", stage.hw_stage,
                                             stage.pgm_hash, resource_index);
                if (captures < 128u && captured_bytes + guest_size <= 64u * 1024u * 1024u &&
                    (observations.contains(key) || observations.size() < 64u)) {
                    auto& observation = observations[key];
                    auto& observed = observation.samples;
                    if (observed < std::numeric_limits<u32>::max()) {
                        ++observed;
                        if (observed == 1u || observed == 32u || observed == 256u ||
                            count - 1 >= std::max(32u, observation.largest_table * 2u)) {
                            ++captures;
                            captured_bytes += guest_size;
                            observation.largest_table = std::max(observation.largest_table, count - 1);
                            const auto dir =
                                Common::FS::GetUserPath(Common::FS::PathType::ShaderDir) / "dumps";
                            std::error_code error;
                            std::filesystem::create_directories(dir, error);
                            if (!error) {
                                const auto name = fmt::format(
                                    "{}.sample{}.stride{}.offset{}.count{}.table.bin", key,
                                    observed, record_stride ? record_stride : sizeof(AmdGpu::Image),
                                    descriptor_offset, count - 1);
                                std::ofstream file(dir / name, std::ios::binary);
                                file.write(reinterpret_cast<const char*>(
                                               static_cast<uintptr_t>(source.base_address)),
                                           guest_size);
                                if (image_desc.indexed_is_pointer_table) {
                                    std::ofstream pointees(dir / (name + ".pointees.bin"),
                                                           std::ios::binary);
                                    for (u32 i = 0; i + 1 < count; ++i) {
                                        AmdGpu::Image raw{};
                                        const u64 address = u64(source.base_address) +
                                                            u64(i) * record_stride +
                                                            descriptor_offset;
                                        const u32 readable = ReadIndexedDescriptor(
                                            memory, address, true,
                                            image_desc.indexed_pointee_offset_bytes, raw);
                                        pointees.write(reinterpret_cast<const char*>(&readable),
                                                       sizeof(readable));
                                        pointees.write(reinterpret_cast<const char*>(&raw),
                                                       sizeof(raw));
                                    }
                                }
                                if (file) {
                                    LOG_INFO(Render_Vulkan,
                                             "IndexedTableDump: shader={:#x} stage={} resource={} "
                                             "sample={} base={:#x} bytes={} file={}",
                                             stage.pgm_hash, stage.hw_stage, resource_index,
                                             observed, static_cast<u64>(source.base_address),
                                             guest_size, name);
                                }
                            }
                        }
                    }
                }
            }
            // Any immutable >1K-entry guest table may be reused ONLY after
            // confirming its exact bytes and every live image generation.
            // This avoids a full TextureCache::FindImage walk for every draw,
            // which otherwise leaves the GPU idle while the CPU loops.
            static const bool cache_enabled = [] {
                const char* const env=std::getenv("SHADPS4_GUEST_TSHARP_CACHE");
                return env && env[0]=='1' && env[1]=='\0';
            }();
            const bool eligible = cache_enabled && complete && count>=1024u &&
                guest_size<=2u*1024u*1024u &&
                record_stride==0u && descriptor_offset==0u;
            const u64 resource_layout_flags =
                u64(image_desc.is_written) |
                (u64(image_desc.is_depth)<<1u) |
                (u64(image_desc.is_atomic)<<2u) |
                (u64(image_desc.is_array)<<3u) |
                (u64(image_desc.is_r128)<<4u) |
                (u64(image_desc.is_atomic_uint_view)<<5u) |
                (u64(image_desc.constant_mip_index)<<8u) |
                (u64(image_desc.mip_fallback_mode)<<16u) |
                (u64(image_desc.post_op)<<24u);
            const auto cache_key_matches=[&](const GuestIndexedBindingCacheEntry& entry){
                return entry.shader_hash==stage.pgm_hash &&
                    entry.shader_stage==static_cast<u32>(stage.hw_stage) &&
                    entry.image_resource_index==resource_index &&
                    entry.resource_layout_flags==resource_layout_flags &&
                    entry.guest_address==source.base_address &&
                    entry.record_stride==record_stride &&
                    entry.descriptor_offset==descriptor_offset &&
                    entry.entries==count;
            };
            GuestIndexedBindingCacheEntry* cache_hit=nullptr;
            if (eligible) {
                for (auto& entry : guest_indexed_binding_cache) {
                    if (!cache_key_matches(entry) ||
                        entry.guest_bytes.size()!=guest_size ||
                        entry.bindings.size()!=count ||
                        entry.image_uids.size()!=count ||
                        std::memcmp(entry.guest_bytes.data(),
                                    reinterpret_cast<const void*>(source.base_address),
                                    static_cast<std::size_t>(guest_size))!=0) continue;
                    bool live=true;
                    for (u32 i=0;i<count;++i) {
                        const auto id=entry.bindings[i].image_id;
                        if (id && !texture_cache.IsImageUidAlive(id,entry.image_uids[i])) {
                            live=false;
                            break;
                        }
                    }
                    // Previously unmapped guest textures can become
                    // resident without changing their T# table bytes.
                    // Revalidate only the initially null slots and fall back
                    // to the full resolver if any is now a usable resource.
                    // Keep the final intentional out-of-range slot null.
                    if (live && entry.valid_slots<count-1u) {
                        const u32 dim_limit=
                            instance.GetPhysicalDevice().getProperties().limits.maxImageDimension2D;
                        for (u32 i=0;i+1u<count;++i) {
                            if (entry.bindings[i].image_id) continue;
                            AmdGpu::Image raw{};
                            const u64 address=source.base_address+
                                              u64(i)*sizeof(AmdGpu::Image);
                            std::memcpy(&raw,reinterpret_cast<const void*>(address),
                                        sizeof(raw));
                            if (!raw.Valid() ||
                                raw.GetType()!=AmdGpu::ImageType::Color2D ||
                                raw.Address()==0 ||
                                !memory->IsValidGpuMapping(raw.Address(),0)) continue;
                            const auto fmt=raw.GetDataFmt();
                            const auto nfmt=raw.GetNumberFmt();
                            if (fmt!=AmdGpu::DataFormat::FormatInvalid &&
                                magic_enum::enum_contains(fmt) &&
                                magic_enum::enum_contains(nfmt) &&
                                !AmdGpu::IsInteger(nfmt) &&
                                raw.width<dim_limit && raw.height<dim_limit &&
                                raw.base_level<=raw.last_level &&
                                raw.pitch>=raw.width) {
                                live=false;
                                break;
                            }
                        }
                    }
                    if (live) {
                        cache_hit=&entry;
                        break;
                    }
                }
            }
            if (cache_hit) {
                std::copy(cache_hit->bindings.begin(),cache_hit->bindings.end(),
                          image_bindings.begin()+num_images);
                for (u32 i=0;i<count;++i) {
                    const auto id=image_bindings[num_images+i].image_id;
                    if (!id) continue;
                    auto& image=texture_cache.GetImage(id);
                    image.tick_accessed_last=scheduler.CurrentTick();
                    image.binding.is_bound=1u;
                }
                num_images+=count;
                image_descriptor_array_sizes.push_back(count);
                static thread_local u64 hits=0;
                const u64 seen=++hits;
                if (seen<=4u || std::has_single_bit(seen)) {
                    LOG_INFO(Render_Vulkan,
                             "GuestTsharpBindingCache: shader={:#x} stage={} "
                             "result=HIT entries={} valid={} hit_count={}",
                             stage.pgm_hash,stage.hw_stage,count-1,
                             cache_hit->valid_slots,seen);
                }
                continue;
            }
            const u32 table_start=num_images;
            bool depth_alias=false;
            u32 valid_slots=0;
            u32 budget_skips=0;
            u64 estimated_resident_bytes=0;
            // A guest V# can point to thousands of malformed T# records.
            // Never allocate arbitrarily large host images just because the
            // guest T# type bit happens to mark the record as an image.
            constexpr u64 MaxEstimatedIndexedImageBytes = 4ull << 30;
            const u32 max_dimension =
                instance.GetPhysicalDevice().getProperties().limits.maxImageDimension2D;
            for (u32 i=0;i<count;++i) {
                auto& slot=image_bindings[num_images++];
                slot.image_id={};
                // The last slot is an intentional null descriptor for guest
                // IDs outside the complete table; it is NOT a fake texture.
                if (!complete || i+1==count) continue;
                const u64 entry_addr=source.base_address +
                    u64(i)*(record_stride ? record_stride : sizeof(AmdGpu::Image)) +
                    descriptor_offset;
                AmdGpu::Image raw{};
                if (!ReadIndexedDescriptor(memory, entry_addr, image_desc.indexed_is_pointer_table,
                                           image_desc.indexed_pointee_offset_bytes, raw))
                    continue;
                if (!raw.Valid() ||
                    raw.GetType()!=AmdGpu::ImageType::Color2D ||
                    raw.Address()==0 ||
                    !memory->IsValidGpuMapping(raw.Address(),0) ||
                    !magic_enum::enum_contains(static_cast<AmdGpu::DataFormat>(
                        raw.data_format)) ||
                    !magic_enum::enum_contains(static_cast<AmdGpu::NumberFormat>(
                        raw.num_format))) continue;
                const auto fmt=raw.GetDataFmt();
                const auto nfmt=raw.GetNumberFmt();
                if (fmt==AmdGpu::DataFormat::FormatInvalid ||
                    !magic_enum::enum_contains(fmt) ||
                    !magic_enum::enum_contains(nfmt) ||
                    AmdGpu::IsInteger(nfmt) ||
                    raw.width >= max_dimension || raw.height >= max_dimension ||
                    raw.base_level > raw.last_level || raw.pitch < raw.width) continue;
                // Conservative upper bound for most regular 2D sampled
                // formats; overestimates block-compressed textures. A refused
                // entry intentionally remains NULL, not a mismatched texture.
                const u64 estimated_bytes =
                    (static_cast<u64>(raw.width) + 1u) *
                    (static_cast<u64>(raw.height) + 1u) * 4u;
                if (estimated_bytes >
                    MaxEstimatedIndexedImageBytes - estimated_resident_bytes) {
                    ++budget_skips;
                    continue;
                }
                estimated_resident_bytes += estimated_bytes;
                auto& desc=slot.desc;
                std::construct_at(&desc,raw,image_desc);
                slot.image_id=texture_cache.FindImage(desc);
                auto* image=&texture_cache.GetImage(slot.image_id);
                if (desc.type==VideoCore::TextureCache::BindingType::Texture) {
                    if (auto depth=texture_cache.GetAssociatedDepth(*image)) {
                        slot.image_id=depth;
                        depth_alias=true;
                        image=&texture_cache.GetImage(slot.image_id);
                    }
                }
                image->binding.is_bound=1u;
                ++valid_slots;
            }
            image_descriptor_array_sizes.push_back(count);
            if (eligible && !depth_alias && budget_skips==0u) {
                // Cache IDs only while the exact guest T# table is unchanged.
                // UIDs reject stale/recycled texture-cache slot identifiers.
                // The normal image-view/layout pass still runs every draw,
                // so descriptor lifetime and synchronization are unchanged.
                auto old=std::find_if(guest_indexed_binding_cache.begin(),
                                      guest_indexed_binding_cache.end(),
                                      cache_key_matches);
                if (old!=guest_indexed_binding_cache.end()) {
                    guest_indexed_binding_cache.erase(old);
                }
                if (guest_indexed_binding_cache.size()>=4u) {
                    guest_indexed_binding_cache.erase(
                        guest_indexed_binding_cache.begin());
                }
                GuestIndexedBindingCacheEntry entry{};
                entry.shader_hash=stage.pgm_hash;
                entry.shader_stage=static_cast<u32>(stage.hw_stage);
                entry.image_resource_index=resource_index;
                entry.resource_layout_flags=resource_layout_flags;
                entry.guest_address=source.base_address;
                entry.record_stride=record_stride;
                entry.descriptor_offset=descriptor_offset;
                entry.entries=count;
                entry.valid_slots=valid_slots;
                entry.estimated_bytes=estimated_resident_bytes;
                const auto* const bytes=
                    reinterpret_cast<const u8*>(source.base_address);
                entry.guest_bytes.assign(bytes,bytes+guest_size);
                entry.bindings.assign(image_bindings.begin()+table_start,
                                      image_bindings.begin()+num_images);
                entry.image_uids.resize(count);
                for (u32 i=0;i<count;++i) {
                    const auto id=entry.bindings[i].image_id;
                    if (id) {
                        entry.image_uids[i]=texture_cache.GetImage(id).image_uid;
                    }
                }
                guest_indexed_binding_cache.push_back(std::move(entry));
            }
            if (audit) {
                static thread_local std::unordered_map<u64,u32> reports;
                const u64 key=(u64(stage.pgm_hash)<<8)|static_cast<u8>(stage.hw_stage);
                const u32 n=++reports[key];
                if(n<=4||std::has_single_bit(n)) {
                    LOG_INFO(Render_Vulkan,
                             "RealIndexedTSharpBound: shader={:#x} stage={} "
                             "entries={} valid={} full_table={} address={:#x} "
                             "budget_skips={} estimated_bytes={} "
                             "record_stride={} descriptor_offset={} null_slot=YES",
                             stage.pgm_hash,stage.hw_stage,count-1,valid_slots,
                             complete && budget_skips==0,
                             static_cast<u64>(source.base_address),
                             budget_skips, estimated_resident_bytes,
                             record_stride, descriptor_offset);
                }
            }
            continue;
        }
        if (audit) {
            using Fetch = std::remove_cvref_t<decltype(image_desc.sharp_fetch)>;
            audit->invalid_image_sharps +=
                image_desc.sharp_fetch.summary == Fetch::Summary::Invalid;
        }
        const auto tsharp = image_desc.GetSharp(stage);
        Diagnostics::TraceFrameProbeImageBinding(stage, resource_index, tsharp,
                                                 image_desc.is_written);
        // A depth R32_FLOAT source might alternatively be written by
        // compute as a storage image. Track exact writer descriptors only
        // when tracing and only for the source seen in the preceding run.
        if (Diagnostics::MenuPassTraceEnabled() &&
            stage.hw_stage==Shader::HwStage::Compute && image_desc.is_written &&
            Diagnostics::WatchedMenuSourceAddress()!=0 &&
            tsharp.Address()==Diagnostics::WatchedMenuSourceAddress()) {
            static thread_local std::unordered_map<u64,u32> counts;
            const u64 key=stage.pgm_hash;
            const u32 n=++counts[key];
            if (n<=4u || std::has_single_bit(n)) {
                LOG_INFO(Render_Vulkan,
                         "GotMenuComputeImageWriter: cs={:#x} "
                         "address={:#x} shader_write=YES depth_view={} "
                         "data_fmt={} number_fmt={} dimensions={}x{} "
                         "observations={}",
                         stage.pgm_hash,tsharp.Address(),image_desc.is_depth,
                         static_cast<u32>(tsharp.GetDataFmt()),
                         static_cast<u32>(tsharp.GetNumberFmt()),
                         static_cast<u32>(tsharp.width)+1u,
                         static_cast<u32>(tsharp.height)+1u,n);
            }
        }
        const auto data_fmt = tsharp.GetDataFmt();
        const auto num_fmt = tsharp.GetNumberFmt();
        if (tsharp.Address() == 0 || data_fmt == AmdGpu::DataFormat::FormatInvalid) {
            if (audit) {
                ++audit->null_images;
            }
            image_bindings[num_images++].image_id = {};
            image_descriptor_array_sizes.push_back(1);
            continue;
        }

        if (!memory->IsValidGpuMapping(tsharp.Address(), 0) ||
            !magic_enum::enum_contains(data_fmt) || !magic_enum::enum_contains(num_fmt)) {
            LOG_WARNING(Render_Vulkan,
                        "Rejecting invalid T# address={:#x}, pitch={}, width={}, "
                        "data_format={}, num_format={}",
                        tsharp.Address(), tsharp.pitch, tsharp.width, static_cast<u32>(data_fmt),
                        static_cast<u32>(num_fmt));
            if (audit) {
                ++audit->unmapped_images;
            }
            image_bindings[num_images++].image_id = {};
            image_descriptor_array_sizes.push_back(1);
            continue;
        }

        if (audit) {
            ++audit->valid_images;
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
    }

    // Second pass to re-bind images that were updated after binding
    for (u32 i = 0; i < num_images; ++i) {
        auto& [image_id, desc] = image_bindings[i];
        bool is_storage = image_id &&
            desc.type == VideoCore::TextureCache::BindingType::Storage;
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
    }

    u32 image_info_idx = first_image_idx;
    u32 image_binding_idx = 0;
    for (u32 image_index = 0; image_index < image_descriptor_array_sizes.size(); ++image_index) {
        const u32 array_size = image_descriptor_array_sizes[image_index];
        // The descriptor set layout is derived from Shader::ImageResource::is_written.
        // TextureCache binding types can differ after depth/stencil association,
        // fallback selection, or invalid texture handles. A descriptor write
        // must use the exact type declared by its pipeline layout.
        const bool is_storage = stage.images[image_index].is_written;
        auto& set_write = set_writes[set_write_index++];
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
            const bool fetched = sampler.indexed_table_fetch.FetchChecked(
                stage.flattened_ud_buf.data(),stage.flattened_ud_buf.size(),&source);
            const u32 record_stride = sampler.indexed_record_stride_bytes;
            const u32 descriptor_offset = sampler.indexed_sampler_offset_bytes;
            const u64 bytes = fetched ? (source.stride == 0
                ? static_cast<u64>(source.num_records)
                : static_cast<u64>(source.stride)*source.num_records) : 0u;
            const bool complete =
                fetched && source.Valid() &&
                Shader::Optimization::CompleteGuestTsharpRecordEntries(
                    static_cast<u32>(source.stride), static_cast<u32>(source.num_records),
                    record_stride, descriptor_offset,
                    Shader::MaxGuestIndexedImageEntries) == count - 1u &&
                bytes == u64(count - 1u) * record_stride &&
                (bytes == 0 ||
                 (source.base_address != 0 && memory->IsValidMapping(source.base_address, bytes)));
            const u32 first_sampler = static_cast<u32>(image_infos.size());
            u32 valid_samplers = 0u;
            // Bind exactly one Vulkan S# per real guest material-record ID.
            // The last descriptor is a valid default solely for out-of-range
            // guest indices (whose paired image slot is null).
            for (u32 i=0;i<count;++i) {
                AmdGpu::Sampler ssharp{};
                if (complete && i+1u < count) {
                    const u64 guest_address = u64(source.base_address) +
                        u64(i)*record_stride + descriptor_offset;
                    if (ReadIndexedDescriptor(memory, guest_address,
                                              sampler.indexed_is_pointer_table,
                                              sampler.indexed_pointee_offset_bytes, ssharp)) {
                        if (ssharp.Valid() &&
                            (ssharp.border_color_type.Value()!=AmdGpu::BorderColor::Custom ||
                             liverpool->regs.ta_bc_base.Address()!=0)) {
                            ++valid_samplers;
                        } else {
                            ssharp = AmdGpu::Sampler{};
                        }
                    }
                }
                const auto vk_sampler = texture_cache.GetSampler(
                    ssharp,liverpool->regs.ta_bc_base,sampler.is_depth,sampler.is_integer);
                image_infos.emplace_back(vk_sampler,VK_NULL_HANDLE,
                                         vk::ImageLayout::eGeneral);
            }
            auto& write = set_writes[set_write_index++];
            write.dstSet=VK_NULL_HANDLE;
            write.dstBinding=binding.unified;
            write.dstArrayElement=0;
            write.descriptorCount=count;
            write.descriptorType=vk::DescriptorType::eSampler;
            write.pImageInfo=&image_infos[first_sampler];
            binding.unified+=count;
            static thread_local std::unordered_map<u64,u32> reports;
            const u64 key=(u64(stage.pgm_hash)<<8)|static_cast<u8>(stage.hw_stage);
            const u32 report=++reports[key];
            if (report<=4u || std::has_single_bit(report)) {
                LOG_INFO(Render_Vulkan,
                         "StridedSamplerBound: shader={:#x} stage={} entries={} "
                         "valid={} table_complete={} record_stride={} offset={}",
                         stage.pgm_hash,stage.hw_stage,count-1u,valid_samplers,
                         complete,record_stride,descriptor_offset);
            }
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
        const auto vk_sampler =
            texture_cache.GetSampler(ssharp, liverpool->regs.ta_bc_base,
                                     sampler.is_depth, sampler.is_integer);
        image_infos.emplace_back(vk_sampler, VK_NULL_HANDLE, vk::ImageLayout::eGeneral);
        auto& set_write = set_writes[set_write_index++];
        set_write.dstSet = VK_NULL_HANDLE;
        set_write.dstBinding = binding.unified++;
        set_write.dstArrayElement = 0;
        set_write.descriptorCount = 1;
        set_write.descriptorType = vk::DescriptorType::eSampler;
        set_write.pImageInfo = &image_infos.back();
    }
    if (audit && (audit->texture_calls == 1 ||
                  std::has_single_bit(audit->texture_calls))) {
        LogResourceAudit(stage, *audit);
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
    for (auto cb = 0u; cb < state.num_color_attachments; ++cb) {
        auto& [image_id, desc] = cb_descs[cb];
        if (!image_id) {
            state.color_attachments[cb] = {};
            continue;
        }
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

    if (state.num_layers == std::numeric_limits<u16>::max()) {
        state.num_layers = 1;
    }

    return state;
}

void Rasterizer::Resolve() {
    const auto& mrt0_hint = liverpool->last_cb_extent[0];
    const auto& mrt1_hint = liverpool->last_cb_extent[1];
    VideoCore::TextureCache::ImageDesc mrt0_desc{liverpool->regs.color_buffers[0], mrt0_hint};
    VideoCore::TextureCache::ImageDesc mrt1_desc{liverpool->regs.color_buffers[1], mrt1_hint};
    auto& mrt0_image = texture_cache.GetImage(texture_cache.FindImage(mrt0_desc, true));
    auto& mrt1_image = texture_cache.GetImage(texture_cache.FindImage(mrt1_desc, true));

    ScopeMarkerBegin(fmt::format("Resolve:MRT0={:#x}:MRT1={:#x}",
                                 liverpool->regs.color_buffers[0].Address(),
                                 liverpool->regs.color_buffers[1].Address()));
    runtime.ResolveImage(&mrt0_image, &mrt1_image, mrt0_desc.view_info.range,
                         mrt1_desc.view_info.range);
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

    ScopeMarkerEnd();
}

void Rasterizer::FillBuffer(VAddr address, u32 num_bytes, u32 value, bool is_gds) {
    if (!is_gds && Diagnostics::GotIndirectProducerTraceEnabled()) {
        const u32 hits = Diagnostics::ObservedIndirectProducerHits(address,num_bytes);
        if (hits) {
            static thread_local u32 observations{};
            if (Diagnostics::ShouldReportIndirectProducer(++observations)) {
                LOG_INFO(Render_Vulkan,
                         "GotIndirectProducer: path=FILL_REQUEST hits_mask={:#x} "
                         "destination={:#x} bytes={} value={:#x} "
                         "host_or_gpu_path=NOT_YET_CHOSEN observed={}",
                         hits,static_cast<u64>(address),num_bytes,value,observations);
            }
        }
    }
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
    if (!dst_gds && Diagnostics::GotIndirectProducerTraceEnabled()) {
        const u32 hits = Diagnostics::ObservedIndirectProducerHits(dst,num_bytes);
        if (hits) {
            static thread_local u32 observations{};
            if (Diagnostics::ShouldReportIndirectProducer(++observations)) {
                LOG_INFO(Render_Vulkan,
                         "GotIndirectProducer: path=DMA_COPY_REQUEST hits_mask={:#x} "
                         "source={:#x} destination={:#x} bytes={} src_gds={} "
                         "host_or_gpu_path=NOT_YET_CHOSEN observed={}",
                         hits,static_cast<u64>(src),static_cast<u64>(dst),
                         num_bytes,src_gds,observations);
            }
        }
    }
    if (!dst_gds && !buffer_cache.IsRegionGpuModified(dst, num_bytes)) {
        if (!src_gds && !buffer_cache.IsRegionGpuModified(src, num_bytes) &&
            !texture_cache.FindImageFromRange(src, num_bytes)) {
            // Both buffers were not transferred to GPU yet. Can safely copy in host memory.
            std::memcpy(std::bit_cast<void*>(dst), std::bit_cast<void*>(src), num_bytes);
            return;
        }
    }
    texture_cache.InvalidateMemoryFromGPU(dst, num_bytes);
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
    Diagnostics::CaptureGotDmaGpuWords(
        instance,scheduler,runtime,src_buffer,src_offset,
        dst_buffer,dst_offset,src,dst,num_bytes,src_gds,dst_gds);
}

u32 Rasterizer::ReadDataFromGds(u32 gds_offset) {
    auto* gds_buf = buffer_cache.GetGdsBuffer();
    // The caller waits for GPU completion before a GDS EOS host read, but
    // VMA stream allocations are not guaranteed HOST_COHERENT. Invalidate
    // the mapped range before interpreting a GPU-written counter on the CPU.
    const auto mapped_size = gds_buf->mapped_data.size();
    ASSERT_MSG(mapped_size >= sizeof(u32) &&
                   gds_offset <= mapped_size - sizeof(u32),
               "GDS counter offset {} exceeds mapped buffer size {}", gds_offset, mapped_size);
    gds_buf->Invalidate(gds_offset, sizeof(u32));
    u32 value{};
    std::memcpy(&value, gds_buf->mapped_data.data() + gds_offset, sizeof(value));
    if (Diagnostics::GotGdsComputeProducerProbeEnabled()) {
        static thread_local u32 reads{};
        const u32 observed=++reads;
        if (Diagnostics::ShouldSampleGotGdsProducer(observed)) {
            LOG_INFO(Render_Vulkan,
                     "GotGdsHostRead: offset={:#x} value={} host_coherent={} "
                     "invalidate_before_read=YES observed={}",
                     gds_offset,value,gds_buf->is_coherent,observed);
        }
    }
    return value;
}

bool Rasterizer::InvalidateMemory(VAddr addr, u64 size, bool assume_locks) {
    if (!IsMapped(addr, size)) {
        // Not GPU mapped memory, can skip invalidation logic entirely.
        return false;
    }
    buffer_cache.InvalidateMemory(addr, size, assume_locks);
    texture_cache.InvalidateMemory(addr, size);
    return true;
}

bool Rasterizer::ReadMemory(VAddr addr, u64 size, bool assume_locks) {
    if (!IsMapped(addr, size)) {
        // Not GPU mapped memory, can skip invalidation logic entirely.
        return false;
    }
    buffer_cache.ReadMemory(addr, size, false, false, assume_locks);
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
    buffer_cache.InvalidateMemory(addr, size);
    texture_cache.UnmapMemory(addr, size);
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
    const auto& regs=liverpool->regs;
    const auto& color=regs.color_buffers[0];
    const bool is_scene_geometry =
        color && color.Address()==0x15203f8000ULL &&
        regs.depth_buffer.DepthValid() &&
        regs.depth_buffer.DepthAddress()==0x1504a08000ULL &&
        regs.num_indices>=128 && regs.color_target_mask.GetMask(0)!=0;
    if (is_scene_geometry && Diagnostics::MenuPassTraceEnabled()) {
        static thread_local u64 observations{};
        const u64 count=++observations;
        if (count<=24 || count==32 || count==128 || (count%1024)==0) {
            const auto vp_count=dynamic_state.viewports.size();
            const auto sc_count=dynamic_state.scissors.size();
            if (vp_count && sc_count) {
                const auto& vp=dynamic_state.viewports.front();
                const auto& sc=dynamic_state.scissors.front();
                LOG_INFO(Render_Vulkan,
                         "GotSceneRasterCoverage: sample={} indexed={} indices={} "
                         "scissor_x={} scissor_y={} scissor_w={} scissor_h={} "
                         "viewport_x={} viewport_y={} viewport_w={} viewport_h={} "
                         "viewport_zmin={} viewport_zmax={} "
                         "cull={:#x} front_face={} "
                         "depth_test={} depth_write={} depth_compare={} "
                         "stencil_test={} raster_discard={} "
                         "clip_space={} clip_disabled={} num_viewports={} num_scissors={}",
                         count,is_indexed,regs.num_indices,
                         sc.offset.x,sc.offset.y,sc.extent.width,sc.extent.height,
                         vp.x,vp.y,vp.width,vp.height,vp.minDepth,vp.maxDepth,
                         static_cast<u32>(static_cast<VkCullModeFlags>(dynamic_state.cull_mode)),
                         static_cast<u32>(dynamic_state.front_face),
                         dynamic_state.depth_test_enabled,
                         dynamic_state.depth_write_enabled,
                         static_cast<u32>(dynamic_state.depth_compare_op),
                         dynamic_state.stencil_test_enabled,
                         dynamic_state.rasterizer_discard_enable,
                         static_cast<u32>(regs.clipper_control.clip_space),
                         regs.IsClipDisabled(),
                         vp_count,sc_count);
            } else {
                LOG_WARNING(Render_Vulkan,
                            "GotSceneRasterCoverage: sample={} missing_viewport_or_scissor "
                            "viewport_count={} scissor_count={} indices={}",
                            count,vp_count,sc_count,regs.num_indices);
            }
        }
    }
    dynamic_state.Commit(instance, scheduler.CommandBuffer());
}

void Rasterizer::UpdateViewportScissorState() const {
    const auto& regs = liverpool->regs;

    const auto combined_scissor_value_tl = [](s16 scr, s16 win, s16 gen, s16 win_offset) {
        return std::max({scr, s16(win + win_offset), s16(gen + win_offset)});
    };
    const auto combined_scissor_value_br = [](s16 scr, s16 win, s16 gen, s16 win_offset) {
        return std::min({scr, s16(win + win_offset), s16(gen + win_offset)});
    };
    const bool enable_offset = !regs.window_scissor.window_offset_disable;

    AmdGpu::Scissor scsr{};
    scsr.top_left_x = combined_scissor_value_tl(
        regs.screen_scissor.top_left_x, s16(regs.window_scissor.top_left_x),
        s16(regs.generic_scissor.top_left_x),
        enable_offset ? regs.window_offset.window_x_offset : 0);
    scsr.top_left_y = combined_scissor_value_tl(
        regs.screen_scissor.top_left_y, s16(regs.window_scissor.top_left_y),
        s16(regs.generic_scissor.top_left_y),
        enable_offset ? regs.window_offset.window_y_offset : 0);
    scsr.bottom_right_x = combined_scissor_value_br(
        regs.screen_scissor.bottom_right_x, regs.window_scissor.bottom_right_x,
        regs.generic_scissor.bottom_right_x,
        enable_offset ? regs.window_offset.window_x_offset : 0);
    scsr.bottom_right_y = combined_scissor_value_br(
        regs.screen_scissor.bottom_right_y, regs.window_scissor.bottom_right_y,
        regs.generic_scissor.bottom_right_y,
        enable_offset ? regs.window_offset.window_y_offset : 0);

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
            vp_scsr.bottom_right_x = std::min(AmdGpu::Scissor::Clamp(vp_scsr.bottom_right_x),
                                              regs.viewport_scissors[i].bottom_right_x);
            vp_scsr.bottom_right_y = std::min(AmdGpu::Scissor::Clamp(vp_scsr.bottom_right_y),
                                              regs.viewport_scissors[i].bottom_right_y);
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

// Vulkan shares its stencil reference between comparison and REPLACE. GCN
// supports independent test/op references: merge bits when compatible and
// retain the established fallback for conflicting masks.
struct StencilReferenceSelection {
    u32 reference;
    u32 conflicting_bits;
};

static constexpr StencilReferenceSelection SelectStencilReference(
    u32 test_value, u32 op_value, u32 compare_mask, u32 write_mask,
    bool compares_reference, bool replaces_test, bool replaces_op) {
    test_value &= 0xffu;
    op_value &= 0xffu;
    compare_mask &= 0xffu;
    write_mask &= 0xffu;
    if (!replaces_op) {
        return {test_value, 0};
    }
    const u32 test_bits = (compares_reference ? compare_mask : 0u) |
                          (replaces_test ? write_mask : 0u);
    const u32 conflict = (test_value ^ op_value) & test_bits & write_mask;
    if (conflict != 0) {
        return {op_value, conflict};
    }
    return {(op_value & ~test_bits) | (test_value & test_bits), 0};
}

void Rasterizer::UpdateDepthStencilState() const {
    const auto& regs = liverpool->regs;
    auto& dynamic_state = scheduler.GetDynamicState();

    // Controlled scene-only A/B: the synchronized GPU readback revealed
    // completely black RGBA8 scene color and depth=0 in spite of large
    // indexed draws. Prove whether depth/stencil rejection is suppressing
    // geometry. This is NOT a production fix: it only runs with an explicit
    // opt-in and the exact, known GoT render-target pair and real scene
    // geometry. Fullscreen UI, depth clears, shadow passes and other titles
    // keep their original state. The A/B leaves guest depth WRITES enabled.
    static const bool got_scene_depth_ab=[] {
        const char* const env=std::getenv("SHADPS4_GOT_SCENE_DEPTH_AB");
        return env && env[0]=='1' && env[1]=='\0';
    }();
    const auto& cb0=regs.color_buffers[0];
    const auto& db=regs.depth_buffer;
    const Diagnostics::GotSceneDepthABInput probe{
        .opted_in=got_scene_depth_ab,
        .guest_color_address=cb0 ? cb0.Address() : 0ULL,
        .guest_depth_address=db.DepthValid() ? db.DepthAddress() : 0ULL,
        .index_count=regs.num_indices,
        .color_write_mask=regs.color_target_mask.GetMask(0),
        .depth_test=bool(regs.depth_control.depth_enable),
        .depth_write=bool(regs.depth_control.depth_write_enable),
        .depth_clear=bool(regs.depth_render_control.depth_clear_enable),
        .raster_discard=bool(regs.clipper_control.dx_rasterization_kill),
    };
    const bool scene_ab=Diagnostics::ShouldForceGotSceneDepthAlways(probe);
    if (scene_ab) {
        static thread_local u64 hits{};
        const u64 n=++hits;
        if (n<=12 || n==32 || n==128 || (n%1024)==0) {
            LOG_INFO(Render_Vulkan,
                     "GotSceneDepthAB: target={:#x} depth={:#x} "
                     "indices={} guest_compare={} applied_compare=ALWAYS "
                     "guest_stencil={} applied_stencil=DISABLED "
                     "depth_write_preserved=YES draw={} "
                     "isolated_opt_in=YES",
                     probe.guest_color_address,probe.guest_depth_address,
                     probe.index_count,
                     static_cast<u32>(regs.depth_control.depth_func),
                     bool(regs.depth_control.stencil_enable),n);
        }
    }

    const auto depth_test_enabled =
        regs.depth_control.depth_enable && regs.depth_buffer.DepthValid();
    dynamic_state.SetDepthTestEnabled(depth_test_enabled);
    if (depth_test_enabled) {
        dynamic_state.SetDepthWriteEnabled(regs.depth_control.depth_write_enable &&
                                           !regs.depth_render_control.depth_clear_enable);
        const auto compare=scene_ab
            ? vk::CompareOp::eAlways
            : LiverpoolToVK::CompareOp(regs.depth_control.depth_func);
        dynamic_state.SetDepthCompareOp(compare);
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
        regs.depth_control.stencil_enable && regs.depth_buffer.StencilValid() && !scene_ab;
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
        const auto& sc = regs.stencil_control;
        const auto has_op = [](AmdGpu::StencilFunc fail, AmdGpu::StencilFunc pass,
                               AmdGpu::StencilFunc depth_fail, AmdGpu::StencilFunc op) {
            return fail == op || pass == op || depth_fail == op;
        };
        const bool front_op = has_op(sc.stencil_fail_front, sc.stencil_zpass_front,
                                     sc.stencil_zfail_front, AmdGpu::StencilFunc::ReplaceOp);
        const bool front_test = has_op(sc.stencil_fail_front, sc.stencil_zpass_front,
                                       sc.stencil_zfail_front, AmdGpu::StencilFunc::ReplaceTest);
        const bool back_op = regs.depth_control.backface_enable
            ? has_op(sc.stencil_fail_back, sc.stencil_zpass_back, sc.stencil_zfail_back,
                     AmdGpu::StencilFunc::ReplaceOp) : front_op;
        const bool back_test = regs.depth_control.backface_enable
            ? has_op(sc.stencil_fail_back, sc.stencil_zpass_back, sc.stencil_zfail_back,
                     AmdGpu::StencilFunc::ReplaceTest) : front_test;
        const u32 front_write = !stencil_clear ? front.stencil_write_mask : 0u;
        const u32 back_write = !stencil_clear ? back.stencil_write_mask : 0u;
        const auto choose = [&](const AmdGpu::StencilRefMask& ref, AmdGpu::CompareFunc func,
                                 u32 write_mask, bool replace_test, bool replace_op,
                                 bool back_face) {
            const bool compares = func != AmdGpu::CompareFunc::Always &&
                                  func != AmdGpu::CompareFunc::Never;
            const auto selected = SelectStencilReference(
                ref.stencil_test_val, ref.stencil_op_val, ref.stencil_mask, write_mask,
                compares, replace_test, replace_op);
            const u32 legacy = replace_op ? ref.stencil_op_val : ref.stencil_test_val;
            if (replace_op && ref.stencil_test_val != ref.stencil_op_val) {
                const u64 signature = u64(ref.stencil_test_val) |
                    (u64(ref.stencil_op_val) << 8) | (u64(ref.stencil_mask) << 16) |
                    (u64(write_mask) << 24) | (u64(func) << 32) |
                    (u64(replace_test) << 36) | (u64(back_face) << 37) |
                    (u64(stencil_clear) << 38);
                static thread_local std::array<u64, 512> seen{};
                static thread_local u32 seen_count{};
                bool found = false;
                for (u32 i = 0; i < seen_count; ++i) {
                    found |= seen[i] == signature;
                }
                if (!found && seen_count < seen.size()) {
                    seen[seen_count++] = signature;
                    LOG_INFO(Render_Vulkan,
                             "GOT51_STENCIL_REFERENCE face={} test={:#x} op={:#x} "
                             "compare_mask={:#x} write_mask={:#x} compare={} "
                             "replace_test={} replace_op={} legacy={:#x} selected={:#x} "
                             "conflict={:#x} changed={} stencil_clear={}",
                             back_face ? "back" : "front", u32(ref.stencil_test_val),
                             u32(ref.stencil_op_val), u32(ref.stencil_mask), write_mask,
                             u32(func), replace_test, replace_op, legacy,
                             selected.reference, selected.conflicting_bits,
                             selected.reference != legacy, stencil_clear);
                }
            }
            return selected.reference;
        };
        const auto back_func = regs.depth_control.backface_enable
            ? regs.depth_control.stencil_bf_func : regs.depth_control.stencil_ref_func;
        dynamic_state.SetStencilReferences(
            choose(front, regs.depth_control.stencil_ref_func, front_write,
                   front_test, front_op, false),
            choose(back, back_func, back_write, back_test, back_op, true));
        dynamic_state.SetStencilWriteMasks(front_write, back_write);
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

    // Separate A/B from the prior depth/stencil experiment. On the exact
    // demonstrated scene target/depth pair, force double-sided geometry to
    // prove whether GCN-to-Vulkan face orientation is wrongly rejecting
    // the terrain. Never apply to UI fullscreen triangles, shadow buffers,
    // depth clears, or unrelated games/targets.
    static const bool got_raster_ab=[] {
        const char* e=std::getenv("SHADPS4_GOT_SCENE_RASTER_AB");
        return e && e[0]=='1' && e[1]=='\0';
    }();
    const auto& cb0=regs.color_buffers[0];
    const auto& db=regs.depth_buffer;
    const Diagnostics::GotSceneDepthABInput selection{
        .opted_in=got_raster_ab,
        .guest_color_address=cb0 ? cb0.Address() : 0ULL,
        .guest_depth_address=db.DepthValid() ? db.DepthAddress() : 0ULL,
        .index_count=regs.num_indices,
        .color_write_mask=regs.color_target_mask.GetMask(0),
        .depth_test=bool(regs.depth_control.depth_enable),
        .depth_write=bool(regs.depth_control.depth_write_enable),
        .depth_clear=bool(regs.depth_render_control.depth_clear_enable),
        .raster_discard=bool(regs.clipper_control.dx_rasterization_kill),
    };
    const bool raster_ab=Diagnostics::ShouldForceGotSceneCullNone(selection);
    if (raster_ab) {
        static thread_local u64 num_overrides{};
        const u64 sample=++num_overrides;
        if (sample<=12 || sample==32 || sample==128 || (sample%1024)==0) {
            LOG_INFO(Render_Vulkan,
                     "GotSceneRasterAB: target={:#x} depth={:#x} "
                     "indices={} old_cull={:#x} new_cull=NONE "
                     "front_face={} original_raster_discard={} "
                     "sample={} isolated_opt_in=YES",
                     selection.guest_color_address,selection.guest_depth_address,
                     selection.index_count,
                     static_cast<u32>(static_cast<VkCullModeFlags>(cull_mode)),
                     static_cast<u32>(front_face),
                     bool(regs.clipper_control.dx_rasterization_kill),sample);
        }
    }
    dynamic_state.SetPrimitiveRestartEnabled(prim_restart);
    dynamic_state.SetRasterizerDiscardEnabled(regs.clipper_control.dx_rasterization_kill);
    dynamic_state.SetCullMode(raster_ab ? vk::CullModeFlags{vk::CullModeFlagBits::eNone} : cull_mode);
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
