#include "shader_recompiler/frontend/ordered_gds_capture_packet.h"
#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <stdexcept>
using namespace Shader::Gcn::OrderedGds;

template<class F> bool Reject(F&& f) {try { f(); }catch(const std::invalid_argument&){return true;}return false;}
static GpuCapturePacket Make(u32 slot,u32 site,u32 control,u32 payload,u64 exec,u64 token) {
    GpuCapturePacket p{};
    p.tag=CapturePacketTag;p.epoch=0;p.site_pc=site;p.encoded_control=control;
    p.logical_slot=slot;p.runtime_m0=0x20u<<16;
    p.exec_lo=u32(exec);p.exec_hi=u32(exec>>32);
    p.source_lane=exec?u32(std::countr_zero(exec)):NoActiveLane;
    p.payload=payload;p.token_lo=u32(token);p.token_hi=u32(token>>32);
    p.flags=CaptureFlags;
    return p;
}
int main(){
    constexpr std::array<u32,2> sites{0x4158,0x41d4};
    constexpr std::array<u64,4> rank_by_slot{2,0,3,1};
    auto verified=VerifiedGuestCreationOrder::FromScheduler(rank_by_slot);
    assert(verified && verified->Size()==4);
    assert(verified->RankForSlot(0)==2 && verified->SlotForRank(0)==1);
    assert(!VerifiedGuestCreationOrder::FromScheduler(std::array<u64,4>{0,1,1,3}));
    assert(!VerifiedGuestCreationOrder::FromScheduler(std::array<u64,2>{0,2}));
    assert(!VerifiedGuestCreationOrder::FromScheduler(std::array<u64,0>{}));
    assert(Reject([&]{(void)verified->RankForSlot(4);}));

    auto w2=Make(0,sites[0],0x1300,0,0,0x100000003ULL); // rank2 inactive exchange
    auto w0=Make(1,sites[0],0x1300,7,u64(1)<<63,0x100000001ULL); // rank0 exchange
    auto w3=Make(2,sites[1],0x0300,5,u64(1)<<7,0x100000004ULL); // rank3 add
    auto w1=Make(3,sites[1],0x0300,3,1,0x100000002ULL); // rank1 add
    const auto decoded=DecodeGpuCapture(w0,0,*verified);
    assert(decoded.request.creation_sequence==0);
    assert(decoded.request.instruction.operation==Operation::Exchange);
    assert(decoded.request.has_valid_lane);
    assert(decoded.site_pc==sites[0]);
    auto bad=w0; bad.source_lane=0;
    assert(Reject([&]{(void)DecodeGpuCapture(bad,0,*verified);}));
    bad=w2;bad.payload=1;
    assert(Reject([&]{(void)DecodeGpuCapture(bad,0,*verified);}));
    bad=w0;bad.flags=0;
    assert(Reject([&]{(void)DecodeGpuCapture(bad,0,*verified);}));
    bad=w0;bad.encoded_control |= (1u<<16);
    assert(Reject([&]{(void)DecodeGpuCapture(bad,0,*verified);}));
    bad=w0;bad.logical_slot=4;
    assert(Reject([&]{(void)DecodeGpuCapture(bad,0,*verified);}));
    bad=w0;bad.token_lo=bad.token_hi=0;
    assert(Reject([&]{(void)DecodeGpuCapture(bad,0,*verified);}));

    ContinuationHandoff host({{{0x20,0},100}});
    host.BeginEpoch(0,4,sites);
    constexpr std::array<GpuCapturePacket,0> empty{};
    assert(Reject([&]{StageGpuCaptures(host,0,empty,*verified);}));
    // Deliberately unordered GPU arrivals. The separately supplied guest
    // creation mapping, not physical arrival order, drives Exchange/Add.
    const std::array<GpuCapturePacket,4> incoming{w3,w2,w1,w0};
    StageGpuCaptures(host,0,incoming,*verified);
    assert(host.CapturedAll(0));
    auto resume=ResolveGpuCaptures(host,0,*verified);
    assert(resume.size()==4);
    assert(host.CounterSnapshot().at({0x20,0})==15);
    assert(resume[1].previous==100 && resume[1].current==7); // rank0
    assert(resume[3].previous==7 && resume[3].current==10); // rank1
    assert(resume[0].previous==10 && resume[0].current==10); // inactive rank2
    assert(resume[2].previous==10 && resume[2].current==15); // rank3
    for(const auto& p:resume){
        assert(p.tag==ResumePacketTag && p.flags==ResumeFlags);
        assert(p.site_pc==sites[p.logical_slot==0||p.logical_slot==1?0:1]);
        const u64 token=(u64(p.token_hi)<<32)|p.token_lo;
        const auto consumed=host.ResumeCaptured(0,token);
        assert(consumed.site_pc==p.site_pc && consumed.value.current==p.current);
        assert(Reject([&]{(void)host.ResumeCaptured(0,token);}));
    }
    const std::array<u64,1> single_rank{0};
    auto single=VerifiedGuestCreationOrder::FromScheduler(single_rank);
    assert(single);
    host.BeginEpoch(1,1,sites);
    auto repeated=Make(0,sites[1],0x0300,2,1,5);
    repeated.epoch=1;
    host.Capture(1,DecodeGpuCapture(repeated,1,*single));
    auto second=ResolveGpuCaptures(host,1,*single);
    assert(second.size()==1 && second[0].previous==15 && second[0].current==17);
    (void)host.ResumeCaptured(1,5);
    std::cout<<"ORDERED_GDS_GPU_PACKET_ROUNDTRIP=PASS mixed_sites=2 waves=4 "
               "capture_order=permuted return_slots=correct inactive_exchange=preserved "
               "epoch_repeat=1 no_vulkan_execution=1\n";
}
