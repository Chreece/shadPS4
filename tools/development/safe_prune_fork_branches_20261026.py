#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conservative, SHA-audited fork branch cleanup; dry-run by default.

Never deletes open PR heads/bases, unique unmerged work, canonical game
worktrees or named protected proof references. --apply only after a separately
reviewed dry run. Archive tags are pushed before any branch deletion.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen

REPO = "Chreece/shadPS4"
REMOTE = "https://github.com/Chreece/shadPS4.git"
CANDIDATE_PREFIX = (
    "diagnostic/", "diagnostics/", "experiment/", "replay/", "test/",
    "build/", "ci/", "playtest/",
)
PERMANENT_PREFIX = (
    "got/", "gow/", "games/", "proof/", "pr-ready/", "sync/", "maintenance/",
    "integration/", "import/", "release/", "ops/", "packaging/",
)
PERMANENT = {"main", "master"}
EXACT_SHA = re.compile(r"^[a-f0-9]{40}$")


def command(*args, cwd=None, check=True, capture=True):
    proc=subprocess.run([str(arg) for arg in args],cwd=cwd,text=True,
                        stdout=subprocess.PIPE if capture else None,
                        stderr=subprocess.PIPE if capture else None,
                        check=False,timeout=1800)
    if check and proc.returncode:
        raise RuntimeError("Command failed: "+' '.join(str(x) for x in args[:5])+
                           " / "+(proc.stderr or "")[-800:])
    return proc


def github_json(path):
    url=f"https://api.github.com/repos/{REPO}/{path}"
    headers={"Accept":"application/vnd.github+json",
             "User-Agent":"shadPS4-safe-pruner-20261026"}
    token=os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"]="Bearer "+token
    req=Request(url,headers=headers)
    with urlopen(req,timeout=30) as resp:
        return json.load(resp)


def current_pr_branches():
    protected=set()
    entries=[]
    for page in range(1,12):
        batch=github_json(f"pulls?state=open&per_page=100&page={page}")
        if not isinstance(batch,list):
            raise RuntimeError("Could not retrieve live open PR protections")
        for pr in batch:
            for key in ("head","base"):
                obj=pr.get(key)
                if isinstance(obj,dict):
                    ref=obj.get("ref")
                    if isinstance(ref,str):
                        protected.add(ref)
            entries.append({"number":pr["number"],"head":pr["head"]["ref"],
                            "base":pr["base"]["ref"]})
        if len(batch)<100:
            break
    else:
        raise RuntimeError("Unexpected PR pagination depth: fail closed")
    return protected,entries


def local_worktree_branches():
    # Never delete a remote branch checked out anywhere in the known project
    # worktrees. Scan only Git metadata, not large games/build artifacts.
    bases=[Path.home()/"projects/shadps4", Path.home()/"Applications"]
    excluded={"build","logs","externals","node_modules","cache","shader",
              "shaders","captures","screenshots","CMakeFiles","roms","vendor"}
    found=set()
    for base in bases:
        if not base.is_dir():
            continue
        for parent,dirs,names in os.walk(base,followlinks=False):
            root=Path(parent)
            try:
                depth=len(root.relative_to(base).parts)
            except ValueError:
                continue
            if depth>7:
                dirs[:]=[]
                continue
            if ".git" in dirs or ".git" in names:
                try:
                    r=command("git","-C",root,"worktree","list","--porcelain",
                              check=False)
                    if r.returncode==0:
                        for line in r.stdout.splitlines():
                            if line.startswith("branch refs/heads/"):
                                found.add(line.removeprefix("branch refs/heads/"))
                except Exception:
                    pass
                if ".git" in dirs:
                    dirs.remove(".git")
            dirs[:]=[d for d in dirs if d not in excluded and
                     not (root/d).is_symlink()]
    return found


