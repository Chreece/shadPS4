// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>
#include <optional>
#include <stdexcept>

#include "shader_recompiler/frontend/ordered_gds_capture_coverage.h"
#include "shader_recompiler/frontend/ordered_gds_checkpoint.h"
#include "shader_recompiler/frontend/ordered_gds_dispatch.h"
#include "shader_recompiler/frontend/ordered_gds_epoch_auth.h"
#include "shader_recompiler/frontend/ordered_gds_lds_checkpoint.h"

namespace Shader::Gcn::OrderedGds {

// Exact, bounded GPU allocations for ONE guest compute dispatch's capture,
// host-resolution and future independent resume pass. This is a memory plan,
// not a wave scheduler: x-fastest physical slot != guest creation sequence.
//
// No buffer may be allocated on a guest-controlled size before Require()
// has validated every extent, LDS byte count and the *combined* VRAM budget.
struct GcnOrderedGdsDevicePlan {
    static constexpr u64 MaxEpochDeviceBytes = 512ull * 1024 * 1024;
    static constexpr u32 MaxGuestCreatedWaves = 1u << 13;
    static constexpr u32 MaxGuestWorkgroupThreads = 1024;

    u32 workgroups{};
    u32 waves_per_group{};
    u32 waves{};
    u64 packet_bytes{};
    u64 epoch_auth_bytes{};
    GcnCheckpointLayout register_layout{};
    std::optional<GcnLdsCheckpointLayout> lds_layout{};
    // Raw per-workgroup guest LDS SSBO survives the first-pass return,
    // independent of temporary Vulkan workgroup-local memory and GDS RAM.
    u64 persistent_lds_bytes{};
    u64 combined_device_bytes{};

    // Must match the existing SharedMemoryToStoragePass calculation:
    // flat WorkgroupIndex * per-workgroup guest LDS bytes. This is a GPU
    // buffer offset, not a CIK wave rank, and never includes a fake header.
    [[nodiscard]] u64 PersistentLdsOffset(u32 group_index) const {
        if (!lds_layout || group_index >= workgroups ||
            persistent_lds_bytes != u64(workgroups) * lds_layout->lds_bytes) {
            throw std::invalid_argument("invalid GDS persistent LDS workgroup slot");
        }
        return u64(group_index) * lds_layout->lds_bytes;
    }

    [[nodiscard]] static GcnOrderedGdsDevicePlan Require(
        DispatchExtent groups, DispatchExtent local_threads,
        u32 allocated_vgprs, u32 guest_shared_lds_bytes) {
        // The current full-register checkpoint writer assigns one invocation
        // to each of the 64 guest lanes. A partial last wave would leave
        // unwritten VGPR lane state; refuse it instead of accepting junk.
        if (!local_threads.x || local_threads.x > MaxGuestWorkgroupThreads ||
            !local_threads.y || local_threads.y > MaxGuestWorkgroupThreads ||
            !local_threads.z || local_threads.z > MaxGuestWorkgroupThreads) {
            throw std::invalid_argument("unsupported ordered GDS guest workgroup shape");
        }
        const u64 xy = u64(local_threads.x) * local_threads.y;
        const u64 threads = xy * local_threads.z; // each dimension already <=1024
        if (threads > MaxGuestWorkgroupThreads || (threads % GcnWaveLanes) != 0) {
            throw std::invalid_argument(
                "ordered GDS checkpoint requires complete guest wave64 lanes");
        }
        const auto dispatch = DispatchLayout::Create(
            groups, local_threads, GcnWaveLanes, MaxGuestCreatedWaves);
        if (!dispatch || dispatch->WavesPerGroup() > 16 ||
            dispatch->TotalSlots() > MaxGuestCreatedWaves) {
            throw std::invalid_argument("ordered GDS dispatch exceeds bounded wave slots");
        }
        const u32 waves = static_cast<u32>(dispatch->TotalSlots());
        const u32 per_group = dispatch->WavesPerGroup();
        const u32 workgroups = waves / per_group;
        if (u64(workgroups) * per_group != waves) {
            throw std::logic_error("invalid ordered GDS group/wave cardinality");
        }

        const auto registers = GcnCheckpointLayout::Require(
            waves, LiveCheckpointCoverage::ScalarCount, allocated_vgprs, 0);
        static_assert(sizeof(GpuCapturePacket) == sizeof(GpuResumePacket));
        const u64 packet_bytes = u64(waves) * sizeof(GpuCapturePacket);
        const u64 epoch_auth_bytes = u64(waves) * sizeof(GpuGcnOrderedEpochAuth);

        std::optional<GcnLdsCheckpointLayout> lds;
        if (guest_shared_lds_bytes != 0) {
            lds = GcnLdsCheckpointLayout::Require(
                workgroups, per_group, guest_shared_lds_bytes);
        }
        const u64 lds_bytes = lds ? lds->total_bytes : 0;
        const u64 persistent_lds_bytes = u64(workgroups) * guest_shared_lds_bytes;
        // The persistent raw LDS buffer is additional to the two existing
        // copy/checkpoint buffers and must count against the full VRAM budget.
        const u64 combined =
            2 * (packet_bytes + registers.total_bytes + lds_bytes) +
            epoch_auth_bytes + persistent_lds_bytes;
        if (combined > MaxEpochDeviceBytes) {
            throw std::invalid_argument(
                "ordered GDS full capture/resume exceeds combined VRAM budget");
        }
        return {workgroups, per_group, waves, packet_bytes, epoch_auth_bytes,
                registers, lds, persistent_lds_bytes, combined};
    }
};

} // namespace Shader::Gcn::OrderedGds
