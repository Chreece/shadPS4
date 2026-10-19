#!/usr/bin/env python3
"""Resume clean GoW permutation build after missing Sirit nested SPIRV-Headers.

Verify the pinned protected source / binary, restore precisely Sirit's nested
SPIRV-Headers gitlink in the existing isolated worktree, then resume CMake/Ninja.
No game launch, source promotion, save mutation, or SSH/Ollama/Sunshine changes.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path.home() / "projects/shadps4"
GOW = ROOT / "GodOfWarRagnarok"
BASE = GOW / "gow-stage25-restored-20261011-7bpufvsl/source"
PROJECT = GOW / "tests/permutation-protected-20261019-220440"
STAGE = PROJECT / "source"
BUILD = PROJECT / "build"
SIRIT = STAGE / "externals/sirit"
NESTED = SIRIT / "externals/SPIRV-Headers"
ORIGINAL_NESTED = BASE / "externals/sirit/externals/SPIRV-Headers"
PROTECTED = GOW / "build/shadps4"
BASE_PIN = "3965a497532edb7b18027692ab04eb97fa4c145b"
CANDIDATE_PIN = "e6ca28e7386faa0624c1421a5dc0585eeb028532"
SIRIT_PIN = "fc2b42d5e57ee2f638676c0c02f7fd71934b596a"
HEADERS_PIN = "2acb319af38d43be3ea76bfabf3998e5281d8d12"
PROTECTED_SHA = "8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
UPSTREAM = "https://github.com/KhronosGroup/SPIRV-Headers.git"
LOGDIR = ROOT / "logs"
STAMP = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
MAIN = LOGDIR / f"gow-permutation-protected-resume-{STAMP}.log"
CMAKE_LOG = LOGDIR / f"gow-permutation-protected-resume-cmake-{STAMP}.log"
BUILD_LOG = LOGDIR / f"gow-permutation-protected-resume-compile-{STAMP}.log"
REPORT = LOGDIR / f"gow-permutation-protected-resume-{STAMP}.json"
ARCHIVE = LOGDIR / f"gow-permutation-protected-resume-{STAMP}.tar.gz"

def note(message):
    print(message, flush=True)
    with MAIN.open("a", encoding="utf-8") as f:
        f.write(message + "\n")

def run(args, *, log=None, cwd=None, check=True):
    cmd = [str(x) for x in args]
    if log:
        with log.open("w", encoding="utf-8") as stream:
            completed = subprocess.run(cmd, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT)
        if completed.returncode:
            note("COMMAND_FAILED_LOG_TAIL_START")
            for line in log.read_text(errors="replace").splitlines()[-85:]:
                note(line)
            note("COMMAND_FAILED_LOG_TAIL_END")
    else:
        completed = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, errors="replace")
        if completed.returncode:
            note("COMMAND_ERROR=" + str(completed.stderr)[-650:])
    if completed.returncode and check:
        raise RuntimeError(f"COMMAND_FAILED_STATUS={completed.returncode} CMD={cmd[:7]}")
    return completed

def git(repo, *args):
    return run(["git", "-C", repo, *args]).stdout.strip()

def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(2 * 1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()

def expected_gitlink(parent, rel, wanted):
    raw = git(parent, "ls-tree", "HEAD", rel)
    # Format: 160000 commit <sha>\t<path>
    expected = f"160000 commit {wanted}\t{rel}"
    if raw != expected:
        raise RuntimeError(f"PINNED_SUBMODULE_GITLINK_MISMATCH={rel} actual={raw}")
    note(f"PINNED_GITLINK=PASS {rel} {wanted}")

def preflight():
    for name in ("git", "cmake", "ninja"):
        if not shutil.which(name):
            raise RuntimeError("MISSING_REQUIRED_TOOL=" + name)
    if not BASE.is_dir() or git(BASE, "rev-parse", "HEAD") != BASE_PIN:
        raise RuntimeError("PROTECTED_SOURCE_REVISION_CHANGED")
    if not PROTECTED.is_file() or PROTECTED.is_symlink() or sha256(PROTECTED) != PROTECTED_SHA:
        raise RuntimeError("PROTECTED_GAME_EXECUTABLE_SHA_MISMATCH")
    if git(STAGE, "rev-parse", "HEAD") != CANDIDATE_PIN:
        raise RuntimeError("ISOLATED_FOUR_FILE_CANDIDATE_HEAD_CHANGED")
    if git(SIRIT, "rev-parse", "HEAD") != SIRIT_PIN:
        raise RuntimeError("PINNED_SIRIT_COMMIT_CHANGED")
    if Path(git(SIRIT, "rev-parse", "--show-toplevel")).resolve() != SIRIT.resolve():
        raise RuntimeError("STAGED_SIRIT_GIT_BOUNDARY_INVALID")
    expected_gitlink(SIRIT, "externals/SPIRV-Headers", HEADERS_PIN)
    cache = BUILD / "CMakeCache.txt"
    if not cache.is_file():
        raise RuntimeError("EXISTING_CMAKE_CACHE_NOT_FOUND")
    if f"CMAKE_HOME_DIRECTORY:INTERNAL={STAGE}" not in cache.read_text(errors="replace"):
        raise RuntimeError("CMAKE_CACHE_POINTS_TO_DIFFERENT_SOURCE")
    if shutil.disk_usage(PROJECT).free < 3 * 2**30:
        raise RuntimeError("INSUFFICIENT_FREE_DISK_UNDER_3_GIB")
    file = STAGE / "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp"
    data = file.read_text()
    if "SHADPS4_PERMUTATION_TRACE" not in data or "saved.GetInfo(program->info)" not in data:
        raise RuntimeError("STAGED_CODE_MISSING_REQUIRED_CORRECTION_OR_TRACE")
    note("PROTECTED_SOURCE_AND_BINARY_CHECKSUM=PASS")
    note("STAGED_FOUR_FILE_CANDIDATE_AND_CMAKE_CACHE=PASS")

def source_object_pool():
    # Reuse the original nested Git object store ONLY if its HEAD matches the
    # exact gitlink. Otherwise use a pinned SHA fetch into the isolated copy.
    if not ORIGINAL_NESTED.is_dir() or not (ORIGINAL_NESTED / ".git").exists():
        return None
    try:
        toplevel = Path(git(ORIGINAL_NESTED, "rev-parse", "--show-toplevel"))
        if toplevel.resolve() != ORIGINAL_NESTED.resolve():
            return None
        if git(ORIGINAL_NESTED, "rev-parse", "HEAD") != HEADERS_PIN:
            return None
        pool = Path(git(ORIGINAL_NESTED, "rev-parse", "--absolute-git-dir")) / "objects"
        if pool.is_dir() and run(["git", "-C", ORIGINAL_NESTED, "cat-file", "-e",
                                  HEADERS_PIN + "^{commit}"], check=False).returncode == 0:
            return pool.resolve()
    except (OSError, RuntimeError):
        pass
    return None

def restore_nested():
    if NESTED.is_symlink():
        raise RuntimeError("NESTED_DEPENDENCY_SYMLINK_UNSAFE")
    if (NESTED / ".git").is_dir():
        if git(NESTED, "rev-parse", "HEAD") != HEADERS_PIN:
            raise RuntimeError("PREEXISTING_NESTED_REPOSITORY_HAS_DIFFERENT_REVISION")
        if Path(git(NESTED, "rev-parse", "--show-toplevel")).resolve() != NESTED.resolve():
            raise RuntimeError("PREEXISTING_NESTED_REPOSITORY_ESCAPES_WORKTREE")
        if not (NESTED / "CMakeLists.txt").is_file():
            run(["git", "-C", NESTED, "restore", "--source=HEAD", "--worktree", "--", "."])
        note("PREVIOUSLY_PINNED_NESTED_DEPENDENCY_REUSED=YES")
        return "existing_exact_pin"
    if NESTED.exists():
        if not NESTED.is_dir() or any(NESTED.iterdir()):
            raise RuntimeError("UNEXPECTED_NESTED_DEPENDENCY_CONTENT_REFUSING_OVERWRITE")
    else:
        NESTED.mkdir(parents=True)
    run(["git", "init", "-q", "-b", "pinned", NESTED])
    pool = source_object_pool()
    mode = None
    if pool:
        alternates = NESTED / ".git/objects/info/alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_text(str(pool) + "\n")
        mode = "pinned_original_objects_read_only"
    else:
        note("NESTED_SOURCE_OBJECTS_NOT_LOCALLY_PINNED; FETCHING_ONLY_EXACT_COMMIT")
        result = run(["git", "-C", NESTED, "-c", "http.version=HTTP/1.1",
                      "fetch", "--no-tags", "--depth=1", UPSTREAM, HEADERS_PIN],
                     check=False)
        if result.returncode:
            raise RuntimeError("EXACT_NESTED_COMMIT_FETCH_FAILED_NO_UNPINNED_FALLBACK")
        mode = "downloaded_exact_pinned_commit"
    if git(NESTED, "cat-file", "-t", HEADERS_PIN) != "commit":
        raise RuntimeError("NESTED_SUBMODULE_PINNED_COMMIT_OBJECT_NOT_AVAILABLE")
    run(["git", "-C", NESTED, "update-ref", "refs/heads/pinned", HEADERS_PIN])
    run(["git", "-C", NESTED, "read-tree", HEADERS_PIN])
    run(["git", "-C", NESTED, "restore", "--source=HEAD", "--worktree", "--", "."])
    note("SPIRV_HEADERS_COPIED_FROM=" + mode)
    return mode

def validate_nested():
    if git(NESTED, "rev-parse", "HEAD") != HEADERS_PIN:
        raise RuntimeError("NESTED_COMMIT_NOT_EXACT_PIN")
    if Path(git(NESTED, "rev-parse", "--show-toplevel")).resolve() != NESTED.resolve():
        raise RuntimeError("NESTED_REPO_WORKTREE_BOUNDARY_MISMATCH")
    if git(NESTED, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("NESTED_DEPENDENCY_TRACKED_FILES_DIRTY")
    tracked = git(NESTED, "ls-tree", "HEAD", "CMakeLists.txt")
    if "CMakeLists.txt" not in tracked or not (NESTED / "CMakeLists.txt").is_file():
        raise RuntimeError("SPIRV_HEADERS_CMAKE_FILE_NOT_FROM_PINNED_COMMIT")
    if git(NESTED, "ls-tree", "-r", "HEAD").count("160000 commit "):
        raise RuntimeError("UNEXPECTED_THIRD_LEVEL_NESTED_DEPENDENCIES")
    note("EXACT_SIRIT_SPIRV_HEADERS_PIN=PASS")
    note("SPIRV_HEADERS_CMAKE_TRACKED_CONTENT=PASS")

def compile_link():
    note("RESUME_EXISTING_ISOLATED_CMAKE_CONFIGURATION")
    run(["cmake", "-S", STAGE, "-B", BUILD, "-G", "Ninja",
         "-DENABLE_TESTS=OFF"], log=CMAKE_LOG)
    note("ISOLATED_CMAKE_CONFIGURATION=PASS")
    note("START_ISOLATED_FULL_LINK")
    run(["cmake", "--build", BUILD, "--target", "shadps4", "--parallel", "4"],
        log=BUILD_LOG)
    candidate = BUILD / "shadps4"
    if not candidate.is_file() or candidate.is_symlink() or candidate.stat().st_size < 1024:
        raise RuntimeError("LINKED_ISOLATED_BINARY_MISSING")
    note("ISOLATED_FULL_LINK_BUILD=PASS")
    note("ISOLATED_GAME_BINARY=" + str(candidate))
    note("ISOLATED_GAME_BINARY_SHA256=" + sha256(candidate))
    return dict(candidate=str(candidate), candidate_sha256=sha256(candidate))

def main():
    LOGDIR.mkdir(parents=True, exist_ok=True)
    status = {"run_at": STAMP, "mode": "isolated CMake/build only, no gameplay",
              "protected_base": BASE_PIN, "candidate_source": CANDIDATE_PIN,
              "nested_dependency_sha": HEADERS_PIN, "outcome": "BLOCKED"}
    exitcode = 2
    try:
        note("=== GOW EXACT NESTED DEPENDENCY STAGING REPAIR ===")
        preflight()
        status["staging_source"] = restore_nested()
        validate_nested()
        status.update(compile_link())
        status["outcome"] = "PASS"
        exitcode = 0
    except Exception as exc:
        note("ISOLATED_RESUME_BLOCKER=" + str(exc))
        status["failure"] = str(exc)
        status["exception"] = type(exc).__name__
    finally:
        try:
            a = PROTECTED.is_file() and sha256(PROTECTED) == PROTECTED_SHA
            b = BASE.is_dir() and git(BASE, "rev-parse", "HEAD") == BASE_PIN
            status.update(protected_binary_unchanged=a, protected_source_head_unchanged=b)
            note("PROTECTED_BINARY_SHA_UNCHANGED=" + str(a).upper())
            note("ORIGINAL_CHECKOUT_HEAD_UNCHANGED=" + str(b).upper())
            if not a or not b:
                exitcode = 3
        except Exception as exc:
            exitcode = 3
            status["final_integrity_error"] = str(exc)
        REPORT.write_text(json.dumps(status, indent=2)+"\n")
        with tarfile.open(ARCHIVE, "w:gz") as tar:
            for p in (MAIN, CMAKE_LOG, BUILD_LOG, REPORT):
                if p.is_file():
                    tar.add(p, arcname="logs/" + p.name)
        note("UPLOAD_ARCHIVE=" + str(ARCHIVE))
        note("GAME_RUNS=ZERO")
        note("OLLAMA_SUNSHINE_SAVES_UNTOUCHED=YES")
        note("SSH_SESSION=REMAINS_OPEN")
        note("RESUME_EXIT_CODE=" + str(exitcode))
    return exitcode

if __name__ == "__main__":
    sys.exit(main())
