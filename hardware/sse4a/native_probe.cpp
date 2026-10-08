// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cpuid.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct Vector {
  uint64_t low, high, upper_low, upper_high;
};
struct Input {
  Vector dst, src;
};
struct Output {
  Vector dst, src;
  uint64_t flags, rax, rcx, rbx, rdx, redzone;
};
static_assert(offsetof(Output, flags) == 64 &&
              offsetof(Output, redzone) == 104);
using Execute = void (*)(const Input *, Output *, void *);
#define DECLARE(name) extern "C" void name(const Input *, Output *, void *);
DECLARE(extrq_reg)
DECLARE(extrq_imm)
DECLARE(insertq_reg)
DECLARE(insertq_imm)
DECLARE(extrq_high)
DECLARE(extrq_imm_high)
DECLARE(insertq_high)
DECLARE(insertq_imm_high)

static int RunProbe(FILE *stream) {
  unsigned a, b, c, d;
  __cpuid(0, a, b, c, d);
  char vendor[13]{};
  memcpy(vendor, &b, 4);
  memcpy(vendor + 4, &d, 4);
  memcpy(vendor + 8, &c, 4);
  __cpuid(1, a, b, c, d);
  fprintf(stream, "CPU vendor=%s signature=%08x hypervisor=%u\n", vendor, a,
          c >> 31);
  if (__get_cpuid_max(0x80000000, nullptr) >= 0x80000004) {
    char brand[49]{};
    for (unsigned i = 0; i < 3; ++i) {
      unsigned registers[4];
      __cpuid(0x80000002 + i, registers[0], registers[1], registers[2],
              registers[3]);
      memcpy(brand + 16 * i, registers, sizeof(registers));
    }
    fprintf(stream, "CPU brand=%s\n", brand);
    __cpuid(1, a, b, c, d);
  }
  if ((c & (3u << 27)) != (3u << 27) ||
      __get_cpuid_max(0x80000000, nullptr) < 0x80000001) {
    fputs("SKIP: AVX/OSXSAVE or extended CPUID unavailable\n", stream);
    return 77;
  }
  __cpuid(0x80000001, a, b, c, d);
  if (!(c & (1u << 6))) {
    fputs("SKIP: native SSE4a unavailable\n", stream);
    return 77;
  }
  asm volatile("xgetbv" : "=a"(a), "=d"(d) : "c"(0));
  if ((a & 6) != 6) {
    fputs("SKIP: XMM/YMM state unavailable\n", stream);
    return 77;
  }
  struct Case {
    const char *name;
    Execute run;
    bool insert, immediate;
  };
  const Case cases[]{
      {"extrq_reg", extrq_reg, false, false},
      {"extrq_imm", extrq_imm, false, true},
      {"insertq_reg", insertq_reg, true, false},
      {"insertq_imm", insertq_imm, true, true},
      {"extrq_high", extrq_high, false, false},
      {"extrq_imm_high", extrq_imm_high, false, true},
      {"insertq_high", insertq_high, true, false},
      {"insertq_imm_high", insertq_imm_high, true, true},
  };
  unsigned checks = 0, failures = 0;
  constexpr unsigned lengths[]{0, 1, 8, 16, 32, 63};
  constexpr unsigned indices[]{0, 1, 8, 32, 63};
  for (const auto &test : cases) {
    unsigned samples = 0, xmm_high_zero = 0, ymm_high_preserved = 0,
             ymm_high_zero = 0;
    for (const unsigned length : lengths) {
      for (const unsigned index : indices) {
        if ((length ? length : 64) + index > 64 ||
            (test.immediate && (length != 16 || index != 8))) {
          continue;
        }
        Input input{{0xfedcba9876543210, 0x9988776655443322, 0x1234432112344321,
                     0xabcdefabcdefabcd},
                    {0x8765432101234567, 0xaabbccddeeff0011, 0x5678876556788765,
                     0x13579bdf13579bdf}};
        if (!test.immediate) {
          (test.insert ? input.src.high : input.src.low) =
              (length | 0xc0u) | ((index | 0xc0u) << 8);
        }
        alignas(16) unsigned char target[32]{};
        Output output{};
        test.run(&input, &output, target);
        const uint64_t mask = length ? (uint64_t{1} << length) - 1 : UINT64_MAX;
        const uint64_t expected = test.insert
                                      ? (input.dst.low & ~(mask << index)) |
                                            ((input.src.low & mask) << index)
                                      : (input.dst.low >> index) & mask;
        const bool ok = output.dst.low == expected &&
                        memcmp(&input.src, &output.src, sizeof(Vector)) == 0 &&
                        (output.flags & 0x8d5) == 0x8d5 &&
                        output.redzone == 0 &&
                        output.rax == 0x123456789abcdef0 &&
                        output.rcx == 0x23456789abcdef01 &&
                        output.rbx == 0x3456789abcdef012 &&
                        output.rdx == reinterpret_cast<uintptr_t>(target);
        ++checks;
        ++samples;
        failures += !ok;
        xmm_high_zero += output.dst.high == 0;
        ymm_high_preserved += output.dst.upper_low == input.dst.upper_low &&
                              output.dst.upper_high == input.dst.upper_high;
        ymm_high_zero +=
            output.dst.upper_low == 0 && output.dst.upper_high == 0;
        fprintf(stream,
                "RAW case=%s length=%u index=%u dst_255_192=%016lx "
                "dst_191_128=%016lx dst_127_64=%016lx dst_63_0=%016lx "
                "defined_ok=%u\n",
                test.name, length, index, output.dst.upper_high,
                output.dst.upper_low, output.dst.high, output.dst.low, ok);
      }
    }
    fprintf(
        stream,
        "CASE name=%s samples=%u xmm_127_64_zero=%u ymm_255_128_preserved=%u "
        "ymm_255_128_zero=%u\n",
        test.name, samples, xmm_high_zero, ymm_high_preserved, ymm_high_zero);
  }
  fprintf(stream, "NATIVE_END checks=%u defined_state_failures=%u\n", checks,
          failures);
  return failures != 0;
}

#ifdef SSE4A_ORBIS
extern "C" int sceSystemServiceLoadExec(const char *, const char *const *);

int main() {
  FILE *stream = fopen("/data/sse4a-hardware-result.txt", "w");
  if (!stream) {
    puts("SSE4A_HARDWARE_ERROR: cannot open /data/sse4a-hardware-result.txt");
    sceSystemServiceLoadExec("EXIT", nullptr);
    return 1;
  }
  setvbuf(stream, nullptr, _IONBF, 0);
  fputs("SSE4A_HARDWARE_BEGIN format=1\n", stream);
  const int result = RunProbe(stream);
  fprintf(stream, "SSE4A_HARDWARE_END result=%d\n", result);
  fclose(stream);
  sceSystemServiceLoadExec("EXIT", nullptr);
  return result;
}
#else
int main() { return RunProbe(stdout); }
#endif
