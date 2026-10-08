#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Capture live PES thread stacks using the already tested CPU-ID candidate."""

import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

HERE = Path(__file__).resolve().parent
REVISION = "251278d933106cb139b9b9df61230e274e60e1d9"
HELPERS = {
    "affinity_game_profile.py": "211ec6d217af6d552f3b2409e65e1579ac2a4aaa930f568c9701ab8ae0414b9c",
    "affinity_game_guard.py": "f56530b095fc4cbfc8424a23225e6d955f984129cf2bf81f2642b926a9759c88",
}
ARTIFACTS = {
    "working-build-95b74819840d/shadps4": "d78c29f93102fa83cfb915a530c71b6a196e7811eeb5757a740427c624af078e",
    "working-build-95b74819840d/src/core/cpu_id_translation/libshadps4_cpu_id.so": "af4b6526a505bd124957584b5c8cb0dca29e85eb0d73ee1a4871f7d1da357861",
    "runtime-build-a522a5055820/bin64/drrun": "e58dcb8cdefec91d144c1a8fa79a7bd1b8a29969c4319f31df4df01644ef0b97",
    "runtime-build-a522a5055820/lib64/release/libdynamorio.so": "f7ab039e3585c78b2bc52bab57de1a61b8ac276ab8c0af52cf17c55b1f2a2233",
}
INSTALLED_SHA = "6bd789967f77e3e8eb315e5d7fde920928c6f6cc86f598d973f733e64a31bfd2"


def dependencies():
    for name, expected in HELPERS.items():
        path = HERE / name
        if path.exists():
            data = path.read_bytes()
        else:
            url = f"https://raw.githubusercontent.com/Chreece/shadPS4/{REVISION}/scripts/{name}"
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(256 * 1024 + 1)
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("Helper checksum mismatch: " + name)
        if not path.exists():
            path.write_bytes(data)
    sys.path.insert(0, str(HERE))
    return importlib.import_module("affinity_game_profile"), importlib.import_module("affinity_game_guard")


def read(path):
    try:
        return path.read_text(errors="replace")
    except OSError as error:
        return "UNAVAILABLE: " + str(error)


