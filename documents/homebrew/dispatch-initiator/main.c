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
static int run(uint8_t *arena, unsigned previous, unsigned current) {
    uint32_t *cmd = (uint32_t *)(arena + 0x10000);
    uint32_t *args = (uint32_t *)(arena + 0x50000);
    uint32_t *out = (uint32_t *)(arena + 0x60000);
    volatile uint32_t *done = (uint32_t *)(arena + 0x41000);
    args[0] = args[1] = args[2] = 1;
    memset(out, 0xa5, 64 * 4 + 256); *done = 0;
    if (sceGnmDrawInitDefaultHardwareState350(cmd, 256) < 0) return -1;
    uint32_t n = 256;
    uint32_t ud[4];
    descriptor(ud, out, 4, 64);
    const uint32_t cs[] = {(uintptr_t)arena >> 8, (uintptr_t)arena >> 40,
        (1 << 21) | (1 << 23), 4 << 1, 64, 1, 1};
    if (sceGnmSetCsShader(cmd + n, 25, cs) < 0) return -2; n += 25;
    cmd[n++] = 0xc0047602; cmd[n++] = 0x240;
    memcpy(cmd + n, ud, sizeof(ud)); n += 4;
    cmd[n++] = 0xc0031502;
    cmd[n++] = 0; cmd[n++] = 1; cmd[n++] = 1; cmd[n++] = previous;
    cmd[n++] = 0xc0021102; cmd[n++] = 1;
    cmd[n++] = (uintptr_t)args; cmd[n++] = (uintptr_t)args >> 32;
    cmd[n++] = 0xc0011602; cmd[n++] = 0; cmd[n++] = current;
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
        if (*done == 1) {flush(out, 64 * 4 + 256); return 0;}
        sceKernelUsleep(1000);
    }
    return -3;
}
int main(void) {
    off_t physical = 0; uint8_t *arena = 0;
    FILE *log = fopen("/data/dispatch-initiator-probe.txt", "w"); if (!log) log = stdout;
    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), 0x200000, 0x4000,
                                     ORBIS_KERNEL_WB_ONION, &physical) < 0 ||
        sceKernelMapDirectMemory((void **)&arena, 0x200000,
            ORBIS_KERNEL_PROT_CPU_RW | ORBIS_KERNEL_PROT_GPU_RW, 0, physical, 0x4000) < 0) {
        fprintf(log, "ALLOCATION_FAILED\n"); fclose(log); return 1;
    }
    memset(arena, 0, 0x200000);
    memcpy(arena, probe_shader, sizeof(probe_shader));
    unsigned failures = 0;
    for (unsigned previous = 0; previous < 2; ++previous) {
        for (unsigned current = 0; current < 2; ++current) {
            int rc = run(arena, previous, current);
            if (rc < 0) {
                fprintf(log, "SUBMISSION_FAILED rc=%d\n", rc); fflush(log);
                for (;;) sceKernelUsleep(1000000);
            }
            const uint32_t *out = (const uint32_t *)(arena + 0x60000);
            uint32_t expected = current ? 0x13579bdf : 0xa5a5a5a5;
            unsigned mismatch = 0;
            for (unsigned lane = 0; lane < 64; ++lane) {
                if (out[lane] != expected) ++mismatch;
            }
            for (unsigned i = 0; i < 256; ++i) {
                if (arena[0x60000 + 64 * 4 + i] != 0xa5) ++mismatch;
            }
            failures += mismatch;
            fprintf(log, "previous=%u packet=%u expected=%08x mismatches=%u\n",
                    previous, current, expected, mismatch);
        }
    }
    fprintf(log, "RESULT=%s failures=%u\n", failures ? "FAIL" : "PASS", failures); fclose(log);
    sceKernelMunmap(arena, 0x200000); sceKernelReleaseDirectMemory(physical, 0x200000);
    return failures != 0;
}
