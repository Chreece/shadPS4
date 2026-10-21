// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <optional>
#include <string_view>
#include <vector>

#include "common/logging/log.h"
#include "common/path_util.h"
#include "core/memory.h"
#include "shader_recompiler/info.h"
#include "video_core/buffer_cache/buffer.h"
#include "video_core/renderer_vulkan/vk_pipeline_common.h"
#include "video_core/renderer_vulkan/vk_runtime.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"

namespace Vulkan::Diagnostics {

inline std::optional<u32> IndirectSnapshotSeconds() {
    static const auto seconds = []() -> std::optional<u32> {
        const char* value = std::getenv("SHADPS4_INDIRECT_ARGUMENT_SNAPSHOT_SECONDS");
        if (!value) {
            return {};
        }
        const std::string_view text{value};
        u32 parsed{};
        const auto [end, error] = std::from_chars(text.data(), text.data() + text.size(), parsed);
        if (error == std::errc{} && end == text.data() + text.size() && parsed <= 900) {
            return parsed;
        }
        LOG_WARNING(Render_Vulkan, "Invalid indirect argument snapshot time; capture disabled");
        return {};
    }();
    return seconds;
}

inline const char* IndirectSnapshotMemoryType(VideoCore::MemoryType type) {
    switch (type) {
    case VideoCore::MemoryType::DeviceLocal:
        return "DeviceLocal";
    case VideoCore::MemoryType::HostUncached:
        return "HostUncached";
    case VideoCore::MemoryType::HostCached:
        return "HostCached";
    case VideoCore::MemoryType::Stream:
        return "Stream";
    case VideoCore::MemoryType::Sparse:
        return "Sparse";
    }
    return "Unknown";
}

struct IndirectSnapshotRecord {
    u32 draw{}, indirect{}, stride{}, max_count{}, captured_commands{};
    u64 vs{}, fs{}, arg_guest{}, count_guest{}, arg_offset{}, count_offset{};
    u64 data_offset{}, arg_bytes{}, count_data_offset{};
    bool indexed{}, arg_gpu_modified{}, count_gpu_modified{};
    bool arg_gpu_copy{}, count_gpu_copy{}, arg_cpu_range_valid{}, count_cpu_range_valid{};
    VideoCore::MemoryType arg_type{}, count_type{};
    const char* status{"CAPTURED"};
};

struct IndirectSnapshotFrame {
    static constexpr u64 ByteLimit = 1_MB;
    static constexpr u32 RecordLimit = 4096;
    u64 elapsed_seconds{}, present{};
    u32 draws{}, indirects{}, budget_skips{}, unsafe_skips{}, truncated{}, gpu_copies{};
    std::vector<IndirectSnapshotRecord> records;
    std::vector<u8> submitted;
    std::vector<u8> cpu;
    std::unique_ptr<VideoCore::Buffer> readback;

