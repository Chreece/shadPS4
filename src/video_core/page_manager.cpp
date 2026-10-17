// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <string>
#include <unordered_map>
#include <utility>
#include "common/adaptive_mutex.h"
#include "common/assert.h"
#include "common/debug.h"
#include "common/div_ceil.h"
#include "common/error.h"
#include "common/multi_level_page_table.h"
#include "common/signal_context.h"
#include "common/thread.h"
#include "core/emulator_settings.h"
#include "core/memory.h"
#include "core/signals.h"
#include "video_core/page_manager.h"
#include "video_core/protection_range_coalescer.h"
#include "video_core/renderer_vulkan/vk_rasterizer.h"

#ifndef _WIN64
#include <sys/mman.h>
#include "common/adaptive_mutex.h"
#else
#include <windows.h>
#endif

#ifdef __linux__
#include <thread>
#include <fcntl.h>
#include <linux/userfaultfd.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "video_core/uffd_range.h"
#else
#include "common/spin_lock.h"
#endif

namespace VideoCore {

struct PageManager::Impl {
    struct PageState {
        u8 num_write_watchers;
        u8 num_read_watchers;
        std::atomic<Core::MemoryPermission> guest_perms{Core::MemoryPermission::None};
        std::atomic<Core::MemoryPermission> watched_perms{Core::MemoryPermission::None};

        Core::MemoryPermission WritePerm() const noexcept {
            return num_write_watchers == 0 ? Core::MemoryPermission::Write
                                           : Core::MemoryPermission::None;
        }

        Core::MemoryPermission ReadPerm() const noexcept {
            return num_read_watchers == 0 ? Core::MemoryPermission::Read
                                          : Core::MemoryPermission::None;
        }

        Core::MemoryPermission Perms() const noexcept {
            return guest_perms.load(std::memory_order_relaxed) &
                   (ReadPerm() | WritePerm() | Core::MemoryPermission::Execute);
        }

        template <bool is_read>
        u8 GetPage() const {
            if constexpr (is_read) {
                return num_read_watchers;
            } else {
                return num_write_watchers;
            }
        }

        Core::MemoryPermission Update(PageOp write_op, bool update_write = true,
                                      PageOp read_op = PageOp::None, bool update_read = false) {
            if (update_read) {
                if (read_op == PageOp::Track) {
                    ASSERT_MSG(num_read_watchers == 0, "Too many watchers");
                } else if (read_op == PageOp::Untrack) {
                    ASSERT_MSG(num_read_watchers > 0, "Not enough watchers");
                }
                num_read_watchers += std::to_underlying(read_op);
            }
            if (update_write) {
                if (write_op == PageOp::Track) {
                    ASSERT_MSG(num_write_watchers < 255, "Too many watchers");
                } else if (write_op == PageOp::Untrack) {
                    ASSERT_MSG(num_write_watchers > 0, "Not enough watchers");
                }
                num_write_watchers += std::to_underlying(write_op);
            }
            watched_perms.store(Core::MemoryPermission::ReadWrite & ~(ReadPerm() | WritePerm()),
                                std::memory_order_release);
            return Perms();
        }
    };

    static constexpr size_t ADDRESS_BITS = 40;
    static constexpr size_t NUM_ADDRESS_PAGES = 1ULL << (40 - PM_PAGE_BITS);
    static constexpr size_t NUM_ADDRESS_LOCKS = NUM_ADDRESS_PAGES / NUM_REGION_PAGES;
    inline static Vulkan::Rasterizer* rasterizer;

    Impl() = default;
    virtual ~Impl() = default;
    virtual void DrainPendingInvalidations() {}

    virtual void OnMap(VAddr address, size_t size) {
        // No-op
    }

    virtual void OnUnmap(VAddr address, size_t size) {
        // No-op
    }

    virtual void Protect(VAddr address, size_t size, Core::MemoryPermission perms) = 0;

    virtual void ProtectGuest(VAddr address, size_t size, Core::MemoryPermission guest_perms,
                              Core::MemoryPermission effective_perms) {
        Core::Memory::Instance()->GetAddressSpace().Protect(address, size, guest_perms);
        Protect(address, size, effective_perms);
    }

