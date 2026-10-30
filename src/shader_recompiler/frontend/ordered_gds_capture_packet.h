// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <bit>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <optional>
#include <span>
#include <stdexcept>
#include <utility>
#include <vector>

#include "shader_recompiler/frontend/ordered_gds_continuation.h"
#include "shader_recompiler/frontend/ordered_gds_legacy_packer.h"
#include "shader_recompiler/frontend/ordered_gds_sparse_epoch.h"

// ABI for a future GPU capture -> synchronized host GDS resolve -> GPU resume.
// This file does NOT dispatch Vulkan work or persist the guest shader state.
// A logical Vulkan wave slot cannot establish guest wave creation order.
namespace Shader::Gcn::OrderedGds {

inline constexpr u32 CapturePacketTag = 0x43534447u;  // GDSC
inline constexpr u32 ResumePacketTag = 0x52534447u;   // GDSR
inline constexpr u32 CaptureFlags = 0x3u;        // valid, ordered GDS
inline constexpr u32 CaptureBypassFlags = 0x1u;  // valid, wave completed without GDS
inline constexpr u32 ResumeFlags = 0x1u;   // value ready
inline constexpr u32 NoActiveLane = std::numeric_limits<u32>::max();
inline constexpr u64 InvalidCreationRank = std::numeric_limits<u64>::max();

// Fixed four-dword alignment compatible with an std430 storage buffer array.
// Untrusted device arrival order is not stored as a creation rank.
struct alignas(16) GpuCapturePacket {
    u32 tag{};
    u32 epoch{};
    u32 site_pc{};
    u32 encoded_control{};
    u32 logical_slot{};
    u32 runtime_m0{};
    u32 exec_lo{};
    u32 exec_hi{};
    u32 source_lane{};
    u32 payload{};
    u32 token_lo{};
    u32 token_hi{};
    u32 flags{};
    u32 reserved0{};
    u32 reserved1{};
    u32 reserved2{};
};
static_assert(sizeof(GpuCapturePacket) == 64);
static_assert(alignof(GpuCapturePacket) == 16);
static_assert(offsetof(GpuCapturePacket, logical_slot) == 16);
static_assert(offsetof(GpuCapturePacket, source_lane) == 32);
static_assert(offsetof(GpuCapturePacket, flags) == 48);

// GPU packet stores use the logical *storage slot*, never guest creation rank.
// Keep packet addressing separate from register/LDS checkpoint addressing.
inline constexpr u32 GpuCapturePacketDwords = sizeof(GpuCapturePacket) / sizeof(u32);
static_assert(GpuCapturePacketDwords == 16);
[[nodiscard]] inline std::optional<u32> GpuCapturePacketDwordOffset(
    u32 physical_wave_slot, u32 packet_word) {
    if (packet_word >= GpuCapturePacketDwords) {
        return std::nullopt;
    }
    const u64 offset = u64(physical_wave_slot) * GpuCapturePacketDwords + packet_word;
    if (offset > std::numeric_limits<u32>::max()) {
        return std::nullopt;
    }
    return static_cast<u32>(offset);
}


struct alignas(16) GpuResumePacket {
    u32 tag{};
    u32 epoch{};
    u32 site_pc{};
    u32 flags{};
    u32 logical_slot{};
    u32 previous{};
    u32 current{};
    u32 counter_index{};
    u32 token_lo{};
    u32 token_hi{};
    u32 creation_lo{};
    u32 creation_hi{};
    u32 reserved0{};
    u32 reserved1{};
    u32 reserved2{};
    u32 reserved3{};
};
static_assert(sizeof(GpuResumePacket) == 64);
static_assert(alignof(GpuResumePacket) == 16);
static_assert(offsetof(GpuResumePacket, logical_slot) == 16);
static_assert(offsetof(GpuResumePacket, token_lo) == 32);

// Must be populated by a proven guest-wave scheduler, NOT by GPU dispatch
// arrival times or by assuming Vulkan logical slots execute in slot order.
class VerifiedGuestCreationOrder {
public:
    static std::optional<VerifiedGuestCreationOrder> FromScheduler(
        std::span<const u64> guest_creation_rank_by_slot) {
        const auto n = guest_creation_rank_by_slot.size();
        if (n == 0 || n > 1'000'000) {
            return std::nullopt;
        }
        std::vector<u64> reverse(n, InvalidCreationRank);
        for (std::size_t slot = 0; slot < n; ++slot) {
            const auto rank = guest_creation_rank_by_slot[slot];
            if (rank >= n || reverse[rank] != InvalidCreationRank) {
                return std::nullopt;
            }
            reverse[rank] = slot;
        }
        return VerifiedGuestCreationOrder(
            std::vector<u64>{guest_creation_rank_by_slot.begin(),
                             guest_creation_rank_by_slot.end()},
            std::move(reverse));
    }

