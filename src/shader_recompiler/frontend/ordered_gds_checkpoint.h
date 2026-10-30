// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <span>
#include <stdexcept>
#include <vector>

#include "shader_recompiler/frontend/ordered_gds_capture_packet.h"

namespace Shader::Gcn::OrderedGds {

// This records state of an ACTUAL guest wave immediately after DS_ORDERED_COUNT,
// before any dependent instruction executes. A CPU fixture and a Vulkan packet
// are NOT guest SGPR/VGPR state. The producer must materialize every register
// named by the shader's declared allocation, including inactive lanes.
inline constexpr u32 GcnCheckpointTag = 0x50434447u; // GDCP
inline constexpr u32 GcnCheckpointFlags = 1u;
inline constexpr u32 GcnWaveLanes = 64u;
inline constexpr u64 MaxCheckpointBytes = 256ull * 1024 * 1024;

struct alignas(16) GpuGcnWaveCheckpoint {
    u32 tag{};
    u32 epoch{};
    u32 site_pc{};
    u32 next_guest_pc{};
    u32 logical_slot{};
    u32 runtime_m0{};
    u32 exec_lo{};
    u32 exec_hi{};
    u32 token_lo{};
    u32 token_hi{};
    u32 destination_vgpr{};
    u32 scalar_words{};
    u32 vector_registers{};
    u32 lane_count{};
    u32 flags{};
    u32 reserved{};
};
static_assert(sizeof(GpuGcnWaveCheckpoint) == 64);
static_assert(alignof(GpuGcnWaveCheckpoint) == 16);

// SGPR payload: scalar_words DWORDs. VGPR payload: vector_registers * 64
// DWORDs, laid out VGPR-major and lane-minor, beginning directly after SGPRs.
// LDS is workgroup-shared and needs separate checkpointing: refuse nonzero
// LDS for now rather than resuming with stale shared memory.
struct GcnCheckpointLayout {
    u32 waves{};
    u32 scalar_words{};
    u32 vector_registers{};
    u64 stride_bytes{};
    u64 total_bytes{};

    [[nodiscard]] static GcnCheckpointLayout Require(u32 waves, u32 scalar_words,
                                                      u32 vector_registers,
                                                      u32 shared_lds_bytes) {
        if (!waves || waves > (1u << 13) || scalar_words > 128 ||
            !vector_registers || vector_registers > 256 || shared_lds_bytes) {
            throw std::invalid_argument("unsupported CIK guest checkpoint dimensions or LDS");
        }
        const u64 payload_words = u64(scalar_words) + u64(vector_registers) * GcnWaveLanes;
        const u64 unaligned_bytes = sizeof(GpuGcnWaveCheckpoint) + payload_words * sizeof(u32);
        const u64 aligned_stride = (unaligned_bytes + 15u) & ~u64{15};
        if (aligned_stride > MaxCheckpointBytes / waves) {
            throw std::invalid_argument("CIK guest checkpoint exceeds bounded VRAM budget");
        }
        return {waves, scalar_words, vector_registers,
                aligned_stride, aligned_stride * waves};
    }
};

// The snapshot data must be read from a GPU-written checkpoint device buffer,
// synchronized with the GPU capture packets of the *same* guest epoch. This
// checks completeness/identity of both streams and the presence of register
// storage, but cannot authenticate that shader state was actually written;
// that requires compiler instrumentation and a resumable guest shader.
inline void RequireCompleteGcnWaveCheckpoints(
    std::span<const std::byte> state_bytes,
    std::span<const GpuCapturePacket> captures,
    const GcnCheckpointLayout& layout, u32 expected_epoch) {
    if (!layout.waves || captures.size() != layout.waves ||
        state_bytes.size() != layout.total_bytes) {
        throw std::invalid_argument("guest wave checkpoint/capture coverage mismatch");
    }
    std::vector<bool> seen(layout.waves, false);
    for (const auto& capture : captures) {
        if (capture.flags != CaptureFlags || capture.logical_slot >= layout.waves ||
            seen[capture.logical_slot] || capture.tag != CapturePacketTag ||
            capture.epoch != expected_epoch || !capture.site_pc) {
            throw std::invalid_argument("invalid or duplicate guest checkpoint capture slot");
        }
        seen[capture.logical_slot] = true;
        const std::size_t offset = std::size_t(capture.logical_slot) * layout.stride_bytes;
        GpuGcnWaveCheckpoint checkpoint{};
        std::memcpy(&checkpoint, state_bytes.data() + offset, sizeof(checkpoint));
        if (checkpoint.tag != GcnCheckpointTag || checkpoint.flags != GcnCheckpointFlags ||
            checkpoint.reserved || checkpoint.epoch != expected_epoch ||
            checkpoint.site_pc != capture.site_pc ||
            checkpoint.site_pc > std::numeric_limits<u32>::max() - 8 ||
            checkpoint.next_guest_pc != checkpoint.site_pc + 8 ||
            checkpoint.logical_slot != capture.logical_slot ||
            checkpoint.runtime_m0 != capture.runtime_m0 ||
            checkpoint.exec_lo != capture.exec_lo || checkpoint.exec_hi != capture.exec_hi ||
            checkpoint.token_lo != capture.token_lo ||
            checkpoint.token_hi != capture.token_hi ||
            checkpoint.scalar_words != layout.scalar_words ||
            checkpoint.vector_registers != layout.vector_registers ||
            checkpoint.lane_count != GcnWaveLanes ||
            checkpoint.destination_vgpr >= layout.vector_registers) {
            throw std::invalid_argument("guest GCN wave checkpoint is stale or incomplete");
        }
    }
}

} // namespace Shader::Gcn::OrderedGds
