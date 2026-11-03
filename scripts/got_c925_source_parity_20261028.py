#!/usr/bin/env python3
"""Read-only collection of retained GoT shader/renderer source lineage.

Capture source trees, tracked Git diffs and positive binary hashes, without
assuming current files produced a historical binary. No game, executable,
save, profile or credentials are archived.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

C925 = "c925523b49ef39434024b16b2b014d120471ee1a"
EXPECTED_BINARIES = {
    "15h40_typed_positive": (
        "candidates/c925-20261027-154043-descriptor-typed-proven-progress/shadps4",
        "be3e8c12cf91117917d355d41a03e227a45cd338981d672de4f0ca2c57b68f28",
    ),
    "19h59_perf_positive": (
        "candidates/c925-20261027-195934-performance-positive/shadps4",
        "395682a478f04e4f4a5a2da89488e90fe54ccf502dc23cbecf716a7adeac4e8b",
    ),
    "20h27_cbmask_experimental": (
        "candidates/c925-20261027-202701-cb-shader-mask-preserved/shadps4",
        "63fe630faed40faf2a7dddde87d1084e5dade151add8f4c0b206f86d9caa69b9",
    ),
}
SOURCE_FILES = """
src/shader_recompiler/info.h
src/shader_recompiler/profile.h
src/shader_recompiler/resource.h
src/shader_recompiler/runtime_info.h
src/shader_recompiler/specialization.h
src/shader_recompiler/compact_image_indices.h
src/shader_recompiler/backend/spirv/emit_spirv.cpp
src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp
src/shader_recompiler/backend/spirv/emit_spirv_image.cpp
src/shader_recompiler/backend/spirv/emit_spirv_instructions.h
src/shader_recompiler/backend/spirv/spirv_emit_context.cpp
src/shader_recompiler/backend/spirv/spirv_emit_context.h
src/shader_recompiler/frontend/translate/export.cpp
src/shader_recompiler/ir/opcodes.inc
src/shader_recompiler/ir/ir_emitter.cpp
src/shader_recompiler/ir/ir_emitter.h
src/shader_recompiler/ir/passes/resource_discover_pass.cpp
src/shader_recompiler/ir/passes/resource_patching_pass.cpp
src/shader_recompiler/ir/passes/resource_pass.h
src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp
src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp
src/shader_recompiler/ir/srt_gvn_table.h
src/video_core/renderer_vulkan/vk_rasterizer.cpp
src/video_core/renderer_vulkan/vk_rasterizer.h
src/video_core/renderer_vulkan/vk_instance.cpp
src/video_core/renderer_vulkan/vk_instance.h
src/video_core/renderer_vulkan/vk_pipeline_cache.cpp
src/video_core/renderer_vulkan/vk_pipeline_cache.h
src/video_core/renderer_vulkan/vk_pipeline_common.cpp
src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp
src/video_core/renderer_vulkan/vk_compute_pipeline.cpp
src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp
src/video_core/renderer_vulkan/vk_runtime.cpp
src/video_core/renderer_vulkan/vk_dispatch_helper.cpp
src/video_core/texture_cache/sampler.cpp
src/video_core/texture_cache/texture_cache.cpp
src/video_core/texture_cache/texture_cache.h
src/video_core/texture_cache/types.h
src/video_core/buffer_cache/buffer_cache.cpp
src/video_core/buffer_cache/buffer_cache.h
src/video_core/amdgpu/regs_color.h
src/video_core/amdgpu/regs_shader.h
src/video_core/amdgpu/liverpool.cpp
src/video_core/amdgpu/liverpool.h
src/core/memory.cpp
""".split()
MAX_SINGLE = 1_500_000
MAX_TOTAL = 96_000_000
SOURCE_TREES = ("src/shader_recompiler", "src/video_core")
SOURCE_SUFFIXES = {".cpp", ".h", ".hpp", ".inc", ".inl", ".c"}


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(2**20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(source: Path, *args: str, timeout: int = 35) -> subprocess.CompletedProcess[bytes]:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
               GIT_NO_LAZY_FETCH="1")
    return subprocess.run(
        ["git", "-C", str(source), *args], capture_output=True,
        env=env, timeout=timeout, check=False,
    )


def safe_bytes(target: Path, data: bytes, budget: list[int]) -> bool:
    if len(data) > MAX_SINGLE or budget[0] + len(data) > MAX_TOTAL:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    budget[0] += len(data)
    return True


def capture(source: Path, got: Path, out: Path) -> dict:
    manifest: dict = {
        "record_type": "GOT_C925_SOURCE_PARITY_READ_ONLY",
        "source_path": str(source),
        "known_c925_commit": C925,
        "captured_current": [], "captured_head": [], "captured_c925": [],
        "missing": [], "excluded": [], "failures": [], "binary_sha256_only": {},
        "game_launched": False,
        "source_changed_by_script": False,
    }
    head = git(source, "rev-parse", "--verify", "HEAD^{commit}")
    if head.returncode:
        raise RuntimeError("SOURCE_IS_NOT_A_GIT_REPOSITORY: " + head.stderr.decode(errors="replace")[:400])
    manifest["head_before"] = head.stdout.decode().strip()
    root = git(source, "rev-parse", "--show-toplevel")
    if root.returncode or Path(root.stdout.decode().strip()).resolve() != source:
        raise RuntimeError("SOURCE_ROOT_MISMATCH")
    c925_available = git(source, "cat-file", "-e", C925 + "^{commit}").returncode == 0
    manifest["c925_commit_available"] = c925_available
    if c925_available:
        manifest["c925_is_ancestor"] = git(source, "merge-base", "--is-ancestor", C925, "HEAD").returncode == 0

    source_files = set(SOURCE_FILES)
    for tree in SOURCE_TREES:
        for path in (source / tree).rglob("*"):
            if path.is_file() and path.suffix in SOURCE_SUFFIXES:
                source_files.add(path.relative_to(source).as_posix())
    for ref in ("HEAD", C925) if c925_available else ("HEAD",):
        listing = git(source, "ls-tree", "-r", "--name-only", ref, "--", *SOURCE_TREES)
        if listing.returncode:
            raise RuntimeError("SOURCE_TREE_ENUMERATION_FAILED: " + ref)
        source_files.update(name for name in listing.stdout.decode().splitlines()
                            if Path(name).suffix in SOURCE_SUFFIXES)
    source_files = sorted(source_files)
    manifest["source_file_count"] = len(source_files)
    manifest["source_to_historical_binary_proven"] = False

    commands = {
        "tracked-status.txt": ["status", "--porcelain=v1", "--untracked-files=no"],
        "commit-history.txt": ["log", "--no-show-signature", "-20", "--format=%H %ad %s", "--date=iso"],
        "commit-reflog.txt": ["reflog", "-20", "--date=iso"],
        "current-vs-head.patch": ["diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", *source_files],
        "index-vs-head.patch": ["diff", "--cached", "--no-ext-diff", "--no-textconv", "HEAD", "--", *source_files],
    }
    if c925_available:
        commands["head-vs-c925.patch"] = ["diff", "--no-ext-diff", "--no-textconv", C925, "HEAD", "--", *source_files]
    for name, command in commands.items():
        proc = git(source, *command, timeout=55)
        if proc.returncode:
            manifest["failures"].append({"command": name, "reason": proc.stderr.decode(errors="replace")[:400]})
        elif len(proc.stdout) > 4_000_000:
            manifest["excluded"].append({"command": name, "reason": "DIFF_GT_4MB"})
        else:
            (out / name).write_bytes(proc.stdout)

    budget = [0]
    for rel in source_files:
        path = source / rel
        if any(p.is_symlink() for p in (path, *path.parents) if p != source):
            manifest["excluded"].append({"path": rel, "reason": "SYMLINK_SOURCE"})
            continue
        if not path.is_file():
            manifest["missing"].append(rel)
        elif path.stat().st_size > MAX_SINGLE:
            manifest["excluded"].append({"path": rel, "reason": "LARGE_SOURCE_FILE"})
        else:
            data = path.read_bytes()
            if safe_bytes(out / "current" / rel, data, budget):
                manifest["captured_current"].append({"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            else:
                manifest["excluded"].append({"path": rel, "reason": "TOTAL_BUDGET_EXCEEDED"})
        for ref, field in (("HEAD", "captured_head"), (C925, "captured_c925")):
            if ref == C925 and not c925_available:
                continue
            result = git(source, "show", ref + ":" + rel)
            if result.returncode:
                continue
            if safe_bytes(out / ("baseline_head" if ref == "HEAD" else "baseline_c925") / rel, result.stdout, budget):
                manifest[field].append({"path": rel, "size": len(result.stdout), "sha256": hashlib.sha256(result.stdout).hexdigest()})
            else:
                manifest["excluded"].append({"path": rel, "reason": ref + "_SIZE_LIMIT"})

    for name, (binary_rel, expected) in EXPECTED_BINARIES.items():
        p = got / binary_rel
        if p.is_file() and not p.is_symlink():
            found = digest_file(p)
            manifest["binary_sha256_only"][name] = {
                "expected": expected, "actual": found, "match": found == expected,
            }
        else:
            manifest["binary_sha256_only"][name] = {"expected": expected, "status": "NOT_PRESENT"}
    final = git(source, "rev-parse", "HEAD")
    manifest["head_after"] = final.stdout.decode().strip() if not final.returncode else "ERROR"
    manifest["head_unchanged"] = manifest["head_before"] == manifest["head_after"]
    changed = []
    for entry in manifest["captured_current"]:
        path = source / entry["path"]
        if not path.is_file() or path.is_symlink() or digest_file(path) != entry["sha256"]:
            changed.append(entry["path"])
    manifest["source_changed_during_capture"] = changed
    manifest["capture_complete"] = not (changed or manifest["excluded"] or manifest["failures"])
    manifest["total_source_bytes"] = budget[0]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    return manifest


def append_diary(project: Path, record: dict) -> None:
    file = project / "docs" / "emulator" / "TRIALS.jsonl"
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open("a", encoding="utf8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.home() / "projects/shadps4")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--got-root", type=Path)
    args = parser.parse_args()
    project = args.project_root.expanduser().resolve()
    got = (args.got_root or project / "GhostOfTsushima").expanduser().resolve()
    experiment = got / "experiments/c925-integrated-gpu-20261027-122701"
    default_source = experiment / "source"
    if not (default_source / "src/shader_recompiler").is_dir():
        default_source = experiment
    source = (args.source or default_source).expanduser().resolve()
    logs = project / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    tag = "got-shader-parity-" + stamp
    out = logs / tag
    out.mkdir(mode=0o700)
    archive = logs / (tag + ".tar.gz")
    result = "NOT_STARTED"
    exit_code = 1
    summary: dict = {}
    try:
        print("STAGE=READ_ONLY_CAPTURE_GO_T_SOURCE", flush=True)
        if not source.is_dir():
            raise RuntimeError("GOT_C925_WORKTREE_MISSING: " + str(source))
        summary = capture(source, got, out)
        if not summary["head_unchanged"]:
            raise RuntimeError("SOURCE_HEAD_CHANGED_DURING_READ")
        if not summary["capture_complete"]:
            raise RuntimeError("SOURCE_CAPTURE_INCOMPLETE_SEE_MANIFEST")
        result = "EXACT_GOT_SHADER_SOURCE_CAPTURED"
        exit_code = 0
        print("GOT_SOURCE_HEAD=" + summary["head_before"], flush=True)
        print("HEAD_UNCHANGED=YES", flush=True)
        print("CAPTURED_CURRENT=" + str(len(summary["captured_current"])), flush=True)
        print("CAPTURED_BASELINE_HEAD=" + str(len(summary["captured_head"])), flush=True)
        print("CAPTURED_BASELINE_C925=" + str(len(summary["captured_c925"])), flush=True)
        for name, check in summary["binary_sha256_only"].items():
            print("BINARY_" + name + "=" + ("MATCH" if check.get("match") else check.get("status", "MISMATCH")), flush=True)
    except BaseException as exc:
        result = "SOURCE_CAPTURE_FAILED_" + type(exc).__name__
        (out / "failure.txt").write_text(str(exc) + "\n", encoding="utf8")
        print(result + ": " + str(exc), file=sys.stderr, flush=True)
        exit_code = 2
    finally:
        (out / "result.txt").write_text("RESULT=" + result + "\nEXIT_CODE=" + str(exit_code) + "\nGAME_LAUNCHED=NO\nSSH_SESSION=OPEN\n", encoding="utf8")
        record = {
            "schema_version": 2, "record_id": tag,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "record_type": "GOT_NATIVE_SHADER_SOURCE_PARITY_READ_ONLY", "game_id": "CUSA11456",
            "tested_games": [], "game_effect": "NOT_RUN", "result": result,
            "exit_code": exit_code, "archive": archive.name,
            "source_commit": summary.get("head_before", "UNVERIFIED"),
            "decision": "SOURCE_DIAGNOSTIC_ONLY_NO_RENDERING_CHANGE",
            "next_test": "Port exact GPU-indexed 2D/3D/Cube and dynamic image/sampler resources to unified source without losing existing GoT progress",
        }
        try:
            append_diary(project, record)
        except OSError as exc:
            (out / "diary-error.txt").write_text(repr(exc) + "\n")
        with tarfile.open(archive, "w:gz", compresslevel=2) as tar:
            tar.add(out, arcname=tag)
        print("RESULT=" + result, flush=True)
        print("UPLOAD_THIS=" + str(archive), flush=True)
        print("SSH_SESSION=OPEN", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
