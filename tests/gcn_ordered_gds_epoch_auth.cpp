// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "shader_recompiler/frontend/ordered_gds_epoch_auth.h"

#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <stdexcept>

using namespace Shader::Gcn::OrderedGds;
template<class F> bool Reject(F&& f) {
    try { f(); } catch (const std::invalid_argument&) { return true; }
    return false;
}

int main() {
    static_assert(sizeof(GpuGcnOrderedEpochAuth) == 32);
    static_assert(offsetof(GpuGcnOrderedEpochAuth, token_lo) == 16);
    static_assert(offsetof(GpuGcnOrderedEpochAuth, flags) == 24);
    constexpr u32 Epoch = 71;
    constexpr std::array<u64,4> scheduler_tokens{
        0x1234567800000001ULL, 0x1234567800000009ULL,
        0xabcdeffe00000011ULL, 0xabcdeffe00000013ULL,
    };
    const auto auth = RequireGcnEpochAuth(4, Epoch, scheduler_tokens);
    assert(auth.size() == 4);
    for (u32 i = 0; i < 4; ++i) {
        const auto& r = auth[i];
        assert(r.tag == GcnEpochAuthTag && r.epoch == Epoch &&
               r.physical_slot == i && r.wave_slots == 4 &&
               r.flags == GcnEpochAuthFlags && !r.reserved &&
               ((u64(r.token_hi) << 32) | r.token_lo) == scheduler_tokens[i]);
    }

    // Production GPU stamp predicate: no valid header from stale tokens,
    // forged slots, partial wave64 capture, or non-quiescent shared LDS.
    for (u32 i = 0; i < 4; ++i) {
        assert(IsAuthorizedGcnCaptureStamp(auth[i], i, true, true));
        assert(!IsAuthorizedGcnCaptureStamp(auth[i], i, false, true));
        assert(!IsAuthorizedGcnCaptureStamp(auth[i], i, true, false));
        auto invalid = auth[i];
        invalid.tag = 0;
        assert(!IsAuthorizedGcnCaptureStamp(invalid, i, true, true));
        invalid = auth[i];
        invalid.epoch = 0;
        assert(!IsAuthorizedGcnCaptureStamp(invalid, i, true, true));
        invalid = auth[i];
        invalid.physical_slot ^= 1;
        assert(!IsAuthorizedGcnCaptureStamp(invalid, i, true, true));
        invalid = auth[i];
        invalid.wave_slots = 0;
        assert(!IsAuthorizedGcnCaptureStamp(invalid, i, true, true));
        invalid = auth[i];
        invalid.wave_slots = GcnEpochAuthMaximumWaves + 1;
        assert(!IsAuthorizedGcnCaptureStamp(invalid, i, true, true));
        invalid = auth[i];
        invalid.wave_slots = i;
        assert(!IsAuthorizedGcnCaptureStamp(invalid, i, true, true));
        invalid = auth[i];
        invalid.token_lo = invalid.token_hi = 0;
        assert(!IsAuthorizedGcnCaptureStamp(invalid, i, true, true));
        invalid = auth[i];
        invalid.flags = 0;
        assert(!IsAuthorizedGcnCaptureStamp(invalid, i, true, true));
        invalid = auth[i];
        invalid.reserved = 1;
        assert(!IsAuthorizedGcnCaptureStamp(invalid, i, true, true));
    }
    // Verify the actual production issuer used by Rasterizer. Tokens are
    // unique monotonic physical-slot identities, not guest creation ranks.
    GcnOrderedGdsEpochIssuer issuer;
    assert(Reject([&] { (void)issuer.Issue(0); }));
    assert(Reject([&] { (void)issuer.Issue(GcnEpochAuthMaximumWaves + 1); }));
    const auto first = issuer.Issue(4);
    assert(first.epoch == 1 && first.tokens_by_slot.size() == 4);
    for (u32 i = 0; i < 4; ++i) assert(first.tokens_by_slot[i] == i + 1u);
    const auto next = issuer.Issue(3);
    assert(next.epoch == 2 && next.tokens_by_slot.size() == 3);
    for (u32 i = 0; i < 3; ++i) assert(next.tokens_by_slot[i] == i + 5u);
    const auto third = issuer.Issue(1);
    assert(third.epoch == 3 && third.tokens_by_slot[0] == 8u);
    const auto issued_auth = RequireGcnEpochAuth(
        static_cast<u32>(first.tokens_by_slot.size()),
        first.epoch, first.tokens_by_slot);
    for (u32 i = 0; i < 4; ++i) {
        assert(issued_auth[i].epoch == first.epoch);
        assert(issued_auth[i].physical_slot == i);
        assert(issued_auth[i].token_lo == first.tokens_by_slot[i]);
        assert(IsAuthorizedGcnCaptureStamp(issued_auth[i], i, true, true));
    }
    std::array<GpuCapturePacket,4> captures{};
    for (u32 i = 0; i < 4; ++i) {
        captures[i].tag = CapturePacketTag;
        captures[i].flags = CaptureFlags;
        captures[i].epoch = Epoch;
        captures[i].logical_slot = i;
        captures[i].token_lo = auth[i].token_lo;
        captures[i].token_hi = auth[i].token_hi;
    }
    RequireCaptureTokenAuthority(captures, auth, Epoch);
    // A complete CIK ordered-count-only capture CAN establish a genuine
    // M0 creation permutation without consulting Vulkan workgroup arrival
    // order; a later terminal bypass must invalidate that inference.
    auto rank_ready = captures;
    for (u32 i = 0; i < rank_ready.size(); ++i) {
        rank_ready[i].site_pc = 0x4158;
        rank_ready[i].encoded_control = 0x0300;
        rank_ready[i].runtime_m0 = (0x20u << 16) | i;
        rank_ready[i].exec_lo = 1;
        rank_ready[i].source_lane = 0;
        rank_ready[i].payload = 1;
    }
    assert(VerifiedGuestCreationOrder::FromCompleteCikComputeM0(rank_ready, Epoch));
    // A genuine terminal S_ENDPGM wave is accounted for without a fake
    // ordered-count operation or continuation token. Its HOST authority
    // remains unique and nonzero, but the BYPASS packet token is zero.
    auto with_terminal = rank_ready;
    auto& terminal = with_terminal[2];
    // No ordered site/EXECount is present for this completed terminal wave.
    terminal.encoded_control = 0;
    terminal.runtime_m0 = 0;
    terminal.exec_lo = 0;
    terminal.flags = CaptureBypassFlags;
    terminal.site_pc = NoActiveLane;
    terminal.source_lane = NoActiveLane;
    terminal.token_lo = 0;
    terminal.token_hi = 0;
    terminal.payload = 0;
    assert(IsCanonicalGcnTerminalBypass(terminal, Epoch));
    RequireCaptureTokenAuthority(with_terminal, auth, Epoch);
    const auto forged_terminal = [&](auto change) {
        auto wrong = with_terminal;
        change(wrong[2]);
        assert(!IsCanonicalGcnTerminalBypass(wrong[2], Epoch));
        assert(Reject([&] {RequireCaptureTokenAuthority(wrong, auth, Epoch);}));
    };
    forged_terminal([](auto& p) {p.token_lo = 99;});
    forged_terminal([](auto& p) {p.site_pc = 0x4158;});
    forged_terminal([](auto& p) {p.source_lane = 0;});
    forged_terminal([](auto& p) {p.payload = 1;});
    forged_terminal([](auto& p) {p.runtime_m0 = 1;});
    forged_terminal([](auto& p) {p.exec_lo = 1;});
    forged_terminal([](auto& p) {p.encoded_control = 0x0300;});
    forged_terminal([](auto& p) {p.reserved0 = 1;});
    forged_terminal([](auto& p) {p.flags = CaptureFlags; p.token_lo = 0;});
    // Without a separate genuine guest creation-order assignment a bypass
    // cannot be used to invent CIK M0 ranks for an entire resolved epoch.
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(
        with_terminal, Epoch));
    const auto wrong_packet = [&](auto change) {
        auto modified = captures;
        change(modified);
        assert(Reject([&] {
            RequireCaptureTokenAuthority(modified, auth, Epoch);
        }));
    };
    wrong_packet([](auto& p) { p[0].tag = 0; }); // reject unauthorized GPU capture
    wrong_packet([](auto& p) { p[0].flags = 0; });
    wrong_packet([](auto& p) { p[1].token_lo ^= 1; });
    wrong_packet([](auto& p) { p[2].token_hi ^= 1; });
    wrong_packet([](auto& p) { p[0].epoch++; });
    wrong_packet([](auto& p) { p[0].logical_slot = 1; });
    wrong_packet([](auto& p) { p[3].logical_slot = 4; });
    assert(Reject([&] {
        RequireCaptureTokenAuthority(
            std::span{captures}.subspan(1), auth, Epoch);
    }));
    auto forged_auth = auth;
    forged_auth[1].token_lo ^= 1;
    assert(Reject([&] {
        RequireCaptureTokenAuthority(captures, forged_auth, Epoch);
    }));
    forged_auth = auth;
    forged_auth[1].reserved = 1;
    assert(Reject([&] {
        RequireCaptureTokenAuthority(captures, forged_auth, Epoch);
    }));
    constexpr std::array<u64,4> duplicate{1, 2, 2, 4};
    constexpr std::array<u64,4> zero_token{1, 2, 0, 4};
    assert(Reject([&] { (void)RequireGcnEpochAuth(4, 0, scheduler_tokens); }));
    assert(Reject([&] { (void)RequireGcnEpochAuth(0, Epoch, scheduler_tokens); }));
    assert(Reject([&] { (void)RequireGcnEpochAuth(4, Epoch, std::span{scheduler_tokens}.subspan(1)); }));
    assert(Reject([&] { (void)RequireGcnEpochAuth(8193, Epoch, scheduler_tokens); }));
    assert(Reject([&] { (void)RequireGcnEpochAuth(4, Epoch, duplicate); }));
    assert(Reject([&] { (void)RequireGcnEpochAuth(4, Epoch, zero_token); }));
    std::cout << "GDS_VERIFIED_HOST_EPOCH_TOKENS=PASS "
                 "full_wave_coverage=PASS distinct_nonzero_tokens=PASS "
                 "no_fabricated_token=PASS forged_packet_rejected=PASS "
                 "stale_epoch_rejected=PASS duplicate_slot_rejected=PASS "
                 "authenticated_stamp_predicate=PASS incomplete_wave_lds_forgery_rejected=PASS "
                 "monotonic_host_dispatch_tickets=PASS rejected_wave_counts_no_ticket_loss=PASS "
                 "real_terminal_bypass_epoch_authority=PASS no_fabricated_M0_ranks=PASS "
                 "GPU_GUEST_CONTINUATION=NOT_ENABLED\n";
}
