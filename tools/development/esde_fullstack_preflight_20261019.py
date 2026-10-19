#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only evidence audit and isolated source-merge preflight for GoW + GoT.

No emulator launch, ES-DE selection, game/config/save changes, process signals,
system updates, GPU/driver resets, service changes or SSH termination.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

ROOT = Path.home() / "projects/shadps4"
ESDE = ROOT / "ESDE"
LOGS = ROOT / "logs"
UNIFIED_SOURCE = ESDE / "unified-20261018-160757/source"
UNIFIED_BIN = Path.home() / "Applications/shadps4/shadps4"
UNIFIED_SHA = "d8ef5b2ae1b12464a5d4ca83d8e5e9c3b0f2f0053148c531eda7457c9c21117d"
UPSTREAM_SHA = "d615b3887ba29025a59242e1a26f5b68bb176158"
GOW_REF = "f909c3abb1ac1e7c8d87de833d3659f7d0b8fc87"
GOT_VISUAL_REF = "e9f470b359845a456139a865aa1d7b3001f0dbfd"
GOW = ROOT / "GodOfWarRagnarok"
GOT = ROOT / "GhostOfTsushima"
GOW_SOURCE = GOW / "tests/native-rendered-gfx-20261016-152535-3014679/source"
GOW_RENDERER = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
GOW_RENDERER_SHA = "bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a"
GOW_BIN = GOW / ("tests/gow-renderer-log-hotpath-20261017/build/"
                 "shadps4.fullgfx-uffd-batched-read-gow-uffd-batched-read-20261017-231412")
GOW_BIN_SHA = "8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef"
GOT_V51_PARENT = "02be53c70ac824ca42b1df41af93163f0e4bdfe5b1666f270193f51fd58f155d"
GOT_V51_RESULT = "814677f63524610ba23932fd4ebbe062be299d641ac4699c390d62fe1cc1b03b"
WATCH_FILES = (
    "src/core/cpu_id.cpp",
    "src/core/cpu_affinity.cpp",
    "src/core/cpu_id_translation/launcher.cpp",
    "src/video_core/page_manager.cpp",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
    "src/shader_recompiler/ir/passes/srt_offset_codegen.h",
    "src/core/libraries/audio/audioout.cpp",
    "src/core/libraries/audio3d/audio3d.cpp",
    "src/video_core/amdgpu/liverpool.cpp",
    "src/core/devtools/quit_dialog.h",
    "src/core/startup_progress.h",
    "src/imgui/startup_loading.cpp",
)


def digest(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def command(*args: str, cwd: Path | None = None, check: bool = True):
    p = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"Command failed ({p.returncode}): {' '.join(map(str,args))}\n{p.stderr[-1500:]}")
    return p


def git(repo: Path, *args: str, check: bool = True):
    return command("git", "-C", str(repo), *args, check=check)


def source_status(path: Path):
    if not path.is_dir() or not (path / ".git").exists():
        return {"exists": False}
    s = git(path, "status", "--porcelain", "--untracked-files=no", check=False)
    h = git(path, "rev-parse", "HEAD", check=False)
    return {"exists": True, "head": h.stdout.strip(), "tracked_dirty": s.stdout.splitlines()[:50],
            "status_rc": s.returncode, "sha": {k: digest(path / k) for k in WATCH_FILES}}


def find_got_stencil_files():
    raster = Path(GOW_RENDERER)
    paths = []
    # Bounded scans in precisely the two known workspaces; never scan games or
    # system directories, follow symlinks or import third-party code.
    for parent in (GOT / "tests", GOT / "builds"):
        if not parent.is_dir():
            continue
        for pattern in ("got-v*/src", "got-v*/source", "got*/source", "got-v*"):
            paths.extend(parent.glob(pattern))
    found = {}
    for base in list(dict.fromkeys(paths))[:450]:
        if base.is_symlink():
            continue
        candidate = base / raster
        h = digest(candidate)
        if h in (GOT_V51_PARENT, GOT_V51_RESULT) and h not in found:
            found[h] = str(candidate)
    return found


