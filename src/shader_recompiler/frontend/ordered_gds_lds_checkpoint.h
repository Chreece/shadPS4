// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <optional>
#include <span>
#include <stdexcept>
#include <vector>

namespace Shader::Gcn::OrderedGds {

// Workgroup LDS is independent of the per-wave SGPR/VGPR checkpoint.
// It must be saved AFTER every wave in the workgroup reaches a compiler-
// proven quiescent barrier. Capturing one wave's LDS at a divergent GDS
// instruction is NOT safe: another wave may still be writing to it.
// No current shader emitter produces this checkpoint, so keep the actual
// ordered-GDS guest opcode fail-closed until a verified writer exists.
inline constexpr std::uint32_t LdsCheckpointTag = 0x53444C47u; // GLDS
inline constexpr std::uint32_t LdsCheckpointAbiVersion = 1u;
inline constexpr std::uint32_t LdsCheckpointQuiescentFlag = 1u;
inline constexpr std::uint64_t MaxLdsCheckpointBytes = 256ull * 1024ull * 1024ull;

struct alignas(16) GpuGcnWorkgroupLdsCheckpoint {
    std::uint32_t tag{};
    std::uint32_t epoch{};
    std::uint32_t workgroup_slot{};
    std::uint32_t lds_bytes{};
    std::uint32_t waves_per_group{};
    std::uint32_t waves_at_barrier{};
    std::uint32_t flags{};
    std::uint32_t abi_version{};
    std::uint32_t reserved0{};
    std::uint32_t reserved1{};
    std::uint32_t reserved2{};
    std::uint32_t reserved3{};
};
static_assert(sizeof(GpuGcnWorkgroupLdsCheckpoint) == 48);
static_assert(alignof(GpuGcnWorkgroupLdsCheckpoint) == 16);

struct GcnLdsCheckpointLayout {
    std::uint32_t workgroups{};
    std::uint32_t waves_per_group{};
    std::uint32_t lds_bytes{};
    std::uint64_t stride_bytes{};
    std::uint64_t total_bytes{};

    [[nodiscard]] static GcnLdsCheckpointLayout Require(
        std::uint32_t workgroups, std::uint32_t waves_per_group,
        std::uint32_t lds_bytes) {
        // A workgroup can contain 1..16 GCN wave64s; 64KiB is the
        // hardware LDS upper bound and all GPU copies need DWORD alignment.
        if (!workgroups || workgroups > 1'000'000u ||
            !waves_per_group || waves_per_group > 16 ||
            !lds_bytes || lds_bytes > 65536u || (lds_bytes & 3u)) {
            throw std::invalid_argument("unsupported guest LDS checkpoint dimensions");
        }
        if (std::uint64_t(workgroups) * waves_per_group > 1'000'000ull) {
            throw std::invalid_argument("guest LDS checkpoint wave count exceeds limit");
        }
        const std::uint64_t unaligned =
            sizeof(GpuGcnWorkgroupLdsCheckpoint) + std::uint64_t(lds_bytes);
        const std::uint64_t stride = (unaligned + 15u) & ~std::uint64_t{15};
        if (stride > MaxLdsCheckpointBytes / workgroups) {
            throw std::invalid_argument("guest LDS checkpoint exceeds bounded memory budget");
        }
        return {workgroups, waves_per_group, lds_bytes,
                stride, stride * workgroups};
    }
};

// A guest workgroup with NO LDS has nothing to checkpoint or restore.
// Requiring a 48-byte LDS header for zero-byte shared memory is impossible
// under GcnLdsCheckpointLayout::Require() and wrongly blocks a valid
// compute dispatch. An absent LDS source must be represented by an ALL-ZERO
// layout, not by a fabricated 48-byte checkpoint or a guessed workgroup.
// Nonzero LDS is never optional: all waves must reach a verified barrier.
[[nodiscard]] inline std::optional<GcnLdsCheckpointLayout>
RequireOptionalGcnLdsCheckpoint(const GcnLdsCheckpointLayout& candidate,
                                std::uint32_t guest_shared_lds_bytes,
                                std::uint64_t guest_created_waves) {
    if (guest_created_waves == 0 || guest_created_waves > 1'000'000ull) {
        throw std::invalid_argument("unsupported guest LDS wave count");
    }
    if (guest_shared_lds_bytes == 0) {
        if (candidate.workgroups || candidate.waves_per_group ||
            candidate.lds_bytes || candidate.stride_bytes || candidate.total_bytes) {
            throw std::invalid_argument(
                "zero-LDS guest cannot claim an LDS checkpoint buffer");
        }
        return std::nullopt;
    }
    const auto verified = GcnLdsCheckpointLayout::Require(
        candidate.workgroups, candidate.waves_per_group, candidate.lds_bytes);
    if (verified.lds_bytes != guest_shared_lds_bytes ||
        verified.stride_bytes != candidate.stride_bytes ||
        verified.total_bytes != candidate.total_bytes ||
        std::uint64_t(verified.workgroups) * verified.waves_per_group !=
            guest_created_waves) {
        throw std::invalid_argument(
            "ordered GDS LDS layout is not guest dispatch layout");
    }
    return verified;
}

inline void RequireCompleteGcnLdsCheckpoints(
    std::span<const std::byte> snapshot, const GcnLdsCheckpointLayout& layout,
    std::uint32_t expected_epoch) {
    const auto checked = GcnLdsCheckpointLayout::Require(
        layout.workgroups, layout.waves_per_group, layout.lds_bytes);
    if (checked.stride_bytes != layout.stride_bytes ||
        checked.total_bytes != layout.total_bytes ||
        snapshot.size() != layout.total_bytes) {
        throw std::invalid_argument("guest LDS checkpoint layout or size mismatch");
    }
    for (std::uint32_t group = 0; group < layout.workgroups; ++group) {
        const auto offset = std::uint64_t(group) * layout.stride_bytes;
        GpuGcnWorkgroupLdsCheckpoint header{};
        std::memcpy(&header, snapshot.data() + offset, sizeof(header));
        if (header.tag != LdsCheckpointTag ||
            header.abi_version != LdsCheckpointAbiVersion ||
            header.epoch != expected_epoch ||
            header.workgroup_slot != group ||
            header.lds_bytes != layout.lds_bytes ||
            header.waves_per_group != layout.waves_per_group ||
            header.waves_at_barrier != layout.waves_per_group ||
            header.flags != LdsCheckpointQuiescentFlag ||
            header.reserved0 || header.reserved1 ||
            header.reserved2 || header.reserved3) {
            throw std::invalid_argument(
                "guest LDS checkpoint has missing or inconsistent workgroup barrier");
        }
    }
}

} // namespace Shader::Gcn::OrderedGds
