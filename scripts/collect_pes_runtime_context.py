#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read existing PES output and procfs activity; never attach or signal a process."""

import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import time

import trace_video_progress as trace

LOG_NAMES = {"shad_log.txt", "shadps4.log", "emulator.log", "CUSA18676.log"}
LOG_LIMIT = 16 * 1024 * 1024


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def copy_regular_log(source, destination, *, limit=LOG_LIMIT):
    """Never consume a pipe/terminal or disturb a writer's current file offset."""
    before = source.stat()
    if not stat.S_ISREG(before.st_mode):
        return {"status": "not_regular_not_read"}
    descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    try:
        opened = os.fstat(descriptor)
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or
                not stat.S_ISREG(opened.st_mode)):
            return {"status": "source_changed_not_read"}
        size = opened.st_size
        ranges = [(0, min(size, limit))]
        if size > limit:
            ranges = [(0, limit // 2), (size - limit // 2, limit // 2)]
        parts = []
        for index, (offset, count) in enumerate(ranges):
            path = destination.with_name(destination.name + f".{index}.log")
            copied = 0
            with path.open("wb") as output:
                while copied < count:
                    data = os.pread(descriptor, min(1024 * 1024, count - copied), offset + copied)
                    if not data:
                        break
                    output.write(data)
                    copied += len(data)
            parts.append({"file": path.name, "offset": offset, "bytes": copied})
        after = os.fstat(descriptor)
        return {"status": "captured", "size_at_open": size, "size_after": after.st_size,
                "mtime_ns": opened.st_mtime_ns, "truncated": size > limit,
                "changed_during_copy": after.st_mtime_ns != opened.st_mtime_ns,
                "device": opened.st_dev, "inode": opened.st_ino, "parts": parts}
    finally:
        os.close(descriptor)


def collect_outputs(proc, work):
    records = []
    for fd in sorted((proc / "fd").iterdir(), key=lambda p: int(p.name)):
        try:
            target = str(fd.readlink())
            record = {"fd": int(fd.name), "target": target}
            if fd.name in ("1", "2") or Path(target).name in LOG_NAMES:
                try:
                    record.update(copy_regular_log(fd, work / ("fd-" + fd.name)))
                except OSError as error:
                    record.update(status="unavailable", error=str(error))
            records.append(record)
        except OSError as error:
            records.append({"fd": int(fd.name), "status": "unavailable", "error": str(error)})
    save_json(work / "output-sources.json", records)
    return records


def read_fields(directory, names):
    result = {}
    for name in names:
        try:
            result[name] = (directory / name).read_text()
        except OSError as error:
            result[name] = {"error": str(error)}
    return result


def sample(proc):
    result = {"monotonic": time.monotonic(), "time_unix": time.time(),
              **read_fields(proc, ("stat", "status", "io")), "threads": {}}
    for thread in (proc / "task").iterdir():
        result["threads"][thread.name] = read_fields(thread, ("comm", "stat", "wchan"))
    return result


def collect(identity, work):
    proc = Path("/proc") / str(identity["pid"])
    metadata = {"identity": identity, "clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
                "errors": [], "debugger_used": False}
    try:
        for index in range(3):
            if (proc / "stat").read_text().rsplit(")", 1)[1].split()[19] != identity["start_ticks"]:
                raise RuntimeError("PES process identity changed; stopped reading it")
            save_json(work / f"sample-{index}.json", sample(proc))
            if index < 2:
                time.sleep(1)
        records = collect_outputs(proc, work)
        (work / "maps.txt").write_text((proc / "maps").read_text())
        for record in records:
            if "status" in record:
                print(f"OUTPUT_FD={record['fd']} STATUS={record['status']} "
                      f"SOURCE={record.get('target', '?')}", flush=True)
    except (Exception, KeyboardInterrupt) as error:
        metadata["errors"].append(type(error).__name__ + ": " + str(error))
        raise
    finally:
        metadata["target_after"] = trace.inspect_target(identity)
        save_json(work / "context.json", metadata)
        archive = work.with_suffix(".tar.gz")
        with tarfile.open(archive, "w:gz") as output:
            for path in sorted(work.iterdir()):
                output.add(path, arcname=work.name + "/" + path.name, recursive=False)
        print("PES_CONTEXT_ARCHIVE=" + str(archive), flush=True)


def main():
    if os.geteuid() == 0:
        raise RuntimeError("Run as chreece without sudo")
    if sys.argv[1:]:
        raise RuntimeError("No arguments expected")
    identity = trace.find_process()
    work = Path(tempfile.mkdtemp(prefix="shadps4-pes-context-", dir=Path.home()))
    print("Reading current PES logs and three activity samples; keep PES open.", flush=True)
    collect(identity, work)


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print("PES_CONTEXT_ERROR=" + (str(error) or "Interrupted"), flush=True)
    finally:
        print("SSH session preserved; returning to your existing shell.", flush=True)
