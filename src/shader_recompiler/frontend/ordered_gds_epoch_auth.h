// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstddef>
#include <cstdint>
#include <limits>
#include <span>
#include <stdexcept>
#include <unordered_set>
#include <vector>

#include "shader_recompiler/frontend/ordered_gds_capture_packet.h"

namespace Shader::Gcn::OrderedGds {

// These records belong to a trusted host dispatch epoch, not to guest GDS,
// arbitrary Vulkan arrival order, or unverified shader-packet contents.
// The host scheduler MUST supply a real unique continuation token for EACH
// created wave. No code here guesses token/seed/wave creation rank.
inline constexpr u32 GcnEpochAuthTag = 0x41444743u; // CGDA
inline constexpr u32 GcnEpochAuthFlags = 1u;
inline constexpr u32 GcnEpochAuthDwords = 8u;
inline constexpr u32 GcnEpochAuthMaximumWaves = 1u << 13;

struct alignas(16) GpuGcnOrderedEpochAuth {
    u32 tag{};
    u32 epoch{};
    u32 physical_slot{};
    u32 wave_slots{};
    u32 token_lo{};
    u32 token_hi{};
    u32 flags{};
    u32 reserved{};
};
static_assert(sizeof(GpuGcnOrderedEpochAuth) == 32);
static_assert(alignof(GpuGcnOrderedEpochAuth) == 16);
static_assert(offsetof(GpuGcnOrderedEpochAuth, token_lo) == 16);
static_assert(offsetof(GpuGcnOrderedEpochAuth, flags) == 24);

// This matches the GPU SPIR-V capture-stamp predicate. A valid stamp means
// only that a full-wave, zero-LDS snapshot is associated with an explicitly
// supplied host epoch/token record; it does NOT establish true guest creation
// order, CIK packer initialization or resumability. The shader compiler must
// reproduce ALL these checks before writing GcnCheckpointTag/CapturePacketTag.
[[nodiscard]] constexpr bool IsAuthorizedGcnCaptureStamp(
    const GpuGcnOrderedEpochAuth& auth, u32 physical_slot,
    bool complete_wave64, bool zero_shared_lds) noexcept {
    return complete_wave64 && zero_shared_lds &&
           auth.tag == GcnEpochAuthTag && auth.epoch != 0 &&
           auth.physical_slot == physical_slot &&
           auth.wave_slots != 0 && auth.wave_slots <= GcnEpochAuthMaximumWaves &&
           physical_slot < auth.wave_slots &&
           (auth.token_lo != 0 || auth.token_hi != 0) &&
           auth.flags == GcnEpochAuthFlags && auth.reserved == 0;
}

// This checks an explicitly PROVIDED complete token assignment. It does not
// choose continuation identities, initialize CIK packers, validate LDS, or
// make a guest shader resumable. A caller must establish cross-epoch token
// freshness and actual guest wave creation order separately.
[[nodiscard]] inline std::vector<GpuGcnOrderedEpochAuth> RequireGcnEpochAuth(
    u32 wave_slots, u32 epoch, std::span<const u64> scheduler_tokens_by_slot) {
    if (!epoch || !wave_slots || wave_slots > GcnEpochAuthMaximumWaves ||
        scheduler_tokens_by_slot.size() != wave_slots) {
        throw std::invalid_argument("guest GDS epoch or scheduler token coverage invalid");
    }
    std::unordered_set<u64> seen;
    seen.reserve(wave_slots);
    std::vector<GpuGcnOrderedEpochAuth> out;
    out.reserve(wave_slots);
    for (u32 slot = 0; slot < wave_slots; ++slot) {
        const u64 token = scheduler_tokens_by_slot[slot];
        if (!token || !seen.insert(token).second) {
            throw std::invalid_argument("missing or duplicate guest continuation token");
        }
        out.push_back({
            .tag = GcnEpochAuthTag,
            .epoch = epoch,
            .physical_slot = slot,
            .wave_slots = wave_slots,
            .token_lo = static_cast<u32>(token),
            .token_hi = static_cast<u32>(token >> 32),
            .flags = GcnEpochAuthFlags,
        });
    }
    return out;
}

// The actual Rasterizer owns ONE issuer for its lifetime. Tickets identify
// physical guest wave *slots* of a specific, newly accepted dispatch; they
// never encode or assume guest wave creation rank, guest M0, or GPU arrival
// order. The host still has to prove a complete CIK creation permutation,
// dedicated counter seed and executable guest continuation separately.
//
// Failed Vulkan uploads consume their issued tickets: they cannot be reused
// as a stale authorization on another epoch or buffer owner. Saturation is
// fail-closed rather than permitting 32-bit epoch or 64-bit token rollover.
struct IssuedGcnOrderedEpoch {
    u32 epoch{};
    std::vector<u64> tokens_by_slot;
};

class GcnOrderedGdsEpochIssuer {
public:
    GcnOrderedGdsEpochIssuer() = default;
    GcnOrderedGdsEpochIssuer(const GcnOrderedGdsEpochIssuer&) = delete;
    GcnOrderedGdsEpochIssuer& operator=(const GcnOrderedGdsEpochIssuer&) = delete;
    GcnOrderedGdsEpochIssuer(GcnOrderedGdsEpochIssuer&&) = delete;
    GcnOrderedGdsEpochIssuer& operator=(GcnOrderedGdsEpochIssuer&&) = delete;

