#!/usr/bin/env python3
"""Resume pinned GoW permutation-scene full build by restoring missing tracked
files INSIDE the 45 isolated, already-copied dependency worktrees only.
No original source/checkouts, protected executable, game files, saves or services
are changed. Dependency files come from exact Git HEAD blobs, never stand-ins.
"""
from __future__ import annotations
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import traceback

ROOT = Path.home() / "projects/shadps4"
GOW = ROOT / "GodOfWarRagnarok"
ORIGINAL = GOW / "gow-stage25-restored-20261011-7bpufvsl/source"
if not (ORIGINAL / ".git").exists():
    ORIGINAL = GOW / "source"
PROJECT = GOW / "tests/permutation-scene-20261019-201332"
STAGE = PROJECT / "source"
BUILD = PROJECT / "build"
PROTECTED = GOW / "build/shadps4"
LOGDIR = ROOT / "logs"
STAMP = datetime.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
RUNLOG = LOGDIR / ("gow-permutation-scene-repair-" + STAMP + ".log")
CMAKELOG = LOGDIR / ("gow-permutation-scene-repair-cmake-" + STAMP + ".log")
BUILDLOG = LOGDIR / ("gow-permutation-scene-repair-compile-" + STAMP + ".log")
REPORT = LOGDIR / ("gow-permutation-scene-repair-" + STAMP + ".json")
ARCHIVE = LOGDIR / ("gow-permutation-scene-repair-" + STAMP + ".tar.gz")
STAGE_PIN = "81b335fa08c8bb2d651bdc988ff113d7bfbb996c"
BASE_PIN = "3965a497532edb7b18027692ab04eb97fa4c145b"
BIN_PIN = "8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
CRITICAL = {
    "externals/sdl3": [
        "include/build_config/SDL_build_config.h.cmake",
        "include/build_config/SDL_revision.h.cmake",
    ],
    "externals/zstd": ["build/cmake/CMakeLists.txt"],
}

def say(message: str):
    print(message, flush=True)
    with RUNLOG.open("a", encoding="utf-8") as log:
        log.write(message + "\n")

def run(args, cwd=None, log=None):
    say("RUN=" + " ".join(map(str, args[:6])) + (" ..." if len(args) > 6 else ""))
    if log is None:
        proc = subprocess.run(args, cwd=cwd, check=False, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors="replace")
        if proc.returncode:
            say("COMMAND_ERROR=" + "\n".join(proc.stdout.splitlines()[-50:]))
        return proc
    with log.open("w") as fp:
        proc = subprocess.run(args, cwd=cwd, check=False,
                              stdout=fp, stderr=subprocess.STDOUT)
    if proc.returncode:
        say("COMMAND_ERROR_TAIL=" + "\n".join(
            log.read_text(errors="replace").splitlines()[-70:]))
    return proc

def git(repo, *arguments):
    result = subprocess.run(["git", "-C", str(repo), *arguments],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"git {arguments[0]} failed in {repo}: {result.stderr[:400]}")
    return result.stdout.strip()

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for piece in iter(lambda: src.read(4 * 1024 * 1024), b""):
            digest.update(piece)
    return digest.hexdigest()

def preflight():
    for exe in ("git", "cmake", "ninja", "tar"):
        if shutil.which(exe) is None:
            raise RuntimeError(f"REQUIRED_TOOL_MISSING={exe}")
    for path in (ORIGINAL, STAGE, BUILD):
        if not path.is_dir():
            raise RuntimeError(f"EXPECTED_ISOLATED_DIRECTORY_MISSING={path}")
    if not PROTECTED.is_file():
        raise RuntimeError("PROTECTED_GAME_EXECUTABLE_NOT_PRESENT")
    old_head, scene_head = git(ORIGINAL, "rev-parse", "HEAD"), git(STAGE, "rev-parse", "HEAD")
    if old_head != BASE_PIN:
        raise RuntimeError(f"ORIGINAL_HEAD_CHANGED={old_head} expected={BASE_PIN}")
    if scene_head != STAGE_PIN:
        raise RuntimeError(f"STAGED_HEAD_CHANGED={scene_head} expected={STAGE_PIN}")
    measured = sha256(PROTECTED)
    if measured != BIN_PIN:
        raise RuntimeError(f"PROTECTED_BINARY_HASH_CHANGED={measured} expected={BIN_PIN}")
    cmake_cache = BUILD / "CMakeCache.txt"
    if not cmake_cache.is_file() or \
       f"CMAKE_HOME_DIRECTORY:INTERNAL={STAGE}" not in cmake_cache.read_text(errors="replace"):
        raise RuntimeError("EXISTING_CMAKE_CACHE_IS_NOT_FOR_EXACT_SCENE_STAGE")
    cpp = (STAGE / "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp").read_text()
    if not all(k in cpp for k in ("SHADPS4_PERMUTATION_TRACE",
                                   "PERM_TRACE_CREATED", "PERM_TRACE_REUSED")):
        raise RuntimeError("CANDIDATE_IS_NOT_THE_GOW_PERMUTATION_TRACE")
    say("PINNED_WORKTREE_PROTECTED_BINARY_AND_CMAKE_CONTEXT=PASS")

