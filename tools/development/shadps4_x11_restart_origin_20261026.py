#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only audit of the 2026-10-26 Xorg :0 and Sunshine turnover.

Collect a narrow, unfiltered journal window and the supervisor/watchdog
control flow. Do not launch a game, stop/restart Xorg, alter services,
change GPU settings, or unload Ollama.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile

SINCE = "2026-10-26 14:29:30"
UNTIL = "2026-10-26 14:30:25"
ROOT = Path.home() / "projects/shadps4/logs"
KEYWORDS = re.compile(
    r"sunshine|amdgpu|xorg|xinit|headless|gpu[-_ ]?recover|"
    r"session[-_ ]?supervisor|kms[-_ ]?guard|reset[-_ ]?watch|"
    r"esde|display[-_ ]?manager|graphics|monitor[-_ ]?guard", re.I)
SCRIPT_LINES = re.compile(
    r"systemctl|pkill|killall|kill\(|os\.kill|SIGTERM|SIGKILL|"
    r"restart|stop|shutdown|terminate|reset|recover|watch|"
    r"subprocess|Popen|Xorg|xinit|start-headless|sunshine|"
    r"shadps4|/proc|sleep|esde|display|os\.system", re.I)
SENSITIVE = re.compile(
    r"(?i)\b(password|passwd|api[_-]?key|access[_-]?token|"
    r"client[_-]?secret|authorization|bearer|cookie)\b"
    r"(\s*[:=]\s*)([^\s,;}\]]+)")
EXPLICIT_PATHS = [
    "/usr/local/sbin/sunshine-amdgpu-reset-watch",
    "/usr/local/sbin/sunshine-session-supervisor",
    "/usr/local/sbin/sunshine-x11-prepare",
    "/usr/local/bin/start-headless-x.sh",
    "/usr/local/sbin/start-headless-x.sh",
    "/usr/local/bin/sunshine-amdgpu-recover",
    str(Path.home() / ".local/bin/start-headless-x.sh"),
    str(Path.home() / ".local/bin/sunshine-kms-guard.sh"),
    str(Path.home() / ".local/bin/sunshine-session-preflight"),
]

def redact(value: str) -> str:
    return SENSITIVE.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", value)

def command(argv: list[str], cap: int = 2500000, timeout: int = 15) -> str:
    if shutil.which(argv[0]) is None:
        return "COMMAND_NOT_INSTALLED=" + argv[0] + "\n"
    try:
        p = subprocess.run(argv, input=b"", capture_output=True,
                           timeout=timeout, check=False)
        text = p.stdout[:cap].decode("utf-8", "replace")
        if len(p.stdout) > cap:
            text += "\n[TRUNCATED]\n"
        error = p.stderr[:2400].decode("utf-8", "replace")
        return redact("EXIT=" + str(p.returncode) + "\n" + text +
                      ("\nSTDERR: " + error + "\n" if error else ""))
    except (OSError, subprocess.TimeoutExpired) as e:
        return type(e).__name__ + ": " + str(e)[:500] + "\n"

def save(folder: Path, name: str, data: str) -> None:
    (folder / name).write_text(redact(data), encoding="utf-8")

def select_units(raw: str) -> list[str]:
    result = set()
    for line in raw.splitlines():
        words = line.lstrip(" ●").split()
        if not words:
            continue
        unit = words[0]
        if unit.endswith((".service", ".timer", ".path")) and KEYWORDS.search(unit):
            result.add(unit)
    return sorted(result)[:35]

def script_evidence(p: Path) -> str:
    if not p.is_file() or p.stat().st_size > 512000:
        return "NOT_ACCESSIBLE_OR_TOO_LARGE: " + str(p) + "\n"
    try:
        raw = p.read_bytes()
        lines = raw.decode("utf-8", "replace").splitlines()
    except OSError as e:
        return "READ_ERROR: " + str(e) + "\n"
    matching = set(range(min(3, len(lines))))
    for i, line in enumerate(lines):
        if SCRIPT_LINES.search(line):
            matching.update(range(max(0, i-2), min(len(lines), i+3)))
    selected = sorted(matching)
    if len(selected) > 280:
        selected = selected[:280]
    return (f"FILE={p}\nSHA256={hashlib.sha256(raw).hexdigest()}\n"
            f"TOTAL_LINES={len(lines)}\n"
            + "\n".join(f"{i+1}: {redact(lines[i])[:550]}" for i in selected)
            + "\n")

