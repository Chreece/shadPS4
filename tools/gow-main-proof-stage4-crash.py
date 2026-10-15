#!/usr/bin/env python3
"""Read-only reproduction of an early shadPS4 guest fault under GDB.

This is a diagnostic harness, NOT a game patch. It uses the checksum-pinned
fresh-upstream emulator, unchanged user settings and the existing verified
Cross binding. It does not compile, alter saves, install software, patch guest
memory, replace the launcher, or touch the Vulkan driver.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import traceback

HOME = Path.home()
ROOT = HOME / "projects/shadps4/GodOfWarRagnarok"
WORK = ROOT / "tests/main-proof-20261015-151529-3107006"
SRC = WORK / "source"
BINARY = WORK / "build/shadps4"
PROFILE = HOME / ".local/share/shadPS4"
LOGROOT = HOME / "projects/shadps4/logs"
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = LOGROOT / f"gow-upstream-stage4-{STAMP}-{os.getpid()}"
UPSTREAM = "b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
BINARY_SHA = "68a1e91cad00745fa0f9dd4cf2c9962c3e35c77f4c69cc459ee8f9a21a9a0d4e"
SOURCE_DIFF_SHA = "b0044088565024240e2827c1d7d77c5bdaf8475e74ee5a2293936fa706cad057"
INPUT_MODULE = ROOT / "scripts/gow-auto-input-base.py"
INPUT_SHA = "8ec81d11f916d1cba00ac1cf66a3ad15b0e83c9d7825f66d8ed69c53f118d071"
SERIAL = "CUSA34384"

def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()

def note(s):
    print("GOW_STAGE4 " + str(s), flush=True)

def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

def run(args, *, cwd=None, timeout=45):
    return subprocess.run([str(v) for v in args], cwd=cwd, text=True,
                          capture_output=True, timeout=timeout, check=True).stdout

def config_snapshot():
    paths = [PROFILE/"config.json", PROFILE/"custom_configs"/(SERIAL+".json")]
    state = {}
    for p in paths:
        if p.is_symlink():
            raise RuntimeError("config symlink: "+str(p))
        if p.exists() and (not p.is_file() or p.stat().st_size > 5000000):
            raise RuntimeError("unsafe config: "+str(p))
        state[str(p)] = checksum(p) if p.exists() else None
    return state

def input_module():
    if not INPUT_MODULE.is_file() or INPUT_MODULE.is_symlink():
        raise RuntimeError("previously verified input helper is unavailable")
    if checksum(INPUT_MODULE) != INPUT_SHA:
        raise RuntimeError("pinned Cross input helper bytes changed")
    spec = importlib.util.spec_from_file_location("proof_input", INPUT_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to read Cross input helper")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper

def child_processes(gdb_pid):
    children = Path(f"/proc/{gdb_pid}/task/{gdb_pid}/children")
    try:
        return [int(x) for x in children.read_text().split()]
    except (OSError, ValueError):
        return []

def owned_inferior(pid, gdbpid):
    proc = Path("/proc") / str(pid)
    try:
        fields = proc.joinpath("status").read_text()
        parent = re.search(r"(?m)^PPid:\s*(\d+)$", fields)
        uid = re.search(r"(?m)^Uid:\s*(\d+)", fields)
        if not parent or int(parent.group(1)) != gdbpid:
            return False
        if not uid or int(uid.group(1)) != os.getuid():
            return False
        maps = proc.joinpath("maps").read_text(errors="replace")
        return str(BINARY) in maps
    except (OSError, ValueError):
        return False

def find_verified_window(helper, pid, env):
    try:
        raw = helper.xcommand(["search", "--onlyvisible", "--pid", pid], env)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    found = []
    for wid in raw.splitlines():
        if not wid.isdecimal():
            continue
        try:
            if helper.xcommand(["getwindowpid", wid], env) != str(pid):
                continue
            title = helper.xcommand(["getwindowname", wid], env)
            if SERIAL in title or "God of War Ragnar" in title:
                found.append(wid)
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
            continue
    if len(found) > 1:
        raise RuntimeError("multiple matching windows; no controller input sent")
    return found[0] if found else None

def gdb_command():
    return [
        "gdb", "--nx", "--quiet", "--batch",
        "-ex", "set pagination off",
        "-ex", "set confirm off",
        "-ex", "set print thread-events off",
        "-ex", "set debuginfod enabled off",
        "-ex", "set follow-exec-mode same",
        "-ex", "set follow-fork-mode child",
        "-ex", "handle SIGSEGV nostop noprint pass",
        "-ex", "handle SIGILL nostop noprint pass",
        "-ex", "catch signal SIGABRT",
        "-ex", "run",
        "-ex", "bt 40",
        "-ex", "thread apply all bt 12",
        "-ex", "info registers",
        "-ex", "info proc mappings",
        "--args", str(BINARY), "--cpu-id-mode", "auto",
        "--game", SERIAL, "--fullscreen", "true",
    ]

def preflight():
    if not BINARY.is_file() or BINARY.is_symlink() or checksum(BINARY) != BINARY_SHA:
        raise RuntimeError("the known 73/73 GCN-tested executable differs")
    if not (SRC/".git").is_dir():
        raise RuntimeError("source Git checkout absent")
    rev = run(["git", "rev-list", "--parents", "-n", "1", "HEAD"], cwd=SRC).strip()
    if not rev.startswith("2e5ff4eb89a5c1ccf9519cd2da1e544e569a831b "):
        raise RuntimeError("original upstream plus generic-stack Git merge changed")
    diff = run(["git", "diff", "--binary", UPSTREAM, "--",
                "src", "CMakeLists.txt", "cmake"], cwd=SRC, timeout=90)
    if hashlib.sha256(diff.encode()).hexdigest() != SOURCE_DIFF_SHA:
        raise RuntimeError("tested general shader fixes changed since Stage 3")
    if not shutil.which("gdb"):
        raise RuntimeError("GDB is missing; not replacing it with an emulator workaround")
    if not shutil.which("xdotool"):
        raise RuntimeError("xdotool unavailable; no controller input attempted")
    existing = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "shadps4"],
                              capture_output=True, text=True)
    if existing.returncode == 0 and existing.stdout.strip():
        raise RuntimeError("shadps4 already running; refusing to affect it")

def execute(result):
    before = config_snapshot()
    helper = input_module()
    cross, mapping = helper.read_mapping(PROFILE)
    if cross != "n":
        raise RuntimeError("Cross binding differs from the verified Stage 3 run")
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    authority = HOME / ".Xauthority"
    if authority.is_file():
        env.setdefault("XAUTHORITY", str(authority))
    write_json(OUT/"preflight.json", {
        "binary_sha256": BINARY_SHA,
        "source_diff_sha256": SOURCE_DIFF_SHA,
        "upstream_main": UPSTREAM,
        "cross_key": cross,
        "mapping": mapping,
        "original_config_sha256": before,
        "emulator_or_driver_patches": False,
        "compiled_objects_modified": False,
    })

    note("GDB=launch original 73/73 GCN-tested emulator with unchanged config")
    note("GDB_LOG="+str(OUT/"gdb-startup.log"))
    events = []
    logfile = (OUT/"gdb-startup.log").open("wb")
    proc = None
    try:
        proc = subprocess.Popen(gdb_command(), env=env, cwd=ROOT,
                                stdin=subprocess.DEVNULL,
                                stdout=logfile, stderr=subprocess.STDOUT,
                                start_new_session=True)
        started = time.monotonic()
        observed = None
        sent = False
        while proc.poll() is None and time.monotonic() - started < 115:
            inferior_pids = [p for p in child_processes(proc.pid) if owned_inferior(p, proc.pid)]
            if len(inferior_pids) == 1 and not sent:
                pid = inferior_pids[0]
                wid = find_verified_window(helper, pid, env)
                if wid and observed is None:
                    observed = (pid, wid, time.monotonic())
                    events.append({"event": "window_verified", "pid": pid, "window": wid})
                if wid and observed and observed[:2] == (pid, wid) and (
                        time.monotonic() - observed[2] >= 4):
                    try:
                        helper.focus_game(wid, pid, env)
                        helper.press_cross(wid, pid, cross, env)
                        events.append({"event": "cross_sent_once", "pid": pid,
                                       "elapsed": round(time.monotonic()-started, 2)})
                    except BaseException as e:
                        events.append({"event": "cross_refused", "reason":repr(e)})
                    sent = True
            time.sleep(.25)
        if proc.poll() is None:
            events.append({"event":"gdb_timeout", "seconds":115})
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
        result["gdb_exit"] = proc.returncode
        result["input_events"] = events
        result["cross_sent"] = any(e["event"]=="cross_sent_once" for e in events)
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=8)
        logfile.close()
        write_json(OUT/"input-events.json", events)
        after = config_snapshot()
        result["user_configs_unchanged"] = before == after
        if before != after:
            raise RuntimeError("user configuration changed unexpectedly")
    text = (OUT/"gdb-startup.log").read_text(errors="replace")
    result["abort_caught"] = bool(re.search(
        r"(?:hit Catchpoint|Catchpoint\s+\d+.*signal SIGABRT)", text))
    result["stack_frames_present"] = bool(re.search(r"(?m)^#0\s+", text))
    result["null_guest_fault_logged"] = (
        "Read from address 0x0" in text or "Read from address 0x0000000000000000" in text)
    result["status"] = ("GDB_ABORT_TRACE_CAPTURED" if
                        result["abort_caught"] and result["stack_frames_present"] else
                        "GDB_REPRODUCTION_INCOMPLETE")

def package(result):
    write_json(OUT/"result.json", result)
    archive = LOGROOT/(OUT.name + ".tar.gz")
    with tarfile.open(archive, "w:gz", compresslevel=3) as tf:
        for item in sorted(OUT.rglob("*")):
            if item.is_file() and not item.is_symlink():
                tf.add(item, arcname=OUT.name+"/"+str(item.relative_to(OUT)))
    note("UPLOAD="+str(archive))

def main():
    LOGROOT.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(mode=0o700)
    result = {"status": "STARTED", "phase": "PREFLIGHT",
              "tested_emulator_sha256": BINARY_SHA,
              "user_configs_unchanged": None,
              "guest_memory_modified": False,
              "installed_launcher_modified": False,
              "game_specific_emulator_changes": False,
              "rendered_gameplay_verified": False}
    try:
        preflight()
        result["phase"] = "GDB_REPRODUCTION"
        execute(result)
        package(result)
        return 0 if result["status"]=="GDB_ABORT_TRACE_CAPTURED" else 1
    except BaseException as e:
        result["status"] = "FAILED"
        result["error"] = repr(e)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        package(result)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
