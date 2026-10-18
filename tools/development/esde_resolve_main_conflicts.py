#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Resolve the FOUR exact Oct 18 fork-main/upstream conflicts, preserving both proven paths.

Fails closed on changed git refs, unexpected conflict paths, stage blobs or source anchors.
This is deliberately NOT a general-purpose 'ours/theirs' conflict auto-resolver.
"""
import subprocess
import sys
from pathlib import Path

UPSTREAM_SHA = "cd3cc8022aef5be7766443f694e79d270544eaf4"
FORK_MAIN_SHA = "aa5b281c0016d64844e784566ef9dd092655ba8b"

LAYER = "src/core/devtools/layer.cpp"
KERNEL = "src/core/libraries/kernel/kernel.cpp"
BUFFER = "src/video_core/buffer_cache/buffer_cache.cpp"
TEXTURE = "src/video_core/texture_cache/texture_cache.cpp"

# Stage 2 = upstream current tree, stage 3 = audited fork/main.
BLOBS = {
    LAYER: ("da64436f7e395a2877953d8785922478e23198a7",
            "f5ab722749ab63c3d5fa5fb13ab11971bffc6355"),
    KERNEL: ("735f7d70a1c6f07b975103dd552810f6863b9c5e",
             "16bf4961e61f24463b8e2ed28ecf6aa511d32beb"),
    BUFFER: ("94116ce380cd6ec313cee6f73f0961b6dc8ba699",
             "a0b239b8689df9a31e8294e262d7dea4038c3638"),
    TEXTURE: ("e36c4f253a4cd5f84657edb146ae2490b6b92af3",
              "4d4f92b37859fa98b1018162ab2860ca827e7e70"),
}


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def unique_replace(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def between(text: str, begin: str, end: str, label: str) -> str:
    if text.count(begin) != 1:
        raise RuntimeError(f"{label}: begin anchor not unique")
    at = text.index(begin)
    after = text.find(end, at + len(begin))
    if after < 0:
        raise RuntimeError(f"{label}: end anchor not found")
    return text[at:after]


def resolve_layer(upstream: str, fork: str) -> str:
    # Controller/Guide and consumed A/B quit events are fork-confirmed behavior.
    # Upstream modified only FPS placement in this conflicted file.
    result = unique_replace(fork,
                            "static float fps_anchor_width = FLT_MAX;",
                            "static bool fps_pinned = false;", "layer FPS state")
    result = unique_replace(
        result,
        "    show_simple_fps = EmulatorSettings.IsShowFpsCounter();\n\n",
        "    show_simple_fps = EmulatorSettings.IsShowFpsCounter();\n"
        "    visibility_toggled = show_simple_fps;\n\n", "layer FPS initial state")
    old = between(fork,
                  "            if (const float width = GetIO().DisplaySize.x; "
                  "width != fps_anchor_width) {",
                  "            if (BeginPopupContextWindow()) {", "fork FPS window")
    new = between(upstream,
                  "            if (visibility_toggled) {\n"
                  "                fps_pinned = true;",
                  "            if (BeginPopupContextWindow()) {", "upstream FPS window")
    result = unique_replace(result, old, new, "layer FPS window reconciliation")
    for symbol in ("ProcessQuitEvent", "quit_dialog.IsVisible()",
                   "SDL_EVENT_QUIT", "fps_pinned"):
        if symbol not in result:
            raise RuntimeError(f"layer: missing required {symbol}")
    if "fps_anchor_width" in result or "show_quit_window" in result:
        raise RuntimeError("layer: obsolete quit/FPS logic survived")
    return result


def resolve_kernel(upstream: str, fork: str) -> str:
    # Retain new upstream Net service/FD integration, OpenPsId and stack diagnostics.
    # Add the fork's tested sanitizer/atexit/errno support and regmgr trace.
    result = unique_replace(upstream, "#include <thread>",
                            "#include <cstring>\n#include <thread>",
                            "kernel explicit memcpy include")
    result = unique_replace(
        result, '#include "core/libraries/kernel/process.h"',
        '#include "core/libraries/kernel/process.h"\n'
        '#include "core/libraries/kernel/sanitizer.h"\n'
        '#include "core/libraries/kernel/thread_atexit.h"',
        "kernel extra headers")
    result = unique_replace(result, '#include "core/libraries/libs.h"',
                            '#include "core/libraries/libs.h"\n#include "core/memory.h"',
                            "kernel memory permission include")
    result = unique_replace(
        result, "    case EINVAL:\n        return POSIX_EINVAL;",
        "    case ENAMETOOLONG:\n        return POSIX_ENAMETOOLONG;\n"
        "    case ELOOP:\n        return POSIX_ELOOP;\n"
        "    case EINVAL:\n        return POSIX_EINVAL;", "kernel POSIX errno mapping")
    result = unique_replace(
        result,
        between(result, "s32 PS4_SYSV_ABI __sys_regmgr_call",
                "s32 PS4_SYSV_ABI sceKernelGetOpenPsId", "upstream regmgr"),
        between(fork, "s32 PS4_SYSV_ABI __sys_regmgr_call",
                "// Nominally: long sysconf(int name);", "fork regmgr"),
        "kernel guarded regmgr input trace")
    result = unique_replace(
        result, "    Libraries::Kernel::RegisterMemory(sym);",
        "    Libraries::Kernel::RegisterMemory(sym);\n"
        "    Libraries::Kernel::RegisterSanitizer(sym);\n"
        "    Libraries::Kernel::RegisterThreadAtexit(sym);",
        "kernel register extra functions")
    for symbol in ("Libraries::Net::InstallKernelIntegration()",
                   "sceKernelGetOpenPsId", "RegisterSanitizer(sym)",
                   "RegisterThreadAtexit(sym)", "Core::MemoryProt::CpuRead"):
        if symbol not in result:
            raise RuntimeError(f"kernel: missing required {symbol}")
    return result


def resolve_buffer(upstream: str, fork: str) -> str:
    # Upstream ALREADY uses vk::MemoryAllocateFlagBits::eDeviceAddress. Do
    # not double-apply that fork fix. Retain the distinct sparse queue fix.
    if "vk::MemoryAllocateFlagBits::eDeviceAddress" not in upstream:
        raise RuntimeError("buffer: upstream BDA allocation correction not found")
    if "instance.GetSparseQueue().bindSparse(sparse_info)" not in fork:
        raise RuntimeError("buffer: tested fork sparse-capable queue routing absent")
    result = unique_replace(
        upstream,
        "instance.GetGraphicsQueue().bindSparse(sparse_info)",
        "instance.GetSparseQueue().bindSparse(sparse_info)",
        "buffer: sparse-capable submission")
    for symbol in ("ReadEdgeImagePages(", "DownloadMemoryFlags"):
        # DownloadMemoryFlags belongs to texture cache, not here. Only
        # verify common buffer-cache redesign in this file.
        if symbol == "ReadEdgeImagePages(" and symbol not in result:
            raise RuntimeError("buffer: upstream async range handling missing")
    return result


def resolve_texture(upstream: str, fork: str) -> str:
    # Upstream ALREADY refreshes newly grown depth subresources; preserve
    # its new GC, async downloads and image bookkeeping intact.
    # Bring over only fork's bounded depth-growth/containment diagnostics.
    result = unique_replace(
        upstream, '#include "video_core/buffer_cache/buffer_cache.h"',
        '#include "video_core/buffer_cache/buffer_cache.h"\n'
        '#include "video_core/graphics_diagnostics.h"',
        "texture: diagnostics include")
    old = between(result,
                  "        if (new_info.num_samples == 1 &&",
                  "        new_image.flags &= ~ImageFlagBits::Dirty;",
                  "upstream depth-growth initialisation")
    new = between(fork,
                  "        const bool grew = new_info.resources.layers",
                  "        new_image.flags &= ~ImageFlagBits::Dirty;",
                  "fork depth-growth initialisation")
    result = unique_replace(result, old, new,
                            "texture: depth-growth equivalent / recording")
    diag = between(fork, "        if (grew) {\n"
                          "            GraphicsDiagnostics::Emit",
                   "        // Free the cache image.", "fork growth diagnostics")
    result = unique_replace(
        result,
        "        runtime.CopyColorAndDepth(&cache_image, &new_image);\n\n",
        "        runtime.CopyColorAndDepth(&cache_image, &new_image);\n\n" + diag,
        "texture: emit real growth state")
    containment = between(
        fork,
        "            GraphicsDiagnostics::Emit(\n"
        "                GraphicsDiagnostics::Event::Containment",
        "            // The image was clearly picked up wrong.",
        "fork containment diagnostics")
    result = unique_replace(
        result,
        "        } else if (image_resolved.info.resources < info.resources) {\n",
        "        } else if (image_resolved.info.resources < info.resources) {\n" + containment,
        "texture: image containment report")
    for symbol in ("GraphicsDiagnostics::Event::DepthGrowth",
                   "GraphicsDiagnostics::Event::Containment",
                   "page_manager.UpdatePageWatchers",
                   "scheduler.PopPendingOperations"):
        if symbol not in result:
            raise RuntimeError(f"texture: missing required {symbol}")
    return result


def main() -> None:
    if len(sys.argv) != 2:
        raise RuntimeError("Usage: esde_resolve_main_conflicts.py <isolated-source-path>")
    repo = Path(sys.argv[1]).resolve(strict=True)
    if git(repo, "rev-parse", "HEAD").strip() != UPSTREAM_SHA:
        raise RuntimeError("Unexpected HEAD; do not auto-resolve")
    if git(repo, "rev-parse", "MERGE_HEAD").strip() != FORK_MAIN_SHA:
        raise RuntimeError("Unexpected MERGE_HEAD; do not auto-resolve")

    found = set(filter(None, git(repo, "diff", "--name-only", "--diff-filter=U").splitlines()))
    expected = set(BLOBS)
    if found != expected:
        raise RuntimeError(
            f"Different conflict set, stop rather than guess: expected={sorted(expected)}, "
            f"found={sorted(found)}")

    source = {}
    for path, (up_sha, fork_sha) in BLOBS.items():
        ours = git(repo, "rev-parse", f":2:{path}").strip()
        theirs = git(repo, "rev-parse", f":3:{path}").strip()
        if (ours, theirs) != (up_sha, fork_sha):
            raise RuntimeError(f"Source blob mismatch for {path}: ours={ours}, theirs={theirs}")
        source[path] = (git(repo, "show", f":2:{path}"),
                        git(repo, "show", f":3:{path}"))

    # Compute and assert EVERY merged file in memory, before modifying the worktree.
    solutions = {
        LAYER: resolve_layer(*source[LAYER]),
        KERNEL: resolve_kernel(*source[KERNEL]),
        BUFFER: resolve_buffer(*source[BUFFER]),
        TEXTURE: resolve_texture(*source[TEXTURE]),
    }
    for path, content in solutions.items():
        (repo / path).write_text(content, encoding="utf-8", newline="")
        subprocess.run(["git", "-C", str(repo), "add", "--", path], check=True)
        print(f"RESOLVED={path} bytes={len(content.encode('utf-8'))}", flush=True)
    subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--check"], check=True)
    if git(repo, "diff", "--name-only", "--diff-filter=U").strip():
        raise RuntimeError("Unresolved files remain")
    subprocess.run(["git", "-C", str(repo), "commit", "--no-edit"], check=True)
    if git(repo, "rev-parse", "--verify", "MERGE_HEAD").strip():
        raise RuntimeError("Unexpected MERGE_HEAD after commit")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ESDE_MERGE_RESOLVE=FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    print("ESDE_MERGE_RESOLVE=PASS: exact four conflicting paths reconciled")
