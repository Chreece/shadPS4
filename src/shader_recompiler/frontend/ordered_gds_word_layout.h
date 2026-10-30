// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

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
};

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
