// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern "C" {
void* scePthreadSelf();
int scePthreadGetaffinity(void*, uint64_t*);
int scePthreadSetaffinity(void*, uint64_t);
int sceKernelGetCurrentCpu();
int sceKernelHasNeoMode();
int sceKernelMmap(void*, uint64_t, int, int, int, int64_t, void**);
int sceKernelMprotect(const void*, uint64_t, int);
int sceKernelMunmap(void*, uint64_t);
int sceSystemServiceLoadExec(const char*, const char* const*);
}

struct Cpuid {
    uint32_t eax, ebx, ecx, edx;
};
using Query = void (*)(uint32_t, uint32_t, Cpuid*);
using Stamp = uint64_t (*)(uint32_t*);

static void ReadCpuid(uint32_t leaf, uint32_t subleaf, Cpuid* result) {
    asm volatile("cpuid" : "=a"(result->eax), "=b"(result->ebx), "=c"(result->ecx),
                 "=d"(result->edx) : "a"(leaf), "c"(subleaf) : "memory");
}

static uint64_t ReadRdtscp(uint32_t* auxiliary) {
    uint32_t low, high;
    asm volatile("rdtscp" : "=a"(low), "=d"(high), "=c"(*auxiliary) : : "memory");
    return (uint64_t{high} << 32) | low;
}

int main() {
    FILE* output = fopen("/data/cpu-identity-hardware.txt", "w");
    if (!output) return 1;
    setvbuf(output, nullptr, _IONBF, 0);
    fprintf(output, "CPU_IDENTITY_BEGIN version=1 neo=%d\n", sceKernelHasNeoMode());
    auto self = scePthreadSelf();
    uint64_t original = 0;
    unsigned errors = 0, rows = 0;
    void* memory = nullptr;
    int ret = scePthreadGetaffinity(self, &original);
    fprintf(output, "CPU_IDENTITY_MASK ret=%08x mask=%llx\n", ret,
            static_cast<unsigned long long>(original));
    if (ret != 0 || original == 0 || (original & ~uint64_t{0xff}) != 0) {
        errors++;
    } else if (sceKernelMmap(nullptr, 16384, 3, 0x1002, -1, 0, &memory) != 0) {
        errors++;
    } else {
        static constexpr unsigned char cpuid_code[]{
            0x53, 0x49, 0x89, 0xd0, 0x89, 0xf8, 0x89, 0xf1, 0x0f, 0xa2, 0x41, 0x89,
            0x00, 0x41, 0x89, 0x58, 0x04, 0x41, 0x89, 0x48, 0x08, 0x41, 0x89, 0x50,
            0x0c, 0x5b, 0xc3,
        };
        static constexpr unsigned char rdtscp_code[]{
            0x0f, 0x01, 0xf9, 0x89, 0x0f, 0x48, 0xc1, 0xe2, 0x20, 0x48, 0x09, 0xd0, 0xc3,
        };
        memcpy(memory, cpuid_code, sizeof(cpuid_code));
        auto* stamp_code = static_cast<unsigned char*>(memory) + 256;
        memcpy(stamp_code, rdtscp_code, sizeof(rdtscp_code));
        if (sceKernelMprotect(memory, 16384, 5) != 0) {
            errors++;
        } else {
            const Query queries[]{ReadCpuid, reinterpret_cast<Query>(memory)};
            const Stamp stamps[]{ReadRdtscp, reinterpret_cast<Stamp>(stamp_code)};
            for (unsigned bit = 0; bit < 8; ++bit) {
                if ((original & (uint64_t{1} << bit)) == 0) continue;
                ret = scePthreadSetaffinity(self, uint64_t{1} << bit);
                fprintf(output, "CPU_IDENTITY_PIN bit=%u ret=%08x\n", bit, ret);
                if (ret != 0) { errors++; continue; }
                uint64_t effective = 0;
                if (scePthreadGetaffinity(self, &effective) != 0) errors++;
                for (unsigned sample = 0; sample < 32; ++sample) {
                    for (unsigned mode = 0; mode < 2; ++mode) {
                        const int before = sceKernelGetCurrentCpu();
                        Cpuid basic{}, extended{};
                        queries[mode](1, 0, &basic);
                        queries[mode](0x8000001e, 0, &extended);
                        uint32_t auxiliary;
                        const uint64_t timestamp = stamps[mode](&auxiliary);
                        const int after = sceKernelGetCurrentCpu();
                        fprintf(output,
                                "CPU_IDENTITY_SAMPLE bit=%u mask=%llx mode=%s sample=%u "
                                "kernel_before=%d kernel_after=%d apic=%u ext_apic=%u "
                                "ext_core=%u ext_node=%u rdtscp_aux=%08x timestamp=%llx\n",
                                bit, static_cast<unsigned long long>(effective),
                                mode ? "generated" : "static", sample, before, after,
                                basic.ebx >> 24, extended.eax, extended.ebx, extended.ecx,
                                auxiliary, static_cast<unsigned long long>(timestamp));
                        rows++;
                    }
                }
            }
        }
        ret = scePthreadSetaffinity(self, original);
        fprintf(output, "CPU_IDENTITY_RESTORE ret=%08x\n", ret);
        errors += ret != 0;
        errors += sceKernelMunmap(memory, 16384) != 0;
    }
    fprintf(output, "CPU_IDENTITY_END errors=%u rows=%u\n", errors, rows);
    errors += ferror(output) != 0;
    errors += fclose(output) != 0;
    sceSystemServiceLoadExec("EXIT", nullptr);
    return errors != 0;
}
