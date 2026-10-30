// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include "shader_recompiler/frontend/ordered_gds_device_plan.h"

#include <cassert>
#include <iostream>
#include <stdexcept>
#include <type_traits>

using namespace Shader::Gcn::OrderedGds;
using Plan = GcnOrderedGdsDevicePlan;

template<typename F> bool Reject(F&& f) {
    try {
        f();
    } catch (const std::invalid_argument&) {
        return true;
    }
    return false;
}

int main() {
    const auto zero = Plan::Require({2, 1, 1}, {128, 1, 1}, 9, 0);
    assert(zero.workgroups == 2 && zero.waves_per_group == 2 &&
           zero.waves == 4 && !zero.lds_layout &&
           zero.persistent_lds_bytes == 0);
    assert(zero.packet_bytes == 4 * sizeof(GpuCapturePacket));
    assert(zero.register_layout.scalar_words == 104 &&
           zero.register_layout.vector_registers == 9);
    assert(zero.register_layout.waves == 4);
    assert(zero.epoch_auth_bytes ==
           zero.waves * sizeof(GpuGcnOrderedEpochAuth));
    assert(zero.combined_device_bytes ==
           2 * (zero.packet_bytes + zero.register_layout.total_bytes) +
               zero.epoch_auth_bytes);
    assert(zero.combined_device_bytes <= Plan::MaxEpochDeviceBytes);

    const auto shared = Plan::Require({2, 1, 1}, {128, 1, 1}, 9, 4096);
    assert(shared.waves == 4 && shared.lds_layout);
    assert(shared.lds_layout->workgroups == 2 &&
           shared.lds_layout->waves_per_group == 2 &&
           shared.lds_layout->lds_bytes == 4096);
    assert(shared.epoch_auth_bytes == zero.epoch_auth_bytes);
    assert(shared.persistent_lds_bytes == 2ull * 4096);
    assert(shared.combined_device_bytes ==
           zero.combined_device_bytes +
           2 * shared.lds_layout->total_bytes + shared.persistent_lds_bytes);
    assert(shared.persistent_lds_bytes ==
           u64(shared.workgroups) * shared.lds_layout->lds_bytes);
    assert(shared.PersistentLdsOffset(0) == 0);
    assert(shared.PersistentLdsOffset(1) == 4096);
    assert(Reject([&] { (void)shared.PersistentLdsOffset(2); }));
    assert(Reject([&] { (void)zero.PersistentLdsOffset(0); }));
    const auto shared_3d = Plan::Require({2,3,2}, {64,1,1}, 4, 4096);
    assert(shared_3d.workgroups == 12 &&
           shared_3d.persistent_lds_bytes == 12ull * 4096);
    for (u32 group = 0; group < 12; ++group) {
        assert(shared_3d.PersistentLdsOffset(group) == u64(group) * 4096);
    }

    // Same total logical slots in a true 3D dispatch; the x-fastest
    // physical slot is NEVER a guest-created wave rank.
    const auto three_d = Plan::Require({2, 3, 2}, {64, 1, 1}, 4, 0);
    assert(three_d.workgroups == 12 && three_d.waves == 12 &&
           three_d.waves_per_group == 1 && !three_d.lds_layout);

    const auto max_workgroup = Plan::Require({1, 1, 1}, {16, 16, 4}, 2, 0);
    assert(max_workgroup.waves_per_group == 16 &&
           max_workgroup.waves == 16);

    // Refuse partial guest wave64s. The current per-invocation GPU
    // checkpoint would leave missing lanes of the final VGPR image.
    assert(Reject([] { (void)Plan::Require({1, 1, 1}, {65, 1, 1}, 9, 0); }));
    assert(Reject([] { (void)Plan::Require({1, 1, 1}, {1, 1, 1}, 9, 0); }));
    assert(Reject([] { (void)Plan::Require({1, 1, 1}, {1025, 1, 1}, 9, 0); }));
    assert(Reject([] { (void)Plan::Require({1, 1, 1}, {64, 0, 1}, 9, 0); }));
    assert(Reject([] { (void)Plan::Require({0, 1, 1}, {64, 1, 1}, 9, 0); }));
    assert(Reject([] { (void)Plan::Require({8193, 1, 1}, {64, 1, 1}, 9, 0); }));
    assert(Reject([] { (void)Plan::Require({1, 1, 1}, {64, 1, 1}, 0, 0); }));
    assert(Reject([] { (void)Plan::Require({1, 1, 1}, {64, 1, 1}, 257, 0); }));
    assert(Reject([] { (void)Plan::Require({1, 1, 1}, {64, 1, 1}, 9, 3); }));
    assert(Reject([] { (void)Plan::Require({1, 1, 1}, {64, 1, 1}, 9, 65540); }));

    // The combined capture+resume budget must be checked, not just
    // independently bounded packet/checkpoint/LDS allocations.
    assert(Reject([] {
        (void)Plan::Require({3500, 1, 1}, {64, 1, 1}, 64, 65536);
    }));
    assert(Reject([] {
        (void)Plan::Require({8192, 1, 1}, {64, 1, 1}, 256, 0);
    }));

    std::cout << "GDS_DEVICE_DISPATCH_PLAN=PASS "
                 "real_wave64_only=PASS validated_gcn_registers=PASS "
                 "zero_lds_no_dummy=PASS nonzero_lds_bounded=PASS "
                 "persistent_lds_workgroup_offsets=PASS "
                 "combined_vram_budget=PASS 3d_slots=PASS "
                 "GPU_DISPATCH=NOT_ENABLED\n";
}