    [[nodiscard]] IssuedGcnOrderedEpoch Issue(u32 actual_wave_slots) {
        if (!actual_wave_slots || actual_wave_slots > GcnEpochAuthMaximumWaves ||
            next_epoch_ == 0 || next_token_ == 0 ||
            u64(actual_wave_slots) > std::numeric_limits<u64>::max() - next_token_) {
            throw std::invalid_argument("ordered GDS host epoch/ticket space exhausted or invalid");
        }
        IssuedGcnOrderedEpoch issued;
        issued.epoch = next_epoch_;
        issued.tokens_by_slot.reserve(actual_wave_slots);
        for (u32 slot = 0; slot < actual_wave_slots; ++slot) {
            issued.tokens_by_slot.push_back(next_token_ + slot);
        }
        // Commit the issuer state only once all bounded allocations succeed.
        ++next_epoch_;
        next_token_ += actual_wave_slots;
        return issued;
    }

private:
    u32 next_epoch_{1};
    u64 next_token_{1};
};

// Host-side cross-check AFTER a complete real GPU checkpoint: a packet cannot
// assert an arbitrary token by merely writing a plausible tag. This is not a
// replacement for the full register/LDS/epoch/guest-ISA validation.
inline void RequireCaptureTokenAuthority(
    std::span<const GpuCapturePacket> packets,
    std::span<const GpuGcnOrderedEpochAuth> auth,
    u32 epoch) {
    if (packets.empty() || packets.size() != auth.size() || !epoch) {
        throw std::invalid_argument("GPU capture does not cover the authenticated epoch");
    }
    std::vector<bool> observed(auth.size(), false);
    for (const auto& p : packets) {
        if (p.logical_slot >= auth.size() || observed[p.logical_slot] ||
            p.tag != CapturePacketTag || p.flags != CaptureFlags ||
            p.epoch != epoch) {
            throw std::invalid_argument("unverified or duplicate GPU capture wave packet");
        }
        observed[p.logical_slot] = true;
        const auto& record = auth[p.logical_slot];
        if (record.tag != GcnEpochAuthTag || record.epoch != epoch ||
            record.physical_slot != p.logical_slot ||
            record.wave_slots != auth.size() ||
            record.flags != GcnEpochAuthFlags || record.reserved ||
            (record.token_lo == 0 && record.token_hi == 0) ||
            p.token_lo != record.token_lo || p.token_hi != record.token_hi) {
            throw std::invalid_argument("GPU continuation token or dispatch epoch disagrees");
        }
    }
}

} // namespace Shader::Gcn::OrderedGds
