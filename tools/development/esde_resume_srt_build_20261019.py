#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Resume the existing Oct 19 shadPS4 candidate after the missing SRT header.

The original installed emulator, source trees, saves, GPU services and SSH
are untouched. All changes go only to the isolated ESDE/fullstack-audit tree.
"""
from __future__ import annotations

import datetime as dt
import fcntl
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
RUN = ESDE / "fullstack-audit-20261019-165723"
SOURCE = RUN / "source"
BUILD = RUN / "build"
PROTECTED = ROOT / "GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source"
PROTECTED_ELF = ROOT / (
    "GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/"
    "shadps4.fullgfx-uffd-batched-read-gow-uffd-batched-read-20261017-231412"
)
PROTECTED_SHA = "8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef"
UPSTREAM = "d615b3887ba29025a59242e1a26f5b68bb176158"
GOW_REF = "f909c3abb1ac1e7c8d87de833d3659f7d0b8fc87"
SOURCE_CPP = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
TARGET = "CMakeFiles/shadps4.dir/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp.o"
IMAGE = "shadps4-ngs2-builder:trixie-clang19-v1"
SNAPSHOT = "d3d17052ec606ac483e8a48a4f62b35d7243a7fd"
PINNED_HEADERS = {
    "src/shader_recompiler/ir/passes/srt_offset_preflight.h":
        "667744445a209b0a74197cc6ee8f28e2a1486b63",
    "src/shader_recompiler/ir/passes/srt_uniformity.h":
        "95bdea6f8db9d0a717bd568fd0ae8e0003442170",
    "src/shader_recompiler/ir/passes/srt_proven_identity.h":
        "c9f1f099f93c1a6ab78c32eed0b6f1ba816cdb39",
    "src/shader_recompiler/ir/passes/srt_host_barrier.h":
        "da91f2ca882474ff990ebe4344bda1ff30354dbf",
    "src/shader_recompiler/ir/passes/srt_dependency_schedule.h":
        "7fa273e9ce2e0a4e598f8caf964a02d6d9f4b943",
    "src/shader_recompiler/ir/passes/srt_inline_read_codegen.h":
        "642f62e5353032ff22a23dbd82f665f0d6b5505d",
    "src/shader_recompiler/ir/passes/srt_offset_codegen.h":
        "46ecf54cb408600570c4f84869c20b3fe7faac3d",
}
INCLUDES = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)


def cmd(*args, check=True, **kw):
    p = subprocess.run(list(map(str, args)), capture_output=True, **kw)
    if check and p.returncode:
        raise RuntimeError("command failed: " + " ".join(map(str, args)) +
                           "\n" + p.stderr.decode("utf8", "replace")[-1800:])
    return p


def git(*args, check=True):
    return cmd("git", "-C", SOURCE, *args, check=check).stdout.decode("utf8", "replace").strip()


def sha_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for buf in iter(lambda: f.read(1024 * 1024), b""):
            h.update(buf)
    return h.hexdigest()


def blob_hash(data: bytes):
    return hashlib.sha1(("blob " + str(len(data)) + "\0").encode() + data).hexdigest()


def safe_file(path):
    return path.is_file() and not path.is_symlink()


def verify(report):
    if not (SOURCE / ".git").exists() or not (BUILD / "build.ninja").is_file():
        raise RuntimeError("Original isolated source/build checkpoint missing")
    if Path(git("rev-parse", "--show-toplevel")).resolve() != SOURCE.resolve():
        raise RuntimeError("Source checkout is not the isolated source tree")
    if git("diff", "--name-only", "--diff-filter=U"):
        raise RuntimeError("Unresolved Git conflict in isolated source; cannot build")
    if cmd("git", "-C", SOURCE, "merge-base", "--is-ancestor", UPSTREAM, "HEAD",
           check=False).returncode:
        raise RuntimeError("October 19 upstream pin is not present")
    if git("rev-parse", "MERGE_HEAD", check=False) != GOW_REF and cmd(
        "git", "-C", SOURCE, "merge-base", "--is-ancestor", GOW_REF, "HEAD",
        check=False).returncode:
        raise RuntimeError("Pinned GoW source merge was not reached")
    if not safe_file(PROTECTED_ELF) or sha_file(PROTECTED_ELF) != PROTECTED_SHA:
        raise RuntimeError("Previously working GoW reference ELF hash differs")
    if not safe_file(SOURCE / SOURCE_CPP):
        raise RuntimeError("Merged SRT source unit missing")
    cpp = (SOURCE / SOURCE_CPP).read_text()
    if '#include "shader_recompiler/ir/passes/srt_offset_preflight.h"' not in cpp:
        raise RuntimeError("Unexpected SRT source revision: referenced missing header absent")
    if "SrtCodegen::EmitConstantLeftShiftByDynamicCount" not in cpp:
        raise RuntimeError("Verified SRT shift helper call unexpectedly missing")
    if not safe_file(PROTECTED / SOURCE_CPP):
        raise RuntimeError("Protected source missing: cannot establish SRT provenance")
    # This SHA belongs to the already-confirmed protected local source, not
    # the three-way merged output, which legitimately differs from it.
    expected = "14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161"
    report["protected_srt_current_sha256"] = sha_file(PROTECTED / SOURCE_CPP)
    report["protected_srt_matches_oct19_proven_checkpoint"] = (
        report["protected_srt_current_sha256"] == expected
    )
    if cmd("docker", "image", "inspect", IMAGE, check=False).returncode:
        raise RuntimeError("Existing Debian/Clang19 Docker builder image is missing")


def load_missing(rel, report):
    """Plan a single missing project-local header, without editing yet."""
    if rel not in PINNED_HEADERS:
        raise RuntimeError("Missing unreviewed SRT source dependency: " + rel)
    protected = PROTECTED / rel
    if safe_file(protected):
        contents = protected.read_bytes()
        origin = "PROTECTED_GOW_LOCAL_SOURCE"
    else:
        url = ("https://raw.githubusercontent.com/Chreece/shadPS4/" +
               SNAPSHOT + "/tools/development/proven_sources_20261019/" + rel)
        fetch = cmd("curl", "-4", "-fLsS", "--retry", "3",
                    "--connect-timeout", "20", "--max-time", "90", url)
        contents = fetch.stdout
        origin = "PINNED_GITHUB_GOT_HEADER"
        if blob_hash(contents) != PINNED_HEADERS[rel]:
            raise RuntimeError("Pinned SRT header Git blob does not match: " + rel)
    actual = blob_hash(contents)
    # The protected GoW user source can be newer than the Oct17 Git snapshot.
    # Keep its code rather than downgrading, but mark its provenance honestly.
    report.setdefault("header_plan", {})[rel] = {
        "origin": origin,
        "git_blob": actual,
        "pinned_git_blob": PINNED_HEADERS[rel],
        "equals_pinned_snapshot": actual == PINNED_HEADERS[rel],
        "sha256": hashlib.sha256(contents).hexdigest(),
    }
    return contents


def plan_headers(report):
    planned: dict[str, bytes] = {}
    examined = set()
    queue = [SOURCE_CPP]
    while queue:
        name = queue.pop()
        if name in examined:
            continue
        examined.add(name)
        in_tree = SOURCE / name
        contents = planned[name] if name in planned else (
            in_tree.read_bytes() if safe_file(in_tree) else None
        )
        if contents is None:
            raise RuntimeError("Dependency vanished during preflight: " + name)
        for inc in INCLUDES.findall(contents.decode("utf8", "replace")):
            if not inc.startswith("shader_recompiler/"):
                continue
            rel = "src/" + inc
            if rel in examined:
                continue
            local_path = SOURCE / rel
            if local_path.is_symlink():
                raise RuntimeError("Unexpected source-header symlink: " + rel)
            if not local_path.exists() and rel not in planned:
                planned[rel] = load_missing(rel, report)
            if not local_path.is_file() and rel not in planned:
                raise RuntimeError("Dependency missing with no trusted source: " + rel)
            queue.append(rel)
    if "src/shader_recompiler/ir/passes/srt_offset_preflight.h" not in planned:
        # A rerun may find the file previously repaired; normal idempotence.
        report["srt_preflight_header_already_present"] = True
    report["dependency_files_inspected"] = len(examined)
    return planned


def write_plan(plan, report):
    # Every dependency has been fetched/verified and parsed before any write.
    for rel, data in sorted(plan.items()):
        dest = SOURCE / rel
        if dest.exists() or dest.is_symlink():
            raise RuntimeError("Missing-header race or unexpected existing path: " + rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if blob_hash(dest.read_bytes()) != blob_hash(data):
            raise RuntimeError("Copied SRT header changed on disk: " + rel)
    if plan:
        git("add", "--", *sorted(plan))
        git("diff", "--cached", "--check")
    report["new_isolated_header_files"] = sorted(plan)


def build(report, log):
    # The game may be running in a different Moonlight/ES-DE session. Never
    # interrupt it or run a high-load build on top of an active emulator.
    if cmd("pgrep", "-u", str(os.getuid()), "-x", "shadps4",
           check=False).returncode == 0:
        raise RuntimeError("A PS4 game is running; close it normally first")
    common = ["docker", "run", "--rm",
              "--user", str(os.getuid()) + ":" + str(os.getgid()),
              "--mount", "type=bind,src=" + str(ESDE) + ",dst=" + str(ESDE),
              "--workdir", str(RUN), "--env", "HOME=" + str(RUN), IMAGE]

    def step(name, argv):
        with log.open("a") as out:
            out.write("\nSTAGE=" + name + "\n")
            out.flush()
            p = subprocess.run(list(map(str, argv)), stdout=out,
                               stderr=subprocess.STDOUT, cwd=RUN)
        report.setdefault("build_steps", {})[name] = p.returncode
        if p.returncode:
            raise RuntimeError("Failed isolated build stage " + name +
                               ", exit=" + str(p.returncode))

    step("RECOMPILE_SRT_UNIT", common + [
        "cmake", "--build", str(BUILD), "--target", TARGET, "--parallel", "1"
    ])
    step("INCREMENTAL_LINK", common + [
        "cmake", "--build", str(BUILD), "--target", "shadps4", "--parallel", "4"
    ])
    exe = BUILD / "shadps4"
    if not safe_file(exe):
        raise RuntimeError("No compiled binary after incremental link")
    with exe.open("rb") as f:
        if f.read(5) != b"\x7fELF\x02":
            raise RuntimeError("Compiled file is not a 64-bit ELF binary")
    p = cmd("ldd", str(exe), check=False)
    if p.returncode or b"not found" in p.stdout:
        raise RuntimeError("New candidate ELF has missing dynamic libraries")
    p = cmd("timeout", "30", str(exe), "--help", check=False, cwd=RUN)
    if p.returncode:
        raise RuntimeError("CLI smoke failed with exit " + str(p.returncode))
    report["candidate"] = {"sha256": sha_file(exe), "path": str(exe),
                           "status": "COMPILED_NOT_GAMEPLAY_VALIDATED"}
    print("CANDIDATE_BINARY=" + str(exe))
    print("CANDIDATE_SHA256=" + report["candidate"]["sha256"])


def self_test():
    """No Git/network/compiler: reproduce the exact missing transitive includes."""
    import tempfile
    global SOURCE, PROTECTED
    old_source, old_protected = SOURCE, PROTECTED
    try:
        with tempfile.TemporaryDirectory(prefix="shadps4-srt-deps-") as d:
            SOURCE = Path(d) / "source"
            PROTECTED = Path(d) / "protected"
            cpp = SOURCE / SOURCE_CPP
            cpp.parent.mkdir(parents=True)
            cpp.write_text(
                '#include "shader_recompiler/ir/passes/srt_offset_preflight.h"\n'
            )
            names = (
                "srt_offset_preflight.h", "srt_uniformity.h", "srt_proven_identity.h"
            )
            for i, name in enumerate(names):
                f = (PROTECTED / "src/shader_recompiler/ir/passes" / name)
                f.parent.mkdir(parents=True, exist_ok=True)
                if i + 1 < len(names):
                    f.write_text(
                        '#include "shader_recompiler/ir/passes/' +
                        names[i + 1] + '"\n'
                    )
                else:
                    f.write_text("#pragma once\n")
            report = {}
            plan = plan_headers(report)
            if len(plan) != 3 or not all(
                ("src/shader_recompiler/ir/passes/" + name) in plan
                for name in names
            ):
                raise RuntimeError("Transitive SRT include closure incomplete")
            if any(v["origin"] != "PROTECTED_GOW_LOCAL_SOURCE"
                   for v in report["header_plan"].values()):
                raise RuntimeError("SRT dependency source order changed")
    finally:
        SOURCE, PROTECTED = old_source, old_protected
    print("SRT_HEADER_DEPENDENCY_SELFTEST=PASS cases=3")
    return 0


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        return self_test()
    if len(sys.argv) != 1:
        raise RuntimeError("Usage: esde_resume_srt_build_20261019.py [--self-test]")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS.mkdir(parents=True, exist_ok=True)
    RUN.mkdir(parents=True, exist_ok=True)
    log = LOGS / ("esde-fullstack-srt-resume-" + stamp + ".log")
    archive = LOGS / ("esde-fullstack-srt-resume-" + stamp + ".tar.gz")
    report = {"kind": "ESDE_SRT_HEADER_INCREMENTAL_REPAIR",
              "workspace": str(RUN), "status": "STARTED",
              "installed_binary_changed": False,
              "protected_binary_or_source_changed": False}
    lock_path = ESDE / ".fullstack-srt-resume.lock"
    rc = 0
    try:
        with lock_path.open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise RuntimeError("A full-stack SRT resume is already running") from e
            verify(report)
            plan = plan_headers(report)
            write_plan(plan, report)
            build(report, log)
            report["status"] = "PASS_COMPILED_ONLY"
    except Exception as exc:
        rc = 2
        report["status"] = "STOPPED_WITHOUT_INSTALLATION"
        report["error"] = str(exc)
        print("SRT_RESUME_ERROR=" + str(exc))
    out = RUN / ("srt-header-resume-" + stamp + ".json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(out, arcname="resume/manifest.json")
        if log.exists():
            tf.add(log, arcname="resume/build.log")
    print("SRT_RESUME_STATUS=" + report["status"])
    print("REPORT=" + str(archive))
    print("INSTALLED_ESDE_BINARY=UNCHANGED")
    print("GOW_GOT_PROTECTED_BUILD_AND_SAVES=UNCHANGED")
    return rc


if __name__ == "__main__":
    sys.exit(main())
