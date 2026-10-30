// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>
#include <stdexcept>
#include <vector>

#include "shader_recompiler/frontend/ordered_gds_checkpoint.h"

namespace Shader::Gcn::OrderedGds {

// Convert an already-authenticated PRE-DS live GPU checkpoint into the
// POST-DS register state the eventual second-pass GCN shader must restore.
// The old counter is the actual verified host packer result, NOT a guess
// from raw GDS RAM, an unordered GPU atomic, or Vulkan arrival order.
//
// This helper never modifies the captured source or inactive guest lanes.
// It patches only the independently decoded VDST in all EXEC-active lanes;
// untouched SGPR, VCC, SCC, M0, EXEC, PC, other VGPRs and inactive VDST lanes
// remain bit-for-bit identical. The guest resumes at next_guest_pc, not PC
// of the ordered instruction, to avoid replaying earlier guest side effects.
//
// Caller MUST have authenticated every capture against the host scheduler
// token table and derived all GpuResumePacket.previous values from a
// verified dedicated CIK ordered counter seed. The validation here checks
// complete checkpoint/response identity, not the external seed provenance.
// This does NOT itself launch or implement the guest continuation shader.
[[nodiscard]] inline std::vector<std::byte> MaterializeGcnPostOrderedCountState(
    std::span<const std::byte> pre_ds_state,
    std::span<const GpuCapturePacket> captures,
    std::span<const GpuResumePacket> resumes,
    const GcnCheckpointLayout& layout, u32 epoch,
    std::span<const VerifiedGcnOrderedSite> decoded_sites,
    const VerifiedGuestCreationOrder& guest_creation) {
    RequireCompleteGcnWaveCheckpoints(
        pre_ds_state, captures, layout, epoch, decoded_sites);
    RequireResolvedGcnResumeHandoff(
        resumes, captures, guest_creation, epoch);

    std::vector<std::byte> post(pre_ds_state.begin(), pre_ds_state.end());
    for (const auto& capture : captures) {
        // The validator above already established unique site identity,
        // slot range and an exact match to decoded destination metadata.
        const auto match = std::find_if(
            decoded_sites.begin(), decoded_sites.end(),
            [&](const VerifiedGcnOrderedSite& site) {
                return site.site_pc == capture.site_pc;
            });
        if (match == decoded_sites.end() ||
            match->destination_vgpr >= layout.vector_registers) {
            throw std::invalid_argument(
                "GCN resume cannot locate decoded destination VGPR");
        }
        const auto& result = resumes[capture.logical_slot];
        const u64 exec = (u64(capture.exec_hi) << 32) | capture.exec_lo;
        const u64 wave_base = u64(capture.logical_slot) * layout.stride_bytes;
        const u64 word_base =
            wave_base + sizeof(GpuGcnWaveCheckpoint) +
            (u64(layout.scalar_words) + u64(match->destination_vgpr) * GcnWaveLanes)
                * sizeof(u32);
        for (u32 lane = 0; lane < GcnWaveLanes; ++lane) {
            if (((exec >> lane) & 1u) == 0) {
                continue;
            }
            const u64 byte_offset = word_base + u64(lane) * sizeof(u32);
            if (byte_offset > post.size() ||
                sizeof(u32) > post.size() - byte_offset ||
                byte_offset - wave_base >= layout.stride_bytes ||
                layout.stride_bytes - (byte_offset - wave_base) < sizeof(u32)) {
                throw std::invalid_argument(
                    "resolved GCN VDST write outside original wave checkpoint");
            }
            std::memcpy(post.data() + byte_offset,
                        &result.previous, sizeof(result.previous));
        }
    }
    return post;
}

} // namespace Shader::Gcn::OrderedGds
