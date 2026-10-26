#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only postmortem for a prior native PS4/X11 failure.

No game is launched. Do not restart Xorg/Sunshine/ES-DE or alter any
emulator binary, active session, profile, GPU setting or Ollama model.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

ANSI = re.compile(r"\x1b\[[0-9;]*m")
MARKERS = (
    "amdgpu", "navi31", "drm:", "ring timeout", "gpu reset", "gpu recovery",
    "gfx_", "gpu fault", "radv", "device lost", "context lost",
    "xorg", "x server", "x11", "xwayland", "sunshine",
    "segfault", "system_error", "resource deadlock", "coredump",
    "display", "sdl", "oom", "killed process", "sunshine-esde",
)

def run(argv: list[str], limit: int = 250000, timeout: int = 12) -> str:
    if shutil.which(argv[0]) is None:
        return "COMMAND_NOT_INSTALLED: " + argv[0] + "\n"
    try:
        p = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=timeout)
        s = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        return (f"EXIT={p.returncode}\n" + s[:limit] +
                (f"\nSTDERR: {err[:1200]}\n" if err else ""))
    except (OSError, subprocess.TimeoutExpired) as error:
        return type(error).__name__ + ": " + str(error)[:400] + "\n"

def selected_journal(kind: str, start: str, end: str) -> str:
    args = ["journalctl"]
    if kind == "kernel":
        args += ["-k"]
    elif kind == "user":
        args += ["--user"]
    args += ["--since", start, "--until", end, "--no-pager",
             "--output", "short-iso", "--quiet"]
    data = run(args, limit=2500000, timeout=14)
    out = [line[:1600] for line in data.splitlines()
           if (any(term in line.lower() for term in MARKERS)
               or line.startswith(("EXIT=", "STDERR:", "COMMAND_NOT_INSTALLED")))]
    return "\n".join(out[-550:]) + "\n"

def safe_tail(path: Path, max_bytes: int = 225000) -> str:
    try:
        if not path.is_file() or path.is_symlink():
            return "FILE_NOT_PRESENT_OR_SYMLINK\n"
        with path.open("rb") as fd:
            fd.seek(max(0, path.stat().st_size - max_bytes))
            return ANSI.sub("", fd.read().decode("utf-8", "replace"))
    except (OSError, UnicodeError) as error:
        return type(error).__name__ + ": " + str(error) + "\n"

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()
    if not re.fullmatch(r"shadps4-srt-warnfix-[0-9]{8}-[0-9]{6}-[0-9]+", args.tag):
        ap.error("Invalid, path-traversal-proof run tag")
    logs = args.root.resolve(strict=True) / "logs"
    report = logs / (args.tag + "-x11-postmortem")
    archive = logs / (args.tag + "-x11-postmortem.tar.gz")
    if report.exists() or archive.exists():
        ap.error("Postmortem report already exists; do not overwrite evidence")
    report.mkdir(mode=0o700)

    def save(name: str, content: str):
        (report / name).write_text(content, encoding="utf-8")

    native_result = logs / (args.tag + "-report") / "native-result.json"
    full_console = logs / (args.tag + "-report") / "full-console.log"
    evidence = {}
    try:
        evidence = json.loads(native_result.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    elapsed = float(evidence.get("elapsed_seconds", 29.191))
    elapsed = max(0, min(180, elapsed))
    try:
        completed = full_console.stat().st_mtime
    except OSError:
        try:
            completed = (logs / (args.tag + ".log")).stat().st_mtime
        except OSError:
            completed = dt.datetime.now().timestamp()
    # Infer clock times from this run's actual on-disk console mtime,
    # not the date encoded in its build-start ID.
    start_dt = dt.datetime.fromtimestamp(completed - elapsed - 60).astimezone()
    end_dt = dt.datetime.fromtimestamp(completed + 45).astimezone()
    start = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    save("window.txt",
         "RUN_TAG=" + args.tag + "\nINFERRED_FROM_CONSOLE_MTIME=YES\n" +
         "START=" + start_dt.isoformat() + "\nEND=" + end_dt.isoformat() + "\n" +
         "TIME_WINDOW_SECONDS=" + str((end_dt-start_dt).total_seconds()) + "\n" +
         "NATIVE_RESULT=" + json.dumps(evidence, indent=2)[:5000] + "\n")
    if full_console.is_file():
        data = safe_tail(full_console, 220000)
        save("console-fatal-tail.txt", data[-220000:])
        patterns = (
            "XIO:", "Resource deadlock avoided", "terminate called",
            "ErrorDeviceLost", "radv/amdgpu", "Vulkan", "Unhandled access violation",
            "Fatal", "SIGSEGV")
        found = [line[:500] for line in data.splitlines()
                 if any(p.lower() in line.lower() for p in patterns)]
        save("console-final-errors.txt", "\n".join(found[-160:]) + "\n")
    for kind in ("kernel", "system", "user"):
        save("journal-" + kind + ".txt", selected_journal(kind, start, end))

    save("coredump-info.txt",
         run(["coredumpctl", "--no-pager", "--since", start,
              "--until", end, "list"], limit=28000) +
         "\n----- GAME PID CORE ----\n" +
         run(["coredumpctl", "--no-pager", "info", "shadps4"], limit=18000))

    # Xorg logs are read-only snapshots and may rotate after the failure.
    candidate_logs = (
        Path.home() / ".local/share/xorg/Xorg.0.log",
        Path.home() / ".local/share/xorg/Xorg.0.log.old",
        Path("/var/log/Xorg.0.log"),
        Path("/var/log/Xorg.0.log.old"),
    )
    for n, path in enumerate(candidate_logs):
        save(f"xorg-log-{n}.txt",
             str(path) + "\n" + safe_tail(path, max_bytes=90000))

    save("x11-current-health.txt",
         "DISPLAY=" + os.environ.get("DISPLAY", "UNSET") + "\n" +
         "XAUTHORITY_PATH=" + os.environ.get("XAUTHORITY", "UNSET") + "\n" +
         "X11_SOCKET_EXISTS=" + str(Path("/tmp/.X11-unix/X0").exists()) + "\n" +
         "----- X SERVER -----\n" +
         run(["pgrep", "-a", "-f", "Xorg|Xwayland|sunshine|shadps4"], limit=20000) +
         "----- DISPLAY QUERY -----\n" +
         run(["xdpyinfo", "-display", ":0"], limit=5500, timeout=5) +
         "----- DISPLAY AUTH QUERY -----\n" +
         run(["xrandr", "-display", ":0", "--current"], limit=5500, timeout=5))
    save("observer-log-tail.txt",safe_tail(logs / (args.tag + ".log"), max_bytes=16000))
    with tarfile.open(archive, "w:gz") as output:
        for path in sorted(report.iterdir()):
            if path.is_file():
                output.add(path, arcname=args.tag + "-x11-postmortem/" + path.name)
    print("NO_GAME_LAUNCHED=YES", flush=True)
    print("NO_XORG_SUNSHINE_OR_OLLAMA_MODIFICATIONS=YES", flush=True)
    print("EVIDENCE_WINDOW=" + start_dt.isoformat() + " to " + end_dt.isoformat(), flush=True)
    print("UPLOAD_THIS=" + str(archive), flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
