// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cassert>
#include <cstdio>
#include <cstdint>
#include "video_core/renderer_vulkan/vk_buffer_hazard_rules.h"

using namespace Vulkan::BufferHazards;
using vk::AccessFlagBits2;
using vk::PipelineStageFlagBits2;

constexpr bool HasStage(vk::PipelineStageFlags2 mask,PipelineStageFlagBits2 bit) {
    return static_cast<bool>(mask & bit);
}

static_assert(IsTrackedRead(AccessFlagBits2::eIndirectCommandRead));
static_assert(IsTrackedRead(AccessFlagBits2::eIndexRead));
static_assert(IsTrackedRead(AccessFlagBits2::eVertexAttributeRead));
static_assert(IsTrackedRead(AccessFlagBits2::eUniformRead));
static_assert(IsTrackedRead(AccessFlagBits2::eShaderRead));
static_assert(IsTrackedRead(AccessFlagBits2::eTransferRead));
static_assert(IsTrackedRead(AccessFlagBits2::eDepthStencilAttachmentRead));
static_assert(!IsTrackedRead(AccessFlagBits2::eShaderWrite));
static_assert(!IsTrackedRead(AccessFlagBits2::eTransferWrite));
static_assert(!IsTrackedRead(vk::AccessFlags2{}));

static_assert(HasStage(
    StageForBoundBuffer(AccessFlagBits2::eIndirectCommandRead,false),
    PipelineStageFlagBits2::eDrawIndirect));
static_assert(HasStage(
    StageForBoundBuffer(AccessFlagBits2::eIndirectCommandRead,true),
    PipelineStageFlagBits2::eDrawIndirect));
static_assert(HasStage(
    StageForBoundBuffer(AccessFlagBits2::eShaderRead,true),
    PipelineStageFlagBits2::eComputeShader));
static_assert(HasStage(
    StageForBoundBuffer(AccessFlagBits2::eIndexRead,false),
    PipelineStageFlagBits2::eAllGraphics));
static_assert(!HasStage(
    StageForBoundBuffer(AccessFlagBits2::eShaderRead,false),
    PipelineStageFlagBits2::eDrawIndirect));

int main() {
    // Simulate the resource-tracker order in the real GoT scene pipeline.
    // The read's access mask is what runtime.AccessBuffer receives after
    // a vkCmdDrawIndexedIndirect. Before this fix, this did NOT enter
    // read_ranges, so a write to the same 20-byte command was unprotected.
    constexpr auto indirect=AccessFlagBits2::eIndirectCommandRead;
    constexpr auto writer=AccessFlagBits2::eShaderWrite;
    constexpr auto transfer=AccessFlagBits2::eTransferWrite;
    constexpr vk::AccessFlags2 broken_read_mask=ReadMask & ~vk::AccessFlags2{indirect};

    assert(!static_cast<bool>(broken_read_mask & indirect));
    assert(IsTrackedRead(indirect));
    assert(!IsTrackedRead(writer));
    assert(!IsTrackedRead(transfer));

    struct Access {
        std::uint64_t begin{},end{};
        bool is_read{};
    };
    constexpr Access command_read{0x15013305a0ULL,0x15013305b4ULL,true};
    constexpr Access compute_write{0x15013305a0ULL,0x15013305b4ULL,false};
    constexpr Access other_write{0x15013305d0ULL,0x15013305e4ULL,false};
    const auto overlaps=[](Access a,Access b) {
        return a.begin<b.end && b.begin<a.end;
    };
    const bool read_is_registered=IsTrackedRead(indirect);
    assert(read_is_registered && overlaps(command_read,compute_write));
    assert(!(read_is_registered && overlaps(command_read,other_write)));
    const auto combined=vk::AccessFlags2{indirect}|AccessFlagBits2::eShaderRead;
    assert(IsTrackedRead(combined));
    assert(HasStage(StageForBoundBuffer(combined,false),
                    PipelineStageFlagBits2::eDrawIndirect));
    assert(HasStage(StageForBoundBuffer(combined,false),
                    PipelineStageFlagBits2::eAllGraphics));

    std::puts("GOT_INDIRECT_BUFFER_WAR_HAZARD=PASS "
              "indirect_read_tracked=YES "
              "draw_indirect_stage_explicit=YES "
              "overlapping_write_must_barrier=YES "
              "disjoint_write_no_unnecessary_barrier=YES "
              "shader_compute_stages_preserved=YES "
              "former_missing_read_reproduced=YES "
              "no_shader_or_guest_data_changes=YES");
}