    void EnsurePages(VAddr begin, VAddr end) {
        end = std::min(end, VAddr{1} << ADDRESS_BITS);
        if (begin >= end) {
            return;
        }
        const size_t start_page = begin >> PM_PAGE_BITS;
        const size_t end_page = (end - 1) >> PM_PAGE_BITS;
        cached_pages.reserve(start_page, end_page);
        locks.reserve(start_page, end_page);
    }

    void ProtectMemory(VAddr address, size_t size, Core::MemoryPermission guest_perms) {
        const VAddr end = address + size;
        const VAddr tracked_end = std::min(end, VAddr{1} << ADDRESS_BITS);
        EnsurePages(address, tracked_end);
        VAddr range_begin = address;
        Core::MemoryPermission range_perms{};
        for (VAddr page_addr = address; page_addr < tracked_end;
             page_addr += PageManager::PM_PAGE_SIZE) {
            const auto page = page_addr >> PM_PAGE_BITS;
            locks[page].lock();
            auto& state = cached_pages[page];
            state.guest_perms.store(guest_perms, std::memory_order_release);
            const auto perms = state.Perms();
            if (page_addr == address) {
                range_perms = perms;
            } else if (perms != range_perms) {
                ProtectGuest(range_begin, page_addr - range_begin, guest_perms, range_perms);
                range_begin = page_addr;
                range_perms = perms;
            }
        }
        if (range_begin < tracked_end) {
            ProtectGuest(range_begin, tracked_end - range_begin, guest_perms, range_perms);
        }
        for (VAddr page_addr = address; page_addr < tracked_end;
             page_addr += PageManager::PM_PAGE_SIZE) {
            locks[page_addr >> PM_PAGE_BITS].unlock();
        }
        if (end > tracked_end) {
            const auto begin = std::max(address, tracked_end);
            Core::Memory::Instance()->GetAddressSpace().Protect(begin, end - begin, guest_perms);
        }
    }

    void UpdatePageWatchers(VAddr addr, u64 size, PageOp write_op) {
        const u64 page_start = addr >> PM_PAGE_BITS;
        const u64 page_end = Common::DivCeil(addr + size, PM_PAGE_SIZE);

        Core::MemoryPermission perms{};
        u64 range_begin = page_start;
        u64 range_pages = 0;
        u64 potential_pages = 0;

        const auto release_pending = [&] {
            if (range_pages > 0) {
                Protect(range_begin << PM_PAGE_BITS, range_pages << PM_PAGE_BITS, perms);
                range_pages = 0;
                potential_pages = 0;
            }
        };

        // Iterate requested pages
        const u64 aligned_addr = page_start << PM_PAGE_BITS;
        const u64 aligned_end = page_end << PM_PAGE_BITS;
        if (!rasterizer->IsMapped(aligned_addr, aligned_end - aligned_addr)) {
            LOG_WARNING(Render,
                        "Tracking memory region {:#x} - {:#x} which is not fully GPU mapped.",
                        aligned_addr, aligned_end);
            EnsurePages(aligned_addr, aligned_end);
        }

        for (u64 page = page_start; page != page_end; ++page) {
            PageState* state = cached_pages.find(page);
            if (!state) {
                continue;
            }

            locks[page].lock();

            const auto old_perms = state->Perms();
            if (page == page_start) {
                perms = old_perms;
            }

            // Apply the change to the page state
            const auto new_perms = state->Update(write_op);
            if (new_perms != perms) [[unlikely]] {
                // If the protection changed add pending (un)protect action
                release_pending();
                perms = new_perms;
            } else if (range_pages != 0) {
                ++potential_pages;
                // The current page ALREADY has these permissions; cap only
                // long runs of unchanged pages merged into a future
                // mprotect call. This never drops an actual page transition.
                if (new_perms == old_perms &&
                    ShouldFlushSparseProtectionRange(range_pages, potential_pages)) {
                    release_pending();
                }
            }

            // If the page must be (un)protected
            if (new_perms != old_perms) {
                if (range_pages == 0) {
                    // Start a new potential range
                    range_begin = page;
                    potential_pages = 1;
                }
                // Extend current range up to potential range
                range_pages = potential_pages;
            }
        }

        // Add pending (un)protect action
        release_pending();

        for (u64 page = page_start; page != page_end; ++page) {
            if (auto* lock = locks.find(page)) {
                lock->unlock();
            }
        }
    }

