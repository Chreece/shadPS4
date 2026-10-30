// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
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
inline constexpr u32 GcnCheckpointAbiVersion = 2u;
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
    // VCC and SCC are independent architectural wave state. Saving only
    // SGPR/VGPR and EXEC is NOT enough to resume guest branch conditions.
    u32 vcc_lo{};
    u32 vcc_hi{};
    u32 scc{};
    u32 abi_version{};
};
static_assert(sizeof(GpuGcnWaveCheckpoint) == 80);
static_assert(alignof(GpuGcnWaveCheckpoint) == 16);

// SGPR payload: scalar_words DWORDs. VGPR payload: vector_registers * 64
// DWORDs, laid out VGPR-major and lane-minor, beginning directly after SGPRs.
// LDS is workgroup-shared and needs separate checkpointing: refuse nonzero
// LDS for now rather than resuming with stale shared memory.
// Independently decoded from GCN instruction bytes, not supplied by a GPU
// capture packet. The VDST and encoded control are part of the immutable
// shader program at that instruction PC.
struct VerifiedGcnOrderedSite {
    u32 site_pc{};
    u32 destination_vgpr{};
    u32 encoded_control{};
};

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
    const GcnCheckpointLayout& layout, u32 expected_epoch,
    std::span<const VerifiedGcnOrderedSite> verified_sites) {
    if (!layout.waves || captures.size() != layout.waves ||
        state_bytes.size() != layout.total_bytes || verified_sites.empty()) {
        throw std::invalid_argument("guest wave checkpoint/capture coverage mismatch");
    }
    std::vector<bool> seen(layout.waves, false);
    for (const auto& capture : captures) {
        if ((capture.flags != CaptureFlags && capture.flags != CaptureBypassFlags) ||
            capture.logical_slot >= layout.waves ||
            seen[capture.logical_slot] || capture.tag != CapturePacketTag ||
            capture.epoch != expected_epoch || !capture.site_pc) {
            throw std::invalid_argument("invalid or duplicate guest checkpoint capture slot");
        }
        const auto match = std::find_if(verified_sites.begin(), verified_sites.end(),
                                        [&](const auto& site) {
                                            return site.site_pc == capture.site_pc;
                                        });
        if (match == verified_sites.end() ||
            match->encoded_control != capture.encoded_control ||
            match->destination_vgpr >= layout.vector_registers ||
            std::count_if(verified_sites.begin(), verified_sites.end(),
                          [&](const auto& site) { return site.site_pc == capture.site_pc; }) != 1) {
            throw std::invalid_argument("guest checkpoint site is not uniquely decoded");
        }
        seen[capture.logical_slot] = true;
        if (capture.flags == CaptureBypassFlags) {
            // A real S_ENDPGM wave has ALREADY completed, without touching
            // DS_ORDERED_COUNT. It cannot possess a pre-DS register image,
            // decoded VDST, checkpoint PC or continuation token. Authenticate
            // the canonical packet and do NOT read its unwritten SSBO slot.
            // Full host-issued slot/token authority is checked separately
            // by RequireCaptureTokenAuthority before host resolution.
            if (!IsCanonicalGcnTerminalBypass(capture, expected_epoch)) {
                throw std::invalid_argument("forged GCN terminal-bypass checkpoint slot");
            }
            continue;
        }
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
            checkpoint.scc > 1 ||
            checkpoint.abi_version != GcnCheckpointAbiVersion ||
            checkpoint.scalar_words != layout.scalar_words ||
            checkpoint.vector_registers != layout.vector_registers ||
            checkpoint.lane_count != GcnWaveLanes ||
            checkpoint.destination_vgpr != match->destination_vgpr) {
            throw std::invalid_argument("guest GCN wave checkpoint is stale or incomplete");
        }
    }
}

// After a genuine GPU capture and host-ordered resolution, verify that
// every resume record still identifies the SAME checkpointed wave. Never
// infer physical invocation order from the resume-packet array position.
// This is a transport/identity guard, not execution of guest instructions.
inline void RequireResolvedGcnResumeHandoff(
    std::span<const GpuResumePacket> resumes,
    std::span<const GpuCapturePacket> captures,
    const VerifiedGuestCreationOrder& order, u32 expected_epoch) {
    if (resumes.empty() || resumes.size() != captures.size() ||
        resumes.size() != order.Size()) {
        throw std::invalid_argument("GCN resume/capture wave count mismatch");
    }
    std::vector<bool> seen(captures.size(), false);
    for (const auto& capture : captures) {
        if (capture.logical_slot >= captures.size() ||
            capture.tag != CapturePacketTag ||
            (capture.flags != CaptureFlags && capture.flags != CaptureBypassFlags) ||
            capture.epoch != expected_epoch || seen[capture.logical_slot]) {
            throw std::invalid_argument("invalid guest capture for resumption");
        }
        seen[capture.logical_slot] = true;
        const auto& resume = resumes[capture.logical_slot];
        if (capture.flags == CaptureBypassFlags) {
            // Sparse resolution supplies a NON-runnable marker for an S_ENDPGM
            // wave. Reject any attempt to turn it into a guest continuation.
            if (!IsCanonicalGcnTerminalBypass(capture, expected_epoch) ||
                resume.tag != ResumePacketTag ||
                resume.epoch != expected_epoch ||
                resume.site_pc != NoActiveLane ||
                resume.flags != 0 ||
                resume.logical_slot != capture.logical_slot ||
                resume.previous || resume.current || resume.counter_index ||
                resume.token_lo || resume.token_hi ||
                resume.creation_lo || resume.creation_hi ||
                resume.reserved0 || resume.reserved1 ||
                resume.reserved2 || resume.reserved3) {
                throw std::invalid_argument("forged GCN terminal-bypass resume marker");
            }
            continue;
        }
        const u64 guest_rank = (u64(resume.creation_hi) << 32) | resume.creation_lo;
        if (resume.tag != ResumePacketTag || resume.epoch != expected_epoch ||
            resume.flags != ResumeFlags || resume.logical_slot != capture.logical_slot ||
            resume.site_pc != capture.site_pc ||
            resume.counter_index != ((capture.encoded_control & 0xffu) >> 2u) ||
            resume.token_lo != capture.token_lo ||
            resume.token_hi != capture.token_hi ||
            guest_rank != order.RankForSlot(capture.logical_slot) ||
            resume.reserved0 || resume.reserved1 || resume.reserved2 ||
            resume.reserved3) {
            throw std::invalid_argument("resolved guest resume does not match captured wave");
        }
    }
}

} // namespace Shader::Gcn::OrderedGds
