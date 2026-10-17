// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
// SPDX-License-Identifier: GPL-2.0-or-later
// Linux native CPU fault-handling policy smoke test. Uses simulated signals,
// not guest code or host timestamp faulting.
#include <cassert>
#include <csignal>
#include <cstdio>

#if defined(__linux__) && defined(__x86_64__)
namespace {
volatile sig_atomic_t depth = 0;
volatile sig_atomic_t greatest_depth = 0;
volatile sig_atomic_t count = 0;
volatile sig_atomic_t callback_masked = 0;

void NestedFault(int, siginfo_t*, void*) {
    depth = depth + 1;
    count = count + 1;
    if (depth > greatest_depth) {
        greatest_depth = depth;
    }
    sigset_t blocked{};
    if (sigprocmask(SIG_SETMASK, nullptr, &blocked) == 0 &&
        sigismember(&blocked, SIGUSR1) == 1) {
        callback_masked = 1;
    }
    if (depth == 1 && count == 1) {
        // Simulate a host timestamp fault while processing a guest fault.
        raise(SIGSEGV);
    }
    depth = depth - 1;
}
} // namespace
#endif

int main() {
#if defined(__linux__) && defined(__x86_64__)
    struct sigaction action{}, original{};
    action.sa_sigaction = NestedFault;
    action.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_NODEFER;
    sigemptyset(&action.sa_mask);
    sigaddset(&action.sa_mask, SIGUSR1);
    assert(sigaction(SIGSEGV, &action, &original) == 0);
    assert(raise(SIGSEGV) == 0);
    assert(sigaction(SIGSEGV, &original, nullptr) == 0);
    assert(count == 2);
    assert(greatest_depth == 2);
    assert(callback_masked == 1);
    std::puts("NESTED_CPU_SIGNAL_POLICY=PASS; SA_NODEFER=REENTRANT; SIGUSR1=MASKED");
#else
    std::puts("NESTED_CPU_SIGNAL_POLICY=SKIPPED_PLATFORM");
#endif
    return 0;
}