    void UpdatePageWatchersForRegion(VAddr base_addr, const Bounds& bounds,
                                     const RegionBits& write_mask, const RegionBits& read_mask,
                                     PageOp write_op, PageOp read_op) {
        const u64 base_page = base_addr >> PM_PAGE_BITS;
        const u64 page_start = bounds.start_word * PAGES_PER_WORD + bounds.start_page;
        const u64 page_end = bounds.end_word * PAGES_PER_WORD + bounds.end_page + 1;

        Core::MemoryPermission perms{};
        u64 range_begin = base_page + page_start;
        u64 range_pages = 0;
        u64 potential_pages = 0;

        const auto release_pending = [&] {
            if (range_pages > 0) {
                Protect(range_begin << PM_PAGE_BITS, range_pages << PM_PAGE_BITS, perms);
                range_pages = 0;
                potential_pages = 0;
            }
        };

        for (u64 page = page_start; page != page_end; ++page) {
            PageState* state = cached_pages.find(base_page + page);
            if (!state) {
                continue;
            }

            locks[base_page + page].lock();

            const auto old_perms = state->Perms();
            if (page == page_start) {
                perms = old_perms;
            }

            // Apply the change to the page state
            const bool update_write = write_op != PageOp::None && write_mask.GetPage(page);
            const bool update_read = read_op != PageOp::None && read_mask.GetPage(page);
            const auto new_perms = state->Update(write_op, update_write, read_op, update_read);

            if (new_perms != perms) [[unlikely]] {
                // If the protection changed add pending (un)protect action
                release_pending();
                perms = new_perms;
            } else if (range_pages != 0) {
                // An unchanged page can bridge a short gap between real
                // permission updates. Avoid re-protecting long gaps: on
                // Linux that can cause unnecessary TLB shootdowns and
                // contention in the process memory-map lock.
                ++potential_pages;
                if (new_perms == old_perms &&
                    ShouldFlushSparseProtectionRange(range_pages, potential_pages)) {
                    release_pending();
                }
            }

            // If the page must be (un)protected
            if (new_perms != old_perms) {
                if (range_pages == 0) {
                    // Start a new potential range
                    range_begin = base_page + page;
                    potential_pages = 1;
                }
                // Extend current rango up to potential range
                range_pages = potential_pages;
            }
        }

        // Add pending (un)protect action
        release_pending();

        for (u64 page = page_start; page != page_end; ++page) {
            if (auto* lock = locks.find(base_page + page)) {
                lock->unlock();
            }
        }
    }

    struct PageTraits {
        using Entry = PageState;
        static constexpr size_t ADDRESS_SPACE_BITS = ADDRESS_BITS;
        static constexpr size_t L1_BITS = 16;
        static constexpr size_t PAGE_BITS = PM_PAGE_BITS;
        static constexpr bool NULL_CHECK = false;
    };
    Common::MultiLevelPageTable<PageTraits> cached_pages;
    struct MutexTraits {
#ifdef PTHREAD_ADAPTIVE_MUTEX_INITIALIZER_NP
        using Entry = Common::AdaptiveMutex;
#else
        using Entry = std::mutex;
#endif
        static constexpr size_t ADDRESS_SPACE_BITS = ADDRESS_BITS;
        static constexpr size_t L1_BITS = 16;
        static constexpr size_t PAGE_BITS = PM_PAGE_BITS;
        static constexpr bool NULL_CHECK = false;
    };
    Common::MultiLevelPageTable<MutexTraits> locks;
};

#ifdef __linux__
struct UffdImpl : public PageManager::Impl {
private:
    std::jthread ufd_thread;
    // GPU-origin faults must remain serviceable while a CPU-origin fault is
    // queued for cache readback on the GPU command processor.
    std::atomic_size_t pending_gpu_invalidations{0};
    int uffd{-1};

