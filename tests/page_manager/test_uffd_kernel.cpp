// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Real Linux userfaultfd write-protect/resolve/wake integration test.
// No game, GPU, display, root privileges or sysctl changes required.
// The runner bounds execution with timeout to detect blocked page faults.
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdint>
#include <thread>

#if defined(__linux__)
#include <cerrno>
#include <fcntl.h>
#include <linux/userfaultfd.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "video_core/uffd_range.h"
#endif

static bool Check(bool result, const char* name) {
    if (!result) std::fprintf(stderr, "UFFD_KERNEL=FAIL %s\n", name);
    return result;
}

int main() {
#if !defined(__linux__)
    std::puts("UFFD_KERNEL=SKIP platform_not_linux");
    return 77;
#else
    const auto page = static_cast<std::uint64_t>(sysconf(_SC_PAGESIZE));
    if (!Check(page >= 4096 && (page & (page-1)) == 0, "system page size")) return 1;

    const int fd=static_cast<int>(syscall(SYS_userfaultfd,
                                         O_NONBLOCK | O_CLOEXEC | UFFD_USER_MODE_ONLY));
    if (fd < 0) {
        if (errno == EPERM || errno == EACCES || errno == ENOSYS) {
            std::puts("UFFD_KERNEL=SKIP userfaultfd_unavailable");
            return 77;
        }
        std::perror("UFFD_KERNEL=FAIL syscall");
        return 1;
    }

    uffdio_api api{};
    api.api=UFFD_API;
    api.features=UFFD_FEATURE_THREAD_ID;
    if (!Check(ioctl(fd, UFFDIO_API, &api)==0, "UFFDIO_API") ||
        !Check((api.features & UFFD_FEATURE_THREAD_ID)!=0, "thread-ID feature")) {
        close(fd);
        return 1;
    }

    constexpr std::uint64_t Pages=4;
    const std::uint64_t size=Pages*page;
    auto* data=static_cast<std::uint8_t*>(
        mmap(nullptr,size,PROT_READ | PROT_WRITE,MAP_PRIVATE | MAP_ANONYMOUS,-1,0));
    if (!Check(data != MAP_FAILED, "mmap allocation")) {
        close(fd);
        return 1;
    }
    // Ensure the protected page is already present when we register it.
    data[page+11]=13;
    const auto lower=reinterpret_cast<std::uint64_t>(data);
    const auto reg_range=VideoCore::UffdRegistrationRange(lower,lower+size,page);
    if (!Check(reg_range.has_value() && reg_range->len==size,
               "exact VMA-to-kernel range translation")) {
        munmap(data,size);
        close(fd);
        return 1;
    }
    uffdio_register reg{};
    reg.range.start=reg_range->start;
    reg.range.len=reg_range->len;
    reg.mode=UFFDIO_REGISTER_MODE_WP;
    if (!Check(ioctl(fd, UFFDIO_REGISTER, &reg)==0,"register four pages")) {
        munmap(data,size);
        close(fd);
        return 1;
    }
    uffdio_writeprotect protect{};
    protect.range.start=lower+page;
    protect.range.len=page;
    protect.mode=UFFDIO_WRITEPROTECT_MODE_WP;
    if (!Check(ioctl(fd, UFFDIO_WRITEPROTECT, &protect)==0, "protect populated page")) {
        munmap(data,size);
        close(fd);
        return 1;
    }

    std::atomic<bool> wrote{false};
    std::thread guest([&]{
        data[page+11]=73; // MUST block and emit exactly a WP pagefault
        wrote.store(true,std::memory_order_release);
    });

    // The current thread models shadPS4:Uffd's event handling.
    // If a failure occurs after guest starts, exit instead of calling
    // std::thread destructor on a potentially still-blocked write.
    auto failed=[](const char* msg) -> void {
        std::fprintf(stderr,"UFFD_KERNEL=FAIL %s\n",msg);
        std::fflush(stderr);
        _exit(2);
    };
    pollfd poll_descriptor{};
    poll_descriptor.fd=fd;
    poll_descriptor.events=POLLIN;
    if (poll(&poll_descriptor,1,5000)!=1 || !(poll_descriptor.revents & POLLIN))
        failed("did not receive write-protect fault within five seconds");
    uffd_msg event{};
    if (read(fd,&event,sizeof(event))!=sizeof(event) ||
        event.event!=UFFD_EVENT_PAGEFAULT ||
        !(event.arg.pagefault.flags & UFFD_PAGEFAULT_FLAG_WP))
        failed("wrong userfaultfd notification");

    const auto wake=VideoCore::UffdFaultWakeRange(event.arg.pagefault.address,page);
    if (!wake || wake->start!=lower+page) failed("wake range is not page-aligned");
    protect.mode=UFFDIO_WRITEPROTECT_MODE_DONTWAKE;
    if (ioctl(fd, UFFDIO_WRITEPROTECT, &protect)!=0)
        failed("kernel could not clear write protection without waking");
    uffdio_range wake_kernel{};
    wake_kernel.start=wake->start;
    wake_kernel.len=wake->len;
    if (ioctl(fd, UFFDIO_WAKE, &wake_kernel)!=0)
        failed("UFFDIO_WAKE failed after clearing WP");

    guest.join();
    if (!Check(wrote.load(std::memory_order_acquire) && data[page+11]==73,
               "guest write did not resume with original data")) return 1;

    // The UFFD worker's bounded poll should be cancellable by a jthread
    // stop request even when no page faults remain queued.
    std::atomic<bool> stopped{false};
    const auto started=std::chrono::steady_clock::now();
    std::jthread stoppable([&](std::stop_token token) {
        while (!token.stop_requested()) {
            pollfd pfd{};
            pfd.fd=fd;
            pfd.events=POLLIN;
            const int rc=poll(&pfd,1,200);
            if (rc<0 && errno!=EINTR) break;
        }
        stopped.store(true,std::memory_order_release);
    });
    stoppable.request_stop();
    stoppable.join();
    const auto elapsed=std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now()-started).count();
    if (!Check(stopped.load(std::memory_order_acquire) && elapsed<=750,
               "UFFD idle poll did not exit promptly")) return 1;

    uffdio_range unregister{};
    unregister.start=lower;
    unregister.len=size;
    if (!Check(ioctl(fd, UFFDIO_UNREGISTER,&unregister)==0,
               "unregister exact VMA range")) return 1;
    munmap(data,size);
    close(fd);
    std::printf("UFFD_KERNEL=PASS registration_pages=%llu write_fault=PASS "
                "clear_then_wake=PASS shutdown_ms=%lld\n",
                static_cast<unsigned long long>(Pages),
                static_cast<long long>(elapsed));
    return 0;
#endif
}