def recent_game_logs():
    roots = [Path.home() / "Applications/shadps4/user/log",
             Path.home() / ".local/share/shadPS4/log",
             ROOT / "ESDE/unified-20261018-160757/source/user/log",
             ROOT / "ESDE/unified-20261018-160757/build/user/log"]
    # This is limited to log directories rather than recursively walking
    # the user's large emulation/video/save libraries.
    for game in (GOW, GOT):
        for sub in ("logs", "play"):
            parent = game / sub
            if not parent.is_dir():
                continue
            roots += [p for p in parent.glob("*/user/log") if p.is_dir()][:50]
    choices = []
    for root in roots:
        if not root.is_dir():
            continue
        for ext in ("*.log", "shad_log.txt", "*.txt"):
            for item in root.glob(ext):
                if not item.is_file() or item.is_symlink():
                    continue
                if item.stat().st_size > 100_000_000:
                    continue
                choices.append(item)
    choices.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    used = set()
    found = []
    for p in choices:
        if str(p) in used or len(found) >= 12:
            continue
        used.add(str(p))
        # Keep only bounded tail and fault-centric lines, no games/saves or
        # user config copied. Sources are never included in the upload archive.
        raw = p.read_bytes()[-220_000:].decode("utf-8", "replace")
        lines = raw.splitlines()
        fault = [x for x in lines if re.search(
            r"SIGSEGV|SIGILL|SIGABRT|SIGTRAP|VUID-|Device.?Lost|FAULT|critical|"
            r"Unhandled access|gpu fault|userfaultfd|stack_chk|GOW_SRT|"
            r"CpuId|fatal|ERROR", x, re.I)]
        found.append({"path": str(p), "mtime": int(p.stat().st_mtime),
                      "size": p.stat().st_size, "tail": "\n".join(lines[-90:]),
                      "fault_lines": fault[-120:]})
    return found


