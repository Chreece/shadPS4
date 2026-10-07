// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <stdio.h>
using Thread = void*;
extern "C" {
Thread scePthreadSelf();
int pthread_kill(Thread, int);
int sceKernelUsleep(unsigned);
void (*signal(int, void (*)(int)))(int);
int sceSystemServiceLoadExec(const char*, const char* const*);
}
static unsigned received;
static int queued;
static void Handler(int sig) {
    if (__atomic_add_fetch(&received, 1, __ATOMIC_RELEASE) == 1)
        queued = pthread_kill(scePthreadSelf(), sig);
}
int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    auto previous = signal(30, Handler);
    const int sent = pthread_kill(scePthreadSelf(), 30);
    for (unsigned wait = 0; wait < 1000 && __atomic_load_n(&received, __ATOMIC_ACQUIRE) < 2; ++wait)
        sceKernelUsleep(1000);
    signal(30, previous);
    const int failures = sent != 0 || queued != 0 || received != 2;
    printf("SIGNAL_REQUEUE_RESULT received=%u failures=%d\n", received, failures);
    sceSystemServiceLoadExec("EXIT", nullptr);
    return failures;
}
