// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef XSTATE_ORBIS
#include <sys/mman.h>
#include <ucontext.h>
#endif

#ifdef XSTATE_ORBIS
extern "C" int sceKernelMmap(void *, uint64_t, int, int, int, int64_t, void **);
extern "C" int sceKernelMprotect(const void *, uint64_t, int);
extern "C" int sceKernelMunmap(void *, uint64_t);
extern "C" int sceKernelInstallExceptionHandler(int, void (*)(int, void *));
extern "C" int sceKernelRemoveExceptionHandler(int);
extern "C" int sceSystemServiceLoadExec(const char *, const char *const *);
extern "C" int sceKernelHasNeoMode();

// OpenOrbis bits/signal.h defines Linux ucontext_t, not the Orbis signal ABI.
struct OrbisContextPrefix {
  uint32_t mask[4], padding[12];
  uint64_t registers[16];
  uint32_t trap;
  uint16_t fs, gs;
  uint64_t address;
  uint32_t flags;
  uint16_t es, ds;
  uint64_t error, rip;
};
static_assert(offsetof(OrbisContextPrefix, rip) == 224);
#endif

struct alignas(64) Observation {
  uint64_t flags_before, flags_after, rax, rdx;
  uint32_t pattern, mxcsr;
  const void *values;
  uint64_t reserved[2];
  unsigned char state[832];
};
static_assert(offsetof(Observation, state) == 64);
using Runner = void (*)(void *, uint64_t, Observation *);

#define DECLARE(name)                                                          \
  extern "C" const unsigned char name[], name##_end[], name##_fault[],         \
      name##_resume[]
DECLARE(probe_xsave);
DECLARE(probe_xsave64);
DECLARE(probe_xsaveopt);
DECLARE(probe_xsaveopt64);
DECLARE(probe_xrstor);
DECLARE(probe_xrstor64);
DECLARE(probe_xgetbv);
#undef DECLARE

