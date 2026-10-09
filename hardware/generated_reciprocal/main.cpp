// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#ifdef RECIPROCAL_GENERATED
#include <signal.h>
#endif

#if defined(RECIPROCAL_GENERATED) && !defined(RECIPROCAL_ORBIS)
#include <sys/mman.h>
#endif

#ifdef RECIPROCAL_ORBIS
extern "C" int sceKernelMmap(void*, uint64_t, int, int, int, int64_t, void**);
extern "C" int sceKernelMprotect(const void*, uint64_t, int);
extern "C" int sceKernelMunmap(void*, uint64_t);
extern "C" int sceSystemServiceLoadExec(const char *, const char *const *);
#endif

struct Result {
  uint32_t lanes[8];
  uint32_t mxcsr;
  uint32_t padding;
  uint64_t flags_before;
  uint64_t flags_after;
};
static_assert(offsetof(Result, flags_before) == 40);
static_assert(offsetof(Result, flags_after) == 48);

using Probe = void (*)(const uint32_t *, const uint32_t *, Result *);
#ifdef RECIPROCAL_GENERATED
#define DECLARE(name) extern "C" const unsigned char name[], name##_end[]
#else
#define DECLARE(name) extern "C" void name(const uint32_t *, const uint32_t *, Result *)
#endif
DECLARE(rcp_ss);
DECLARE(rcp_ss_mem);
DECLARE(rcp_vss);
DECLARE(rcp_vss_mem);
DECLARE(rcp_ps);
DECLARE(rcp_vps);
DECLARE(rcp_vps256);
DECLARE(rsqrt_ss);
DECLARE(rsqrt_ss_mem);
DECLARE(rsqrt_vss);
DECLARE(rsqrt_vss_mem);
DECLARE(rsqrt_ps);
DECLARE(rsqrt_vps);
DECLARE(rsqrt_vps256);
#undef DECLARE

struct Case {
  const char *name;
#ifdef RECIPROCAL_GENERATED
  const unsigned char *code, *end;
#else
  Probe run;
#endif
  unsigned computed_lanes;
  bool vex;
};
#ifdef RECIPROCAL_GENERATED
#define ENTRY(name) name, name##_end
#else
#define ENTRY(name) name
#endif
static const Case cases[] = {
    {"rcpss", ENTRY(rcp_ss), 1, false},
    {"rcpss_mem", ENTRY(rcp_ss_mem), 1, false},
    {"vrcpss", ENTRY(rcp_vss), 1, true},
    {"vrcpss_mem", ENTRY(rcp_vss_mem), 1, true},
    {"rcpps", ENTRY(rcp_ps), 4, false},
    {"vrcpps", ENTRY(rcp_vps), 4, true},
    {"vrcpps256", ENTRY(rcp_vps256), 8, true},
    {"rsqrtss", ENTRY(rsqrt_ss), 1, false},
    {"rsqrtss_mem", ENTRY(rsqrt_ss_mem), 1, false},
    {"vrsqrtss", ENTRY(rsqrt_vss), 1, true},
    {"vrsqrtss_mem", ENTRY(rsqrt_vss_mem), 1, true},
    {"rsqrtps", ENTRY(rsqrt_ps), 4, false},
    {"vrsqrtps", ENTRY(rsqrt_vps), 4, true},
    {"vrsqrtps256", ENTRY(rsqrt_vps256), 8, true},
};

static uint32_t ReadMxcsr() {
  uint32_t value;
  asm volatile("stmxcsr %0" : "=m"(value));
  return value;
}

static void WriteMxcsr(uint32_t value) {
  asm volatile("ldmxcsr %0" : : "m"(value) : "memory");
}

