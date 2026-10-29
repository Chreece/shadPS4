// CPU-only ordered GDS reference semantics and decoder tests.
// This does not provide a Vulkan/SPIR-V implementation or emulate all M0 scheduling.
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <stdexcept>
#include <vector>

using u32 = std::uint32_t;
using u64 = std::uint64_t;
enum class OrderedOpcode : u32 { Add=0, Exchange=1, Reserved=2, Wrap=3 };
struct OrderedInstruction {
    u32 offset0{}, offset1{};
    bool gds{}, wave_release{}, wave_done{};
    OrderedOpcode op{};
    u32 counter_index{}, addr_vgpr{}, data0_vgpr{}, data1_vgpr{}, dest_vgpr{};
};

void require(bool okay, const char* msg) {
    if (!okay) { std::fprintf(stderr,"ORDERED_COUNT_MODEL_FAIL=%s\n",msg); std::exit(2); }
}

OrderedInstruction decode(u64 raw) {
    require(((raw>>26)&63u)==0x36, "must be DS encoding");
    require(((raw>>18)&255u)==63, "must be DS_ORDERED_COUNT opcode");
    OrderedInstruction d;
    d.offset0=raw&255u; d.offset1=(raw>>8)&255u;
    d.gds=((raw>>17)&1u)!=0; d.wave_release=(d.offset1&1u)!=0;
    d.wave_done=(d.offset1&2u)!=0;
    d.op=static_cast<OrderedOpcode>((d.offset1>>4)&3u);
    d.counter_index=d.offset0>>2;
    d.addr_vgpr=(raw>>32)&255u; d.data0_vgpr=(raw>>40)&255u;
    d.data1_vgpr=(raw>>48)&255u; d.dest_vgpr=(raw>>56)&255u;
    return d;
}

u64 build(u32 operation,u32 offset,u32 vaddr,u32 vdest,bool gds=true) {
    return (static_cast<u64>(vdest)<<56) | (static_cast<u64>(vaddr)<<32) |
           (static_cast<u64>(0x36)<<26) | (static_cast<u64>(63)<<18) |
           (static_cast<u64>(gds)<<17) | (static_cast<u64>(operation)<<8) | offset;
}

struct Request { u32 wave_id; u32 increment; OrderedOpcode op; u32 counter; };
struct Result { u32 wave_id; u32 old_value; u32 new_value; };
std::vector<Result> cpu_reference(std::vector<Request> entries) {
    // The reference only models the guaranteed wave-creation-order result for
    // already supplied logical wave identities. It does not model hardware's
    // wave crawler, readiness or cross-wave scheduling.
    std::stable_sort(entries.begin(),entries.end(),[](const Request &a,const Request &b){
        return a.wave_id < b.wave_id;
    });
    std::map<u32,u32> counters;
    std::vector<Result> res;
    for(const auto& entry: entries) {
        auto& cur = counters[entry.counter];
        const u32 old=cur;
        switch(entry.op) {
        case OrderedOpcode::Add: cur+=entry.increment; break;
        case OrderedOpcode::Exchange: cur=entry.increment; break;
        default: throw std::invalid_argument("unsupported ordered index operation");
        }
        res.push_back({entry.wave_id,old,cur});
    }
    return res;
}

int main() {
    // Synthetic DS encodings only. Guest instruction words are NOT published.
    const auto x=decode(build(0x13,0,9,11));
    const auto a=decode(build(0x03,0,9,11));
    require(x.gds && x.op==OrderedOpcode::Exchange && x.wave_release && x.wave_done,
            "exchange flags and GDS");
    require(a.gds && a.op==OrderedOpcode::Add && a.wave_release && a.wave_done,
            "add flags and GDS");
    require(x.counter_index==0 && a.counter_index==0 &&
            x.addr_vgpr==9 && a.dest_vgpr==11, "counter and registers");
    require((0x13&0x10)!=0 && (0x03&0x10)==0, "do not equate exchange and add");

    unsigned tests=0;
    for(u32 n=2;n<=32;n++) {
        std::vector<Request> shuffled;
        // Each wave sends one counter update with distinct non-unit values.
        for(u32 i=0;i<n;i++)shuffled.push_back({n-1-i, (i%5)+2,
            i%3==0 ? OrderedOpcode::Exchange:OrderedOpcode::Add,0});
        auto sorted=cpu_reference(shuffled);
        require(sorted.size()==n,"all requests processed");
        for(u32 i=1;i<n;i++)require(sorted[i-1].wave_id<sorted[i].wave_id,
            "wave-creation order must dominate arrival order");
        for(u32 i=0;i<n;i++) {
            auto& cur=sorted[i];
            require(cur.old_value==(i?sorted[i-1].new_value:0),"sequential return values");
            auto orig=std::find_if(shuffled.begin(),shuffled.end(),[&](const auto& e){return e.wave_id==cur.wave_id;});
            require(orig!=shuffled.end(),"source request found");
            const u32 expected=orig->op==OrderedOpcode::Exchange ? orig->increment :
                               cur.old_value+orig->increment;
            require(cur.new_value==expected,"reference add versus exchange");
        }
        ++tests;
    }
    // An ordinary atomic-add substitution changes the result of exchange.
    auto witness=cpu_reference({{1,13,OrderedOpcode::Exchange,0},
                                {0,7,OrderedOpcode::Add,0}});
    require(witness[0].new_value==7 && witness[1].old_value==7 &&
            witness[1].new_value==13,"atomic-add exchange substitution invalid");
    std::printf("ORDERED_GDS_CPU_REFERENCE=PASS sequences=%u synthetic_ops=2 "
                "FIRST_VALID_LANE_AND_M0_INPUT_REQUIRED NO_GPU_WORK\n",tests);
}
