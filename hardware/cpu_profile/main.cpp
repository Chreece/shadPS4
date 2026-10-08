// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifdef CPU_PROFILE_ORBIS
extern "C" {
void* scePthreadSelf();
int scePthreadGetaffinity(void*, uint64_t*);
int scePthreadSetaffinity(void*, uint64_t);
int sceKernelGetCurrentCpu();
int sceKernelMmap(void*, uint64_t, int, int, int, int64_t, void**);
int sceKernelMprotect(const void*, uint64_t, int);
int sceKernelMunmap(void*, uint64_t);
int sceSystemServiceLoadExec(const char*, const char* const*);
}
#else
#include <sched.h>
#include <sys/mman.h>
#endif

struct Cpuid {
    uint32_t eax, ebx, ecx, edx;
};

using Query = void (*)(uint32_t, uint32_t, Cpuid*);
static unsigned rows;
static unsigned failures;
static FILE* saved_output;

static void ReadCpuid(uint32_t leaf, uint32_t subleaf, Cpuid* result) {
    asm volatile("cpuid"
                 : "=a"(result->eax), "=b"(result->ebx), "=c"(result->ecx), "=d"(result->edx)
                 : "a"(leaf), "c"(subleaf)
                 : "memory");
}

static Cpuid Emit(Query query, const char* mode, int cpu, uint32_t leaf, uint32_t subleaf) {
    Cpuid value;
    query(leaf, subleaf, &value);
    char line[256];
    snprintf(line, sizeof(line),
             "CPU_PROFILE_CPUID mode=%s cpu=%d leaf=%08x subleaf=%08x eax=%08x ebx=%08x ecx=%08x "
             "edx=%08x\n",
             mode, cpu, leaf, subleaf, value.eax, value.ebx, value.ecx, value.edx);
    fputs(line, stdout);
    if (saved_output) {
        fputs(line, saved_output);
    }
    ++rows;
    return value;
}

static void Leaf(Query query, const char* mode, int cpu, uint32_t leaf) {
    const auto first = Emit(query, mode, cpu, leaf, 0);
    if (leaf == 4 || leaf == 0x8000001d) {
        auto value = first;
        for (uint32_t subleaf = 1; (value.eax & 31) != 0 && subleaf < 32; ++subleaf) {
            value = Emit(query, mode, cpu, leaf, subleaf);
        }
    } else if (leaf == 7 || leaf == 0x14 || leaf == 0x17 || leaf == 0x18 || leaf == 0x1d) {
        for (uint32_t subleaf = 1; subleaf <= first.eax && subleaf < 32; ++subleaf) {
            Emit(query, mode, cpu, leaf, subleaf);
        }
    } else if (leaf == 0xb || leaf == 0x1f || leaf == 0x80000026) {
        auto value = first;
        for (uint32_t subleaf = 1; (value.ebx & 0xffff) != 0 && subleaf < 32; ++subleaf) {
            value = Emit(query, mode, cpu, leaf, subleaf);
        }
    } else if (leaf == 0xd) {
        const auto second = Emit(query, mode, cpu, leaf, 1);
        const uint64_t states = (uint64_t{first.edx | second.edx} << 32) | first.eax | second.ecx;
        for (uint32_t subleaf = 2; subleaf < 64; ++subleaf) {
            if ((states & (uint64_t{1} << subleaf)) != 0) {
                Emit(query, mode, cpu, leaf, subleaf);
            }
        }
    } else if (leaf == 0xf || leaf == 0x10) {
        const uint32_t resources = leaf == 0xf ? first.edx : first.ebx;
        for (uint32_t subleaf = 1; subleaf < 32; ++subleaf) {
            if ((resources & (1u << subleaf)) != 0) {
                Emit(query, mode, cpu, leaf, subleaf);
            }
        }
    }
}

static void Dump(Query query, const char* mode, int cpu) {
    const auto basic = Emit(query, mode, cpu, 0, 0);
    const auto extended = Emit(query, mode, cpu, 0x80000000, 0);
    if (basic.eax > 0x100 || extended.eax < 0x80000000 || extended.eax > 0x80000100) {
        printf("CPU_PROFILE_ERROR invalid_leaf_range mode=%s cpu=%d\n", mode, cpu);
        ++failures;
        return;
    }
    for (uint32_t leaf = 1; leaf <= basic.eax; ++leaf) {
        Leaf(query, mode, cpu, leaf);
    }
    for (uint32_t leaf = 0x80000001; leaf <= extended.eax; ++leaf) {
        Leaf(query, mode, cpu, leaf);
    }
    Cpuid features;
    query(1, 0, &features);
    if ((features.ecx & (3u << 26)) == (3u << 26)) {
        uint32_t low, high;
        asm volatile("xgetbv" : "=a"(low), "=d"(high) : "c"(0));
        char line[160];
        snprintf(line, sizeof(line), "CPU_PROFILE_XCR0 mode=%s cpu=%d value=%08x%08x\n", mode, cpu,
                 high, low);
        fputs(line, stdout);
        if (saved_output) {
            fputs(line, saved_output);
        }
    }
}

