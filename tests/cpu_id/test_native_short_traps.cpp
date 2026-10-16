// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Standalone Linux x86-64 regression for the actual in-place UD2 CPU-ID
// interception mechanism. Uses a small synthetic executable instruction
// stream. The callback simulates guest semantics; the integrated emulator
// uses HandleCpuIdNativeTrap and existing guest CPUID fault trampolines.
#define _GNU_SOURCE
#include <cassert>
#include <cstdint>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <sys/mman.h>
#include <ucontext.h>
#include <unistd.h>

#include "core/cpu_id_native_traps.h"

static volatile sig_atomic_t traps_seen=0;
static volatile sig_atomic_t expected_kind=-1;

static void OnIllegal(int signal, siginfo_t*, void* context) {
    assert(signal==SIGILL);
    auto& regs=static_cast<ucontext_t*>(context)->uc_mcontext.gregs;
    const auto pc=static_cast<uintptr_t>(regs[REG_RIP]);
    const auto* entry=Core::NativeCpuId::TrapRegistry::Lookup(pc);
    assert(entry!=nullptr);
    assert(reinterpret_cast<const unsigned char*>(pc)[0]==0x0f);
    assert(reinterpret_cast<const unsigned char*>(pc)[1]==0x0b);
    assert(static_cast<unsigned>(entry->instruction)==
           static_cast<unsigned>(expected_kind));
    ++traps_seen;
    regs[REG_RAX]=static_cast<greg_t>(11+
        static_cast<unsigned>(entry->instruction));
    regs[REG_RIP]+=entry->length;
}

using NativeFunction=uintptr_t(*)();

static uintptr_t RunSynthetic(unsigned char* code, uint32_t length,
                              Core::CpuIdInstruction kind,
                              uint32_t destination=0) {
    const uintptr_t pc=reinterpret_cast<uintptr_t>(code);
    assert(Core::NativeCpuId::TrapRegistry::Register(
        pc,kind,destination,length));
    code[0]=0x0f;
    code[1]=0x0b;
    for (unsigned i=2;i<length;++i) code[i]=0x90;
    code[length]=0xc3; // RET, cannot be overwritten by the trap.
    __builtin___clear_cache(reinterpret_cast<char*>(code),
                            reinterpret_cast<char*>(code)+length+1);
    expected_kind=static_cast<sig_atomic_t>(kind);
    auto f=reinterpret_cast<NativeFunction>(code);
    return f();
}

int main() {
    struct sigaction signal_action{};
    signal_action.sa_sigaction=OnIllegal;
    signal_action.sa_flags=SA_SIGINFO;
    sigemptyset(&signal_action.sa_mask);
    struct sigaction previous{};
    assert(sigaction(SIGILL,&signal_action,&previous)==0);

    const long pagesize=sysconf(_SC_PAGESIZE);
    assert(pagesize>=4096);
    auto* code=static_cast<unsigned char*>(mmap(
        nullptr,pagesize,PROT_READ|PROT_WRITE|PROT_EXEC,
        MAP_PRIVATE|MAP_ANONYMOUS,-1,0));
    assert(code!=MAP_FAILED);

    // 1: Two-byte CPUID, with only two bytes available before RET.
    assert(RunSynthetic(code,2,Core::CpuIdInstruction::Cpuid)==11);
    assert(traps_seen==1);

    // 2: Three-byte RDTSCP.
    assert(RunSynthetic(code+64,3,Core::CpuIdInstruction::Rdtscp)==12);
    assert(traps_seen==2);

    // 3: Four-byte RDPID r32; hardware support must not bypass SIGILL.
    assert(RunSynthetic(code+128,4,Core::CpuIdInstruction::Rdpid,3)==13);
    assert(traps_seen==3);

    // 4: Same patch address after module/code reload: newest registration wins.
    assert(RunSynthetic(code+128,2,Core::CpuIdInstruction::Cpuid)==11);
    assert(traps_seen==4);

    // 5: A regular address is not a registered CPU-ID trap.
    assert(Core::NativeCpuId::TrapRegistry::Lookup(
        reinterpret_cast<uintptr_t>(code+200))==nullptr);

    // 6-8: Reject malformed registrations before publication.
    assert(!Core::NativeCpuId::TrapRegistry::Register(
        0,Core::CpuIdInstruction::Cpuid,0,2));
    assert(!Core::NativeCpuId::TrapRegistry::Register(
        reinterpret_cast<uintptr_t>(code+256),Core::CpuIdInstruction::Cpuid,0,1));
    assert(!Core::NativeCpuId::TrapRegistry::Register(
        reinterpret_cast<uintptr_t>(code+320),Core::CpuIdInstruction::Rdpid,16,4));

    assert(munmap(code,pagesize)==0);
    assert(sigaction(SIGILL,&previous,nullptr)==0);
    std::puts("NATIVE_CPU_ID_SHORT_TRAPS=PASS cases=8");
    return 0;
}