    // Read-only telemetry, opt-in and disabled for normal execution. Counters
    // may be updated on the GPU processor while the UFFD receiver polls.
    struct Perf {
        std::atomic<u64> cpu_faults{0}, gpu_faults{0}, audio_cpu_faults{0};
        std::atomic<u64> game_cpu_faults{0}, cpu_completed{0};
        std::atomic<u64> over_21ms{0}, over_100ms{0}, audio_over_21ms{0};
        std::atomic<u64> total_cpu_wait_us{0}, max_cpu_wait_us{0};
        std::atomic<u64> max_audio_wait_us{0}, max_gpu_inline_us{0};
        std::atomic<u64> max_pending{0}, last_slow_tid{0};
    } perf;
    const bool perf_enabled = [] {
        const char* value = std::getenv("SHADPS4_UFFD_PERF_PROBE");
        return value && value[0] == '1' && value[1] == '\0';
    }();
    // Kernel userfaultfd read(2) supports more than one uffd_msg per call.
    // Optional optimization only: the default handler still reads exactly one
    // event, so behavior remains unchanged unless explicitly enabled.
    const bool batch_reads_enabled = [] {
        const char* value = std::getenv("SHADPS4_UFFD_BATCH_READ");
        return value && value[0] == '1' && value[1] == '\0';
    }();
    // Owned exclusively by UffdHandler; never accessed from queued callbacks.
    std::unordered_map<u32, std::string> fault_thread_names;

    static void PerfMax(std::atomic<u64>& value, u64 observed) {
        auto old = value.load(std::memory_order_relaxed);
        while (observed > old &&
               !value.compare_exchange_weak(old, observed, std::memory_order_relaxed)) {}
    }

public:
    UffdImpl(Vulkan::Rasterizer* rasterizer_) : Impl() {
        rasterizer = rasterizer_;
        uffd = syscall(SYS_userfaultfd, O_CLOEXEC | O_NONBLOCK | UFFD_USER_MODE_ONLY);
        if (uffd == -1) {
            LOG_ERROR(Common_Memory,
                      "userfaultfd syscall failed: {}, falling back to signal implementation",
                      Common::GetLastErrorMsg());
            throw std::runtime_error("userfaultfd");
        }

        try {
            // This backend needs the faulting Linux TID to distinguish GPU
            // command processor faults from faults on other guest threads.
            uffdio_api api{};
            api.api = UFFD_API;
            api.features = UFFD_FEATURE_THREAD_ID;
            if (ioctl(uffd, UFFDIO_API, &api) != 0 ||
                (api.features & UFFD_FEATURE_THREAD_ID) == 0) {
                LOG_ERROR(Common_Memory,
                          "userfaultfd thread-ID feature negotiation failed: {}",
                          Common::GetLastErrorMsg());
                throw std::runtime_error("uffdio_api");
            }

            // Register ALL existing reserved VMAs before publishing the fault
            // handler or allowing GPU memory watches. Registration needs a
            // start and LENGTH; boost::icl's upper() is an ending address.
            // If any registration fails, close the FD and safely use the
            // existing signal backend instead of leaving a partial tracker.
            const auto regions = Core::Memory::Instance()->GetAddressSpace().GetUsableRegions();
            size_t count = 0;
            for (const auto& region : regions) {
                const auto range =
                    UffdRegistrationRange(region.lower(), region.upper(), PageManager::PM_PAGE_SIZE);
                if (!range || !RegisterRange(range->start, range->len)) {
                    LOG_ERROR(Common_Memory,
                              "Failed to register userfaultfd VM interval [{:#x}, {:#x}): {}",
                              region.lower(), region.upper(), Common::GetLastErrorMsg());
                    throw std::runtime_error("uffdio_register");
                }
                ++count;
            }
            LOG_INFO(Common_Memory,
                     "registered {} reserved VM ranges with userfaultfd (exact bounds)",
                     count);
            // The handler now runs with its registrations already complete.
            ufd_thread = std::jthread([this](std::stop_token token) { UffdHandler(token); });
        } catch (...) {
            close(uffd); // releases any partially registered WP ranges
            uffd = -1;
            throw;
        }
    }

