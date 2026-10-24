#!/usr/bin/env python3
"""Resume the verified October 24 generic GoT build after the shared-SRT compile fix.

The preserved Ninja outputs were built from scratch for the exact candidate.
Only the missing host-uniform SRT definition is added. Never modifies the
original GoT checkout, Git HEAD, user configuration, or ES-DE emulator.
Falls back to the corrected full composer if the preserved build is unavailable.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path.home() / "projects/shadps4"
SRC = ROOT / "GhostOfTsushima/source"
RUNNER = ROOT / "scripts/build_run_generic_large_indexed_resources_20261023.py"
PREVIOUS_RUN = ROOT / "logs/generic-large-indexed-resource-20261024-120530-9j9uiu2i"
BUILD = ROOT / "GhostOfTsushima/fresh-builds/ca69b6509427-3967624"
WORK = ROOT / "GhostOfTsushima/fresh-source/ca69b6509427-3967624"
PREFIX = "ca69b6509427"
PREVIOUS_PARENT = "d6705757e77056fa4708b528167c3a1f72bd1d0a"
FLAT = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
CODEGEN = "src/shader_recompiler/ir/passes/srt_dependency_codegen.inc"


def fallback(reason: str) -> int:
    print("PARTIAL_BUILD_RESUME_UNAVAILABLE=" + reason, flush=True)
    print("FALLBACK=CORRECTED_FULL_GENERIC_BUILD", flush=True)
    return subprocess.call([sys.executable, "-u", str(RUNNER), "--launch"])


def load_runner():
    spec = importlib.util.spec_from_file_location("generic_got_runner", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("CANNOT_LOAD_PINNED_RUNNER")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if not callable(getattr(module, "insert_host_uniform_srt_gate", None)):
        raise RuntimeError("SRT_HOST_UNIFORM_RUNNER_FIX_MISSING")
    return module


def main() -> int:
    if not RUNNER.is_file():
        raise RuntimeError("CORRECTED_RUNNER_MISSING")
    r = load_runner()
    r.BASE = r.git("rev-parse", "HEAD").decode().strip()
    r.BASE_STATUS = r.source_status()
    r.validate_source_status(r.BASE_STATUS)

    old_info = PREVIOUS_RUN / "info"
    previous_manifest = old_info / "correction.json"
    previous_log = old_info / "full-build.log"
    ninja_file = BUILD / "build.ninja"
    cmake_cache = BUILD / "CMakeCache.txt"

    if not (previous_manifest.is_file() and previous_log.is_file()
            and ninja_file.is_file() and cmake_cache.is_file()):
        return fallback("previous verified build outputs or evidence missing")
    if "IsHostUniformSrtOffset" not in previous_log.read_text(errors="replace"):
        return fallback("previous failure no longer matches the reviewed SRT error")
    cached_home = next((line.partition("=")[2]
                        for line in cmake_cache.read_text(errors="replace").splitlines()
                        if line.startswith("CMAKE_HOME_DIRECTORY:INTERNAL=")), "")
    if cached_home != str(WORK):
        return fallback("existing Ninja build references a different source directory")
    q = r.run(["git", "-C", SRC, "rev-parse", "--verify",
               PREFIX + "^{commit}"], check=False, timeout=20)
    if q.returncode:
        return fallback("the previous synthetic source commit was pruned")
    old_rev = (q.stdout or b"").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", old_rev) or not old_rev.startswith(PREFIX):
        return fallback("previous synthetic commit ambiguous or unavailable")

    manifest = json.loads(previous_manifest.read_text())
    parent = manifest.get("previous_parent")
    if parent != PREVIOUS_PARENT:
        raise RuntimeError("PREVIOUS_RECONSTRUCTION_PARENT_CHANGED")
    if r.git("rev-parse", old_rev + "^").decode().strip() != parent:
        raise RuntimeError("OLD_CANDIDATE_PARENT_MISMATCH")
    srt = r.show(old_rev, FLAT).decode()
    included = r.show(old_rev, CODEGEN).decode()
    if (srt.count('#include "shader_recompiler/ir/passes/srt_dependency_codegen.inc"') != 1
            or srt.count("static bool IsHostUniformSrtOffset(") != 0
            or included.count("IsHostUniformSrtOffset(") != 2):
        raise RuntimeError("PREVIOUS_SRT_SOURCE_NOT_REVIEWED_VERSION")
    if WORK.exists() or WORK.is_symlink():
        raise RuntimeError("PRESERVED_SOURCE_PATH_ALREADY_EXISTS_NOT_OVERWRITING")

    (ROOT / "logs").mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(
        prefix="got-generic-srt-resume-" + time.strftime("%Y%m%d-%H%M%S") + "-",
        dir=ROOT / "logs"))
    info = run_dir / "info"
    info.mkdir()

    temp_ref = None
    try:
        with (ROOT / "GhostOfTsushima/.build.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("ANOTHER_SHADPS4_BUILD_OWNS_LOCK")

            r.stage("resume_compilation_after_verified_generic_srt_fix")
            patched = r.insert_host_uniform_srt_gate(srt)
            with tempfile.TemporaryDirectory(dir=ROOT / "logs",
                                             prefix="got-srt-index-") as d:
                idx = Path(d) / "index"
                e = r.env({"GIT_INDEX_FILE": str(idx)})
                r.git("read-tree", old_rev, e=e)
                blob, encoded = r.update_index_text(idx, FLAT, patched)
                tree = r.git("write-tree", e=e).decode().strip()
            diff = r.git("diff", "--name-only", old_rev, tree).decode().splitlines()
            if diff != [FLAT]:
                raise RuntimeError("SRT_CORRECTION_CHANGED_OTHER_SOURCE=" + repr(diff))
            rev = r.commit_tree(
                tree, parent,
                b"Repair generic host-uniform SRT dependency gate on the verified GoT candidate\n")
            if r.git("rev-parse", rev + "^{tree}").decode().strip() != tree:
                raise RuntimeError("RECOVERED_SOURCE_TREE_MISMATCH")

            temp_ref = "refs/shadps4-composer/resume-" + rev[:12]
            r.git("update-ref", temp_ref, rev)
            r.stage("restore_disposable_source_at_original_ninja_path")
            r.add_fresh_source(WORK, rev, temp_ref)
            r.submodules_at(WORK)
            if r.run(["git", "-C", WORK, "rev-parse", "HEAD"],
                     timeout=20).stdout.decode().strip() != rev:
                raise RuntimeError("RESUME_CLONE_REVISION_MISMATCH")

            objects = [p for p in BUILD.rglob("*.o") if p.is_file()]
            if len(objects) < 2000:
                raise RuntimeError("PRESERVED_BUILD_OBJECTS_MISSING")
            # Every source byte except FLAT is identical to the previously
            # compiled candidate. Restored checkout timestamps must not
            # invalidate thousands of otherwise correct Ninja objects.
            old_time = min(p.stat().st_mtime for p in objects) - 300
            for path in WORK.rglob("*"):
                if (".git" not in path.parts and path.is_file()
                        and not path.is_symlink() and path != WORK / FLAT):
                    os.utime(path, (old_time, old_time))
            changed_source = WORK / FLAT
            os.utime(changed_source, None)
            if changed_source.read_text() != patched:
                raise RuntimeError("RESTORED_SRT_SOURCE_MISMATCH")

            manifest["files"][FLAT] = {
                "original_blob": r.git("rev-parse", old_rev + ":" + FLAT).decode().strip(),
                "corrected_blob": blob,
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
            manifest["srt_host_uniform_gate"] = "CONSERVATIVE_SCALAR_IR_CLOSURE"
            manifest["resumed_from_previous_failed_build"] = str(PREVIOUS_RUN)
            mp = info / "correction.json"
            mp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            (info / "srt-host-uniform.patch").write_bytes(
                r.git("diff", old_rev, rev, "--", FLAT))
            r.stage("incremental_compile_preserved_2565_of_2644")
            print("PRESERVED_COMPILATION_OBJECTS=" + str(len(objects)), flush=True)
            print("PRESERVED_NINJA_BUILD=" + str(BUILD), flush=True)
            r.tee(["nice", "-n", "10", "cmake", "--build", BUILD, "--target",
                   "shadps4", "--parallel", "2"], info / "full-build.log")

            binary = r.exe(BUILD)
            if not binary:
                raise RuntimeError("RESUMED_EXECUTABLE_NOT_FOUND")
            csha = r.digest(binary)
            cand = ROOT / "GhostOfTsushima/candidates" / (rev + "-" + csha) / "shadps4"
            cand.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(binary, cand)
            cand.chmod(cand.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            r.assert_source_unchanged()

            report = {
                "status": "GENERIC_LARGE_INDEXED_RESOURCE_BUILD_PASS",
                "base": r.BASE,
                "source_parent": parent,
                "source_parent_tree": r.git("rev-parse", parent + "^{tree}").decode().strip(),
                "verified_parent": manifest["verified_parent"],
                "verified_parent_tree": manifest["verified_parent_tree"],
                "verified_parent_mode": manifest["verified_parent_mode"],
                "upstream_parent": manifest["upstream_parent"],
                "upstream_parent_tree": manifest["upstream_parent_tree"],
                "upstream_main": r.UPSTREAM_MAIN,
                "pr_5492_head": r.PR_5492_HEAD,
                "generic_source_commit": r.GEN,
                "revision": rev,
                "tree": tree,
                "candidate": str(cand),
                "candidate_sha256": csha,
                "manifest_sha256": r.digest(mp),
                "full_build": "PASS",
                "source_untouched": True,
                "fresh_source_clone": True,
                "fresh_build_from_zero": True,
                "legacy_builds_searched": False,
                "tests_enabled": False,
                "original_submodules_untouched": True,
                "generic_indexed_image_tables": "GPU_NONUNIFORM_COMPLETE_TSHARP_SSHARP",
                "game_specific_options": False,
                "resumed_partial_fresh_build": True,
                "all_games_validated": False,
            }
            (info / "build-result.json").write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n")
            print("RECOVERED_BUILD=PASS", flush=True)
            print("CANDIDATE=" + str(cand), flush=True)
            print("CANDIDATE_SHA256=" + csha, flush=True)
            print("COMPOSED_REVISION=" + rev, flush=True)

        # The trusted runtime launcher isolates itself from the SSH session.
        r.stage("launch_got_candidate")
        rc = r.launch(info, rev, tree, csha, cand)
        if rc:
            (info / "runtime-exit.txt").write_text("GAME_EXIT_CODE=" + str(rc) + "\n")
        print("UPLOAD_THIS=" + str(r.archive(run_dir, None)), flush=True)
        return rc
    except BaseException as error:
        import traceback
        traceback.print_exc()
        print("RESUMED_BUILD_ERROR=" + str(error), flush=True)
        print("UPLOAD_THIS=" + str(r.archive(run_dir, error)), flush=True)
        return 2
    finally:
        if temp_ref:
            r.git("update-ref", "-d", temp_ref, check=False)
        # Keep the restored source and Ninja objects for rapid runtime fixes.
        # Do not modify or remove the original GhostOfTsushima/source checkout.


if __name__ == "__main__":
    raise SystemExit(main())
