// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <numeric>
#include <random>
#include <stdexcept>
#include <vector>
#include "srt_dependency_schedule.h"
using namespace Shader::Optimization;
static void check(bool ok,const char* reason) {if(!ok)throw std::runtime_error(reason);}
int main(){try{
 std::mt19937 rng(0x5a71u);size_t graphs=0,vertices=0,old_failures=0;
 for(size_t n=1;n<=64;++n)for(unsigned trial=0;trial<64;++trial){
  std::vector<std::vector<size_t>> deps(n);std::vector<size_t> order(n);std::iota(order.begin(),order.end(),0);
  std::shuffle(order.begin(),order.end(),rng);
  for(size_t i=1;i<n;++i){deps[i].push_back(i-1);if(i>2)deps[i].push_back(rng()%i);}
  std::vector<bool> ready(n),old(n);
  for(const auto i:order){bool available=true;for(const auto d:deps[i])available=available&&old[d];if(available)old[i]=true;}
  if(!std::all_of(old.begin(),old.end(),[](bool x){return x;}))++old_failures;
  auto local=ScheduleSrtReads(n,[&](size_t item){
   auto i=order[item];for(auto d:deps[i])if(!ready[d])return SrtReadAttempt::Deferred;
   ready[i]=true;return SrtReadAttempt::Complete;
  });
  check(local.completed==n&&local.pending==0&&local.unsupported==0&&local.sweeps<=n,"local DAG scheduling");
  std::fill(ready.begin(),ready.end(),false);size_t words=16;
  auto root=ScheduleSrtRoots(65535,[&]{return words;},[&]{
   size_t pending=0;for(auto i:order){if(ready[i])continue;bool available=true;
    for(auto d:deps[i])available=available&&ready[d];
    if(available){ready[i]=true;++words;}else ++pending;}
   return pending;
  });
  check(root.pending==0&&!root.invalid_progress&&root.new_words==n&&root.rounds<=n,"cross-root DAG scheduling");
  ++graphs;vertices+=n;
 }
 check(old_failures>0,"old single sweep negative control did not fail");
 unsigned unknown_calls=0;
 auto unknown=ScheduleSrtReads(3,[&](size_t i){if(i==0){++unknown_calls;return SrtReadAttempt::Unsupported;}
  return i==1?SrtReadAttempt::Complete:SrtReadAttempt::Deferred;});
 check(unknown.unsupported==1&&unknown_calls==1&&unknown.pending==1&&unknown.completed==1,"unsupported retried or guessed");
 auto empty=ScheduleSrtReads(0,[](size_t){throw std::runtime_error("empty callback");return SrtReadAttempt::Deferred;});
 check(empty.sweeps==0,"empty scheduling");
 size_t count=65534;
 auto full=ScheduleSrtRoots(65535,[&]{return count;},[&]{++count;return size_t{1};});
 check(full.rounds==1&&full.pending==1&&!full.invalid_progress,"capacity");
 auto overflow=ScheduleSrtRoots(65535,[&]{return count;},[&]{++count;return size_t{0};});
 check(overflow.invalid_progress,"overflow accepted");count=30;
 auto backward=ScheduleSrtRoots(65535,[&]{return count;},[&]{--count;return size_t{1};});
 check(backward.invalid_progress,"backward progress accepted");
 auto cycle=ScheduleSrtRoots(65535,[&]{return count;},[]{return size_t{2};});
 check(cycle.pending==2&&cycle.rounds==1&&!cycle.invalid_progress,"cycle did not stop");
 std::cout<<"SRT_SCHEDULER=PASS graphs="<<graphs<<" vertices="<<vertices<<" old_single_sweep_failures="<<old_failures<<" cycles_capacity_unknown=PASS\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 2;}}
