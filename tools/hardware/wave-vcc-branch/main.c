// SPDX-License-Identifier: GPL-2.0-or-later
#define _DEFAULT_SOURCE
#include "shader.h"
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

extern int sceKernelAllocateDirectMemory(int64_t, int64_t, size_t, size_t, int, int64_t *);
extern int sceKernelMapDirectMemory(void **, size_t, int, int, int64_t, size_t);
extern int64_t sceKernelGetDirectMemorySize(void);
extern uint32_t sceGnmDrawInitDefaultHardwareState350(uint32_t *, uint32_t);
extern int sceGnmSetCsShader(uint32_t *, uint32_t, const uint32_t *);
extern int sceGnmDispatchDirect(uint32_t *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t);
extern int sceGnmSubmitCommandBuffers(uint32_t, void **, uint32_t *, void **, uint32_t *);
extern int sceGnmSubmitDone(void);

static uint64_t ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

int main(void) {
    int64_t physical = 0;
    void *allocation = 0;
    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), 0x10000, 0x4000, 3,
                                      &physical) < 0 ||
        sceKernelMapDirectMemory(&allocation, 0x10000, 0x33, 0, physical, 0x4000) < 0)
        return 1;
    if (((uintptr_t)allocation + 0x10000) >> 40)
        return 2;
    uint8_t *memory = allocation;
    uint32_t *command = (uint32_t *)memory;
    uint32_t *shader = (uint32_t *)(memory + 0x4000);
    uint32_t *output = (uint32_t *)(memory + 0x8000);
    volatile uint32_t *fence = (volatile uint32_t *)(memory + 0xc000);
    memcpy(shader, wave_vcc_cs, sizeof(wave_vcc_cs));
    const struct { uint64_t vcc, bits, exec; } cases[] = {
        {0, 0, UINT64_MAX},
        {1, 1, 1},
        {UINT64_C(1) << 63, UINT64_C(1) << 63, UINT64_C(1) << 63},
        {0xaaaaaaaaaaaaaaaaULL, 0x8000000100000001ULL, 0xaaaaaaaaaaaaaaaaULL},
        {0x5555555555555555ULL, 0x0000000080000000ULL, 0x5555555555555555ULL},
        {UINT64_MAX, UINT64_MAX, 0x00000000ffffffffULL},
        {0, UINT64_MAX, 0xffffffff00000000ULL},
        {1, 0, 0x8000000000000001ULL},
    };
    FILE *log = fopen("/data/wave-vcc-branch-probe.log", "w");
    if (!log)
        return 3;
    unsigned failures = 0;
    for (unsigned test = 0; test < sizeof(cases) / sizeof(cases[0]); ++test) {
        memset(output, 0xa5, 64 * 16);
        *fence = 0;
        unsigned used = sceGnmDrawInitDefaultHardwareState350(command, 256);
        if (!used || used > 256)
            return 4;
        const uint32_t cs_regs[] = {(uint32_t)((uintptr_t)shader >> 8), 0,
                                   0x000c0081, 10u << 1, 64, 1, 1};
        if (sceGnmSetCsShader(command + used, 25, cs_regs) < 0)
            return 5;
        used += 25;
        const uint64_t address = (uintptr_t)output;
        const uint32_t user[] = {
            (uint32_t)address, ((uint32_t)(address >> 32) & 255u) | (16u << 16),
            64, 0x00027fac,
            (uint32_t)cases[test].vcc, (uint32_t)(cases[test].vcc >> 32),
            (uint32_t)cases[test].bits, (uint32_t)(cases[test].bits >> 32),
            (uint32_t)cases[test].exec, (uint32_t)(cases[test].exec >> 32),
        };
        command[used++] = 0xc00a7602;
        command[used++] = 0x240;
        memcpy(command + used, user, sizeof(user));
        used += 10;
        if (sceGnmDispatchDirect(command + used, 9, 1, 1, 1, 0) < 0)
            return 6;
        used += 9;
        const uint64_t fence_address = (uintptr_t)fence;
        command[used++] = 0xc0044700;
        command[used++] = 0x514;
        command[used++] = (uint32_t)fence_address;
        command[used++] = (uint32_t)(fence_address >> 32) | (1u << 29);
        command[used++] = 1;
        command[used++] = 0;
        __sync_synchronize();
        void *buffers[] = {command};
        uint32_t lengths[] = {used * 4};
        if (sceGnmSubmitCommandBuffers(1, buffers, lengths, 0, 0) < 0 || sceGnmSubmitDone() < 0)
            return 7;
        const uint64_t start = ms();
        while (*fence != 1 && ms() - start < 2000)
            usleep(1000);
        if (*fence != 1) {
            fputs("STOP fence timeout; no more submissions\n", log);
            fclose(log);
            return 8;
        }
        __sync_synchronize();
        const uint32_t expected[] = {cases[test].vcc == 0, cases[test].vcc != 0,
                                    __builtin_popcountll(cases[test].bits), 0};
        unsigned mismatches = 0;
        for (unsigned lane = 0; lane < 64; ++lane)
            for (unsigned component = 0; component < 4; ++component)
                mismatches += output[lane * 4 + component] != expected[component];
        failures += mismatches != 0;
        fprintf(log, "case=%u vcc=%016llx bits=%016llx exec=%016llx mismatches=%u\n", test,
                (unsigned long long)cases[test].vcc, (unsigned long long)cases[test].bits,
                (unsigned long long)cases[test].exec, mismatches);
        fflush(log);
    }
    fprintf(log, "observations_complete failures=%u\n", failures);
    fclose(log);
    return failures ? 9 : 0;
}
