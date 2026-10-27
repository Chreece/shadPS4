#!/usr/bin/env python3
"""Deterministic c925 PR74/75/77/78 source-only merger. Never launches a game.

Everything happens in an existing, isolated experimental Git worktree. Every
subprocess is logged and checked; no Bash errexit, no force apply or reset.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime

C925 = "c925523b49ef39434024b16b2b014d120471ee1a"
ELF_SHA = "260ed43abbe538127ab82b3580a48c5f67be19ac4bbdd32ebefd78c73cdaf50d"
PATCH_SHA = {
    74: "91f4857007e579933cf88fd27a51418a9b63c312",
    75: "26f6e58b238ccccbed011558c47acb18af6f8178",
    77: "89d866b761857e5f47e3998db07af52a61463ab1",
    78: "011f50c42471c79a2187fa26a784b114c676c3ff",
}
BASE_REF = {
    74: "074f2478efcb159359f66c12d33b1db080d070ad",
    75: "8b8807b96701c70f99c0b7d29391767e8a7f4c6a",
    77: "5a8665c89b5ac9c4b930a51add6a4b9d55c809a2",
    78: "ffb892994002593549a7a4537a8593ff370df160",
}
ROOT = Path.home() / "projects/shadps4/GhostOfTsushima"
SRC = ROOT / "experiments/c925-typed-native-20261027-083533/source"
PATCHES = Path.home() / "projects/shadps4/logs/c925-typed-native-20261027-083533/patches"
GOOD = ROOT / "candidates" / f"{C925}-strided-precise-image-readbacks-{ELF_SHA}" / "shadps4"
LOGS = Path.home() / "projects/shadps4/logs"
OUT = LOGS / f"c925-python-merge-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
OUT.mkdir(parents=True, exist_ok=True)
STATUS = "PREFLIGHT"
RECORDED: list[dict] = []


class Stop(Exception):
    def __init__(self, code: str, rc: int = 7):
        super().__init__(code)
        self.code = code
        self.rc = rc


def stage(name: str) -> None:
    global STATUS
    STATUS = name
    print(f"STAGE={name}", flush=True)
    (OUT / "progress.txt").open("a").write(f"STAGE={name}\n")


def run(name: str, args: list[str], *, cwd: Path | None = None, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Every command has a full separate output file and an explicit result."""
    clean = re.sub(r"[^A-Za-z0-9_.-]", "-", name)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=env, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        (OUT / f"{clean}.log").write_text(str(exc) + "\n")
        raise Stop(f"{clean}_PROCESS_ERROR") from exc
    (OUT / f"{clean}.log").write_text(result.stdout)
    RECORDED.append({"step": name, "exit": result.returncode, "args": args})
    if check and result.returncode:
        raise Stop(f"{clean}_EXIT_{result.returncode}")
    return result