def remote_refs():
    data=command("git","ls-remote","--heads",REMOTE).stdout
    refs={}
    for line in data.splitlines():
        sha,ref=line.split("\t",1)
        prefix="refs/heads/"
        if not EXACT_SHA.fullmatch(sha) or not ref.startswith(prefix):
            raise RuntimeError("Malformed remote branch reference")
        refs[ref[len(prefix):]]=sha
    return refs


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true",
                        help="Actually archive then delete only independently verified redundant refs")
    parser.add_argument("--manifest",type=Path,
                        default=Path(__file__).resolve().parents[2]/
                        "docs/emulator/BRANCH_SNAPSHOT_2026-10-26.json")
    parser.add_argument("--output",type=Path,
                        default=Path.home()/"projects/shadps4/logs")
    args=parser.parse_args()
    if args.manifest.is_file():
        manifest=json.loads(args.manifest.read_text())
    else:
        # A standalone one-paste script may live under ~/projects/shadps4/scripts,
        # not inside a git checkout. Fetch the same immutable audit snapshot.
        pinned_url=("https://raw.githubusercontent.com/Chreece/shadPS4/"
                    "3a1cfba433b9486145760b04d3bf155b19922318/"
                    "docs/emulator/BRANCH_SNAPSHOT_2026-10-26.json")
        req=Request(pinned_url,headers={"User-Agent":"shadPS4-safe-pruner-20261026"})
        with urlopen(req,timeout=30) as response:
            manifest=json.load(response)
    if manifest.get("repo")!=REPO or not isinstance(manifest.get("branches"),list):
        raise RuntimeError("Invalid immutable branch inventory")
    pinned={r["ref"]:r["commit"] for r in manifest["branches"]}
    if len(pinned)!=len(manifest["branches"]):
        raise RuntimeError("Duplicate refs in archive snapshot")
    protected,open_prs=current_pr_branches()
    local_active=local_worktree_branches()
    protected.update(local_active)
    current=remote_refs()
    required={"main","got/proven-progress-20261026","gow/proven-progress-20261026"}
    if not required.issubset(current):
        raise RuntimeError("Missing current main or canonical game refs")

    stamp=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.output.mkdir(parents=True,exist_ok=True)
    report=args.output/f"shadps4-fork-prune-{stamp}.json"
    audit={"timestamp":stamp,"mode":"APPLY" if args.apply else "DRY_RUN",
           "initial_remote_refs":current,"open_prs":open_prs,
           "protected_by_pr_or_active_worktree":sorted(protected),
           "active_local_worktree_branches":sorted(local_active),
           "immutable_manifest":str(args.manifest),"eligible":[],
           "skipped":{},"operations":[]}
    # Need complete Git ancestry rather than comparing short names/dates.
    with tempfile.TemporaryDirectory(prefix="shadps4-safe-prune-") as tmp:
        bare=Path(tmp)/"repo.git"
        command("git","init","--bare",bare)
        command("git","remote","add","origin",REMOTE,cwd=bare)
        command("git","-c","http.version=HTTP/1.1","fetch","--no-tags","origin",
                "+refs/heads/*:refs/remotes/origin/*",cwd=bare)
        baseline=[current[name] for name in required]
        for name,sha in sorted(current.items()):
            reason=None
            if name in PERMANENT or name.startswith(PERMANENT_PREFIX):
                reason="canonical/protected branch"
            elif name in protected:
                reason="open PR head/base or active local worktree"
            elif not name.startswith(CANDIDATE_PREFIX):
                reason="outside conservative cleanup prefixes"
            elif name not in pinned:
                reason="created after immutable inventory"
            elif pinned[name]!=sha:
                reason="branch changed since immutable inventory"
            else:
                integrated=any(
                    command("git","merge-base","--is-ancestor",sha,tip,
                            cwd=bare,check=False).returncode==0 for tip in baseline
                )
                if not integrated:
                    reason="unique commits not fully integrated into main/GoT/GoW"
            if reason is None:
                audit["eligible"].append({"name":name,"sha":sha})
            else:
                audit["skipped"][name]=reason
        report.write_text(json.dumps(audit,indent=2,sort_keys=True)+"\n")
        print(f"SAFE_CLEANUP_ELIGIBLE={len(audit['eligible'])}")
        print(f"UNTOUCHED_BRANCHES={len(audit['skipped'])}")
        print(f"DRY_RUN_REPORT={report}")
        if not args.apply:
            print("NO_REMOTE_BRANCHES_DELETED=YES")
            return 0
        # No deletion without an exact SHA audit, live PR protection and
        # preserved recovery tag. No force push, and no rewrite of main.
        for candidate in audit["eligible"]:
            name,sha=candidate["name"],candidate["sha"]
            live_prs,_=current_pr_branches()
            live_active=local_worktree_branches()
            live_refs=remote_refs()
            if name in live_prs or name in live_active or live_refs.get(name)!=sha:
                audit["operations"].append({"name":name,"result":"SKIPPED_LIVE_REF_OR_PR_CHANGED"})
                continue
            tag="fork-archive/"+stamp+"/"+name
            command("git","tag","-a",tag,sha,"-m",
                    f"Archived redundant fork branch {name} ({sha})",cwd=bare)
            command("git","push","origin",f"refs/tags/{tag}:refs/tags/{tag}",cwd=bare)
            # Guard immediately before deletion to avoid racing branch changes.
            if remote_refs().get(name)!=sha:
                audit["operations"].append({"name":name,"result":"SKIPPED_SHA_CHANGED_AFTER_ARCHIVE",
                                            "backup":tag})
                continue
            deleted=command("git","push",
                            f"--force-with-lease=refs/heads/{name}:{sha}",
                            "origin",f":refs/heads/{name}",cwd=bare,check=False)
            audit["operations"].append({"name":name,"result":"DELETED" if deleted.returncode==0
                                        else "FAILED_TO_DELETE","backup":tag,"sha":sha})
            report.write_text(json.dumps(audit,indent=2,sort_keys=True)+"\n")
    report.write_text(json.dumps(audit,indent=2,sort_keys=True)+"\n")
    print(f"FINAL_CLEANUP_AUDIT={report}")
    print("MAIN_AND_GAME_BRANCHES_CHANGED=NO")
    return 0


if __name__=="__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"SAFE_PRUNE_BLOCKED={type(exc).__name__}: {exc}",file=sys.stderr)
        raise SystemExit(2)
