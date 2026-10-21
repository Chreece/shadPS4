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
static void descriptor(uint32_t *dst, void *ptr, uint32_t stride, uint32_t records) {
    uint64_t addr = (uintptr_t)ptr;
    dst[0] = addr;
    dst[1] = (addr >> 32 & 255) | stride << 16;
    dst[2] = records;
    dst[3] = 0x18027fac;
}
static int append_dispatch(uint32_t *cmd, uint32_t *n, uint8_t *arena,
                           void *dst, uint32_t records, uint32_t mode, uint32_t seed) {
    uint32_t ud[10];
    descriptor(ud, dst, 4, records);
    descriptor(ud + 4, arena + 0x50000, 0, 64 * 8);
    ud[8] = mode; ud[9] = seed;
    cmd[(*n)++] = 0xc00a7602; cmd[(*n)++] = 0x240;
    memcpy(cmd + *n, ud, sizeof(ud)); *n += 10;
    if (sceGnmDispatchDirect(cmd + *n, 9, 64, 1, 1, 1) < 0) return -1;
    *n += 9;
    cmd[(*n)++] = 0xc0004602; cmd[(*n)++] = 7 | (4 << 8);
    return 0;
}
static int run(uint8_t *arena, uint32_t seed, int gpu_writes) {
    uint32_t *cmd = (uint32_t *)(arena + 0x10000);
    uint32_t *out = (uint32_t *)(arena + 0x60000);
    volatile uint32_t *done = (uint32_t *)(arena + 0x41000);
    memset(out, 0xa5, 8192); *done = 0;
    if (sceGnmDrawInitDefaultHardwareState350(cmd, 256) < 0) return -1;
    uint32_t n = 256;
    uint32_t cs[7] = {(uintptr_t)arena >> 8, 0, 1 | (2 << 6) | (1 << 21) | (1 << 23), (10 << 1) | (1 << 7), 1, 1, 1};
    if (sceGnmSetCsShader(cmd + n, 25, cs) < 0) return -2;
    n += 25;
    if (gpu_writes) {
        if (append_dispatch(cmd, &n, arena, arena + 0x80000, 64 * 0x1000, 0, seed) < 0 ||
            append_dispatch(cmd, &n, arena, out, 64, 1, 0) < 0 ||
            append_dispatch(cmd, &n, arena, arena + 0x80000, 64 * 0x1000, 0, seed + 256) < 0 ||
            append_dispatch(cmd, &n, arena, arena + 0x61000, 64, 1, 0) < 0) return -3;
    } else if (append_dispatch(cmd, &n, arena, out, 64, 1, 0) < 0) {
        return -3;
    }
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
        if (*done == 1) {
            flush(out, 8192);
            if (gpu_writes) flush(arena + 0x80000, 0x100000);
            return 0;
        }
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
    for (unsigned round = 0; round < 8; ++round) {
        const int gpu_writes = round >= 4;
        const uint32_t seed = (gpu_writes ? 0x23450000 : 0x12340000) + round * 512;
        const char *kind = gpu_writes ? "gpu" : "cpu";
        uint64_t *table = (uint64_t *)(arena + 0x50000);
        for (unsigned page = 0; page < 64; ++page) {
            uint32_t *marker = (uint32_t *)(arena + 0x80000 + page * 0x4000);
            marker[0] = gpu_writes ? 0xa5a5a5a5 : seed + page;
            marker[1] = 0xa5a5a5a5;
        }
        for (unsigned group = 0; group < 64; ++group)
            table[group] = (uintptr_t)(arena + 0x80000 + ((group * 17 + round) & 63) * 0x4000);
        int rc = run(arena, seed, gpu_writes);
        if (rc < 0) {
            fprintf(log, "SUBMISSION_FAILED rc=%d\n", rc); fflush(log);
            for (;;) sceKernelUsleep(1000000);
        }
        for (unsigned pass = 0; pass < (gpu_writes ? 2 : 1); ++pass) {
            const uint32_t *out = (const uint32_t *)(arena + 0x60000 + pass * 0x1000);
            for (unsigned group = 0; group < 64; ++group) {
                uint32_t expected = seed + pass * 256 + ((group * 17 + round) & 63);
                if (out[group] != expected) {
                    ++failures;
                    fprintf(log, "kind=%s round=%u pass=%u group=%u got=%08x expected=%08x\n",
                            kind, round, pass, group, out[group], expected);
                }
            }
            for (unsigned j = 64; j < 1024; ++j) if (out[j] != 0xa5a5a5a5) ++failures;
        }
        for (unsigned page = 0; page < 64; ++page) {
            const uint32_t *marker = (const uint32_t *)(arena + 0x80000 + page * 0x4000);
            if (marker[0] != seed + (gpu_writes ? 256 : 0) + page ||
                marker[1] != 0xa5a5a5a5) ++failures;
        }
        fprintf(log, "kind=%s round=%u failures=%u\n", kind, round, failures); fflush(log);
    }
    fprintf(log, "RESULT=%s\n", failures ? "FAIL" : "PASS"); fclose(log);
    sceKernelMunmap(arena, 0x200000); sceKernelReleaseDirectMemory(physical, 0x200000);
    return failures != 0;
}
