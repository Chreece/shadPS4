#!/usr/bin/env python3
"""Resume protected-base GoW shader-permutation build after missing Discord RPC RapidJSON.

Restores the EXACT nested gitlink into the existing isolated source worktree,
verifies pinned bytes, and resumes Ninja without touching the original source,
protected executable, original saves, Ollama, Sunshine, or SSH.
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
ORIGINAL = GOW / "gow-stage25-restored-20261011-7bpufvsl/source"
PROJECT = GOW / "tests/permutation-protected-20261019-220440"
SOURCE = PROJECT / "source"
BUILD = PROJECT / "build"
DISCORD = SOURCE / "externals/discord-rpc"
RAPIDJSON = DISCORD / "thirdparty/rapidjson"
PROTECTED = GOW / "build/shadps4"
BASE_SHA = "3965a497532edb7b18027692ab04eb97fa4c145b"
FIX_SHA = "e6ca28e7386faa0624c1421a5dc0585eeb028532"
DISCORD_SHA = "19f66e6dcabb2268965f453db9e5774ede43238f"
RAPIDJSON_SHA = "d621dc9e9c77f81e5c8a35b8dcc16dcd63351321"
RAPIDJSON_DOCUMENT_BLOB = "4b2d723224833d70208512881091a068f2d075c6"
PROTECTED_SHA = "8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
UPSTREAM = "https://github.com/Tencent/rapidjson.git"
EXPECTED_CHANGED = {
    "src/shader_recompiler/specialization.h",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.h",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp",
}

LOGS = ROOT / "logs"
STAMP = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
CONSOLE_LOG = LOGS / f"gow-permutation-protected-rapidjson-{STAMP}.log"
NINJA_LOG = LOGS / f"gow-permutation-protected-rapidjson-compile-{STAMP}.log"
REPORT = LOGS / f"gow-permutation-protected-rapidjson-{STAMP}.json"
ARCHIVE = LOGS / f"gow-permutation-protected-rapidjson-{STAMP}.tar.gz"

def emit(message):
    print(message, flush=True)
    with CONSOLE_LOG.open("a", encoding="utf-8") as f:
        f.write(message + "\n")

def run(args, *, log=None, check=True):
    cmd = [str(x) for x in args]
    if log:
        with log.open("w", encoding="utf-8") as f:
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
        if result.returncode:
            emit("NINJA_FAILURE_LOG_TAIL_BEGIN")
            for line in log.read_text(errors="replace").splitlines()[-90:]:
                emit(line)
            emit("NINJA_FAILURE_LOG_TAIL_END")
    else:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, errors="replace")
        if result.returncode:
            emit("COMMAND_ERROR_TAIL=" + result.stderr[-800:])
    if check and result.returncode:
        raise RuntimeError(f"COMMAND_FAILED status={result.returncode} command={cmd[:7]}")
    return result

def git(repo, *args):
    return run(["git", "-C", repo, *args]).stdout.strip()

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(2 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def protected_integrity():
    ok = PROTECTED.is_file() and not PROTECTED.is_symlink() and digest(PROTECTED) == PROTECTED_SHA
    baseline = ORIGINAL.is_dir() and git(ORIGINAL, "rev-parse", "HEAD") == BASE_SHA
    return ok, baseline

def preflight():
    for executable in ("git", "ninja", "cmake", "tar"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"REQUIRED_EXECUTABLE_MISSING={executable}")
    safe_binary, safe_head = protected_integrity()
    if not (safe_binary and safe_head):
        raise RuntimeError("PROTECTED_BINARY_OR_ORIGINAL_SOURCE_CHANGED")
    if git(SOURCE, "rev-parse", "HEAD") != FIX_SHA:
        raise RuntimeError("EXACT_FOUR_FILE_STAGE_COMMIT_MISMATCH")
    changes = set(git(SOURCE, "diff", "--name-only", BASE_SHA, FIX_SHA).splitlines())
    if changes != EXPECTED_CHANGED:
        raise RuntimeError("CANDIDATE_DIFF_NOT_EXACT_FOUR_FILES=" + repr(sorted(changes)))
    if git(DISCORD, "rev-parse", "HEAD") != DISCORD_SHA:
        raise RuntimeError("STAGED_DISCORD_RPC_COMMIT_MISMATCH")
    if Path(git(DISCORD, "rev-parse", "--show-toplevel")).resolve() != DISCORD.resolve():
        raise RuntimeError("STAGED_DISCORD_RPC_GIT_BOUNDARY_MISMATCH")
    expected_link = f"160000 commit {RAPIDJSON_SHA}\tthirdparty/rapidjson"
    if git(DISCORD, "ls-tree", "HEAD", "thirdparty/rapidjson") != expected_link:
        raise RuntimeError("DISCORD_RPC_RAPIDJSON_GITLINK_PIN_MISMATCH")
    cmake_cache = BUILD / "CMakeCache.txt"
    if not cmake_cache.is_file() or \
       f"CMAKE_HOME_DIRECTORY:INTERNAL={SOURCE}" not in cmake_cache.read_text(errors="replace"):
        raise RuntimeError("CMAKE_CACHE_IS_NOT_ISOLATED_FOUR_FILE_BUILD")
    if shutil.disk_usage(PROJECT).free < 2 * 1024**3:
        raise RuntimeError("FREE_DISK_BELOW_2_GIB")
    emit("PROTECTED_BINARY_ORIGINAL_HEAD_AND_FOUR_FILE_PATCH=PASS")
    emit("DISCORD_RPC_TO_RAPIDJSON_EXACT_GITLINK=PASS")

def verified_source_objects():
    originals = (
        ORIGINAL / "externals/discord-rpc/thirdparty/rapidjson",
        GOW / "tests/permutation-scene-20261019-201332/source/externals/discord-rpc/thirdparty/rapidjson",
    )
    for path in originals:
        if not path.is_dir() or not (path / ".git").exists():
            continue
        try:
            objects = Path(git(path, "rev-parse", "--absolute-git-dir")) / "objects"
            exists = run(["git", "-C", path, "cat-file", "-e",
                          RAPIDJSON_SHA + "^{commit}"], check=False)
            if objects.is_dir() and exists.returncode == 0:
                emit(f"REUSING_VERIFIED_GIT_OBJECT_DATABASE={path}")
                return objects.resolve()
        except (OSError, RuntimeError):
            continue
    return None

def restore_rapidjson():
    if RAPIDJSON.is_symlink():
        raise RuntimeError("RAPIDJSON_IS_SYMLINK_REFUSE")
    git_marker = RAPIDJSON / ".git"
    if git_marker.exists():
        if not git_marker.is_dir():
            raise RuntimeError("RAPIDJSON_GIT_METADATA_IS_NOT_LOCAL_DIRECTORY")
        if Path(git(RAPIDJSON, "rev-parse", "--show-toplevel")).resolve() != RAPIDJSON.resolve():
            raise RuntimeError("RAPIDJSON_GIT_REPO_ESCAPES_ISOLATED_WORKTREE")
        if git(RAPIDJSON, "rev-parse", "HEAD") != RAPIDJSON_SHA:
            raise RuntimeError("EXISTING_RAPIDJSON_GIT_PIN_MISMATCH")
        emit("RAPIDJSON_REUSED_FROM_PREVIOUS_EXACT_STAGED_PIN=YES")
    else:
        if RAPIDJSON.exists():
            if not RAPIDJSON.is_dir() or any(RAPIDJSON.iterdir()):
                raise RuntimeError("UNEXPECTED_NESTED_DIRECTORY_CONTENT_REFUSE_OVERWRITE")
        else:
            RAPIDJSON.mkdir(parents=True)
        run(["git", "init", "-q", "-b", "pinned", RAPIDJSON])
        obj_pool = verified_source_objects()
        if obj_pool:
            alternate = RAPIDJSON / ".git/objects/info/alternates"
            alternate.parent.mkdir(parents=True, exist_ok=True)
            alternate.write_text(str(obj_pool) + "\n", encoding="utf-8")
            emit("RAPIDJSON_SOURCE=pinned_read_only_object_alternate")
        else:
            emit("RAPIDJSON_SOURCE=fetch_exact_SHA_only")
            result = run(["git", "-C", RAPIDJSON, "-c", "http.version=HTTP/1.1",
                          "fetch", "--no-tags", "--depth=1", UPSTREAM, RAPIDJSON_SHA],
                         check=False)
            if result.returncode:
                raise RuntimeError("RAPIDJSON_EXACT_COMMIT_FETCH_FAILED_NO_FALLBACK_TO_HEAD")
        if git(RAPIDJSON, "cat-file", "-t", RAPIDJSON_SHA) != "commit":
            raise RuntimeError("RAPIDJSON_PINNED_COMMIT_NOT_IN_OBJECT_DATABASE")
        run(["git", "-C", RAPIDJSON, "update-ref", "refs/heads/pinned", RAPIDJSON_SHA])
        run(["git", "-C", RAPIDJSON, "read-tree", RAPIDJSON_SHA])
    run(["git", "-C", RAPIDJSON, "restore", "--source=HEAD", "--worktree",
         "--", "include"])
    return verify_rapidjson()

def verify_rapidjson():
    if git(RAPIDJSON, "rev-parse", "HEAD") != RAPIDJSON_SHA:
        raise RuntimeError("RAPIDJSON_CHECKOUT_NOT_EXACT_PIN")
    if Path(git(RAPIDJSON, "rev-parse", "--show-toplevel")).resolve() != RAPIDJSON.resolve():
        raise RuntimeError("RAPIDJSON_WORKTREE_ESCAPES_ISOLATION")
    header = RAPIDJSON / "include/rapidjson/document.h"
    if not header.is_file() or header.is_symlink():
        raise RuntimeError("RAPIDJSON_DOCUMENT_HEADER_ABSENT_OR_SYMLINK")
    if git(RAPIDJSON, "hash-object", header) != RAPIDJSON_DOCUMENT_BLOB:
        raise RuntimeError("RAPIDJSON_DOCUMENT_HEADER_BLOB_MISMATCH")
    if git(RAPIDJSON, "ls-tree", "-r", "HEAD", "include/rapidjson/document.h").split()[-2] != RAPIDJSON_DOCUMENT_BLOB:
        raise RuntimeError("RAPIDJSON_DOCUMENT_HEADER_NOT_FROM_HEAD")
    emit("EXACT_RAPIDJSON_COMMIT_AND_DOCUMENT_HEADER_BLOB=PASS")
    return {"revision": RAPIDJSON_SHA, "document_blob": RAPIDJSON_DOCUMENT_BLOB,
            "header": str(header)}

def unresolved_nested_audit():
    """Report remaining exact-gitlink paths, without downloading optional tests."""
    entries = subprocess.check_output(["git", "-C", str(SOURCE), "ls-tree", "-rz", "HEAD"])
    children = []
    for row in entries.split(b"\0"):
        if not row.startswith(b"160000 "):
            continue
        descriptor, rawpath = row.split(b"\t", 1)
        sub = SOURCE / os.fsdecode(rawpath)
        if not sub.is_dir() or not (sub / ".git").is_dir():
            continue
        nested = subprocess.check_output(["git", "-C", str(sub),
                                          "ls-tree", "-rz", "HEAD"])
        for entry in nested.split(b"\0"):
            if entry.startswith(b"160000 "):
                descriptor2, relative2 = entry.split(b"\t", 1)
                if not (sub / os.fsdecode(relative2)).is_dir() or \
                   not (sub / os.fsdecode(relative2) / ".git").exists():
                    children.append(str(sub.relative_to(SOURCE) / os.fsdecode(relative2)))
    emit(f"OTHER_UNPOPULATED_NESTED_GITLINKS={len(children)}")
    for path in children[:30]:
        emit("UNPOPULATED_NESTED_GITLINK=" + path)
    emit("NOTE=UNPOPULATED_NESTED_GITLINKS_MAY_BE_OPTIONAL_BUILD_DEPENDENCIES")
    return children

def resume_ninja():
    emit("RESUMING_EXISTING_NINJA_BUILD_WITHOUT_RECONFIGURE")
    env = dict(os.environ)
    env["CMAKE_BUILD_PARALLEL_LEVEL"] = "4"
    command = ["cmake", "--build", BUILD, "--target", "shadps4", "--parallel", "4"]
    output = [str(s) for s in command]
    with NINJA_LOG.open("w", encoding="utf-8") as f:
        result = subprocess.run(output, env=env, stdout=f, stderr=subprocess.STDOUT)
    if result.returncode:
        emit("NINJA_BUILD_FAILURE_LOG_TAIL_START")
        for line in NINJA_LOG.read_text(errors="replace").splitlines()[-95:]:
            emit(line)
        emit("NINJA_BUILD_FAILURE_LOG_TAIL_END")
        raise RuntimeError("ISOLATED_NINJA_BUILD_FAILED_RC=" + str(result.returncode))
    binary = BUILD / "shadps4"
    if not binary.is_file() or binary.is_symlink() or binary.stat().st_size < 1024:
        raise RuntimeError("NINJA_SUCCESS_BUT_NO_CANDIDATE_BINARY")
    emit("ISOLATED_FULL_LINK_BUILD=PASS")
    emit("ISOLATED_CANDIDATE_BINARY=" + str(binary))
    emit("ISOLATED_CANDIDATE_SHA256=" + digest(binary))
    return {"path": str(binary), "sha256": digest(binary)}

def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    report = {"when": STAMP, "protected_base": BASE_SHA, "candidate": FIX_SHA,
              "mode": "restore nested rapidjson and compile only", "result": "BLOCKED"}
    code = 2
    emit("=== GOW CLEAN FOUR-FILE BUILD: PINNED RAPIDJSON RESUME ===")
    try:
        preflight()
        report["restored_dependency"] = restore_rapidjson()
        report["other_missing_nested_gitlinks"] = unresolved_nested_audit()
        report["executable"] = resume_ninja()
        report["result"] = "PASS"
        code = 0
    except Exception as exc:
        report["failure"] = str(exc)
        emit("BUILD_BLOCKER=" + str(exc))
    finally:
        try:
            good, source = protected_integrity()
            report.update(protected_binary_unchanged=good,
                          protected_source_head_unchanged=source)
            emit("PROTECTED_BINARY_SHA_UNCHANGED=" + str(good).upper())
            emit("ORIGINAL_CHECKOUT_HEAD_UNCHANGED=" + str(source).upper())
            if not (good and source):
                code = 3
        except Exception as exc:
            report["integrity_check_error"] = str(exc)
            code = 3
        REPORT.write_text(json.dumps(report, indent=2) + "\n")
        with tarfile.open(ARCHIVE, "w:gz") as archive:
            for path in (CONSOLE_LOG, NINJA_LOG, REPORT):
                if path.is_file():
                    archive.add(path, arcname="logs/" + path.name)
        emit("UPLOAD_ARCHIVE=" + str(ARCHIVE))
        emit("GAME_LAUNCHED=NO")
        emit("OLLAMA_SUNSHINE_SAVES_UNTOUCHED=YES")
        emit("SSH_SESSION=REMAINS_OPEN")
        emit("BUILD_EXIT_CODE=" + str(code))
    return code

if __name__ == "__main__":
    sys.exit(main())
