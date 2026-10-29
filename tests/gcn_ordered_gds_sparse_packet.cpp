#include "shader_recompiler/frontend/ordered_gds_capture_packet.h"

#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <stdexcept>

using namespace Shader::Gcn::OrderedGds;

template<class Fn> static bool Reject(Fn&& fn) {
    try { fn(); } catch (const std::invalid_argument&) { return true; }
    return false;
}

static GpuCapturePacket Ordered(u32 slot, u32 site, u32 control, u32 payload,
                                u64 exec, u64 token) {
    GpuCapturePacket packet{};
    packet.tag = CapturePacketTag;
    packet.epoch = 0;
    packet.site_pc = site;
    packet.encoded_control = control;
    packet.logical_slot = slot;
    packet.runtime_m0 = u32{0x20} << 16;
    packet.exec_lo = u32(exec);
    packet.exec_hi = u32(exec >> 32);
    packet.source_lane = exec ? u32(std::countr_zero(exec)) : NoActiveLane;
    packet.payload = payload;
    packet.token_lo = u32(token);
    packet.token_hi = u32(token >> 32);
    packet.flags = CaptureFlags;
    return packet;
}

static GpuCapturePacket Completed(u32 slot) {
    GpuCapturePacket packet{};
    packet.tag = CapturePacketTag;
    packet.site_pc = NoActiveLane;
    packet.logical_slot = slot;
    packet.source_lane = NoActiveLane;
    packet.flags = CaptureBypassFlags;
    return packet;
}

int main() {
    constexpr std::array<u32,2> sites{0x4158, 0x41d4};
    constexpr std::array<u64,4> ranks{2, 0, 3, 1};
    const auto order = VerifiedGuestCreationOrder::FromScheduler(ranks);
    assert(order);

    const auto w0 = Ordered(1, sites[0], 0x1300, 1, u64{1} << 63, 1000);
    const auto w1 = Ordered(3, sites[1], 0x0300, 1, 1, 1001);
    const auto w2 = Completed(0);
    const auto w3 = Ordered(2, sites[1], 0x0300, 2, (u64{1} << 7) | (u64{1} << 63), 1003);

    auto bad = w2;
    bad.payload = 2;
    assert(Reject([&]{ (void)DecodeCompletedGpuWave(bad, 0, *order); }));
    bad = w2;
    bad.token_lo = 1;
    assert(Reject([&]{ (void)DecodeCompletedGpuWave(bad, 0, *order); }));
    bad = w2;
    bad.site_pc = sites[0];
    assert(Reject([&]{ (void)DecodeCompletedGpuWave(bad, 0, *order); }));
    assert(DecodeCompletedGpuWave(w2, 0, *order) == 2);

    SparseOrderedGdsEpoch host(4, {{{0x20,0},100}}, sites);
    const std::array<GpuCapturePacket,4> packets{w3, w2, w1, w0};
    auto duplicates = packets;
    duplicates[1] = duplicates[0];
    assert(Reject([&]{ StageGpuCaptures(host,0,duplicates,*order); }));
    assert(host.Empty() && host.CounterSnapshot().at({0x20,0}) == 100);
    duplicates = packets;
    duplicates[3].token_lo = duplicates[2].token_lo;
    assert(Reject([&]{ StageGpuCaptures(host,0,duplicates,*order); }));
    assert(host.Empty());
    duplicates = packets;
    duplicates[0].payload = 5; // A forged ADDR value cannot drive a CIK packer.
    assert(Reject([&]{ StageGpuCaptures(host,0,duplicates,*order); }));
    assert(host.Empty());
    duplicates = packets;
    duplicates[3].site_pc = 0xdead;
    assert(Reject([&]{ StageGpuCaptures(host,0,duplicates,*order); }));
    assert(host.Empty());

    StageGpuCaptures(host,0,packets,*order);
    assert(host.Complete() && !host.Empty());
    assert(Reject([&]{ StageGpuCaptures(host,0,packets,*order); }));
    assert(host.CounterSnapshot().at({0x20,0}) == 100);
    const auto resumes = ResolveGpuCaptures(host,0,*order);
    assert(resumes.size()==4);
    assert(host.CounterSnapshot().at({0x20,0}) == 4);
    assert(resumes[0].tag == ResumePacketTag &&
           resumes[0].flags == 0 &&
           resumes[0].site_pc == NoActiveLane &&
           resumes[0].token_lo == 0 && resumes[0].token_hi == 0);
    assert(resumes[1].flags == ResumeFlags && resumes[1].previous == 100 &&
           resumes[1].current == 1 && resumes[1].site_pc == sites[0]);
    assert(resumes[3].flags == ResumeFlags && resumes[3].previous == 1 &&
           resumes[3].current == 2 && resumes[3].site_pc == sites[1]);
    assert(resumes[2].flags == ResumeFlags && resumes[2].previous == 2 &&
           resumes[2].current == 4 && resumes[2].site_pc == sites[1]);
    assert(Reject([&]{ (void)host.Resolve(); }));

    const std::array<u64,2> all_ranks{1,0};
    const auto order2=VerifiedGuestCreationOrder::FromScheduler(all_ranks);
    assert(order2);
    SparseOrderedGdsEpoch all_done(2, {}, sites);
    const std::array<GpuCapturePacket,2> only_done{Completed(0),Completed(1)};
    StageGpuCaptures(all_done,0,only_done,*order2);
    const auto no_resume=ResolveGpuCaptures(all_done,0,*order2);
    assert(no_resume.size()==2 && no_resume[0].flags==0 && no_resume[1].flags==0);
    assert(all_done.CounterSnapshot().empty());

    std::cout << "ORDERED_GDS_GPU_SPARSE_PACKET=PASS waves=4 "
                 "ordered=3 completed_without_gds=1 no_fake_exchange=1 "
                 "duplicate_slot_and_token_reject=1 no_resume_for_completed=1 "
                 "all_completed_without_counter=1 cik_exec_popcount=PASS NO_GPU_WORK\n";
}
