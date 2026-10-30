// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>

#include "shader_recompiler/frontend/ordered_gds_checkpoint.h"
#include "shader_recompiler/ir/reg.h"

namespace Shader::Gcn::OrderedGds {

// First byte of a wave's register snapshot is the 80-byte checkpoint header.
// Followed by all 104 scalar dwords, then full 64-lane images of every
// allocated guest VGPR. Keeping this one address calculation shared between
// host metadata and Vulkan shader emission avoids a silent layout mismatch.
inline constexpr u32 CheckpointHeaderDwords = sizeof(GpuGcnWaveCheckpoint) / sizeof(u32);
inline constexpr u32 CheckpointScalarDwords = IR::NumScalarRegs;
inline constexpr u32 CheckpointVectorLanes = GcnWaveLanes;
inline constexpr u32 CheckpointMaxAllocatedVgprs = IR::NumVectorRegs;

enum class CheckpointRegisterBank : u32 {
    Scalar = 0,
    Vector = 1,
    Architectural = 2,
    SiteMetadata = 3,
    // Three fields copied from a trusted host scheduler's GPU transport:
    // epoch, continuation-token lower and upper DWORDs.
    // Tag/flags stay zero until actual wave/LDS state is authenticated.
    AuthenticatedIdentity = 5,
};

// Header DWORDs that can be written from the actual decoded GCN site and
// current physical wave slot. Tag and flags remain deliberately zero: epoch,
// continuation token, verified counter seed and a resumable second pass are
// not available. An incomplete record must NEVER pass checkpoint validation.
// Epoch and token offsets 1/8/9 are reserved for authenticated dispatch state.
inline constexpr std::array<u32, 11> CheckpointSiteMetadataDwords{
    offsetof(GpuGcnWaveCheckpoint, tag) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, site_pc) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, next_guest_pc) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, logical_slot) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, destination_vgpr) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, scalar_words) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, vector_registers) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, lane_count) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, flags) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, reserved) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, abi_version) / sizeof(u32),
};
static_assert(CheckpointSiteMetadataDwords ==
              std::array<u32, 11>{0, 2, 3, 4, 10, 11, 12, 13, 14, 15, 19});

inline constexpr std::array<u32, 3> CheckpointIdentityDwords{
    offsetof(GpuGcnWaveCheckpoint, epoch) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, token_lo) / sizeof(u32),
    offsetof(GpuGcnWaveCheckpoint, token_hi) / sizeof(u32),
};
static_assert(CheckpointIdentityDwords == std::array<u32, 3>{1, 8, 9});

[[nodiscard]] inline std::optional<u32> CheckpointRelativeDword(
    u32 allocated_vgprs, u32 bank, u32 index, u32 lane) {
    if (allocated_vgprs == 0 || allocated_vgprs > CheckpointMaxAllocatedVgprs ||
        lane >= CheckpointVectorLanes) {
        return std::nullopt;
    }
    switch (static_cast<CheckpointRegisterBank>(bank)) {
    case CheckpointRegisterBank::Scalar:
        if (index < CheckpointScalarDwords) {
            return CheckpointHeaderDwords + index;
        }
        break;
    case CheckpointRegisterBank::Vector:
        if (index < allocated_vgprs) {
            return CheckpointHeaderDwords + CheckpointScalarDwords +
                   index * CheckpointVectorLanes + lane;
        }
        break;
    case CheckpointRegisterBank::Architectural:
        // Header field offsets match the actual GpuGcnWaveCheckpoint layout:
        // M0 5, VCC low/high 16/17, SCC 18, EXEC low/high 6/7.
        switch (index) {
        case 0: return 5;
        case 1: return 16;
        case 2: return 17;
        case 3: return 18;
        case 4: return 6;
        case 5: return 7;
        default: break;
        }
        break;
    case CheckpointRegisterBank::SiteMetadata:
        if (index < CheckpointSiteMetadataDwords.size()) {
            return CheckpointSiteMetadataDwords[index];
        }
        break;
    case CheckpointRegisterBank::AuthenticatedIdentity:
        if (index < CheckpointIdentityDwords.size()) {
            return CheckpointIdentityDwords[index];
        }
        break;
    }
    return std::nullopt;
}

[[nodiscard]] inline u32 CheckpointStrideDwords(u32 allocated_vgprs) {
    const auto checked = GcnCheckpointLayout::Require(
        1, CheckpointScalarDwords, allocated_vgprs, 0);
    return static_cast<u32>(checked.stride_bytes / sizeof(u32));
}

// Host-only verifier for Vulkan-workgroup x-fastest logical slot mapping.
// It deliberately returns an offset, not a guest creation-order rank.
[[nodiscard]] inline std::optional<u32> CheckpointDwordOffset(
    u32 logical_slot, u32 allocated_vgprs, u32 bank, u32 index, u32 lane) {
    const auto relative = CheckpointRelativeDword(allocated_vgprs, bank, index, lane);
    if (!relative) {
        return std::nullopt;
    }
    const u64 stride = CheckpointStrideDwords(allocated_vgprs);
    const u64 word = u64(logical_slot) * stride + *relative;
    if (word > std::numeric_limits<u32>::max()) {
        return std::nullopt;
    }
    return static_cast<u32>(word);
}

} // namespace Shader::Gcn::OrderedGds
