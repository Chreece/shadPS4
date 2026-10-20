#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Integrate general Ghost shader/GPU implementation into GoW/upstream ES-DE.

Three-way source merges ONLY. Never choose wholesale ours/theirs, skip a
conflict, add a title/shader-specific bypass, or install a production binary.
Any conflicts/unknown includes stop BEFORE source changes or GPU/game work.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from esde_general_semantic_resolver_20261019 import (
    resolve_reviewed, preserve_verified_srt_header,
    check_archived_conflict_evidence,
)
from esde_general_late_semantic_resolver_20261020 import (
    resolve as resolve_late_reviewed,
    check_fixture as check_later_conflict_fixture,
)

ROOT = Path.home() / "projects/shadps4"
ESDE = ROOT / "ESDE"
LOGS = ROOT / "logs"
WORK = ESDE / "fullstack-audit-20261019-165723"
SOURCE = WORK / "source"
BUILD = WORK / "build"
OLD_ELF = "48ae4ac2ec8f42e2155425521c8699359b466dd6459905948762de539865b5bc"
UPSTREAM = "d615b3887ba29025a59242e1a26f5b68bb176158"
UPSTREAM_REVIEWED = "f68923fe807f657348e6719ce658bcbd0a2b1c72"
OFFICIAL_REMOTE = "https://github.com/shadps4-emu/shadPS4.git"
GOW = "f909c3abb1ac1e7c8d87de833d3659f7d0b8fc87"
GOT = "e9f470b359845a456139a865aa1d7b3001f0dbfd"
GOT_LATE = "71faef4a8d76282a1931445198571082f0e3c70e"
# Chronological general correctness improvements after the last horse-visible
# baseline. No per-shader feedback counters, game IDs, or GDS debug probes.
# A commit is replayed at source-delta level with its exact first parent.
GENERAL_LATE_COMMITS = (
    "16ab0edbd70e",  # dynamic image descriptor bindings
    "2ba9963f228a",  # descriptor resource capacity
    "e6af55857bc0",  # bounded large table budget
    "be463d8d361a",  # strided T# real SSA indices
    "0dc9a1dff77d",  # guest record and offset provenance
    "5cf2cf619302",  # real Vulkan binding layout
    "f336b0dbfc20",  # complete strided material specialization
    "b29106a5c6b6",  # bind real strided guest images
    "f2169f2fb9a4",  # indexed gradient IR opcode
    "39d58e288e25",  # GPU gradient SPIR-V
    "760b73d3e641",  # paired sampler and texture discovery
    "0cecd34d1760",  # matching S# record provenance
    "20ec7bfbb7a1",  # same GPU selector for S# and T#
    "69e7892b1c72",  # sampler nonuniform descriptor capability
    "8b75cb119781",  # complete selected sampler array
    "9b4d6ebd6523",  # reject unsupported sampler/texture pair
    "dd00aa320c9e",  # preserve descriptor and SPIR-V slots
    "3edc91cd2175",  # preserve original GPU index
    "6c9cfc0c1f23",  # sampler array descriptor count
    "6ff828d3a06b",  # full S# arrays with real T# arrays
    "538fc65527d9",  # standard Vulkan sampled-image capability
    "69792c1c37bf",  # texture-cache UID residency
    "c7ac9ac753b7",  # bounded table cache
    "e8063a04b9f0",  # shader stage and layout key
    "d4041bfb8978",  # unchanged table reuse and lifetime
    "ea945f704902",  # refresh previously absent image
    "11b81a8698f6",  # fail closed on partial compute descriptors
    "c741c64707d3",  # invalidate incompatible shader cache
    "a3a1097bd56a",  # overflow-safe buffer read
    "92327b967857",  # validated descriptor flatbuffer range
    "f77e3c74f6bf",  # out-of-range V# fail closed
    "1cddfe5c715f",  # indexed GPU table read bounds
    "47eda1e41d37",  # live material table witness
    "5b7939573657",  # provenance for all guest-material V#
    "b913874ce5a9",  # live material specialization cache
    "c41c07769062",  # owned shader Info per permutation
    "deab626789e3",  # matching material resource binding
    "d27ec61bbad7",  # metadata-permutation cache deserialization
    "f2a88f76ed02",  # real GPU attribute T# selector
    "2c03de7f77b3",  # full 27K table from guest resource
    "645d6cf9586e",  # indirect buffer hazard
    "69c3c02d2aa5",  # indirect argument GPU read
    "c036336bf4b9",  # draw indirect correct access stage
    "baceadf44d0a",  # singleton LDS and Phi fold correction
    "c8121ad97140",  # packed table size changes invalidate specialization
    "aa775cd99651",  # bounded pointer-selected descriptors
    "1b1ca11bfea7",  # uniform samplers + explicit LOD
    "84117bbd1e35",  # generic GPU descriptor paths
    "e255fa8f608c",  # GPU-selected buffer loads and stores
    "71faef4a8d76",  # cached buffer-table bounds
    "989e0507db82",  # initial bounded full guest T# capability
    "7635be96513a",  # large sampled-image descriptor constraints
    "37a957d2a67f",  # GPU-indexed array capability safety
    "75c15f42ce4c",  # Vulkan physical sampled-image limits
    "5568da00029a",  # single allocation large descriptor pool
    "c6f721296e74",  # descriptor pool allocation API
    "6d97d6818550",  # descriptor pool reuse
    "f74a01cb704c",  # large descriptor resource count
    "6b6d462d1d1f",  # stable descriptor memory
    "29983ea0417a",  # single indexed descriptor allocation
    "9e30254c86b5",  # dynamic indexed descriptor writes
    "db7a92900988",  # cache generation 32-bit descriptor count
    "95c8e5bddb64",  # exact strided material T# source indices
    "476f8b83e3c7",  # overflow safe strided record matcher
    "15ec6ede7034",  # retain selected record source
    "02c22fd61253",  # full guest record table validation
    "a61c10ff0059",  # indexed gradient op IR prerequisites
    "e6b7acce13db",  # indexed gradient opcode prerequisites
    "2718fe1101bc",  # indexed gradient operation plumbing
    "5610f50a6239",  # indexed gradient preservation
    "ed84b34b91a3",  # gradient/material shader cache metadata
    "24565892d51c",  # nonuniform sampler array capability
    "1c57c6e225a4",  # sampler feature negotiation
    "131768ecb02a",  # sampler feature validation
    "ec6f257ed685",  # sampler descriptor slot preservation
    "ba7f618fe468",  # indexed sampler descriptor sizing
    "630f51ecf3c8",  # sampler descriptor layout
    "bfa0d918e33f",  # complete descriptor count bounds
    "069cedfea2ee",  # indexed sampler shader cache version
    "76c3225babcb",  # indexed sampler shader cache metadata
    "34a46b656f75",  # standard nonuniform sampler capability
    "cbbcc969156f",  # Vulkan sampled-image declaration
    "d72990fbd8fe",  # sampler array Vulkan validation
)
COMMON = "32021e41bc52f744c80d122e732e24e97496c98d"
REMOTE = "https://github.com/Chreece/shadPS4.git"
IMAGE = "shadps4-ngs2-builder:trixie-clang19-v1"

