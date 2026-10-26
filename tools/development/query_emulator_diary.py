#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Search ALL shadPS4 game/engine trials, including abandoned experiments.

Reads the append-only central ledger from git plus the optional private host
ledger. Does not change emulator code, game saves, branch state or diary data.

Examples:
  python3 tools/development/query_emulator_diary.py --find "DynamoRIO" --rejected
  python3 tools/development/query_emulator_diary.py --game CUSA11456
  python3 tools/development/query_emulator_diary.py --fix gpu-linear-readback
  python3 tools/development/query_emulator_diary.py --next --all
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "docs/emulator/TRIALS.jsonl"
PRIVATE = Path.home() / "projects/shadps4/diary/trials.jsonl"
REGISTRY = ROOT / "docs/emulator/FIXES.json"


def load_ledger(path: Path) -> list[dict]:
    entries = []
    if not path.is_file():
        return entries
    with path.open(encoding="utf-8") as stream:
        for number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid immutable JSONL entry: {exc}") from exc
            if not isinstance(entry, dict):
                raise ValueError(f"{path}:{number}: expected JSON object")
            entries.append(entry)
    return entries


def game_keys(item: dict) -> set[str]:
    games = set()
    for key in ("game_id", "game"):
        value = item.get(key)
        if isinstance(value, str) and value and value not in ("UNSPECIFIED", "ALL_GAMES"):
            games.add(value)
    for key in ("tested_games", "context_games", "games"):
        value = item.get(key)
        if isinstance(value, list):
            games.update(x for x in value if isinstance(x, str))
    outcomes = item.get("game_results")
    if isinstance(outcomes, dict):
        games.update(outcomes)
    return games


def record_key(item: dict) -> str:
    sha = item.get("artifact_sha256")
    if isinstance(sha, str) and len(sha) == 64:
        return "sha256:" + sha
    stable = item.get("record_id")
    if stable:
        return "id:" + str(stable)
    archive = item.get("archive") or item.get("artifact")
    if archive:
        return "archive:" + str(archive)
    return json.dumps(item, sort_keys=True)


def normalize_date(item: dict) -> str:
    return str(item.get("date") or item.get("recorded_at") or "UNKNOWN")


def text_blob(item: dict) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).lower()


def decision(item: dict) -> str:
    return str(item.get("decision") or "PENDING_REVIEW")


