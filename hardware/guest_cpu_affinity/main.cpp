// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <orbis/_types/pthread.h>

extern "C" {
int32_t sceKernelGetCurrentCpu(void);
int32_t sceKernelUsleep(uint32_t);
int32_t scePthreadAttrInit(OrbisPthreadAttr*);
int32_t scePthreadAttrDestroy(OrbisPthreadAttr*);
int32_t scePthreadAttrSetaffinity(OrbisPthreadAttr*, uint64_t);
int32_t scePthreadCreate(OrbisPthread*, const OrbisPthreadAttr*, void* (*)(void*), void*, const char*);
int32_t scePthreadJoin(OrbisPthread, void**);
OrbisPthread scePthreadSelf(void);
int32_t scePthreadSetaffinity(OrbisPthread, uint64_t);
}

struct Case {
    uint64_t mask;
    uint64_t next_mask;
    std::atomic<bool> ready{false};
    int32_t first_cpu{-1};
    int32_t last_cpu{-1};
    int32_t update_result{};
    unsigned outside_mask{};
};

static void Sample(Case& test, uint64_t mask) {
    for (unsigned i = 0; i < 100; ++i) {
        const int32_t cpu = sceKernelGetCurrentCpu();
        if (test.first_cpu < 0) {
            test.first_cpu = cpu;
        }
        test.last_cpu = cpu;
        if (cpu < 0 || cpu >= 64 || (mask & (uint64_t{1} << cpu)) == 0) {
            ++test.outside_mask;
        }
        sceKernelUsleep(1000);
    }
}

static void* Worker(void* argument) {
    auto& test = *static_cast<Case*>(argument);
    while (!test.ready.load(std::memory_order_acquire)) {
        sceKernelUsleep(1000);
    }
    Sample(test, test.mask);
    if (test.next_mask != 0) {
        test.update_result = scePthreadSetaffinity(scePthreadSelf(), test.next_mask);
        if (test.update_result == 0) {
            Sample(test, test.next_mask);
        }
    }
    return nullptr;
}

int main() {
    FILE* output = std::fopen("/data/guest-cpu-affinity.log", "w");
    Case cases[]{{0x1, 0}, {0x10, 0}, {0x20, 0}, {0x30, 0}, {0x10, 0x20}};
    unsigned failed{};
    for (auto& test : cases) {
        OrbisPthreadAttr attr{};
        OrbisPthread thread{};
        int32_t result = scePthreadAttrInit(&attr);
        if (result == 0) {
            result = scePthreadAttrSetaffinity(&attr, test.mask);
        }
        if (result == 0) {
            result = scePthreadCreate(&thread, &attr, Worker, &test, "CpuAffinity");
        }
        int32_t mutate_result{};
        if (attr != nullptr) {
            mutate_result = scePthreadAttrSetaffinity(&attr, 0x2);
            scePthreadAttrDestroy(&attr);
        }
        test.ready.store(true, std::memory_order_release);
        if (result == 0) {
            result = scePthreadJoin(thread, nullptr);
        }
        const bool passed = result == 0 && mutate_result == 0 &&
                            test.update_result == 0 && test.outside_mask == 0;
        failed += !passed;
        char line[256];
        std::snprintf(line, sizeof(line),
                      "mask=%llx next=%llx first=%d last=%d create_join=%08x "
                      "mutate=%08x update=%08x outside=%u status=%s\n",
                      static_cast<unsigned long long>(test.mask),
                      static_cast<unsigned long long>(test.next_mask), test.first_cpu,
                      test.last_cpu, static_cast<unsigned>(result),
                      static_cast<unsigned>(mutate_result),
                      static_cast<unsigned>(test.update_result), test.outside_mask,
                      passed ? "PASS" : "FAIL");
        std::fputs(line, stdout);
        if (output != nullptr) {
            std::fputs(line, output);
            std::fflush(output);
        }
    }
    if (output != nullptr) {
        std::fprintf(output, "failed=%u\n", failed);
        std::fclose(output);
    }
    return failed != 0;
}
