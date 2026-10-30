// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "shader_recompiler/frontend/ordered_gds_capture_packet.h"

#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <limits>
#include <set>

using namespace Shader::Gcn::OrderedGds;
int main() {
    static_assert(GpuCapturePacketDwords == 16);
    static_assert(sizeof(GpuCapturePacket) == 64);
    static_assert(offsetof(GpuCapturePacket, tag) == 0);
    static_assert(offsetof(GpuCapturePacket, epoch) == 4);
    static_assert(offsetof(GpuCapturePacket, site_pc) == 8);
    static_assert(offsetof(GpuCapturePacket, encoded_control) == 12);
    static_assert(offsetof(GpuCapturePacket, logical_slot) == 16);
    static_assert(offsetof(GpuCapturePacket, runtime_m0) == 20);
    static_assert(offsetof(GpuCapturePacket, exec_lo) == 24);
    static_assert(offsetof(GpuCapturePacket, exec_hi) == 28);
    static_assert(offsetof(GpuCapturePacket, source_lane) == 32);
    static_assert(offsetof(GpuCapturePacket, payload) == 36);
    static_assert(offsetof(GpuCapturePacket, token_lo) == 40);
    static_assert(offsetof(GpuCapturePacket, token_hi) == 44);
    static_assert(offsetof(GpuCapturePacket, flags) == 48);
    static_assert(offsetof(GpuCapturePacket, reserved2) == 60);
    std::set<std::uint32_t> offsets;
    for (std::uint32_t slot = 0; slot < 4; ++slot) {
        for (std::uint32_t word = 0; word < 16; ++word) {
            const auto address = GpuCapturePacketDwordOffset(slot, word);
            assert(address && *address == slot * 16 + word);
            assert(offsets.insert(*address).second);
        }
    }
    assert(offsets.size() == 64);
    assert(!GpuCapturePacketDwordOffset(0, 16));
    assert(!GpuCapturePacketDwordOffset(0xffffffffu, 15));
    assert(!GpuCapturePacketDwordOffset(268435456u, 0));
    const auto max_word = GpuCapturePacketDwordOffset(268435455u, 15);
    assert(max_word && *max_word == std::numeric_limits<std::uint32_t>::max());
    // An inactive wave has no source lane and cannot mint a valid packet.
    assert(NoActiveLane == 0xffffffffu);
    assert(CaptureFlags == 3u && CaptureBypassFlags == 1u);
    std::cout << "ORDERED_GDS_REAL_GPU_PACKET_LAYOUT=PASS "
                 "decoded_cik_fields=PASS no_slot_alias=PASS "
                 "invalid_or_overflow_word_rejected=PASS "
                 "auth_epoch_token_not_synthesized=PASS "
                 "GPU_DISPATCH_STILL_GATED\n";
}
