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

static void* fault_page;
static volatile sig_atomic_t recovered;
static volatile sig_atomic_t recovery_error;

static void Recover(int) {
    recovered = recovered + 1;
    recovery_error = sceKernelMprotect(fault_page, 16384, 7);
}

static unsigned CheckAccess(FILE* output, uintptr_t hint, const char* region) {
    void* memory = nullptr;
    if (sceKernelMmap(reinterpret_cast<void*>(hint), 32768, 3, 0x1002, -1, 0, &memory)) {
        fprintf(output, "MEMORY_MAP region=%s failed=1\n", region);
        return 1;
    }
    fault_page = memory;
    if (hint && reinterpret_cast<uintptr_t>(memory) < hint) return 1;
    auto* data = static_cast<uint32_t*>(memory);
    unsigned failures = 0;
    for (int kind = 0; kind != 3; ++kind) {
        *data = 0x12345678;
        const unsigned before = recovered;
        const int prot = kind == 2 ? 1 : 0;
        if (sceKernelMprotect(memory, 16384, prot)) return 1;
        uint32_t value = 0;
        if (kind == 0) {
            asm volatile("movl (%1), %0" : "=r"(value) : "r"(data) : "memory");
        } else {
            asm volatile("movl $0x12345678, (%0)" : : "r"(data) : "memory");
            value = *data;
        }
        failures += value != 0x12345678 || unsigned(recovered) != before + 1 || recovery_error != 0;
        fprintf(output, "MEMORY_ACCESS region=%s case=%s faults=%u value=%08x failures=%u\n",
                region, kind == 0 ? "none_read" : kind == 1 ? "none_write" : "readonly_write",
                unsigned(recovered) - before, value, failures);
    }
    const unsigned char code[] = {0xb8, 0x78, 0x56, 0x34, 0x12, 0xc3};
    memcpy(memory, code, sizeof(code));
    const unsigned before = recovered;
    if (sceKernelMprotect(memory, 16384, 3)) return 1;
    const auto value = reinterpret_cast<uint32_t (*)()>(memory)();
    failures += value != 0x12345678 || unsigned(recovered) != before + 1 || recovery_error != 0;
    fprintf(output, "MEMORY_ACCESS region=%s case=noexec faults=%u value=%08x failures=%u\n",
            region, unsigned(recovered) - before, value, failures);
    const int unmap_result = sceKernelMunmap(memory, 32768);
    failures += unmap_result != 0;
    fprintf(output, "MEMORY_UNMAP region=%s result=%d\n", region, unmap_result);
    return failures;
}

int main() {
    FILE* output = fopen("/data/guest-memory.txt", "w");
    if (!output) return 1;
    setvbuf(output, nullptr, _IONBF, 0);
    fprintf(output, "MEMORY_BEGIN\n");
    const auto previous = signal(SIGSEGV, Recover);
    if (previous == SIG_ERR) return 2;
    unsigned failures = CheckAccess(output, 0, "low");
    failures += CheckAccess(output, 0x10000000000ULL, "high");
    for (unsigned i = 0; i != 64; ++i) {
        void* memory = nullptr;
        if (sceKernelMmap(reinterpret_cast<void*>(0x10000000000ULL), 32768, 3,
                          0x1002, -1, 0, &memory)) {
            ++failures;
            break;
        }
        *static_cast<volatile uint32_t*>(memory) = i;
        if (sceKernelMunmap(memory, 32768)) {
            ++failures;
            break;
        }
        fprintf(output, "MEMORY_CYCLE index=%u\n", i);
    }
    signal(SIGSEGV, previous);
    fprintf(output, "MEMORY_END recovered=%u failures=%u\n", unsigned(recovered), failures);
    fclose(output);
    sceSystemServiceLoadExec("EXIT", nullptr);
    return failures ? 1 : 0;
}
