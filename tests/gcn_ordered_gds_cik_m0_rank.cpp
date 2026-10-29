// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Validates guest-CIK *logical wave IDs*, not Vulkan execution order.
#include "shader_recompiler/frontend/ordered_gds_capture_packet.h"
#include <array>
#include <bit>
#include <cassert>
#include <iostream>
#include <vector>

using namespace Shader::Gcn::OrderedGds;

static GpuCapturePacket GuestPacket(u32 slot, u32 wave_id, u32 site_pc,
                                    u32 control, u64 guest_exec, u64 token) {
    GpuCapturePacket p{};
    p.tag = CapturePacketTag;
    p.epoch = 17;
    p.site_pc = site_pc;
    p.encoded_control = control;
    p.logical_slot = slot;
    p.runtime_m0 = (0x20u << 16) | wave_id;
    p.exec_lo = u32(guest_exec);
    p.exec_hi = u32(guest_exec >> 32);
    p.source_lane = guest_exec ? u32(std::countr_zero(guest_exec)) : NoActiveLane;
    p.payload = CikOrderedCountIncrement(guest_exec);
    p.token_lo = u32(token);
    p.token_hi = u32(token >> 32);
    p.flags = CaptureFlags;
    return p;
}

int main() {
    constexpr std::array<u32, 2> sites{0x4158, 0x41d4};
    const std::array<GpuCapturePacket, 4> received{{
        GuestPacket(3, 1, sites[1], 0x0300, u64{1} << 63, 0x100000002ULL),
        GuestPacket(0, 2, sites[0], 0x1300, 0, 0x100000003ULL),
        GuestPacket(2, 3, sites[1], 0x0300,
                    (u64{1} << 7) | (u64{1} << 63), 0x100000004ULL),
        GuestPacket(1, 0, sites[0], 0x1300, 1, 0x100000001ULL),
    }};
    const auto order = VerifiedGuestCreationOrder::FromCompleteCikComputeM0(received, 17);
    assert(order && order->Size() == 4);
    assert(order->RankForSlot(0) == 2);
    assert(order->RankForSlot(1) == 0);
    assert(order->RankForSlot(2) == 3);
    assert(order->RankForSlot(3) == 1);
    assert(order->SlotForRank(0) == 1);

    // A real guest rank 0 Exchange precedes the guest rank 1 Add regardless
    // of physical arrival, logical Vulkan slots or the site taken.
    SparseOrderedGdsEpoch host(4, {{{0x20, 0}, 100}}, sites);
    StageGpuCaptures(host, 17, received, *order);
    const auto resume = ResolveGpuCaptures(host, 17, *order);
    assert(resume.size() == 4);
    assert(resume[1].previous == 100 && resume[1].current == 1);
    assert(resume[3].previous == 1 && resume[3].current == 2);
    assert(resume[0].previous == 2 && resume[0].current == 2);
    assert(resume[2].previous == 2 && resume[2].current == 4);
    assert(host.CounterSnapshot().at({0x20, 0}) == 4);

    auto bad = received;
    bad[0].runtime_m0 = (0x20u << 16) | 0u; // duplicate guest wave ID
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(bad, 17));
    bad = received;
    bad[0].runtime_m0 = (0x20u << 16) | 4u; // missing/out-of-range ID
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(bad, 17));
    bad = received;
    bad[0].runtime_m0 |= 1u << 13; // crawler increment not modeled
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(bad, 17));
    bad = received;
    bad[0].flags = CaptureBypassFlags; // bypass wave has no proven M0 identity
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(bad, 17));
    bad = received;
    bad[0].epoch = 18; // stale capture
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(bad, 17));
    bad = received;
    bad[0].logical_slot = 0; // duplicate physical slot must not hide a wave
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(bad, 17));
    bad = received;
    bad[0].tag = 0; // malformed/unwritten packet
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(bad, 17));
    bad = received;
    bad[0].site_pc = 0; // missing ordered-count site
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(bad, 17));
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(
        std::span<const GpuCapturePacket>{}, 17));
    std::vector<GpuCapturePacket> too_many((1u << 13) + 1);
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(too_many, 17));

    std::cout << "ORDERED_GDS_CIK_M0_GUEST_ORDER=PASS "
                 "guest_m0_logical_wave_ids=PASS host_permutation=PASS "
                 "mixed_sites=PASS exchange_add=PASS "
                 "bypass_duplicate_crawler_stale_rejected=PASS "
                 "gpu_dispatch_not_replayed=1 NO_GPU_WORK\n";
}