static bool DescribeCpu(FILE *output) {
  uint32_t a, b, c, d;
  asm volatile("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(0), "c"(0));
  char vendor[13]{};
  memcpy(vendor, &b, 4);
  memcpy(vendor + 4, &d, 4);
  memcpy(vendor + 8, &c, 4);
  asm volatile("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(1), "c"(0));
  fprintf(output, "CPU vendor=%s signature=%08x ecx=%08x edx=%08x\n", vendor, a,
          c, d);
  if ((c & 0x1c000000) != 0x1c000000) {
    return false;
  }
  asm volatile("xgetbv" : "=a"(a), "=d"(d) : "c"(0));
  fprintf(output, "XCR0=%08x%08x\n", d, a);
  return (a & 6) == 6;
}

#ifdef RECIPROCAL_GENERATED
static void *fault_page;
static unsigned recovered_faults;
static int Protect(void *address, unsigned size, int flags) {
#ifdef RECIPROCAL_ORBIS
  return sceKernelMprotect(address, size, flags);
#else
  return mprotect(address, size, flags);
#endif
}
static void RecoverFault(int) {
  ++recovered_faults;
  Protect(fault_page, 16384, 3);
}
static unsigned CheckMemory(Probe *runners, FILE *output) {
  void *memory = nullptr;
#ifdef RECIPROCAL_ORBIS
  if (sceKernelMmap(reinterpret_cast<void *>(0x10000000000ULL), 32768, 3, 0x1002, -1, 0, &memory) != 0) return 1;
  if (reinterpret_cast<uintptr_t>(memory) < 0x10000000000ULL) return 1;
#else
  memory = mmap(nullptr, 32768, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (memory == MAP_FAILED) return 1;
#endif
  auto *edge = reinterpret_cast<uint32_t *>(static_cast<char *>(memory) + 16384 - 4);
  fault_page = static_cast<char *>(memory) + 16384;
  *edge = 0x40400000;
  *static_cast<uint32_t *>(fault_page) = 0x40400000;
  const auto previous = signal(SIGSEGV, RecoverFault);
  const uint32_t merge[8] = {1,2,3,4,5,6,7,8};
  unsigned failures = 0;
  const unsigned forms[] = {1, 3, 8, 10};
  for (unsigned operation : forms) {
    const uint32_t expected = operation < 7 ? 0x3eaaa800 : 0x3f13c800;
    Result result{};
    if (Protect(fault_page, 16384, 0) != 0) { ++failures; break; }
    const unsigned before = recovered_faults;
    runners[operation](edge, merge, &result);
    const uint32_t boundary_value = result.lanes[0];
    const unsigned boundary_faults = recovered_faults - before;
    failures += boundary_value != expected || boundary_faults != 0;
    runners[operation](static_cast<uint32_t *>(fault_page), merge, &result);
    failures += result.lanes[0] != expected || recovered_faults != before + 1;
    failures += result.flags_before != result.flags_after;
    fprintf(output, "RECIPROCAL_MEMORY op=%s boundary=%08x boundary_faults=%u value=%08x recovered=%u flags=%llx/%llx failures=%u\n",
            cases[operation].name, boundary_value, boundary_faults, result.lanes[0], recovered_faults,
            static_cast<unsigned long long>(result.flags_before), static_cast<unsigned long long>(result.flags_after), failures);
    fflush(output);
  }
  signal(SIGSEGV, previous);
#ifndef RECIPROCAL_ORBIS
  munmap(memory, 32768);
#else
  // The emulator baseline misregisters coalesced CPU-only unmaps with its GPU tracker.
  // Keep these 32 KiB until process exit; all four protected accesses are still tested.
#endif
  return failures;
}
#endif

int main(int argc, char **argv) {
#ifdef RECIPROCAL_ORBIS
  const char *path = "/data/reciprocal-hardware.txt";
  (void)argc;
  (void)argv;
#else
  const char *path = argc > 1 ? argv[1] : "reciprocal-hardware.txt";
#endif
  FILE *output = fopen(path, "w");
  if (!output) {
    perror("reciprocal output");
    return 1;
  }
  puts("RECIPROCAL_BEGIN format=1");
  fprintf(output, "RECIPROCAL_BEGIN format=1\n");
  if (!DescribeCpu(output)) {
    fprintf(output, "RECIPROCAL_ERROR AVX unavailable\n");
    fclose(output);
    return 1;
  }
  Probe runners[sizeof(cases) / sizeof(cases[0])]{};
#ifdef RECIPROCAL_GENERATED
  void *code = nullptr;
#ifdef RECIPROCAL_ORBIS
  if (sceKernelMmap(nullptr, 16384, 3, 0x1002, -1, 0, &code) != 0) return 1;
#else
  code = mmap(nullptr, 16384, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (code == MAP_FAILED) return 1;
#endif
#endif
  for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
#ifdef RECIPROCAL_GENERATED
    const size_t size = cases[i].end - cases[i].code;
    if (size > 256) return 1;
    auto *destination = static_cast<unsigned char *>(code) + i * 256;
    memcpy(destination, cases[i].code, size);
    runners[i] = reinterpret_cast<Probe>(destination);
#else
    runners[i] = cases[i].run;
#endif
  }
#ifdef RECIPROCAL_GENERATED
#ifdef RECIPROCAL_ORBIS
  if (sceKernelMprotect(code, 16384, 5) != 0) return 1;
#else
  if (mprotect(code, 16384, PROT_READ | PROT_EXEC) != 0) return 1;
#endif
  fprintf(output, "RECIPROCAL_MODE generated\n");
#endif
  const uint32_t saved_mxcsr = ReadMxcsr();
  const uint32_t merge[8] = {0x41200000, 0x42200001, 0x43200002, 0x44200003,
                             0x45200004, 0x46200005, 0x47200006, 0x48200007};
  const uint32_t edges[] = {
      0,          0x80000000, 1,          0x80000001, 0x007fffff, 0x807fffff,
      0x00800000, 0x80800000, 0x00800001, 0x80800001, 0x3f7fffff, 0x3f800000,
      0x3f800001, 0xbf800000, 0x3fffffff, 0x40000000, 0x40000001, 0x40400000,
      0x40800000, 0x7f7fffff, 0xff7fffff, 0x7f800000, 0xff800000, 0x7f800001,
      0xff800001, 0x7fbfffff, 0xffbfffff, 0x7fc00000, 0xffc00000, 0x7fc12345,
      0xffc12345, 0x7fffffff,
  };
  uint32_t values[sizeof(edges) / sizeof(edges[0]) + 512];
  memcpy(values, edges, sizeof(edges));
  unsigned count = sizeof(edges) / sizeof(edges[0]);
  uint32_t random = 0x194abc23;
  for (unsigned exponent = 0; exponent < 256; ++exponent) {
    random = random * 1664525 + 1013904223;
    const uint32_t positive = (exponent << 23) | (random & 0x7fffff);
    values[count++] = positive;
    values[count++] = positive | 0x80000000;
  }
  unsigned rows = 0, failures = 0, scalar_packed_differences = 0;
  for (unsigned setting = 0; setting < 16; ++setting) {
    const uint32_t mxcsr = 0x1f80 | ((setting & 3) << 13) |
                           ((setting & 4) << 4) | ((setting & 8) << 12);
    for (unsigned index = 0; index < count; ++index) {
      uint32_t input[8];
      for (auto &lane : input) {
        lane = values[index];
      }
      uint32_t scalar[2]{};
      for (unsigned operation = 0; operation < sizeof(cases) / sizeof(cases[0]);
           ++operation) {
        const auto &test = cases[operation];
        Result result{};
        WriteMxcsr(mxcsr);
        runners[operation](input, merge, &result);
        WriteMxcsr(saved_mxcsr);
        unsigned errors = result.flags_before != result.flags_after ? 1 : 0;
        errors += result.mxcsr != mxcsr;
        for (unsigned lane = test.computed_lanes; lane < 8; ++lane) {
          const uint32_t expected = test.vex && lane >= 4 ? 0 : merge[lane];
          errors += result.lanes[lane] != expected;
        }
        failures += errors;
        if (operation % 7 == 0) {
          scalar[operation / 7] = result.lanes[0];
        } else if (operation % 7 == 4) {
          scalar_packed_differences += result.lanes[0] != scalar[operation / 7];
        }
        fprintf(output, "RAW op=%s in=%08x mxcsr_in=%08x mxcsr_out=%08x out=",
                test.name, input[0], mxcsr, result.mxcsr);
        for (unsigned lane = 0; lane < 8; ++lane) {
          fprintf(output, "%s%08x", lane ? "," : "", result.lanes[lane]);
        }
        fprintf(output, " flags=%llx/%llx errors=%u\n",
                static_cast<unsigned long long>(result.flags_before),
                static_cast<unsigned long long>(result.flags_after), errors);
        ++rows;
      }
    }
    fflush(output);
    printf("RECIPROCAL_PROGRESS settings=%u/16\n", setting + 1);
  }
  WriteMxcsr(saved_mxcsr);
#ifdef RECIPROCAL_GENERATED
  failures += CheckMemory(runners, output);
#endif
  fprintf(
      output,
      "RECIPROCAL_END rows=%u state_errors=%u scalar_packed_differences=%u\n",
      rows, failures, scalar_packed_differences);
  const bool write_failed = ferror(output) != 0;
  const int close_result = fclose(output);
  printf("RECIPROCAL_END rows=%u state_errors=%u scalar_packed_differences=%u "
         "file_ok=%u\n",
         rows, failures, scalar_packed_differences,
         !write_failed && close_result == 0);
#ifdef RECIPROCAL_GENERATED
#ifdef RECIPROCAL_ORBIS
  sceKernelMunmap(code, 16384);
#else
  munmap(code, 16384);
#endif
#endif
#ifdef RECIPROCAL_ORBIS
  sceSystemServiceLoadExec("EXIT", nullptr);
#endif
  return failures == 0 && !write_failed && close_result == 0 ? 0 : 1;
}
