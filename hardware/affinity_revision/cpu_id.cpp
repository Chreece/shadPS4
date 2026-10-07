// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <stdint.h>
#include <stdio.h>
#include <string.h>

using Thread = void*;
using Attr = void*;

extern "C" {
Thread scePthreadSelf();
int scePthreadCreate(Thread*, const Attr*, void* (*)(void*), void*, const char*);
int scePthreadJoin(Thread, void**);
int scePthreadAttrInit(Attr*);
int scePthreadAttrDestroy(Attr*);
int scePthreadAttrSetaffinity(Attr*, uint64_t);
int scePthreadGetaffinity(Thread, uint64_t*);
int scePthreadSetaffinity(Thread, uint64_t);
int sceKernelGetCurrentCpu();
int sceKernelUsleep(unsigned);
int sceKernelMmap(void*, uint64_t, int, int, int, int64_t, void**);
int sceKernelMprotect(const void*, uint64_t, int);
int sceKernelMunmap(void*, uint64_t);
int sceSystemServiceLoadExec(const char*, const char* const*);
void (*signal(int, void (*)(int)))(int);
int pthread_kill(Thread, int);
}

struct Cpuid {
    uint32_t eax, ebx, ecx, edx;
};

static Cpuid ReadCpuid(uint32_t leaf, uint32_t subleaf = 0) {
    Cpuid value;
    asm volatile("cpuid"
                 : "=a"(value.eax), "=b"(value.ebx), "=c"(value.ecx), "=d"(value.edx)
                 : "a"(leaf), "c"(subleaf)
                 : "memory");
    return value;
}

struct Snapshot {
    uint64_t registers[16];
    uint64_t flags;
    uint64_t simd[64];
    uint32_t mxcsr;
    uint16_t fpcw;
    uint16_t padding;
    uint64_t stack_before;
    uint64_t red_zone[15];
};
static_assert(sizeof(Snapshot) == 784);

extern "C" {
void SnapshotCpuid(Snapshot*, void*);
void SnapshotFaultCpuid(Snapshot*, void*);
void SnapshotRdtscp(Snapshot*, void*);
void SnapshotRdpid(Snapshot*, void*);
}

static void* generated_cpuid;
static int failures;
static uint64_t samples;

static void Check(bool ok, const char* name) {
    if (!ok) {
        ++failures;
        if (failures < 30) {
            printf("CPU_ID_FAILURE %s\n", name);
        }
    }
}

static void CheckState(int cpu) {
    using Capture = void (*)(Snapshot*, void*);
    const Capture functions[]{SnapshotCpuid, SnapshotFaultCpuid, SnapshotRdtscp, SnapshotRdpid};
    for (unsigned instruction = 0; instruction < 4; ++instruction) {
        Snapshot state{};
        functions[instruction](&state, generated_cpuid);
        for (unsigned reg = 0; reg < 16; ++reg) {
            if (reg == 4) {
                Check(state.registers[reg] == state.stack_before, "RSP preservation");
            } else if ((instruction < 2 && reg < 4) || (instruction == 2 && reg < 3)) {
                Check((state.registers[reg] >> 32) == 0, "32-bit output zero extension");
            } else {
                const uint64_t expected = instruction == 3 && reg == 15 ? cpu
                                          : reg == 0                    ? 1
                                          : reg == 1                    ? 0
                                                     : 0x1234567800000000ULL + reg;
                Check(state.registers[reg] == expected, "GPR preservation");
            }
        }
        Check((state.flags & 0xcd5) == 0xcd5, "RFLAGS/DF preservation");
        Check(state.mxcsr == 0x3f80 && state.fpcw == 0x077f, "FP control preservation");
        for (uint64_t value : state.simd) {
            Check(value == 0x0123456789abcdefULL, "YMM preservation");
        }
        for (uint64_t value : state.red_zone) {
            Check(value == 0x11223344, "red-zone preservation");
        }
        if (instruction < 2) {
            Check((state.registers[3] >> 24) == static_cast<unsigned>(cpu), "CPUID APIC ID");
            Check(((state.registers[3] >> 16) & 0xff) == 8, "CPUID logical count");
        } else if (instruction == 2) {
            Check(state.registers[1] == static_cast<unsigned>(cpu), "RDTSCP AUX");
        }
        ++samples;
    }
}

static bool InMask(uint32_t cpu, uint64_t mask) {
    return cpu < 8 && (mask & (uint64_t{1} << cpu));
}

