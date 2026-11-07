// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <cstdio>
#include <map>
#include <vector>
#include "common/arch.h"
#include "common/performance_diagnostics.h"

#if defined(__linux__) && defined(ARCH_X86_64)
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <sys/ucontext.h>
#include <sys/uio.h>
#include <unistd.h>
#endif

namespace Common::PerformanceDiagnostics {
namespace {

#if defined(__linux__) && defined(ARCH_X86_64)
constexpr std::size_t SampleCapacity = 32768;
constexpr std::array RegisterIndices{REG_RIP, REG_RSP, REG_RBP, REG_RAX, REG_RBX, REG_RCX,
                                     REG_RDX, REG_RSI, REG_RDI, REG_R8,  REG_R9,  REG_R10,
                                     REG_R11, REG_R12, REG_R13, REG_R14, REG_R15};
constexpr std::array RegisterNames{"rip", "rsp", "rbp", "rax", "rbx", "rcx", "rdx", "rsi", "rdi",
                                   "r8",  "r9",  "r10", "r11", "r12", "r13", "r14", "r15"};
struct Sample {
    std::atomic<bool> ready{};
    unsigned tid{};
    std::uint64_t milliseconds{};
    std::array<std::uint64_t, RegisterIndices.size()> registers{};
};
static_assert(std::atomic<bool>::is_always_lock_free);
static_assert(std::atomic<unsigned>::is_always_lock_free);
std::array<Sample, SampleCapacity> samples{};
std::atomic<unsigned> sample_count{};
std::atomic<bool> sampling{};
timespec started{};

void SampleCpu(int, siginfo_t*, void* context) {
    const int saved_errno = errno;
    if (sampling.load(std::memory_order_acquire)) {
        const auto index = sample_count.fetch_add(1, std::memory_order_relaxed);
        if (index < SampleCapacity) {
            auto& sample = samples[index];
            const auto& state = static_cast<ucontext_t*>(context)->uc_mcontext;
            sample.tid = static_cast<unsigned>(syscall(SYS_gettid));
            timespec now{};
            clock_gettime(CLOCK_MONOTONIC, &now);
            sample.milliseconds =
                (now.tv_sec - started.tv_sec) * 1000 + (now.tv_nsec - started.tv_nsec) / 1000000;
            for (std::size_t i = 0; i < RegisterIndices.size(); ++i) {
                sample.registers[i] = state.gregs[RegisterIndices[i]];
            }
            sample.ready.store(true, std::memory_order_release);
        }
    }
    errno = saved_errno;
}

void StartCpuSampling() {
    struct sigaction previous{};
    itimerval existing{};
    if (sigaction(SIGPROF, nullptr, &previous) != 0 || getitimer(ITIMER_PROF, &existing) != 0 ||
        previous.sa_handler != SIG_DFL || existing.it_value.tv_sec || existing.it_value.tv_usec ||
        existing.it_interval.tv_sec || existing.it_interval.tv_usec) {
        std::fputs("PERF_CPU status=unavailable reason=existing-profiler-or-query-failure\n",
                   stderr);
        return;
    }
    struct sigaction action{};
    action.sa_sigaction = SampleCpu;
    action.sa_flags = SA_SIGINFO | SA_RESTART | SA_ONSTACK;
    sigemptyset(&action.sa_mask);
    clock_gettime(CLOCK_MONOTONIC, &started);
    if (sigaction(SIGPROF, &action, nullptr) != 0) {
        std::fprintf(stderr, "PERF_CPU status=unavailable reason=sigaction errno=%d\n", errno);
        return;
    }
    const itimerval timer{{0, 20000}, {0, 20000}};
    sampling.store(true, std::memory_order_release);
    if (setitimer(ITIMER_PROF, &timer, nullptr) != 0) {
        sampling.store(false, std::memory_order_relaxed);
        const int error = errno;
        sigaction(SIGPROF, &previous, nullptr);
        std::fprintf(stderr, "PERF_CPU status=unavailable reason=setitimer errno=%d\n", error);
        return;
    }
    std::fprintf(stderr, "PERF_CPU status=active process_cpu_interval_us=20000 capacity=%zu\n",
                 SampleCapacity);
}

void DumpCpuSamples() {
    if (!sampling.exchange(false, std::memory_order_relaxed)) {
        return;
    }
    const itimerval disabled{};
    setitimer(ITIMER_PROF, &disabled, nullptr);
    struct Hotspot {
        unsigned count{};
        const Sample* first{};
        const Sample* last{};
    };
    std::map<std::pair<unsigned, std::uint64_t>, Hotspot> grouped;
    const auto attempted = sample_count.load(std::memory_order_relaxed);
    for (unsigned i = 0; i < std::min<std::size_t>(attempted, SampleCapacity); ++i) {
        const auto& sample = samples[i];
        if (!sample.ready.load(std::memory_order_acquire)) {
            continue;
        }
        auto& hotspot = grouped[{sample.tid, sample.registers[0]}];
        if (!hotspot.count++) {
            hotspot.first = &sample;
        }
        hotspot.last = &sample;
    }
    std::vector<Hotspot> ranked;
    for (const auto& [key, value] : grouped) {
        ranked.push_back(value);
    }
    std::ranges::sort(ranked, [](const auto& a, const auto& b) { return a.count > b.count; });
    std::fprintf(stderr, "PERF_CPU_SUMMARY attempted=%u capacity=%zu unique=%zu reported=%zu\n",
                 attempted, SampleCapacity, ranked.size(),
                 std::min<std::size_t>(ranked.size(), 256));
    const int memory_fd = open("/proc/self/mem", O_RDONLY | O_CLOEXEC);
    for (std::size_t i = 0; i < std::min<std::size_t>(ranked.size(), 256); ++i) {
        const auto& item = ranked[i];
        const auto& last = *item.last;
        std::array<char, 2048> line{};
        std::size_t length = 0;
        const auto append = [&](const char* format, auto... args) {
            const auto available = line.size() - length;
            const int written = std::snprintf(line.data() + length, available, format, args...);
            if (written > 0) {
                length += std::min<std::size_t>(written, available - 1);
            }
        };
        append("PERF_CPU_SAMPLE tid=%u count=%u first_ms=%llu last_ms=%llu", last.tid, item.count,
               static_cast<unsigned long long>(item.first->milliseconds),
               static_cast<unsigned long long>(last.milliseconds));
        for (std::size_t reg = 0; reg < RegisterNames.size(); ++reg) {
            append(" %s=%llx", RegisterNames[reg],
                   static_cast<unsigned long long>(last.registers[reg]));
        }
        std::array<unsigned char, 64> code{};
        const std::uint64_t address = last.registers[0] >= 16 ? last.registers[0] - 16 : 0;
        const iovec local{code.data(), code.size()};
        const iovec remote{reinterpret_cast<void*>(address), code.size()};
        auto read = process_vm_readv(getpid(), &local, 1, &remote, 1, 0);
        if (read < 0 && memory_fd >= 0) {
            read = pread(memory_fd, code.data(), code.size(), static_cast<off_t>(address));
        }
        const int read_error = read < 0 ? errno : 0;
        append(" code_address=%llx code_bytes=%zd code_errno=%d code=",
               static_cast<unsigned long long>(address), read, read_error);
        for (ssize_t byte = 0; byte < read; ++byte) {
            append("%02x", code[byte]);
        }
        append("\n");
        std::fwrite(line.data(), 1, length, stderr);
    }
    if (memory_fd >= 0) {
        close(memory_fd);
    }
}
#else
void StartCpuSampling() {
    std::fputs("PERF_CPU status=unavailable reason=platform\n", stderr);
}
void DumpCpuSamples() {}
#endif

void Dump() {
    DumpCpuSamples();
    static constexpr std::array names{
        "draw",           "dispatch",        "ordered-ds",     "bind-buffers",    "bind-textures",
        "shader-compile", "pipeline-lookup", "image-readback", "buffer-readback", "page-protect",
        "submit",         "gpu-wait",        "buffer-clamp"};
    static_assert(names.size() == counters.size());
    for (std::size_t i = 0; i < counters.size(); ++i) {
        const auto& counter = counters[i];
        std::fprintf(stderr,
                     "PERF_REGION name=%s calls=%llu inclusive_ns=%llu self_ns=%llu "
                     "max_ns=%llu bytes=%llu last_ns=%llu\n",
                     names[i], static_cast<unsigned long long>(counter.calls.load()),
                     static_cast<unsigned long long>(counter.inclusive_ns.load()),
                     static_cast<unsigned long long>(counter.self_ns.load()),
                     static_cast<unsigned long long>(counter.max_ns.load()),
                     static_cast<unsigned long long>(counter.bytes.load()),
                     static_cast<unsigned long long>(counter.last_ns.load()));
    }
    std::fflush(stderr);
}

} // namespace

void Start() {
    if (!Enabled()) {
        return;
    }
    static const bool started_once = [] {
        std::atexit(Dump);
        std::at_quick_exit(Dump);
        StartCpuSampling();
        return true;
    }();
    static_cast<void>(started_once);
}

} // namespace Common::PerformanceDiagnostics
