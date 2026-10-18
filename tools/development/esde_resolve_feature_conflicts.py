#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Resolve only the exact startup-GUI and Vulkan-16 feature conflicts, not other merges.

Stage 2 is the combined upstream+curated fork; stage 3 is the previously audited
small feature branch. Anchor and parent guards reject unfamiliar source layouts.
"""
import subprocess
import sys
from pathlib import Path

UPSTREAM = "cd3cc8022aef5be7766443f694e79d270544eaf4"
FORK = "aa5b281c0016d64844e784566ef9dd092655ba8b"
LOADING = "0b7ff78483e831db2d424b2351f5d931a56a1555"
VULKAN = "047cc7cf992f8cf60fdc4f0adfdcfcfb27bbed7d"
GUI_PATH = "CMakeLists.txt"
VK_PATH = "src/video_core/renderer_vulkan/vk_instance.cpp"
GUI_BLOB = "5ceed43c64d327f2f84bef6e4396965427f88ff2"
VK_BLOB = "d86822cec9e341f683b100fc8c5d17f65dfd5750"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def assert_once(s, key, label):
    n = s.count(key)
    if n != 1:
        raise RuntimeError(f"{label}: expected 1 occurrence, found {n}")


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("loading", "vulkan"):
        raise RuntimeError("Usage: esde_resolve_feature_conflicts.py loading|vulkan <src>")
    feature = sys.argv[1]
    repo = Path(sys.argv[2]).resolve(strict=True)
    head = git(repo, "rev-parse", "HEAD")
    parents = git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(parents) != 3 or parents[1:] != [UPSTREAM, FORK]:
        # Vulkan follows the completed GUI merge, which must be a descendant
        # of exact upstream + fork; don't assume the temporary merged SHA.
        if feature == "loading":
            raise RuntimeError(f"Unexpected pre-loading merge parents: {parents}")
        if not git(repo, "merge-base", "--is-ancestor", FORK, "HEAD") == "":
            raise RuntimeError("Curated fork must be an ancestor of Vulkan integration")
        if not git(repo, "merge-base", "--is-ancestor", LOADING, "HEAD") == "":
            raise RuntimeError("Loading GUI must be an ancestor of Vulkan integration")
    merge_head = git(repo, "rev-parse", "MERGE_HEAD")
    expected = LOADING if feature == "loading" else VULKAN
    if merge_head != expected:
        raise RuntimeError(f"Unrecognized feature MERGE_HEAD: {merge_head}")
    path, blob = (GUI_PATH, GUI_BLOB) if feature == "loading" else (VK_PATH, VK_BLOB)
    conflicted = set(filter(None, git(repo, "diff", "--name-only", "--diff-filter=U").splitlines()))
    if conflicted != {path}:
        raise RuntimeError(f"Unexpected conflicted files: {sorted(conflicted)}")

    if git(repo, "rev-parse", f":3:{path}") != blob:
        raise RuntimeError("Changed feature source blob: refuse incompatible port")
    ours = subprocess.check_output(["git", "-C", str(repo), "show", f":2:{path}"], text=True)
    theirs = subprocess.check_output(["git", "-C", str(repo), "show", f":3:{path}"], text=True)

    if feature == "loading":
        if "src/core/startup_progress.h" not in theirs or "src/imgui/startup_loading.cpp" not in theirs:
            raise RuntimeError("Feature source does not register startup GUI")
        old_core = "set(CORE src/core/aerolib/stubs.cpp"
        old_imgui = "          src/imgui/imgui_texture.h\n"
        assert_once(ours, old_core, "CORE registration")
        assert_once(ours, old_imgui, "IMGUI registration")
        if "src/core/startup_progress.h" in ours or "src/imgui/startup_loading.cpp" in ours:
            raise RuntimeError("Loading features were already registered in stage2")
        if "set(NET_CORE" not in ours:
            raise RuntimeError("New upstream network sources missing from stage2 CMake")
        result = ours.replace(old_core, "set(CORE src/core/startup_progress.h\n         src/core/aerolib/stubs.cpp", 1)
        result = result.replace(old_imgui, old_imgui +
                                "          src/imgui/startup_loading.cpp\n"
                                "          src/imgui/startup_loading.h\n", 1)
    else:
        old = "            .storageBuffer16BitAccess = vk11_features.storageBuffer16BitAccess,\n"
        addition = ("            .uniformAndStorageBuffer16BitAccess =\n"
                    "                vk11_features.uniformAndStorageBuffer16BitAccess,\n")
        assert_once(ours, old, "Vulkan11 device features")
        if "uniformAndStorageBuffer16BitAccess" in ours:
            raise RuntimeError("16-bit capability already present in stage2")
        if addition not in theirs:
            raise RuntimeError("Vulkan feature not found in source branch")
        if "GetSparseQueue" not in ours:
            raise RuntimeError("Curated Vulkan sparse-queue support missing")
        result = ours.replace(old, old + addition, 1)

    if "<<<<<<<" in result or "=======" in result or ">>>>>>>" in result:
        raise RuntimeError("Merge markers survived stage2 reconstruction")
    (repo / path).write_text(result, encoding="utf-8", newline="")
    subprocess.run(["git", "-C", str(repo), "add", "--", path], check=True)
    subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--check"], check=True)
    if git(repo, "diff", "--name-only", "--diff-filter=U"):
        raise RuntimeError("Unresolved files remain")
    subprocess.run(["git", "-C", str(repo), "commit", "--no-edit"], check=True)
    after = git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(after) != 3 or after[1:] != [head, expected]:
        raise RuntimeError(f"Wrong feature-merge parentage after commit: {after}")
    print(f"FEATURE_MERGE_RESOLVED=PASS feature={feature} path={path} new_sha={after[0]}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"FEATURE_MERGE_RESOLVED=FAIL {exc}", file=sys.stderr)
        sys.exit(1)