def stage_preflight(report: dict, run: Path):
    # Git's merge engine computes and reports ALL conflicts in one pass.
    # This staging repo is disposable; do not change existing user sources,
    # the live launcher, or either game's protected trees.
    source = UNIFIED_SOURCE
    state = report["source"]["unified"]
    if not state.get("exists") or state.get("tracked_dirty"):
        raise RuntimeError("Existing ES-DE source is missing or dirty; no staging")
    if not report["checks"]["installed_unified_matches_known_sha"]:
        raise RuntimeError("Installed core changed; refusing to stage against an unverified base")
    if not report["checks"]["gow_binary_matches_proven_sha"]:
        raise RuntimeError("Proven GoW full-graphics executable missing/modified; no staging")
    if not report["checks"]["gow_renderer_matches_proven_sha"]:
        raise RuntimeError("Proven GoW source rasterizer mismatch; no staging")
    if not report["checks"]["got_v51_both_source_versions_found"]:
        raise RuntimeError("GoT v51 source ancestry not available; no staged overlay")

    proof_git = GOW_SOURCE
    for p in (source, proof_git):
        if not (p / ".git").exists():
            raise RuntimeError(f"Missing git provenance: {p}")
    # Read-only local transport: record which proof branch and path actually
    # changed; merging a branch is NOT enough to certify the compiled overlay.
    staging = run / "source"
    git(source, "worktree", "add", "--detach", str(staging), state["head"])
    command("git", "-C", str(staging), "config", "user.name", "ES-DE Source Audit")
    command("git", "-C", str(staging), "config", "user.email",
            "esde-fullstack@users.noreply.github.com")
    # Import local, potentially unpushed Git objects into the isolated
    # worktree's object store, never push/fetch over untrusted remote.
    git(staging, "fetch", "--no-tags", str(proof_git), "HEAD")
    proven_head = git(staging, "rev-parse", "FETCH_HEAD").stdout.strip()
    report["stage"] = {"path": str(staging), "source_head": state["head"],
                       "local_proof_head": proven_head,
                       "runtime_proof_binary_sha256": GOW_BIN_SHA}
    git(staging, "merge-base", "--is-ancestor", GOW_REF, proven_head)
    # Stage the protected local GoW *source ancestry*; the post-merge proof
    # still requires source-object hashes and compiled runtime A/B.
    merged = git(staging, "merge", "--no-commit", "--no-ff",
                 proven_head, check=False)
    conflicts = git(staging, "diff", "--name-only", "--diff-filter=U")
    report["stage"]["gow_merge_rc"] = merged.returncode
    report["stage"]["conflicts"] = conflicts.stdout.splitlines()
    report["stage"]["merge_output"] = (merged.stdout + merged.stderr)[-8000:]
    report["stage"]["conflict_diff"] = git(staging, "diff", "--cc", check=False).stdout[:300_000]
    # No "ours"/"theirs" conflict auto-choice, no source replacement.
    # Avoid launching an emulator from unverified hardware semantics.
    if merged.returncode or conflicts.stdout.strip():
        report["stage"]["status"] = "REQUIRES_EXPLICIT_SOURCE_RECONCILIATION_NO_BUILD"
        return
    report["stage"]["status"] = "GOW_GIT_ANCESTRY_MERGED_ONLY_LOCAL_OVERLAYS_NOT_YET_VERIFIED"
    # Do NOT merge the experimental Ghost 27k-table GPU-feedback branch.
    # Its last world-visible reference is recorded in report for a focused
    # stencil/resource port after GoW's exact source+object proof.


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", action="store_true",
                    help="Also try a source-only GoW merge in a NEW disposable worktree")
    args = ap.parse_args()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS.mkdir(parents=True, exist_ok=True)
    run = ESDE / ("fullstack-audit-" + stamp)
    run.mkdir(parents=True)
    record = run / "audit.json"
    archive = LOGS / ("esde-fullstack-audit-" + stamp + ".tar.gz")
    report = {
        "date": dt.datetime.now(dt.timezone.utc).isoformat(),
        "installed_binary": {"path": str(UNIFIED_BIN), "sha256": digest(UNIFIED_BIN)},
        "goW_proven_binary": {"path": str(GOW_BIN), "sha256": digest(GOW_BIN)},
        "source": {"unified": source_status(UNIFIED_SOURCE),
                   "gow": source_status(GOW_SOURCE)},
        "ghost_v51": find_got_stencil_files(),
        "goW_checkpoint": GOW_REF, "goT_world_visible": GOT_VISUAL_REF,
        "official_upstream_20261019": UPSTREAM_SHA,
        "logs": recent_game_logs(),
        "checks": {},
        "note": "No game was launched; binary/source proof is not a diagnosis of the current crash."
    }
    report["checks"] = {
        "installed_unified_matches_known_sha": digest(UNIFIED_BIN) == UNIFIED_SHA,
        "gow_binary_matches_proven_sha": digest(GOW_BIN) == GOW_BIN_SHA,
        "gow_renderer_matches_proven_sha":
            digest(GOW_SOURCE / GOW_RENDERER) == GOW_RENDERER_SHA,
        "got_v51_both_source_versions_found":
            all(h in report["ghost_v51"] for h in (GOT_V51_PARENT, GOT_V51_RESULT)),
        "unified_lacks_guest_cpuid_source":
            report["source"]["unified"].get("sha", {}).get("src/core/cpu_id.cpp") is None,
        "unified_lacks_guest_affinity_source":
            report["source"]["unified"].get("sha", {}).get("src/core/cpu_affinity.cpp") is None,
    }
    if args.stage:
        try:
            stage_preflight(report, run)
        except Exception as e:
            report["stage"] = {**report.get("stage", {}),
                               "status": "STOPPED_WITHOUT_INSTALLING",
                               "exception": str(e)}
    record.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    # Only bounded audit evidence, not saved games, full source trees or config.
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(record, arcname="esde-fullstack-audit/audit.json")
    print("FULLSTACK_AUDIT="+str(record))
    print("FULLSTACK_ARCHIVE="+str(archive))
    for key, ok in report["checks"].items():
        print(key.upper()+"="+str(ok))
    if args.stage:
        print("STAGE_STATUS="+report.get("stage", {}).get("status", "NOT_STARTED"))
        print("STAGE_CONFLICT_COUNT="+str(len(report.get("stage", {}).get("conflicts", []))))
    print("INSTALLED_EMULATOR_UNCHANGED=YES")
    print("SUNSHINE_SSH_SAVES_CONFIG_DRIVER_UNCHANGED=YES")


if __name__ == "__main__":
    main()
