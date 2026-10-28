#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Search the shared GoW/GoT journal, including failed and rejected experiments.

Reads TRIALS.jsonl, its sibling reviews/*.json, and the optional private host
ledger as one history. No emulator, repository, save or journal file is changed.

Examples:
  python3 tools/development/query_emulator_diary.py --find "DynamoRIO" --rejected
  python3 tools/development/query_emulator_diary.py --game CUSA11456 --all
  python3 tools/development/query_emulator_diary.py --fix gpu-linear-readback
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


def load_reviews(directory: Path) -> list[dict]:
    """Include the committed review addenda; never silently ignore bad records."""
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError(f"Review path is not a directory: {directory}")
    entries = []
    for path in sorted(directory.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid shared review: {exc}") from exc
        if not isinstance(entry, dict) or not isinstance(entry.get("record_id"), str) or not entry["record_id"].strip():
            raise ValueError(f"{path}: shared review requires an object with record_id")
        # Display/search provenance without rewriting the original record.
        entry = dict(entry)
        entry["_journal_review_file"] = path.name
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
    # Distinct reviews/corrections of the same physical archive are distinct
    # events. Never collapse explicit record IDs merely because hashes match.
    stable = item.get("record_id")
    if stable:
        return "id:" + str(stable)
    sha = item.get("artifact_sha256")
    if isinstance(sha, str) and len(sha) == 64:
        return "sha256:" + sha
    archive = item.get("archive") or item.get("artifact")
    if archive:
        return "archive:" + str(archive)
    return json.dumps(item, sort_keys=True)


def normalize_date(item: dict) -> str:
    return str(item.get("date") or item.get("recorded_at") or item.get("recorded_on") or "UNKNOWN")


def text_blob(item: dict) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).casefold()


def decision(item: dict) -> str:
    return str(item.get("decision") or "PENDING_REVIEW")


def display(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def render(item: dict) -> str:
    when = normalize_date(item)[:19]
    fix_id = item.get("fix_id")
    refs = [f"#{item['pr']}"] if item.get("pr") else []
    refs += [f"#{pr}" for pr in (item.get("related_prs") or []) if f"#{pr}" not in refs]
    tag = " ".join(refs)
    archive = item.get("artifact") or item.get("archive") or "no archive"
    change = (item.get("introduced_generic_change") or item.get("generic_change") or
              item.get("emulator_effect") or item.get("actual_source_defect") or
              item.get("reason") or item.get("change") or item.get("source_changes") or
              item.get("experiment") or "UNRECORDED")
    effect = item.get("game_effect") or item.get("game_result") or item.get("graphics") or "NOT_OBSERVED"
    next_step = (item.get("next_check") or item.get("next_test") or
                 item.get("next_falsifiable_test") or item.get("next") or "UNRECORDED")
    tested = ", ".join(item.get("tested_games") or []) or "none confirmed"
    games = ", ".join(sorted(game_keys(item))) or "not applicable"
    merge = (item.get("merge_commit") or item.get("main_merge_sha") or
             item.get("main_merge_commits") or "not merged / not verified")
    origin = item.get("_journal_review_file")
    return (
        f"{when}  [{decision(item)}]  games={games}  tested={tested}\n"
        f"  {archive}" + (f"  {tag}" if tag else "") +
        (f"  fix={fix_id}" if fix_id else "") + "\n" +
        (f"  REVIEW: {origin}\n" if origin else "") +
        f"  EMULATOR: {display(change)}\n"
        f"  GAME: {display(effect)}\n"
        f"  MAIN: {display(merge)}\n"
        f"  NEXT: {display(next_step)}"
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", type=Path, default=SHARED)
    p.add_argument("--private-ledger", type=Path, default=PRIVATE)
    p.add_argument("--registry", type=Path, default=REGISTRY)
    p.add_argument("--reviews", type=Path, help="Shared review directory; defaults to <ledger parent>/reviews")
    p.add_argument("--game", help="Context or actually tested title/serial")
    p.add_argument("--find", default="", help="Case-insensitive substring in any trial or review field")
    p.add_argument("--decision", default="", help="Filter by exact decision")
    p.add_argument("--rejected", action="store_true", help="Include rejected/superseded approaches")
    p.add_argument("--next", action="store_true", help="Only work needing validation/action")
    p.add_argument("--fix", default="", help="Search universal FIXES registry by id or text")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--all", action="store_true", help="Show every matching record")
    p.add_argument("--json", action="store_true", help="Machine-readable results")
    args = p.parse_args()
    if args.limit < 0:
        p.error("Negative --limit")

    shared = args.ledger.expanduser()
    reviews = args.reviews.expanduser() if args.reviews else shared.parent / "reviews"
    merged = {}
    # Earlier immutable rows win exact-ID collisions. Addenda supplement rather
    # than replace old observations. Context games never become tested games.
    for batch in (load_ledger(shared), load_reviews(reviews), load_ledger(args.private_ledger.expanduser())):
        for item in batch:
            key = record_key(item)
            if key not in merged:
                merged[key] = item

    entries = list(merged.values())
    if args.game:
        entries = [x for x in entries if args.game.casefold() in {s.casefold() for s in game_keys(x)}]
    if args.decision:
        entries = [x for x in entries if decision(x).casefold() == args.decision.casefold()]
    if args.rejected:
        entries = [x for x in entries if "REJECT" in decision(x).upper() or
                   decision(x).upper().startswith("SUPERSEDED")]
    if args.next:
        entries = [x for x in entries if not decision(x).upper().startswith(("REJECT", "SUPERSEDED"))
                   and decision(x).upper() not in ("MERGED_MAIN", "PROVEN_GENERIC")]
    if args.find:
        needle = args.find.casefold()
        entries = [x for x in entries if needle in text_blob(x)]

    fixes = []
    registry_path = args.registry.expanduser()
    if registry_path.is_file():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
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
        raise ValueError("Registry unavailable: " + str(registry_path))

    if args.fix:
        needle = args.fix.casefold()
        wanted_ids = {str(f.get("id", "")).casefold() for f in fixes}
        wanted_prs = {pr for f in fixes for pr in (f.get("related_prs") or [])}
        entries = [entry for entry in entries
                   if str(entry.get("fix_id") or "").casefold() in wanted_ids
                   or entry.get("pr") in wanted_prs
                   or bool(set(entry.get("related_prs") or []).intersection(wanted_prs))
                   or needle in text_blob(entry)]

    entries.sort(key=lambda x: (normalize_date(x), str(x.get("record_id") or
                 x.get("artifact") or x.get("archive") or "")), reverse=True)
    total = len(entries)
    if not args.all and args.limit:
        entries = entries[:args.limit]
    if args.json:
        print(json.dumps({"matching_trial_count": total, "trials": entries, "fixes": fixes},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    counts = Counter(decision(x) for x in merged.values())
    print("UNIVERSAL_DIARY_ENTRIES=" + str(len(merged)))
    print("SHARED_REVIEW_ENTRIES=" + str(sum("_journal_review_file" in x for x in merged.values())))
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
