// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#include <array>
#include <cstdio>
#include "input/gamepad_hotplug_snapshot.h"

int main() {
    Input::GamepadHotplugSnapshot<int> snap;
    const std::array<int,0> empty{};
    const std::array<int,1> padA{101};
    const std::array<int,2> two{101,202};
    const std::array<int,2> reordered{202,101};
    const std::array<int,3> duplicates{101,202,202};
    const std::array<int,1> padB{202};
    const std::array<int,1> newDeviceId{303};

    if (snap.Initialized()) return 1;
    if (!snap.Update(empty)) return 2;         // first scan even when no pads
    if (!snap.Initialized()) return 3;
    if (snap.Update(empty)) return 4;          // no wakeups/reopen on idle
    if (!snap.Update(padA)) return 5;          // Sunshine virtual pad appears
    if (snap.Update(padA)) return 6;           // no repeated open
    if (!snap.Update(two)) return 7;           // second Moonlight controller
    if (snap.Update(reordered)) return 8;      // SDL enumeration order unstable
    if (snap.Update(duplicates)) return 9;     // duplicate IDs not a new device
    if (!snap.Update(padB)) return 10;         // first user disconnects
    if (!snap.Update(newDeviceId)) return 11;  // same port, new SDL instance
    if (!snap.Update(empty)) return 12;        // all pads gone; fallback
    if (snap.Update(empty)) return 13;
    if (!snap.Update(empty,true)) return 14;   // explicit SDL add/remove event
    if (snap.Update(empty)) return 15;
    std::puts("GAMEPAD_HOTPLUG=PASS late_attach=YES reconnect=YES "
              "two_controllers=YES order_independent=YES idle_no_reopen=YES");
    return 0;
}
