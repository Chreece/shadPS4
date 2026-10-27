#include "shader_recompiler/ir/passes/indexed_sample_eligibility.h"
#include "common/log_occurrence_gate.h"
#include <array>
#include <atomic>
#include <cstdint>
#include <iostream>
#include <thread>
#include <vector>
#include <cstdlib>
struct Flags {
  bool is_depth{},is_array{},is_gather{},has_lod{},force_level0{},has_lod_clamp{},is_r128{},is_unnormalized{};
};
using Shader::Optimization::IsBindableGuestIndexedSampleMode;
constexpr bool fixture() {
  for(unsigned mask=0;mask<256;++mask){
    Flags f{bool(mask&1),bool(mask&2),bool(mask&4),bool(mask&8),bool(mask&16),bool(mask&32),bool(mask&64),bool(mask&128)};
    if(IsBindableGuestIndexedSampleMode(f)!=(mask==0))return false;
  }
  return true;
}
static_assert(fixture());
int main() {
  if(!fixture())return 1;
  std::atomic<std::uint64_t> count{0};
  for(std::uint64_t i=1;i<=500000;++i){
    std::uint64_t event=0;
    bool reported=Common::ShouldReportRepeatedEvent(count,event);
    if(event!=i || reported!=(i<=4||(i&(i-1))==0))return 2;
  }
  std::atomic<std::uint64_t> shared{0};
  std::atomic<std::uint64_t> logs{0};
  std::array<std::thread,8> workers;
  for(auto& thread:workers)thread=std::thread([&]{
    for(std::size_t i=0;i<20000;++i){
      std::uint64_t n=0;
      if(Common::ShouldReportRepeatedEvent(shared,n)) logs.fetch_add(1);
    }
  });
  for(auto& thread:workers)thread.join();
  std::uint64_t expected=4;
  for(std::uint64_t n=8;n<=160000;n*=2)++expected;
  if(shared.load()!=160000||logs.load()!=expected)return 3;
  std::cout<<"INDEXED_SAMPLE_MODE_256_CASES=PASS\n";
  std::cout<<"HOTLOG_500000_SERIAL_160000_PARALLEL=PASS\n";
}
