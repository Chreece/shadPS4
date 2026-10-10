// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#define main reciprocal_benchmark_main
#include "probe-optimized.cpp"
#undef main
#include <sys/mman.h>
#include <unistd.h>
#include <cstring>
#include <string>
#include <sstream>

struct alignas(32) MachineState {
    uint32_t before[16][8];
    uint32_t after[16][8];
    uint64_t flags_wanted, flags_before, flags_after;
    uint64_t rax_before, rax_after, rcx_before, rcx_after, rsp_before, rsp_after;
    uint32_t mxcsr_before, mxcsr_after;
};
using StateFn = void(*)(MachineState*, const uint32_t*);
struct Oracle { uint32_t input, rcp, rsqrt; };
static std::unique_ptr<Xbyak::CodeGenerator> MakeStateProbe(bool rsqrt, bool vex, unsigned dst,
                                                         unsigned merge, unsigned src,
                                                         unsigned memory, const uint32_t* guard) {
    auto c=std::make_unique<Xbyak::CodeGenerator>(4096);
    c->pushfq();
    c->lea(rsp,ptr[rsp-256]);
    c->stmxcsr(dword[rsp+240]);
    for(unsigned i=0;i<16;++i)c->vmovdqu(Xbyak::Ymm(i),ptr[rdi+offsetof(MachineState,before)+i*32]);
    c->mov(rax,0x123456789abcdef0ULL);
    c->mov(rcx,0xfedcba9876543210ULL);
    ZydisDecodedInstruction ins{};
    ZydisDecodedOperand op[3]{};
    op[0].type=ZYDIS_OPERAND_TYPE_REGISTER;op[0].reg.value=ZydisRegister(ZYDIS_REGISTER_XMM0+dst);
    op[1].type=ZYDIS_OPERAND_TYPE_REGISTER;op[1].reg.value=ZydisRegister(ZYDIS_REGISTER_XMM0+merge);
    auto& input=op[vex?2:1];
    void* address=nullptr;
    if(memory) {
        input.type=ZYDIS_OPERAND_TYPE_MEMORY;input.mem.index=ZYDIS_REGISTER_NONE;
        input.mem.base=ZYDIS_REGISTER_RSI;
        if(memory==2){input.mem.base=ZYDIS_REGISTER_RAX;c->mov(rax,rsi);}
        if(memory==3){input.mem.base=ZYDIS_REGISTER_RCX;c->mov(rcx,rsi);}
        if(memory==4){input.mem.base=ZYDIS_REGISTER_RAX;input.mem.index=ZYDIS_REGISTER_RCX;input.mem.scale=4;c->lea(rax,ptr[rsi-32]);c->mov(rcx,8);}
        if(memory==5){input.mem.base=ZYDIS_REGISTER_RCX;input.mem.index=ZYDIS_REGISTER_RAX;input.mem.scale=4;c->lea(rcx,ptr[rsi-32]);c->mov(rax,8);}
        if(memory>=6 && memory<=8){
            int disp=memory==6?-4:memory==7?-128:48;
            c->mov(eax,dword[rsi]);c->mov(dword[rsp+disp],eax);
            input.mem.base=ZYDIS_REGISTER_RSP;input.mem.disp.size=32;input.mem.disp.value=disp;
            c->mov(rax,0x123456789abcdef0ULL);
        }
        if(memory==9){
            input.mem.base=ZYDIS_REGISTER_RIP;ins.length=7;input.mem.disp.size=32;input.mem.disp.value=57;
            address=reinterpret_cast<void*>(reinterpret_cast<uintptr_t>(guard)-64);
        }
    } else {
        input.type=ZYDIS_OPERAND_TYPE_REGISTER;input.reg.value=ZydisRegister(ZYDIS_REGISTER_XMM0+src);
    }
    c->mov(qword[rdi+offsetof(MachineState,rax_before)],rax);
    c->mov(qword[rdi+offsetof(MachineState,rcx_before)],rcx);
    c->mov(qword[rdi+offsetof(MachineState,rsp_before)],rsp);
    c->ldmxcsr(dword[rdi+offsetof(MachineState,mxcsr_before)]);
    c->push(qword[rdi+offsetof(MachineState,flags_wanted)]);c->popfq();
    c->pushfq();c->pop(qword[rdi+offsetof(MachineState,flags_before)]);
    if(memory>=6 && memory<=8){
        int disp=memory==6?-4:memory==7?-128:48;
        c->mov(r10d,dword[rsi]);c->mov(dword[rsp+disp],r10d);
    }
    flags_optimized::GenerateScalarReciprocalInstruction(address,ins,op,*c,rsqrt,vex);
    c->pushfq();c->pop(qword[rdi+offsetof(MachineState,flags_after)]);
    c->mov(qword[rdi+offsetof(MachineState,rax_after)],rax);
    c->mov(qword[rdi+offsetof(MachineState,rcx_after)],rcx);
    c->mov(qword[rdi+offsetof(MachineState,rsp_after)],rsp);
    c->stmxcsr(dword[rdi+offsetof(MachineState,mxcsr_after)]);
    for(unsigned i=0;i<16;++i)c->vmovdqu(ptr[rdi+offsetof(MachineState,after)+i*32],Xbyak::Ymm(i));
    c->ldmxcsr(dword[rsp+240]);c->lea(rsp,ptr[rsp+256]);c->popfq();c->vzeroupper();c->ret();c->ready();
    return c;
}
int main() {
    for(auto name:{"rcp","rsqrt"}){
        std::ifstream f(std::string("./")+name+".bin",std::ios::binary);
        auto& t=std::string(name)=="rcp"?rcp_index_table:rsqrt_index_table;
        f.read(reinterpret_cast<char*>(t.data()),t.size());if(!f)abort();
    }
    for(auto* table:{&rcp_index_table,&rsqrt_index_table})for(unsigned sign=0;sign<2;++sign)
        for(unsigned i=(0x7f800000U>>11)+(sign<<20);i<((sign+1)<<20);++i)if((*table)[i]!=0)abort();
    std::vector<Oracle> oracle;std::ifstream refs("./oracle.tsv");Oracle row;
    while(refs>>std::hex>>row.input>>row.rcp>>row.rsqrt)oracle.push_back(row);
    if(oracle.empty())abort();
    const size_t page=sysconf(_SC_PAGESIZE);
    auto* region=static_cast<char*>(mmap(nullptr,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0));
    if(region==MAP_FAILED||mprotect(region+page,page,PROT_NONE))abort();
    auto* guard=reinterpret_cast<uint32_t*>(region+page-4);
    uint64_t cases_run=0;
    auto check=[&](StateFn fn,bool sqrt,bool vex,unsigned dst,unsigned merge,unsigned src,unsigned mem,const Oracle& value,unsigned flags){
        MachineState s{};
        for(unsigned i=0;i<16;++i)for(unsigned lane=0;lane<8;++lane)s.before[i][lane]=0x41200000U+i*0x1000+lane;
        if(!mem)s.before[src][0]=value.input;
        *guard=value.input;
        static const uint64_t bits[]={1,4,16,64,128,2048};
        s.flags_wanted=0x202;
        for(unsigned bit=0;bit<6;++bit)if(flags&(1<<bit))s.flags_wanted|=bits[bit];
        s.mxcsr_before=flags%3==0?0:flags%3==1?0x1f80:0xffff;
        uint32_t expected[16][8];memcpy(expected,s.before,sizeof expected);
        for(unsigned lane=1;lane<8;++lane)expected[dst][lane]=vex?(lane<4?s.before[merge][lane]:0):s.before[dst][lane];
        expected[dst][0]=sqrt?value.rsqrt:value.rcp;
        fn(&s,guard);
        bool good=memcmp(expected,s.after,sizeof expected)==0 && s.flags_before==s.flags_after && s.rax_before==s.rax_after && s.rcx_before==s.rcx_after && s.rsp_before==s.rsp_after && s.mxcsr_before==s.mxcsr_after;
        if(!good){fprintf(stderr,"FAIL sqrt=%d vex=%d dst=%u merge=%u src=%u mem=%u input=%08x flags=%llx/%llx mxcsr=%x/%x rax=%llx/%llx rcx=%llx/%llx rsp=%llx/%llx\n",sqrt,vex,dst,merge,src,mem,value.input,(unsigned long long)s.flags_before,(unsigned long long)s.flags_after,s.mxcsr_before,s.mxcsr_after,(unsigned long long)s.rax_before,(unsigned long long)s.rax_after,(unsigned long long)s.rcx_before,(unsigned long long)s.rcx_after,(unsigned long long)s.rsp_before,(unsigned long long)s.rsp_after);exit(1);}
        ++cases_run;
    };
    for(bool sqrt:{false,true})for(bool vex:{false,true})for(unsigned dst=0;dst<16;++dst)for(unsigned merge=0;merge<(vex?16U:1U);++merge)for(unsigned src=0;src<16;++src){
        auto code=MakeStateProbe(sqrt,vex,dst,merge,src,0,guard);unsigned k=0;
        for(const auto& value:oracle)check(code->getCode<StateFn>(),sqrt,vex,dst,merge,src,0,value,(dst+merge+src+k++)%64);
    }
    printf("REGISTER_ALIAS_CASES=%llu PASS\n",(unsigned long long)cases_run);
    auto register_cases=cases_run;
    for(bool sqrt:{false,true})for(bool vex:{false,true})for(unsigned mem=1;mem<=9;++mem)for(unsigned dst:{0U,7U,15U})for(unsigned merge:{0U,7U,15U}){
        auto code=MakeStateProbe(sqrt,vex,dst,merge,0,mem,guard);
        for(unsigned flags=0;flags<64;++flags)for(const auto& value:oracle)check(code->getCode<StateFn>(),sqrt,vex,dst,merge,0,mem,value,flags);
    }
    printf("MEMORY_FLAGS_CASES=%llu PASS\n",(unsigned long long)(cases_run-register_cases));
    printf("GUARD_PAGE_WIDTH=4 PASS\nTABLE_NONFINITE_BUCKETS_ZERO=PASS\nTOTAL_CASES=%llu PASS\n",(unsigned long long)cases_run);
    munmap(region,page*2);
}
