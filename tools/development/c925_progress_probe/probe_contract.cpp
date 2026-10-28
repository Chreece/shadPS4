// SPDX-License-Identifier: GPL-2.0-or-later
#include "execution_progress_probe.h"
#include <array>
#include <cassert>
#include <cstdio>
#include <thread>
#include <vector>
#include <limits>
using namespace ExecutionProgress;
#define CHECK(x) do { ++checks; if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x);return 1; } } while(0)
int main() {
 unsigned checks=0; Start();
 const bool requested=std::getenv("SHADPS4_PROGRESS_FILE") != nullptr;
 CHECK(Enabled()==requested);
 if(!requested) { Scope s(Phase::Draw);CHECK(storage.load()==nullptr);std::puts("PROBE_DISABLED=PASS");return 0; }
 auto* p=storage.load();CHECK(p[0]==Magic);CHECK(p[2]==Bytes);
 {
  Scope outer(Phase::Draw,123,456);
  const unsigned b=SlotBase+local_slot*SlotWords;
  CHECK(p[b+2]==1);CHECK(p[b+4+2]==123);CHECK(p[b+4+3]==456);
  {
   Scope inner(Phase::TimelineWait,111,500,400,2);
   CHECK(p[b+2]==2);const unsigned e=b+4+Fields;
   CHECK(p[e]==Word(Phase::TimelineWait));CHECK(p[e+3]==500);CHECK(p[e+4]==400);
   inner.Update(111,500,450,2);CHECK(p[e+3]==500);CHECK(p[e+4]==450);CHECK((p[b]&1)==0);
  }
  CHECK(p[b+2]==1);CHECK(p[b+4+2]==123);
 }
 CHECK(p[SlotBase+local_slot*SlotWords+2]==0);
 constexpr unsigned n=8,iterations=1000;
 std::vector<std::thread> threads;
 for(unsigned i=0;i<n;++i)threads.emplace_back([i]{for(unsigned j=0;j<iterations;++j){Scope x(Phase::Dispatch,i,j);x.Update(i,j,42,9);}});
 for(auto& t:threads)t.join();
 const unsigned c=Header+unsigned(Phase::Dispatch)*CounterWords;
 CHECK(p[c]==n*iterations);CHECK(p[c+1]==n*iterations);CHECK(p[c+2]>=p[c+3]);CHECK(p[c+3]>0);
 for(unsigned i=0;i<p[5];++i){CHECK(p[SlotBase+i*SlotWords+2]==0);CHECK((p[SlotBase+i*SlotWords]&1)==0);}
 CHECK(p[6]==0);
 std::printf("PROGRESS_PROBE_CONTRACT=PASS checks=%u scopes=%u\n",checks,n*iterations+2);
}
