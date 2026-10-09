// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cassert>
#include <cstdio>
#include <csignal>
#include <csetjmp>
#include <fstream>
#include <string>
#include <sys/mman.h>
#include "core/memory.h"
#include "core/signals.h"
#include "video_core/renderer_vulkan/vk_rasterizer.h"
static sigjmp_buf target;
static volatile sig_atomic_t guest_faults=0;
static void handler(int, siginfo_t* info, void* ctx){
 if(!Core::Signals::Instance()->handler(ctx,info->si_addr)){guest_faults=guest_faults+1;siglongjmp(target,1);}
}
static std::string perms(VAddr address){
 std::ifstream input("/proc/self/maps");std::string line;
 while(std::getline(input,line)){unsigned long a,b;char p[5];if(sscanf(line.c_str(),"%lx-%lx %4s",&a,&b,p)==3 && address>=a && address<b)return p;}
 return "";
}
int main(){
 using P=Core::MemoryPermission;using O=VideoCore::PageOp;
 struct sigaction sa{};sa.sa_sigaction=handler;sa.sa_flags=SA_SIGINFO;sigaction(SIGSEGV,&sa,nullptr);
 VAddr a=0x400000000ULL;
 assert(mmap((void*)a,16384,PROT_READ|PROT_WRITE,MAP_FIXED_NOREPLACE|MAP_PRIVATE|MAP_ANONYMOUS,-1,0)==(void*)a);
 auto* data=(volatile unsigned*)a;*data=123;
 Vulkan::Rasterizer r;r.start=a;r.end=a+16384;
 VideoCore::PageManager m(&r);r.manager=&m;m.MapMemory(a,16384);m.ProtectMemory(a,16384,P::ReadWriteExecute);
 m.UpdatePageWatchers(a,4096,O::Track);assert(perms(a)=="r-xp");
 *data=456;assert(r.writes==1 && *data==456 && guest_faults==0 && perms(a)=="rwxp");
 m.ProtectMemory(a,4096,P::Read);m.UpdatePageWatchers(a,4096,O::Track);
 if(sigsetjmp(target,1)==0){*data=789;assert(false);}
 assert(guest_faults==1 && r.writes==1);
 m.UpdatePageWatchers(a,4096,O::Untrack);assert(perms(a)=="r--p");
 m.ProtectMemory(a,4096,P::ReadWrite);m.UpdatePageWatchers(a,4096,O::Track);m.ProtectMemory(a,4096,P::None);
 if(sigsetjmp(target,1)==0){unsigned x=*data;(void)x;assert(false);}
 assert(guest_faults==2 && r.reads==0);
 m.ProtectMemory(a,4096,P::ReadWrite);assert(perms(a)=="r--p");*data=999;assert(r.writes==2);
 VideoCore::RegionBits bits{};bits.Fill(false);bits[0]=1;
 m.UpdatePageWatchersForRegion(a,{0,0,0,0},bits,bits,O::Track,O::Track);
 assert(perms(a)=="---p");unsigned value=*data;assert(value==999 && r.reads==1);
 m.ProtectMemory(a,4096,P::Read);m.ProtectMemory(a+4096,4096,P::ReadWriteExecute);
 m.UpdatePageWatchers(a,8192,O::Track);m.UpdatePageWatchers(a,8192,O::Untrack);
 assert(perms(a)=="r--p" && perms(a+4096)=="rwxp");
 ucontext_t context{};context.uc_mcontext.gregs[REG_ERR]=2;
 assert(Core::Signals::Instance()->handler(&context,(void*)(a+4096)) && r.writes==2);
 context.uc_mcontext.gregs[REG_ERR]=16;assert(!Core::Signals::Instance()->handler(&context,(void*)a));
 m.OnGpuMap(0x10000000000ULL,0xeffffffffffULL);
 assert(munmap((void*)a,16384)==0);
 puts("PASS: GPU watch read/write handling, guest fault ownership, permission restoration, execute-bit preservation, mixed-page batching, stale fault retry, high re-registration");
}