def render(item: dict) -> str:
    when = normalize_date(item)[:19]
    fix_id = item.get("fix_id")
    refs = [f"#{item['pr']}"] if item.get("pr") else []
    refs += [f"#{pr}" for pr in (item.get("related_prs") or [])
             if f"#{pr}" not in refs]
    tag = " ".join(refs)
    archive = item.get("artifact") or item.get("archive") or "no archive"
    change = (item.get("introduced_generic_change") or
              item.get("generic_change") or item.get("emulator_effect") or
              "UNRECORDED")
    effect = item.get("game_effect") or "NOT_OBSERVED"
    next_step = item.get("next_check") or item.get("next_test") or "UNRECORDED"
    tested = ", ".join(item.get("tested_games") or []) or "none confirmed"
    games = ", ".join(sorted(game_keys(item))) or "not applicable"
    merge = item.get("merge_commit") or "not merged / not verified"
    value = (
        f"{when}  [{decision(item)}]  games={games}"
        f"  tested={tested}\n"
        f"  {archive}" + (f"  {tag}" if tag else "") +
        (f"  fix={fix_id}" if fix_id else "") + "\n"
        f"  EMULATOR: {change}\n"
        f"  GAME: {effect}\n"
        f"  MAIN: {merge}\n"
        f"  NEXT: {next_step}"
    )
    return value


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", type=Path, default=SHARED)
    p.add_argument("--private-ledger", type=Path, default=PRIVATE)
    p.add_argument("--registry", type=Path, default=REGISTRY)
    p.add_argument("--game", help="Context or actually tested title/serial")
    p.add_argument("--find", default="", help="Case-insensitive substring in any trial field")
    p.add_argument("--decision", default="", help="Filter by exact decision")
    p.add_argument("--rejected", action="store_true", help="Include rejected, superseded, invalid approaches only")
    p.add_argument("--next", action="store_true", help="Only work needing validation/action")
    p.add_argument("--fix", default="", help="Search universal FIXES registry by id or text")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--all", action="store_true", help="Show every matching record")
    p.add_argument("--json", action="store_true", help="Machine-readable results")
    args = p.parse_args()

    if args.limit < 0:
        p.error("Negative --limit")
    merged = {}
    # Shared historical ledger is authoritative for imported old records;
    # newly tested host records are also included without overwriting history.
    for source in (args.ledger.expanduser(), args.private_ledger.expanduser()):
        for item in load_ledger(source):
            key = record_key(item)
            if key not in merged:
                merged[key] = item

    entries = list(merged.values())
    if args.game:
        entries = [x for x in entries if args.game.casefold() in
                   {s.casefold() for s in game_keys(x)}]
    if args.decision:
        entries = [x for x in entries if decision(x).casefold() == args.decision.casefold()]
    if args.rejected:
        entries = [x for x in entries if
                   decision(x).upper().startswith("REJECT") or
                   decision(x).upper().startswith("SUPERSEDED") or
                   "REJECT" in decision(x).upper()]
    if args.next:
        # A rejection stays searchable under --rejected, but it is not active
        # work merely because its historical next step says "do not retry".
        entries = [x for x in entries
                   if not decision(x).upper().startswith(("REJECT", "SUPERSEDED"))
                   and decision(x).upper() not in ("MERGED_MAIN", "PROVEN_GENERIC")]
    if args.find:
        needle = args.find.casefold()
        entries = [x for x in entries if needle in text_blob(x)]

    entries.sort(key=lambda x: (normalize_date(x), str(x.get("record_id") or
                        x.get("artifact") or x.get("archive") or "")), reverse=True)
    total = len(entries)
    if not args.all and args.limit:
        entries = entries[:args.limit]

    fixes = []
    if args.registry.is_file():
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
        if not isinstance(registry, dict) or not isinstance(registry.get("fixes"), list):
            raise ValueError("Malformed universal FIXES registry")
        fixes = registry["fixes"]
        if args.fix:
            needle = args.fix.casefold()
            fixes = [f for f in fixes if needle in text_blob(f)]
        elif args.find:
            needle = args.find.casefold()
            fixes = [f for f in fixes if needle in text_blob(f)]
        else:
            fixes = []
    elif args.fix:
        raise ValueError("Registry unavailable: " + str(args.registry))

    if args.json:
        print(json.dumps({"matching_trial_count":total,"trials":entries,"fixes":fixes},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    counts = Counter(decision(x) for x in merged.values())
    print("UNIVERSAL_DIARY_ENTRIES=" + str(len(merged)))
    print("MATCHING_TRIALS=" + str(total))
    print("DECISIONS=" + json.dumps(dict(sorted(counts.items())), sort_keys=True))
    if fixes:
        print("\nMATCHING_UNIVERSAL_FIXES=" + str(len(fixes)))
        for f in fixes:
            print(f"\n[{f['id']}] {f['status']} / {f['subsystem']}")
            print("  PRS: " + str(f.get("related_prs") or []))
            print("  MAIN: " + str(f.get("main_merge_sha") or "not merged or unverified"))
            print("  OBSERVED: " + str(f.get("observed_game_effects")))
            print("  AVOID: " + str(f.get("negative_result_or_prohibition")))
            print("  NEXT: " + str(f.get("next_test")))
    if entries:
        print("\nMATCHING_TRIALS_CHRONOLOGICAL_NEWEST_FIRST:")
        for x in entries:
            print("\n" + render(x))
    elif not fixes:
        print("No matching trial or fix. Check missing raw archives and game diaries.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print("UNIVERSAL_DIARY_QUERY_FAILED=" + str(error), file=sys.stderr)
        raise SystemExit(2)
