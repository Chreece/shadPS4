#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Audit omitted proven GoW/GoT sources; optionally A/B a protected GoW ELF.

No source edits, GPU resets, services, installed ES-DE changes, or process kills.
This is a binary/source provenance check, not a claim of restored gameplay.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path.home() / "projects/shadps4"
ESDE = ROOT / "ESDE"
LOGS = ROOT / "logs"
SRC = ESDE / "fullstack-audit-20261019-165723/source"
NEW = ESDE / "fullstack-audit-20261019-165723/build/shadps4"
OLD = ROOT / ("GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/"
              "shadps4.fullgfx-uffd-batched-read-gow-uffd-batched-read-20261017-231412")
OBSERVER = ROOT / "scripts/esde_unified_game_observer_20261019.py"
GOW_ROOT = ROOT / "GodOfWarRagnarok"
GHOST_REF = "e9f470b359845a456139a865aa1d7b3001f0dbfd"
RENDERER = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
RENDERER_SHA = "bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a"
NEW_SHA = "48ae4ac2ec8f42e2155425521c8699359b466dd6459905948762de539865b5bc"
OLD_SHA = "8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef"
INPUT_PROFILE = ESDE / "playtest-CUSA34384-20261019-185051/user"

def sha(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def execute(*args, check=True, timeout=150):
    proc = subprocess.run(list(map(str, args)), text=True, capture_output=True,
                          timeout=timeout)
    if check and proc.returncode:
        raise RuntimeError(
            f"Command failed rc={proc.returncode}: {args[0]}; "
            f"{proc.stderr[-1000:]}")
    return proc

def git(repo, *args, **kw):
    return execute("git", "-C", repo, *args, **kw)

def renderer_from_workspaces():
    """Read exact local files only; skip build, profiles and enormous trees."""
    found = []
    examined = 0
    for top in (GOW_ROOT / "tests", GOW_ROOT / "builds"):
        if not top.is_dir():
            continue
        for base, dirs, files in os.walk(top, followlinks=False):
            # No traversing .git, submodules, shader caches or emulated data.
            dirs[:] = [d for d in dirs if d not in (
                ".git", "build", "logs", "play", "shader", "user",
                "externals", "_deps", "bin-backups", "data", "savedata",
                "cache", "generated", "screenshots", "captures")]
            examined += 1
            if examined > 16000:
                return found, "DIRECTORY_SCAN_LIMIT_REACHED"
            if "vk_rasterizer.cpp" not in files:
                continue
            file = Path(base) / "vk_rasterizer.cpp"
            if not file.is_file() or file.is_symlink():
                continue
            if file.stat().st_size > 3_000_000:
                continue
            digest = sha(file)
            if digest == RENDERER_SHA:
                found.append(str(file))
                if len(found) >= 10:
                    return found, "STOPPED_AT_TEN_MATCHES"
    return found, "COMPLETE"

def renderer_from_git_history():
    """Search only reachable Git blobs for the SHA of the proven renderer."""
    bases = [
        GOW_ROOT / "tests/native-rendered-gfx-20261016-152535-3014679/source",
        GOW_ROOT / "tests/gow-renderer-log-hotpath-20261017/source",
    ]
    matches = []
    searches = []
    for root in bases:
        if not (root / ".git").exists():
            continue
        commits = git(root, "log", "--all", "--format=%H",
                      "--", RENDERER, timeout=35).stdout.splitlines()
        seen = set()
        for rev in commits[:400]:
            try:
                blob = git(root, "rev-parse", rev + ":" + RENDERER).stdout.strip()
            except RuntimeError:
                continue
            if blob in seen:
                continue
            seen.add(blob)
            info = execute("git", "-C", root, "cat-file", "-s", blob)
            if int(info.stdout.strip()) > 3_000_000:
                continue
            sample = subprocess.run(["git", "-C", str(root), "cat-file", "blob", blob],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if sample.returncode == 0 and hashlib.sha256(sample.stdout).hexdigest() == RENDERER_SHA:
                matches.append({"git_root":str(root), "commit":rev, "blob":blob})
        searches.append({"git_root":str(root),
                         "commits_considered":min(len(commits),400),
                         "blobs_checked":len(seen)})
    return matches, searches

def ghost_merge_preview():
    """Fetch pinned reference and preview conflicts; never merge source."""
    if not (SRC / ".git").exists():
        return {"status":"MISSING_FULLSTACK_GIT_SOURCE"}
    if git(SRC, "rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0:
        return {"status":"UNRESOLVED_GIT_MERGE_DO_NOT_PROBE"}
    before = git(SRC, "status", "--porcelain", "--untracked-files=no")
    got = git(SRC, "cat-file", "-e", GHOST_REF + "^{commit}", check=False)
    if got.returncode:
        # Object-only fetch; HEAD/worktree and the compiled source are unchanged.
        execute("git", "-C", SRC, "-c", "http.version=HTTP/1.1",
                "fetch", "--no-tags", "--no-recurse-submodules",
                "https://github.com/Chreece/shadPS4.git", GHOST_REF, timeout=180)
    head = git(SRC, "rev-parse", "HEAD").stdout.strip()
    tree = git(SRC, "merge-tree", "--write-tree", head, GHOST_REF,
               check=False, timeout=120)
    combined = tree.stdout + "\n" + tree.stderr
    entries = re.findall(r'^CONFLICT .*$', combined, re.M)
    after = git(SRC, "status", "--porcelain", "--untracked-files=no")
    if before.stdout != after.stdout:
        raise RuntimeError("Ghost source preview changed tracked worktree unexpectedly")
    return {"status":"MERGE_PREVIEW_ONLY",
            "fullstack_head":head, "ghost_world_ref":GHOST_REF,
            "rc":tree.returncode, "conflicts":entries[:150],
            "conflicts_count":len(entries),
            "output_excerpt":combined[-24000:],
            "compiled_worktree_has_local_overlays": bool(before.stdout.strip())}

def capture_old_gow(report, tag):
    if sha(OLD) != OLD_SHA or sha(NEW) != NEW_SHA:
        raise RuntimeError("One of the two GoW executables differs from pinned SHA")
    if not OBSERVER.is_file() or OBSERVER.is_symlink():
        raise RuntimeError("Pinned generic observer not available")
    if not INPUT_PROFILE.is_dir() or INPUT_PROFILE.is_symlink():
        raise RuntimeError("Original failed GoW portable profile is missing")
    if execute("pgrep", "-u", os.getuid(), "-x", "shadps4",
               check=False).returncode == 0:
        raise RuntimeError("Another emulator is active; do not interrupt it")
    trial = ESDE / ("reference-gow-" + tag)
    tmp_user = trial / "user"
    trial.mkdir()
    # This is a new isolated copy of the SAME config the failed new candidate
    # used, so a difference is more attributable to the binary.
    skip = shutil.ignore_patterns("shader", "log", "logs", "captures",
                                  "screenshots", "cache", "dumps")
    shutil.copytree(INPUT_PROFILE, tmp_user, symlinks=False, ignore=skip)
    display = os.environ.get("DISPLAY") or ":0"
    env = os.environ.copy()
    env["DISPLAY"] = display
    report_dir = LOGS / ("esde-reference-gow-" + tag)
    print("TESTING_PROVEN_GOW_ELF=" + OLD_SHA, flush=True)
    print("CWD=" + str(trial), flush=True)
    print("CONTROLLER_INPUT=MANUAL; CURRENT_ESDE_INSTALL=UNCHANGED", flush=True)
    cmd = [sys.executable, "-B", str(OBSERVER), "--binary", str(OLD),
           "--sha256", OLD_SHA, "--game", "CUSA34384",
           "--cwd", str(trial), "--report", str(report_dir),
           "--seconds", "90"]
    with (trial / "launch-command.json").open("w") as f:
        json.dump(cmd, f, indent=2)
    rc = subprocess.run(cmd, env=env).returncode
    if report_dir.is_dir():
        archive = LOGS / ("esde-reference-gow-" + tag + ".tar.gz")
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(report_dir, arcname=report_dir.name)
        print("UPLOAD_REFERENCE=" + str(archive), flush=True)
        try:
            outcome = json.loads((report_dir / "gameplay-result.json").read_text())
            report["reference_result"] = {
                "observer_rc":rc, "game_exit":outcome.get("exit_code"),
                "process_alive_at_end":outcome.get("process_alive_when_reported"),
                "failure":outcome.get("failure"),
            }
        except (OSError, ValueError):
            report["reference_result"] = {"observer_rc":rc}
    return rc

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-gow", action="store_true",
                        help="Run SHA-pinned proven GoW ELF using the failed build's cloned profile")
    args = parser.parse_args()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS.mkdir(parents=True, exist_ok=True)
    report = {"gameplay_claim":False, "installed_esde_changed":False,
              "candidate_sha":sha(NEW), "candidate_sha_expected":NEW_SHA,
              "gow_reference_sha":sha(OLD), "gow_reference_sha_expected":OLD_SHA,
              "missing_proven_renderer_sha256":RENDERER_SHA}
    result = 0
    try:
        found,status = renderer_from_workspaces()
        report["renderer_search"] = {"local":found, "status":status}
        found_git,stats = renderer_from_git_history()
        report["renderer_search"]["git"] = found_git
        report["renderer_search"]["git_stats"] = stats
        report["ghost_merge_preview"] = ghost_merge_preview()
        if args.reference_gow:
            result = capture_old_gow(report, stamp)
    except Exception as e:
        result = 2
        report["error"] = str(e)
        print("AUDIT_ERROR=" + str(e), flush=True)
    destination = LOGS / ("esde-fullstack-regression-triage-" + stamp + ".json")
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    archive = destination.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(destination, arcname="triage/manifest.json")
    print("RENDERER_SOURCE_MATCHES=" +
          str(len(report.get("renderer_search",{}).get("local",[])) +
              len(report.get("renderer_search",{}).get("git",[]))), flush=True)
    print("GHOST_MERGE_PREVIEW_CONFLICTS=" +
          str(report.get("ghost_merge_preview",{}).get("conflicts_count","UNAVAILABLE")),
          flush=True)
    print("UPLOAD_THIS=" + str(archive), flush=True)
    print("SOURCES_SAVES_ESDE_SUNSHINE_SSH_UNCHANGED=YES", flush=True)
    return result

if __name__ == "__main__":
    sys.exit(main())
