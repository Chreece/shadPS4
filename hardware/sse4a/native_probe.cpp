// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cpuid.h>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>

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

int main() {
  unsigned a, b, c, d;
  __cpuid(0, a, b, c, d);
  char vendor[13]{};
  memcpy(vendor, &b, 4);
  memcpy(vendor + 4, &d, 4);
  memcpy(vendor + 8, &c, 4);
  __cpuid(1, a, b, c, d);
  printf("CPU vendor=%s signature=%08x hypervisor=%u\n", vendor, a, c >> 31);
  if (__get_cpuid_max(0x80000000, nullptr) >= 0x80000004) {
    char brand[49]{};
    for (unsigned i = 0; i < 3; ++i) {
      unsigned registers[4];
      __cpuid(0x80000002 + i, registers[0], registers[1], registers[2],
              registers[3]);
      memcpy(brand + 16 * i, registers, sizeof(registers));
    }
    printf("CPU brand=%s\n", brand);
    __cpuid(1, a, b, c, d);
  }
  if ((c & (3u << 27)) != (3u << 27) ||
      __get_cpuid_max(0x80000000, nullptr) < 0x80000001) {
    puts("SKIP: AVX/OSXSAVE or extended CPUID unavailable");
    return 77;
  }
  __cpuid(0x80000001, a, b, c, d);
  if (!(c & (1u << 6))) {
    puts("SKIP: native SSE4a unavailable");
    return 77;
  }
  asm volatile("xgetbv" : "=a"(a), "=d"(d) : "c"(0));
  if ((a & 6) != 6) {
    puts("SKIP: XMM/YMM state unavailable");
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
  for (const auto &test : cases) {
    unsigned samples = 0, xmm_high_zero = 0, ymm_high_preserved = 0,
             ymm_high_zero = 0;
    for (const unsigned length : {0u, 1u, 8u, 16u, 32u, 63u}) {
      for (const unsigned index : {0u, 1u, 8u, 32u, 63u}) {
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
        printf("RAW case=%s length=%u index=%u dst_255_192=%016lx "
               "dst_191_128=%016lx dst_127_64=%016lx dst_63_0=%016lx "
               "defined_ok=%u\n",
               test.name, length, index, output.dst.upper_high,
               output.dst.upper_low, output.dst.high, output.dst.low, ok);
      }
    }
    printf(
        "CASE name=%s samples=%u xmm_127_64_zero=%u ymm_255_128_preserved=%u "
        "ymm_255_128_zero=%u\n",
        test.name, samples, xmm_high_zero, ymm_high_preserved, ymm_high_zero);
  }
  printf("NATIVE_END checks=%u defined_state_failures=%u\n", checks, failures);
  return failures != 0;
}
