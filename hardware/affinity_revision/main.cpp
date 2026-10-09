// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <stdint.h>
#include <stdio.h>

using Thread = void*;
using Attr = void*;

extern "C" {
Thread scePthreadSelf();
int scePthreadCreate(Thread*, const Attr*, void* (*)(void*), void*, const char*);
int scePthreadJoin(Thread, void**);
int scePthreadAttrInit(Attr*);
int scePthreadAttrDestroy(Attr*);
int scePthreadAttrSetaffinity(Attr*, uint64_t);
int scePthreadGetaffinity(Thread, uint64_t*);
int scePthreadSetaffinity(Thread, uint64_t);
int sceKernelGetCurrentCpu();
int sceKernelUsleep(unsigned);
int sceSystemServiceLoadExec(const char*, const char* const*);
}

struct Worker {
    uint64_t mask;
    int failures;
    int samples;
};

static void* Sample(void* arg) {
    auto& worker = *static_cast<Worker*>(arg);
    uint64_t actual = 0;
    if (scePthreadGetaffinity(scePthreadSelf(), &actual) != 0 || actual != worker.mask) {
        ++worker.failures;
    }
    for (int i = 0; i < 25000; ++i) {
        const int cpu = sceKernelGetCurrentCpu();
        if (cpu < 0 || cpu >= 8 || !(worker.mask & (uint64_t{1} << cpu))) {
            ++worker.failures;
        }
        ++worker.samples;
        if (i % 1000 == 0) {
            sceKernelUsleep(1);
        }
    }
    return nullptr;
}

struct Migration {
    int request;
    int ack;
    uint64_t mask;
    int failures;
    int samples;
};

static void* ObserveMigration(void* arg) {
    auto& migration = *static_cast<Migration*>(arg);
    int completed = 0;
    for (;;) {
        const int request = __atomic_load_n(&migration.request, __ATOMIC_ACQUIRE);
        if (request < 0) {
            break;
        }
        if (request == completed) {
            sceKernelUsleep(1);
            continue;
        }
        uint64_t actual = 0;
        if (scePthreadGetaffinity(scePthreadSelf(), &actual) != 0 || actual != migration.mask) {
            ++migration.failures;
        }
        for (int i = 0; i < 1000; ++i) {
            const int cpu = sceKernelGetCurrentCpu();
            if (cpu < 0 || cpu >= 8 || !(migration.mask & (uint64_t{1} << cpu))) {
                ++migration.failures;
            }
            ++migration.samples;
        }
        completed = request;
        __atomic_store_n(&migration.ack, completed, __ATOMIC_RELEASE);
    }
    return nullptr;
}

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    int failures = 0;
    int samples = 0;
    const Thread self = scePthreadSelf();
    uint64_t original = 0;
    if (scePthreadGetaffinity(self, &original) != 0 || original == 0 || original > 0x7f) {
        printf("AFFINITY_RESULT failures=1 samples=0 invalid_initial_mask=%llx\n",
               static_cast<unsigned long long>(original));
        sceSystemServiceLoadExec("EXIT", nullptr);
        return 1;
    }
    printf("AFFINITY_START mask=%llx\n", static_cast<unsigned long long>(original));
    Worker workers[8]{};
    Thread threads[8]{};
    for (int cpu = 0; cpu < 8; ++cpu) {
        if (!(original & (uint64_t{1} << cpu))) {
            continue;
        }
        Attr attr = nullptr;
        workers[cpu].mask = uint64_t{1} << cpu;
        int ret = scePthreadAttrInit(&attr);
        if (ret == 0) {
            ret = scePthreadAttrSetaffinity(&attr, workers[cpu].mask);
        }
        if (ret == 0) {
            ret = scePthreadCreate(&threads[cpu], &attr, Sample, &workers[cpu], "affinity-single");
        }
        if (attr != nullptr) {
            scePthreadAttrDestroy(&attr);
        }
        if (ret != 0) {
            printf("AFFINITY_CREATE_ERROR cpu=%d ret=%d\n", cpu, ret);
            threads[cpu] = nullptr;
            ++failures;
        }
    }
    for (int cpu = 0; cpu < 8; ++cpu) {
        if (threads[cpu] != nullptr) {
            if (scePthreadJoin(threads[cpu], nullptr) != 0) {
                ++failures;
            }
            failures += workers[cpu].failures;
            samples += workers[cpu].samples;
        }
    }
    Worker inherited{original & (~original + 1)};
    Thread child = nullptr;
    if (scePthreadSetaffinity(self, inherited.mask) != 0 ||
        scePthreadCreate(&child, nullptr, Sample, &inherited, "affinity-inherit") != 0) {
        ++failures;
    } else {
        if (scePthreadJoin(child, nullptr) != 0) {
            ++failures;
        }
        failures += inherited.failures;
        samples += inherited.samples;
    }
    if (scePthreadSetaffinity(self, original) != 0) {
        ++failures;
    }
    Worker multi{original & 0x30};
    if (multi.mask != 0) {
        if (scePthreadSetaffinity(self, multi.mask) != 0) {
            ++failures;
        } else {
            Sample(&multi);
            failures += multi.failures;
            samples += multi.samples;
        }
        if (scePthreadSetaffinity(self, original) != 0) {
            ++failures;
        }
    }
    Migration migration{};
    Thread moving = nullptr;
    if (scePthreadCreate(&moving, nullptr, ObserveMigration, &migration, "affinity-migrate") != 0) {
        ++failures;
    } else {
        int phase = 0;
        for (int repeat = 0; repeat < 16; ++repeat) {
            for (int cpu = 0; cpu < 8; ++cpu) {
                if (!(original & (uint64_t{1} << cpu))) {
                    continue;
                }
                migration.mask = uint64_t{1} << cpu;
                if (scePthreadSetaffinity(moving, migration.mask) != 0) {
                    ++failures;
                    continue;
                }
                __atomic_store_n(&migration.request, ++phase, __ATOMIC_RELEASE);
                while (__atomic_load_n(&migration.ack, __ATOMIC_ACQUIRE) != phase) {
                    sceKernelUsleep(1);
                }
            }
        }
        __atomic_store_n(&migration.request, -1, __ATOMIC_RELEASE);
        if (scePthreadJoin(moving, nullptr) != 0) {
            ++failures;
        }
        failures += migration.failures;
        samples += migration.samples;
    }
    printf("AFFINITY_RESULT failures=%d samples=%d\n", failures, samples);
    sceSystemServiceLoadExec("EXIT", nullptr);
    return failures != 0;
}