# These are generic shader resource operations, NOT title-specific
# compatibility switches. The GoT source pin precedes the known-regressing
# per-fragment feedback experiments.
GPU_SOURCE_PATHS = {
    "src/video_core/amdgpu/liverpool.cpp",
    "src/video_core/amdgpu/pm4_register_write.h",
    "src/video_core/amdgpu/pm4_cmds.h",
    "src/video_core/amdgpu/regs_depth.h",
    "src/video_core/amdgpu/pixel_format.h",
    "src/video_core/page_manager.cpp",
    "src/video_core/page_manager.h",
    "src/video_core/protection_range_coalescer.h",
}
VULKAN_FILES = {
    "vk_rasterizer.cpp", "vk_rasterizer.h",
    "vk_graphics_pipeline.cpp", "vk_graphics_pipeline.h",
    "vk_compute_pipeline.cpp", "vk_compute_pipeline.h",
    "vk_pipeline_cache.cpp", "vk_pipeline_cache.h",
    "vk_pipeline_common.cpp", "vk_pipeline_common.h",
    "vk_pipeline_serialization.cpp",
    "vk_resource_pool.cpp", "vk_resource_pool.h",
    "vk_instance.cpp", "vk_instance.h", "vk_runtime.cpp",
    "vk_presenter.cpp", "vk_presenter.h",
    "resource_audit_hash_filter.h", "vk_buffer_hazard_rules.h",
    "minmax_alpha_scale.h", "minmax_src_alpha_one.h",
}
UNSAFE_CPP_MARKERS = re.compile(
    r"(?:\bCUSA\d{5}\b|\bSHADPS4_(?:GOW|GOT|GHOST)_[A-Z0-9_]*\b|"
    r"\bSHADPS4_INDEXED_IMAGE_FEEDBACK[A-Z0-9_]*\b|"
    r"\bSHADPS4_(?:STRIDED_GUEST_TSHARP|TEXTURE_SLOT_PRESSURE)\b|"
    r"\"(?:shader_recompiler/indexed_image_feedback|video_core/renderer_vulkan/vk_got_[^\"]+)\"|"
    r"\bIndexedImageFeedback\b|"
    r"\b0x(?:7eaf40ad|5c4c2b6f|55d17c16|52b6e003|4950cf6)\b|"
    r"\bif\s*\([^;\n]*(?:pgm_hash|shader_hash|shaderHash)\s*(?:==|!=)\s*0x[0-9a-f]{6,8}\b)",
    re.I,
)
INCLUDES = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)

def run(*args, check=True, cwd=None, timeout=160):
    p = subprocess.run([str(x) for x in args], cwd=cwd,
                       capture_output=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f"Command failed ({p.returncode}): {args[0]} "
                           + p.stderr.decode("utf8", "replace")[-1800:])
    return p

def git(*args, check=True, timeout=160):
    return run("git", "-C", SOURCE, *args, check=check, timeout=timeout)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def sha_file(file):
    if not file.is_file() or file.is_symlink():
        return None
    with file.open("rb") as s:
        h = hashlib.sha256()
        for chunk in iter(lambda: s.read(1024*1024), b""):
            h.update(chunk)
        return h.hexdigest()

def git_data(commit, rel):
    p = git("show", f"{commit}:{rel}", check=False)
    return p.stdout if p.returncode == 0 else None

def eligible(rel):
    if not rel.startswith("src/") or not rel.endswith((".cpp",".h",".inc")):
        return False
    if rel.startswith("src/shader_recompiler/"):
        return not any(k in rel for k in ("diagnostics", "got_", "gow_"))
    if rel in GPU_SOURCE_PATHS:
        return True
    if rel.startswith("src/video_core/renderer_vulkan/"):
        return Path(rel).name in VULKAN_FILES
    if rel.startswith(("src/video_core/texture_cache/",
                       "src/video_core/buffer_cache/")):
        return True
    return False