    // Legacy CIK compute DS_ORDERED_COUNT carries an *explicit* logicalWaveID
    // in live guest M0[12:0]. Unlike a Vulkan invocation/arrival index, this
    // value is the guest's ordered-walker identity. This strictly bounded
    // fast path works only when ALL created waves reach ordered count, the
    // 13-bit identities form the complete [0,N) permutation and the wave
    // crawler increment is zero. It MUST NOT manufacture positions for
    // completed/bypassed waves, graphics packer IDs, nonzero crawler strides,
    // or an epoch with missing or duplicate GPU capture packets.
    //
    // Only use for a compute shader whose live guest M0 bits come from the
    // translated shader. This does not prove the Vulkan shader can yet
    // checkpoint/re-enter or that counter initialization is known.
    static std::optional<VerifiedGuestCreationOrder> FromCompleteCikComputeM0(
        std::span<const GpuCapturePacket> captures, u32 expected_epoch) {
        constexpr std::size_t CikLogicalWaveLimit = 1u << 13;
        if (captures.empty() || captures.size() > CikLogicalWaveLimit) {
            return std::nullopt;
        }
        const std::size_t total = captures.size();
        std::vector<u64> rank_by_slot(total, InvalidCreationRank);
        std::vector<bool> seen_rank(total, false);
        for (const auto& packet : captures) {
            if (packet.tag != CapturePacketTag || packet.epoch != expected_epoch ||
                packet.flags != CaptureFlags || !packet.site_pc ||
                packet.logical_slot >= total ||
                rank_by_slot[packet.logical_slot] != InvalidCreationRank) {
                return std::nullopt;
            }
            const u32 wave_crawler_increment = (packet.runtime_m0 >> 13) & 7u;
            const u32 logical_wave_id = packet.runtime_m0 & 0x1FFFu;
            if (wave_crawler_increment != 0 || logical_wave_id >= total ||
                seen_rank[logical_wave_id]) {
                return std::nullopt;
            }
            seen_rank[logical_wave_id] = true;
            rank_by_slot[packet.logical_slot] = logical_wave_id;
        }
        // FromScheduler validates a complete permutation independently.
        return FromScheduler(rank_by_slot);
    }

