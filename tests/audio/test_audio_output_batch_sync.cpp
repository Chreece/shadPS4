// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Standalone host-concurrency regression for the actual multi-port audio
// synchronization helper used by sceAudioOutOutputs.
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <future>
#include <iostream>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

#include "core/libraries/audio/audioout_sync.h"

using namespace std::chrono_literals;

struct TestAudioPort {
    std::mutex mutex;
    std::condition_variable_any output_cv;
    bool output_ready{false};
    bool closing{false};
};

using Port = std::shared_ptr<TestAudioPort>;
using Locks = std::vector<std::unique_lock<std::mutex>>;

static void Require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "AUDIO_MULTI_PORT_TEST_FAIL " << message << '\n';
        std::terminate();
    }
}

static void Drain(TestAudioPort& port) {
    {
        std::lock_guard lock(port.mutex);
        port.output_ready = false;
    }
    port.output_cv.notify_all();
}

static void MultipleBusyPortsCanBothDrain() {
    auto a = std::make_shared<TestAudioPort>();
    auto b = std::make_shared<TestAudioPort>();
    a->output_ready = true;
    b->output_ready = true;

    auto producer = std::async(std::launch::async, [a,b] {
        Locks locks;
        const bool ready =
            Libraries::AudioOut::WaitForAudioPortsReady(std::vector<Port>{a,b},locks);
        if (!ready || locks.size()!=2 || !locks[0].owns_lock() || !locks[1].owns_lock()) {
            return false;
        }
        a->output_ready = true;
        b->output_ready = true;
        locks.clear();
        return true;
    });

    std::this_thread::sleep_for(25ms);
    // First output worker drains A; then B must be free to drain as well.
    // The previous implementation waited on B while retaining B's mutex.
    Drain(*a);
    std::this_thread::sleep_for(25ms);
    Drain(*b);
    Require(producer.wait_for(1000ms) == std::future_status::ready,
            "producer remained blocked after both output workers drained");
    Require(producer.get(),"producer failed to atomically publish two buffers");
    Require(a->output_ready && b->output_ready,
            "producer did not publish both audio buffers");
}

static void ReversePortOrderCannotDeadlock() {
    auto a = std::make_shared<TestAudioPort>();
    auto b = std::make_shared<TestAudioPort>();
    auto send=[&](std::vector<Port> order){
        Locks locks;
        if (!Libraries::AudioOut::WaitForAudioPortsReady(order,locks)) return false;
        if (locks.size()!=2) return false;
        std::this_thread::sleep_for(3ms);
        return true;
    };
    auto left=std::async(std::launch::async,send,std::vector<Port>{a,b});
    auto right=std::async(std::launch::async,send,std::vector<Port>{b,a});
    Require(left.wait_for(1000ms)==std::future_status::ready,
            "forward producer deadlocked");
    Require(right.wait_for(1000ms)==std::future_status::ready,
            "reverse producer deadlocked");
    Require(left.get() && right.get(),"reverse-order atomic locking failed");
}

static void ClosingPortUnblocksDrainWait() {
    auto a=std::make_shared<TestAudioPort>();
    auto b=std::make_shared<TestAudioPort>();
    a->output_ready=true;
    auto future=std::async(std::launch::async,[&]{
        Locks locks;
        const bool ready =
            Libraries::AudioOut::WaitForAudioPortsReady(std::vector<Port>{a,b},locks);
        return !ready && locks.empty();
    });
    std::this_thread::sleep_for(25ms);
    {
        std::lock_guard lock(a->mutex);
        a->closing=true;
    }
    a->output_cv.notify_all();
    Require(future.wait_for(1000ms)==std::future_status::ready,
            "closed audio port was not released");
    Require(future.get(),"closed audio port left mutexes held");
}

int main() {
    MultipleBusyPortsCanBothDrain();
    ReversePortOrderCannotDeadlock();
    ClosingPortUnblocksDrainWait();
    std::cout << "AUDIO_MULTI_PORT_SYNC=PASS cases=3" << std::endl;
}
