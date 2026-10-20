#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include <emmintrin.h>
#include <orbis/libkernel.h>
#include "vs.h"
#include "ps.h"
#include <math.h>

extern int32_t sceGnmDrawInitDefaultHardwareState350(uint32_t *, uint32_t);
extern int32_t sceGnmSetVsShader(uint32_t *, uint32_t, const uint32_t *, uint32_t);
extern int32_t sceGnmSetPsShader350(uint32_t *, uint32_t, const uint32_t *);
extern int32_t sceGnmDrawIndexAuto(uint32_t *, uint32_t, uint32_t, uint32_t);
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
static void reg(uint32_t *cmd, uint32_t *n, uint32_t opcode, uint32_t offset, uint32_t value) {
    cmd[(*n)++] = 0xc0010000 | opcode << 8;
    cmd[(*n)++] = offset; cmd[(*n)++] = value;
}
static uint32_t bits(float f) { uint32_t v; memcpy(&v, &f, 4); return v; }
static int run(uint8_t *arena) {
    uint32_t *cmd = (uint32_t *)(arena + 0x10000);
    uint8_t *out = arena + 0x60000;
    volatile uint32_t *done = (uint32_t *)(arena + 0x41000);
    memset(out, 0xa5, 64 * 64 * 8 + 256); *done = 0;
    if (sceGnmDrawInitDefaultHardwareState350(cmd, 256) < 0) return -1;
    uint32_t n = 256;
    const uint32_t vs[] = {(uintptr_t)arena >> 8, 0, 1 | 0xc0000, 0, 0, 4, 0};
    const uint32_t flags = (1 << 1) | (1 << 8) | (1 << 9);
    const uint32_t ps[] = {(uintptr_t)(arena + 0x1000) >> 8, 0,
        1 | 0xc0000, 4 << 1, 0, 0, flags, flags, 1, 0, (1 << 9) | (1 << 10), 0};
    if (sceGnmSetVsShader(cmd + n, 29, vs, 0) < 0) return -2; n += 29;
    if (sceGnmSetPsShader350(cmd + n, 40, ps) < 0) return -2; n += 40;
    uint32_t ud[4]; descriptor(ud, out, 8, 4096);
    cmd[n++] = 0xc0047600; cmd[n++] = 0x0c;
    memcpy(cmd + n, ud, sizeof(ud)); n += 4;
    reg(cmd, &n, 0x69, 0x1a0, 0);
    reg(cmd, &n, 0x69, 0x200, 0);
    reg(cmd, &n, 0x69, 0x202, 0);
    reg(cmd, &n, 0x69, 0x08e, 0);
    reg(cmd, &n, 0x69, 0x204, 0);
    reg(cmd, &n, 0x69, 0x205, 0);
    reg(cmd, &n, 0x69, 0x206, 0x3f);
    reg(cmd, &n, 0x69, 0x2d5, 0);
    reg(cmd, &n, 0x69, 0x2f8, 0);
    reg(cmd, &n, 0x69, 0x293, 0);
    reg(cmd, &n, 0x69, 0x10f, bits(32));
    reg(cmd, &n, 0x69, 0x110, bits(32));
    reg(cmd, &n, 0x69, 0x111, bits(32));
    reg(cmd, &n, 0x69, 0x112, bits(32));
    reg(cmd, &n, 0x69, 0x113, bits(1));
    reg(cmd, &n, 0x69, 0x114, 0);
    reg(cmd, &n, 0x69, 0x00c, 0);
    reg(cmd, &n, 0x69, 0x00d, 64 | 64 << 16);
    reg(cmd, &n, 0x69, 0x081, 0x80000000);
    reg(cmd, &n, 0x69, 0x082, 64 | 64 << 16);
    reg(cmd, &n, 0x69, 0x090, 0x80000000);
    reg(cmd, &n, 0x69, 0x091, 64 | 64 << 16);
    reg(cmd, &n, 0x79, 0x242, 4);
    cmd[n++] = 0xc0002f00; cmd[n++] = 1;
    if (sceGnmDrawIndexAuto(cmd + n, 7, 3, 0) < 0) return -2; n += 7;
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
        if (*done == 1) {flush(out, 64 * 64 * 8 + 256); return 0;}
        sceKernelUsleep(1000);
    }
    return -3;
}
int main(void) {
    off_t physical = 0; uint8_t *arena = 0;
    FILE *log = fopen("/data/component-interpolation-probe.txt", "w"); if (!log) log = stdout;
    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), 0x200000, 0x4000,
                                     ORBIS_KERNEL_WB_ONION, &physical) < 0 ||
        sceKernelMapDirectMemory((void **)&arena, 0x200000,
            ORBIS_KERNEL_PROT_CPU_RW | ORBIS_KERNEL_PROT_GPU_RW, 0, physical, 0x4000) < 0) {
        fprintf(log, "ALLOCATION_FAILED\n"); fclose(log); return 1;
    }
    memcpy(arena, vs_shader, sizeof(vs_shader));
    memcpy(arena + 0x1000, ps_shader, sizeof(ps_shader));
    unsigned failures = 0;
    int rc = run(arena);
    if (rc < 0) {
        fprintf(log, "SUBMISSION_FAILED rc=%d\n", rc); fflush(log);
        for (;;) sceKernelUsleep(1000000);
    }
    const uint32_t *out = (const uint32_t *)(arena + 0x60000);
    for (unsigned y = 4; y < 60; ++y) for (unsigned x = 4; x < 60; ++x) {
        unsigned i = (y * 64 + x) * 2; float smooth;
        memcpy(&smooth, out + i + 1, 4);
        float expected = ((float)x + 0.5f) / 32.0f - 1.0f;
        if (out[i] != 2 || !isfinite(smooth) || fabsf(smooth - expected) > 0.001f) {
            if (failures < 16) fprintf(log, "x=%u y=%u raw=%08x smooth=%g expected=%g\n", x, y, out[i], smooth, expected);
            ++failures;
        }
    }
    for (unsigned i = 8192; i < 8256; ++i) if (out[i] != 0xa5a5a5a5) ++failures;
    fprintf(log, "RESULT=%s\n", failures ? "FAIL" : "PASS"); fclose(log);
    sceKernelMunmap(arena, 0x200000); sceKernelReleaseDirectMemory(physical, 0x200000);
    return failures != 0;
}