def main() -> int:
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True)
    tag = "shadps4-x11-origin-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid())
    report = ROOT / tag
    archive = ROOT / (tag + ".tar.gz")
    report.mkdir(mode=0o700)
    save(report, "scope.txt",
         "TYPE=READ_ONLY_POSTMORTEM\n"
         "CAUSE_NOT_YET_ESTABLISHED=YES\n"
         f"SINCE={SINCE}\nUNTIL={UNTIL}\n"
         "PRIOR_GAME_SHA=d642061720dfa2c6773e75050185f53ac60e11a1a859d01a5f3308747657c908\n")
    for suffix, pre in (("system", []), ("user", ["--user"]), ("kernel", ["-k"])):
        save(report, "journal-"+suffix+".txt",
             command(["journalctl", *pre, "--since", SINCE, "--until", UNTIL,
                      "--no-pager", "--output=short-iso-precise", "--all"], cap=2700000))
    unit_sets = {}
    for label, pref in (("user", ["--user"]), ("system", [])):
        listed = command(["systemctl", *pref, "list-units", "--all", "--plain",
                          "--no-legend", "--no-pager"])
        installed = command(["systemctl", *pref, "list-unit-files", "--no-legend",
                             "--no-pager"])
        save(report, "matching-"+label+"-units.txt",
             "\n".join(line for line in (listed+"\n"+installed).splitlines()
                       if KEYWORDS.search(line)) + "\n")
        units = select_units(listed+"\n"+installed)
        unit_sets[label] = units
        rows = []
        for unit in units:
            rows.append("### " + label + " " + unit + "\n")
            rows.append(command(["systemctl", *pref, "show", unit,
                                 "-p", "Id,ActiveState,SubState,Restart,NRestarts,"
                                 "Result,ExecMainCode,ExecMainStatus,MainPID,"
                                 "FragmentPath,DropInPaths,ExecStart,ExecStop,ExecStopPost,"
                                 "KillMode,PartOf,BindsTo,Requires,Wants,After,Before"]))
            rows.append(command(["journalctl", *pref, "--unit", unit,
                                 "--since", SINCE, "--until", UNTIL,
                                 "--no-pager", "--output=short-iso-precise"],
                                cap=180000, timeout=12))
        save(report, "matched-"+label+"-unit-details.txt", "\n".join(rows))
    save(report, "current-processes.txt",
         "\n".join(line for line in command(
             ["ps", "-eo", "pid,ppid,lstart,args"], cap=250000).splitlines()
             if KEYWORDS.search(line)) + "\n")
    save(report, "current-xorg-health.txt",
         command(["pgrep", "-af", "Xorg|Xwayland|sunshine|headless-x"]) +
         command(["xdpyinfo", "-display", ":0"], cap=4000, timeout=5))
    found = []
    paths = [Path(x) for x in EXPLICIT_PATHS]
    for name in ("sunshine-amdgpu-reset-watch", "sunshine-session-supervisor",
                 "start-headless-x.sh", "sunshine-x11-prepare"):
        for folder in ("/usr/local/sbin", "/usr/local/bin",
                       str(Path.home() / ".local/bin")):
            paths.append(Path(folder) / name)
    for p in dict.fromkeys(paths):
        if p.is_file() and os.access(p, os.R_OK):
            found.append(script_evidence(p))
    save(report, "supervisor-watchdog-source-excerpts.txt",
         "\n---\n".join(found) if found else "NO_READABLE_SUPERVISOR_SCRIPTS\n")
    with tarfile.open(archive, "w:gz") as tf:
        for p in sorted(report.iterdir()):
            if p.is_file():
                tf.add(p, arcname=tag+"/"+p.name)
    print("READ_ONLY=YES")
    print("GAMES_OR_SERVICES_STARTED_STOPPED=NO")
    print("JOURNAL_WINDOW=" + SINCE + " to " + UNTIL)
    print("MATCHED_USER_UNITS=" + str(len(unit_sets["user"])))
    print("MATCHED_SYSTEM_UNITS=" + str(len(unit_sets["system"])))
    print("UPLOAD_THIS=" + str(archive))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