def get_submodules():
    output = subprocess.check_output(["git", "-C", str(STAGE), "ls-tree", "-rz", "HEAD"])
    entries = []
    for blob in output.split(b"\0"):
        if not blob.startswith(b"160000 "):
            continue
        desc, path = blob.split(b"\t", 1)
        expected = desc.split()[-1].decode()
        relative = os.fsdecode(path)
        subdir = STAGE / relative
        if not (subdir / ".git").is_dir() or subdir.is_symlink():
            raise RuntimeError(f"NOT_AN_INDEPENDENT_STAGED_GIT_REPOSITORY={relative}")
        if git(subdir, "rev-parse", "HEAD") != expected:
            raise RuntimeError(f"STAGED_DEPENDENCY_PIN_MISMATCH={relative}")
        if Path(git(subdir, "rev-parse", "--show-toplevel")).resolve() != subdir.resolve():
            raise RuntimeError(f"STAGED_DEPENDENCY_REPOSITORY_BOUNDARY_ERROR={relative}")
        entries.append((relative, subdir))
    if len(entries) != 45:
        raise RuntimeError(f"EXPECTED_45_PINNED_DEPENDENCIES_GOT={len(entries)}")
    return entries

def missing_tracked(subdir):
    entries = subprocess.check_output(
        ["git", "-C", str(subdir), "ls-files", "-s", "-z"]).split(b"\0")
    missing = []
    for entry in entries:
        if not entry:
            continue
        meta, rel = entry.split(b"\t", 1)
        mode, oid, stage = meta.decode("ascii").split(" ")
        if mode == "160000":
            continue
        if stage != "0":
            raise RuntimeError("UNMERGED_STAGED_DEPENDENCY_INDEX=" + str(subdir))
        name = os.fsdecode(rel)
        if not os.path.lexists(subdir / name):
            missing.append((name, oid))
    return missing

def blob_sizes(subdir, missing):
    if not missing:
        return 0
    unique = list(dict.fromkeys(oid for _, oid in missing))
    check = subprocess.run(["git", "-C", str(subdir),
                            "cat-file", "--batch-check=%(objectsize)"],
                           input="\n".join(unique) + "\n",
                           capture_output=True, text=True)
    if check.returncode:
        raise RuntimeError("GIT_OBJECT_INSPECTION_FAILED=" + str(subdir))
    sizes = check.stdout.splitlines()
    if len(sizes) != len(unique) or any(not line.isdecimal() for line in sizes):
        raise RuntimeError("REQUIRED_PINNED_GIT_OBJECT_MISSING=" + str(subdir))
    by_sha = {sha: int(size) for sha, size in zip(unique, sizes)}
    return sum(by_sha[oid] for _, oid in missing)

def audit(entries):
    report = []
    total_bytes = total_files = 0
    all_missing = {}
    for relative, subdir in entries:
        missing = missing_tracked(subdir)
        count = len(missing)
        estimate = blob_sizes(subdir, missing)
        all_missing[relative] = missing
        total_files += count
        total_bytes += estimate
        report.append({"submodule": relative, "tracked_files_missing": count,
                       "source_blob_bytes": estimate, "sample": [x for x, _ in missing[:8]]})
        if count:
            say(f"STAGED_MISSING_TRACKED_FILES={relative} count={count} bytes={estimate}")
    for relative, names in CRITICAL.items():
        subdir = STAGE / relative
        tracked = {name for name, _ in missing_tracked(subdir)}
        for name in names:
            check = subprocess.run(["git", "-C", str(subdir), "cat-file", "-e",
                                    "HEAD:" + name], capture_output=True)
            if check.returncode:
                raise RuntimeError(f"REQUIRED_UPSTREAM_FILE_NOT_IN_PINNED_REVISION={relative}/{name}")
            say(f"PINNED_UPSTREAM_FILE_EXISTS=YES {relative}/{name} missing={name in tracked}")
    say(f"RESTORE_PLAN=files:{total_files} total_bytes:{total_bytes} submodules:{len(entries)}")
    return report, all_missing, total_files, total_bytes

