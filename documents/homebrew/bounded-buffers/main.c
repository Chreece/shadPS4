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
static int dispatch(uint8_t *arena, uint32_t selected, uint32_t count) {
    uint32_t *cmd = (uint32_t *)(arena + 0x10000);
    uint32_t *table = (uint32_t *)(arena + 0x20000);
    uint32_t *selector = (uint32_t *)(arena + 0x21000);
    volatile uint32_t *done = (uint32_t *)(arena + 0x22000);
    *selector = selected;
    *done = 0;
    uint32_t ud[9];
    descriptor(ud, table, 0, count * 32);
    descriptor(ud + 4, selector, 4, 1);
    ud[8] = count;
    uint32_t cs[7] = {(uintptr_t)arena >> 8, 0, 1 | (2 << 6) | (1 << 21) | (1 << 23), 9 << 1, 1, 1, 1};
    if (sceGnmDrawInitDefaultHardwareState350(cmd, 256) < 0) return -1;
    uint32_t n = 256;
    if (sceGnmSetCsShader(cmd+n, 25, cs) < 0) return -2;
    n += 25;
    cmd[n++] = 0xc0097602;
    cmd[n++] = 0x240;
    memcpy(cmd+n, ud, sizeof(ud)); n += 9;
    if (sceGnmDispatchDirect(cmd+n, 9, 1, 1, 1, 0) < 0) return -3;
    n += 9;
    cmd[n++] = 0xc0004602; cmd[n++] = 7 | (4 << 8);
    cmd[n++] = 0xc0044700; cmd[n++] = 20 | (5 << 8);
    cmd[n++] = (uintptr_t)done;
    cmd[n++] = ((uintptr_t)done >> 32) | (1 << 29);
    cmd[n++] = 1; cmd[n++] = 0;
    flush(arena, 0x40000);
    void *buffers[] = {cmd}; uint32_t sizes[] = {n * 4};
    int rc = sceGnmSubmitCommandBuffers(1, buffers, sizes, 0, 0);
    if (rc < 0) return rc;
    sceGnmSubmitDone();
    for (unsigned i = 0; i < 5000; ++i) {
        flush((void *)done, 4);
        if (*done == 1) {flush(arena + 0x30000, 0x10000); return 0;}
        sceKernelUsleep(1000);
    }
    return -4;
}
int main(void) {
    off_t physical = 0;
    uint8_t *arena = 0;
    FILE *log = fopen("/data/bounded-buffer-probe.txt", "w");
    if (!log) log = stdout;
    if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), 0x40000, 0x4000,
                                     ORBIS_KERNEL_WB_ONION, &physical) < 0 ||
        sceKernelMapDirectMemory((void **)&arena, 0x40000,
            ORBIS_KERNEL_PROT_CPU_RW | ORBIS_KERNEL_PROT_GPU_RW, 0, physical, 0x4000) < 0) {
        fprintf(log, "ALLOCATION_FAILED\n"); fclose(log); return 1;
    }
    memcpy(arena, probe_shader, sizeof(probe_shader));
    const uint32_t counts[] = {0, 1, 4, 13, 10, 11, 0, 5, 13, 4};
    unsigned failures = 0;
    for (unsigned c = 0; c < sizeof(counts)/sizeof(counts[0]); ++c) {
        uint32_t count = counts[c];
        for (uint32_t selected = 0; selected <= count; ++selected) {
            memset(arena+0x30000, 0xa5, 0x10000);
            for (unsigned i = 0; i < 13; ++i) {
                uint32_t stride = i & 1 ? 8 : 4;
                uint8_t *data = arena+0x30000+i*256;
                uint32_t *record = (uint32_t *)(arena+0x20000+i*32);
                descriptor(record, data, stride, 4);
                if (i >= count) record[3] = (record[3] & ~0x7f000u) | (11u << 15) | (6u << 12);
                float initial = (float)i+1.25f;
                memcpy(data+stride, &initial, 4);
            }
            int rc = dispatch(arena, selected, count);
            if (rc < 0) {
                fprintf(log, "DISPATCH_FAILED rc=%d count=%u selected=%u\n", rc, count, selected);
                fflush(log);
                // Keep GPU-visible memory alive after an uncertain completion.
                for (;;) sceKernelUsleep(1000000);
            }
            for (unsigned i = 0; i < 13; ++i) {
                uint32_t stride = i & 1 ? 8 : 4;
                float expected = (float)i+1.25f;
                uint32_t bits; memcpy(&bits, &expected, 4);
                if (i == selected && selected < count) {
                    bits += 256; memcpy(&expected, &bits, 4); expected += 1.0f;
                    memcpy(&bits, &expected, 4);
                }
                const uint8_t *data = arena+0x30000+i*256;
                uint32_t actual; memcpy(&actual, data+stride, 4);
                if (actual != bits) ++failures;
                for (unsigned j = 0; j < 256; ++j)
                    if ((j < stride || j >= stride+4) && data[j] != 0xa5) ++failures;
            }
            fprintf(log, "count=%u selected=%u cumulative_failures=%u\n", count, selected, failures);
            fflush(log);
        }
    }
    fprintf(log, "RESULT=%s\n", failures ? "FAIL" : "PASS"); fclose(log);
    sceKernelMunmap(arena, 0x40000); sceKernelReleaseDirectMemory(physical, 0x40000);
    return failures != 0;
}
