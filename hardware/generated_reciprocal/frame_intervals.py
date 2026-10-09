# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

import math
import statistics


def summarize(path, started_ns, width=15):
    groups = {}
    previous = 0
    with path.open() as stream:
        for line in stream:
            try:
                timestamp, duration = line.strip().split(",")
                timestamp, duration = int(timestamp), float(duration)
            except ValueError:
                raise RuntimeError("Malformed frame-time sample") from None
            if timestamp <= previous or not math.isfinite(duration) or duration <= 0:
                raise RuntimeError("Invalid frame-time sample")
            previous = timestamp
            elapsed = (timestamp - started_ns) / 1e9
            if elapsed < 0:
                raise RuntimeError("Frame timestamp precedes launch")
            group = int(elapsed // width)
            groups.setdefault(group, []).append(duration)
    if not groups:
        raise RuntimeError("No frame-time samples were recorded")
    intervals = []
    for group, times in sorted(groups.items()):
        ordered = sorted(times)
        intervals.append({"start_seconds": group * width, "end_seconds": (group + 1) * width,
                          "frames": len(times), "fps": len(times) / sum(times),
                          "median_ms": statistics.median(times) * 1000,
                          "p95_ms": ordered[math.ceil(len(times) * .95) - 1] * 1000,
                          "p99_ms": ordered[math.ceil(len(times) * .99) - 1] * 1000})
    return {"interpretation": "Intervals include menus, loading and gameplay; compare only visually matched scenes.",
            "intervals": intervals}
