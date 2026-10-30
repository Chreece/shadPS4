// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "shader_recompiler/frontend/ordered_gds_resume_state.h"

#include <algorithm>
#include <array>
#include <cassert>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>

using namespace Shader::Gcn::OrderedGds;

template<class F> bool Reject(F&& f) {
    try { f(); } catch (const std::invalid_argument&) { return true; }
    return false;
}

int main() {
    constexpr u32 epoch = 27;
    const auto layout = GcnCheckpointLayout::Require(3, 104, 4, 0);
    constexpr std::array<VerifiedGcnOrderedSite,2> sites{{
        {0x4158, 1, 0x0300}, {0x41d4, 2, 0x0300},
    }};
    constexpr std::array<u32,3> guest_sites{0x4158, 0x41d4, 0x4158};
    constexpr std::array<u32,3> exec_lo{
        (1u << 0) | (1u << 7), 0, 1u << 1,
    };
    constexpr std::array<u32,3> exec_hi{
        1u << 31, 0, 1u << 0,
    };
    const std::array<u64,3> ranks_by_slot{2, 0, 1};
    const auto order = VerifiedGuestCreationOrder::FromScheduler(ranks_by_slot);
    assert(order);

    std::vector<std::byte> captured(layout.total_bytes, std::byte{0xa5});
    std::array<GpuCapturePacket,3> captures{};
    std::array<GpuResumePacket,3> resumes{};
    // Deliberately permute packet arrival relative to storage slot.
    constexpr std::array<u32,3> arrival{2,0,1};
    for (u32 arrival_index = 0; arrival_index < 3; ++arrival_index) {
        const u32 slot = arrival[arrival_index];
        auto& c = captures[arrival_index];
        c.tag = CapturePacketTag;
        c.epoch = epoch;
        c.flags = CaptureFlags;
        c.site_pc = guest_sites[slot];
        c.encoded_control = 0x0300;
        c.logical_slot = slot;
        c.runtime_m0 = 0x220000u | slot;
        c.exec_lo = exec_lo[slot];
        c.exec_hi = exec_hi[slot];
        c.token_lo = 1700 + slot;
        c.token_hi = 4200 + slot;
        const u64 base = u64(slot) * layout.stride_bytes;
        GpuGcnWaveCheckpoint h{};
        h.tag = GcnCheckpointTag;
        h.epoch = epoch;
        h.flags = GcnCheckpointFlags;
        h.site_pc = c.site_pc;
        h.next_guest_pc = c.site_pc + 8;
        h.logical_slot = slot;
        h.runtime_m0 = c.runtime_m0;
        h.exec_lo = c.exec_lo;
        h.exec_hi = c.exec_hi;
        h.token_lo = c.token_lo;
        h.token_hi = c.token_hi;
        h.destination_vgpr = guest_sites[slot] == 0x4158 ? 1u : 2u;
        h.scalar_words = layout.scalar_words;
        h.vector_registers = layout.vector_registers;
        h.lane_count = GcnWaveLanes;
        h.vcc_lo = 0x12345678u ^ slot;
        h.vcc_hi = 0xabcdef00u ^ slot;
        h.scc = slot & 1u;
        h.abi_version = GcnCheckpointAbiVersion;
        std::memcpy(captured.data() + base, &h, sizeof(h));
        for (u32 reg = 0; reg < 4; ++reg) {
            for (u32 lane = 0; lane < GcnWaveLanes; ++lane) {
                const u32 original = 0x90000000u + (slot << 16) +
                                     (reg << 8) + lane;
                const u64 byte_offset = base + sizeof(h) +
                    (u64(layout.scalar_words) + u64(reg) * GcnWaveLanes + lane) * 4;
                std::memcpy(captured.data() + byte_offset, &original, sizeof(original));
            }
        }
        auto& r = resumes[slot];
        r.tag = ResumePacketTag;
        r.epoch = epoch;
        r.flags = ResumeFlags;
        r.logical_slot = slot;
        r.site_pc = c.site_pc;
        r.counter_index = 0;
        r.token_lo = c.token_lo;
        r.token_hi = c.token_hi;
        r.creation_lo = static_cast<u32>(order->RankForSlot(slot));
        r.previous = 0xcafe0000u + 79u * slot;
        r.current = r.previous + 1;
    }
    const auto pristine = captured;
    const auto post = MaterializeGcnPostOrderedCountState(
        captured, captures, resumes, layout, epoch, sites, *order);
    assert(captured == pristine && post.size() == pristine.size());
    auto expected = captured;
    for (u32 slot = 0; slot < 3; ++slot) {
        const u32 vdst = guest_sites[slot] == 0x4158 ? 1 : 2;
        const u64 exec = (u64(exec_hi[slot]) << 32) | exec_lo[slot];
        for (u32 lane = 0; lane < GcnWaveLanes; ++lane) {
            if (((exec >> lane) & 1u) == 0) continue;
            const u64 offset = u64(slot) * layout.stride_bytes +
                sizeof(GpuGcnWaveCheckpoint) +
                (u64(layout.scalar_words) + u64(vdst) * GcnWaveLanes + lane) * 4;
            const u32 previous = resumes[slot].previous;
            std::memcpy(expected.data() + offset, &previous, sizeof(previous));
        }
    }
    // Comparing the ENTIRE wave snapshot proves every SGPR, other VGPR,
    // inactive VDST lane, architectural flag and next guest PC is untouched.
    assert(post == expected);
    // An EXEC=0 wave never overwrites even its decoded VDST lanes.
    const auto b1 = u64(1) * layout.stride_bytes;
    for (u64 i = 0; i < layout.stride_bytes; ++i) {
        assert(post[b1 + i] == pristine[b1 + i]);
    }

    const auto invalid_resume = [&](auto mutate) {
        auto wrong = resumes;
        mutate(wrong);
        assert(Reject([&] {
            (void)MaterializeGcnPostOrderedCountState(
                pristine, captures, wrong, layout, epoch, sites, *order);
        }));
    };
    invalid_resume([](auto& p) { p[0].token_lo ^= 1; });
    invalid_resume([](auto& p) { p[1].flags = 0; });
    invalid_resume([](auto& p) { p[2].creation_lo++; });
    invalid_resume([](auto& p) { p[1].site_pc += 8; });

    auto bad_header = pristine;
    GpuGcnWaveCheckpoint header{};
    std::memcpy(&header, bad_header.data(), sizeof(header));
    header.destination_vgpr = 2;
    std::memcpy(bad_header.data(), &header, sizeof(header));
    assert(Reject([&] {
        (void)MaterializeGcnPostOrderedCountState(
            bad_header, captures, resumes, layout, epoch, sites, *order);
    }));
    constexpr std::array<VerifiedGcnOrderedSite,2> mismatched{{
        {0x4158, 3, 0x0300}, {0x41d4, 2, 0x0300},
    }};
    assert(Reject([&] {
        (void)MaterializeGcnPostOrderedCountState(
            pristine, captures, resumes, layout, epoch, mismatched, *order);
    }));
    assert(Reject([&] {
        (void)MaterializeGcnPostOrderedCountState(
            pristine, captures, resumes, layout, epoch + 1, sites, *order);
    }));

    // Genuine mixed terminal/ordered epochs: physical slot 1 returned from
    // original S_ENDPGM without reaching DS_ORDERED_COUNT. Its checkpoint
    // slot can contain arbitrary unwritten bytes; never manufacture a guest
    // register image, decoded VDST, token, PC, rank, or runnable resume.
    auto mixed_captures = captures;
    auto terminal = std::find_if(
        mixed_captures.begin(), mixed_captures.end(),
        [](const auto& c) { return c.logical_slot == 1; });
    assert(terminal != mixed_captures.end());
    *terminal = GpuCapturePacket{
        .tag = CapturePacketTag,
        .epoch = epoch,
        .site_pc = NoActiveLane,
        .logical_slot = 1,
        .source_lane = NoActiveLane,
        .flags = CaptureBypassFlags,
    };
    auto mixed_resumes = resumes;
    mixed_resumes[1] = GpuResumePacket{
        .tag = ResumePacketTag,
        .epoch = epoch,
        .site_pc = NoActiveLane,
        .flags = 0,
        .logical_slot = 1,
    };
    auto mixed_pre = pristine;
    const auto bypass_start = layout.stride_bytes;
    std::fill(mixed_pre.begin() + bypass_start,
              mixed_pre.begin() + bypass_start + layout.stride_bytes,
              std::byte{0xa5});
    auto mixed_expected = expected;
    std::fill(mixed_expected.begin() + bypass_start,
              mixed_expected.begin() + bypass_start + layout.stride_bytes,
              std::byte{0xa5});
    const auto mixed_post = MaterializeGcnPostOrderedCountState(
        mixed_pre, mixed_captures, mixed_resumes, layout, epoch, sites, *order);
    assert(mixed_post == mixed_expected);
    assert(mixed_pre != mixed_post); // active ordered slots STILL get VDST.
    const auto reject_bad_terminal_capture = [&](auto mutate) {
        auto bad = mixed_captures;
        for (auto& c : bad) {
            if (c.logical_slot == 1) mutate(c);
        }
        assert(Reject([&] {
            (void)MaterializeGcnPostOrderedCountState(
                mixed_pre, bad, mixed_resumes, layout, epoch, sites, *order);
        }));
    };
    reject_bad_terminal_capture([](auto& c) { c.site_pc = 0x4158; });
    reject_bad_terminal_capture([](auto& c) { c.token_lo = 17; });
    reject_bad_terminal_capture([](auto& c) { c.source_lane = 0; });
    reject_bad_terminal_capture([](auto& c) { c.runtime_m0 = 1; });
    reject_bad_terminal_capture([](auto& c) { c.exec_lo = 1; });
    reject_bad_terminal_capture([](auto& c) { c.flags = CaptureFlags; });
    const auto reject_bad_terminal_resume = [&](auto mutate) {
        auto bad = mixed_resumes;
        mutate(bad[1]);
        assert(Reject([&] {
            (void)MaterializeGcnPostOrderedCountState(
                mixed_pre, mixed_captures, bad, layout, epoch, sites, *order);
        }));
    };
    reject_bad_terminal_resume([](auto& r) { r.flags = ResumeFlags; });
    reject_bad_terminal_resume([](auto& r) { r.site_pc = 0x4158; });
    reject_bad_terminal_resume([](auto& r) { r.token_lo = 123; });
    reject_bad_terminal_resume([](auto& r) { r.creation_lo = 1; });
    reject_bad_terminal_resume([](auto& r) { r.previous = 1; });
    reject_bad_terminal_resume([](auto& r) { r.reserved0 = 1; });
    // The current compute M0 fast path must NOT invent guest creation rank
    // for a bypass wave; this test has a separate verified scheduler order.
    assert(!VerifiedGuestCreationOrder::FromCompleteCikComputeM0(
        mixed_captures, epoch));
    std::cout << "GDS_POST_DS_RESOLVED_ACTIVE_VDST=PASS "
                 "verified_old_value=PASS preserve_all_other_registers=PASS "
                 "inactive_exec_vdst_unchanged=PASS "
                 "permuted_wave_slots=PASS wrong_site_token_epoch_rejected=PASS "\n                 "real_terminal_mixed_state_resume_guard=PASS "
                 "REAL_GUEST_SHADER_REENTRY=NOT_IMPLEMENTED\n";
}
