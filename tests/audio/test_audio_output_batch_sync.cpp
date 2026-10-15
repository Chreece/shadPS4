// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

// Standalone host-concurrency regression for the actual multi-port audio
// synchronization helper used by sceAudioOutOutputs.
#include <array>
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
    std::mutex backend_mutex;
    std::condition_variable_any output_cv;
    std::shared_ptr<int> impl{std::make_shared<int>(1)};
    std::array<std::uint8_t, 16> samples{};
    void* output_buffer{samples.data()};
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

static void GuestCanSubmitNextBufferWhileBackendIsSlow() {
    auto port = std::make_shared<TestAudioPort>();
    port->samples[0] = 11;
    port->samples[1] = 22;
    port->output_ready = true;
    std::promise<void> backend_started;
    auto future_started = backend_started.get_future();

    // This worker uses the identical staging operation as AudioOutputThread.
    auto worker = std::async(std::launch::async, [&] {
        std::array<std::uint8_t, 16> staged{};
        if (!Libraries::AudioOut::StageAudioOutputBuffer(
                *port, staged.data(), staged.size())) {
            return false;
        }
        {
            std::lock_guard backend_lock{port->backend_mutex};
            backend_started.set_value();
            // SDL pacing may block here, but the producer must not be held.
            std::this_thread::sleep_for(300ms);
        }
        return staged[0] == 11 && staged[1] == 22;
    });
    Require(future_started.wait_for(1000ms)==std::future_status::ready,
            "slow backend never entered playback");

    auto producer = std::async(std::launch::async, [&] {
        std::unique_lock lock{port->mutex};
        port->output_cv.wait(lock, [&] {
            return !port->output_ready || port->closing;
        });
        if (port->closing) return false;
        port->samples[0] = 33;
        port->samples[1] = 44;
        port->output_ready = true;
        return true;
    });
    Require(producer.wait_for(150ms)==std::future_status::ready,
            "guest submission was blocked by slow host playback");
    Require(producer.get(), "guest could not prepare next audio block");
    Require(worker.wait_for(1000ms)==std::future_status::ready,
            "backend playback remained blocked");
    Require(worker.get(), "producer overwrite corrupted already-staged samples");
    Require(port->output_ready && port->samples[0] == 33 && port->samples[1] == 44,
            "next queued audio block was lost");
}

static void ClosingPortDoesNotStageBuffer() {
    TestAudioPort port;
    port.output_ready = true;
    port.closing = true;
    std::array<std::uint8_t, 16> destination{};
    Require(!Libraries::AudioOut::StageAudioOutputBuffer(
                port, destination.data(), destination.size()),
            "closed port accepted a new audio buffer");
    Require(port.output_ready, "closed port unexpectedly consumed guest audio");
}

int main() {
    MultipleBusyPortsCanBothDrain();
    ReversePortOrderCannotDeadlock();
    ClosingPortUnblocksDrainWait();
    GuestCanSubmitNextBufferWhileBackendIsSlow();
    ClosingPortDoesNotStageBuffer();
    std::cout << "AUDIO_MULTI_PORT_SYNC=PASS cases=5" << std::endl;
}
