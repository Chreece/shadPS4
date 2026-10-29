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
   // PS4/legacy-GCN payload is active-lane count, not a hardcoded increment.
   assert(ResolveGuestPayload(GuestPayloadMode::LegacyGcnExecCount, {0, std::nullopt})==0);
   assert(ResolveGuestPayload(GuestPayloadMode::LegacyGcnExecCount, {0xfffffULL, std::nullopt})==20);
   assert(ResolveGuestPayload(GuestPayloadMode::LegacyGcnExecCount, {~u64{0}, 777u})==64);
   // Modern RDNA3 payload comes from the first active lane's ADDR VGPR.
   assert(ResolveGuestPayload(GuestPayloadMode::RdnaFirstValidLaneValue, {0, std::nullopt})==0);
   assert(ResolveGuestPayload(GuestPayloadMode::RdnaFirstValidLaneValue, {0x81, 17u})==17);
   bool missing_rdna_input_rejected=false;
   try { (void)ResolveGuestPayload(GuestPayloadMode::RdnaFirstValidLaneValue,
                                  {0x80, std::nullopt}); }
   catch (const std::invalid_argument&) { missing_rdna_input_rejected=true; }
   assert(missing_rdna_input_rejected);

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
   // Counter base is DWORD-aligned; the low two M0 base bits are ignored.
   CompleteBatch base_alias({{{0x1010,0},8}});
   auto aliased=base_alias.Apply(std::array<Request,1>{{{0,0x1013u<<16,5,add}}},1);
   assert(aliased[0].previous==8 && aliased[0].current==13);
   assert(base_alias.Counters().size()==1 && base_alias.Counters().at({0x1010,0})==13);
   // A collector takes out-of-order wave events without a busy-wait, then
   // performs exactly one complete resolution. Missing waves never mutate GDS.
   for(unsigned wave_total=1;wave_total<=99;wave_total++){
      CompleteWaveCollector collector(wave_total,{{{0x20,0},7}});
      assert(!collector.Complete());
      unsigned expected_value=7;
      for(unsigned i=0;i<wave_total;i++){
          auto payload=i+2;
          auto instruction=i%4==0?ex:add;
          expected_value=instruction.operation==Operation::Exchange?payload:expected_value+payload;
      }
      for(unsigned i=wave_total;i-- >0;){
          collector.Submit({i,0x20u<<16,i+2,i%4==0?ex:add});
      }
      assert(collector.Complete());
      const auto result=collector.Resolve();
      assert(result.size()==wave_total && result.back().current==expected_value);
      assert(collector.Counters().at({0x20,0})==expected_value);
      bool second_rejected=false;
      try { (void)collector.Resolve(); } catch(const std::invalid_argument&) {second_rejected=true;}
      assert(second_rejected);
   }
   CompleteWaveCollector incomplete_collector(2,{{{0x10,0},9}});
   incomplete_collector.Submit({1,0x10u<<16,100,ex});
   assert(!incomplete_collector.Complete());
   bool premature_rejected=false;
   try{(void)incomplete_collector.Resolve();}catch(const std::invalid_argument&){premature_rejected=true;}
   assert(premature_rejected && incomplete_collector.Counters().at({0x10,0})==9);
   bool duplicate_rejected=false;
   try{incomplete_collector.Submit({1,0x10u<<16,100,ex});}catch(const std::invalid_argument&){duplicate_rejected=true;}
   assert(duplicate_rejected);
   CompleteBatch missing({{{0,0},99}});
   assert(Bad(missing,{{0,0x00100000u,12,ex}},1));
   assert(missing.Counters().at({0,0})==99);
   std::cout << "ORDERED_GDS_COMPLETE_BATCH_CPU=PASS sequences=99 invariants=no_fake_counter,no_missing_wave,no_unordered_exchange,transactional,wrap32 NO_GPU_WORK\n";
}
