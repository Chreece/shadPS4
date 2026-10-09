// SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <stdint.h>
#include <stdio.h>
#include <signal.h>

extern "C" int sceKernelMmap(void*, uint64_t, int, int, int, int64_t, void**);
extern "C" int sceKernelMprotect(const void*, uint64_t, int);
extern "C" int sceSystemServiceLoadExec(const char*, const char* const*);
static void* page;
static volatile sig_atomic_t recovered;
static void Recover(int) {
    recovered = recovered + 1;
    sceKernelMprotect(page, 16384, 3);
}

int main() {
    FILE* out = fopen("/data/plain-read-control.txt", "w");
    if (!out) return 1;
    setvbuf(out, nullptr, _IONBF, 0);
    fprintf(out, "PLAIN_READ_BEGIN\n");
    if (sceKernelMmap(nullptr, 16384, 3, 0x1002, -1, 0, &page)) return 2;
    *static_cast<uint32_t*>(page) = 0x12345678;
    if (signal(SIGSEGV, Recover) == SIG_ERR) return 3;
    if (sceKernelMprotect(page, 16384, 0)) return 4;
    fprintf(out, "PLAIN_READ_ARMED\n");
    uint32_t value;
    asm volatile("movl (%1), %0" : "=r"(value) : "r"(page) : "memory");
    fprintf(out, "PLAIN_READ_END value=%08x recovered=%u\n", value, unsigned(recovered));
    fclose(out);
    sceSystemServiceLoadExec("EXIT", nullptr);
    return value == 0x12345678 && recovered == 1 ? 0 : 5;
}
