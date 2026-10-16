// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Test the exact monotonic deadline-selection policy used by production HLE
// equeues. These checks need no GPU, game files, scheduler hacks or root.
#include <chrono>
#include <cstdio>
#include <unordered_map>

#include "core/libraries/kernel/hrtimer_deadline.h"

using Clock = std::chrono::steady_clock;
using namespace std::chrono_literals;
using Libraries::Kernel::NextHighResolutionWaitDeadline;
struct Timer {
    Clock::time_point added;
    std::chrono::nanoseconds interval;
};
static bool Check(bool test, const char* message) {
    if (!test) std::fprintf(stderr, "HRTIMER_WAIT=FAIL %s\n", message);
    return test;
}
int main() {
    using Map = std::unordered_map<unsigned, Timer>;
    const auto base = Clock::time_point{1h};
    const auto infinite = Clock::time_point::max();
    Map timers;
    if (!Check(NextHighResolutionWaitDeadline(timers, infinite)==infinite,
               "empty indefinite queue") ||
        !Check(NextHighResolutionWaitDeadline(timers, base+3ms)==base+3ms,
               "empty finite timeout")) return 1;

    timers.emplace(4, Timer{base, 1200us});
    timers.emplace(5, Timer{base+50us, 100us});
    timers.emplace(9, Timer{base-10us, 250us});
    if (!Check(NextHighResolutionWaitDeadline(timers,infinite)==base+150us,
               "earliest timer selection") ||
        !Check(NextHighResolutionWaitDeadline(timers,base+60us)==base+60us,
               "global timeout beats timer") ||
        !Check(NextHighResolutionWaitDeadline(timers,base+400us)==base+150us,
               "timer beats global timeout")) return 1;

    // Mutating an existing deadline must take effect on the very next scan.
    timers[4] = Timer{base-30us, 40us};
    if (!Check(NextHighResolutionWaitDeadline(timers,infinite)==base+10us,
               "modified timer moved earlier")) return 1;
    timers.erase(4);
    if (!Check(NextHighResolutionWaitDeadline(timers,infinite)==base+150us,
               "removed timer still blocks deadline")) return 1;

    // For many maps and bounds, prove the helper returns precisely min(all
    // deadlines, timeout); never a rounded, fabricated or negative interval.
    unsigned assertions=0;
    for (unsigned n=0;n<100;n++) {
        timers.clear();
        const auto max_wait=base+std::chrono::microseconds{n*23};
        auto expected=max_wait;
        for (unsigned id=0;id<n;id++) {
            const auto start=base+std::chrono::microseconds{(id*37)%901};
            const auto interval=std::chrono::microseconds{(id*17)%1100};
            timers[id] = Timer{start, interval};
            expected=std::min(expected,start+interval);
        }
        if (!Check(NextHighResolutionWaitDeadline(timers,max_wait)==expected,
                   "varying insertion count/bounds")) return 1;
        ++assertions;
    }
    std::printf("HRTIMER_WAIT=PASS deadline_cases=%u timeout_priority=PASS "
                "modification_and_removal=PASS\n", assertions+8);
    return 0;
}
