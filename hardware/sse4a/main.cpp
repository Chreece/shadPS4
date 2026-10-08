// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern "C" {
int sceKernelMmap(void*, uint64_t, int, int, int, int64_t, void**);
int sceKernelMprotect(const void*, uint64_t, int);
int sceKernelMunmap(void*, uint64_t);
int sceSystemServiceLoadExec(const char*, const char* const*);
}

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
using Execute = void (*)(const Input*, Output*, void*);
#define DECLARE(name)                                                                              \
    extern "C" void name(const Input*, Output*, void*), name##_end(), raw_##name(),                \
        raw_##name##_end();
DECLARE(extrq_reg)
DECLARE(extrq_imm)
DECLARE(insertq_reg)
DECLARE(insertq_imm)
DECLARE(extrq_high)
DECLARE(extrq_imm_high)
DECLARE(insertq_high)
DECLARE(insertq_imm_high)
DECLARE(movntss)
DECLARE(movntsd)
DECLARE(movntss_high)
DECLARE(movntsd_high)

struct Case {
    const char* name;
    Execute start;
    void (*raw)();
    void (*end)();
    bool insert, immediate;
    unsigned store_bytes, store_offset;
};
#define CASE(name, ...) {#name, name, raw_##name, raw_##name##_end, __VA_ARGS__}
static const Case cases[]{
    CASE(extrq_reg, false, false, 0, 0),    CASE(extrq_imm, false, true, 0, 0),
    CASE(insertq_reg, true, false, 0, 0),   CASE(insertq_imm, true, true, 0, 0),
    CASE(extrq_high, false, false, 0, 0),   CASE(extrq_imm_high, false, true, 0, 0),
    CASE(insertq_high, true, false, 0, 0),  CASE(insertq_imm_high, true, true, 0, 0),
    CASE(movntss, false, false, 4, 0),      CASE(movntsd, false, false, 8, 0),
    CASE(movntss_high, false, false, 4, 8), CASE(movntsd_high, false, false, 8, 8),
};

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    auto* selection = fopen("/data/sse4a-case.txt", "r");
    unsigned which = 0, generated = 0;
    if (!selection || fscanf(selection, "%u %u", &which, &generated) != 2 ||
        which >= sizeof(cases) / sizeof(cases[0]) || generated > 1) {
        printf("SSE4A_ERROR invalid_selection\n");
        return 1;
    }
    fclose(selection);
    const auto& test = cases[which];
    auto* log = fopen("/data/sse4a-result.txt", "w");
    if (!log) {
        return 1;
    }
    setvbuf(log, nullptr, _IONBF, 0);
    uint32_t a, b, c, d;
    asm volatile("cpuid" : "=a"(a), "=b"(b), "=c"(c), "=d"(d) : "a"(0x80000001), "c"(0));
    fprintf(log, "SSE4A_BEGIN case=%s generated=%u advertised=%u\n", test.name, generated,
            (c >> 6) & 1);
    Execute run = test.start;
    void* memory = nullptr;
    if (generated) {
        const auto size =
            reinterpret_cast<uintptr_t>(test.end) - reinterpret_cast<uintptr_t>(test.raw);
        if (size > 16384 || sceKernelMmap(nullptr, 16384, 3, 0x1002, -1, 0, &memory) != 0) {
            fprintf(log, "SSE4A_ERROR allocation\n");
            return 1;
        }
        memcpy(memory, reinterpret_cast<void*>(test.raw), size);
        if (sceKernelMprotect(memory, 16384, 5) != 0) {
            fprintf(log, "SSE4A_ERROR protection\n");
            return 1;
        }
        run = reinterpret_cast<Execute>(memory);
    }
    unsigned checks = 0, failures = 0;
    constexpr unsigned lengths[]{0, 1, 8, 16, 32, 63};
    constexpr unsigned indices[]{0, 1, 8, 32, 63};
    for (unsigned length : lengths) {
        for (unsigned index : indices) {
            if ((length ? length : 64) + index > 64) {
                continue;
            }
            if ((test.immediate || test.store_bytes) && (length != 16 || index != 8)) {
                continue;
            }
            Input input{
                {0xfedcba9876543210, 0x9988776655443322, 0x1234432112344321, 0xabcdefabcdefabcd},
                {0x8765432101234567, 0xaabbccddeeff0011, 0x5678876556788765, 0x13579bdf13579bdf}};
            const uint64_t control = (length | 0xc0u) | ((index | 0xc0u) << 8);
            if (!test.immediate && !test.store_bytes) {
                (test.insert ? input.src.high : input.src.low) = control;
            }
            alignas(16) unsigned char target[32], expected_target[32];
            memset(target, 0xa5, sizeof(target));
            memcpy(expected_target, target, sizeof(target));
            Output output{};
            run(&input, &output, target);
            const uint64_t mask = length ? (uint64_t{1} << length) - 1 : UINT64_MAX;
            uint64_t expected =
                test.insert ? (input.dst.low & ~(mask << index)) | ((input.src.low & mask) << index)
                            : (input.dst.low >> index) & mask;
            if (test.store_bytes) {
                expected = input.dst.low;
                memcpy(expected_target + test.store_offset, &input.dst.low, test.store_bytes);
            }
            const bool value_ok = output.dst.low == expected;
            const bool upper_ok = output.dst.upper_low == input.dst.upper_low &&
                                  output.dst.upper_high == input.dst.upper_high;
            const bool src_ok = memcmp(&input.src, &output.src, sizeof(Vector)) == 0;
            const bool flags_ok = (output.flags & 0x8d5) == 0x8d5;
            const bool regs_ok = output.rax == 0x123456789abcdef0 &&
                                 output.rcx == 0x23456789abcdef01 &&
                                 output.rbx == 0x3456789abcdef012 &&
                                 output.rdx == reinterpret_cast<uintptr_t>(target);
            const bool memory_ok = memcmp(target, expected_target, sizeof(target)) == 0;
            const bool ok = value_ok && upper_ok && src_ok && flags_ok && regs_ok && memory_ok &&
                            output.redzone == 0;
            fprintf(log,
                    "SSE4A_CHECK length=%u index=%u value=%u upper=%u source=%u flags=%u "
                    "registers=%u memory=%u redzone=%u ok=%u\n",
                    length, index, value_ok, upper_ok, src_ok, flags_ok, regs_ok, memory_ok,
                    output.redzone == 0, ok);
            ++checks;
            failures += !ok;
        }
    }
    fprintf(log, "SSE4A_END checks=%u failures=%u\n", checks, failures);
    fclose(log);
    if (memory) {
        sceKernelMunmap(memory, 16384);
    }
    sceSystemServiceLoadExec("EXIT", nullptr);
    return failures != 0;
}
