#include "copy.h"
#include "vertex.h"
#include <emmintrin.h>
#include <orbis/libkernel.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>

extern int32_t sceGnmDrawInitDefaultHardwareState350(uint32_t *, uint32_t);
extern int32_t sceGnmSetVsShader(uint32_t *, uint32_t, const uint32_t *,
                                 uint32_t);
extern int32_t sceGnmSetPsShader350(uint32_t *, uint32_t, const uint32_t *);
extern int32_t sceGnmSetCsShader(uint32_t *, uint32_t, const uint32_t *);
extern int32_t sceGnmDrawIndexAuto(uint32_t *, uint32_t, uint32_t, uint32_t);
extern int32_t sceGnmDispatchDirect(uint32_t *, uint32_t, uint32_t, uint32_t,
                                    uint32_t, uint32_t);
extern int32_t sceGnmSubmitCommandBuffers(uint32_t, void **, uint32_t *,
                                          void **, uint32_t *);
extern int32_t sceGnmSubmitDone(void);

static void flush(void *ptr, size_t bytes) {
  uintptr_t first = (uintptr_t)ptr & ~(uintptr_t)63;
  for (uintptr_t p = first; p < (uintptr_t)ptr + bytes; p += 64)
    _mm_clflush((void *)p);
  _mm_mfence();
}
static void reg(uint32_t *cmd, uint32_t *n, uint32_t offset, uint32_t value) {
  cmd[(*n)++] = 0xc0016900;
  cmd[(*n)++] = offset;
  cmd[(*n)++] = value;
}
static void descriptor(uint32_t *dst, void *ptr) {
  uint64_t a = (uintptr_t)ptr;
  dst[0] = a;
  dst[1] = (a >> 32 & 255) | (4 << 16);
  dst[2] = 4096;
  dst[3] = 0x18027fac;
}
static int submit(uint8_t *arena, uint32_t n) {
  uint32_t *cmd = (uint32_t *)(arena + 0x10000);
  volatile uint32_t *done = (uint32_t *)(arena + 0x40000);
  *done = 0;
  cmd[n++] = 0xc0044700;
  cmd[n++] = 20 | (5 << 8);
  cmd[n++] = (uintptr_t)done;
  cmd[n++] = ((uintptr_t)done >> 32) | (1 << 29);
  cmd[n++] = 1;
  cmd[n++] = 0;
  flush(arena, 0x400000);
  void *buffers[] = {cmd};
  uint32_t sizes[] = {n * 4};
  int rc = sceGnmSubmitCommandBuffers(1, buffers, sizes, 0, 0);
  if (rc < 0)
    return rc;
  sceGnmSubmitDone();
  for (unsigned i = 0; i < 5000; ++i) {
    flush((void *)done, 4);
    if (*done == 1)
      return 0;
    sceKernelUsleep(1000);
  }
  return -3;
}
static int render(uint8_t *arena, uint32_t depth) {
  uint32_t *cmd = (uint32_t *)(arena + 0x10000);
  if (sceGnmDrawInitDefaultHardwareState350(cmd, 256) < 0)
    return -1;
  uint32_t n = 256;
  const uint32_t vs[] = {
      (uintptr_t)arena >> 8, 0, 1 | (1 << 21) | (1 << 23), 1 << 1, 0, 4, 0};
  if (sceGnmSetVsShader(cmd + n, 29, vs, 0) < 0)
    return -1;
  n += 29;
  if (sceGnmSetPsShader350(cmd + n, 40, 0) < 0)
    return -1;
  n += 40;
  cmd[n++] = 0xc0017600;
  cmd[n++] = 0x4c;
  cmd[n++] = depth;
  reg(cmd, &n, 0, 0);
  reg(cmd, &n, 2, 0);
  reg(cmd, &n, 3, 0);
  reg(cmd, &n, 5, (uintptr_t)(arena + 0x300000) >> 8);
  reg(cmd, &n, 0xb, 0x3f800000);
  reg(cmd, &n, 0xc, 0);
  reg(cmd, &n, 0xd, 512 | (512 << 16));
  reg(cmd, &n, 0x10, 3 | (1 << 29));
  reg(cmd, &n, 0x11, 1 << 29);
  reg(cmd, &n, 0x12, (uintptr_t)(arena + 0x100000) >> 8);
  reg(cmd, &n, 0x13, 0);
  reg(cmd, &n, 0x14, (uintptr_t)(arena + 0x100000) >> 8);
  reg(cmd, &n, 0x15, 0);
  reg(cmd, &n, 0x16, 63 | (63 << 11));
  reg(cmd, &n, 0x17, 4095);
  reg(cmd, &n, 0x81, 0x80000000);
  reg(cmd, &n, 0x82, 512 | (512 << 16));
  reg(cmd, &n, 0x90, 0x80000000);
  reg(cmd, &n, 0x91, 512 | (512 << 16));
  reg(cmd, &n, 0x94, 0x80000000);
  reg(cmd, &n, 0x95, 512 | (512 << 16));
  reg(cmd, &n, 0xb4, 0);
  reg(cmd, &n, 0xb5, 0x3f800000);
  for (unsigned i = 0; i < 4; ++i)
    reg(cmd, &n, 0x10f + i, 0x43800000);
  reg(cmd, &n, 0x113, 0x3f800000);
  reg(cmd, &n, 0x114, 0);
  reg(cmd, &n, 0x200, 0x76);
  reg(cmd, &n, 0x202, 0);
  reg(cmd, &n, 0x204, 1 << 19);
  reg(cmd, &n, 0x205, 0);
  reg(cmd, &n, 0x206, 0x3f);
  reg(cmd, &n, 0x2af, 2);
  reg(cmd, &n, 0x2d5, 0);
  cmd[n++] = 0xc0017900;
  cmd[n++] = 0x242;
  cmd[n++] = 4;
  if (sceGnmDrawIndexAuto(cmd + n, 7, 3, 0) < 0)
    return -1;
  n += 7;
  return submit(arena, n);
}
static int copy_metadata(uint8_t *arena) {
  uint32_t *cmd = (uint32_t *)(arena + 0x10000);
  uint32_t ud[8];
  descriptor(ud, arena + 0x300000);
  descriptor(ud + 4, arena + 0x310000);
  const uint32_t cs[] = {(uintptr_t)(arena + 0x1000) >> 8,
                         0,
                         (1 << 6) | (1 << 21) | (1 << 23),
                         (8 << 1) | (1 << 7),
                         64,
                         1,
                         1};
  uint32_t n = 0;
  if (sceGnmSetCsShader(cmd, 25, cs) < 0)
    return -1;
  n += 25;
  cmd[n++] = 0xc0087602;
  cmd[n++] = 0x240;
  memcpy(cmd + n, ud, sizeof(ud));
  n += 8;
  if (sceGnmDispatchDirect(cmd + n, 9, 64, 1, 1, 1) < 0)
    return -1;
  n += 9;
  cmd[n++] = 0xc0004602;
  cmd[n++] = 7 | (4 << 8);
  return submit(arena, n);
}
int main(void) {
  off_t physical = 0;
  uint8_t *arena = 0;
  FILE *log = fopen("/data/depth-htile-probe.txt", "w");
  if (!log)
    log = stdout;
  if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(), 0x400000,
                                    0x10000, ORBIS_KERNEL_WB_ONION,
                                    &physical) < 0 ||
      sceKernelMapDirectMemory((void **)&arena, 0x400000,
                               ORBIS_KERNEL_PROT_CPU_RW |
                                   ORBIS_KERNEL_PROT_GPU_RW,
                               0, physical, 0x10000) < 0) {
    fprintf(log, "ALLOCATION_FAILED\n");
    fclose(log);
    return 1;
  }
  memset(arena, 0, 0x400000);
  memcpy(arena, vertex_shader, sizeof(vertex_shader));
  memcpy(arena + 0x1000, copy_shader, sizeof(copy_shader));
  const uint32_t depths[] = {0x3e800000, 0x3f400000, 0x3f800000, 0};
  const unsigned quantized[] = {4096, 12287, 16383, 0};
  unsigned failures = 0;
  for (unsigned c = 0; c < 4; ++c) {
    for (unsigned i = 0; i < 512 * 512; ++i)
      ((uint32_t *)(arena + 0x100000))[i] = 0x3f800000;
    for (unsigned i = 0; i < 4096; ++i)
      ((uint32_t *)(arena + 0x300000))[i] = 0xfffc000f;
    memset(arena + 0x310000, 0xa5, 16384 + 256);
    int rc = render(arena, depths[c]);
    if (rc == 0)
      rc = copy_metadata(arena);
    if (rc < 0) {
      fprintf(log, "SUBMISSION_FAILED case=%u rc=%d\n", c, rc);
      fflush(log);
      for (;;)
        sceKernelUsleep(1000000);
    }
    flush(arena + 0x310000, 16384 + 256);
    const uint32_t *out = (const uint32_t *)(arena + 0x310000);
    unsigned bad = 0;
    for (unsigned i = 0; i < 4096; ++i) {
      unsigned lo = (out[i] >> 4) & 16383, hi = out[i] >> 18;
      unsigned q = quantized[c];
      if (lo > q + 1 || lo + 1 < q || hi > q + 1 || hi + 1 < q)
        ++bad;
    }
    for (unsigned i = 0; i < 256; ++i)
      if (arena[0x314000 + i] != 0xa5)
        ++bad;
    failures += bad;
    fprintf(log, "case=%u depth=%08x first=%08x bad_tiles_or_guards=%u\n", c,
            depths[c], out[0], bad);
  }
  fprintf(log, "RESULT=%s failures=%u\n", failures ? "FAIL" : "PASS", failures);
  fclose(log);
  sceKernelMunmap(arena, 0x400000);
  sceKernelReleaseDirectMemory(physical, 0x400000);
  return failures != 0;
}
