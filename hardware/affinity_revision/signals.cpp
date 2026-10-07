// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <stdint.h>
#include <stdio.h>

using Thread = void*;
extern "C" {
Thread scePthreadSelf();
int scePthreadCreate(Thread*, const void*, void* (*)(void*), void*, const char*);
int scePthreadJoin(Thread, void**);
int scePthreadGetaffinity(Thread, uint64_t*);
int scePthreadSetaffinity(Thread, uint64_t);
int sceKernelGetCurrentCpu();
int sceKernelUsleep(unsigned);
int sceSystemServiceLoadExec(const char*, const char* const*);
void (*signal(int, void (*)(int)))(int);
int pthread_kill(Thread, int);
}

static Thread target;
static int handled;
static int failures;
static int finished;
static uint64_t samples;

static void CheckCpu() {
    if (sceKernelGetCurrentCpu() != 0) {
        __atomic_add_fetch(&failures, 1, __ATOMIC_RELAXED);
    }
    __atomic_add_fetch(&samples, 1, __ATOMIC_RELAXED);
}

static void Callback(int) {
    CheckCpu();
    __atomic_add_fetch(&handled, 1, __ATOMIC_RELEASE);
}

static void* Send(void*) {
    for (int count = 1; count <= 2048; ++count) {
        if (pthread_kill(target, 30) != 0) {
            __atomic_add_fetch(&failures, 1, __ATOMIC_RELAXED);
            break;
        }
        unsigned waits = 0;
        while (__atomic_load_n(&handled, __ATOMIC_ACQUIRE) < count && waits++ < 1000) {
            sceKernelUsleep(1000);
        }
        if (waits >= 1000) {
            __atomic_add_fetch(&failures, 1, __ATOMIC_RELAXED);
            break;
        }
    }
    __atomic_store_n(&finished, 1, __ATOMIC_RELEASE);
    return nullptr;
}

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    target = scePthreadSelf();
    uint64_t original = 0;
    if (scePthreadGetaffinity(target, &original) != 0 || original != 0x7f ||
        scePthreadSetaffinity(target, 1) != 0) {
        return 1;
    }
    printf("AFFINITY_START mask=%llx\n", static_cast<unsigned long long>(original));
    const auto previous = signal(30, Callback);
    Thread sender = nullptr;
    if (previous == reinterpret_cast<void (*)(int)>(-1) ||
        scePthreadCreate(&sender, nullptr, Send, nullptr, "affinity-signals") != 0) {
        return 1;
    }
    while (!__atomic_load_n(&finished, __ATOMIC_ACQUIRE)) {
        CheckCpu();
        if (scePthreadSetaffinity(target, 1) != 0) {
            __atomic_add_fetch(&failures, 1, __ATOMIC_RELAXED);
        }
    }
    if (scePthreadJoin(sender, nullptr) != 0 || handled != 2048 ||
        scePthreadSetaffinity(target, original) != 0) {
        ++failures;
    }
    signal(30, previous);
    printf("AFFINITY_SIGNAL_RESULT failures=%d handled=%d samples=%llu\n", failures, handled,
           static_cast<unsigned long long>(samples));
    printf("AFFINITY_RESULT failures=%d samples=%d\n", failures, handled);
    sceSystemServiceLoadExec("EXIT", nullptr);
    return failures != 0;
}