static unsigned SampleIdentity(uint64_t mask) {
    unsigned bad = 0;
    bad += !InMask(ReadCpuid(1).ebx >> 24, mask);
    bad += !InMask(ReadCpuid(0xb).edx, mask);
    bad += !InMask(ReadCpuid(0x1f, 1).edx, mask);
    bad += !InMask(ReadCpuid(0x8000001e).eax, mask);
    uint32_t low, high, auxiliary;
    asm volatile("rdtscp" : "=a"(low), "=d"(high), "=c"(auxiliary) : : "memory");
    bad += !InMask(auxiliary, mask);
    uint64_t pid;
    asm volatile("rdpid %0" : "=r"(pid));
    bad += !InMask(pid, mask);
    bad += !InMask(sceKernelGetCurrentCpu(), mask);
    return bad;
}

static int signals_received;
static int signal_failures;
static int sender_finished;
static Thread signal_target;

static void ObserveSignal(int) {
    unsigned bad = SampleIdentity(1);
    Snapshot state{};
    SnapshotFaultCpuid(&state, generated_cpuid);
    bad += (state.registers[3] >> 24) != 0;
    __atomic_add_fetch(&signal_failures, bad, __ATOMIC_RELAXED);
    __atomic_add_fetch(&signals_received, 1, __ATOMIC_RELEASE);
}

static void* SendSignals(void*) {
    for (int sent = 1; sent <= 32; ++sent) {
        if (pthread_kill(signal_target, 30) != 0) {
            __atomic_add_fetch(&signal_failures, 1, __ATOMIC_RELAXED);
            break;
        }
        int waits = 0;
        while (__atomic_load_n(&signals_received, __ATOMIC_ACQUIRE) < sent && waits++ < 1000) {
            sceKernelUsleep(1000);
        }
        if (waits >= 1000) {
            __atomic_add_fetch(&signal_failures, 1, __ATOMIC_RELAXED);
            break;
        }
    }
    __atomic_store_n(&sender_finished, 1, __ATOMIC_RELEASE);
    return nullptr;
}

static void CheckSignals() {
    signal_target = scePthreadSelf();
    Check(scePthreadSetaffinity(signal_target, 1) == 0, "pin signal target");
    const auto previous = signal(30, ObserveSignal);
    Check(previous != reinterpret_cast<void (*)(int)>(-1), "install signal handler");
    Thread sender = nullptr;
    if (scePthreadCreate(&sender, nullptr, SendSignals, nullptr, "cpu-id-signals") == 0) {
        while (!__atomic_load_n(&sender_finished, __ATOMIC_ACQUIRE)) {
            failures += SampleIdentity(1);
            ++samples;
        }
        Check(scePthreadJoin(sender, nullptr) == 0, "join signal sender");
    } else {
        ++failures;
    }
    Check(signals_received == 32 && signal_failures == 0, "CPU identity from guest signals");
    signal(30, previous);
    printf("CPU_ID_SIGNAL_RESULT failures=%d handled=%d\n", signal_failures, signals_received);
}

struct Worker {
    uint64_t mask;
    uint64_t samples;
    uint64_t failures;
};
static int started;
static int stopping;

