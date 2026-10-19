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
static int append_dispatch(uint32_t *cmd, uint32_t *n, uint8_t *arena, void *out) {
    uint32_t ud[4];
    descriptor(ud, out, 4, 256);
    uint32_t cs[7] = {(uintptr_t)arena >> 8, 0, 2 | (1 << 6) | (1 << 21) | (1 << 23), 4 << 1, 1, 1, 1};
    if (sceGnmSetCsShader(cmd + *n, 25, cs) < 0) return -1;
    *n += 25;
    cmd[(*n)++] = 0xc0047602; cmd[(*n)++] = 0x240;
    memcpy(cmd + *n, ud, sizeof(ud)); *n += 4;
    if (sceGnmDispatchDirect(cmd + *n, 9, 1, 1, 1, 1) < 0) return -2;
    *n += 9;
    cmd[(*n)++] = 0xc0004602; cmd[(*n)++] = 7 | (4 << 8);
    return 0;
}
static int run(uint8_t *arena) {
    uint32_t *cmd = (uint32_t *)(arena + 0x10000);
    uint8_t *out = arena + 0x60000;
    volatile uint32_t *done = (uint32_t *)(arena + 0x41000);
    memset(out, 0, 1024); *done = 0;
    if (sceGnmDrawInitDefaultHardwareState350(cmd, 256) < 0) return -1;
    uint32_t n = 256;
    if (append_dispatch(cmd, &n, arena, out) < 0) return -2;
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
        if (*done == 1) {flush(out, 1024); return 0;}
        sceKernelUsleep(1000);
    }
    return -3;
}
int main(void) {
    off_t physical = 0; uint8_t *arena = 0;
    FILE *log = fopen("/data/gds-base-probe.txt", "w"); if (!log) log = stdout;
    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), 0x200000, 0x4000,
                                     ORBIS_KERNEL_WB_ONION, &physical) < 0 ||
        sceKernelMapDirectMemory((void **)&arena, 0x200000,
            ORBIS_KERNEL_PROT_CPU_RW | ORBIS_KERNEL_PROT_GPU_RW, 0, physical, 0x4000) < 0) {
        fprintf(log, "ALLOCATION_FAILED\n"); fclose(log); return 1;
    }
    memcpy(arena, probe_shader, sizeof(probe_shader));
    unsigned failures = 0;
    for (uint32_t i = 0; i < 64; ++i) {
        int rc = run(arena);
        if (rc < 0) {
            fprintf(log, "SUBMISSION_FAILED rc=%d\n", rc); fflush(log);
            for (;;) sceKernelUsleep(1000000);
        }
        const uint32_t *out = (const uint32_t *)(arena + 0x60000);
        const uint32_t expected[] = {17, 29, 17, 20, 23, 41, 23, 41};
        for (unsigned j = 0; j < 8; ++j) {
            if (out[j] != expected[j]) ++failures;
            fprintf(log, "case=%u word=%u actual=%u expected=%u failures=%u\n",
                    i, j, out[j], expected[j], failures);
        }
        fflush(log);
    }
    fprintf(log, "RESULT=%s\n", failures ? "FAIL" : "PASS"); fclose(log);
    sceKernelMunmap(arena, 0x200000); sceKernelReleaseDirectMemory(physical, 0x200000);
    return failures != 0;
}
