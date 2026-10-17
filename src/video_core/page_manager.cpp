// SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <atomic>
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

        Core::MemoryPermission WritePerm() const noexcept {
            return num_write_watchers == 0 ? Core::MemoryPermission::Write
                                           : Core::MemoryPermission::None;
        }

        Core::MemoryPermission ReadPerm() const noexcept {
            return num_read_watchers == 0 ? Core::MemoryPermission::Read
                                          : Core::MemoryPermission::None;
        }

        Core::MemoryPermission Perms() const noexcept {
            return ReadPerm() | WritePerm();
        }

        template <bool is_read>
        u8 GetPage() const {
            if constexpr (is_read) {
                return num_read_watchers;
            } else {
                return num_write_watchers;
            }
        }

        constexpr Core::MemoryPermission Update(PageOp write_op, bool update_write = true,
                                                PageOp read_op = PageOp::None,
                                                bool update_read = false) {
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
        EnsurePages(address, address + size);
    }

    virtual void OnUnmap(VAddr address, size_t size) {
        // No-op
    }

    virtual void Protect(VAddr address, size_t size, Core::MemoryPermission perms) = 0;

    void EnsurePages(VAddr begin, VAddr end) {
        end = std::min(end, VAddr{1} << ADDRESS_BITS) - 1;
        const size_t start_page = begin >> PM_PAGE_BITS;
        const size_t end_page = end >> PM_PAGE_BITS;
        cached_pages.reserve(start_page, end_page);
        locks.reserve(start_page, end_page);
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
                // Avoid re-protecting large spans of unchanged pages just to
                // coalesce two distant access-permission transitions.
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
        while (!token.stop_requested() || pending_gpu_invalidations.load() != 0) {
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

            uffd_msg msg{};
            const ssize_t bytes = read(uffd, &msg, sizeof(msg));
            if (bytes == -1) {
                if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) {
                    continue;
                }
                LOG_ERROR(Common_Memory, "userfaultfd read failed: {}",
                          Common::GetLastErrorMsg());
                break;
            }
            // uffd_msg is packed. fmt/spdlog arguments must not bind a
            // reference directly to its unaligned bitfield members.
            const u64 fault_flags = msg.arg.pagefault.flags;
            if (bytes != sizeof(msg) || msg.event != UFFD_EVENT_PAGEFAULT ||
                !(fault_flags & UFFD_PAGEFAULT_FLAG_WP)) {
                LOG_ERROR(Common_Memory,
                          "Unexpected userfaultfd message: bytes={}, event={}, fault_flags={:#x}",
                          bytes, msg.event, fault_flags);
                break;
            }

            const VAddr addr = msg.arg.pagefault.address;
            const u32 ptid = msg.arg.pagefault.feat.ptid;
            if (ptid == rasterizer->GetGpuCommandProcessorThreadId()) {
                // The GPU processor is paused in this fault. Its own memory
                // invalidation must execute inline, without posting to itself.
                rasterizer->InvalidateMemory(addr, 1, true);
                WakeFault(addr, ptid);
            } else {
                // Queue the *complete* invalidation on the GPU processor.
                // Keep the event receiver responsive to faults in that
                // processor. Never wake the CPU writer before readback.
                pending_gpu_invalidations.fetch_add(1);
                rasterizer->InvalidateMemoryOnGpuThread(addr, 1, [this, addr, ptid] {
                    WakeFault(addr, ptid);
                    pending_gpu_invalidations.fetch_sub(1);
                    pending_gpu_invalidations.notify_all();
                });
            }
        }
    }
};
#endif // __linux__

struct SignalImpl : public PageManager::Impl {
    SignalImpl(Vulkan::Rasterizer* rasterizer_) : Impl() {
        rasterizer = rasterizer_;

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

    static bool GuestFaultSignalHandler(void* context, void* fault_address) {
        const auto addr = reinterpret_cast<VAddr>(fault_address);
        const auto size = std::min<u64>(8, PageManager::GetNextPageAddr(addr) - addr);
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