static void* Observe(void* arg) {
    auto& worker = *static_cast<Worker*>(arg);
    __atomic_add_fetch(&started, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&stopping, __ATOMIC_ACQUIRE)) {
        for (int i = 0; i < 100; ++i) {
            worker.failures += SampleIdentity(worker.mask);
            ++worker.samples;
        }
        sceKernelUsleep(1000);
    }
    return nullptr;
}

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    const Thread self = scePthreadSelf();
    uint64_t original = 0;
    Check(scePthreadGetaffinity(self, &original) == 0 && original == 0x7f, "initial mask");
    const uint8_t code[]{0x0f, 0xa2, 0xc3};
    Check(sceKernelMmap(nullptr, 16384, 3, 0x1002, -1, 0, &generated_cpuid) == 0,
          "allocate generated code");
    if (generated_cpuid == nullptr) {
        sceSystemServiceLoadExec("EXIT", nullptr);
        return 1;
    }
    memcpy(generated_cpuid, code, sizeof(code));
    Check(sceKernelMprotect(generated_cpuid, 16384, 5) == 0, "protect generated code");
    Check(ReadCpuid(0).eax >= 0x1f, "basic maximum");
    Check(ReadCpuid(0x80000000).eax >= 0x8000001e, "extended maximum");
    const uint32_t cache_leaves[]{4, 0x8000001d};
    for (uint32_t leaf : cache_leaves) {
        for (unsigned subleaf = 0; subleaf < 32; ++subleaf) {
            const auto cache = ReadCpuid(leaf, subleaf);
            if ((cache.eax & 0x1f) == 0)
                break;
            const unsigned sharing = ((cache.eax >> 5) & 7) >= 3 ? 8 : 1;
            Check(((cache.eax >> 14) & 0xfff) + 1 == sharing &&
                      (cache.eax >> 26) == (leaf == 4 ? 7u : 0u),
                  "cache topology");
        }
    }
    Check(((ReadCpuid(0x80000008).ecx >> 12) & 0xf) == 3 && (ReadCpuid(0x80000008).ecx & 0xff) == 7,
          "AMD core count");
    Check((ReadCpuid(7).ecx & (1u << 22)) == 0, "RDPID not advertised");
    for (unsigned cpu = 0; cpu < 7; ++cpu) {
        const uint64_t mask = uint64_t{1} << cpu;
        Check(scePthreadSetaffinity(self, mask) == 0, "set single affinity");
        Check(sceKernelGetCurrentCpu() == static_cast<int>(cpu), "kernel CPU ID");
        CheckState(cpu);
        const auto amd = ReadCpuid(0x8000001e);
        Check(amd.eax == cpu && amd.ebx == cpu && amd.ecx == 0 && amd.edx == 0,
              "AMD APIC/core/node");
        const uint32_t topology_leaves[]{0xb, 0x1f};
        for (uint32_t leaf : topology_leaves) {
            for (unsigned subleaf = 0; subleaf < 4; ++subleaf) {
                const auto value = ReadCpuid(leaf, subleaf);
                Check(value.eax == (subleaf == 1 ? 3u : 0u) &&
                          value.ebx == (subleaf == 0   ? 1u
                                        : subleaf == 1 ? 8u
                                                       : 0u) &&
                          value.ecx == (subleaf | (subleaf < 2 ? (subleaf + 1) << 8 : 0)) &&
                          value.edx == cpu,
                      "topology subleaf");
            }
        }
        for (int i = 0; i < 100; ++i) {
            failures += SampleIdentity(mask);
            ++samples;
        }
    }
    Check(scePthreadSetaffinity(self, 0x50) == 0, "set multiple affinity");
    for (int i = 0; i < 1000; ++i) {
        failures += SampleIdentity(0x50);
        ++samples;
    }
    Check(scePthreadSetaffinity(self, original) == 0, "restore affinity");
    printf("CPU_ID_STATE_RESULT failures=%d samples=%llu\n", failures,
           static_cast<unsigned long long>(samples));
    CheckSignals();
    Check(scePthreadSetaffinity(self, original) == 0, "restore affinity after signals");
    Worker workers[7]{};
    Thread threads[7]{};
    int count = 0;
    for (unsigned cpu = 0; cpu < 7; ++cpu) {
        workers[cpu].mask = uint64_t{1} << cpu;
        Attr attr = nullptr;
        int result = scePthreadAttrInit(&attr);
        if (result == 0)
            result = scePthreadAttrSetaffinity(&attr, workers[cpu].mask);
        if (result == 0)
            result =
                scePthreadCreate(&threads[cpu], &attr, Observe, &workers[cpu], "affinity-live");
        if (attr != nullptr)
            scePthreadAttrDestroy(&attr);
        Check(result == 0, "create CPU-ID worker");
        if (result == 0)
            ++count;
    }
    while (__atomic_load_n(&started, __ATOMIC_ACQUIRE) != count)
        sceKernelUsleep(1000);
    printf("AFFINITY_DYNAMIC_READY workers=%d mask=%llx\n", count,
           static_cast<unsigned long long>(original));
    sceKernelUsleep(8000000);
    __atomic_store_n(&stopping, 1, __ATOMIC_RELEASE);
    for (unsigned cpu = 0; cpu < 7; ++cpu) {
        if (threads[cpu] != nullptr) {
            Check(scePthreadJoin(threads[cpu], nullptr) == 0, "join CPU-ID worker");
            failures += workers[cpu].failures;
            samples += workers[cpu].samples;
        }
    }
    Check(sceKernelMunmap(generated_cpuid, 16384) == 0, "free generated code");
    printf("AFFINITY_DYNAMIC_RESULT failures=%d samples=%llu workers=%d\n", failures,
           static_cast<unsigned long long>(samples), count);
    sceSystemServiceLoadExec("EXIT", nullptr);
    return failures != 0;
}
