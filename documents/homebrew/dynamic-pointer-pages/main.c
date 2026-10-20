#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include <emmintrin.h>
#include <orbis/libkernel.h>
#include "shader.h"

extern int32_t sceGnmDrawInitDefaultHardwareState350(uint32_t *, uint32_t);
extern int32_t sceGnmSetCsShader(uint32_t *, uint32_t, const uint32_t *);
extern int32_t sceGnmDispatchDirect(uint32_t *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t);
extern int32_t sceGnmSubmitCommandBuffers(uint32_t, void **, uint32_t *, void **, uint32_t *);
extern int32_t sceGnmSubmitDone(void);

static void flush(void *ptr, size_t bytes) {
    uintptr_t first = (uintptr_t)ptr & ~(uintptr_t)63;
    for (uintptr_t p = first; p < (uintptr_t)ptr + bytes; p += 64) _mm_clflush((void *)p);
    _mm_mfence();
}
static int run(uint8_t *arena) {
    uint32_t *cmd = (uint32_t *)(arena + 0x10000);
    uint32_t *out = (uint32_t *)(arena + 0x60000);
    volatile uint32_t *done = (uint32_t *)(arena + 0x41000);
    memset(out, 0xa5, 4096); *done = 0;
    if (sceGnmDrawInitDefaultHardwareState350(cmd, 256) < 0) return -1;
    uint32_t n = 256;
    uint32_t cs[7] = {(uintptr_t)arena >> 8, 0, 1 | (1 << 6) | (1 << 21) | (1 << 23), (6 << 1) | (1 << 7), 1, 1, 1};
    if (sceGnmSetCsShader(cmd + n, 25, cs) < 0) return -2;
    n += 25;
    uint32_t ud[6] = {(uintptr_t)out, ((uintptr_t)out >> 32 & 255) | (4 << 16),
                      64, 0x18027fac, (uintptr_t)(arena + 0x50000),
                      (uintptr_t)(arena + 0x50000) >> 32};
    cmd[n++] = 0xc0067602; cmd[n++] = 0x240;
    memcpy(cmd + n, ud, sizeof(ud)); n += 6;
    if (sceGnmDispatchDirect(cmd + n, 9, 64, 1, 1, 1) < 0) return -3;
    n += 9;
    cmd[n++] = 0xc0004602; cmd[n++] = 7 | (4 << 8);
    cmd[n++] = 0xc0044700; cmd[n++] = 20 | (5 << 8);
    cmd[n++] = (uintptr_t)done; cmd[n++] = ((uintptr_t)done >> 32) | (1 << 29);
    cmd[n++] = 1; cmd[n++] = 0;
    flush(arena, 0x200000);
    void *buffers[] = {cmd}; uint32_t sizes[] = {n * 4};
    int rc = sceGnmSubmitCommandBuffers(1, buffers, sizes, 0, 0);
    if (rc < 0) return rc;
    sceGnmSubmitDone();
    for (unsigned i = 0; i < 5000; ++i) {
        flush((void *)done, 4);
        if (*done == 1) {flush(out, 4096); return 0;}
        sceKernelUsleep(1000);
    }
    return -4;
}
int main(void) {
    off_t physical = 0; uint8_t *arena = 0;
    FILE *log = fopen("/data/dynamic-pointer-pages-probe.txt", "w"); if (!log) log = stdout;
    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), 0x200000, 0x4000,
                                     ORBIS_KERNEL_WB_ONION, &physical) < 0 ||
        sceKernelMapDirectMemory((void **)&arena, 0x200000,
            ORBIS_KERNEL_PROT_CPU_RW | ORBIS_KERNEL_PROT_GPU_RW, 0, physical, 0x4000) < 0) {
        fprintf(log, "ALLOCATION_FAILED\n"); fclose(log); return 1;
    }
    memcpy(arena, probe_shader, sizeof(probe_shader));
    unsigned failures = 0;
    for (unsigned round = 0; round < 4; ++round) {
        uint64_t *table = (uint64_t *)(arena + 0x50000);
        for (unsigned page = 0; page < 64; ++page)
            *(uint32_t *)(arena + 0x80000 + page * 0x4000) = 0x12340000 + round * 256 + page;
        for (unsigned group = 0; group < 64; ++group)
            table[group] = (uintptr_t)(arena + 0x80000 + ((group * 17 + round) & 63) * 0x4000);
        int rc = run(arena);
        if (rc < 0) {
            fprintf(log, "SUBMISSION_FAILED rc=%d\n", rc); fflush(log);
            for (;;) sceKernelUsleep(1000000);
        }
        const uint32_t *out = (const uint32_t *)(arena + 0x60000);
        for (unsigned group = 0; group < 64; ++group) {
            uint32_t expected = 0x12340000 + round * 256 + ((group * 17 + round) & 63);
            if (out[group] != expected) {
                ++failures;
                fprintf(log, "round=%u group=%u got=%08x expected=%08x\n",
                        round, group, out[group], expected);
            }
        }
        for (unsigned j = 64; j < 1024; ++j) if (out[j] != 0xa5a5a5a5) ++failures;
        fprintf(log, "round=%u failures=%u\n", round, failures); fflush(log);
    }
    fprintf(log, "RESULT=%s\n", failures ? "FAIL" : "PASS"); fclose(log);
    sceKernelMunmap(arena, 0x200000); sceKernelReleaseDirectMemory(physical, 0x200000);
    return failures != 0;
}
