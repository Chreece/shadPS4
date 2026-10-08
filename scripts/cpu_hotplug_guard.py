#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Temporarily offline one CPU, with an independent restoration watchdog."""

import fcntl
import json
import os
from pathlib import Path
import select
import signal
import sys
import time

CPU_ROOT = Path("/sys/devices/system/cpu")
LOCK = "/run/lock/shadps4-cpu-hotplug.lock"
WATCHDOG_TIMEOUT = 10
OWNER_TIMEOUT = 15


def cpulist(value):
    result = set()
    for item in value.strip().split(","):
        first, _, last = item.partition("-")
        result.update(range(int(first), int(last or first) + 1))
    return result


def restore(path):
    last_error = None
    for _ in range(3):
        try:
            if path.read_text().strip() != "1":
                path.write_text("1\n")
            if path.read_text().strip() == "1":
                return {"restored": True}
        except OSError as error:
            last_error = str(error)
        time.sleep(0.2)
    return {"restored": False, "error": last_error or "CPU did not return online"}


def interrupted(signum, frame):
    raise SystemExit(128 + signum)


def watchdog(path, heartbeat, result):
    os.setsid()
    os.close(0)
    os.close(1)
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, signal.SIG_IGN)
    try:
        os.write(result, b"ready\n")
        while select.select([heartbeat], [], [], WATCHDOG_TIMEOUT)[0]:
            if not os.read(heartbeat, 4096):
                break
    finally:
        outcome = restore(path)
        try:
            os.write(result, (json.dumps(outcome) + "\n").encode())
        except OSError:
            pass
        os.close(result)
        os._exit(0 if outcome["restored"] else 1)


def reply(**values):
    print(json.dumps(values), flush=True)


def serve(cpu):
    if os.geteuid() != 0 or cpu <= 0:
        raise RuntimeError("Root and a nonzero logical CPU are required")
    path = CPU_ROOT / f"cpu{cpu}" / "online"
    online = cpulist((CPU_ROOT / "online").read_text())
    if len(online) < 3 or cpu not in online or path.read_text().strip() != "1":
        raise RuntimeError("CPU must already be online, with at least two other CPUs online")
    controls = os.sched_getaffinity(0) - {cpu}
    if not controls:
        raise RuntimeError("The controller needs another permitted CPU")
    lock = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.sched_setaffinity(0, controls)
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    heartbeat_read, heartbeat_write = os.pipe()
    result_read, result_write = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(heartbeat_write)
        os.close(result_read)
        watchdog(path, heartbeat_read, result_write)
    os.close(heartbeat_read)
    os.close(result_write)
    try:
        if not select.select([result_read], [], [], 5)[0] or os.read(result_read, 6) != b"ready\n":
            raise RuntimeError("Restoration watchdog did not start")
        reply(event="ready", cpu=cpu, watchdog=child, online=sorted(online))
        pending = b""
        last_request = time.monotonic()
        cycles = 0
        while time.monotonic() - last_request < OWNER_TIMEOUT:
            os.write(heartbeat_write, b".")
            if not select.select([0], [], [], 1)[0]:
                continue
            chunk = os.read(0, 1024)
            if not chunk:
                break
            pending += chunk
            if len(pending) > 4096:
                raise RuntimeError("Unexpected controller input")
            while b"\n" in pending:
                request, pending = pending.split(b"\n", 1)
                last_request = time.monotonic()
                if request == b"close":
                    return
                if request not in (b"offline", b"online", b"ping"):
                    raise RuntimeError("Unknown hotplug command")
                if request == b"offline":
                    online = cpulist((CPU_ROOT / "online").read_text())
                    if cycles >= 4 or len(online) < 3 or path.read_text().strip() != "1":
                        raise RuntimeError("CPU offlining preconditions changed")
                    cycles += 1
                    path.write_text("0\n")
                    if path.read_text().strip() != "0":
                        raise RuntimeError("CPU did not go offline")
                elif request == b"online":
                    outcome = restore(path)
                    if not outcome["restored"]:
                        raise RuntimeError(outcome["error"])
                reply(event=request.decode(), cpu=cpu, state=path.read_text().strip(),
                      online=sorted(cpulist((CPU_ROOT / "online").read_text())))
    finally:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, signal.SIG_IGN)
        outcome = restore(path)
        os.close(heartbeat_write)
        if select.select([result_read], [], [], WATCHDOG_TIMEOUT + 5)[0]:
            message = os.read(result_read, 4096)
            if message:
                outcome["watchdog_result"] = message.decode(errors="replace").strip()
                os.waitpid(child, 0)
        os.close(result_read)
        reply(event="closed", cpu=cpu, **outcome)
        os.close(lock)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise RuntimeError("Usage: cpu_hotplug_guard.py CPU")
        serve(int(sys.argv[1]))
    except Exception as error:
        reply(event="error", error=str(error))
        raise SystemExit(1)
