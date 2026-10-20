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
static int run(uint8_t *arena) {
    uint32_t *cmd = (uint32_t *)(arena + 0x10000);
    float *texture = (float *)(arena + 0x50000);
    uint8_t *out = arena + 0x60000;
    volatile uint32_t *done = (uint32_t *)(arena + 0x41000);
    for (unsigned y = 0; y < 8; ++y) for (unsigned x = 0; x < 8; ++x) {
        unsigned i = (y * 16 + x) * 4;
        texture[i] = (x + 1) / 16.0f; texture[i + 1] = (y + 1) / 16.0f;
        texture[i + 2] = 0.25f; texture[i + 3] = x >= 4 ? 1.0f : 0.0f;
    }
    memset(out, 0xa5, 64 * 48 + 256); *done = 0;
    if (sceGnmDrawInitDefaultHardwareState350(cmd, 256) < 0) return -1;
    uint32_t n = 256;
    uint32_t ud[16] = {0};
    uint64_t address = (uintptr_t)texture >> 8;
    ud[0] = address; ud[1] = (address >> 32) | (14 << 20) | (7 << 26);
    ud[2] = 7 | (7 << 14);
    ud[3] = 4 | (5 << 3) | (6 << 6) | (7 << 9) | (8 << 20) | (9u << 28);
    ud[4] = 15 << 13;
    ud[8] = 2 | (2 << 3) | (2 << 6);
    descriptor(ud + 12, out, 48, 64);
    const uint32_t cs[] = {(uintptr_t)arena >> 8, 0,
        3 | (1 << 6) | (1 << 21) | (1 << 23), 16 << 1, 64, 1, 1};
    if (sceGnmSetCsShader(cmd + n, 25, cs) < 0) return -2; n += 25;
    cmd[n++] = 0xc0107602; cmd[n++] = 0x240;
    memcpy(cmd + n, ud, sizeof(ud)); n += 16;
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
        if (*done == 1) {flush(out, 64 * 48 + 256); return 0;}
        sceKernelUsleep(1000);
    }
    return -3;
}
int main(void) {
    off_t physical = 0; uint8_t *arena = 0;
    FILE *log = fopen("/data/coarse-texture-gradients-probe.txt", "w"); if (!log) log = stdout;
    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), 0x200000, 0x4000,
                                     ORBIS_KERNEL_WB_ONION, &physical) < 0 ||
        sceKernelMapDirectMemory((void **)&arena, 0x200000,
            ORBIS_KERNEL_PROT_CPU_RW | ORBIS_KERNEL_PROT_GPU_RW, 0, physical, 0x4000) < 0) {
        fprintf(log, "ALLOCATION_FAILED\n"); fclose(log); return 1;
    }
    memset(arena, 0, 0x200000);
    memcpy(arena, probe_shader, sizeof(probe_shader));
    int rc = run(arena);
    if (rc < 0) {
        fprintf(log, "SUBMISSION_FAILED rc=%d\n", rc); fflush(log);
        for (;;) sceKernelUsleep(1000000);
    }
    unsigned failures = 0;
    const float *out = (const float *)(arena + 0x60000);
    for (unsigned lane = 0; lane < 64; ++lane) {
        const unsigned x = lane & 7, y = lane >> 3;
        const float expected[] = {(x + 1) / 16.0f, (y + 1) / 16.0f, 0.25f, x >= 4 ? 1.0f : 0.0f};
        for (unsigned op = 0; op < 3; ++op) for (unsigned c = 0; c < 4; ++c) {
            float actual = out[lane * 12 + op * 4 + c];
            if (actual != expected[c]) {
                ++failures;
                fprintf(log, "lane=%u opcode=%s component=%u actual=%g expected=%g\n",
                        lane, op == 0 ? "D" : op == 1 ? "CD" : "LZ", c, actual, expected[c]);
            }
        }
    }
    for (unsigned i = 0; i < 256; ++i)
        if (arena[0x60000 + 64 * 48 + i] != 0xa5) ++failures;
    fprintf(log, "RESULT=%s failures=%u\n", failures ? "FAIL" : "PASS", failures); fclose(log);
    sceKernelMunmap(arena, 0x200000); sceKernelReleaseDirectMemory(physical, 0x200000);
    return failures != 0;
}
