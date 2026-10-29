// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <map>
#include <span>
#include <utility>
#include <vector>

#include "common/recursive_lock.h"
#include "shader_recompiler/frontend/ordered_gds_capture_packet.h"
#include "shader_recompiler/frontend/ordered_gds_legacy_packer.h"
#include "common/shared_first_mutex.h"
#include "video_core/buffer_cache/buffer_cache.h"
#include "video_core/page_manager.h"
#include "video_core/renderer_vulkan/vk_pipeline_cache.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/texture_cache/texture_cache.h"

namespace AmdGpu {
struct Liverpool;
}

namespace Core {
class MemoryManager;
}

namespace Vulkan {

class GraphicsPipeline;
class Runtime;
class OcclusionQuery;

class Rasterizer {
public:
    explicit Rasterizer(const Instance& instance, Scheduler& scheduler, Runtime& runtime,
                        AmdGpu::Liverpool* liverpool);
    ~Rasterizer();

    [[nodiscard]] Scheduler& GetScheduler() noexcept {
        return scheduler;
    }

    [[nodiscard]] Runtime& GetRuntime() noexcept {
        return runtime;
    }

    [[nodiscard]] VideoCore::BufferCache& GetBufferCache() noexcept {
        return buffer_cache;
    }

    [[nodiscard]] VideoCore::TextureCache& GetTextureCache() noexcept {
        return texture_cache;
    }

    void Draw(bool is_indexed, u32 index_offset = 0);
    void DrawIndirect(bool is_indexed, VAddr arg_address, u32 offset, u32 size, u32 max_count,
                      VAddr count_address, u16 vertex_sgpr_offset, u16 instance_sgpr_offset);

    void DispatchDirect();
    void DispatchIndirect(VAddr address, u32 offset, u32 size);

    // Staged Vulkan capture/readback and result upload API. GPU packet-writing,
    // guest-wave ordering and shader continuation are intentionally not enabled.
    [[nodiscard]] std::vector<Shader::Gcn::OrderedGds::GpuCapturePacket>
    DownloadOrderedGdsPackets(const VideoCore::Buffer& device_capture,
                              u64 source_offset, std::size_t packet_count);
    void UploadOrderedGdsPackets(VideoCore::Buffer& device_resume,
                                 u64 destination_offset,
                                 std::span<const Shader::Gcn::OrderedGds::GpuResumePacket> packets);

    // Production GPU-buffer bridge for an ALREADY checkpointed complete guest
    // wave epoch. This cannot create wave ranks, packer seeds or shader state.
    // Callers must supply ranks from the real guest scheduler and CIK packer
    // values from verified dedicated-state initialization (never raw GDS RAM).
    // If capture/resolve/upload fails, no new host packer state is returned.
    // No guest dispatch currently calls this until real shader checkpoint and
    // continuation are implemented; the ordered-GDS SPIR-V guard stays active.
    [[nodiscard]] std::map<Shader::Gcn::OrderedGds::CounterKey, u32>
    ResolveVerifiedOrderedGdsEpoch(
        const VideoCore::Buffer& device_capture, u64 capture_offset,
        VideoCore::Buffer& device_resume, u64 resume_offset, u32 epoch,
        const Shader::Gcn::OrderedGds::VerifiedGuestCreationOrder& creation,
        std::span<const u32> verified_sites,
        std::span<const Shader::Gcn::OrderedGds::OrderedCounterSeed> seed,
        Shader::Gcn::OrderedGds::OrderedCounterSeedOrigin seed_origin);

    // Read the true GPU-visible 64 KiB GDS contents after prior writes,
    // then apply only specifically verified physical dword counter changes.
    // The caller must NOT invent CounterKey -> byte-offset mappings.
    [[nodiscard]] std::vector<u32> SnapshotOrderedGdsDwords();
    void CommitOrderedGdsDwords(std::span<const std::pair<u32, u32>> updates);

    void ScopeMarker(fmt::string_view fmt, fmt::format_args args, auto&& func) {
        if (host_markers_enabled) {
            ScopeMarkerBegin(fmt::vformat(fmt, args));
            func();
            ScopeMarkerEnd();
        } else {
            func();
        }
    }

    void ScopeMarkerBegin(const std::string_view& str, bool from_guest = false);
    void ScopeMarkerEnd(bool from_guest = false);
    void ScopedMarkerInsert(const std::string_view& str, bool from_guest = false);
    void ScopedMarkerInsertColor(const std::string_view& str, const u32 color,
                                 bool from_guest = false);

