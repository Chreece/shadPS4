#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Append one immutable emulator/game trial to the permanent structured diary.

No telemetry is invented: unobserved game visuals are explicitly NOT_OBSERVED.
Works for every game and even prelaunch/build failures. One universal archive
ledger records context games separately from games actually launched; no title
is treated as tested just because its name appeared in a test configuration.
The archive is hashed, not copied into git, so logs and game data remain private.
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
    "MERGED_PENDING_CROSS_GAME_VALIDATION", "REJECT_PER_GAME_PARAMETER",
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
              "game_signals": [], "files_seen": 0, "archive_error": None,
              "game_launch_observed": False}
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
                if not name.endswith((".log", ".txt")):
                    continue
                file = archive.extractfile(member)
                if file is None:
                    continue
                # Even huge native console logs have useful first/last markers;
                # sample bounded bytes without extracting the entire archive.
                chunk = file.read(EVIDENCE_LIMIT)
                text = chunk.decode("utf-8", "replace")
                for line in text.splitlines():
                    if "GAME_LAUNCHED=YES" in line or "NATIVE_GENERIC_GAME_LAUNCHED=YES" in line:
                        result["game_launch_observed"] = True
                    if any(mark in line for mark in (
                        "NATIVE_FULL_EMULATOR_LINK=PASS",
                        "NATIVE_BUILD=PASS_FULL_LINK",
                        "DYNAMORIO_IN_NINJA_AND_CACHE=NO",
                        "KERNEL_SANITIZER_REGRESSION=PASS",
                        "KERNEL_THREAD_ATEXIT_REGRESSION=PASS",
                        "NATIVE_CPU_EMULATOR_SHA256=",
                        "NATIVE_ONLY_SHADPS4_LINK=PASS",
                        "NATIVE_BUILD_EXIT=",
                    )) and line not in result["build_signals"] and len(result["build_signals"]) < 30:
                        result["build_signals"].append(line[:220])
                    if any(mark in line for mark in (
                        "GAME_OBSERVATION=", "GAME_LAUNCHED=",
                        "NATIVE_GENERIC_GAME_LAUNCHED=", "GAMEPLAY_OBSERVER_EXIT=",
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
    parser.add_argument("--game", action="append", default=[],
                        help="Context game ID/name; repeat for A/B across titles; metadata only")
    parser.add_argument("--tested-game", action="append", default=[],
                        help="Game actually launched and verified; must be in --game")
    parser.add_argument("--game-result", action="append", nargs=2, default=[],
                        metavar=("GAME", "OBSERVATION"),
                        help="Per-game observed effect; repeat; no gameplay claim inferred")
    parser.add_argument("--visual", action="append", nargs=2, default=[],
                        metavar=("GAME", "OBSERVATION"))
    parser.add_argument("--audio", action="append", nargs=2, default=[],
                        metavar=("GAME", "OBSERVATION"))
    parser.add_argument("--fps", action="append", nargs=2, default=[],
                        metavar=("GAME", "OBSERVATION"))
    parser.add_argument("--fix-id", default="",
                        help="Generic capability/failed approach ID in docs/emulator/FIXES.json")
    parser.add_argument("--source", default="", help="Exact tested local commit, if known")
    parser.add_argument("--upstream", default="", help="Upstream parent exact SHA, if known")
    parser.add_argument("--candidate-sha256", default="")
    parser.add_argument("--change", default="PENDING_ANALYSIS")
    parser.add_argument("--emulator-effect", default="NOT_OBSERVED")
    parser.add_argument("--game-effect", default="NOT_OBSERVED")
    parser.add_argument("--decision", choices=sorted(DECISIONS), default="PENDING_REVIEW")
    parser.add_argument("--pr", type=int, action="append", default=[],
                        help="Related PR; repeat for stacked generic fixes")
    parser.add_argument("--next", dest="next_check", default="PENDING_ANALYSIS")
    parser.add_argument("--revision-of", default="", help="Existing evidence SHA to correct; append only")
    parser.add_argument("--ledger", type=Path, default=Path.home() /
                        "projects/shadps4/diary/trials.jsonl")
    args = parser.parse_args()
    archive = args.archive.expanduser().resolve(strict=True)
    if not archive.is_file() or not archive.name.endswith(".tar.gz"):
        parser.error("Archive must be an existing .tar.gz file")
    games = list(dict.fromkeys(args.game or ["UNSPECIFIED"]))
    if any(not g or len(g) > 256 or "\\x00" in g for g in games):
        parser.error("Invalid context game label")
    tested = list(dict.fromkeys(args.tested_game))
    if any(g not in games for g in tested):
        parser.error("--tested-game must be in the repeated --game list")
    if args.fix_id and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,95}", args.fix_id):
        parser.error("--fix-id must be a lowercase generic capability ID")
    for attribute in ("game_result", "visual", "audio", "fps"):
        for game, observation in getattr(args, attribute):
            if game not in games or not observation.strip():
                parser.error(f"--{attribute.replace('_','-')} must name a context game and observation")
    for label, value in (("source", args.source), ("upstream", args.upstream)):
        if value:
            validate_sha(value, label)
    for label, value in (("candidate-sha256", args.candidate_sha256),
                         ("revision-of", args.revision_of)):
        if value and not HEX64.fullmatch(value):
            parser.error(label + " must be a 64-character SHA256 digest")
    if any(pr <= 0 for pr in args.pr):
        parser.error("PR numbers must be positive")

    evidence_sha = digest(archive)
    evidence = archive_evidence(archive)
    # A game selected on the command line is not a game successfully started.
    # For ordinary one-game tests, a recorded child-launch marker suffices to
    # establish a runtime *attempt*, never a visual or gameplay pass.
    if evidence["game_launch_observed"] and len(games) == 1 and games[0] != "UNSPECIFIED":
        tested = list(dict.fromkeys(tested + games))
    observations = {g: {"summary": "NOT_OBSERVED", "visual": "NOT_OBSERVED",
                        "audio": "NOT_OBSERVED", "fps": "NOT_OBSERVED"} for g in games}
    for field, pairs in (("summary", args.game_result), ("visual", args.visual),
                         ("audio", args.audio), ("fps", args.fps)):
        for game, value in pairs:
            observations[game][field] = value.strip()
    if args.game_effect != "NOT_OBSERVED" and len(games) == 1:
        observations[games[0]]["summary"] = args.game_effect
    related_prs = list(dict.fromkeys(args.pr))
    entry = {
        "schema_version": 2,
        "record_id": "archive-" + evidence_sha[:24],
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "record_type": "CORRECTION" if args.revision_of else "TRIAL",
        "scope": "universal shadPS4",
        "implementation_scope": "EMULATOR_WIDE",
        "fix_id": args.fix_id or None,
        "game_id": games[0],     # backward-compatible one-game query
        "context_games": games,
        "tested_games": tested,
        "game_results": observations,
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
        "pr": related_prs[0] if related_prs else None,
        "related_prs": related_prs,
        "main_merge_verified": False,
        "merge_commit": None,
        "next_check": args.next_check,
        "revision_of_sha256": args.revision_of or None,
        "source_of_truth": "Byte-exact SHA-pinned archive; visual/audio observations only when explicitly provided",
        "evidence_quality": ("RAW_ARCHIVE_SHA256_CONFIRMED" if evidence["archive_error"] is None
                             else "ARCHIVE_PARSE_ERROR"),
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
    print("CONTEXT_GAMES=" + ",".join(games))
    print("TESTED_GAMES=" + ",".join(tested) if tested else "TESTED_GAMES=NONE_CONFIRMED")
    print("PER_GAME_OBSERVATIONS=" + json.dumps(observations, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("DIARY_WRITE_REJECTED=" + str(error), file=sys.stderr)
        raise SystemExit(2)
