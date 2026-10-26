#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Append one immutable emulator/game trial to the permanent structured diary.

No telemetry is invented: unobserved game visuals are explicitly NOT_OBSERVED.
Works with both build-failure and gameplay archives. The archive is hashed but
not copied into git, so large logs and game data remain on the private host.
Default ledger lives in ~/projects/shadps4/diary/trials.jsonl. Supply --ledger
with docs/emulator/TRIALS.jsonl when curating reviewed results into the fork.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tarfile

SHA = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DECISIONS = {
    "PENDING_REVIEW", "PROVEN_GENERIC", "KEEP_GAME_BRANCH",
    "KEEP_DIAGNOSTIC", "BUILD_PASS_RUNTIME_PENDING",
    "REJECT", "SUPERSEDED", "BLOCKED", "CORRECTION",
}
EVIDENCE_LIMIT = 196608


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def archive_evidence(path: Path) -> dict:
    result = {"run_exit_code": None, "build_signals": [],
              "game_signals": [], "files_seen": 0, "archive_error": None}
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                result["files_seen"] += 1
                name = Path(member.name).name
                if name.endswith(".exit") and member.size <= 32:
                    try:
                        value = archive.extractfile(member).read().decode().strip()
                        if re.fullmatch(r"-?[0-9]+", value):
                            result["run_exit_code"] = int(value)
                    except (OSError, UnicodeError, ValueError):
                        pass
                if not name.endswith((".log", ".txt")) or member.size > EVIDENCE_LIMIT:
                    continue
                file = archive.extractfile(member)
                if file is None:
                    continue
                text = file.read(EVIDENCE_LIMIT).decode("utf-8", "replace")
                for line in text.splitlines():
                    if any(mark in line for mark in (
                        "NATIVE_FULL_EMULATOR_LINK=PASS",
                        "NATIVE_BUILD=PASS_FULL_LINK",
                        "DYNAMORIO_IN_NINJA_AND_CACHE=NO",
                        "KERNEL_SANITIZER_REGRESSION=PASS",
                        "KERNEL_THREAD_ATEXIT_REGRESSION=PASS",
                        "NATIVE_CPU_EMULATOR_SHA256=",
                    )) and line not in result["build_signals"] and len(result["build_signals"]) < 30:
                        result["build_signals"].append(line[:220])
                    if any(mark in line for mark in (
                        "GAME_OBSERVATION=", "GAME_LAUNCHED=",
                        "Unhandled access violation", "SIGABRT",
                        "OLLAMA_MODELS_RESTORED=", "VRAM_AFTER_OLLAMA_UNLOAD_FREE_GIB=",
                    )) and line not in result["game_signals"] and len(result["game_signals"]) < 35:
                        result["game_signals"].append(line[:220])
    except (tarfile.TarError, OSError, UnicodeError) as err:
        result["archive_error"] = type(err).__name__ + ": " + str(err)
    return result


def validate_sha(value: str, label: str) -> str:
    if not SHA.fullmatch(value):
        raise ValueError(label + " must be the exact 40-character Git commit SHA")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--game", default="UNSPECIFIED",
                        help="Title being observed; never a game-specific emulator switch")
    parser.add_argument("--source", default="", help="Exact tested local commit, if known")
    parser.add_argument("--upstream", default="", help="Upstream parent exact SHA, if known")
    parser.add_argument("--candidate-sha256", default="")
    parser.add_argument("--change", default="PENDING_ANALYSIS")
    parser.add_argument("--emulator-effect", default="NOT_OBSERVED")
    parser.add_argument("--game-effect", default="NOT_OBSERVED")
    parser.add_argument("--decision", choices=sorted(DECISIONS), default="PENDING_REVIEW")
    parser.add_argument("--pr", type=int, default=None)
    parser.add_argument("--next", dest="next_check", default="PENDING_ANALYSIS")
    parser.add_argument("--revision-of", default="", help="Existing evidence SHA to correct; append only")
    parser.add_argument("--ledger", type=Path, default=Path.home() /
                        "projects/shadps4/diary/trials.jsonl")
    args = parser.parse_args()
    archive = args.archive.expanduser().resolve(strict=True)
    if not archive.is_file() or not archive.name.endswith(".tar.gz"):
        parser.error("Archive must be an existing .tar.gz file")
    if not args.game or len(args.game) > 256:
        parser.error("Invalid game label")
    for label, value in (("source", args.source), ("upstream", args.upstream)):
        if value:
            validate_sha(value, label)
    for label, value in (("candidate-sha256", args.candidate_sha256),
                         ("revision-of", args.revision_of)):
        if value and not HEX64.fullmatch(value):
            parser.error(label + " must be a 64-character SHA256 digest")
    if args.pr is not None and args.pr <= 0:
        parser.error("PR number must be positive")

    evidence_sha = digest(archive)
    evidence = archive_evidence(archive)
    entry = {
        "schema_version": 1,
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "record_type": "CORRECTION" if args.revision_of else "TRIAL",
        "scope": "universal shadPS4",
        "game_id": args.game,
        "artifact": archive.name,
        "artifact_sha256": evidence_sha,
        "artifact_size": archive.stat().st_size,
        "source_commit": args.source or "UNRECORDED",
        "upstream_commit": args.upstream or "UNRECORDED",
        "candidate_sha256": args.candidate_sha256 or "UNRECORDED",
        "introduced_generic_change": args.change,
        "emulator_effect": args.emulator_effect,
        "game_effect": args.game_effect,
        "decision": args.decision,
        "pr": args.pr,
        "next_check": args.next_check,
        "revision_of_sha256": args.revision_of or None,
        "source_of_truth": "SHA-pinned archive + separately recorded user-visible observation",
        "archive_evidence": evidence,
    }
    ledger = args.ledger.expanduser()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger.with_suffix(ledger.suffix + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = []
        if ledger.is_file():
            with ledger.open(encoding="utf-8") as file:
                for line in file:
                    if not line.strip():
                        continue
                    existing.append(json.loads(line))
        if args.revision_of:
            if not any(row.get("artifact_sha256") == args.revision_of for row in existing):
                parser.error("Correction's referenced evidence SHA not found; no write")
        else:
            for row in existing:
                if row.get("artifact") == archive.name:
                    if row.get("artifact_sha256") != evidence_sha:
                        parser.error("Archive filename reused with differing bytes; no write")
                    print("ALREADY_RECORDED=" + evidence_sha)
                    return 0
                if row.get("artifact_sha256") == evidence_sha:
                    print("ALREADY_RECORDED=" + evidence_sha)
                    return 0
        raw = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode()
        fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
    print("DIARY_APPENDED=" + str(ledger))
    print("ARCHIVE_SHA256=" + evidence_sha)
    print("DECISION=" + args.decision)
    print("GAME_VISUAL_EFFECT=" + args.game_effect)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("DIARY_WRITE_REJECTED=" + str(error), file=sys.stderr)
        raise SystemExit(2)
