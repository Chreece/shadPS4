#!/usr/bin/env python3
"""Observe a SHA-pinned native UFFD shadPS4 candidate, without changing X11."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import traceback

PROJECT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
TEST = PROJECT / "tests/native-rendered-gfx-20261016-152535-3014679"
LOGS = Path.home() / "projects/shadps4/logs"
BIN = TEST / "build/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543"
EXPECTED_SHA256 = "e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa"
CAPTURE_SEC = 180
SAMPLE_SEC = 15


def cmdline(proc: Path) -> list[str]:
    data = (proc / "cmdline").read_bytes()
    return [a.decode("utf-8", "replace") for a in data.rstrip(b"\0").split(b"\0") if a]


def active_emulators() -> list[dict]:
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            args = cmdline(proc)
            if not args or not Path(args[0]).name.startswith("shadps4"):
                continue
            state = (proc / "stat").read_text().rsplit(") ", 1)[1].split()[0]
            if state not in ("Z", "X"):
                found.append({"pid": int(proc.name), "state": state, "args": args})
        except (OSError, IndexError):
            pass
    return found


def sample_threads(pid: int) -> dict:
    result = {}
    for thread in Path(f"/proc/{pid}/task").glob("*"):
        if not thread.name.isdecimal():
            continue
        try:
            name = (thread / "comm").read_text().strip()
            if not any(term in name for term in ("GpuComm", "Uffd", "Game:Main", "TaskManager")):
                continue
            fields = (thread / "stat").read_text().rsplit(") ", 1)[1].split()
            result[thread.name] = {
                "name": name,
                "state": fields[0],
                "ticks": int(fields[11]) + int(fields[12]),
                "wchan": (thread / "wchan").read_text().strip(),
            }
        except (OSError, ValueError, IndexError):
            pass
    return result


def observation(prev: dict, now: dict, seconds: int) -> tuple[dict, bool]:
    rows = []
    gpu_fault = False
    uffd_wait = False
    all_idle = True
    hz = os.sysconf("SC_CLK_TCK")
    for tid, thread in sorted(now.items(), key=lambda x: int(x[0])):
        old = prev.get(tid)
        cpu_ms = round((thread["ticks"] - old["ticks"]) * 1000 / hz) if old else None
        if cpu_ms is not None and cpu_ms > 0:
            all_idle = False
        row = {"tid": tid, "cpu_delta_ms": cpu_ms, **thread}
        rows.append(row)
        if "GpuComm" in thread["name"] and "handle_userfault" in thread["wchan"] and cpu_ms == 0:
            gpu_fault = True
        if "Uffd" in thread["name"] and "futex_wait_queue" in thread["wchan"] and cpu_ms == 0:
            uffd_wait = True
    stuck = gpu_fault and uffd_wait and all_idle
    return {"seconds": seconds, "threads": rows, "deadlock_signature": stuck}, stuck


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = LOGS / f"gow-uffd-deferred-gameplay-{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    archive = LOGS / f"{out.name}.tar.gz"
    status = {
        "test": "general_linux_uffd_deferred_invalidation",
        "userfaultfd": True,
        "game": "CUSA34384",
        "rebuild": False,
        "sunshine_x11_modified": False,
        "other_emulators_modified": False,
        "ssh_session_modified": False,
    }
    proc = None
    logfile = None
    samples = []
    result = 1
    try:
        if not BIN.is_file() or BIN.is_symlink():
            raise RuntimeError(f"CANDIDATE_MISSING_OR_SYMLINKED={BIN}")
        sha = hashlib.sha256(BIN.read_bytes()).hexdigest()
        status["binary_sha256"] = sha
        if sha != EXPECTED_SHA256:
            raise RuntimeError(f"BINARY_SHA_MISMATCH={sha}")
        running = active_emulators()
        (out / "preflight-emulators.json").write_text(json.dumps(running, indent=2) + "\n")
        if running:
            for item in running:
                print(f"EXISTING_EMULATOR={item['pid']} {' '.join(item['args'])[:260]}", flush=True)
            raise RuntimeError("EXISTING_EMULATOR_PRESENT; NONE_TERMINATED")
        env = os.environ.copy()
        for key in list(env):
            if key.startswith(("SHADPS4_GOW_", "SHADPS4_CPU_ID_")):
                env.pop(key, None)
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
        args = [
            str(BIN), "--cpu-id-mode", "builtin",
            "--userfaultfd", "--game", "CUSA34384", "--fullscreen", "true", "--show-fps"
        ]
        (out / "launch-command.txt").write_text(" ".join(args) + "\n")
        logfile = (out / "game-console.log").open("wb")
        proc = subprocess.Popen(args, env=env, cwd=TEST, stdin=subprocess.DEVNULL,
                                stdout=logfile, stderr=subprocess.STDOUT, start_new_session=True)
        status["pid"] = proc.pid
        print(f"GAME_LAUNCHED=YES PID={proc.pid}", flush=True)
        print("USE_MOONLIGHT_TO_CONTROL_GAME=YES; NO_AUTOMATIC_CLICKS", flush=True)
        print("OBSERVATION_SECONDS=180; NO_AUTOMATIC_GAME_TERMINATION", flush=True)
        before = sample_threads(proc.pid)
        t0 = time.monotonic()
        consecutive = 0
        for seconds in range(SAMPLE_SEC, CAPTURE_SEC + 1, SAMPLE_SEC):
            time.sleep(max(0, t0 + seconds - time.monotonic()))
            if proc.poll() is not None:
                status["exit_code"] = proc.returncode
                print(f"GAME_EXITED={proc.returncode} AT_SECOND={seconds}", flush=True)
                break
            after = sample_threads(proc.pid)
            item, deadlock = observation(before, after, seconds)
            samples.append(item)
            consecutive = consecutive + 1 if deadlock else 0
            gpu = [(x["cpu_delta_ms"], x["wchan"]) for x in item["threads"] if "GpuComm" in x["name"]]
            uffd = [(x["cpu_delta_ms"], x["wchan"]) for x in item["threads"] if "Uffd" in x["name"]]
            print(f"T={seconds}s GPU={gpu} UFFD={uffd} DEADLOCK_SIGNATURE={int(deadlock)}", flush=True)
            if consecutive >= 3:
                status["deadlock_reproduced"] = True
                print("DEADLOCK_REPRODUCED=YES; CAPTURING_NATIVE_STACKS", flush=True)
                if shutil.which("gdb") and shutil.which("timeout"):
                    try:
                        gdb = subprocess.run(
                            ["timeout", "30s", "gdb", "-nx", "--batch", "-q",
                             "-ex", "set pagination off", "-ex", "set confirm off",
                             "-ex", "info threads", "-ex", "thread apply all bt 18",
                             "-ex", "detach", "-p", str(proc.pid)],
                            capture_output=True, text=True, errors="replace", timeout=35)
                        (out / "deadlock-stacks.txt").write_text(
                            f"exit={gdb.returncode}\n{gdb.stdout}\n{gdb.stderr}")
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        (out / "deadlock-stacks-error.txt").write_text(repr(exc) + "\n")
                break
            before = after
        status["process_running_at_end"] = proc.poll() is None
        status["deadlock_reproduced"] = status.get("deadlock_reproduced", False)
        status["samples"] = len(samples)
        result = 0
    except Exception as exc:
        status["error"] = str(exc)
        (out / "error-trace.txt").write_text(traceback.format_exc())
        print(f"TEST_BLOCKED={exc}", flush=True)
    finally:
        status["capture_result"] = "COMPLETE" if result == 0 else "BLOCKED"
        (out / "result.json").write_text(json.dumps(status, indent=2) + "\n")
        (out / "thread-samples.json").write_text(json.dumps(samples, indent=2) + "\n")
        if logfile is not None:
            logfile.flush()
            logfile.close()
        log = out / "game-console.log"
        if log.is_file() and log.stat().st_size > 10 * 1024 * 1024:
            with log.open("rb") as f:
                first = f.read(1024 * 1024)
                f.seek(-8 * 1024 * 1024, 2)
                last = f.read()
            (out / "game-console-excerpt.log").write_bytes(
                first + b"\n...[MIDDLE OMITTED]...\n" + last)
        with tarfile.open(archive, "w:gz") as tf:
            for p in sorted(out.iterdir()):
                if p.is_file():
                    if p.name == "game-console.log" and log.stat().st_size > 10 * 1024 * 1024:
                        continue
                    tf.add(p, arcname=p.name)
        print(f"ARCHIVE={archive}", flush=True)
        print(f"GAME_RUNNING_AT_END={bool(proc and proc.poll() is None)}", flush=True)
        print("SUNSHINE_X11=UNCHANGED; SSH_SESSION=UNCHANGED", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
