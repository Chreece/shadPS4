// SPDX-License-Identifier: GPL-2.0-or-later
#define _DEFAULT_SOURCE
#include "shader.h"
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

extern int sceKernelAllocateDirectMemory(int64_t, int64_t, size_t, size_t, int,
                                         int64_t *);
extern int sceKernelMapDirectMemory(void **, size_t, int, int, int64_t, size_t);
extern int64_t sceKernelGetDirectMemorySize(void);
extern uint32_t sceGnmDrawInitDefaultHardwareState350(uint32_t *, uint32_t);
extern int sceGnmSetCsShader(uint32_t *, uint32_t, const uint32_t *);
extern int sceGnmDispatchDirect(uint32_t *, uint32_t, uint32_t, uint32_t,
                                uint32_t, uint32_t);
extern int sceGnmSubmitCommandBuffers(uint32_t, void **, uint32_t *, void **,
                                      uint32_t *);
extern int sceGnmSubmitDone(void);

static uint64_t ms(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return (uint64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

int main(void) {
  int64_t physical = 0;
  void *allocation = 0;
  const size_t allocation_size = 0x80000;
  if (sceKernelAllocateDirectMemory(0, sceKernelGetDirectMemorySize(),
                                    allocation_size, 0x4000, 3,
                                    &physical) < 0 ||
      sceKernelMapDirectMemory(&allocation, allocation_size, 0x33, 0, physical,
                               0x4000) < 0)
    return 1;
  uint8_t *memory = allocation;
  if (((uintptr_t)memory + allocation_size) >> 40)
    return 2;
  uint32_t *command = (uint32_t *)memory;
  uint32_t *shader = (uint32_t *)(memory + 0x4000);
  uint32_t *output = (uint32_t *)(memory + 0x8000);
  volatile uint32_t *fence = (volatile uint32_t *)(memory + 0xc000);
  uint32_t *texture = (uint32_t *)(memory + 0x10000);
  memcpy(shader, indexed_lod_cs, sizeof(indexed_lod_cs));
  uint32_t *table = (uint32_t *)(memory + 0x70000);
  for (unsigned image = 0; image < 2; ++image) {
    uint32_t *pixels = texture + image * 0x8000;
    for (unsigned y = 0; y < 128; ++y)
      for (unsigned x = 0; x < 128; ++x)
        pixels[y * 128 + x] = x | (y << 8) | ((image ? 192u : 64u) << 16) | 0xff000000u;
    uint64_t address = (uintptr_t)pixels >> 8;
    uint32_t *record = table + image * 16;
    memset(record, 0, 64);
    record[0] = (uint32_t)address;
    record[1] = (uint32_t)(address >> 32) | (10u << 20);
    record[2] = 127u | (127u << 14);
    record[3] = 0xfacu | (8u << 20) | (9u << 28);
    record[4] = 127u << 13;
  }
  FILE *log = fopen("/data/indexed-image-lod-probe.log", "w");
  if (!log)
    return 3;
  unsigned failures = 0;
  for (unsigned test = 0; test < 2; test++) {
    memset(output, 0, 64);
    output[0] = test;
    *fence = 0;
    unsigned used = sceGnmDrawInitDefaultHardwareState350(command, 256);
    if (!used || used > 256)
      return 4;
    const uint32_t cs_regs[] = {
        (uint32_t)((uintptr_t)shader >> 8), 0, 0x000c0081, 8u << 1, 1, 1, 1};
    if (sceGnmSetCsShader(command + used, 25, cs_regs) < 0)
      return 5;
    used += 25;
    const uint64_t out_address = (uintptr_t)output;
    const uint64_t table_address = (uintptr_t)table;
    const uint32_t user[] = {
        (uint32_t)out_address, (uint32_t)(out_address >> 32) & 255u, 64, 0x00027fac,
        (uint32_t)table_address, (uint32_t)(table_address >> 32) & 255u, 128, 0x00027fac,
    };
    command[used++] = 0xc0087602;
    command[used++] = 0x240;
    memcpy(command + used, user, sizeof(user));
    used += 8;
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
    if (sceGnmSubmitCommandBuffers(1, buffers, lengths, 0, 0) < 0 ||
        sceGnmSubmitDone() < 0)
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
    for (unsigned sample = 0; sample < 3; ++sample) {
      const unsigned x = sample == 2 ? 65 : 64, y = sample == 2 ? 63 : 64;
      const unsigned expected[] = {x, y, test ? 192u : 64u, 255};
      float values[4];
      memcpy(values, output + 4 + sample * 4, sizeof(values));
      unsigned matches = 1;
      for (unsigned c = 0; c < 4; ++c) {
        float error = values[c] - (float)expected[c] / 255.f;
        matches &= error >= -0.00001f && error <= 0.00001f;
      }
      failures += !matches;
      fprintf(log, "record=%u sample=%u rgba=%.8f,%.8f,%.8f,%.8f match=%u\n",
              test, sample, values[0], values[1], values[2], values[3], matches);
    }
    fflush(log);
  }
  fprintf(
      log,
      "observations_complete failures=%u; compare hardware and emulator logs\n",
      failures);
  fclose(log);
  return failures ? 9 : 0;
}