def git(name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(name, ["git", "-C", str(SRC), *args], check=check)


def verify() -> list[int]:
    stage("VERIFY_PREVIOUS_EXPERIMENT_AND_MENU_ELF")
    if not ((SRC / ".git").exists() and PATCHES.is_dir() and GOOD.is_file()):
        raise Stop("ISOLATED_SOURCE_PATCHES_OR_BINARY_MISSING", 2)
    digest = hashlib.sha256()
    with GOOD.open("rb") as stream:
        for part in iter(lambda: stream.read(2**20), b""):
            digest.update(part)
    if digest.hexdigest() != ELF_SHA:
        raise Stop("PROTECTED_MENU_ELF_HASH_MISMATCH", 2)
    if git("source-ancestor", "merge-base", "--is-ancestor", C925, "HEAD", check=False).returncode:
        raise Stop("C925_NOT_SOURCE_ANCESTOR", 2)
    if git("source-clean", "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise Stop("EXPERIMENT_SOURCE_HAS_TRACKED_CHANGES", 2)
    history = git("committed-stages", "log", "-8", "--format=%s").stdout
    existing: list[int] = []
    for p in PATCH_SHA:
        if f"source port PR{p} from c925" in history:
            existing.append(p)
    expected_prefix = list(PATCH_SHA)[:len(existing)]
    if existing != expected_prefix:
        raise Stop(f"UNEXPECTED_EXPERIMENT_COMMIT_ORDER_{existing}", 2)
    for p, expected in PATCH_SHA.items():
        path = PATCHES / f"PR{p}.patch"
        if not path.is_file():
            raise Stop(f"PR{p}_EXISTING_PATCH_MISSING", 2)
        actual = git(f"hash-pr{p}", "hash-object", str(path)).stdout.strip()
        if actual != expected:
            raise Stop(f"PR{p}_HASH_MISMATCH_{actual}", 2)
    stage("ALL_FOUR_VERIFIED_ENTERING_FIRST_UNMERGED_STAGE")
    return existing


def get_base(p: int, patch: Path) -> None:
    contents = patch.read_text()
    matches: list[tuple[str, str]] = []
    for section in re.split(r"(?=^diff --git a/)", contents, flags=re.M):
        fn = re.search(r"^diff --git a/(.*?) b/", section, re.M)
        index = re.search(r"^index ([a-f0-9]{40})\.\.([a-f0-9]{40})", section, re.M)
        if fn and index and int(index.group(1), 16):
            matches.append((index.group(1), fn.group(1)))
    if not matches:
        raise Stop(f"PR{p}_PATCH_BASE_HEADER_MISSING")
    (OUT / f"pr{p}-ancestors.json").write_text(json.dumps(matches, indent=2))
    for i, (blob, file_path) in enumerate(matches):
        if git(f"pr{p}-base-present-{i}", "cat-file", "-e", blob + "^{blob}", check=False).returncode == 0:
            continue
        url = f"https://raw.githubusercontent.com/Chreece/shadPS4/{BASE_REF[p]}/{file_path}"
        (OUT / f"pr{p}-base-url-{i}.txt").write_text(url + "\n")
        dest = OUT / f"pr{p}-ancestor-{i}.src"
        run(f"pr{p}-base-download-{i}", ["curl", "-4", "--http1.1", "-fLsS", "--retry", "3", "--max-time", "60", url, "-o", str(dest)], timeout=90)
        got = git(f"pr{p}-base-hash-{i}", "hash-object", str(dest)).stdout.strip()
        if got != blob:
            raise Stop(f"PR{p}_BASE_BLOB_HASH_MISMATCH_{i}")
        written = git(f"pr{p}-base-import-{i}", "hash-object", "-w", str(dest)).stdout.strip()
        if written != blob:
            raise Stop(f"PR{p}_BASE_IMPORT_MISMATCH_{i}")


def apply_one(p: int) -> None:
    patch = PATCHES / f"PR{p}.patch"
    stage(f"PR{p}_DIRECT_SOURCE_CHECK")
    direct = git(f"pr{p}-direct-check", "apply", "--check", str(patch), check=False)
    if direct.returncode == 0:
        git(f"pr{p}-direct-apply", "apply", str(patch))
    else:
        stage(f"PR{p}_LOAD_EXACT_ANCESTOR_GIT_BLOBS")
        get_base(p, patch)
        stage(f"PR{p}_THREE_WAY_CONFLICT_CHECK")
        trial = git(f"pr{p}-3way-check", "apply", "--3way", "--check", str(patch), check=False)
        if trial.returncode:
            raise Stop(f"PR{p}_GENUINE_THREE_WAY_CONFLICT", 8)
        git(f"pr{p}-3way-apply", "apply", "--3way", str(patch))
    git(f"pr{p}-whitespace", "diff", "--check")
    if p == 74:
        stage("PR74_NATIVE_INDEPENDENT_CPP_COHERENCE_FIXTURE")
        source = SRC / "tests/guest_descriptor_coherence_test.cpp"
        if not source.is_file():
            raise Stop("PR74_COHERENCE_FIXTURE_SOURCE_MISSING")
        run("pr74-fixture-build", ["c++", "-std=c++20", "-O2", "-DNDEBUG", "-Wall", "-Wextra", "-Werror", "-pedantic", "-I", str(SRC / "src"), str(source), "-o", str(OUT / "coherence-fixture")], timeout=100)
        run("pr74-fixture-execute", [str(OUT / "coherence-fixture")], timeout=15)
    git(f"pr{p}-stage", "add", "--", "src", "tests")
    git(f"pr{p}-commit", "-c", "user.name=Isolated c925 source test", "-c", "user.email=isolated-c925@invalid.local", "commit", "--no-gpg-sign", "-m", f"source port PR{p} from c925")
    stage(f"PR{p}_SOURCE_PORT_COMMITTED")


def save_result(rc: int) -> None:
    state = {"schema_version": 2, "record_type": "LOCAL_C925_SOURCE_MERGE_PYTHON",
             "status": STATUS, "runner_exit": rc, "base": C925,
             "game_launched": False, "protected_binary_overwritten": False,
             "commands": RECORDED}
    (OUT / "trial.json").write_text(json.dumps(state, indent=2) + "\n")
    try:
        with (LOGS / "emulator-trials-local.jsonl").open("a") as f:
            f.write(json.dumps({k: state[k] for k in ("schema_version", "record_type", "status", "runner_exit", "base", "game_launched")}) + "\n")
    except OSError as exc:
        print(f"LOCAL_DIARY_WRITE_ERROR={exc}", file=sys.stderr)
    try:
        state_now = subprocess.run(["git", "-C", str(SRC), "status", "--porcelain"], capture_output=True, text=True, timeout=8)
        (OUT / "source-final-status.txt").write_text(state_now.stdout + state_now.stderr)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        with tarfile.open(str(OUT) + ".tar.gz", "w:gz", compresslevel=1) as tar:
            tar.add(OUT, arcname=".")
        print(f"UPLOAD_THIS={OUT}.tar.gz", flush=True)
    except OSError as exc:
        print(f"ARCHIVE_ERROR={exc}", file=sys.stderr)
    print("SSH_SESSION=OPEN", flush=True)


def main() -> int:
    global STATUS
    rc = 0
    try:
        existing = verify()
        for p in PATCH_SHA:
            if p in existing:
                stage(f"PR{p}_ALREADY_COMMITTED_SKIP")
                continue
            apply_one(p)
        stage("ALL_TYPED_SOURCE_PATCHES_APPLIED_NO_GAME_TEST")
    except Stop as exc:
        STATUS = exc.code
        rc = exc.rc
        print(f"SOURCE_PORT_STOP={STATUS} EXIT={rc}", file=sys.stderr, flush=True)
    except BaseException as exc:
        STATUS = f"UNEXPECTED_PYTHON_EXCEPTION_{type(exc).__name__}"
        rc = 9
        import traceback
        (OUT / "unexpected-exception.txt").write_text(traceback.format_exc())
        print(f"SOURCE_PORT_STOP={STATUS}", file=sys.stderr, flush=True)
    finally:
        save_result(rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
