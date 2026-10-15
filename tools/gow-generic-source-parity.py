#!/usr/bin/env python3
"""Read-only upstream-vs-proven-build source parity, never gameplay configuration.

Does not run emulators, set flags, compile, replace objects, edit source,
change saves/configuration, or touch the installed launcher.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import traceback

FILES = (
    "src/core/libraries/kernel/process.cpp",
    "src/core/cpu_patches.cpp",
    "src/core/cpu_id.cpp",
    "src/shader_recompiler/frontend/translate/translate.cpp",
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
    "src/shader_recompiler/ir/passes/resource_discover_pass.cpp",
    "src/shader_recompiler/ir/passes/resource_patching_pass.cpp",
    "src/shader_recompiler/resource.h",
    "src/video_core/buffer_cache/buffer_cache.cpp",
    "src/video_core/buffer_cache/buffer_cache.h",
    "src/video_core/buffer_cache/fault_manager.cpp",
    "src/video_core/buffer_cache/memory_tracker.h",
    "src/video_core/buffer_cache/region_manager.h",
    "src/video_core/renderer_vulkan/vk_instance.cpp",
    "src/video_core/renderer_vulkan/vk_instance.h",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.h",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
    "src/video_core/renderer_vulkan/vk_rasterizer.h",
)
MARKERS = (
    "MakeSrtRootDependencyPlan", "SrtWalkerSignalHandler",
    "ReadFirstLane", "EmitComputeOffset", "PushPtr",
    "SplitBufferLoadBySharpPhi", "ComputeProvenWriteSize",
    "MarkRegionAsGpuModifiedTracked", "gpu_selective_tracked",
    "TEXEL_STREAM_ROUTE", "TEXEL_IMAGE_SUBRANGE_SYNC",
    "GetArena", "EnsureResident", "BdaPagetable",
    "force_align_arg_pointer", "PipelineCache::GetProgram",
)
BANNED = ("SHADPS4_GOW_", "GOW_SRT_", "CUSA34384",
          "0xe83356c8", "0xad0b8f1f", "0x04691bd5")


def checksum(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def add_text(tar, root, relative, text):
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text)


def safe_read(path, limit=2_000_000):
    p = Path(path)
    if not p.is_file() or p.is_symlink() or p.stat().st_size > limit:
        return None
    return p.read_text(errors="replace")


def path_sha(path):
    p = Path(path)
    return checksum(p) if p.is_file() and not p.is_symlink() else None


def proof(label, path, expected):
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(label + " not available: " + str(path))
    got = checksum(path)
    if got != expected:
        raise RuntimeError(label + " SHA-256 mismatch. Expected " + expected +
                           " actual " + got + "; no claims about this build")
    return {"path": str(path), "sha256": got, "exact_binary_match": True}


def main():
    root = Path.home() / "projects/shadps4/GodOfWarRagnarok"
    good = root / "tests/gow91-build-20261015-125214-1308309"
    new = root / "tests/main-proof-20261015-151529-3107006"
    out_root = Path.home() / "projects/shadps4/logs"
    out_root.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out_root / ("gow-generic-source-parity-" + stamp + "-" + str(os.getpid()))
    out.mkdir(mode=0o700)
    result = {
        "purpose": "Recover exact last-rendering general emulator source",
        "code_changed": False, "compilation_performed": False,
        "game_launched": False, "settings_modified": False,
        "shader_or_game_specific_overrides_enabled": False,
        "status": "STARTED",
    }
    try:
        result["verified_good_binary"] = proof(
            "GoW108 known rendered build", good / "shadps4",
            "7ee847328072f9d65772fc85ee19f93143cbaaafe69e381d70379479ddd81a30")
        result["verified_upstream_binary"] = proof(
            "GoW upstream Stage 5 candidate", new / "build/shadps4",
            "5e9ea0381be1db8c1d98f944e7ec2e6a0842a9af194056f25a99773fc659c272")
        old_manifest = good / "manifest.json"
        if not old_manifest.is_file() or old_manifest.is_symlink():
            raise RuntimeError("GoW108 binary exists but build manifest is missing")
        obj = json.loads(old_manifest.read_text())
        if not isinstance(obj, dict):
            raise RuntimeError("invalid GoW108 build manifest")
        result["good_manifest_keys"] = sorted(obj)
        result["good_manifest_game_specific"] = obj.get("game_specific", "unreported")
        result["good_manifest_binary_sha256"] = obj.get("binary_sha256", "unreported")
        add_text(None, out, "proven-manifest.json", json.dumps(obj, indent=2, default=str))
        git = new / "source/.git"
        if not git.is_dir():
            raise RuntimeError("upstream source checkout is missing")
        upstream_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=new / "source",
            check=True, capture_output=True, text=True, timeout=10).stdout.strip()
        result["new_source_head"] = upstream_sha
        overlays = []
        # Do not infer effective object provenance from source folder order.
        # List ALL available candidates and identify primary last-stage overlays.
        for rel in FILES:
            current = new / "source" / rel
            primary = good / "overlay" / rel
            candidates = [primary, good / "source" / rel]
            old_root = root / "tests"
            for directory in sorted(old_root.glob("gow91-build-*/overlay")):
                candidate = directory / rel
                if candidate.is_file() and not candidate.is_symlink():
                    candidates.append(candidate)
            seen = set()
            sources = []
            for p in candidates:
                if not p.is_file() or p.is_symlink():
                    continue
                digest = path_sha(p)
                if digest in seen:
                    continue
                seen.add(digest)
                sources.append({"path": str(p), "sha256": digest,
                                "primary_good_overlay": p == primary})
            source_current = safe_read(current)
            source_previous = safe_read(primary)
            record = {
                "relative_path": rel,
                "candidate_sha256": path_sha(current),
                "last_good_overlay_sha256": path_sha(primary),
                "possible_prior_overlays": sources[:100],
                "actual_linked_object_provenance": "requires build manifest verification",
            }
            if source_current is not None:
                record["current_marker_counts"] = {
                    key: source_current.count(key) for key in MARKERS if key in source_current}
                record["current_game_specific_markers"] = [
                    key for key in BANNED if key in source_current]
            if source_previous is not None:
                record["good_overlay_marker_counts"] = {
                    key: source_previous.count(key) for key in MARKERS if key in source_previous}
                record["good_overlay_game_specific_markers"] = [
                    key for key in BANNED if key in source_previous]
                if source_current is not None and source_previous != source_current:
                    diff = "".join(difflib.unified_diff(
                        source_previous.splitlines(keepends=True),
                        source_current.splitlines(keepends=True),
                        fromfile="last-good-overlay/" + rel,
                        tofile="upstream-proof/" + rel))
                    add_text(None, out, "source-diffs/" + rel + ".patch", diff[:600000])
            result.setdefault("file_comparisons", []).append(record)
        result["status"] = "READ_ONLY_PARITY_COMPLETE"
    except BaseException as exc:
        result["status"] = "FAILED"
        result["error"] = repr(exc)
        add_text(None, out, "traceback.txt", traceback.format_exc())
    finally:
        add_text(None, out, "result.json", json.dumps(result, indent=2, sort_keys=True)+"\n")
        archive = out_root / (out.name + ".tar.gz")
        with tarfile.open(archive, "w:gz", compresslevel=3) as tar:
            for p in sorted(out.rglob("*")):
                if p.is_file() and not p.is_symlink():
                    tar.add(p, arcname=out.name+"/"+str(p.relative_to(out)))
        print("PARITY_STATUS=" + result["status"], flush=True)
        print("UPLOAD=" + str(archive), flush=True)
    return 0 if result["status"] == "READ_ONLY_PARITY_COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
