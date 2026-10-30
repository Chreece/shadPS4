// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "shader_recompiler/frontend/ordered_gds_checkpoint.h"
#include <array>
#include <cassert>
#include <iostream>
#include <stdexcept>
#include <utility>
#include <vector>

using namespace Shader::Gcn::OrderedGds;

template<typename Fn> bool Reject(Fn&& f) {
    try { f(); } catch (const std::invalid_argument&) { return true; }
    return false;
}

int main() {
    constexpr u32 Epoch = 17;
    const auto layout = GcnCheckpointLayout::Require(3, 84, 16, 0);
    assert(layout.waves == 3 && layout.scalar_words == 84 &&
           layout.vector_registers == 16 && layout.stride_bytes % 16 == 0 &&
           layout.total_bytes == 3 * layout.stride_bytes);
    std::vector<std::byte> storage(layout.total_bytes);
    std::array<GpuCapturePacket,3> captures{};
    constexpr std::array<u32,3> slots{2,0,1};
    constexpr std::array<u32,3> sites{0x4158,0x41d4,0x4158};
    for (std::size_t i = 0; i < slots.size(); ++i) {
        auto& capture = captures[i];
        capture.tag = CapturePacketTag;
        capture.epoch = Epoch;
        capture.flags = CaptureFlags;
        capture.site_pc = sites[i];
        capture.logical_slot = slots[i];
        capture.runtime_m0 = 0x220000u | u32(i);
        capture.exec_lo = u32{1} << i;
        capture.exec_hi = i == 2 ? 1u : 0u;
        capture.token_lo = 100 + u32(i);
        capture.token_hi = 200 + u32(i);
        GpuGcnWaveCheckpoint header{};
        header.tag = GcnCheckpointTag;
        header.epoch = Epoch;
        header.flags = GcnCheckpointFlags;
        header.site_pc = capture.site_pc;
        header.next_guest_pc = capture.site_pc + 8;
        header.logical_slot = capture.logical_slot;
        header.runtime_m0 = capture.runtime_m0;
        header.exec_lo = capture.exec_lo;
        header.exec_hi = capture.exec_hi;
        header.token_lo = capture.token_lo;
        header.token_hi = capture.token_hi;
        header.destination_vgpr = 7;
        header.scalar_words = layout.scalar_words;
        header.vector_registers = layout.vector_registers;
        header.lane_count = GcnWaveLanes;
        std::memcpy(storage.data() + header.logical_slot * layout.stride_bytes,
                    &header, sizeof(header));
    }
    RequireCompleteGcnWaveCheckpoints(storage, captures, layout, Epoch);
    const auto require_bad = [&](auto&& mutation) {
        auto wrong_captures = captures;
        auto wrong_storage = storage;
        mutation(wrong_captures, wrong_storage);
        assert(Reject([&] { RequireCompleteGcnWaveCheckpoints(
            wrong_storage, wrong_captures, layout, Epoch); }));
    };
    require_bad([](auto& caps, auto&) { caps[0].logical_slot = caps[1].logical_slot; });
    require_bad([](auto& caps, auto&) { caps[0].flags = 1; }); // bypass cannot resume
    require_bad([](auto& caps, auto&) { caps[0].runtime_m0 ^= 1u; });
    require_bad([](auto& caps, auto&) { caps[0].exec_lo ^= 1u; });
    require_bad([](auto& caps, auto&) { caps[0].token_hi ^= 1u; });
    require_bad([](auto& caps, auto&) { caps[0].epoch++; });
    require_bad([](auto& caps, auto&) { caps[0].tag = 0; });
    require_bad([](auto& caps, auto&) { caps[0].site_pc = 0; });
    const auto bad_header = [&](auto&& modify) {
        require_bad([&](auto&, auto& mem) {
            GpuGcnWaveCheckpoint header{};
            std::memcpy(&header, mem.data(), sizeof(header));
            modify(header);
            std::memcpy(mem.data(), &header, sizeof(header));
        });
    };
    bad_header([](auto& h) { h.tag = 0; });
    bad_header([](auto& h) { h.epoch++; });
    bad_header([](auto& h) { h.site_pc ^= 8u; });
    bad_header([](auto& h) { h.next_guest_pc--; });
    bad_header([](auto& h) { h.logical_slot++; });
    bad_header([](auto& h) { h.runtime_m0 ^= 1u; });
    bad_header([](auto& h) { h.exec_lo ^= 1u; });
    bad_header([](auto& h) { h.exec_hi ^= 1u; });
    bad_header([](auto& h) { h.token_lo ^= 1u; });
    bad_header([](auto& h) { h.token_hi ^= 1u; });
    bad_header([](auto& h) { h.destination_vgpr = 16; });
    bad_header([](auto& h) { h.scalar_words++; });
    bad_header([](auto& h) { h.vector_registers++; });
    bad_header([](auto& h) { h.lane_count = 32; });
    bad_header([](auto& h) { h.flags = 0; });
    bad_header([](auto& h) { h.reserved = 1; });
    assert(Reject([&] { RequireCompleteGcnWaveCheckpoints(
        std::span<const std::byte>{storage}.subspan(1), captures, layout, Epoch); }));
    assert(Reject([&] { RequireCompleteGcnWaveCheckpoints(storage, captures, layout, Epoch + 1); }));
    assert(Reject([&] { (void)GcnCheckpointLayout::Require(0,84,16,0); }));
    assert(Reject([&] { (void)GcnCheckpointLayout::Require(3,129,16,0); }));
    assert(Reject([&] { (void)GcnCheckpointLayout::Require(3,84,0,0); }));
    assert(Reject([&] { (void)GcnCheckpointLayout::Require(3,84,257,0); }));
    assert(Reject([&] { (void)GcnCheckpointLayout::Require(8193,84,16,0); }));
    assert(Reject([&] { (void)GcnCheckpointLayout::Require(3,84,16,1024); }));
    assert(Reject([&] { (void)GcnCheckpointLayout::Require(8192,128,256,0); }));
    std::cout << "ORDERED_GDS_CHECKPOINT_CONTRACT=PASS "
                 "sgpr_vgpr_layout=PASS epoch_site_m0_exec_token=PASS "
                 "shader_next_pc=PASS no_lds_or_unbounded_alloc=PASS "
                 "duplicate_missing_stale_rejected=PASS GPU_GUEST_STATE_NOT_WRITTEN\n";
}