    ~UffdImpl() override {
        // poll(-1) previously prevented std::jthread from joining. The event
        // loop now wakes periodically to observe request_stop, then the FD
        // is closed only after the handler thread has finished using it.
        ufd_thread.request_stop();
        if (ufd_thread.joinable()) {
            ufd_thread.join();
        }
        if (uffd >= 0) {
            close(uffd);
        }
    }

    bool RegisterRange(VAddr address, size_t size) {
        uffdio_register reg{};
        reg.range.start = address;
        reg.range.len = size;
        reg.mode = UFFDIO_REGISTER_MODE_WP;
        return ioctl(uffd, UFFDIO_REGISTER, &reg) == 0;
    }

    void OnMap(VAddr address, size_t size) override {
        PageManager::Impl::OnMap(address, size);
        ASSERT_MSG(RegisterRange(address, size),
                   "Uffdio register failed at {:#x} length {:#x}: {}",
                   address, size, Common::GetLastErrorMsg());
    }

    void OnUnmap(VAddr address, size_t size) override {
        uffdio_range range{};
        range.start = address;
        range.len = size;
        const int ret = ioctl(uffd, UFFDIO_UNREGISTER, &range);
        ASSERT_MSG(ret != -1, "Uffdio unregister failed with error: {}",
                   Common::GetLastErrorMsg());
    }

    void Protect(VAddr address, size_t size, Core::MemoryPermission perms) override {
        const bool allow_write = True(perms & Core::MemoryPermission::Write);
        uffdio_writeprotect wp{};
        wp.range.start = address;
        wp.range.len = size;
        wp.mode = allow_write ? UFFDIO_WRITEPROTECT_MODE_DONTWAKE
                              : UFFDIO_WRITEPROTECT_MODE_WP;
        const int ret = ioctl(uffd, UFFDIO_WRITEPROTECT, &wp);
        ASSERT_MSG(ret != -1, "Uffdio writeprotect failed with error: {}",
                   Common::GetLastErrorMsg());
    }

    void DrainPendingInvalidations() override {
        // C++20 atomic wait avoids polling and cannot miss a completion wakeup.
        for (auto pending = pending_gpu_invalidations.load(); pending != 0;
             pending = pending_gpu_invalidations.load()) {
            pending_gpu_invalidations.wait(pending);
        }
    }

    void WakeFault(VAddr addr, u32 tid) {
        // Wake only *after* the corresponding GPU/CPU data is coherent and
        // the page's write protection has been removed by memory tracking.
        const auto wake = UffdFaultWakeRange(addr, PageManager::PM_PAGE_SIZE);
        ASSERT(wake.has_value());
        uffdio_range range{};
        range.start = wake->start;
        range.len = wake->len;
        const int ret = ioctl(uffd, UFFDIO_WAKE, &range);
        ASSERT_MSG(ret != -1, "Waking userfaultfd thread {} failed: {}",
                   tid, Common::GetLastErrorMsg());
    }

