#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Capture session supervision and recover only isolated affinity test processes."""

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tarfile
import tempfile
import time


BINARY_SHA256 = "f26c7cbed2369fa1db0c4bf76c6364629601461fa4d616dadadebb711217eb17"
HELPER_HASHES = {
    "d768269f5ae77b212a35316fc6e04e28ec569e3a5980d909251a0b8936c3d79a",
    "62faa452274c5eef7866b641d48b69c332c8c324e0a3ceac13bf2c6966d85908",
    "c6aedd5e516cafc70526b3ff10d392fe096fe6451046df2b3cd5444790f00b51",
    "b844e26cf893999c0b86bc1e0e0fc42789c36f1d74d24664eb64912495a5332e",
}
UNITS = ["sunshine.service", "sunshine-session-supervisor.service",
         "sunshine-esde-idle-guard.service", "sunshine-display-watchdog.service"]


def say(value):
    print(value, flush=True)


def redact(value):
    value = re.sub(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", r"\1<redacted>@", value)
    value = re.sub(r"(?i)(authorization\s*[:=]\s*)([^\r\n]+)", r"\1<redacted>", value)
    return re.sub(r'''(?im)(\b[\w]*(?:password|passwd|access_token|api_key|secret)[\w]*["']?\s*[:=]\s*)(["'])(.*?)\2''',
                  r'\1"<redacted>"', value)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_process(proc):
    fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
    result = {"pid": int(proc.name), "ppid": int(fields[1]), "group": int(fields[2]),
              "session": int(fields[3]), "state": fields[0], "start": fields[19],
              "uid": proc.stat().st_uid}
    for name in ("exe", "cwd", "comm", "cmdline", "cgroup", "wchan"):
        try:
            if name in {"exe", "cwd"}:
                result[name] = os.readlink(proc / name)
            elif name == "cmdline":
                result["args"] = [s.decode(errors="replace") for s in
                                  (proc / name).read_bytes().split(b"\0") if s]
            else:
                result[name] = (proc / name).read_text(errors="replace").strip()
        except OSError as error:
            result[name + "_error"] = str(error)
    return result


def process_table():
    result = {}
    for proc in Path("/proc").iterdir():
        if proc.name.isdigit():
            try:
                item = read_process(proc)
                result[item["pid"]] = item
            except (OSError, ValueError, IndexError):
                pass
    return result


def related_processes(table):
    selected = set()
    for pid, item in table.items():
        identity = " ".join([item.get("exe", ""), item.get("comm", ""), *item.get("args", [])])
        if re.search(r"shadps4|sunshine|(?:^|[/ ])es-de(?:$|[ /])|shadps4-esde|emulator-session|affinity-pes-", identity, re.I):
            selected.add(pid)
    for pid in list(selected):
        seen = set()
        while pid in table and pid not in seen:
            seen.add(pid)
            selected.add(pid)
            pid = table[pid]["ppid"]
    return [table[pid] for pid in sorted(selected)]


def owned_test(item, cache, binary):
    if item["uid"] != os.getuid() or item["state"] == "Z":
        return None
    cwd = Path(item.get("cwd", "/"))
    if (item.get("exe") == str(binary) and cwd.parent == cache and
            cwd.name.startswith("pes-runtime-") and (cwd / "user").is_dir()):
        try:
            if digest(binary) == BINARY_SHA256:
                return "candidate"
        except OSError:
            pass
    args = item.get("args", [])
    if len(args) == 2 and Path(item.get("exe", "")).name.startswith("python"):
        script = Path(args[1])
        if (script.name == "run.py" and script.parent.parent == Path("/tmp") and
                script.parent.name.startswith("affinity-pes-")):
            if not script.exists():
                return "helper_with_removed_bootstrap"
            try:
                if digest(script) in HELPER_HASHES:
                    return "helper"
            except OSError:
                pass
    return None


def signal_test(item, sig, cache, binary):
    try:
        fd = os.pidfd_open(item["pid"])
        try:
            current = read_process(Path("/proc") / str(item["pid"]))
            if current["start"] != item["start"] or owned_test(current, cache, binary) is None:
                return False
            signal.pidfd_send_signal(fd, sig)
            return True
        finally:
            os.close(fd)
    except ProcessLookupError:
        return False


def main():
    home = Path.home()
    cache = home / ".cache/shadps4-affinity-20261007"
    binary = cache / "build-82d07380b609/shadps4"
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-session-recovery-", dir=home))
    actions = []

    def write(name, value):
        target = evidence / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(redact(value))

    def copy_tail(source, name, limit=4 * 1024 * 1024):
        if not source.is_file():
            return
        try:
            with source.open("rb") as stream:
                size = source.stat().st_size
                stream.seek(max(0, size - limit))
                data = stream.read(limit).decode(errors="replace")
            write(name, ("[tail of file]\n" if size > limit else "") + data)
        except OSError as error:
            write(name + ".error", str(error))

    def command(name, args):
        try:
            result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=12, text=True, errors="replace")
            write(name, f"returncode={result.returncode}\n" + result.stdout[-4 * 1024 * 1024:])
        except (OSError, subprocess.TimeoutExpired) as error:
            write(name, str(error))

    def collect_test_logs():
        folders = sorted((p for p in home.glob("shadps4-affinity-pes-*") if p.is_dir()),
                         key=lambda p: p.stat().st_mtime, reverse=True)[:5]
        for folder in folders:
            for path in folder.iterdir():
                if path.suffix in {".json", ".jsonl", ".log", ".txt"}:
                    copy_tail(path, "tests/" + folder.name + "/" + path.name)
        for runtime in cache.glob("pes-runtime-*"):
            for path in (runtime / "user/log").glob("*"):
                copy_tail(path, "runtime-logs/" + runtime.name + "/" + path.name)

    try:
        say("CAPTURING=Processes, guard scripts/services, and unfinished affinity logs")
        initial = process_table()
        write("processes-before.json", json.dumps(related_processes(initial), indent=2))
        collect_test_logs()
        scripts = [home / ".local/bin/shadps4-esde", home / ".local/bin/sunshine-session-preflight",
                   home / ".local/bin/sunshine-es-min", home / ".local/lib/shadps4-session-guard/guard.py",
                   home / ".local/lib/emulator-session/cleanup.py",
                   Path("/usr/local/sbin/sunshine-session-supervisor")]
        for index, path in enumerate(scripts):
            copy_tail(path, f"guard-scripts/{index}-{path.name}")
        properties = "Id,ActiveState,SubState,MainPID,ControlGroup,FragmentPath,DropInPaths,NRestarts,ExecMainStatus"
        for mode, flag in [("system", []), ("user", ["--user"])]:
            say("READING_GUARD_SERVICES=" + mode)
            command(f"{mode}-services.txt", ["systemctl", *flag, "show", "--no-pager", "--property=" + properties, *UNITS])
            command(f"{mode}-units.txt", ["systemctl", *flag, "cat", "--no-pager", *UNITS])
            command(f"{mode}-journal.txt", ["journalctl", *flag, "--no-pager", "--since=-30min", "-n", "350",
                                              *[value for unit in UNITS for value in ("-u", unit)]])
        lock_names = [("guard", home / ".local/state/shadps4-session-guard"),
                      ("cleanup", home / ".local/state/emulator-session")]
        for label, folder in lock_names:
            for path in folder.glob("*"):
                if path.suffix in {".json", ".lock", ".pid", ".log"}:
                    copy_tail(path, "guard-state/" + label + "-" + path.name, 128 * 1024)
        write("kernel-locks.txt", Path("/proc/locks").read_text())
        targets = []
        for item in initial.values():
            kind = owned_test(item, cache, binary)
            if kind:
                targets.append({**item, "test_kind": kind})
        write("owned-test-processes.json", json.dumps(targets, indent=2))
        say(f"STOPPING_TEST_ONLY={len(targets)} verified affinity test processes; ES-DE and installed emulators are untouched")
        for item in sorted(targets, key=lambda i: i["test_kind"] == "candidate"):
            actions.append({"pid": item["pid"], "signal": "TERM", "sent": signal_test(item, signal.SIGTERM, cache, binary)})
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if not any(owned_test(item, cache, binary) for item in process_table().values()):
                break
            time.sleep(0.2)
        for item in process_table().values():
            if owned_test(item, cache, binary):
                actions.append({"pid": item["pid"], "signal": "KILL", "sent": signal_test(item, signal.SIGKILL, cache, binary)})
        time.sleep(0.3)
        collect_test_logs()
        final = process_table()
        write("processes-after.json", json.dumps(related_processes(final), indent=2))
        remaining = [item["pid"] for item in final.values() if owned_test(item, cache, binary)]
        say("TEST_PROCESSES_REMAINING=" + str(remaining))
    except Exception as error:
        write("error.txt", str(error))
        say("CAPTURE_ERROR=" + str(error))
    finally:
        write("recovery-actions.json", json.dumps(actions, indent=2))
        archive_path = evidence.with_suffix(".tar.gz")
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(evidence, arcname=evidence.name)
        say("UPLOAD_ONLY=" + str(archive_path))
        say("No game was launched. Your SSH session stays open.")


if __name__ == "__main__":
    main()
