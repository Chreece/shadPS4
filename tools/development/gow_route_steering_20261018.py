#!/usr/bin/env python3
"""Pure controller route scheduler for GoW intro: pulse right, recenter left.

No screenshots, no game memory inspection, no network. A caller supplies a
keyboard object exposing press(key: str, down: bool). Per-phase transitions
are logged and XTest keys are released by both the route and caller finally.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

# Durations are WALL CLOCK after first verified active gameplay, NOT after
# emulator launch or after pressing Continue. This avoids holding W+D during
# load when movement controls may not yet be processed.
#
# W only gets caught at the early obstacle; W+D forever ends at the right wall.
# Short diagonal corrections rather than holding right indefinitely.
PHASES = (
    (3.0, "wd", "RIGHT_PASS"),
    (8.0, "w", "STRAIGHT"),
    (2.5, "wa", "RE_CENTER_LEFT"),
    (8.0, "w", "STRAIGHT"),
    (3.0, "wd", "RIGHT_CORRECT"),
    (8.0, "w", "STRAIGHT"),
    (2.5, "wa", "LEFT_RECOVERY"),
    (8.0, "w", "STRAIGHT"),
)
CYCLE = sum(duration for duration, _, _ in PHASES)
MAX_STEER_SECONDS = 115.0


@dataclass(frozen=True)
class Phase:
    index: int
    name: str
    keys: frozenset[str]
    cycle_index: int


def phase_at(elapsed: float, forward: str = "w",
             right: str = "d", left: str = "a") -> Phase:
    if elapsed < 0:
        raise ValueError("NEGATIVE_STEERING_TIME")
    if len({forward, right, left}) != 3:
        raise ValueError("DUPLICATE_STEERING_KEYS")
    within = elapsed % CYCLE
    cycle_index = int(elapsed // CYCLE)
    for i, (seconds, key_spec, name) in enumerate(PHASES):
        if within < seconds:
            letters = {
                'w': forward, 'd': right, 'a': left
            }
            return Phase(i, name,
                         frozenset(letters[ch] for ch in key_spec), cycle_index)
        within -= seconds
    raise AssertionError("PHASE_SCHEDULE_INCOMPLETE")


class RouteDriver:
    def __init__(self, forward="w", right="d", left="a"):
        if len({forward, right, left}) != 3:
            raise ValueError("DUPLICATE_STEERING_KEYS")
        self.forward = forward
        self.right = right
        self.left = left
        self.held: set[str] = set()
        self.current = None
        self.history = []

    def update(self, seconds: float, keyboard):
        if seconds > MAX_STEER_SECONDS:
            raise TimeoutError("ROUTE_EXHAUSTED_115_SECONDS_UNCONFIRMED_PROGRESS")
        phase = phase_at(seconds, self.forward, self.right, self.left)
        key = (phase.cycle_index, phase.index)
        if key == self.current:
            return None
        # Release first so A and D are NEVER held simultaneously. Keep W
        # continuously pressed across phases where possible.
        newkeys = set(phase.keys)
        for old in (self.right, self.left, self.forward):
            if old in self.held and old not in newkeys:
                keyboard.press(old, False)
                self.held.discard(old)
        for new in (self.forward, self.right, self.left):
            if new in newkeys and new not in self.held:
                keyboard.press(new, True)
                self.held.add(new)
        self.current = key
        item = {"action": "STEERING_PHASE", "phase": phase.name,
                "cycle": phase.cycle_index, "phase_index": phase.index,
                "t_route": round(seconds, 3), "keys": sorted(self.held)}
        self.history.append(item)
        return item

    def release_all(self, keyboard):
        failures = []
        for key in (self.left, self.right, self.forward):
            if key in self.held:
                try:
                    keyboard.press(key, False)
                    self.held.discard(key)
                except Exception as e:
                    failures.append(str(e))
        if failures:
            raise RuntimeError("UNABLE_TO_RELEASE_ROUTE_KEYS=" + ";".join(failures))


def self_test():
    class FakeKeyboard:
        def __init__(self):
            self.held = set()
            self.events = []
        def press(self, k, down):
            self.events.append((k, down))
            if down:
                self.held.add(k)
            else:
                assert k in self.held
                self.held.remove(k)

    k = FakeKeyboard()
    route = RouteDriver()
    count = 0
    for idx in range(0, 1150):
        t = idx / 10
        event = route.update(t, k)
        assert route.held == k.held
        assert ("a" not in k.held or "d" not in k.held)
        assert "w" in k.held
        if event:
            count += 1
            assert event["keys"] == sorted(k.held)
    assert 15 <= count <= 30, count
    # Each cycle MUST recenter left and NEVER hold diagonal to right forever.
    assert sum(e["phase"] == "RE_CENTER_LEFT" for e in route.history) >= 2
    route.release_all(k)
    assert not k.held
    for t, expected in ((0, "RIGHT_PASS"), (3, "STRAIGHT"),
                        (11, "RE_CENTER_LEFT"), (13.5, "STRAIGHT"),
                        (21.5, "RIGHT_CORRECT"), (24.5, "STRAIGHT"),
                        (32.5, "LEFT_RECOVERY"), (35, "STRAIGHT"),
                        (43, "RIGHT_PASS")):
        assert phase_at(t).name == expected, (t, phase_at(t).name, expected)
    try:
        route.update(MAX_STEER_SECONDS + 1, k)
    except TimeoutError:
        pass
    else:
        raise AssertionError("ROUTE_TIMEOUT_MISSING")
    print("GOW_ROUTE_STEERING_SELFTEST=PASS")
    print("FORWARD_ALWAYS_HELD_DURING_ROUTE=PASS")
    print("LEFT_RIGHT_NEVER_CONCURRENT=PASS")
    print("WALL_RECOVERY_PHASES=PASS")
    print("ROUTE_STOP_AT_115_SECONDS=PASS")
    print("KEYS_RELEASED_ON_COMPLETION=PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true", required=True)
    opts = parser.parse_args()
    self_test()