struct Case {
  const char *name;
  const unsigned char *start, *end, *fault, *resume;
};
#define CASE(name)                                                             \
  {#name, probe_##name, probe_##name##_end, probe_##name##_fault,              \
   probe_##name##_resume}
static const Case cases[]{CASE(xsave),      CASE(xsave64), CASE(xsaveopt),
                          CASE(xsaveopt64), CASE(xrstor),  CASE(xrstor64),
                          CASE(xgetbv)};
#undef CASE
static FILE *output;
static volatile uintptr_t expected_fault, fault_resume;
static volatile sig_atomic_t caught_signal, unexpected_fault;
static unsigned rows, failures;
alignas(64) static uint32_t patterns[128];

static void Fault(int signal, void *context) {
#ifdef XSTATE_ORBIS
  auto &pc = static_cast<OrbisContextPrefix *>(context)->rip;
#else
  auto &pc = static_cast<ucontext_t *>(context)->uc_mcontext.gregs[REG_RIP];
#endif
  if (!expected_fault || static_cast<uintptr_t>(pc) != expected_fault ||
      caught_signal) {
    unexpected_fault = 1;
    _Exit(128 + signal);
  }
  caught_signal = signal;
  pc = fault_resume;
}
#ifndef XSTATE_ORBIS
static void HostFault(int signal, siginfo_t *, void *context) {
  Fault(signal, context);
}
#endif

static int Protect(void *memory, size_t size, int flags) {
#ifdef XSTATE_ORBIS
  return sceKernelMprotect(memory, size, flags);
#else
  return mprotect(memory, size, flags);
#endif
}
static void *Allocate(size_t size) {
  void *memory = nullptr;
#ifdef XSTATE_ORBIS
  if (sceKernelMmap(nullptr, size, 3, 0x1002, -1, 0, &memory))
    return nullptr;
#else
  memory = mmap(nullptr, size, 3, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (memory == MAP_FAILED)
    return nullptr;
#endif
  return memory;
}
static int Release(void *memory, size_t size) {
#ifdef XSTATE_ORBIS
  return sceKernelMunmap(memory, size);
#else
  return munmap(memory, size);
#endif
}

static void Cpuid(uint32_t leaf, uint32_t subleaf, uint32_t (&value)[4]) {
  asm volatile("cpuid"
               : "=a"(value[0]), "=b"(value[1]), "=c"(value[2]), "=d"(value[3])
               : "a"(leaf), "c"(subleaf)
               : "memory");
}

static void Dump(const char *label, const unsigned char *data, size_t size) {
  fprintf(output, "%s row=%u size=%zu bytes=", label, rows, size);
  for (size_t i = 0; i < size; ++i)
    fprintf(output, "%02x", data[i]);
  fputc('\n', output);
}

static int Invoke(unsigned which, unsigned char *generated, const char *mode,
                  const char *name, void *area, uint64_t mask, bool dirty,
                  bool dump = true) {
  const auto &test = cases[which];
  const unsigned char *entry =
      generated ? generated + which * 1024 : test.start;
  Observation result{};
  result.pattern = dirty;
  result.mxcsr = dirty ? 0x5f80 : 0x1f80;
  result.values = patterns;
  caught_signal = 0;
  expected_fault =
      reinterpret_cast<uintptr_t>(entry + (test.fault - test.start));
  fault_resume =
      reinterpret_cast<uintptr_t>(entry + (test.resume - test.start));
  fprintf(output,
          "XSTATE_START row=%u mode=%s op=%s case=%s mask=%016llx dirty=%u\n",
          rows, mode, test.name, name, static_cast<unsigned long long>(mask),
          unsigned(dirty));
  reinterpret_cast<Runner>(const_cast<unsigned char *>(entry))(area, mask,
                                                               &result);
  expected_fault = 0;
  unsigned errors = ((result.flags_before ^ result.flags_after) & 0x8d5) != 0;
  if (which != 6)
    errors += result.rax != mask || result.rdx != mask >> 32;
  failures += errors;
  fprintf(output,
          "XSTATE_RESULT row=%u signal=%d flags=%llx/%llx rax=%016llx "
          "rdx=%016llx errors=%u\n",
          rows, int(caught_signal),
          static_cast<unsigned long long>(result.flags_before),
          static_cast<unsigned long long>(result.flags_after),
          static_cast<unsigned long long>(result.rax),
          static_cast<unsigned long long>(result.rdx), errors);
  if (dump && which != 6)
    Dump("XSTATE_REGISTERS", result.state, sizeof(result.state));
  return caught_signal;
}

int main() {
#ifdef XSTATE_ORBIS
  output = fopen("/data/cpu-xstate-hardware.txt", "w");
#else
  output = fopen("cpu-xstate-hardware.txt", "w");
#endif
  if (!output)
    return 1;
  setvbuf(output, nullptr, _IONBF, 0);
  fprintf(output, "XSTATE_BEGIN version=1\n");
#ifdef XSTATE_ORBIS
  fprintf(output, "XSTATE_MODEL neo=%d\n", sceKernelHasNeoMode());
#endif
  uint32_t value[4];
  Cpuid(1, 0, value);
  if ((value[2] & 0x1c000000) != 0x1c000000) {
    fprintf(output, "XSTATE_UNSUPPORTED features=%08x\n", value[2]);
    fclose(output);
    return 1;
  }
  uint32_t low, high;
  asm volatile("xgetbv" : "=a"(low), "=d"(high) : "c"(0));
  const uint64_t xcr0 = (uint64_t{high} << 32) | low;
  fprintf(output, "XSTATE_XCR0 value=%016llx\n",
          static_cast<unsigned long long>(xcr0));
  if ((xcr0 & 7) != 7)
    return 2;
  Cpuid(0xd, 0, value);
  const unsigned capacity = value[2];
  if (capacity < 832 || capacity > 65536)
    return 3;
  bool optimized = false;
  for (unsigned subleaf = 0; subleaf < 64; ++subleaf) {
    Cpuid(0xd, subleaf, value);
    fprintf(output,
            "XSTATE_CPUID subleaf=%u eax=%08x ebx=%08x ecx=%08x edx=%08x\n",
            subleaf, value[0], value[1], value[2], value[3]);
    if (subleaf == 1)
      optimized = (value[0] & 1) != 0;
  }
  for (unsigned i = 0; i < 128; ++i)
    patterns[i] = 0x3f000000 + i * 0x10203;
#ifdef XSTATE_ORBIS
  const int signals[]{4, 10, 11};
#else
  const int signals[]{SIGILL, SIGBUS, SIGSEGV};
  struct sigaction previous[3]{};
#endif
  for (unsigned i = 0; i < 3; ++i) {
#ifdef XSTATE_ORBIS
    const int status = sceKernelInstallExceptionHandler(signals[i], Fault);
    if (status) {
      fprintf(output, "XSTATE_ERROR install_signal=%d status=%08x\n",
              signals[i], status);
      return 4;
    }
#else
    struct sigaction action{};
    action.sa_sigaction = HostFault;
    action.sa_flags = SA_SIGINFO;
    sigemptyset(&action.sa_mask);
    if (sigaction(signals[i], &action, &previous[i]))
      return 4;
#endif
  }
  auto *generated = static_cast<unsigned char *>(Allocate(16384));
  auto *area = static_cast<unsigned char *>(Allocate(81920));
  auto *guard = static_cast<unsigned char *>(Allocate(32768));
  alignas(64) unsigned char valid[832]{};
  if (!generated || !area || !guard)
    return 5;
  for (unsigned i = 0; i < 7; ++i) {
    const auto size = size_t(cases[i].end - cases[i].start);
    if (size > 1024)
      return 6;
    memcpy(generated + i * 1024, cases[i].start, size);
  }
  if (Protect(generated, 16384, 5))
    return 7;
  const uint64_t masks[]{0, 1, 2, 3, 4, 7, uint64_t{1} << 63, ~uint64_t{0}};
  const uint64_t indexes[]{0, 1, 2, 0xffffffff};
  const unsigned boundary_ops[]{0, 1, 4, 5};
  const unsigned offsets[]{832, 576};
  const uint64_t boundary_masks[]{3, 7};
  for (unsigned mode = 0; mode < 2; ++mode) {
    auto *code = mode ? generated : nullptr;
    const char *label = mode ? "generated" : "static";
    for (uint64_t index : indexes) {
      Invoke(6, code, label, "index", nullptr, index, false);
      rows++;
    }
    for (unsigned op = 0; op < 4; ++op) {
      if (op >= 2 && !optimized)
        continue;
      for (unsigned dirty = 0; dirty < 2; ++dirty) {
        for (uint64_t mask : masks) {
          memset(area, 0xa5, 81920);
          memset(area + 512, 0, 64);
          const int fault = Invoke(op, code, label, "save", area, mask, dirty);
          failures += fault != 0;
          Dump("XSTATE_AREA", area, 832);
          unsigned beyond = 0, maximum = 0;
          for (unsigned i = 832; i < 81920; ++i) {
            if (area[i] != 0xa5) {
              beyond++;
              maximum = i + 1;
            }
          }
          failures += maximum > capacity;
          fprintf(output,
                  "XSTATE_EXTENT row=%u beyond832=%u maximum=%u capacity=%u\n",
                  rows, beyond, maximum, capacity);
          if (op == 1 && dirty && mask == 7 && !fault)
            memcpy(valid, area, 832);
          rows++;
        }
      }
    }
    for (unsigned op = 4; op < 6; ++op) {
      for (uint64_t mask : masks) {
        if (mask == ~uint64_t{0} && xcr0 != 7)
          continue;
        memset(area, 0, 81920);
        memcpy(area, valid, 832);
        Invoke(op, code, label, "restore", area, mask, false);
        rows++;
      }
      const char *names[]{"init_bv",         "unsupported_bv", "compact_bv",
                          "reserved_header", "invalid_mxcsr",  "misaligned"};
      for (unsigned variant = 0; variant < 6; ++variant) {
        memset(area, 0, 81920);
        memcpy(area, valid, 832);
        if (variant == 0)
          memset(area + 512, 0, 8);
        if (variant == 1)
          area[513] |= 2;
        if (variant == 2)
          area[527] |= 0x80;
        if (variant == 3)
          area[528] = 1;
        if (variant == 4)
          area[27] |= 0x80;
        Invoke(op, code, label, names[variant], area + (variant == 5), 7, true);
        rows++;
      }
    }
    for (unsigned op : boundary_ops) {
      for (unsigned offset : offsets) {
        for (uint64_t mask : boundary_masks) {
          memset(guard, 0, 32768);
          unsigned char *target = guard + 16384 - offset;
          memcpy(target, valid, 832);
          if (Protect(guard + 16384, 16384, 0))
            return 8;
          Invoke(op, code, label, offset == 832 ? "boundary" : "guard", target,
                 mask, true);
          if (Protect(guard + 16384, 16384, 3))
            return 8;
          Dump("XSTATE_AREA", target, 832);
          rows++;
        }
      }
    }
  }
  failures += Release(generated, 16384) != 0;
  failures += Release(area, 81920) != 0;
  failures += Release(guard, 32768) != 0;
  for (unsigned i = 0; i < 3; ++i) {
#ifdef XSTATE_ORBIS
    failures += sceKernelRemoveExceptionHandler(signals[i]) != 0;
#else
    failures += sigaction(signals[i], &previous[i], nullptr) != 0;
#endif
  }
  fprintf(output,
          "XSTATE_END rows=%u collection_errors=%u unexpected_fault=%d\n", rows,
          failures, int(unexpected_fault));
  failures += fclose(output) != 0;
#ifdef XSTATE_ORBIS
  sceSystemServiceLoadExec("EXIT", nullptr);
#endif
  return failures ? 1 : 0;
}