def restore(entries, all_missing):
    for relative, subdir in entries:
        missing = all_missing[relative]
        if not missing:
            continue
        for i in range(0, len(missing), 125):
            batch = [name for name, _ in missing[i:i+125]]
            proc = run(["git", "-C", str(subdir), "restore", "--source=HEAD",
                        "--worktree", "--", *batch])
            if proc.returncode:
                raise RuntimeError(f"PINNED_DEPENDENCY_RESTORE_FAILED={relative} batch={i//125}")
        remaining = missing_tracked(subdir)
        if remaining:
            raise RuntimeError(f"STAGED_TRACKED_FILES_STILL_MISSING={relative} count={len(remaining)}")
        say(f"PINNED_DEPENDENCY_COMPLETED={relative} restored={len(missing)}")
    say("ALL_STAGED_PINNED_TRACKED_FILES=AVAILABLE")

def compile_and_link():
    configured = run(["cmake", "-S", str(STAGE), "-B", str(BUILD),
                      "-G", "Ninja", "-DENABLE_TESTS=OFF"], log=CMAKELOG)
    if configured.returncode:
        raise RuntimeError(f"CMAKE_CONFIGURE_FAILED={configured.returncode}")
    say("ISOLATED_CMAKE_CONFIGURATION=PASS")
    built = run(["cmake", "--build", str(BUILD), "--target", "shadps4",
                 "--parallel", "4"], log=BUILDLOG)
    if built.returncode:
        raise RuntimeError(f"ISOLATED_BUILD_OR_LINK_FAILED={built.returncode}")
    candidate = BUILD / "shadps4"
    if not candidate.is_file() or candidate.stat().st_size == 0:
        raise RuntimeError("BUILT_CANDIDATE_BINARY_NOT_FOUND")
    say("ISOLATED_FULL_LINK_BUILD=PASS")
    say(f"ISOLATED_CANDIDATE_BINARY={candidate}")
    say(f"ISOLATED_CANDIDATE_SHA256={sha256(candidate)}")
    say("TRACE_MARKERS=PERM_TRACE_ACTIVE,PERM_TRACE_CREATED,PERM_TRACE_REUSED")

def final_integrity_check():
    say("PROTECTED_EXECUTABLE_SHA_UNCHANGED=" +
        ("YES" if PROTECTED.is_file() and sha256(PROTECTED) == BIN_PIN else "NO"))
    say("ORIGINAL_SOURCE_HEAD_UNCHANGED=" +
        ("YES" if ORIGINAL.is_dir() and git(ORIGINAL, "rev-parse", "HEAD") == BASE_PIN else "NO"))

def make_archive():
    existing = [file for file in (RUNLOG, CMAKELOG, BUILDLOG, REPORT) if file.is_file()]
    with tarfile.open(ARCHIVE, "w:gz") as bundle:
        for file in existing:
            bundle.add(file, arcname="logs/" + file.name)
    say(f"UPLOAD_EVIDENCE={ARCHIVE}")
    say("GAMEPLAY_RUNS=ZERO")
    say("SSH_SESSION=REMAINS_OPEN")

def main():
    LOGDIR.mkdir(parents=True, exist_ok=True)
    say("=== GOW PINNED DEPENDENCY REPAIR AND ISOLATED FULL BUILD ===")
    say("DATE=" + datetime.datetime.now().astimezone().isoformat())
    status = {"timestamp": STAMP, "stage": str(STAGE),
              "protected_binary": str(PROTECTED), "result": "UNKNOWN"}
    rc = 0
    try:
        preflight()
        entries = get_submodules()
        report, missing, total_files, total_bytes = audit(entries)
        status["submodules"] = report
        status["total_files_restored_requested"] = total_files
        status["estimated_restoration_bytes"] = total_bytes
        free = shutil.disk_usage(PROJECT).free
        if total_files > 250000 or total_bytes > max(0, free - 3 * 1024**3):
            raise RuntimeError("RESTORE_PLAN_EXCEEDS_DISK_SAFETY_BUDGET")
        restore(entries, missing)
        compile_and_link()
        status["result"] = "PASS"
    except Exception as exc:
        rc = 2
        status["result"] = "FAIL"
        status["failure"] = str(exc)
        say("REPAIR_OR_BUILD_FAILED=" + str(exc))
        say("FAILURE_TYPE=" + type(exc).__name__)
    finally:
        try:
            final_integrity_check()
        except Exception as exc:
            say("FINAL_INTEGRITY_CHECK_FAILED=" + str(exc))
            status["integrity_check_error"] = str(exc)
            rc = 3
        REPORT.write_text(json.dumps(status, indent=2)+"\n")
        try:
            make_archive()
        except Exception as exc:
            say("ARCHIVE_FAILED=" + str(exc))
            rc = max(rc, 3)
        say("NEXT_GATE=" + ("SCENE_MATCHED_INSTRUMENTED_RUN" if rc == 0 else
                            "REVIEW_EXACT_FAILURE_BEFORE_GAME_RUN"))
    return rc

if __name__ == "__main__":
    sys.exit(main())
