// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <stdint.h>
#include <stdio.h>
using Thread = void*;
extern "C" {
int scePthreadCreate(Thread*, const void*, void* (*)(void*), void*, const char*);
int scePthreadJoin(Thread, void**);
int sceSystemServiceLoadExec(const char*, const char* const*);
}
static unsigned completed;
static void* Worker(void* value) {
    __atomic_add_fetch(&completed, 1, __ATOMIC_RELEASE);
    return value;
}
int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    int failures = 0;
    for (uintptr_t index = 1; index <= 512; ++index) {
        Thread thread = nullptr;
        void* result = nullptr;
        if (scePthreadCreate(&thread, nullptr, Worker, reinterpret_cast<void*>(index), "recycle") != 0) {
            ++failures;
            break;
        }
        if (scePthreadJoin(thread, &result) != 0 || result != reinterpret_cast<void*>(index)) {
            ++failures;
            break;
        }
    }
    failures += __atomic_load_n(&completed, __ATOMIC_ACQUIRE) != 512;
    printf("THREAD_RECYCLE_RESULT completed=%u failures=%d\n", completed, failures);
    sceSystemServiceLoadExec("EXIT", nullptr);
    return failures;
}
