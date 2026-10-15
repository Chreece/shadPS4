// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <algorithm>
#include <cstddef>
#include <cstring>
#include <functional>
#include <memory>
#include <mutex>
#include <vector>

namespace Libraries::AudioOut {

// Acquire every port mutex only while all ports are simultaneously available.
// A producer must never wait for one port while holding another port's mutex:
// that would prevent the second port's output worker from draining its buffer.
// Lock in a stable order to avoid deadlocks among concurrent batch producers.
//
// Returns true with every mutex still locked in `locks`. The caller can then
// publish all buffers atomically. Returns false if any port is closing, with
// no mutexes held.
template <typename Port>
bool WaitForAudioPortsReady(const std::vector<std::shared_ptr<Port>>& ports,
                            std::vector<std::unique_lock<std::mutex>>& locks) {
    std::vector<Port*> ordered;
    ordered.reserve(ports.size());
    for (const auto& port : ports) {
        ordered.push_back(port.get());
    }
    std::sort(ordered.begin(), ordered.end(), std::less<Port*>{});

    for (;;) {
        locks.clear();
        for (Port* port : ordered) {
            locks.emplace_back(port->mutex);
        }
        if (std::ranges::any_of(ports, [](const auto& p) { return p->closing; })) {
            locks.clear();
            return false;
        }

        auto blocked = std::ranges::find_if(ports, [](const auto& p) {
            return p->output_ready;
        });
        if (blocked == ports.end()) {
            return true;
        }

        // Release ALL port mutexes before waiting for the selected port.
        // Otherwise a different output worker can never make progress.
        const std::shared_ptr<Port> waiting = *blocked;
        locks.clear();
        std::unique_lock lock{waiting->mutex};
        waiting->output_cv.wait(lock, [&] {
            return !waiting->output_ready || waiting->closing;
        });
    }
}

// Copy a completed guest buffer to backend-owned staging while the producer
// buffer is protected. Release the guest-facing mutex before any backend
// operation, which may wait for host audio pacing and must not block the guest
// from preparing its next buffer. The worker owns the staging buffer; this
// function never changes its length, volume, channel format or sample data.
template <typename Port>
bool StageAudioOutputBuffer(Port& port, void* destination, std::size_t bytes) {
    std::unique_lock lock{port.mutex};
    if (!port.impl || port.closing || !port.output_ready) {
        return false;
    }
    std::memcpy(destination, port.output_buffer, bytes);
    port.output_ready = false;
    lock.unlock();
    port.output_cv.notify_all();
    return true;
}

} // namespace Libraries::AudioOut