    void UffdHandler(std::stop_token token) {
        Common::SetCurrentThreadName("shadPS4:Uffd");
        // Continue receiving GPU-origin faults while queued GPU work is
        // draining during teardown; these faults may otherwise deadlock it.
        auto last_perf_report = std::chrono::steady_clock::now();
        // No heap allocation or extra copy on the hot fault path. All
        // messages are consumed in kernel delivery order, including GPU faults
        // which must be serviced inline while the GPU thread is blocked.
        static constexpr size_t MAX_READ_MESSAGES = 32;
        std::array<uffd_msg, MAX_READ_MESSAGES> messages{};
        u64 read_calls = 0, read_events = 0, multi_reads = 0, largest_batch = 0;
        while (!token.stop_requested() || pending_gpu_invalidations.load() != 0) {
            if (perf_enabled) {
                const auto now = std::chrono::steady_clock::now();
                const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                    now - last_perf_report).count();
                if (elapsed >= 5000) {
                    last_perf_report = now;
                    const u64 completed = perf.cpu_completed.exchange(0);
                    const u64 sum = perf.total_cpu_wait_us.exchange(0);
                    LOG_WARNING(Common_Memory,
                        "UFFD_PERF window_ms={} cpu_faults={} completed={} gpu_faults={} "
                        "audio_faults={} game_main_faults={} avg_cpu_us={} max_cpu_us={} "
                        "slow_21ms={} slow_100ms={} audio_slow_21ms={} "
                        "max_audio_us={} max_gpu_inline_us={} pending={} max_pending={} "
                        "last_slow_tid={} batch_read={} read_calls={} read_events={} "
                        "multi_reads={} max_read_batch={}",
                        elapsed, perf.cpu_faults.exchange(0), completed,
                        perf.gpu_faults.exchange(0), perf.audio_cpu_faults.exchange(0),
                        perf.game_cpu_faults.exchange(0), completed ? sum / completed : 0,
                        perf.max_cpu_wait_us.exchange(0), perf.over_21ms.exchange(0),
                        perf.over_100ms.exchange(0), perf.audio_over_21ms.exchange(0),
                        perf.max_audio_wait_us.exchange(0),
                        perf.max_gpu_inline_us.exchange(0),
                        pending_gpu_invalidations.load(), perf.max_pending.exchange(0),
                        perf.last_slow_tid.load(),
                        batch_reads_enabled ? 1 : 0, read_calls, read_events,
                        multi_reads, largest_batch);
                    read_calls = 0;
                    read_events = 0;
                    multi_reads = 0;
                    largest_batch = 0;
                }
            }
            pollfd descriptor{};
            descriptor.fd = uffd;
            descriptor.events = POLLIN;

            // A bounded blocking poll is needed for clean jthread teardown.
            // This is a shut-down check, not guest-time polling or busy spin.
            const int pollres = poll(&descriptor, 1, 200);
            if (pollres == 0) {
                continue;
            }
            if (pollres < 0) {
                if (errno == EINTR) {
                    continue;
                }
                LOG_ERROR(Common_Memory, "userfaultfd poll failed: {}",
                          Common::GetLastErrorMsg());
                break;
            }
            if (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)) {
                LOG_ERROR(Common_Memory, "userfaultfd poll reported invalid FD, events={:#x}",
                          descriptor.revents);
                break;
            }
            if (!(descriptor.revents & POLLIN)) {
                continue;
            }

