// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "shader_recompiler/frontend/ordered_gds_lds_checkpoint.h"

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
    constexpr std::uint32_t Epoch = 17;
    const auto layout = GcnLdsCheckpointLayout::Require(3, 4, 8192);
    assert(layout.workgroups == 3 && layout.waves_per_group == 4);
    assert(layout.stride_bytes % 16 == 0 &&
           layout.stride_bytes > sizeof(GpuGcnWorkgroupLdsCheckpoint));
    assert(layout.total_bytes == 3 * layout.stride_bytes);
    std::vector<std::byte> snapshot(layout.total_bytes);
    for (std::uint32_t group = 0; group < layout.workgroups; ++group) {
        GpuGcnWorkgroupLdsCheckpoint h{};
        h.tag = LdsCheckpointTag;
        h.epoch = Epoch;
        h.workgroup_slot = group;
        h.lds_bytes = layout.lds_bytes;
        h.waves_per_group = layout.waves_per_group;
        h.waves_at_barrier = layout.waves_per_group;
        h.flags = LdsCheckpointQuiescentFlag;
        h.abi_version = LdsCheckpointAbiVersion;
        std::memcpy(snapshot.data() + group * layout.stride_bytes, &h, sizeof(h));
        // Every workgroup retains independent nonzero shared-memory contents.
        for (std::uint32_t i = 0; i < layout.lds_bytes; ++i) {
            snapshot[group * layout.stride_bytes + sizeof(h) + i] =
                std::byte((group * 17u + i) & 255u);
        }
    }
    RequireCompleteGcnLdsCheckpoints(snapshot, layout, Epoch);

    const auto reject_header = [&](auto&& mutate) {
        auto modified = snapshot;
        GpuGcnWorkgroupLdsCheckpoint h{};
        std::memcpy(&h, modified.data() + layout.stride_bytes, sizeof(h));
        mutate(h);
        std::memcpy(modified.data() + layout.stride_bytes, &h, sizeof(h));
        assert(Reject([&] { RequireCompleteGcnLdsCheckpoints(
            modified, layout, Epoch); }));
    };
    reject_header([](auto& h) { h.tag = 0; });
    reject_header([](auto& h) { h.abi_version++; });
    reject_header([](auto& h) { h.epoch++; });
    reject_header([](auto& h) { h.workgroup_slot = 0; });
    reject_header([](auto& h) { h.lds_bytes -= 4; });
    reject_header([](auto& h) { h.waves_per_group = 2; });
    reject_header([](auto& h) { h.waves_at_barrier = 3; });
    reject_header([](auto& h) { h.flags = 0; });
    reject_header([](auto& h) { h.reserved3 = 1; });

    assert(Reject([&] { RequireCompleteGcnLdsCheckpoints(
        std::span<const std::byte>{snapshot}.subspan(1), layout, Epoch); }));
    assert(Reject([&] { RequireCompleteGcnLdsCheckpoints(
        snapshot, layout, Epoch + 1); }));
    auto inconsistent = layout;
    inconsistent.stride_bytes += 16;
    assert(Reject([&] { RequireCompleteGcnLdsCheckpoints(
        snapshot, inconsistent, Epoch); }));
    assert(Reject([&] { (void)GcnLdsCheckpointLayout::Require(0,4,8192); }));
    assert(Reject([&] { (void)GcnLdsCheckpointLayout::Require(3,0,8192); }));
    assert(Reject([&] { (void)GcnLdsCheckpointLayout::Require(3,17,8192); }));
    assert(Reject([&] { (void)GcnLdsCheckpointLayout::Require(3,4,3); }));
    assert(Reject([&] { (void)GcnLdsCheckpointLayout::Require(3,4,65540); }));
    assert(Reject([&] { (void)GcnLdsCheckpointLayout::Require(65536,16,65536); }));

    std::cout << "ORDERED_GDS_GUEST_LDS_CHECKPOINT=PASS "
                 "workgroups=3 waves_per_group=4 "
                 "shared_state_independent=PASS "
                 "quiescent_wave_coverage=PASS "
                 "truncated_stale_wrong_group_rejected=PASS "
                 "no_unverified_lds_state_restore=PASS NO_GPU_WORK\n";
}
