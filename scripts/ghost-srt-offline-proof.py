#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Collect the actual dynamic-SRT shader IR for Ghost; no game run or build.

Collects only shader artifacts named by the failure hashes in the most recent
Ghost log. Does not alter config, cache, source, game files, process or SSH.
"""
from __future__ import annotations

import collections
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

HOME = Path.home()
STATE = HOME / ".local/state/shadps4-playtest-logs"
SOURCE = HOME / ".cache/shadps4-ghost-fullstack-20261008-131621/source"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
WORK = HOME / ".cache" / ("ghost-srt-proof-" + STAMP)
OUT = HOME / ("ghost-srt-proof-" + STAMP + ".tar.gz")

FAILURE = re.compile(
    r"Unexpected instruction for offset computation,\s*(\w+)\s+shader=0x([0-9a-fA-F]+)"
)
ASSIGN = re.compile(r"%([0-9]+)\s+=\s+(.*)")
READ = re.compile(r"=\s+ReadConst(?:Buffer)?\b.*?\s(%[0-9]+|#[0-9]+)\s+\(uses:")
LIMIT_TOTAL = 35 * 1024 * 1024
LIMIT_FILE = 5 * 1024 * 1024


def log_candidates():
    for meta in STATE.glob("*Ghost_of_Tsushima.ps4/session.meta"):
        runtime = meta.parent / "runtime.log"
        if runtime.is_file():
            yield runtime


def dump_dirs():
    return [
        HOME / ".local/share/shadPS4/shader/dumps",
        HOME / "Applications/shadps4/user/shader/dumps",
        SOURCE / "user/shader/dumps",
    ]


def ir_trace(raw: str) -> str:
    lines = raw.splitlines()
    defs = {}
    for line in lines:
        m = ASSIGN.search(line)
        if m:
            defs[m.group(1)] = m.group(2).strip()

    output = []
    dynamic = [(i + 1, line, READ.search(line).group(1))
               for i, line in enumerate(lines)
               if " = ReadConst" in line and READ.search(line)
               and READ.search(line).group(1).startswith("%")]
    phis = [(i + 1, line) for i, line in enumerate(lines) if " = Phi " in line]
    output.append(f"Dynamic ReadConst offsets: {len(dynamic)}")
    output.append(f"Phi instructions: {len(phis)}")
    for line_no, line, offset in dynamic[:40]:
        output.append(f"line {line_no}: {line[:240]}")
        queue = [(offset, 0)]
        seen = set()
        while queue and len(seen) < 36:
            value, depth = queue.pop(0)
            ident = value.lstrip("%")
            if ident in seen or depth > 5:
                continue
            seen.add(ident)
            definition = defs.get(ident, "unknown")
            output.append("  " + "  " * depth + value + " = " + definition[:230])
            for child in re.findall(r"%[0-9]+", definition)[:4]:
                if child != value:
                    queue.append((child, depth + 1))
    for line_no, line in phis[:24]:
        output.append(f"Phi line {line_no}: {line[:220]}")
    return "\n".join(output) + "\n"


def selftest():
    sample = ("[Render.Recompiler] Unexpected instruction for offset computation, "
              "Phi shader=0x8ced785b")
    assert FAILURE.search(sample).groups() == ("Phi", "8ced785b")
    ir = ("[001] %10 = Phi [ #0, {Block $1} ], [ %11, {Block $2} ] (uses: 1)\n"
          "[002] %12 = IAdd32 %10, #4 (uses: 1)\n"
          "[003] %13 = ReadConst (flags=0x0) %5, %12 (uses: 1)\n")
    r = ir_trace(ir)
    assert "Dynamic ReadConst offsets: 1" in r and "Phi line 1" in r
    assert "%10 = Phi" in r
    assert f"{int('8ced785b', 16):016x}" == "000000008ced785b"
    print("SELFTEST PASS: failure hashes, Phi ancestry, dynamic offsets, file matching")


def run():
    WORK.mkdir(parents=True, exist_ok=True)
    notes = []
    def say(x):
        notes.append(x)
        print(x, flush=True)

    say("GHOST SRT OFFLINE COLLECTION: no build, game launch or process modification.")
    logs = list(log_candidates())
    if not logs:
        say("No Ghost session runtime.log found; recording available shader artifacts only.")
        counts = collections.Counter()
        latest = None
    else:
        latest = max(logs, key=lambda p: p.stat().st_mtime)
        say(f"Selected game log: {latest}")
        raw = latest.read_text(errors="replace")
        counts = collections.Counter((opcode, shader.lower().lstrip("0") or "0")
                                     for opcode, shader in FAILURE.findall(raw))
        (WORK / "runtime-tail.txt").write_text(raw[-3_500_000:])
        (WORK / "session.meta").write_bytes((latest.parent / "session.meta").read_bytes())
    aggregate = collections.Counter()
    for (opcode, shader), n in counts.items():
        aggregate[shader] += n
    known = ["8ced785b", "5e89a8b4", "5c4c2b6f", "2ba74851",
             "a4f19a", "4950cf6", "55d17c16", "52b6e003"]
    wanted = list(dict.fromkeys([x for x, _ in aggregate.most_common(16)] + known))
    report = {
        "selected_log": str(latest) if latest else None,
        "failures": [{ "shader": shader, "opcode": opcode, "count": n }
                     for (opcode, shader), n in counts.most_common()],
        "requested_shaders": wanted, "artifacts": [],
    }
    file_count = 0
    total_bytes = 0
    shader_dir = WORK / "shaders"
    for shader in wanted:
        key = f"{int(shader, 16):016x}"
        matches = []
        for folder in dump_dirs():
            if not folder.is_dir():
                continue
            try:
                matches += [p for p in folder.glob(f"*0x{key}*") if p.is_file()]
            except OSError:
                pass
        # Prefer intermediate IR and SRT assembly over binaries, and keep
        # duplicates from multiple portable directories out of the archive.
        suffix_priority = (".pre-res-discover.irprogram.txt",
                           ".pre-lower-phi.irprogram.txt",
                           ".pre-res-patch.irprogram.txt",
                           ".irprogram.txt", ".srtprogram.txt", ".asl.txt", ".spv", ".bin")
        matches.sort(key=lambda p: next((i for i, suf in enumerate(suffix_priority)
                                         if p.name.endswith(suf)), 99))
        copied = set()
        for src in matches:
            if src.name in copied or len(copied) >= 20:
                continue
            try:
                n = src.stat().st_size
                if n > LIMIT_FILE or n + total_bytes > LIMIT_TOTAL:
                    continue
                destination = shader_dir / shader / src.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, destination)
                copied.add(src.name)
                file_count += 1
                total_bytes += n
                report["artifacts"].append({"shader": shader, "filename": src.name,
                                            "bytes": n, "sha256":
                                            hashlib.sha256(destination.read_bytes()).hexdigest()})
                if src.name.endswith(".irprogram.txt"):
                    analysis = destination.with_suffix(destination.suffix + ".dynamic-analysis.txt")
                    analysis.write_text(ir_trace(destination.read_text(errors="replace")))
            except OSError as e:
                say(f"Skipped {src.name}: {e}")
        say(f"Shader 0x{shader}: {len(copied)} matching artifacts")
    if SOURCE.is_dir() and (SOURCE / ".git").exists():
        try:
            for name in ("flatten_extended_userdata_pass.cpp",
                         "resource_patching_pass.cpp"):
                p = SOURCE / "src/shader_recompiler/ir/passes" / name
                if p.is_file() and p.stat().st_size < 500_000:
                    shutil.copyfile(p, WORK / name)
            g = subprocess.run(["git", "-C", str(SOURCE), "log", "-1", "--format=%H %s"],
                               text=True, capture_output=True, timeout=8)
            (WORK / "git-head.txt").write_text(g.stdout + g.stderr)
        except Exception as exc:
            say(f"Source metadata unavailable: {exc}")
    report["file_count"] = file_count
    report["total_bytes"] = total_bytes
    (WORK / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    (WORK / "status.txt").write_text("\n".join(notes) + "\n")
    with tarfile.open(OUT, "w:gz") as archive:
        archive.add(WORK, arcname=WORK.name)
    say(f"COLLECTION_COMPLETE files={file_count} bytes={total_bytes}")
    say(f"ARCHIVE={OUT}")
    say("No executable, shaders, caches, saves, config, SSH session or game process changed.")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        selftest()
    else:
        run()
