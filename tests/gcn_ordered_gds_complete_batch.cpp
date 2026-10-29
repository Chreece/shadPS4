#include "shader_recompiler/frontend/ordered_gds.h"
#include <array>
#include <cassert>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>
using namespace Shader::Gcn::OrderedGds;

static std::uint64_t Enc(unsigned offset1, unsigned offset0, unsigned addr=9, unsigned dst=11) {
    return (std::uint64_t(dst) << 56) | (std::uint64_t(addr) << 32) |
           (std::uint64_t(0x36) << 26) | (std::uint64_t(63) << 18) |
           (std::uint64_t(1) << 17) | (std::uint64_t(offset1) << 8) | offset0;
}
static Descriptor D(unsigned op,unsigned offset=0){return *DecodeInstruction(Enc(op,offset));}
static bool Bad(CompleteBatch& c,const std::vector<Request>& req,unsigned n) {
    try { c.Apply(req,n); return false; } catch(const std::invalid_argument&) { return true; }
}
int main(){
   assert(!DecodeInstruction(0).has_value());
   auto ex=D(0x13), add=D(0x03);
   assert(ex.operation==Operation::Exchange&&add.operation==Operation::Add);
   assert(ex.gds&&ex.wave_done&&ex.wave_release&&ex.addr_register==9&&ex.destination_register==11);
   for(unsigned n=1;n<100;n++){
      CompleteBatch c({{{0x1234,0},0}});
      std::vector<Request> requests;
      unsigned expected=0;
      for(unsigned i=0;i<n;i++){
         unsigned inc=i*13+3;
         requests.push_back({i,(0x1234u<<16u)|i,inc,(i%5==0)?ex:add});
         expected=(i%5==0)?inc:expected+inc;
      }
      std::reverse(requests.begin(), requests.end());
      const auto r=c.Apply(requests,n);
      assert(r.size()==n&&r.back().current==expected&&c.Counters().at({0x1234,0})==expected);
      assert(r[0].creation_sequence==0&&r.back().creation_sequence==n-1);
      assert(Bad(c,requests,n+1)&&c.Counters().at({0x1234,0})==expected);
      auto gap=requests; gap[0].creation_sequence=n+2;
      assert(Bad(c,gap,n)&&c.Counters().at({0x1234,0})==expected);
      auto reserved=requests;reserved[0].instruction.operation=Operation::Wrap;
      assert(Bad(c,reserved,n)&&c.Counters().at({0x1234,0})==expected);
      auto noGds=requests;noGds[0].instruction.gds=false;
      assert(Bad(c,noGds,n)&&c.Counters().at({0x1234,0})==expected);
      auto badDone=requests;badDone[0].instruction.wave_release=false;
      assert(Bad(c,badDone,n)&&c.Counters().at({0x1234,0})==expected);
      auto incomplete=requests;incomplete[0].instruction.wave_done=false;
      assert(Bad(c,incomplete,n)&&c.Counters().at({0x1234,0})==expected);
   }
   CompleteBatch distinct({{{0x10,0},5},{{0x20,0},50}});
   auto result=distinct.Apply(std::array<Request,2>{{{1,0x20u<<16,17,ex},{0,0x10u<<16,9,add}}},2);
   assert(result[0].previous==5&&result[0].current==14);
   assert(result[1].previous==50&&result[1].current==17);
   CompleteBatch wraps({{{0,0},std::numeric_limits<u32>::max()}});
   const auto wrapped=wraps.Apply(std::array<Request,1>{{{0,0,2,add}}},1);
   assert(wrapped[0].current==1);
   CompleteBatch missing({{{0,0},99}});
   assert(Bad(missing,{{0,0x00100000u,12,ex}},1));
   assert(missing.Counters().at({0,0})==99);
   std::cout << "ORDERED_GDS_COMPLETE_BATCH_CPU=PASS sequences=99 invariants=no_fake_counter,no_missing_wave,no_unordered_exchange,transactional,wrap32 NO_GPU_WORK\n";
}