def start_time(pid):
    return (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[19]


def elf_base(path):
    # Subtract the ELF's preferred base as well, so non-PIE executables work too.
    with path.open("rb") as stream:
        header = stream.read(64)
        if header[:6] != b"\x7fELF\x02\x01":
            raise RuntimeError("Expected a little-endian ELF64 image: " + str(path))
        offset = struct.unpack_from("<Q", header, 32)[0]
        size, count = struct.unpack_from("<HH", header, 54)
        bases = []
        for index in range(count):
            stream.seek(offset + index * size)
            kind, flags, file_offset, address = struct.unpack("<IIQQ", stream.read(24))
            if kind == 1:
                bases.append(address - file_offset)
        return min(bases)


def capture_stack(info, folder, debugger, symbols=()):
    """A bounded read-only attach, with identity checked before and after attach."""
    folder.mkdir()
    pid = info["pid"]
    if start_time(pid) != str(info["start"]):
        raise RuntimeError("Process identity changed before stack capture")
    maps = read(Path("/proc") / str(pid) / "maps")
    (folder / "maps.txt").write_text(maps)
    commands = [
        "set pagination off", "set confirm off", "set debuginfod enabled off",
        "set auto-load off", "set may-call-functions off", "set print frame-arguments none",
        "handle SIGILL nostop noprint pass", "handle SIGSEGV nostop noprint pass",
        "handle SIGBUS nostop noprint pass", "python", "import gdb, pathlib",
        f"p = pathlib.Path('/proc/{pid}/stat')",
        "def identity(): return p.read_text().rsplit(')', 1)[1].split()[19]",
        f"if identity() != {str(info['start'])!r}: raise gdb.GdbError('PID changed before attach')",
        f"gdb.execute('attach {pid}')",
        f"if identity() != {str(info['start'])!r}:",
        "    gdb.execute('detach')", "    raise gdb.GdbError('PID changed after attach')", "end",
    ]
    # DynamoRIO's private loader is not automatically discovered by GDB.
    for path in symbols:
        bases = []
        for line in maps.splitlines():
            fields = line.split(None, 5)
            if len(fields) == 6 and fields[5] == str(path):
                bases.append(int(fields[0].split('-')[0], 16) - int(fields[2], 16))
        if bases:
            command = "add-symbol-file " + json.dumps(str(path)) + " -o " + hex(min(bases) - elf_base(path))
            commands += ["python", "try:", f"    gdb.execute({command!r})",
                         "except gdb.error as error: print('SYMBOL_WARNING:', error)", "end"]
    commands += ["info threads", "thread apply all bt 24",
                 "thread apply all info registers rip rsp rbp eflags",
                 "detach", "echo STACK_CAPTURE_OK\\n"]
    script = folder / "capture.gdb"
    script.write_text("\n".join(commands) + "\n")
    started = time.monotonic()
    with (folder / "stacks.txt").open("w") as output:
        result = subprocess.run(debugger + ["-nx", "-nh", "-batch", "-x", str(script)],
                                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                timeout=10)
    text = (folder / "stacks.txt").read_text(errors="replace")
    status = {"pid": pid, "start": str(info["start"]), "returncode": result.returncode,
              "seconds": time.monotonic() - started,
              "captured": result.returncode == 0 and "STACK_CAPTURE_OK" in text and "#0" in text}
    (folder / "result.json").write_text(json.dumps(status, indent=2) + "\n")
    return status


def debugger_preflight(evidence):
    gdb, timeout = shutil.which("gdb"), shutil.which("timeout")
    if not gdb or not timeout:
        raise RuntimeError("gdb and timeout are required for live stacks; launcher left untouched")
    command = [timeout, "--signal=INT", "--kill-after=1s", "6s", gdb]
    attempts = [command]
    if shutil.which("sudo"):
        attempts.append([shutil.which("sudo"), "-n"] + command)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    try:
        info = {"pid": child.pid, "start": start_time(child.pid)}
        for index, attempt in enumerate(attempts):
            result = capture_stack(info, evidence / ("debugger-preflight-" + str(index)), attempt)
            if result["captured"] and child.poll() is None:
                return attempt
        raise RuntimeError("Live stack attach preflight failed; launcher left untouched. Upload the archive")
    finally:
        child.kill()
        child.wait()


def monitor(work, evidence, games, debugger, symbols, stopped, outcome):
    started = None
    next_stack = 0
    failures = 0
    index = 0
    try:
        with (evidence / "thread-samples.jsonl").open("w") as output:
            while not stopped.wait(1):
                job_file = work / "CUSA18676-translated/job.json"
                if not job_file.exists():
                    continue
                job = json.loads(job_file.read_text())
                processes = games.owned(job)
                for info in processes:
                    proc = Path("/proc") / str(info["pid"])
                    # drrun briefly precedes the emulator; wait for the candidate mapping.
                    if str(symbols[0]) not in read(proc / "maps"):
                        continue
                    if started is None:
                        started = time.monotonic()
                        next_stack = started + 15
                    if not games.alive(info):
                        continue
                    threads = []
                    try:
                        tasks = list((proc / "task").iterdir())
                    except OSError:
                        continue
                    for task in tasks:
                        threads.append({"tid": int(task.name), **{
                            name: read(task / name) for name in ("comm", "stat", "wchan", "syscall")}})
                    output.write(json.dumps({"time": time.time(), "seconds": time.monotonic() - started,
                                             "pid": info["pid"], "threads": threads}) + "\n")
                    output.flush()
                    if time.monotonic() >= next_stack and failures < 2:
                        folder = evidence / ("stack-%03d" % index)
                        index += 1
                        try:
                            result = capture_stack(info, folder, debugger, symbols)
                        except OSError as error:
                            result = {"captured": False, "error": str(error)}
                        outcome.append(result)
                        failures = 0 if result["captured"] else failures + 1
                        games.profile.say("STACK_CAPTURE=" + ("saved" if result["captured"] else "incomplete"))
                        next_stack = time.monotonic() + 30
                        if failures == 2:
                            games.profile.say("STACK_CAPTURE_STOPPED=Two failed attaches; thread sampling continues")
    except Exception as error:
        outcome.append({"monitor_error": str(error)})
        games.profile.say("STACK_MONITOR_ERROR=" + str(error))


def cancel(signum, frame):
    raise KeyboardInterrupt


def main():
    profile, games = dependencies()
    home = Path.home()
    cache = home / ".cache/shadps4-affinity-20261007"
    if not cache.is_dir():
        raise RuntimeError("The tested build cache is missing; no build started")
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-loading-stall-", dir=home))
    work = Path(tempfile.mkdtemp(prefix="loading-stall-", dir=cache))
    summary = {"stack_captures": [], "runner_sha256": profile.digest(__file__)}
    stopped = threading.Event()
    worker = None
    lock = (cache / "homebrew.lock").open("a")
    handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if profile.emulators():
            raise RuntimeError("An emulator is running; close it normally before testing")
        paths = [cache / name for name in ARTIFACTS]
        for path, expected in zip(paths, ARTIFACTS.values()):
            if profile.digest(path) != expected:
                raise RuntimeError("Tested artifact missing or changed: " + str(path))
        if profile.digest(home / "Applications/shadps4/shadps4") != INSTALLED_SHA:
            raise RuntimeError("Installed baseline changed; launcher left untouched")
        binary, client, drrun, runtime = paths
        summary["artifacts"] = {str(cache / name): sha for name, sha in ARTIFACTS.items()}
        debugger = debugger_preflight(evidence)
        summary["debugger"] = debugger
        prefix = [str(drrun), "-disable_rseq", "-vm_base", "0x710020000000",
                  "-no_vm_base_near_app", "-c", str(client), "--", str(binary)]
        worker = threading.Thread(target=monitor, args=(work, evidence, games, debugger,
                                  [binary, client, runtime], stopped, summary["stack_captures"]))
        worker.start()
        profile.say("CAPTURE=Reusing the exact tested build. Live stacks start after 15s, then every 30s.")
        profile.say("REPRODUCE=Day match, leave match, then start night match. Keep Moonlight connected.")
        profile.say("If loading stalls, leave it there for 90 seconds, then exit normally. Brief debugger pauses are expected.")
        summary["game"] = games.run_stage("CUSA18676", "PES", "translated", binary, prefix, work, evidence)
    except KeyboardInterrupt:
        summary["error"] = "Interrupted; collecting evidence and cleaning up this test"
    except Exception as error:
        summary["error"] = str(error)
        profile.say("CAPTURE_ERROR=" + str(error))
    finally:
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        stopped.set()
        if worker is not None:
            worker.join()
        active = False
        errors = []
        try:
            for path in work.glob("*/job.json"):
                job = json.loads(path.read_text())
                for action in (games.restore, games.stop_owned):
                    try:
                        action(job)
                    except Exception as error:
                        errors.append(str(error))
                status_path = path.parent / "status.json"
                status = json.loads(status_path.read_text()) if status_path.exists() else {}
                active |= bool(status.get("bridge") and games.alive(status["bridge"])) or bool(games.owned(job))
            summary["cleanup_errors"] = errors
            summary["live_stacks_captured"] = sum(bool(item.get("captured"))
                                                   for item in summary["stack_captures"])
            games.write_json(evidence / "summary.json", summary)
            archive_path = evidence.with_suffix(".tar.gz")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(evidence, arcname=evidence.name)
            if not active and not errors:
                shutil.rmtree(work)
            profile.say("UPLOAD_ONLY=" + str(archive_path))
            profile.say("LIVE_STACKS=" + str(summary["live_stacks_captured"]))
            profile.say("SSH stays open. The installed emulator was not replaced.")
        finally:
            lock.close()
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    return 1 if summary.get("error") or summary.get("cleanup_errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