            // read(2) on userfaultfd fills multiple whole uffd_msg records
            // when available (Linux userfaultfd(2)); no correctness shortcut.
            // In default mode this request is exactly one uffd_msg, as before.
            const size_t requested = batch_reads_enabled ? sizeof(messages) :
                                     sizeof(messages[0]);
            const ssize_t bytes = read(uffd, messages.data(), requested);
            if (bytes == -1) {
                if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) {
                    continue;
                }
                LOG_ERROR(Common_Memory, "userfaultfd read failed: {}",
                          Common::GetLastErrorMsg());
                break;
            }
            if (bytes <= 0 || bytes % sizeof(messages[0]) != 0) {
                LOG_ERROR(Common_Memory,
                          "Unexpected userfaultfd read length: bytes={}, record_bytes={}",
                          bytes, sizeof(messages[0]));
                break;
            }
            const size_t count = static_cast<size_t>(bytes) / sizeof(messages[0]);
            if (perf_enabled) {
                ++read_calls;
                read_events += count;
                multi_reads += count > 1;
                largest_batch = std::max<u64>(largest_batch, count);
            }
            // Never merge addresses or reorder GPU and CPU write faults:
            // each event retains its original invalidation and wakeup.
            bool malformed = false;
            for (size_t i = 0; i != count; ++i) {
                const uffd_msg& msg = messages[i];
                // uffd_msg is packed. Copy values before passing to fmt.
                const u64 fault_flags = msg.arg.pagefault.flags;
                if (msg.event != UFFD_EVENT_PAGEFAULT ||
                    !(fault_flags & UFFD_PAGEFAULT_FLAG_WP)) {
                    LOG_ERROR(Common_Memory,
                              "Unexpected userfaultfd message: bytes={}, event={}, fault_flags={:#x}",
                              bytes, msg.event, fault_flags);
                    malformed = true;
                    break;
                }
                const VAddr addr = msg.arg.pagefault.address;
                const u32 ptid = msg.arg.pagefault.feat.ptid;
            if (ptid == rasterizer->GetGpuCommandProcessorThreadId()) {
                // The GPU processor is paused in this fault. Its own memory
                // invalidation must execute inline, without posting to itself.
                const auto start = perf_enabled ? std::chrono::steady_clock::now() :
                    std::chrono::steady_clock::time_point{};
                rasterizer->InvalidateMemory(addr, 1, true);
                WakeFault(addr, ptid);
                if (perf_enabled) {
                    perf.gpu_faults.fetch_add(1, std::memory_order_relaxed);
                    const u64 duration = std::chrono::duration_cast<std::chrono::microseconds>(
                        std::chrono::steady_clock::now() - start).count();
                    PerfMax(perf.max_gpu_inline_us, duration);
                }
            } else {
                // Queue the *complete* invalidation on the GPU processor.
                // Keep the event receiver responsive to faults in that
                // processor. Never wake the CPU writer before readback.
                bool from_audio = false;
                if (perf_enabled) {
                    const auto [iter, inserted] = fault_thread_names.try_emplace(ptid);
                    if (inserted) {
                        std::ifstream name("/proc/self/task/" + std::to_string(ptid) + "/comm");
                        std::getline(name, iter->second);
                        if (iter->second.find("Audio") != std::string::npos ||
                            iter->second.find("Game:Main") != std::string::npos) {
                            LOG_WARNING(Common_Memory, "UFFD_ORIGIN tid={} name={}",
                                        ptid, iter->second);
                        }
                    }
                    from_audio = iter->second.find("Audio") != std::string::npos ||
                                 iter->second.find("AK::") != std::string::npos;
                    perf.cpu_faults.fetch_add(1, std::memory_order_relaxed);
                    if (from_audio)
                        perf.audio_cpu_faults.fetch_add(1, std::memory_order_relaxed);
                    if (iter->second.find("Game:Main") != std::string::npos)
                        perf.game_cpu_faults.fetch_add(1, std::memory_order_relaxed);
                }
                const auto start = perf_enabled ? std::chrono::steady_clock::now() :
                    std::chrono::steady_clock::time_point{};
                const auto pending = pending_gpu_invalidations.fetch_add(1) + 1;
                if (perf_enabled)
                    PerfMax(perf.max_pending, pending);
                rasterizer->InvalidateMemoryOnGpuThread(addr, 1,
                    [this, addr, ptid, start, from_audio] {
                    WakeFault(addr, ptid);
                    if (perf_enabled) {
                        const u64 duration = std::chrono::duration_cast<
                            std::chrono::microseconds>(
                                std::chrono::steady_clock::now() - start).count();
                        perf.cpu_completed.fetch_add(1, std::memory_order_relaxed);
                        perf.total_cpu_wait_us.fetch_add(duration, std::memory_order_relaxed);
                        PerfMax(perf.max_cpu_wait_us, duration);
                        if (duration >= 21333) {
                            perf.over_21ms.fetch_add(1, std::memory_order_relaxed);
                            if (from_audio)
                                perf.audio_over_21ms.fetch_add(1, std::memory_order_relaxed);
                        }
                        if (duration >= 100000) {
                            perf.over_100ms.fetch_add(1, std::memory_order_relaxed);
                            perf.last_slow_tid.store(ptid, std::memory_order_relaxed);
                        }
                        if (from_audio)
                            PerfMax(perf.max_audio_wait_us, duration);
                    }
                    pending_gpu_invalidations.fetch_sub(1);
                    pending_gpu_invalidations.notify_all();
                });
            }
            } // Each fault is fully dispatched before handling the next one.
            if (malformed) {
                break;
            }
        }
    }
};
#endif // __linux__

struct SignalImpl : public PageManager::Impl {
    inline static SignalImpl* instance;

