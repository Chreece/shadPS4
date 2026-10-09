// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <stdint.h>
#include <stdio.h>

using Thread = void *;
using Attr = void *;

extern "C" {
Thread scePthreadSelf();
int scePthreadCreate(Thread *, const Attr *, void *(*)(void *), void *,
                     const char *);
int scePthreadJoin(Thread, void **);
int scePthreadAttrInit(Attr *);
int scePthreadAttrDestroy(Attr *);
int scePthreadAttrSetaffinity(Attr *, uint64_t);
int scePthreadGetaffinity(Thread, uint64_t *);
int scePthreadSetaffinity(Thread, uint64_t);
int sceKernelGetCurrentCpu();
int sceKernelUsleep(unsigned);
int sceSystemServiceLoadExec(const char *, const char *const *);
}

struct Worker {
  uint64_t mask;
  uint64_t samples;
  uint64_t failures;
};

static int started;
static int stopping;

static void *Observe(void *arg) {
  auto &worker = *static_cast<Worker *>(arg);
  __atomic_add_fetch(&started, 1, __ATOMIC_RELEASE);
  while (!__atomic_load_n(&stopping, __ATOMIC_ACQUIRE)) {
    uint64_t actual = 0;
    if (scePthreadGetaffinity(scePthreadSelf(), &actual) != 0 ||
        actual != worker.mask) {
      ++worker.failures;
    }
    for (int i = 0; i < 1000; ++i) {
      const int cpu = sceKernelGetCurrentCpu();
      if (cpu < 0 || cpu >= 8 || !(worker.mask & (uint64_t{1} << cpu))) {
        ++worker.failures;
      }
      ++worker.samples;
    }
    if (scePthreadSetaffinity(scePthreadSelf(), worker.mask) != 0) {
      ++worker.failures;
    }
    sceKernelUsleep(1000);
  }
  return nullptr;
}

int main() {
  setvbuf(stdout, nullptr, _IONBF, 0);
  uint64_t original = 0;
  int failures = scePthreadGetaffinity(scePthreadSelf(), &original) != 0;
  Worker workers[8]{};
  Thread threads[8]{};
  int count = 0;
  for (int cpu = 0; cpu < 8; ++cpu) {
    if (!(original & (uint64_t{1} << cpu))) {
      continue;
    }
    Attr attr = nullptr;
    workers[cpu].mask = uint64_t{1} << cpu;
    int ret = scePthreadAttrInit(&attr);
    if (ret == 0) {
      ret = scePthreadAttrSetaffinity(&attr, workers[cpu].mask);
    }
    if (ret == 0) {
      ret = scePthreadCreate(&threads[cpu], &attr, Observe, &workers[cpu],
                             "affinity-live");
    }
    if (attr != nullptr) {
      scePthreadAttrDestroy(&attr);
    }
    if (ret != 0) {
      ++failures;
      threads[cpu] = nullptr;
    } else {
      ++count;
    }
  }
  while (__atomic_load_n(&started, __ATOMIC_ACQUIRE) != count) {
    sceKernelUsleep(1000);
  }
  printf("AFFINITY_DYNAMIC_READY workers=%d mask=%llx\n", count,
         static_cast<unsigned long long>(original));
  sceKernelUsleep(8000000);
  __atomic_store_n(&stopping, 1, __ATOMIC_RELEASE);
  uint64_t samples = 0;
  for (int cpu = 0; cpu < 8; ++cpu) {
    if (threads[cpu] != nullptr) {
      if (scePthreadJoin(threads[cpu], nullptr) != 0) {
        ++failures;
      }
      failures += workers[cpu].failures;
      samples += workers[cpu].samples;
    }
  }
  printf("AFFINITY_DYNAMIC_RESULT failures=%d samples=%llu workers=%d\n",
         failures, static_cast<unsigned long long>(samples), count);
  sceSystemServiceLoadExec("EXIT", nullptr);
  return failures != 0;
}