    void Save() {
        try {
            if (readback) {
                readback->Invalidate(0, submitted.size());
                if (readback->mapped_data.size() < submitted.size()) {
                    LOG_ERROR(Render_Vulkan, "Indirect argument snapshot readback is not mapped");
                    return;
                }
                for (const auto& record : records) {
                    if (record.arg_gpu_copy) {
                        std::memcpy(submitted.data() + record.data_offset,
                                    readback->mapped_data.data() + record.data_offset,
                                    record.arg_bytes);
                    }
                    if (record.count_gpu_copy) {
                        std::memcpy(submitted.data() + record.count_data_offset,
                                    readback->mapped_data.data() + record.count_data_offset, 4);
                    }
                }
            }
            const auto dir = Common::FS::GetUserPath(Common::FS::PathType::ShaderDir) /
                             "indirect-arguments";
            std::filesystem::create_directories(dir);
            const auto write_bytes = [&](const char* name, const std::vector<u8>& bytes) {
                std::ofstream output;
                output.exceptions(std::ios::badbit | std::ios::failbit);
                output.open(dir / name, std::ios::binary);
                if (!bytes.empty()) {
                    output.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
                }
                output.close();
            };
            write_bytes("submitted.bin", submitted);
            write_bytes("cpu.bin", cpu);
            std::ofstream csv;
            csv.exceptions(std::ios::badbit | std::ios::failbit);
            csv.open(dir / "draws.csv");
            csv << "draw,indirect,vs,fs,indexed,stride,max_count,captured_commands,arg_guest,"
                   "arg_offset,arg_type,arg_gpu_modified,arg_gpu_copy,arg_cpu_range_valid,"
                   "count_guest,count_offset,count_type,count_gpu_modified,count_gpu_copy,"
                   "count_cpu_range_valid,data_offset,arg_bytes,count_data_offset,status\n";
            for (const auto& r : records) {
                csv << r.draw << ',' << r.indirect << ',' << r.vs << ',' << r.fs << ','
                    << r.indexed << ',' << r.stride << ',' << r.max_count << ','
                    << r.captured_commands << ',' << r.arg_guest << ',' << r.arg_offset << ','
                    << IndirectSnapshotMemoryType(r.arg_type) << ',' << r.arg_gpu_modified << ','
                    << r.arg_gpu_copy << ',' << r.arg_cpu_range_valid << ',' << r.count_guest << ','
                    << r.count_offset << ',' << IndirectSnapshotMemoryType(r.count_type) << ','
                    << r.count_gpu_modified << ',' << r.count_gpu_copy << ','
                    << r.count_cpu_range_valid << ',' << r.data_offset << ',' << r.arg_bytes << ','
                    << r.count_data_offset << ',' << r.status << '\n';
            }
            csv.close();
            std::ofstream summary;
            summary.exceptions(std::ios::badbit | std::ios::failbit);
            summary.open(dir / "summary.txt");
            summary << "STATUS=COMPLETE\nCAPTURE_FRAMES=1\nSHADER_FILTERS=NONE\n"
                       "ARGUMENTS_CHANGED=NO\nPIPELINE_QUERIES=NONE\n"
                       "SNAPSHOT_POINT=BEFORE_DRAW\nCPU_MIRROR=SPARSE_BACKING_ZERO_FILLS_HOLES\n"
                    << "ELAPSED_SECONDS=" << elapsed_seconds << '\n'
                    << "PRESENT=" << present << '\n'
                    << "SUBMITTED_DRAWS=" << draws << '\n'
                    << "INDIRECT_CALLS=" << indirects << '\n'
                    << "RECORDS=" << records.size() << '\n'
                    << "BYTES_PER_BINARY=" << submitted.size() << '\n'
                    << "BYTE_LIMIT_PER_BINARY=" << ByteLimit << '\n'
                    << "RECORD_LIMIT=" << RecordLimit << '\n'
                    << "BUDGET_SKIPS=" << budget_skips << '\n'
                    << "UNSAFE_SKIPS=" << unsafe_skips << '\n'
                    << "TRUNCATED_CALLS=" << truncated << '\n'
                    << "GPU_COPIES=" << gpu_copies << '\n'
                    << "GPU_COPY_SYNCHRONIZATION_ADDED=" << (gpu_copies ? "YES" : "NO") << '\n'
                    << "COVERAGE=" << (budget_skips || unsafe_skips || truncated ? "PARTIAL" : "ALL")
                    << '\n';
            summary.close();
            LOG_WARNING(Render_Vulkan,
                        "IndirectArgumentSnapshot: complete indirects={} bytes={} gpu_copies={} "
                        "budget_skips={} unsafe_skips={} truncated={}",
                        indirects, submitted.size(), gpu_copies, budget_skips, unsafe_skips,
                        truncated);
        } catch (const std::exception& error) {
            LOG_ERROR(Render_Vulkan, "Indirect argument snapshot write failed: {}", error.what());
        }
    }
};

struct IndirectSnapshotState {
    std::optional<std::chrono::steady_clock::time_point> start;
    std::unique_ptr<IndirectSnapshotFrame> frame;
    u64 presents{};
    bool finished{};
};

inline IndirectSnapshotState& IndirectSnapshot() {
    static IndirectSnapshotState state;
    return state;
}

inline void IndirectSnapshotDrawSubmitted() {
    if (IndirectSnapshotSeconds()) {
        if (auto& frame = IndirectSnapshot().frame) {
            ++frame->draws;
        }
    }
}

inline void IndirectSnapshotPresent(Scheduler& scheduler) {
    const auto seconds = IndirectSnapshotSeconds();
    if (!seconds) {
        return;
    }
    auto& state = IndirectSnapshot();
    if (state.finished) {
        return;
    }
    ++state.presents;
    const auto now = std::chrono::steady_clock::now();
    if (!state.start) {
        state.start = now;
    }
    if (state.frame) {
        state.finished = true;
        if (state.frame->readback) {
            scheduler.EndRendering();
            const vk::MemoryBarrier2 barrier{
                .srcStageMask = vk::PipelineStageFlagBits2::eCopy,
                .srcAccessMask = vk::AccessFlagBits2::eTransferWrite,
                .dstStageMask = vk::PipelineStageFlagBits2::eHost,
                .dstAccessMask = vk::AccessFlagBits2::eHostRead,
            };
            scheduler.CommandBuffer().pipelineBarrier2(vk::DependencyInfo{
                .memoryBarrierCount = 1,
                .pMemoryBarriers = &barrier,
            });
        }
        scheduler.DeferPriorityOperation([frame = std::move(state.frame)] { frame->Save(); });
        return;
    }
    const auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(now - *state.start).count();
    if (elapsed >= *seconds) {
        state.frame = std::make_unique<IndirectSnapshotFrame>();
        state.frame->elapsed_seconds = elapsed;
        state.frame->present = state.presents;
        LOG_WARNING(Render_Vulkan, "IndirectArgumentSnapshot: armed elapsed_s={} frames=1", elapsed);
    }
}

inline bool CaptureIndirectArguments(
    const Instance& instance, Runtime& runtime, const Pipeline& pipeline, bool indexed,
    const VideoCore::Buffer* args, u64 arg_offset, u64 arg_guest, u32 stride, u32 max_count,
    bool arg_gpu_modified, const VideoCore::Buffer* count, u64 count_offset, u64 count_guest,
    bool count_gpu_modified) {
    if (!IndirectSnapshotSeconds() || !IndirectSnapshot().frame) {
        return false;
    }
    auto& frame = *IndirectSnapshot().frame;
    ++frame.indirects;
    if (frame.records.size() >= IndirectSnapshotFrame::RecordLimit) {
        ++frame.budget_skips;
        return false;
    }
    auto& record = frame.records.emplace_back();
    record.draw = frame.draws + 1;
    record.indirect = frame.indirects;
    record.indexed = indexed;
    record.stride = stride;
    record.max_count = max_count;
    record.arg_guest = arg_guest;
    record.arg_offset = arg_offset;
    record.arg_type = args ? args->mem_type : VideoCore::MemoryType::DeviceLocal;
    record.arg_gpu_modified = arg_gpu_modified;
    record.count_guest = count_guest;
    record.count_offset = count_offset;
    record.count_type = count ? count->mem_type : VideoCore::MemoryType::DeviceLocal;
    record.count_gpu_modified = count_gpu_modified;
    for (const auto* stage : pipeline.GetStages()) {
        if (stage && stage->sw_stage == Shader::SwStage::Vertex) {
            record.vs = stage->pgm_hash;
        } else if (stage && stage->sw_stage == Shader::SwStage::Fragment) {
            record.fs = stage->pgm_hash;
        }
    }
    const u64 requested_bytes = u64{stride} * max_count;
    const auto safe_range = [](const VideoCore::Buffer* source, u64 offset, u64 size) {
        return source && offset <= source->SizeBytes() && size <= source->SizeBytes() - offset;
    };
    if (stride != (indexed ? 20u : 16u) ||
        !safe_range(args, arg_offset, requested_bytes) ||
        (count_guest && (!count || !safe_range(count, count_offset, 4)))) {
        record.status = "UNSAFE_RANGE";
        ++frame.unsafe_skips;
        return false;
    }
    const u64 count_bytes = count ? 4u : 0u;
    const u64 remaining = IndirectSnapshotFrame::ByteLimit - frame.submitted.size();
    if (remaining < count_bytes || (max_count && remaining - count_bytes < stride)) {
        record.status = "BYTE_LIMIT";
        ++frame.budget_skips;
        return false;
    }
    record.captured_commands = std::min<u64>(max_count, (remaining - count_bytes) / stride);
    if (record.captured_commands != max_count) {
        record.status = "TRUNCATED";
        ++frame.truncated;
    }
    record.arg_bytes = u64{record.captured_commands} * stride;
    record.data_offset = frame.submitted.size();
    record.count_data_offset = record.data_offset + record.arg_bytes;
    frame.submitted.resize(record.count_data_offset + count_bytes);
    frame.cpu.resize(frame.submitted.size());
    auto* memory = Core::Memory::Instance();
    const auto copy = [&](const VideoCore::Buffer* source, u64 offset, u64 guest, u64 destination,
                          u64 bytes, bool& gpu_copy, bool& cpu_range_valid) {
        if (!bytes) {
            return;
        }
        cpu_range_valid = memory->IsValidMapping(guest, bytes);
        memory->CopySparseMemory(guest, frame.cpu.data() + destination, bytes);
        if (source->mem_type == VideoCore::MemoryType::Stream &&
            offset <= source->mapped_data.size() && bytes <= source->mapped_data.size() - offset) {
            std::memcpy(frame.submitted.data() + destination,
                        source->mapped_data.data() + offset, bytes);
            return;
        }
        if (!frame.readback) {
            frame.readback = std::make_unique<VideoCore::Buffer>(
                instance, 0, IndirectSnapshotFrame::ByteLimit, VideoCore::MemoryType::HostCached,
                "Indirect argument snapshot");
        }
        const vk::BufferCopy region{offset, destination, bytes};
        runtime.CopyBuffer(source, frame.readback.get(), std::span{&region, 1});
        gpu_copy = true;
        ++frame.gpu_copies;
    };
    copy(args, arg_offset, arg_guest, record.data_offset, record.arg_bytes, record.arg_gpu_copy,
         record.arg_cpu_range_valid);
    if (count) {
        copy(count, count_offset, count_guest, record.count_data_offset, 4, record.count_gpu_copy,
             record.count_cpu_range_valid);
    }
    return record.arg_gpu_copy || record.count_gpu_copy;
}

} // namespace Vulkan::Diagnostics