    SignalImpl(Vulkan::Rasterizer* rasterizer_) : Impl() {
        rasterizer = rasterizer_;
        instance = this;

        // Should be called first.
        constexpr auto priority = std::numeric_limits<u32>::min();
        Core::Signals::Instance()->RegisterAccessViolationHandler(GuestFaultSignalHandler,
                                                                  priority);
    }

    void Protect(VAddr address, size_t size, Core::MemoryPermission perms) override {
        RENDERER_TRACE;
        auto* memory = Core::Memory::Instance();
        auto& impl = memory->GetAddressSpace();
        ASSERT_MSG(perms != Core::MemoryPermission::Write,
                   "Attempted to protect region as write-only which is not a valid permission");
        impl.Protect(address, size, perms);
    }

    void ProtectGuest(VAddr address, size_t size, Core::MemoryPermission guest_perms,
                      Core::MemoryPermission effective_perms) override {
        Protect(address, size, effective_perms);
    }

    static bool GuestFaultSignalHandler(void* context, void* fault_address) {
        const auto addr = reinterpret_cast<VAddr>(fault_address);
        const auto size = std::min<u64>(8, PageManager::GetNextPageAddr(addr) - addr);
        if (addr >= (VAddr{1} << ADDRESS_BITS) || Common::IsExecuteError(context) ||
            !rasterizer->IsMapped(addr, size)) {
            return false;
        }
        const auto* state = instance->cached_pages.find(addr >> PageManager::PM_PAGE_BITS);
        const auto access = Common::IsWriteError(context) ? Core::MemoryPermission::Write
                                                          : Core::MemoryPermission::Read;
        if (!state || False(state->guest_perms.load(std::memory_order_acquire) & access)) {
            return false;
        }
        if (False(state->watched_perms.load(std::memory_order_acquire) & access)) {
            // Another thread may have already removed the GPU watch for this fault.
            return true;
        }
        const auto is_gpu_thread =
            std::this_thread::get_id() == rasterizer->GetGpuCommandProcessorThread();
        if (Common::IsWriteError(context)) {
            return rasterizer->InvalidateMemory(addr, size, is_gpu_thread);
        } else {
            return rasterizer->ReadMemory(addr, size, is_gpu_thread);
        }
        return false;
    }
};

PageManager::PageManager(Vulkan::Rasterizer* rasterizer_) {
#ifdef __linux__
    if (EmulatorSettings.IsUserfaultfdTracking()) {
        try {
            impl = std::make_unique<UffdImpl>(rasterizer_);
            LOG_INFO(Config, "Memory tracking method: userfaultfd");
            return;
        } catch (const std::runtime_error& e) {
            // if uffd is unsupported, falls back to SignalImpl
        }
    }
    LOG_INFO(Config, "Memory tracking method: signals");
#endif
    impl = std::make_unique<SignalImpl>(rasterizer_);
}

PageManager::~PageManager() = default;

void PageManager::DrainPendingInvalidations() {
    impl->DrainPendingInvalidations();
}

void PageManager::OnGpuMap(VAddr address, size_t size) {
    impl->OnMap(address, size);
}

void PageManager::MapMemory(VAddr address, size_t size) {
    impl->EnsurePages(address, address + size);
}

void PageManager::ProtectMemory(VAddr address, size_t size, Core::MemoryPermission perms) {
    impl->ProtectMemory(address, size, perms);
}

void PageManager::OnGpuUnmap(VAddr address, size_t size) {
    impl->OnUnmap(address, size);
}

void PageManager::UpdatePageWatchers(VAddr addr, u64 size, PageOp write_op) const {
    impl->UpdatePageWatchers(addr, size, write_op);
}

void PageManager::UpdatePageWatchersForRegion(VAddr base_addr, const Bounds& bounds,
                                              const RegionBits& write_mask,
                                              const RegionBits& read_mask, PageOp write_op,
                                              PageOp read_op) const {
    impl->UpdatePageWatchersForRegion(base_addr, bounds, write_mask, read_mask, write_op, read_op);
}

} // namespace VideoCore