def ensure_git_history(report):
    if not (SOURCE / ".git").exists() or not (BUILD / "build.ninja").is_file():
        raise RuntimeError("Pinned ES-DE source and build.ninja required")
    if git("rev-parse", "--show-toplevel").stdout.decode().strip() != str(SOURCE):
        raise RuntimeError("Different project checkout; cannot modify")
    if git("rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0:
        raise RuntimeError("Previous Git merge still unresolved")
    if git("ls-files", "-u").stdout:
        raise RuntimeError("Unmerged index entries; no integration attempt")
    for commit in (UPSTREAM, GOW):
        if git("merge-base", "--is-ancestor", commit, "HEAD",
               check=False).returncode:
            raise RuntimeError(f"Compiled source missing pinned ancestor {commit}")
    old = BUILD / "shadps4"
    if sha_file(old) != OLD_ELF:
        raise RuntimeError("Compiled source baseline ELF no longer matches Oct19 SHA")
    report["previous_binary_sha"] = sha_file(old)
    # Five newer upstream commits were reviewed on Oct 19: #5433 texture
    # subresources, #5429 singleton LDS, #5427 Phi folding, #5421 Voice HLE,
    # #5437 networking. Bring their REAL source diffs into the same candidate;
    # do not just change the stale old esde_unified.sh SHA.
    if git("cat-file", "-e", f"{UPSTREAM_REVIEWED}^{{commit}}",
           check=False).returncode:
        run("git", "-C", SOURCE, "-c", "http.version=HTTP/1.1",
            "fetch", "--no-tags", "--no-recurse-submodules",
            OFFICIAL_REMOTE, UPSTREAM_REVIEWED, timeout=240)
    if git("cat-file", "-e", f"{UPSTREAM_REVIEWED}^{{commit}}",
           check=False).returncode:
        raise RuntimeError("Reviewed upstream main commit unavailable")
    if git("merge-base", "--is-ancestor", UPSTREAM, UPSTREAM_REVIEWED,
           check=False).returncode:
        raise RuntimeError("New upstream review pin not descendant of source base")
    report["reviewed_official_upstream_head"] = UPSTREAM_REVIEWED
    for commit in (GOT, GOT_LATE, COMMON):
        if git("cat-file", "-e", f"{commit}^{{commit}}", check=False).returncode:
            run("git", "-C", SOURCE, "-c", "http.version=HTTP/1.1",
                "fetch", "--no-tags", "--no-recurse-submodules",
                "--depth=512", REMOTE, commit, timeout=240)
        if git("cat-file", "-e", f"{commit}^{{commit}}", check=False).returncode:
            raise RuntimeError("Pinned GoT/base Git history incomplete: " + commit)
    # A cached shallow branch tip is insufficient: 84 generic C++ fixes
    # span hundreds of commits. The replay must see their real first parents.
    missing_ancestry = [
        prefix for prefix in GENERAL_LATE_COMMITS
        if git("rev-parse", "--verify", prefix+"^{commit}", check=False).returncode
    ]
    if missing_ancestry:
        run("git", "-C", SOURCE, "-c", "http.version=HTTP/1.1",
            "fetch", "--no-tags", "--no-recurse-submodules", "--depth=512",
            REMOTE, GOT_LATE, timeout=300)
    if git("merge-base", "--is-ancestor", GOT, GOT_LATE,
           check=False).returncode:
        raise RuntimeError("Later general GPU source is not descendant of horse-visible baseline")
    if git("merge-base", "--is-ancestor", COMMON, GOT,
           check=False).returncode:
        raise RuntimeError("GoT source not descended from audited common base")
    if git("merge-base", "--is-ancestor", COMMON, "HEAD",
           check=False).returncode:
        raise RuntimeError("Current unified source has different ancestry")
    report["source_head"] = git("rev-parse", "HEAD").stdout.decode().strip()
    report["source_dirty_before"] = git(
        "status", "--porcelain", "--untracked-files=no"
    ).stdout.decode("utf8", "replace").splitlines()[:160]

def merged_bytes(current, parent, incoming, base, rel, temp):
    if incoming is None:
        return None, "REMOVAL_NOT_AUTOMATICALLY_SUPPORTED"
    if current is None:
        if parent is not None:
            return None, "LOCAL_SOURCE_MISSING_BUT_PRESENT_IN_BASE"
        return incoming, None
    if current == incoming or incoming == parent:
        return current, None
    if parent is None:
        return None, "INDEPENDENT_ADDED_HEADER_CONFLICT"
    if current == parent:
        return incoming, None
    ours = temp / "ours"
    ancestor = temp / "base"
    theirs = temp / "theirs"
    ours.write_bytes(current)
    ancestor.write_bytes(parent)
    theirs.write_bytes(incoming)
    p = run("git", "merge-file", "-p", "-L", "current-GoW-upstream",
            "-L", "common-ancestor", "-L", "generic-GoT",
            ours, ancestor, theirs, check=False)
    if p.returncode or b"<<<<<<<" in p.stdout:
        return p.stdout, "SOURCE_OVERLAP_NEEDS_SEMANTIC_REVIEW"
    return p.stdout, None

def guarded_delta(old, new, rel):
    # Only check CHANGES, not a pre-existing engine diagnostic from main.
    if new is None:
        return "UNEXPECTED_DELETION"
    if old is None:
        additions = new.decode("utf8", "replace").splitlines()
    else:
        delta = difflib.unified_diff(
            old.decode("utf8", "replace").splitlines(),
            new.decode("utf8", "replace").splitlines(), n=0)
        additions = [ln[1:] for ln in delta if ln.startswith("+")
                     and not ln.startswith("+++")]
    violations = [ln.strip()[:250] for ln in additions if UNSAFE_CPP_MARKERS.search(ln)]
    if violations:
        return "GAME_OR_SHADER_SPECIFIC_NEW_LOGIC: " + repr(violations[:5])
    return None

def replay_later_general_fixes(proposals, conflicts, plan_dir, report):
    """Replay only reviewed generic SOURCE diffs after the horse-visible branch.

    Each commit retains its actual first-parent base, never a fake replay
    against Oct 17. No diagnostic-only commit, title/shader selector or
    arbitrary Vulkan descriptor value is silently chosen as a resolution.
    """
    if conflicts:
        report["later_generic_commits"] = "WAITING_FOR_BASELINE_SEMANTIC_CONFLICTS"
        return
    history = git("rev-list", "--reverse", f"{GOT}..{GOT_LATE}").stdout.decode().splitlines()
    by_prefix = {}
    for prefix in GENERAL_LATE_COMMITS:
        candidates = [h for h in history if h.startswith(prefix)]
        if len(candidates) != 1:
            raise RuntimeError(f"Ambiguous or missing pinned general commit {prefix}: {candidates}")
        by_prefix[candidates[0]] = prefix
    report["later_generic_commits_expected"] = len(GENERAL_LATE_COMMITS)
    report["later_generic_commits_pinned"] = list(by_prefix)
    report["later_generic_commits_skipped_already_upstream"] = []
    report["later_generic_change_replayed"] = {}
    report["later_semantically_resolved"] = {}
    report["later_blocked_dependency_chain"] = {}
    blocked = {}
    reviewed = 0
    for sha_commit in history:
        if sha_commit not in by_prefix:
            continue
        if git("merge-base", "--is-ancestor", sha_commit, "HEAD",
               check=False).returncode == 0:
            report["later_generic_commits_skipped_already_upstream"].append(sha_commit)
            continue
        parents = git("rev-list", "--parents", "-n", "1", sha_commit).stdout.decode().split()
        if len(parents) != 2 or parents[0] != sha_commit:
            raise RuntimeError("Unexpected merge-commit dependency: " + sha_commit)
        parent = parents[1]
        delta = git("diff", "--name-only", parent, sha_commit,
                    "--", "src/shader_recompiler", "src/video_core").stdout
        paths = sorted(set(delta.decode().splitlines()))
        for idx,rel in enumerate(paths):
            if not eligible(rel):
                continue
            if rel in blocked:
                report["later_blocked_dependency_chain"].setdefault(
                    rel, []).append({
                        "blocked_by": blocked[rel],
                        "skipped_commit": sha_commit,
                    })
                continue
            local = SOURCE / rel
            if local.is_symlink() or (local.exists() and not local.is_file()):
                conflicts[sha_commit[:12]+":"+rel] = "UNSAFE_LOCAL_FILE_TYPE"
                continue
            old = proposals.get(rel, local.read_bytes() if local.is_file() else None)
            prev = git_data(parent,rel)
            incoming = git_data(sha_commit,rel)
            temp = plan_dir / ("late-"+sha_commit[:12]+"-"+str(idx))
            temp.mkdir(exist_ok=False)
            merged, error = merged_bytes(old,prev,incoming,parent,rel,temp)
            if error == "SOURCE_OVERLAP_NEEDS_SEMANTIC_REVIEW" and merged:
                reviewed_merge = resolve_late_reviewed(sha_commit[:12],rel,merged)
                if reviewed_merge is not None:
                    merged, error = reviewed_merge, None
                    report["later_semantically_resolved"][
                        sha_commit[:12]+":"+rel
                    ] = "AUDITED_SOURCE_AND_SEMANTIC_POSTIMAGE_VERIFIED"
            if error:
                conflicts[sha_commit[:12]+":"+rel] = error
                blocked[rel] = sha_commit
                if merged and b"<<<<<<<" in merged:
                    target = plan_dir/"conflicts"/"later"/sha_commit[:12]/rel
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(merged)
                continue
            policy = guarded_delta(old,merged,rel)
            if policy:
                conflicts[sha_commit[:12]+":"+rel] = policy
                blocked[rel] = sha_commit
                continue
            if old != merged:
                proposals[rel] = merged
                report["later_generic_change_replayed"].setdefault(
                    sha_commit,[]).append(rel)
        reviewed += 1
    report["later_generic_commits_reviewed"]=reviewed
    report["later_first_blocking_commits"]=blocked
    report["later_unapplied_dependent_edits"]=sum(
        len(v) for v in report["later_blocked_dependency_chain"].values()
    )
    report["later_generic_source_edits"]=sum(
        len(x) for x in report["later_generic_change_replayed"].values())

def reviewed_upstream_changes(plan_dir, report):
    """Stage all five reviewed upstream commits via real C++ three-way merges.

    This integrates newer code without resetting the user's GoW/ESDE tree.
    No file is actually written to SOURCE before all conflicts are evaluated.
    """
    delta = git("diff", "--name-only", UPSTREAM, UPSTREAM_REVIEWED).stdout
    changed = sorted(set(delta.decode("utf8").splitlines()))
    report["official_upstream_reviewed_changes"] = changed
    if not changed or len(changed) > 40:
        raise RuntimeError("Reviewed upstream five-commit diff has changed scope")
    proposals = {}
    conflicts = {}
    for idx,rel in enumerate(changed):
        file = SOURCE / rel
        if file.is_symlink() or (file.exists() and not file.is_file()):
            conflicts["upstream:"+rel] = "UNSAFE_LOCAL_PATH"
            continue
        current = file.read_bytes() if file.is_file() else None
        ancestor = git_data(UPSTREAM,rel)
        upstream = git_data(UPSTREAM_REVIEWED,rel)
        directory = plan_dir/("upstream-"+str(idx))
        directory.mkdir()
        candidate,error = merged_bytes(
            current,ancestor,upstream,UPSTREAM,rel,directory)
        if error:
            conflicts["upstream:"+rel] = error
            if candidate and b"<<<<<<<" in candidate:
                dest = plan_dir/"conflicts"/"official-upstream"/rel
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(candidate)
            continue
        violation = guarded_delta(current,candidate,rel)
        if violation:
            conflicts["upstream:"+rel] = violation
            continue
        if current != candidate:
            proposals[rel] = candidate
    report["official_upstream_staged_files"] = sorted(proposals)
    report["official_upstream_conflicts"] = sorted(conflicts)
    return proposals,conflicts


def collect_change_plan(plan_dir, report):
    changes = git("diff", "--name-only", COMMON, GOT,
                  "--", "src/shader_recompiler", "src/video_core").stdout
    options = sorted({x.decode("utf8") for x in changes.split(b"\0") if x}) if b"\0" in changes else sorted(
        set(changes.decode("utf8").splitlines()))
    selected = [p for p in options if eligible(p)]
    if not selected or len(selected) > 180:
        raise RuntimeError("Unexpected generic shader/GPU source inventory size")
    report["changed_paths_from_GOT"] = len(options)
    report["selected_paths"] = selected
    report["excluded_paths"] = [p for p in options if p not in selected]
    # Compose the actually reviewed current upstream delta FIRST, then
    # integrate the original Ghost source and all later general corrections.
    proposals,conflicts = reviewed_upstream_changes(plan_dir,report)
    for i,rel in enumerate(selected):
        target = SOURCE / rel
        if target.is_symlink() or (target.exists() and not target.is_file()):
            conflicts[rel] = "SUSPICIOUS_LOCAL_SOURCE_FILE_TYPE"
            continue
        old = proposals.get(rel, target.read_bytes() if target.is_file() else None)
        ancestor = git_data(COMMON, rel)
        newer = git_data(GOT, rel)
        tmp = plan_dir / ("merge" + str(i))
        tmp.mkdir()
        candidate, error = merged_bytes(old, ancestor, newer, COMMON, rel, tmp)
        if error == "SOURCE_OVERLAP_NEEDS_SEMANTIC_REVIEW" and candidate is not None:
            # Exact-Git-preview, 22:21 archive-proven general C++ port.
            reviewed = resolve_reviewed(rel, candidate)
            if reviewed is not None:
                candidate, error = reviewed, None
                report.setdefault("semantically_resolved",{})[rel] = (
                    "REVIEWED_CONFLICT_SHA_AND_ENGINE_INVARIANTS_PASS"
                )
        elif error == "INDEPENDENT_ADDED_HEADER_CONFLICT":
            if old is not None and newer is not None:
                reviewed = preserve_verified_srt_header(rel, old, newer)
                if reviewed is not None:
                    candidate, error = reviewed, None
                    report.setdefault("semantically_resolved",{})[rel] = (
                        "KEEP_PREVIOUS_NATIVE_COMPILED_GOW_SRT_HEADER"
                    )
        if error:
            conflicts[rel] = error
            if candidate and b"<<<<<<<" in candidate:
                out = plan_dir / "conflicts" / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(candidate)
            continue
        if candidate is None:
            conflicts[rel] = "NO_SOURCE_CANDIDATE"
            continue
        violation = guarded_delta(old, candidate, rel)
        if violation:
            conflicts[rel] = violation
            continue
        if old == candidate:
            continue
        proposals[rel] = candidate

    # A second, separate, commit-by-commit replay covers later *general*
    # table bounds, strided images/samplers, indexed buffers, cache lifetimes,
    # shader Phi/LDS, and indirect GPU hazards. Pure test/diagnostic commits
    # are deliberately not replayed.
    replay_later_general_fixes(proposals,conflicts,plan_dir,report)

    # Inspect the COMPLETE changed subsystem for missing project-local headers.
    # Sources outside the vetted subsystem must not be manufactured.
    errors = {}
    queued = list(proposals)
    seen = set()
    count = 0
    while queued:
        rel = queued.pop()
        if rel in seen:
            continue
        seen.add(rel)
        count += 1
        if count > 1600:
            errors[rel] = "UNBOUNDED_INCLUDE_DEPENDENCY_GRAPH"
            break
        path = SOURCE / rel
        current = proposals.get(rel)
        if current is None and path.is_file() and not path.is_symlink():
            current = path.read_bytes()
        if current is None:
            continue
        for include in INCLUDES.findall(current.decode("utf8","replace")):
            if not include.startswith(("shader_recompiler/", "video_core/")):
                continue
            dep = "src/" + include
            location = SOURCE / dep
            if dep not in proposals and not location.is_file():
                # New general headers may be introduced in the later vetted
                # source chain rather than in the Oct17 horse-visible tree.
                from_got = git_data(GOT, dep)
                source_ref = GOT
                if from_got is None:
                    from_got = git_data(GOT_LATE, dep)
                    source_ref = GOT_LATE
                if from_got is None or not eligible(dep):
                    errors[dep] = f"UNVERIFIED_TRANSITIVE_SOURCE_DEPENDENCY from {rel}"
                    continue
                prior = git_data(COMMON, dep)
                if prior is not None:
                    errors[dep] = "MISSING_EXISTING_ANCESTOR_HEADER"
                    continue
                violation = guarded_delta(None,from_got,dep)
                if violation:
                    errors[dep] = "UNSAFE_TRANSITIVE_HEADER: " + violation
                    continue
                proposals[dep] = from_got
                report.setdefault("transitive_source_headers",{})[dep] = source_ref
            if dep not in seen:
                queued.append(dep)
    conflicts.update(errors)
    report["dependency_files_walked"] = count
    report["proposals_count"] = len(proposals)
    report["all_conflicts"] = conflicts
    report["selected_compiler_paths"] = len(selected)
    report["merge_strategy"] = "GIT_3WAY_PINNED_REVIEWED_COMBINATIONS_FAIL_CLOSED"
    report["source_merges_reviewed"] = len(report.get("semantically_resolved", {}))
    report["source_merged_no_title_switches"] = not any(
        str(reason).startswith("GAME_OR_SHADER_SPECIFIC") for reason in conflicts.values())
    for rel,blob in proposals.items():
        dest = plan_dir / "merged" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
    return proposals

def existing_gow_contract():
    def read(rel):
        p = SOURCE / rel
        return p.read_text(errors="replace") if p.is_file() else ""
    gpu = read("src/video_core/renderer_vulkan/vk_rasterizer.cpp")
    gpu_h = read("src/video_core/renderer_vulkan/vk_rasterizer.h")
    mem = read("src/video_core/page_manager.cpp")
    mem_h = read("src/video_core/page_manager.h")
    gnm = read("src/core/libraries/gnmdriver/gnmdriver.cpp")
    a = gnm.find("std::unique_ptr<AmdGpu::Liverpool> liverpool;")
    b = gnm.find("std::unique_ptr<Vulkan::Presenter> presenter;")
    return {
        "deferred_gpu_fault_callback":
            "Rasterizer::InvalidateMemoryOnGpuThread(" in gpu and
            "InvalidateMemoryOnGpuThread(" in gpu_h,
        "drain_pending_gpu_invalidations":
            "page_manager.DrainPendingInvalidations();" in gpu and
            "void DrainPendingInvalidations();" in mem_h,
        "kernel_uffd_pending_queue": "pending_gpu_invalidations" in mem,
        "gpu_lifetime_destruction_order": a >= 0 and b >= 0 and a < b,
        "gpu_write_extent_from_real_execution":
            "ComputeProvenWriteSize(" in gpu,
    }

def apply_changes(proposals, report, plan_dir):
    before = plan_dir / "before"
    created = []
    try:
        for rel,data in sorted(proposals.items()):
            dest = SOURCE / rel
            if dest.is_symlink() or (dest.exists() and not dest.is_file()):
                raise RuntimeError("Source changed before apply: "+rel)
            previous = dest.read_bytes() if dest.is_file() else None
            if previous is not None:
                snapshot = before / rel
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                snapshot.write_bytes(previous)
            else:
                created.append(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if sha_file(dest) != sha(data):
                raise RuntimeError("Integrated source did not match proposed bytes")
        report["source_applied"] = sorted(proposals)
        report["newly_added"] = created
        report["gow_contracts_after"] = existing_gow_contract()
        report["missing_required_gow_contracts"] = [
            k for k,v in report["gow_contracts_after"].items() if not v
        ]
        # GoT source must never silently remove a previously working GoW
        # GPU/UFFD ownership or lifetime guarantee.
        previous_contracts = report.get("gow_contracts_before", {})
        lost = [key for key, was_ok in previous_contracts.items()
                if was_ok and not report["gow_contracts_after"].get(key, False)]
        report["regressed_proven_gow_contracts"] = lost
        if lost:
            raise RuntimeError("GoW GPU/UFFD contract regression: " + ",".join(lost))
        # Stage no actual Git commit; preserve the original history for review.
    except Exception:
        for rel in proposals:
            old = before / rel
            dst = SOURCE / rel
            if old.is_file():
                dst.write_bytes(old.read_bytes())
            elif rel in created and dst.is_file():
                dst.unlink()
        raise

def restore_failed_source_trial(report, plan_dir):
    """Revert only known staged source files after an unsuccessful validation.

    The complete candidate source proposals remain separately in
    general-engine-merge-<timestamp>/merged/, and failed compiler logs remain.
    Do not reset Git, user files, game saves or any installed binary.
    """
    paths=report.get("source_applied",[])
    created=set(report.get("newly_added",[]))
    restored=[]
    for rel in paths:
        dst=SOURCE/rel
        prior=plan_dir/"before"/rel
        if prior.is_file() and not prior.is_symlink():
            data=prior.read_bytes()
            if not dst.is_file() or dst.is_symlink():
                raise RuntimeError("FAILED_STAGE_RESTORE_TARGET_CHANGED:"+rel)
            dst.write_bytes(data)
            os.utime(dst,None)
            if sha_file(dst)!=sha(data):
                raise RuntimeError("FAILED_STAGE_RESTORE_VERIFICATION_FAILED:"+rel)
            restored.append(rel)
        elif rel in created:
            if dst.is_file() and not dst.is_symlink():
                dst.unlink()
                restored.append(rel)
        else:
            raise RuntimeError("FAILED_STAGE_MISSING_ORIGINAL_SNAPSHOT:"+rel)
    report["source_restored_after_failure"]=restored
    if len(restored)!=len(paths):
        raise RuntimeError("FAILED_STAGE_SOURCE_RESTORE_INCOMPLETE")

def focused_native_regressions(report, plan_dir):
    """Compile actual source-aware C++ tests before the full emulator relink."""
    cpp=shutil.which("g++") or shutil.which("clang++") or shutil.which("c++")
    if not cpp:
        raise RuntimeError("Native C++ compiler unavailable for regression tests")
    tests = (
        "tests/pm4_shader_register_window_test.cpp",
        "tests/shader_recompiler/test_guest_tsharp_array.cpp",
        "tests/shader_recompiler/test_indexed_image_selector.cpp",
        "tests/shader_recompiler/test_strided_material_specialization.cpp",
        "tests/shader_recompiler/test_srt_proven_identity.cpp",
        "tests/shader_recompiler/test_srt_dependency_schedule.cpp",
        "tests/shader_recompiler/test_srt_host_barrier.cpp",
        "tests/shader_recompiler/test_srt_uniformity.cpp",
    )
    dirs = [SOURCE/"src",SOURCE/"externals/magic_enum/include",
            SOURCE/"externals/fmt/include",BUILD/"_deps/fmt-src/include"]
    opts=[f"-I{d}" for d in dirs if d.is_dir()]
    report["focused_native_regressions"]={}
    root=plan_dir/"host-tests"
    root.mkdir(exist_ok=True)
    for i,rel in enumerate(tests):
        blob=git_data(GOT_LATE,rel)
        if blob is None:
            raise RuntimeError("Pinned native regression source missing: "+rel)
        source=root/("test-"+str(i)+".cpp")
        exe=root/("test-"+str(i))
        source.write_bytes(blob)
        argv=[cpp,"-std=c++23","-O2","-Wall","-Wextra","-Werror",
              *opts,source,"-o",exe]
        built=run(*argv,check=False,timeout=180)
        (root/("test-"+str(i)+".compile.log")).write_bytes(
            built.stdout+built.stderr)
        if built.returncode:
            report["focused_native_regressions"][rel]="COMPILE_FAILED"
            raise RuntimeError("C++ regression compile failed: "+rel)
        result=run(exe,check=False,timeout=90)
        (root/("test-"+str(i)+".run.log")).write_bytes(
            result.stdout+result.stderr)
        if result.returncode:
            report["focused_native_regressions"][rel]="BEHAVIOR_FAILED"
            raise RuntimeError("C++ regression behavior failed: "+rel)
        report["focused_native_regressions"][rel]="PASS"
    print("GENERIC_SHADER_GPU_NATIVE_REGRESSIONS=PASS count="+str(len(tests)))

def build(report, plan_dir):
    # An isolated CMake build only; never install it or launch any game.
    if run("pgrep","-u",str(os.getuid()),"-x","shadps4",
           check=False).returncode == 0:
        raise RuntimeError("Existing emulator running; no concurrent build")
    if shutil.disk_usage(ESDE).free < 5*1024**3:
        raise RuntimeError("Need 5 GiB available disk before relink")
    if run("docker","image","inspect",IMAGE,check=False).returncode:
        raise RuntimeError("Pinned existing Docker Clang19 image unavailable")
    previous = BUILD / "shadps4"
    backup = plan_dir / "previous-compiled-candidate"
    shutil.copy2(previous, backup)
    report["previous_candidate_backup_sha256"] = sha_file(backup)
    log = plan_dir / "build.log"
    common = ["docker","run","--rm","--user",f"{os.getuid()}:{os.getgid()}",
              "--mount",f"type=bind,src={ESDE},dst={ESDE}",
              "--workdir",str(WORK),"--env","HOME="+str(WORK),IMAGE]
    cmd = common+["cmake","--build",str(BUILD),"--target","shadps4",
                  "--parallel","4"]
    try:
        with log.open("wb") as out:
            proc=subprocess.run(cmd,stdout=out,stderr=subprocess.STDOUT,cwd=WORK)
        report["build_exit"]=proc.returncode
        if proc.returncode:
            raise RuntimeError("Isolated source candidate did not compile/link")
        compiled=BUILD/"shadps4"
        if not compiled.is_file() or compiled.is_symlink():
            raise RuntimeError("No regular emulator binary after link")
        with compiled.open("rb") as file:
            if file.read(5)!=b"\x7fELF\x02":
                raise RuntimeError("Native result is not x86-64 ELF")
        ldd = run("ldd",compiled,check=False)
        if ldd.returncode or b"not found" in ldd.stdout:
            raise RuntimeError("Missing ELF dependency; candidate not accepted")
        smoke=run("timeout","30",compiled,"--help",check=False,cwd=WORK)
        if smoke.returncode:
            raise RuntimeError("CLI smoke failed: "+smoke.stderr.decode(errors="replace")[-1200:])
        candidate=plan_dir/"shadps4-general-candidate"
        shutil.copy2(compiled,candidate)
        report["candidate"]={"path":str(candidate),
                             "sha256":sha_file(candidate),
                             "state":"LINKED_AND_CLI_CHECKED_NOT_GAMEPLAY_VALIDATED"}
        print("ISOLATED_GENERAL_CANDIDATE="+str(candidate))
        print("ISOLATED_GENERAL_SHA256="+report["candidate"]["sha256"])
    except Exception:
        # Even if native link succeeded but ELF/ldd/smoke later rejected it,
        # the previously compiled binary remains the exact reusable baseline.
        shutil.copy2(backup, previous)
        report["previous_candidate_restored_sha256"]=sha_file(previous)
        if report["previous_candidate_restored_sha256"] != report["previous_candidate_backup_sha256"]:
            raise RuntimeError("NATIVE_BINARY_ROLLBACK_SHA_MISMATCH")
        raise


def self_test():
    """Native three-way source contract: retain nonoverlap, stop overlaps/hacks."""
    ESDE.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".general-engine-selftest-",dir=ESDE) as d:
        p=Path(d)
        base=b"int first=0;\nint keep=0;\nint second=0;\n"
        ours=b"int first=1;\nint keep=0;\nint second=0;\n"
        got=b"int first=0;\nint keep=0;\nint second=1;\n"
        merged,error=merged_bytes(ours,base,got,COMMON,"test.cpp",p)
        assert error is None and merged is not None
        assert b"int first=1;" in merged and b"int second=1;" in merged
    with tempfile.TemporaryDirectory(prefix=".general-engine-selftest-",dir=ESDE) as d:
        p=Path(d)
        first=b"int first=0;\n"
        ours=b"int first=1;\n"
        got=b"int first=2;\n"
        merged,error=merged_bytes(ours,first,got,COMMON,"test.cpp",p)
        assert error=="SOURCE_OVERLAP_NEEDS_SEMANTIC_REVIEW"
        assert merged is not None and b"<<<<<<<" in merged
    assert guarded_delta(b"",b"if (id == CUSA11456) return 1;\n","x.cpp")
    assert guarded_delta(b"",b"if (hash == 0x7eaf40ad) return 1;\n","x.cpp")
    assert guarded_delta(b"",b"if (SHADPS4_GOW_SPECIAL) return 1;\n","x.cpp")
    assert guarded_delta(b"",b"if (shader_hash == 0x12345678) return 1;\n","x.cpp")
    assert guarded_delta(b"",b"IndexedImageFeedback counters;\n","x.cpp")
    assert guarded_delta(b"",b"auto v = RealBufferDescriptor(index);\n","x.cpp") is None
    assert eligible("src/shader_recompiler/ir/passes/resource_discover_pass.cpp")
    assert eligible("src/video_core/renderer_vulkan/vk_rasterizer.cpp")
    assert eligible("src/video_core/renderer_vulkan/resource_audit_hash_filter.h")
    assert not eligible("src/video_core/renderer_vulkan/vk_got_scene_special.h")
    fixture = LOGS / "esde-general-engine-merge-20261019-222123.tar.gz"
    if fixture.is_file() and not fixture.is_symlink():
        proofs = check_archived_conflict_evidence(fixture)
        print("NINE_UPLOADED_CPP_SEMANTIC_MERGES=PASS count="+str(len(proofs)))
    else:
        print("NINE_UPLOADED_CPP_SEMANTIC_MERGES=ARCHIVE_NOT_ON_HOST")
    later_fixture = LOGS / "esde-general-engine-merge-20261020-054547.tar.gz"
    if later_fixture.is_file() and not later_fixture.is_symlink():
        proofs = check_later_conflict_fixture(later_fixture)
        print("LATER_REVIEWED_SEMANTIC_PREVIEWS=PASS count="+str(proofs))
    else:
        print("LATER_REVIEWED_SEMANTIC_PREVIEWS=ARCHIVE_NOT_ON_HOST")
    print("GENERAL_ENGINE_MERGE_SELFTEST=PASS cases=12")
    return 0

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--build",action="store_true",
                   help="Apply clean general source changes and compile isolated candidate")
    p.add_argument("--self-test",action="store_true",
                   help="Run offline native three-way merger and policy regression tests")
    args=p.parse_args()
    if args.self_test:
        return self_test()
    stamp=dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS.mkdir(parents=True,exist_ok=True)
    workdir=ESDE/("general-engine-merge-"+stamp)
    workdir.mkdir(parents=True)
    lock=ESDE/".general-engine-merge.lock"
    report={"tool":"ESDE_GENERAL_ENGINE_SOURCE_MERGE",
            "date":stamp,"mode":"BUILD" if args.build else "PREFLIGHT",
            "source_root":str(SOURCE),"installed_esde_changed":False,
            "protected_gow_got_sources_changed":False,
            "no_game_or_shader_specific_runtime_changes":True,
            "source_pin_official_upstream_20261019":UPSTREAM_REVIEWED,
            "source_pin_ghost_world_visible":GOT,
            "source_pin_ghost_later_general_source":GOT_LATE,
            "source_pin_gow_generic":GOW,
            "common_ancestor":COMMON}
    rc=0
    try:
        with lock.open("w") as lockfile:
            fcntl.flock(lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
            ensure_git_history(report)
            report["gow_contracts_before"]=existing_gow_contract()
            proposals=collect_change_plan(workdir,report)
            if report["all_conflicts"]:
                report["status"]="SOURCE_CONFLICTS_NOT_APPLIED"
                rc=2
            elif args.build:
                # Don't mutate even this disposable worktree while ES-DE
                # currently has a PS4 game using the host/GPU.
                if run("pgrep","-u",str(os.getuid()),"-x","shadps4",
                       check=False).returncode == 0:
                    raise RuntimeError("ACTIVE_EMULATOR_SOURCE_STAGE_REFUSED")
                apply_changes(proposals,report,workdir)
                report["status"]="SOURCE_APPLIED_NOT_YET_RUNTIME_TESTED"
                try:
                    focused_native_regressions(report,workdir)
                    build(report,workdir)
                except Exception:
                    restore_failed_source_trial(report,workdir)
                    raise
                if report.get("missing_required_gow_contracts"):
                    report["status"]="COMPILED_UNVERIFIED_GOW_GRAPHICS_CONTRACTS_AND_GAMEPLAY"
                else:
                    report["status"]="CANDIDATE_NATIVE_BUILD_PASS_GAMEPLAY_PENDING"
            else:
                report["status"]="SOURCE_PLAN_CLEAN_NOT_APPLIED"
    except Exception as exc:
        rc=2
        report["status"]="STOPPED_BEFORE_PRODUCTION_INSTALL"
        report["error"]=str(exc)
    (workdir/"report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n")
    archive=LOGS/("esde-general-engine-merge-"+stamp+".tar.gz")
    with tarfile.open(archive,"w:gz") as tf:
        for rel in ("report.json","build.log"):
            item=workdir/rel
            if item.is_file():
                tf.add(item,arcname=rel)
        conflicts=workdir/"conflicts"
        if conflicts.is_dir():
            tf.add(conflicts,arcname="conflicts")
        host=workdir/"host-tests"
        if host.is_dir():
            for item in host.glob("*.log"):
                if item.is_file() and item.stat().st_size < 2_000_000:
                    tf.add(item,arcname="host-tests/"+item.name)
        # A bounded patch captures the exact new generic source work, so a
        # compiler conflict can be reconciled without a speculative reset.
        if report.get("source_applied"):
            diff=git("diff","--","src/shader_recompiler","src/video_core",
                     check=False)
            if diff.returncode==0 and len(diff.stdout)<=8_000_000:
                from io import BytesIO
                import tarfile as _tar
                info=_tar.TarInfo("applied-source-diff.patch")
                info.size=len(diff.stdout)
                tf.addfile(info,BytesIO(diff.stdout))
    print("ENGINE_INTEGRATION_STATUS="+report["status"])
    print("GHOST_SHADER_GPU_FILES_SELECTED="+str(report.get("selected_compiler_paths",0)))
    print("SOURCE_CONFLICTS="+str(len(report.get("all_conflicts",{}))))
    print("PROVEN_GOW_GPU_CONTRACTS="+json.dumps(report.get("gow_contracts_before",{})))
    if report.get("error"):
        print("ERROR="+report["error"])
    print("UPLOAD_THIS="+str(archive))
    print("INSTALLED_ESDE_GAMES_SUNSHINE_SSH_UNCHANGED=YES")
    return rc

if __name__=="__main__":
    sys.exit(main())
