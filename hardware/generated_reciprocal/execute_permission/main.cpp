// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern "C" int sceKernelMmap(void*, uint64_t, int, int, int, int64_t, void**);
extern "C" int sceKernelMprotect(const void*, uint64_t, int);
extern "C" int sceKernelMunmap(void*, uint64_t);
extern "C" int sceSystemServiceLoadExec(const char*, const char* const*);

static void* base;
static volatile sig_atomic_t faults;
static volatile sig_atomic_t recovery_error;

static void Recover(int) {
    faults = faults + 1;
    recovery_error = sceKernelMprotect(base, 32768, 7);
}

static unsigned Check(FILE* output, uintptr_t hint, const char* region) {
    if (sceKernelMmap(reinterpret_cast<void*>(hint), 32768, 3, 0x1002, -1, 0, &base))
        return 1;
    auto* bytes = static_cast<unsigned char*>(base);
    const unsigned char code[] = {0xb8, 0x78, 0x56, 0x34, 0x12, 0xc3};
    memcpy(bytes, code, sizeof(code));
    unsigned failures = 0;
    const char* names[] = {"first_nx", "cached_rw", "cached_r", "cached_none",
                           "cross_first", "cross_cached", "fallthrough", "branch_untaken"};
    for (unsigned i = 0; i != 8; ++i) {
        auto* entry = bytes;
        unsigned expected_faults = 1;
        if (i < 4) {
            if (sceKernelMprotect(base, 16384, i == 2 ? 1 : i == 3 ? 0 : 3)) return 1;
        } else {
            if (sceKernelMprotect(base, 32768, 7)) return 1;
            if (i == 4 || i == 5) {
                entry = bytes + 16382;
                memcpy(entry, code, sizeof(code));
            } else if (i == 6) {
                entry = bytes + 16383;
                *entry = 0x90;
                memcpy(entry + 1, code, sizeof(code));
            } else {
                entry = bytes + 16371;
                const unsigned char branch[] = {0xb8, 0x78, 0x56, 0x34, 0x12,
                                                 0x85, 0xc0, 0x74, 0x04, 0xc3};
                memcpy(entry, branch, sizeof(branch));
                memcpy(bytes + 16384, code, sizeof(code));
                expected_faults = 0;
            }
            if (i == 5 && reinterpret_cast<uint32_t (*)()>(entry)() != 0x12345678)
                return 1;
            if (sceKernelMprotect(bytes + 16384, 16384, 3)) return 1;
        }
        const unsigned before = faults;
        const auto value = reinterpret_cast<uint32_t (*)()>(entry)();
        const unsigned actual = unsigned(faults) - before;
        failures += value != 0x12345678 || actual != expected_faults || recovery_error != 0;
        fprintf(output, "EXECUTE region=%s case=%s faults=%u expected=%u value=%08x failures=%u\n",
                region, names[i], actual, expected_faults, value, failures);
    }
    const int result = sceKernelMunmap(base, 32768);
    failures += result != 0;
    fprintf(output, "EXECUTE_UNMAP region=%s result=%d\n", region, result);
    return failures;
}

int main() {
    FILE* output = fopen("/data/execute-permission.txt", "w");
    if (!output) return 1;
    setvbuf(output, nullptr, _IONBF, 0);
    fprintf(output, "EXECUTE_BEGIN\n");
    const auto previous = signal(SIGSEGV, Recover);
    if (previous == SIG_ERR) return 2;
    unsigned failures = Check(output, 0, "low");
    failures += Check(output, 0x10000000000ULL, "high");
    signal(SIGSEGV, previous);
    fprintf(output, "EXECUTE_END recovered=%u failures=%u\n", unsigned(faults), failures);
    fclose(output);
    sceSystemServiceLoadExec("EXIT", nullptr);
    return failures ? 1 : 0;
}
