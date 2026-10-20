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

static const uint32_t inputs[] = {
    0x00000000, 0x80000000, 0x3f800000, 0xbf800000,
    0x40000000, 0xc0000000, 0x3f000000, 0xbf000000,
    0x7f800000, 0xff800000, 0x7fc00001, 0xffc00001,
    0x00000001, 0x80000001, 0x7f7fffff, 0xff7fffff
};
static const uint32_t expected[] = {
    0x00000000, 0x00000000, 0x3f800000, 0xbf800000,
    0x3f000000, 0xbf000000, 0x40000000, 0xc0000000,
    0x00000000, 0x80000000
};
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
static int run(uint8_t *arena, unsigned denorm_mode) {
    uint32_t *cmd = (uint32_t *)(arena + 0x10000);
    uint32_t *in = (uint32_t *)(arena + 0x50000);
    uint32_t *out = (uint32_t *)(arena + 0x60000);
    volatile uint32_t *done = (uint32_t *)(arena + 0x41000);
    for (unsigned lane = 0; lane < 64; ++lane) in[lane] = inputs[lane % 16];
    memset(out, 0xa5, 64 * 8 + 256); *done = 0;
    if (sceGnmDrawInitDefaultHardwareState350(cmd, 256) < 0) return -1;
    uint32_t n = 256;
    uint32_t ud[8];
    descriptor(ud, in, 4, 64); descriptor(ud + 4, out, 8, 64);
    const uint32_t cs[] = {(uintptr_t)arena >> 8, (uintptr_t)arena >> 40,
        (denorm_mode << 16) | (1 << 21) | (1 << 23), 8 << 1, 64, 1, 1};
    if (sceGnmSetCsShader(cmd + n, 25, cs) < 0) return -2; n += 25;
    cmd[n++] = 0xc0087602; cmd[n++] = 0x240;
    memcpy(cmd + n, ud, sizeof(ud)); n += 8;
    if (sceGnmDispatchDirect(cmd + n, 9, 1, 1, 1, 1) < 0) return -2; n += 9;
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
        if (*done == 1) {flush(out, 64 * 8 + 256); return 0;}
        sceKernelUsleep(1000);
    }
    return -3;
}
int main(void) {
    off_t physical = 0; uint8_t *arena = 0;
    FILE *log = fopen("/data/legacy-reciprocal-probe.txt", "w"); if (!log) log = stdout;
    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), 0x200000, 0x4000,
                                     ORBIS_KERNEL_WB_ONION, &physical) < 0 ||
        sceKernelMapDirectMemory((void **)&arena, 0x200000,
            ORBIS_KERNEL_PROT_CPU_RW | ORBIS_KERNEL_PROT_GPU_RW, 0, physical, 0x4000) < 0) {
        fprintf(log, "ALLOCATION_FAILED\n"); fclose(log); return 1;
    }
    memset(arena, 0, 0x200000); memcpy(arena, probe_shader, sizeof(probe_shader));
    unsigned failures = 0;
    for (unsigned mode = 0; mode < 4; ++mode) {
        int rc = run(arena, mode);
        if (rc < 0) {
            fprintf(log, "SUBMISSION_FAILED rc=%d\n", rc); fflush(log);
            for (;;) sceKernelUsleep(1000000);
        }
        const uint32_t *out = (const uint32_t *)(arena + 0x60000);
        for (unsigned lane = 0; lane < 64; ++lane) {
            const unsigned i = lane % 16;
            const uint32_t result = out[lane * 2];
            if (i < 10 && result != expected[i]) ++failures;
            if ((i == 10 || i == 11) &&
                ((result & 0x7f800000) != 0x7f800000 || (result & 0x7fffff) == 0)) ++failures;
            if (lane < 16) fprintf(log, "mode=%u input=%08x legacy=%08x ieee=%08x checked=%u\n",
                                  mode, inputs[i], result, out[lane * 2 + 1], i < 12);
        }
        for (unsigned i = 0; i < 256; ++i) {
            if (arena[0x60000 + 64 * 8 + i] != 0xa5) ++failures;
        }
    }
    fprintf(log, "RESULT=%s failures=%u\n", failures ? "FAIL" : "PASS", failures); fclose(log);
    sceKernelMunmap(arena, 0x200000); sceKernelReleaseDirectMemory(physical, 0x200000);
    return failures != 0;
}
