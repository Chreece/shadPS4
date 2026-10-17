// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <algorithm>
#include <span>
#include <vector>

namespace Input {

// Keep keyboard input available when the last real gamepad disappears.
// Never replace a physical pad or re-connect an already connected virtual pad.
[[nodiscard]] constexpr bool ShouldRestorePrimaryVirtualController(
    bool any_gamepad_available, bool primary_connected) noexcept {
    return !any_gamepad_available && !primary_connected;
}

// SDL device discovery is event-driven, but Linux uinput/udev virtual pads
// (including streaming devices) may arrive between SDL events. Compare the
// current set of joystick instance IDs periodically to detect a missed add,
// remove, or reconnect without scanning/reopening unchanged devices.
//
// Generic and SDL-independent so the decision is regression-testable.
template <typename InstanceId>
class GamepadHotplugSnapshot {
public:
    [[nodiscard]] bool Update(std::span<const InstanceId> available, bool force = false) {
        std::vector<InstanceId> current{available.begin(), available.end()};
        std::sort(current.begin(), current.end());
        current.erase(std::unique(current.begin(), current.end()), current.end());
        if (!initialized || force || current != previous) {
            previous = std::move(current);
            initialized = true;
            return true;
        }
        return false;
    }

    [[nodiscard]] bool Initialized() const noexcept {
        return initialized;
    }

private:
    std::vector<InstanceId> previous;
    bool initialized{};
};

} // namespace Input
