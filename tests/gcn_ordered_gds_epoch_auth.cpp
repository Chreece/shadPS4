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
    const auto wrong_packet = [&](auto change) {
        auto modified = captures;
        change(modified);
        assert(Reject([&] {
            RequireCaptureTokenAuthority(modified, auth, Epoch);
        }));
    };
    wrong_packet([](auto& p) { p[0].tag = 0; }); // current GPU body is NOT valid
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
                 "GPU_GUEST_CONTINUATION=NOT_ENABLED\n";
}