    void FillBuffer(VAddr address, u32 num_bytes, u32 value, bool is_gds);
    void CopyBuffer(VAddr dst, VAddr src, u32 num_bytes, bool dst_gds, bool src_gds);
    u32 ReadDataFromGds(u32 gsd_offset);
    bool InvalidateMemory(VAddr addr, u64 size, bool assume_locks = false);
    bool ReadMemory(VAddr addr, u64 size, bool assume_locks = false);
    bool IsMapped(VAddr addr, u64 size);
    void MapMemory(VAddr addr, u64 size);
    void RegisterMemory(VAddr addr, u64 size);
    void UnmapMemory(VAddr addr, u64 size);

    u64 Flush();
    void Finish();
    void OnSubmit();
    void OnFence();
    OcclusionQuery& GetOcclusionQuery();
    void SubmitPendingQueries();

    PipelineCache& GetPipelineCache() {
        return pipeline_cache;
    }

    template <typename Func>
    void ForEachMappedRangeInRange(VAddr addr, u64 size, Func&& func) {
        const auto range = decltype(mapped_ranges)::interval_type::right_open(addr, addr + size);
        Common::RecursiveSharedLock lock{mapped_ranges_mutex};
        for (const auto& mapped_range : (mapped_ranges & range)) {
            func(mapped_range);
        }
    }

    std::thread::id GetGpuCommandProcessorThread();
#ifdef __linux__
    u32 GetGpuCommandProcessorThreadId();
#endif

private:
    void PrepareRenderState(const GraphicsPipeline* pipeline);
    RenderState BeginRendering(const GraphicsPipeline* pipeline);
    void Resolve();
    void DepthStencilCopy(bool is_depth, bool is_stencil);
    void EliminateFastClear();

    void UpdateDynamicState(const GraphicsPipeline* pipeline, bool is_indexed) const;
    void UpdateViewportScissorState() const;
    void UpdateDepthStencilState() const;
    void UpdatePrimitiveState(bool is_indexed) const;
    void UpdateRasterizationState() const;
    void UpdateColorBlendingState(const GraphicsPipeline* pipeline) const;

    bool FilterDraw();

    void BindBuffers(const Shader::Info& stage, Shader::Backend::Bindings& binding,
                     Shader::PushData& push_data);
    void BindTextures(const Shader::Info& stage, Shader::Backend::Bindings& binding);
    bool BindResources(const Pipeline* pipeline);

    void BindVertexBuffers(const GraphicsPipeline* pipeline);
    void BindIndexBuffer(u32 index_offset = 0);

    void ResetBindings(bool is_compute);

    bool IsComputeMetaClear(const Pipeline* pipeline);
    bool IsComputeImageCopy(const Pipeline* pipeline);
    bool IsComputeImageClear(const Pipeline* pipeline);

private:
    friend class VideoCore::BufferCache;

    const Instance& instance;
    Scheduler& scheduler;
    Runtime& runtime;
    VideoCore::PageManager page_manager;
    VideoCore::BufferCache buffer_cache;
    VideoCore::TextureCache texture_cache;
    AmdGpu::Liverpool* liverpool;
    Core::MemoryManager* memory;
    boost::icl::interval_set<VAddr> mapped_ranges;
    Common::SharedFirstMutex mapped_ranges_mutex;
    PipelineCache pipeline_cache;
    const bool host_markers_enabled;
    const bool guest_markers_enabled;
    std::unique_ptr<OcclusionQuery> occlusion;

    using RenderTargetInfo = std::pair<VideoCore::ImageId, VideoCore::TextureCache::ImageDesc>;
    std::array<RenderTargetInfo, AmdGpu::NUM_COLOR_BUFFERS> cb_descs;
    std::pair<VideoCore::ImageId, VideoCore::TextureCache::ImageDesc> db_desc;
    boost::container::static_vector<vk::DescriptorImageInfo, Shader::NUM_IMAGES> image_infos;
    boost::container::static_vector<vk::DescriptorBufferInfo, Shader::NUM_BUFFERS> buffer_infos;
    boost::container::static_vector<VideoCore::ImageId, Shader::NUM_IMAGES> bound_images;
    struct BoundBuffer {
        const VideoCore::Buffer* buffer;
        u64 offset;
        u32 size;
        bool is_written;
    };
    boost::container::static_vector<BoundBuffer, Shader::NUM_BUFFERS> bound_buffers;

    u32 set_write_index{};
    Pipeline::DescriptorWrites set_writes;
    Shader::PushData push_data;

    using ImageBindingInfo = std::pair<VideoCore::ImageId, VideoCore::TextureCache::ImageDesc>;
    boost::container::static_vector<ImageBindingInfo, Shader::NUM_IMAGES> image_bindings;
    bool attachment_feedback_loop{};
    bool needs_barrier{};
};

} // namespace Vulkan
