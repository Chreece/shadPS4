// SPDX-License-Identifier: GPL-2.0-or-later
// Temporary Linux diagnostic: scalar telemetry only, no GPU commands or forced completion.
#pragma once
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <new>
#if defined(__linux__)
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#endif

namespace ExecutionProgress {
using Word = std::uint64_t;
constexpr Word Magic = 0x4350524f47563131ULL;
constexpr unsigned Bytes = 65536, Words = Bytes / sizeof(Word);
constexpr unsigned Header = 16, Phases = 16, CounterWords = 4;
constexpr unsigned Slots = 32, Levels = 12, Fields = 7;
constexpr unsigned SlotBase = Header + Phases * CounterWords;
constexpr unsigned SlotWords = 4 + Levels * Fields;
static_assert(SlotBase + Slots * SlotWords < Words);
static_assert(std::atomic_ref<Word>::is_always_lock_free);
enum class Phase : Word {
    Draw = 0, DrawIndirect, Dispatch, DispatchIndirect, ShaderLookup,
    BufferDownload, ImageDownload, Submit, TimelineWait,
    FlipRequest, FlipPrepare, FlipPresent, Presenter, RenderFrame,
    ComputePipeline, GraphicsPipeline
};
inline std::atomic<Word*> storage{nullptr};
inline std::atomic<bool> attempted{false};
inline thread_local unsigned local_slot = Slots, local_depth = 0;
inline Word Now() noexcept {
#if defined(__linux__)
    timespec t{}; clock_gettime(CLOCK_MONOTONIC, &t);
    return Word(t.tv_sec) * 1000000000ULL + Word(t.tv_nsec);
#else
    return 0;
#endif
}
inline std::atomic_ref<Word> Ref(Word* p, unsigned n) noexcept { return std::atomic_ref<Word>(p[n]); }
inline bool Enabled() noexcept { return storage.load(std::memory_order_acquire) != nullptr; }
inline void Start() noexcept {
#if defined(__linux__)
    if (attempted.exchange(true)) return;
    const char* path = std::getenv("SHADPS4_PROGRESS_FILE");
    if (!path || !*path) return;
    const int fd = open(path, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return;
    struct stat st{};
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_uid != geteuid() ||
        st.st_size != Bytes || (st.st_mode & 077) != 0) { close(fd); return; }
    void* map = mmap(nullptr, Bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (map == MAP_FAILED) return;
    auto* p = ::new (map) Word[Words]{};
    p[1] = 1; p[2] = Bytes; p[3] = Word(getpid()); p[4] = Now();
    Ref(p, 0).store(Magic, std::memory_order_release);
    storage.store(p, std::memory_order_release);
#endif
}
class Scope {
public:
    explicit Scope(Phase phase, Word a = 0, Word b = 0, Word c = 0, Word d = 0) noexcept
        : p{storage.load(std::memory_order_acquire)}, phase_id{unsigned(phase)} {
        if (!p) return;
        if (local_slot == Slots) {
            const Word next = Ref(p, 5).fetch_add(1, std::memory_order_relaxed);
            // Permanently disable this thread if the fixed diagnostic budget is exhausted.
            local_slot = next < Slots ? unsigned(next) : Slots + 1;
        }
        if (local_slot >= Slots || local_depth >= Levels) {
            Ref(p, 6).fetch_add(1, std::memory_order_relaxed); p = nullptr; return;
        }
        level = local_depth++;
        base = SlotBase + local_slot * SlotWords;
        started = Now();
        Ref(p, base).fetch_add(1, std::memory_order_acq_rel);
#if defined(__linux__)
        Ref(p, base + 1).store(Word(syscall(SYS_gettid)), std::memory_order_relaxed);
#endif
        const unsigned entry = base + 4 + level * Fields;
        Ref(p, entry).store(phase_id, std::memory_order_relaxed);
        Ref(p, entry + 1).store(started, std::memory_order_relaxed);
        Values(entry, a, b, c, d);
        Ref(p, base + 2).store(local_depth, std::memory_order_relaxed);
        Ref(p, base).fetch_add(1, std::memory_order_release);
        Ref(p, Header + phase_id * CounterWords).fetch_add(1, std::memory_order_relaxed);
    }
    Scope(const Scope&) = delete;
    Scope& operator=(const Scope&) = delete;
    void Update(Word a, Word b, Word c, Word d) noexcept {
        if (!p) return;
        Ref(p, base).fetch_add(1, std::memory_order_acq_rel);
        Values(base + 4 + level * Fields, a, b, c, d);
        Ref(p, base).fetch_add(1, std::memory_order_release);
    }
    ~Scope() {
        if (!p) return;
        const Word elapsed = Now() - started;
        const unsigned counter = Header + phase_id * CounterWords;
        Ref(p, counter + 1).fetch_add(1, std::memory_order_relaxed);
        Ref(p, counter + 2).fetch_add(elapsed, std::memory_order_relaxed);
        auto peak = Ref(p, counter + 3);
        Word old = peak.load(std::memory_order_relaxed);
        while (old < elapsed && !peak.compare_exchange_weak(old, elapsed, std::memory_order_relaxed)) {}
        Ref(p, base).fetch_add(1, std::memory_order_acq_rel);
        Ref(p, base + 2).store(--local_depth, std::memory_order_relaxed);
        Ref(p, base).fetch_add(1, std::memory_order_release);
    }
private:
    void Values(unsigned entry, Word a, Word b, Word c, Word d) noexcept {
        Ref(p, entry + 2).store(a, std::memory_order_relaxed);
        Ref(p, entry + 3).store(b, std::memory_order_relaxed);
        Ref(p, entry + 4).store(c, std::memory_order_relaxed);
        Ref(p, entry + 5).store(d, std::memory_order_relaxed);
        Ref(p, entry + 6).store(Now(), std::memory_order_relaxed);
    }
    Word* p{}; unsigned phase_id{}, base{}, level{}; Word started{};
};
} // namespace ExecutionProgress
