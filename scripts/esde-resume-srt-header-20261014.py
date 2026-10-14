#!/usr/bin/env python3
"""Repair ONLY the missing GoW84 SRT companion header and resume existing Ninja.

No fresh clone, no overlay replay, no GoW/GoT test runs, no automated input,
no launcher replacement. Uses the already committed graphics merge 776c5fa8.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HOME = Path.home()
ROOT = HOME / "projects/shadps4"
WORK = ROOT / "ESDE/release-20261014-160446"
SRC = WORK / "source"
BUILD = WORK / "build"
GOW = ROOT / "GodOfWarRagnarok"
GOW84 = GOW / "builds/gow84-ds-ordered-20261013-194151-79151/source"
LOGS = ROOT / "logs"
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
LOG = LOGS / ("esde-srt-header-" + STAMP + ".log")
REPORT = WORK / "evidence" / ("srt-header-" + STAMP)
PARENT = "776c5fa8c41613bcce3ad5fc8cbdf7c86f5a2021"
COMMIT_TITLE = "ES-DE: retain verified GoW84 SRT offset companion header"
SRT = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
HEADER = "src/shader_recompiler/ir/passes/srt_offset_codegen.h"
SRT_SHA = "b70df7890134f497e9eb7efd2c717d6cf09afa784a007ca7dac59f50a26f6651"
COMPILED_BASE_SHA = "837d07746f33efdd85390ea5e61084e781a44fa82f780253786ea9307cb4b26f"
STATUS = "NOT_BUILT"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def log(*parts):
    value = " ".join(str(p) for p in parts)
    print(value, flush=True)
    with LOG.open("a") as stream:
        stream.write(value + "\n")


def command(*argv: str, check=True) -> subprocess.CompletedProcess:
    result = subprocess.run([str(x) for x in argv], cwd=SRC,
                            stdin=subprocess.DEVNULL, capture_output=True)
    if check and result.returncode:
        raise RuntimeError("Command failed: " + " ".join(map(str, argv)) +
                           "\n" + result.stderr.decode(errors="replace")[-1000:])
    return result


def git(*argv: str) -> str:
    return command("git", *argv).stdout.decode(errors="replace").strip()


def pinned(path: Path, root: Path, expected: str | None = None) -> bytes:
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError("Missing/unsafe source: " + str(path))
    if path.stat().st_size < 10 or path.stat().st_size > 2_000_000:
        raise RuntimeError("Unusual source file length: " + str(path))
    raw = path.read_bytes()
    if expected and sha(raw) != expected:
        raise RuntimeError("Pinned source digest differs: " + str(path))
    return raw


def source_candidates(rel: str) -> list[Path]:
    # The exact GoW84 source is preferred. Fallbacks are valid ONLY when the
    # sibling flatten_extended_userdata_pass.cpp has the same pinned SHA-256.
    candidates = [GOW84 / rel]
    bases = [GOW / "builds", GOW / "tests"]
    for base in bases:
        if base.is_dir():
            for source in sorted(base.glob("*/source")):
                candidates.append(source / rel)
    return list(dict.fromkeys(candidates))


def verified_companion(rel: str) -> tuple[Path, bytes]:
    verified = []
    examined = []
    for path in source_candidates(rel):
        if not path.is_file() or path.is_symlink():
            continue
        root = path.parents[len(Path(rel).parts)-1]
        sibling = root / SRT
        try:
            if sha(pinned(sibling, GOW, SRT_SHA)) != SRT_SHA:
                continue
            raw = pinned(path, GOW)
        except RuntimeError:
            continue
        digest = sha(raw)
        examined.append({"source": str(path), "sha256": digest})
        verified.append((path, raw))
    (REPORT / "source-candidates.json").write_text(
        json.dumps(examined, indent=2) + "\n")
    if not verified:
        raise RuntimeError("SRT companion header is absent from the exact GoW84 source and "
                           "other source trees with the same verified SRT .cpp. "
                           "No files changed. See source-candidates.json")
    digests = {sha(raw) for _, raw in verified}
    if len(digests) > 1:
        raise RuntimeError("Ambiguous SRT companion versions: " + repr(sorted(digests)))
    # Prefer the original Phase84 source path.
    verified.sort(key=lambda item: item[0] != GOW84 / rel)
    return verified[0]


def include_dependencies(body: bytes, rel: str) -> list[str]:
    result = []
    for included in re.findall(rb'(?m)^[ \t]*#[ \t]*include[ \t]*"([^"\n]+)"', body):
        inc = included.decode("utf-8", errors="strict")
        if inc.startswith(("shader_recompiler/", "core/", "common/",
                           "video_core/", "imgui/")):
            candidate = (Path("src") / inc)
        else:
            candidate = Path(rel).parent / inc
        if candidate.is_absolute() or ".." in candidate.parts:
            raise RuntimeError("Header includes unsafe path: " + inc)
        normalized = candidate.as_posix()
        if (SRC / candidate).exists() or (SRC / candidate).is_symlink():
            if (SRC / candidate).is_symlink():
                raise RuntimeError("Companion include is symlinked: " + normalized)
            continue
        if (GOW84 / candidate).is_file():
            result.append(normalized)
    return result


def put_new(path: Path, data: bytes) -> bool:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or sha(path.read_bytes()) != sha(data):
            raise RuntimeError("Refusing to replace different existing source: " + str(path))
        return False
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise RuntimeError("Unexpected missing/symlinked destination directory: " + str(path.parent))
    fd, tmp = tempfile.mkstemp(prefix=".srt-header-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        # link() atomically creates the final name without overwriting anything.
        os.link(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    if file_sha(path) != sha(data):
        raise RuntimeError("Copied source checksum differs: " + str(path))
    return True


def main():
    global STATUS
    LOGS.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(parents=True, exist_ok=False)
    log("RESUME_EXISTING_COMPILED_GRAPHICS_CHECKPOINT", WORK)
    if not (SRC / ".git").is_dir() or not (BUILD / "build.ninja").is_file():
        raise RuntimeError("Existing CMake/Ninja tree is missing. No new checkout attempted")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Existing source has tracked edits; no changes made")
    head = git("rev-parse", "HEAD")
    if head == PARENT:
        pass
    elif git("log", "-1", "--format=%s") == COMMIT_TITLE and git("rev-parse", "HEAD^") == PARENT:
        log("HEADER_CHECKPOINT_ALREADY_COMMITTED", head)
    else:
        raise RuntimeError("Unrecognized source commit: " + head)
    root_cpp = pinned(SRC / SRT, SRC, SRT_SHA)
    pinned(GOW84 / SRT, GOW, SRT_SHA)
    if root_cpp.count(b'#include "shader_recompiler/ir/passes/srt_offset_codegen.h"') != 1:
        raise RuntimeError("Compiled SRT does not include the expected missing header exactly once")
    backup = WORK / "checkpoints/shadps4-compiles-gow-837d07746f33efdd"
    if file_sha(backup) != COMPILED_BASE_SHA:
        raise RuntimeError("Original successfully compiled binary backup changed")
    header_path, header_data = verified_companion(HEADER)
    pending = {HEADER: header_data}
    # Import ONLY missing sibling header dependencies (never override existing
    # modern upstream code or import extra historical .cpp units).
    queue = [HEADER]
    while queue:
        rel = queue.pop(0)
        for additional in include_dependencies(pending[rel], rel):
            if additional in pending:
                continue
            source, raw = verified_companion(additional)
            pending[additional] = raw
            queue.append(additional)
    hashes = {rel: sha(raw) for rel, raw in pending.items()}
    (REPORT / "headers.json").write_text(json.dumps({
        "from_srt_sha256": SRT_SHA, "primary_header": str(header_path),
        "headers": hashes, "clone": False, "auto_input": False,
        "existing_ESDE_install_changed": False
    }, indent=2) + "\n")
    log("VERIFIED_SRT_COMPANION_HEADER", HEADER, hashes[HEADER])
    log("EXACT_HEADER_DEPENDENCIES", len(pending))
    for rel, raw in pending.items():
        dst = SRC / rel
        if dst.exists() or dst.is_symlink():
            pinned(dst, SRC, sha(raw))
    new = []
    for rel, raw in pending.items():
        if put_new(SRC / rel, raw):
            new.append(rel)
            log("ADDED_MISSING_HEADER", rel, hashes[rel])
    if new:
        command("git", "add", "--", *new)
        command("git", "diff", "--cached", "--check")
        command("git", "-c", "user.name=Verified ESDE Header Recovery",
                "-c", "user.email=esde-header@localhost",
                "commit", "-m", COMMIT_TITLE)
        log("SOURCE_CHECKPOINT", git("rev-parse", "HEAD"))
    log("COMPILING_EXISTING_CMAKE_BUILD", "NO_TESTS", "NO_AUTO_CLICKS", "NO_INSTALL")
    with LOG.open("ab") as out:
        result = subprocess.run(["cmake", "--build", str(BUILD), "--target",
                                 "shadps4", "--parallel", "5"],
                                cwd=SRC, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT)
    if result.returncode:
        lines = LOG.read_text(errors="replace").splitlines()
        errors = [line for line in lines if any(x in line for x in
                  ("FAILED:", "error:", "fatal error:", "undefined reference",
                   "ninja: build stopped"))]
        log("COMPILER_EXIT_CODE", result.returncode)
        for line in errors[:16]:
            log("COMPILER_ERROR", line[:1000])
        raise RuntimeError("Incremental compiler failed; see " + str(LOG))
    binary = BUILD / "shadps4"
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("Build returned success but executable is absent")
    result_sha = file_sha(binary)
    STATUS = "COMPILE_PASS_PENDING_INSTALL"
    log("COMPILE=PASS", "SHA256=" + result_sha, "BINARY=" + str(binary))
    log("INSTALLED_ESDE=UNCHANGED")
    log("GAMEPLAY_AUTOMATION=NONE")


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        log("STOP", type(exc).__name__, str(exc))
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if REPORT.exists():
            (REPORT / "failure.txt").write_text(traceback.format_exc())
        sys.exit(1)
    finally:
        log("RESULT=" + STATUS, "LOG=" + str(LOG), "SSH_SESSION=UNCHANGED")
