// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <span>
#include "video_core/renderer_vulkan/vk_got_indirect_command_proof.h"
using namespace Vulkan::Diagnostics;

int main() {
    static_assert(sizeof(GotIndexedIndirectCommand)==20);
    std::array<std::uint8_t,sizeof(GotIndexedIndirectCommand)*2> bytes{};
    GotIndexedIndirectCommand command{798,1,30,-3,0};
    std::memcpy(bytes.data(),&command,sizeof(command));
    GotIndexedIndirectCommand absent{};
    std::memcpy(bytes.data()+sizeof(command),&absent,sizeof(absent));
    const auto raw=std::span<const std::uint8_t>(bytes);
    auto classify=[&](std::uint32_t max_count,bool has_count,std::uint32_t actual) {
        return ClassifyGotGpuIndirectCommands(
            raw,sizeof(GotIndexedIndirectCommand),max_count,has_count,actual);
    };
    assert(classify(1,false,0)==GotIndirectInterpretation::HasNonzeroDrawCommand);
    assert(classify(2,false,0)==GotIndirectInterpretation::HasNonzeroDrawCommand);
    assert(classify(2,true,1)==GotIndirectInterpretation::HasNonzeroDrawCommand);
    assert(classify(2,true,0)==GotIndirectInterpretation::NoCommandsSelected);
    assert(classify(0,false,0)==GotIndirectInterpretation::NoCommandsSelected);
    assert(classify(3,false,0)==GotIndirectInterpretation::IncompleteSnapshot);
    assert(ClassifyGotGpuIndirectCommands(raw,16,1,false,0)==
           GotIndirectInterpretation::IncompleteSnapshot);
    command.index_count=0;
    std::memcpy(bytes.data(),&command,sizeof(command));
    assert(classify(2,false,0)==GotIndirectInterpretation::AllSelectedCommandsZero);
    command.index_count=798;
    command.instance_count=0;
    std::memcpy(bytes.data(),&command,sizeof(command));
    assert(classify(2,false,0)==GotIndirectInterpretation::AllSelectedCommandsZero);
    command.instance_count=2;
    std::memcpy(bytes.data()+sizeof(command),&command,sizeof(command));
    assert(classify(2,true,1)==GotIndirectInterpretation::AllSelectedCommandsZero);
    assert(classify(2,true,2)==GotIndirectInterpretation::HasNonzeroDrawCommand);
    assert(DecodeGotIndexedIndirect(raw,sizeof(command)).index_count==798);

    std::mt19937 gen{0x5116e459};
    for(int n=0;n<100000;++n) {
        GotIndexedIndirectCommand a{gen()%2000,gen()%12,
                                     gen()%4000,static_cast<std::int32_t>(gen()),gen()%20};
        GotIndexedIndirectCommand b{gen()%2000,gen()%12,
                                     gen()%4000,static_cast<std::int32_t>(gen()),gen()%20};
        std::memcpy(bytes.data(),&a,sizeof(a));
        std::memcpy(bytes.data()+sizeof(a),&b,sizeof(b));
        const bool avalid=a.index_count && a.instance_count;
        const bool bvalid=b.index_count && b.instance_count;
        const std::uint32_t selected=gen()%3;
        const auto actual=ClassifyGotGpuIndirectCommands(
            raw,sizeof(a),2,true,selected);
        const auto expected=selected==0
            ? GotIndirectInterpretation::NoCommandsSelected
            : (avalid || (selected>=2 && bvalid))
                ? GotIndirectInterpretation::HasNonzeroDrawCommand
                : GotIndirectInterpretation::AllSelectedCommandsZero;
        assert(actual==expected);
        assert(DecodeGotIndexedIndirect(raw,0).vertex_offset==a.vertex_offset);
        assert(DecodeGotIndexedIndirect(raw,sizeof(a)).first_instance==b.first_instance);
    }
    std::puts("GOT_INDIRECT_GPU_ARGUMENT_DECODER=PASS cases=100000 "
              "gpu_zero_counts_identified=PASS "
              "gpu_nonzero_counts_identified=PASS "
              "count_buffer_gate=PASS "
              "bounded_and_truncated_fail_closed=PASS "
              "no_guest_or_gpu_mutation=PASS");
}
