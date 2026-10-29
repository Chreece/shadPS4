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
#include <vector>

#include "shader_recompiler/frontend/ordered_gds_continuation.h"

// ABI for a future GPU capture -> synchronized host GDS resolve -> GPU resume.
// This file does NOT dispatch Vulkan work or persist the guest shader state.
// A logical Vulkan wave slot cannot establish guest wave creation order.
namespace Shader::Gcn::OrderedGds {

inline constexpr u32 CapturePacketTag = 0x43534447u;  // GDSC
inline constexpr u32 ResumePacketTag = 0x52534447u;   // GDSR
inline constexpr u32 CaptureFlags = 0x3u;  // valid, GDS
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
        throw std::invalid_argument("GDS payload not from first active guest lane");
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
        .payload = packet.payload,
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

} // namespace Shader::Gcn::OrderedGds