static Query GeneratedQuery(void** memory) {
    static constexpr uint8_t code[]{
        0x53, 0x49, 0x89, 0xd0, 0x89, 0xf8, 0x89, 0xf1, 0x0f, 0xa2, 0x41, 0x89, 0x00, 0x41,
        0x89, 0x58, 0x04, 0x41, 0x89, 0x48, 0x08, 0x41, 0x89, 0x50, 0x0c, 0x5b, 0xc3,
    };
#ifdef CPU_PROFILE_ORBIS
    if (sceKernelMmap(nullptr, 16384, 3, 0x1002, -1, 0, memory) != 0) {
        return nullptr;
    }
#else
    *memory = mmap(nullptr, 16384, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (*memory == MAP_FAILED) {
        *memory = nullptr;
        return nullptr;
    }
#endif
    memcpy(*memory, code, sizeof(code));
#ifdef CPU_PROFILE_ORBIS
    const int result = sceKernelMprotect(*memory, 16384, 5);
#else
    const int result = mprotect(*memory, 16384, PROT_READ | PROT_EXEC);
#endif
    return result == 0 ? reinterpret_cast<Query>(*memory) : nullptr;
}

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
#ifdef CPU_PROFILE_ORBIS
    const char* platform = "orbis";
    saved_output = fopen("/data/cpu-profile.txt", "a");
#else
    const char* platform = "linux";
#endif
    printf("CPU_PROFILE_BEGIN version=1 platform=%s\n", platform);
    if (saved_output) {
        fprintf(saved_output, "CPU_PROFILE_BEGIN version=1 platform=%s\n", platform);
    }
    void* memory = nullptr;
    const auto generated = GeneratedQuery(&memory);
    if (!generated) {
        printf("CPU_PROFILE_ERROR generated_allocation\n");
        ++failures;
    }
#ifdef CPU_PROFILE_ORBIS
    uint64_t original = 0;
    const auto self = scePthreadSelf();
    if (scePthreadGetaffinity(self, &original) != 0 || original == 0 || (original & ~0xffULL)) {
        printf("CPU_PROFILE_ERROR read_affinity\n");
        ++failures;
    } else {
        for (int cpu = 0; cpu < 8; ++cpu) {
            if ((original & (uint64_t{1} << cpu)) == 0) {
                continue;
            }
            if (scePthreadSetaffinity(self, uint64_t{1} << cpu) != 0) {
                printf("CPU_PROFILE_ERROR pin cpu=%d\n", cpu);
                ++failures;
                continue;
            }
            Dump(ReadCpuid, "static", cpu);
            if (generated) {
                Dump(generated, "generated", cpu);
            }
        }
        if (scePthreadSetaffinity(self, original) != 0) {
            ++failures;
        }
    }
    if (memory) {
        sceKernelMunmap(memory, 16384);
    }
#else
    cpu_set_t original;
    if (sched_getaffinity(0, sizeof(original), &original) != 0) {
        printf("CPU_PROFILE_ERROR read_affinity\n");
        ++failures;
    } else {
        for (int cpu = 0; cpu < CPU_SETSIZE; ++cpu) {
            if (!CPU_ISSET(cpu, &original)) {
                continue;
            }
            cpu_set_t selected;
            CPU_ZERO(&selected);
            CPU_SET(cpu, &selected);
            if (sched_setaffinity(0, sizeof(selected), &selected) != 0) {
                printf("CPU_PROFILE_ERROR pin cpu=%d\n", cpu);
                ++failures;
                continue;
            }
            Dump(ReadCpuid, "static", cpu);
            if (generated) {
                Dump(generated, "generated", cpu);
            }
        }
        if (sched_setaffinity(0, sizeof(original), &original) != 0) {
            ++failures;
        }
    }
    if (memory) {
        munmap(memory, 16384);
    }
#endif
    printf("CPU_PROFILE_END failures=%u rows=%u\n", failures, rows);
    if (saved_output) {
        fprintf(saved_output, "CPU_PROFILE_END failures=%u rows=%u\n", failures, rows);
        fclose(saved_output);
    }
#ifdef CPU_PROFILE_ORBIS
    sceSystemServiceLoadExec("EXIT", nullptr);
#endif
    return failures == 0 ? 0 : 1;
}
