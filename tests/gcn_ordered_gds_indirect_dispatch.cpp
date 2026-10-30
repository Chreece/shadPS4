// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
// Actual Vulkan indirect dispatch command ABI, not a CPU register snapshot.
#include "shader_recompiler/frontend/ordered_gds_device_plan.h"
#include <array>
#include <cassert>
#include <cstddef>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
using namespace Shader::Gcn::OrderedGds;

template<typename F> bool Reject(F&& cb) {
    try { cb(); } catch(const std::invalid_argument&) { return true; }
    return false;
}
static std::array<std::byte,sizeof(GpuIndirectDispatchCommand)>
Command(u32 x, u32 y, u32 z) {
    const GpuIndirectDispatchCommand cmd{x,y,z};
    std::array<std::byte,sizeof(cmd)> bytes{};
    std::memcpy(bytes.data(), &cmd, sizeof(cmd));
    return bytes;
}
int main() {
    static_assert(sizeof(GpuIndirectDispatchCommand) == 3 * sizeof(u32));
    const auto raw = Command(2,3,2);
    const auto dims = DecodeGpuIndirectDispatchCommand(raw);
    assert(dims.x == 2 && dims.y == 3 && dims.z == 2);
    const auto plan = GcnOrderedGdsDevicePlan::Require(dims,{64,1,1},9,4096);
    assert(plan.workgroups == 12 && plan.waves == 12 &&
           plan.lds_layout && plan.lds_layout->workgroups == 12);
    // Ordinary direct group registers may disagree with the GPU command.
    const auto wrong_direct_plan =
        GcnOrderedGdsDevicePlan::Require({1,1,1},{64,1,1},9,4096);
    assert(wrong_direct_plan.waves != plan.waves);
    assert(Reject([&]{(void)DecodeGpuIndirectDispatchCommand(
        std::span{raw}.subspan(1));}));
    const auto zero = Command(0,1,1);
    assert(Reject([&]{(void)DecodeGpuIndirectDispatchCommand(zero);}));
    const auto zero_y = Command(1,0,1);
    assert(Reject([&]{(void)DecodeGpuIndirectDispatchCommand(zero_y);}));
    const auto zero_z = Command(1,1,0);
    assert(Reject([&]{(void)DecodeGpuIndirectDispatchCommand(zero_z);}));
    const auto too_many = Command(8193,1,1);
    assert(Reject([&]{const auto x = DecodeGpuIndirectDispatchCommand(too_many);
        (void)GcnOrderedGdsDevicePlan::Require(x,{64,1,1},9,0);}));
    const auto overflow = Command(std::numeric_limits<u32>::max(),
                                  std::numeric_limits<u32>::max(),2);
    assert(Reject([&]{const auto x = DecodeGpuIndirectDispatchCommand(overflow);
        (void)GcnOrderedGdsDevicePlan::Require(x,{64,1,1},9,0);}));
    std::cout << "GDS_GPU_INDIRECT_DISPATCH_ABI=PASS "
                 "gpu_command_bytes=12 direct_register_mismatch_detected=PASS "
                 "zero_truncated_overflow_wave_budget_rejected=PASS "
                 "real_capture_gpu_execution=NOT_ENABLED\n";
}