    [[nodiscard]] u64 RankForSlot(u32 logical_slot) const {
        if (logical_slot >= ranks_.size()) {
            throw std::invalid_argument("ordered GDS capture slot outside guest creation map");
        }
        return ranks_[logical_slot];
    }
    [[nodiscard]] u32 SlotForRank(u64 rank) const {
        if (rank >= slots_.size()) {
            throw std::invalid_argument("ordered GDS resume rank outside creation map");
        }
        return static_cast<u32>(slots_[rank]);
    }
    [[nodiscard]] std::size_t Size() const noexcept { return ranks_.size(); }

private:
    VerifiedGuestCreationOrder(std::vector<u64> ranks, std::vector<u64> slots)
        : ranks_{std::move(ranks)}, slots_{std::move(slots)} {}
    std::vector<u64> ranks_;
    std::vector<u64> slots_;
};

inline CapturedWave DecodeGpuCapture(const GpuCapturePacket& packet, u32 expected_epoch,
                                     const VerifiedGuestCreationOrder& creation) {
    if (packet.tag != CapturePacketTag || packet.epoch != expected_epoch ||
        packet.flags != CaptureFlags || packet.encoded_control > 0xffffu ||
        packet.reserved0 || packet.reserved1 || packet.reserved2) {
        throw std::invalid_argument("invalid ordered GDS GPU capture header");
    }
    const u64 token = (u64(packet.token_hi) << 32) | packet.token_lo;
    if (!token) {
        throw std::invalid_argument("ordered GDS GPU capture requires continuation token");
    }
    const u64 guest_exec_mask = (u64(packet.exec_hi) << 32) | packet.exec_lo;
    if (guest_exec_mask == 0) {
        if (packet.source_lane != NoActiveLane || packet.payload != 0) {
            throw std::invalid_argument("inactive GDS wave must not fabricate lane/payload");
        }
    } else if (packet.source_lane != static_cast<u32>(std::countr_zero(guest_exec_mask))) {
        throw std::invalid_argument("invalid first active guest lane metadata");
    }
    // Legacy CIK uses count_bits(EXEC) for its dedicated ordered-count packer.
    // Source-lane metadata cannot determine the counter payload. In particular,
    // do not apply the later RDNA first-active-lane ADDR semantics on PS4 GCN.
    const u32 guest_cik_payload = CikOrderedCountIncrement(guest_exec_mask);
    if (packet.payload != guest_cik_payload) {
        throw std::invalid_argument("GPU CIK ordered-count payload disagrees with guest EXEC");
    }
    const u32 offset0 = packet.encoded_control & 0xffu;
    const u32 offset1 = (packet.encoded_control >> 8) & 0xffu;
    if ((offset0 & 3u) || (offset1 & 0xc0u) ||
        (offset1 & 3u) != 3u) {
        throw std::invalid_argument("unsupported GPU captured ordered GDS control");
    }
    const Descriptor instruction{
        .operation = Operation((offset1 >> 4) & 3u),
        .counter_index = offset0 >> 2,
        .wave_release = bool(offset1 & 1u),
        .wave_done = bool(offset1 & 2u),
        .gds = true,
        .addr_register = NoActiveLane, // not provided by GPU capture packet
        .destination_register = NoActiveLane,
        .raw_offset0 = offset0,
        .raw_offset1 = offset1,
    };
    const Request request{
        .creation_sequence = creation.RankForSlot(packet.logical_slot),
        .runtime_m0 = packet.runtime_m0,
        .payload = guest_cik_payload,
        .instruction = instruction,
        .has_valid_lane = guest_exec_mask != 0,
    };
    return CapturedWave{request, token, packet.site_pc};
}

inline GpuResumePacket EncodeGpuContinuation(const ReadyContinuation& ready,
                                              u32 epoch,
                                              const VerifiedGuestCreationOrder& creation) {
    if (!ready.continuation_token || !ready.site_pc) {
        throw std::invalid_argument("ordered GDS resume requires real token and site");
    }
    const u64 seq = ready.value.creation_sequence;
    return GpuResumePacket{
        .tag = ResumePacketTag,
        .epoch = epoch,
        .site_pc = *ready.site_pc,
        .flags = ResumeFlags,
        .logical_slot = creation.SlotForRank(seq),
        .previous = ready.value.previous,
        .current = ready.value.current,
        .counter_index = ready.value.key.index,
        .token_lo = u32(ready.continuation_token),
        .token_hi = u32(ready.continuation_token >> 32),
        .creation_lo = u32(seq),
        .creation_hi = u32(seq >> 32),
    };
}

// The caller owns the explicit GPU/host barrier and the dispatch lifetime.
// No device execution or wait occurs here, and no GDS counter is guessed.
inline void StageGpuCaptures(ContinuationHandoff& host, u32 epoch,
                             std::span<const GpuCapturePacket> packets,
                             const VerifiedGuestCreationOrder& creation) {
    if (packets.size() != creation.Size()) {
        throw std::invalid_argument("ordered GDS requires full GPU capture packet coverage");
    }
    std::vector<CapturedWave> decoded;
    decoded.reserve(packets.size());
    std::vector<bool> seen_slots(creation.Size(), false);
    std::vector<u64> tokens;
    tokens.reserve(packets.size());
    for (const auto& packet : packets) {
        // Validate the entire GPU-produced set before touching the host
        // collector: a repeated slot would otherwise conceal a missing wave.
        const auto wave = DecodeGpuCapture(packet, epoch, creation);
        if (seen_slots[packet.logical_slot]) {
            throw std::invalid_argument("duplicate ordered GDS GPU capture slot");
        }
        seen_slots[packet.logical_slot] = true;
        tokens.push_back(wave.continuation_token);
        decoded.push_back(wave);
    }
    if (std::find(seen_slots.begin(), seen_slots.end(), false) != seen_slots.end()) {
        throw std::invalid_argument("incomplete ordered GDS GPU slot coverage");
    }
    std::sort(tokens.begin(), tokens.end());
    if (std::adjacent_find(tokens.begin(), tokens.end()) != tokens.end()) {
        throw std::invalid_argument("duplicate ordered GDS GPU continuation token");
    }
    for (const auto& wave : decoded) {
        host.Capture(epoch, wave);
    }
}

inline std::vector<GpuResumePacket> ResolveGpuCaptures(
    ContinuationHandoff& host, u32 epoch,
    const VerifiedGuestCreationOrder& creation) {
    const auto ready = host.Resolve(epoch);
    if (ready.size() != creation.Size()) {
        throw std::logic_error("ordered GDS capture/resume wave-count mismatch");
    }
    std::vector<GpuResumePacket> output(creation.Size());
    for (const auto& item : ready) {
        const auto packet = EncodeGpuContinuation(item, epoch, creation);
        output[packet.logical_slot] = packet;
    }
    return output;
}


// A completed wave that never executes DS_ORDERED_COUNT is NOT an inactive
// GDS operation. A real GPU terminal-path capture must explicitly mark it;
// static CFG may show a possible bypass but cannot invent a completed wave.
inline u64 DecodeCompletedGpuWave(const GpuCapturePacket& packet, u32 expected_epoch,
                                  const VerifiedGuestCreationOrder& creation) {
    if (packet.tag != CapturePacketTag || packet.epoch != expected_epoch ||
        packet.flags != CaptureBypassFlags || packet.site_pc != NoActiveLane ||
        packet.encoded_control || packet.runtime_m0 || packet.exec_lo ||
        packet.exec_hi || packet.payload || packet.source_lane != NoActiveLane ||
        packet.token_lo || packet.token_hi || packet.reserved0 ||
        packet.reserved1 || packet.reserved2) {
        throw std::invalid_argument("invalid GDS bypass completion packet");
    }
    return creation.RankForSlot(packet.logical_slot);
}

// Overload for verified guest waves that may EXIT without ordered-GDS. The
// older ContinuationHandoff overload remains available for proven full-wave
// ordered epochs; neither version guesses guest wave rank from Vulkan arrival.
inline void StageGpuCaptures(SparseOrderedGdsEpoch& host, u32 epoch,
                             std::span<const GpuCapturePacket> packets,
                             const VerifiedGuestCreationOrder& creation) {
    if (!host.Empty() || packets.size() != creation.Size()) {
        throw std::invalid_argument(
            "sparse ordered GDS requires an empty epoch and full wave coverage");
    }
    struct Prepared {
        u64 rank;
        std::optional<CapturedWave> captured;
    };
    std::vector<Prepared> staged;
    staged.reserve(packets.size());
    std::vector<bool> seen_slots(creation.Size(), false);
    std::vector<u64> active_tokens;
    for (const auto& packet : packets) {
        if (packet.logical_slot >= creation.Size() || seen_slots[packet.logical_slot]) {
            throw std::invalid_argument("invalid or duplicate sparse GPU wave slot");
        }
        seen_slots[packet.logical_slot] = true;
        if (packet.flags == CaptureBypassFlags) {
            staged.push_back({DecodeCompletedGpuWave(packet, epoch, creation), std::nullopt});
        } else {
            auto captured = DecodeGpuCapture(packet, epoch, creation);
            if (!host.AllowsSite(*captured.site_pc)) {
                throw std::invalid_argument("sparse GPU capture at unknown guest site PC");
            }
            active_tokens.push_back(captured.continuation_token);
            staged.push_back({captured.request.creation_sequence, std::move(captured)});
        }
    }
    if (std::find(seen_slots.begin(), seen_slots.end(), false) != seen_slots.end()) {
        throw std::invalid_argument("missing sparse ordered GDS GPU wave coverage");
    }
    std::sort(active_tokens.begin(), active_tokens.end());
    if (std::adjacent_find(active_tokens.begin(), active_tokens.end()) != active_tokens.end()) {
        throw std::invalid_argument("duplicate sparse GPU continuation token");
    }
    // All packet and site validation completed before host state is mutated.
    for (auto& item : staged) {
        if (item.captured) {
            host.Capture(std::move(*item.captured));
        } else {
            host.CompleteWithoutOrdered(item.rank);
        }
    }
}

inline std::vector<GpuResumePacket> ResolveGpuCaptures(
    SparseOrderedGdsEpoch& host, u32 epoch,
    const VerifiedGuestCreationOrder& creation) {
    const auto ready = host.Resolve();
    // Every logical wave slot receives a deterministic record, but a
    // completed-without-GDS slot has ResumeFlags=0 and no continuation.
    std::vector<GpuResumePacket> output(creation.Size());
    for (std::size_t i = 0; i < output.size(); ++i) {
        output[i] = GpuResumePacket{
            .tag = ResumePacketTag,
            .epoch = epoch,
            .site_pc = NoActiveLane,
            .flags = 0,
            .logical_slot = static_cast<u32>(i),
        };
    }
    for (const auto& item : ready) {
        const auto packet = EncodeGpuContinuation(item, epoch, creation);
        output[packet.logical_slot] = packet;
    }
    return output;
}

} // namespace Shader::Gcn::OrderedGds
