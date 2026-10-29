#include "shader_recompiler/frontend/ordered_gds_continuation.h"
#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>
using namespace Shader::Gcn::OrderedGds;

static std::uint64_t Enc(std::uint32_t c) {
    return (std::uint64_t(11) << 56) | (std::uint64_t(9) << 32) |
           (std::uint64_t(0x36) << 26) | (std::uint64_t(63) << 18) |
           (std::uint64_t(1) << 17) | (std::uint64_t(c) << 8);
}
static Descriptor Ex(){ return *DecodeInstruction(Enc(0x13)); }
static Descriptor Add(){ return *DecodeInstruction(Enc(0x03)); }
template<class F> bool Reject(F&& f) {
    try { f(); } catch(const std::invalid_argument&) { return true; }
    return false;
}

int main(){
    for(unsigned n=1;n<=99;++n) {
        ContinuationHandoff q({{{0x1234,0},100}});
        assert(Reject([&]{q.BeginEpoch(1,n);}));
        q.BeginEpoch(0,n);
        assert(Reject([&]{q.BeginEpoch(0,n);}));
        assert(!q.CapturedAll(0));
        assert(Reject([&]{(void)q.Resolve(0);}));
        std::uint32_t expected=100;
        for(unsigned i=0;i<n;++i) {
            const auto value=i+7;
            const auto op=i%3==0?Ex():Add();
            expected=op.operation==Operation::Exchange?value:expected+value;
        }
        for(unsigned i=n;i-- >0;) {
            const auto token=10000+i*11;
            q.Capture(0,{{i,0x1234u<<16,i+7,i%3==0?Ex():Add()},token});
            assert(Reject([&]{q.Capture(0,{{i,0x1234u<<16,999,Add()},token});}));
        }
        assert(q.CapturedAll(0));
        const auto ready=q.Resolve(0);
        assert(ready.size()==n);
        assert(q.CounterSnapshot().at({0x1234,0})==expected);
        assert(Reject([&]{(void)q.Resolve(0);}));
        assert(Reject([&]{q.BeginEpoch(1,1);}));
        assert(Reject([&]{(void)q.Resume(1,ready[0].continuation_token);}));
        for(auto& event:ready){
            const Result value=q.Resume(0,event.continuation_token);
            assert(value.creation_sequence==event.value.creation_sequence);
            assert(value.previous==event.value.previous);
            assert(value.current==event.value.current);
            assert(Reject([&]{(void)q.Resume(0,event.continuation_token);}));
        }
        assert(Reject([&]{q.BeginEpoch(0,n);}));
        // Synthetic two-epoch continuation test. A shader with different
        // static sites does not necessarily execute both sites in every wave;
        // mixed-site dispatch coverage is tested below.
        q.BeginEpoch(1,n);
        assert(Reject([&]{q.Capture(0,{{0,0x1234u<<16,10,Add()},999});}));
        assert(Reject([&]{(void)q.Resolve(1);}));
        std::uint32_t second_sum{};
        for(unsigned wave=n;wave-- >0;){
            const auto token=20000+wave*13;
            q.Capture(1,{{wave,0x1234u<<16,wave+2,Add()},token});
            second_sum+=wave+2;
        }
        assert(q.CapturedAll(1));
        const auto second=q.Resolve(1);
        assert(second.size()==n && second.front().value.previous==expected);
        assert(second.back().value.current==expected+second_sum);
        assert(q.CounterSnapshot().at({0x1234,0})==expected+second_sum);
        for (const auto& wave: second) {
            auto resumed=q.Resume(1,wave.continuation_token);
            assert(resumed.current==wave.value.current);
            assert(resumed.previous==wave.value.previous);
        }
        assert(Reject([&]{(void)q.Resume(1,20000);}));
        assert(Reject([&]{(void)q.Resolve(1);}));
    }
    ContinuationHandoff missing({{{0x20,0},3}});
    missing.BeginEpoch(0,2);
    missing.Capture(0,{{1,0x20u<<16,4,Add()},99});
    assert(Reject([&]{(void)missing.Resolve(0);}));
    assert(missing.CounterSnapshot().at({0x20,0})==3);
    missing.Capture(0,{{0,0x20u<<16,2,Ex()},101});
    auto ready=missing.Resolve(0);
    assert(ready.size()==2);
    assert(missing.CounterSnapshot().at({0x20,0})==6);
    for(auto& wave:ready)(void)missing.Resume(0,wave.continuation_token);
    // No-active-lane waves must still deliver a continuation token and
    // advance order. Inactive Exchange must leave the live counter intact.
    ContinuationHandoff empty_exec({{{0x20,0},9}});
    empty_exec.BeginEpoch(0,2);
    empty_exec.Capture(0,{{1,0x20u<<16,0,Ex(),false},9991});
    empty_exec.Capture(0,{{0,0x20u<<16,6,Add()},9990});
    const auto inactive_ready=empty_exec.Resolve(0);
    assert(inactive_ready.size()==2 && inactive_ready[1].value.previous==15);
    assert(inactive_ready[1].value.current==15);
    assert(empty_exec.CounterSnapshot().at({0x20,0})==15);
    for (const auto& continuation : inactive_ready) {
        (void)empty_exec.Resume(0, continuation.continuation_token);
    }
    // Each wave may reach a DIFFERENT static site of a shader. Two sites
    // with wave_done may be mutually exclusive, not two full-wave epochs.
    // A single epoch must collect exactly one request per created wave,
    // then return a continuation to that wave's actual instruction PC.
    ContinuationHandoff mixed({{{0x20,0},100}});
    const std::array<u32,2> valid_sites{17,19};
    mixed.BeginEpoch(0,4,valid_sites);
    assert(Reject([&]{mixed.BeginEpoch(0,4,valid_sites);}));
    assert(Reject([&]{mixed.Capture(0,{{0,0x20u<<16,9,Ex()},911,23});}));
    assert(Reject([&]{mixed.Capture(0,{{0,0x20u<<16,9,Ex()},911});}));
    assert(!mixed.CapturedAll(0));
    assert(mixed.CounterSnapshot().at({0x20,0}) == 100);
    // Supply out of sequence to prove host arrival != creation order.
    mixed.Capture(0,{{3,0x20u<<16,5,Add()},3103,19});
    mixed.Capture(0,{{1,0x20u<<16,3,Add()},3101,19});
    mixed.Capture(0,{{0,0x20u<<16,7,Ex()},3100,17});
    mixed.Capture(0,{{2,0x20u<<16,4,Add()},3102,17});
    assert(mixed.CapturedAll(0));
    const auto mixed_ready=mixed.Resolve(0);
    assert(mixed_ready.size()==4 && mixed.CounterSnapshot().at({0x20,0})==19);
    assert(Reject([&]{mixed.BeginEpoch(1,1,valid_sites);}));
    for (const auto& wave : mixed_ready) {
        const auto expected_pc=wave.value.creation_sequence%2==0?17u:19u;
        assert(wave.site_pc && *wave.site_pc == expected_pc);
        const auto resumed=mixed.ResumeCaptured(0,wave.continuation_token);
        assert(resumed.site_pc==wave.site_pc &&
               resumed.value.previous==wave.value.previous &&
               resumed.value.current==wave.value.current);
        assert(Reject([&]{(void)mixed.ResumeCaptured(0,wave.continuation_token);}));
    }
    mixed.BeginEpoch(1,1,valid_sites);
    mixed.Capture(1,{{0,0x20u<<16,2,Add()},4100,19});
    const auto repeated_site=mixed.Resolve(1);
    assert(repeated_site.size()==1 && repeated_site[0].site_pc==19);
    assert(repeated_site[0].value.previous==19 && repeated_site[0].value.current==21);
    const auto resumed_repeat=mixed.ResumeCaptured(1,4100);
    assert(resumed_repeat.value.current==21 && resumed_repeat.site_pc==19);
    assert(Reject([&]{mixed.BeginEpoch(1,1,valid_sites);}));
    assert(mixed.CounterSnapshot().at({0x20,0})==21);
    std::cout << "ORDERED_GDS_MIXED_STATIC_SITES=PASS creation_order=4 divergent_sites=2"
                 " dynamic_epoch=2 stale_token_reject=1 NO_GPU_WORK\n";
    std::cout << "ORDERED_GDS_HOST_CONTINUATION=PASS epochs=198 full_second_wave_batches=99 cases=99"
                 " missing_reject=1 stale_reject=1 no_gpu_spinlock=1 NO_GPU_WORK\n";
}
