#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only evidence audit and isolated source-merge preflight for GoW + GoT.

No emulator launch, ES-DE selection, game/config/save changes, process signals,
system updates, GPU/driver resets, service changes or SSH termination.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

ROOT = Path.home() / "projects/shadps4"
ESDE = ROOT / "ESDE"
LOGS = ROOT / "logs"
UNIFIED_SOURCE = ESDE / "unified-20261018-160757/source"
UNIFIED_BIN = Path.home() / "Applications/shadps4/shadps4"
UNIFIED_SHA = "d8ef5b2ae1b12464a5d4ca83d8e5e9c3b0f2f0053148c531eda7457c9c21117d"
UPSTREAM_SHA = "d615b3887ba29025a59242e1a26f5b68bb176158"
GOW_REF = "f909c3abb1ac1e7c8d87de833d3659f7d0b8fc87"
GOT_VISUAL_REF = "e9f470b359845a456139a865aa1d7b3001f0dbfd"
GOW = ROOT / "GodOfWarRagnarok"
GOT = ROOT / "GhostOfTsushima"
GOW_SOURCE = GOW / "tests/native-rendered-gfx-20261016-152535-3014679/source"
GOW_HOTPATH_SOURCE = GOW / "tests/gow-renderer-log-hotpath-20261017/source"
GOW_RENDERER = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
GOW_RENDERER_SHA = "bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a"
GOW_BIN = GOW / ("tests/gow-renderer-log-hotpath-20261017/build/"
                 "shadps4.fullgfx-uffd-batched-read-gow-uffd-batched-read-20261017-231412")
GOW_BIN_SHA = "8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef"

# SHA-1 Git blobs from the Oct 18 GoW checkpoint's exact source manifest.
GOW_PROVEN_BLOBS = {
    "src/common/logging/log.cpp":
        "c0db514053650522588349215738e46408c905e0",
    "src/video_core/page_manager.cpp":
        "75ede506fa3a07b01263542af320a3e90535adc4",
    "src/core/libraries/audio/audioout.cpp":
        "82e6698b821b452f1b34402a58aee43c2098704c",
    "src/core/libraries/audio/audioout_sync.h":
        "8efda58a938100999a248e7fcdbd7b7159ec5658",
}

GOT_V51_PARENT = "02be53c70ac824ca42b1df41af93163f0e4bdfe5b1666f270193f51fd58f155d"
GOT_V51_RESULT = "814677f63524610ba23932fd4ebbe062be299d641ac4699c390d62fe1cc1b03b"
WATCH_FILES = (
    "src/core/cpu_id.cpp",
    "src/core/cpu_affinity.cpp",
    "src/core/cpu_id_translation/launcher.cpp",
    "src/video_core/page_manager.cpp",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
    "src/shader_recompiler/ir/passes/srt_offset_codegen.h",
    "src/core/libraries/audio/audioout.cpp",
    "src/core/libraries/audio3d/audio3d.cpp",
    "src/video_core/amdgpu/liverpool.cpp",
    "src/core/devtools/quit_dialog.h",
    "src/core/startup_progress.h",
    "src/imgui/startup_loading.cpp",
)


def digest(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def installed_digest() -> str | None:
    # Existing ES-DE selector creates an intentional symlink from the default
    # core to a versioned release; reject arbitrary external symlink targets.
    try:
        release_root = Path.home() / "Applications/shadps4/releases"
        resolved = UNIFIED_BIN.resolve(strict=True)
        if not resolved.is_relative_to(release_root.resolve(strict=True)):
            return None
        return digest(resolved)
    except (OSError, RuntimeError):
        return None


def command(*args: str, cwd: Path | None = None, check: bool = True):
    p = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"Command failed ({p.returncode}): {' '.join(map(str,args))}\n{p.stderr[-1500:]}")
    return p


def git(repo: Path, *args: str, check: bool = True):
    return command("git", "-C", str(repo), *args, check=check)


def source_status(path: Path):
    if not path.is_dir() or not (path / ".git").exists():
        return {"exists": False}
    s = git(path, "status", "--porcelain", "--untracked-files=no", check=False)
    h = git(path, "rev-parse", "HEAD", check=False)
    return {"exists": True, "head": h.stdout.strip(), "tracked_dirty": s.stdout.splitlines()[:50],
            "status_rc": s.returncode, "sha": {k: digest(path / k) for k in WATCH_FILES}}


def find_got_stencil_files():
    raster = Path(GOW_RENDERER)
    paths = []
    # Bounded scans in precisely the two known workspaces; never scan games or
    # system directories, follow symlinks or import third-party code.
    for parent in (GOT / "tests", GOT / "builds"):
        if not parent.is_dir():
            continue
        for pattern in ("got-v*/src", "got-v*/source", "got*/source", "got-v*"):
            paths.extend(parent.glob(pattern))
    found = {}
    for base in list(dict.fromkeys(paths))[:450]:
        if base.is_symlink():
            continue
        candidate = base / raster
        h = digest(candidate)
        if h in (GOT_V51_PARENT, GOT_V51_RESULT) and h not in found:
            found[h] = str(candidate)
    return found


def recent_game_logs():
    roots = [Path.home() / "Applications/shadps4/user/log",
             Path.home() / ".local/share/shadPS4/user/log",
             Path.home() / ".local/share/shadPS4/log",
             Path.home() / "user/log",
             ROOT / "ESDE/unified-20261018-160757/source/user/log",
             ROOT / "ESDE/unified-20261018-160757/build/user/log"]
    releases = Path.home() / "Applications/shadps4/releases"
    if releases.is_dir():
        roots += [p for p in releases.glob("*/user/log") if p.is_dir()][:25]
    # This is limited to log directories rather than recursively walking
    # the user's large emulation/video/save libraries.
    for game in (GOW, GOT):
        for sub in ("logs", "play"):
            parent = game / sub
            if not parent.is_dir():
                continue
            roots += [p for p in parent.glob("*/user/log") if p.is_dir()][:50]
    choices = []
    for root in roots:
        if not root.is_dir():
            continue
        for ext in ("*.log", "shad_log.txt", "*.txt"):
            for item in root.glob(ext):
                if not item.is_file() or item.is_symlink():
                    continue
                if item.stat().st_size > 100_000_000:
                    continue
                choices.append(item)
    choices.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    used = set()
    found = []
    for p in choices:
        if str(p) in used or len(found) >= 12:
            continue
        used.add(str(p))
        # Keep only bounded tail and fault-centric lines, no games/saves or
        # user config copied. Sources are never included in the upload archive.
        with p.open("rb") as handle:
            handle.seek(max(0, p.stat().st_size - 220_000))
            raw = handle.read().decode("utf-8", "replace")
        lines = raw.splitlines()
        fault = [x for x in lines if re.search(
            r"SIGSEGV|SIGILL|SIGABRT|SIGTRAP|VUID-|Device.?Lost|FAULT|critical|"
            r"Unhandled access|gpu fault|userfaultfd|stack_chk|GOW_SRT|"
            r"CpuId|fatal|ERROR", x, re.I)]
        found.append({"path": str(p), "mtime": int(p.stat().st_mtime),
                      "size": p.stat().st_size, "tail": "\n".join(lines[-90:]),
                      "fault_lines": fault[-120:]})
    return found


def stage_preflight(report: dict, run: Path):
    # Git's merge engine computes and reports ALL conflicts in one pass.
    # This staging repo is disposable; do not change existing user sources,
    # the live launcher, or either game's protected trees.
    source = UNIFIED_SOURCE
    state = report["source"]["unified"]
    if not state.get("exists") or state.get("tracked_dirty"):
        raise RuntimeError("Existing ES-DE source is missing or dirty; no staging")
    if not report["checks"]["installed_unified_matches_known_sha"]:
        raise RuntimeError("Installed core changed; refusing to stage against an unverified base")
    if not report["checks"]["gow_binary_matches_proven_sha"]:
        raise RuntimeError("Proven GoW full-graphics executable missing/modified; no staging")
    if not report["checks"]["gow_renderer_matches_proven_sha"]:
        raise RuntimeError("Proven GoW source rasterizer mismatch; no staging")
    if not report["checks"]["got_v51_both_source_versions_found"]:
        raise RuntimeError("GoT v51 source ancestry not available; no staged overlay")

    proof_git = GOW_SOURCE
    for p in (source, proof_git):
        if not (p / ".git").exists():
            raise RuntimeError(f"Missing git provenance: {p}")
    # Read-only local transport: record which proof branch and path actually
    # changed; merging a branch is NOT enough to certify the compiled overlay.
    staging = run / "source"
    git(source, "worktree", "add", "--detach", str(staging), state["head"])
    command("git", "-C", str(staging), "config", "user.name", "ES-DE Source Audit")
    command("git", "-C", str(staging), "config", "user.email",
            "esde-fullstack@users.noreply.github.com")
    # Preserve the previous unified front-end and runtime stack, but advance
    # the isolated candidate to the EXACT Oct 19 official upstream main first.
    # An upstream conflict is surfaced as a complete path list; never choose
    # "ours" or "theirs" wholesale.
    git(staging, "-c", "http.version=HTTP/1.1", "fetch", "--no-tags",
        "https://github.com/shadps4-emu/shadPS4.git", UPSTREAM_SHA)
    upstream_head = git(staging, "rev-parse", "FETCH_HEAD").stdout.strip()
    if upstream_head != UPSTREAM_SHA:
        raise RuntimeError("Official upstream pin mismatch, refusing unexpected source")
    baseline = git(staging, "merge", "--no-ff", "--no-edit", UPSTREAM_SHA, check=False)
    if baseline.returncode:
        conflicts = git(staging, "diff", "--name-only", "--diff-filter=U")
        report["stage"] = {**report.get("stage", {}),
                           "status": "LATEST_UPSTREAM_MERGE_CONFLICT_NO_BUILD",
                           "upstream_conflicts": conflicts.stdout.splitlines(),
                           "conflict_diff": git(staging, "diff", "--cc", check=False).stdout[:300_000],
                           "upstream_output": (baseline.stdout + baseline.stderr)[-8000:],
                           "path": str(staging)}
        return
    # The Oct 16 protected working tree is OLDER than the Oct 17
    # full-graphics UFFD checkpoint. Merging its HEAD would silently omit
    # subsequent patches. Import the exact pinned Oct 17 GoW revision.
    git(staging, "-c", "http.version=HTTP/1.1", "fetch", "--no-tags",
        "https://github.com/Chreece/shadPS4.git", GOW_REF)
    proven_head = git(staging, "rev-parse", "FETCH_HEAD").stdout.strip()
    if proven_head != GOW_REF:
        raise RuntimeError("GoW pinned commit resolved to unexpected SHA")
    local_head = git(proof_git, "rev-parse", "HEAD").stdout.strip()
    report["stage"] = {"path": str(staging), "source_head": state["head"],
                       "goW_pinned_upstream_based_commit": proven_head,
                       "protected_oct16_source_head": local_head,
                       "runtime_proof_binary_sha256": GOW_BIN_SHA,
                       "source_object_proof_note":
                           "The f909 Git snapshot must still be reconciled with "
                           "the checksum-pinned local Oct 17 fullgfx source overlays."}
    # Stage the relevant generic GoW source ancestry. No game is launched
    # and we refuse to pretend that this alone equals the saved fullgfx ELF.
    merged = git(staging, "merge", "--no-commit", "--no-ff",
                 proven_head, check=False)
    conflicts = git(staging, "diff", "--name-only", "--diff-filter=U")
    report["stage"]["gow_merge_rc"] = merged.returncode
    report["stage"]["conflicts"] = conflicts.stdout.splitlines()
    report["stage"]["merge_output"] = (merged.stdout + merged.stderr)[-8000:]
    report["stage"]["conflict_diff"] = git(staging, "diff", "--cc", check=False).stdout[:300_000]
    # No "ours"/"theirs" conflict auto-choice, no source replacement.
    # Avoid launching an emulator from unverified hardware semantics.
    if merged.returncode or conflicts.stdout.strip():
        report["stage"]["status"] = "REQUIRES_EXPLICIT_SOURCE_RECONCILIATION_NO_BUILD"
        return
    # The proof's local renderer was compiled from a bounded full-graphics
    # overlay. If its disk version differs from Git HEAD, port THAT exact
    # delta using three-way merging rather than overwriting modern GPU code.
    renderer_root = next((root for root in (GOW_SOURCE, GOW_HOTPATH_SOURCE)
                          if digest(root / GOW_RENDERER) == GOW_RENDERER_SHA), None)
    if renderer_root is None:
        raise RuntimeError("No exact proven GoW renderer source available")
    tested_renderer = renderer_root / GOW_RENDERER
    baseline = git(renderer_root, "show", "HEAD:" + GOW_RENDERER, check=False)
    if baseline.returncode:
        raise RuntimeError("Proven renderer has no Git base for safe 3-way port")
    local_overlay = tested_renderer.read_text()
    if local_overlay != baseline.stdout:
        ours = run / "renderer-before-gow.cpp"
        old = run / "renderer-original-gow.cpp"
        new = run / "renderer-proven-gow.cpp"
        ours.write_bytes((staging / GOW_RENDERER).read_bytes())
        old.write_text(baseline.stdout)
        new.write_text(local_overlay)
        overlay = command("git", "merge-file", "-p", str(ours), str(old), str(new), check=False)
        report["stage"]["gow_renderer_overlay_rc"] = overlay.returncode
        if overlay.returncode or "<<<<<<<" in overlay.stdout:
            report["stage"]["gow_renderer_conflict"] = overlay.stdout[-120_000:]
            report["stage"]["status"] = "GOW_RENDERER_PROVEN_OVERLAY_CONFLICT_NO_BUILD"
            return
        (staging / GOW_RENDERER).write_text(overlay.stdout)
        git(staging, "add", "--", GOW_RENDERER)
        report["stage"]["gow_exact_renderer_source_ported"] = True
    else:
        report["stage"]["gow_exact_renderer_source_ported"] = False
        report["stage"]["gow_renderer_already_git_tracked"] = True

    # Recover additional preserved UFFD/page and AudioOut source files only
    # from EXACT blob-hash matches. Their provenance is stronger than a
    # mutable branch name. Unknown/missing blobs stop before compiling.
    report["stage"]["pinned_gow_blob_ports"] = {}
    for rel, expected_blob in GOW_PROVEN_BLOBS.items():
        selected_root = None
        for root in (GOW_HOTPATH_SOURCE, GOW_SOURCE):
            candidate = root / rel
            if candidate.is_file() and not candidate.is_symlink():
                h = git(root, "hash-object", str(candidate), check=False)
                if h.returncode == 0 and h.stdout.strip() == expected_blob:
                    selected_root = root
                    break
        if selected_root is None:
            report["stage"]["status"] = "GOW_PINNED_SOURCE_BLOB_MISSING_NO_BUILD"
            report["stage"]["missing_proof_blob"] = rel
            return
        proven = selected_root / rel
        base = git(selected_root, "show", "HEAD:" + rel, check=False)
        current_file = staging / rel
        if not current_file.is_file() or base.returncode:
            report["stage"]["status"] = "GOW_PINNED_SOURCE_NO_THREEWAY_BASE_NO_BUILD"
            report["stage"]["missing_merge_base"] = rel
            return
        before = current_file.read_text()
        new_content = proven.read_text()
        if before == new_content or new_content == base.stdout:
            report["stage"]["pinned_gow_blob_ports"][rel] = "ALREADY_PRESENT_OR_NO_LOCAL_DELTA"
            continue
        ours_path = run / ("gow-ours-" + Path(rel).name)
        base_path = run / ("gow-base-" + Path(rel).name)
        new_path = run / ("gow-proof-" + Path(rel).name)
        ours_path.write_text(before)
        base_path.write_text(base.stdout)
        new_path.write_text(new_content)
        outcome = command("git", "merge-file", "-p", str(ours_path),
                          str(base_path), str(new_path), check=False)
        if outcome.returncode or "<<<<<<<" in outcome.stdout:
            report["stage"]["status"] = "GOW_PROVEN_SOURCE_THREEWAY_CONFLICT_NO_BUILD"
            report["stage"]["pinned_conflict_file"] = rel
            report["stage"]["pinned_conflict_excerpt"] = outcome.stdout[-100_000:]
            return
        current_file.write_text(outcome.stdout)
        git(staging, "add", "--", rel)
        report["stage"]["pinned_gow_blob_ports"][rel] = "PORTED_EXACT_PROVEN_SOURCE_DELTA"

    # Port exactly the previously source-hash-proven v51 stencil correction.
    # This is a three-way source merge and may conflict with the newer GoW
    # rasterizer. A conflict is an explicit stop, never an "ours" override.
    pre = Path(report["ghost_v51"][GOT_V51_PARENT])
    post = Path(report["ghost_v51"][GOT_V51_RESULT])
    raster = staging / GOW_RENDERER
    current = run / "renderer-current.cpp"
    current.write_bytes(raster.read_bytes())
    overlay = command("git", "merge-file", "-p", str(current), str(pre), str(post),
                      check=False)
    report["stage"]["ghost_stencil_merge_rc"] = overlay.returncode
    report["stage"]["ghost_stencil_diff"] = overlay.stdout[-120_000:]
    if overlay.returncode or "<<<<<<<" in overlay.stdout:
        report["stage"]["status"] = "GOW_MERGED_GOT_STENCIL_CONFLICT_NO_BUILD"
        return
    # A successful 3-way port is *staged only*; it still requires a native
    # build and runtime validation before being installed.
    raster.write_text(overlay.stdout)
    git(staging, "add", "--", GOW_RENDERER)
    check = git(staging, "diff", "--cached", "--check", check=False)
    if check.returncode:
        raise RuntimeError("Compiled-source whitespace/merge checks failed")
    report["stage"]["status"] = "GOW_SOURCE_PLUS_GOT_V51_STENCIL_STAGED_NO_BUILD"
    report["stage"]["remaining"] = (
        "Confirm exact fullgfx object/source fingerprints, then port other "
        "verified GoT GPU bindings and validate both games in an isolated build. "
        "Unproven 27K GPU-indexed image feedback deliberately excluded."
    )
    # Do NOT merge the experimental Ghost 27k-table GPU-feedback branch.



def build_staged_source(report: dict, run: Path):
    """Compile the isolated candidate only after every pinned source merge succeeds.

    The binary stays in project/ESDE; no ES-DE symlink or live emulator is
    changed, and no game/process is launched.
    """
    stage = report.get("stage", {})
    if stage.get("status") != "GOW_SOURCE_PLUS_GOT_V51_STENCIL_STAGED_NO_BUILD":
        report["build"] = {"status": "SKIPPED_SOURCE_PREFLIGHT_NOT_PASSED"}
        return
    if shutil.which("docker") is None:
        raise RuntimeError("Docker unavailable; do not modify host toolchain")
    image = "shadps4-ngs2-builder:trixie-clang19-v1"
    if command("docker", "image", "inspect", image, check=False).returncode:
        raise RuntimeError("Pinned Debian 13/Clang 19 builder image not installed")
    source = run / "source"
    build = run / "build"
    log = LOGS / ("esde-fullstack-build-" + run.name + ".log")
    report["build"] = {"status": "STARTED", "build_log": str(log)}
    # Existing source worktree has native git and submodules; never touch
    # original GoW/GoT or installed ES-DE source/binaries.
    def run_logged(label, argv):
        with log.open("a") as out:
            out.write("\nSTAGE=" + label + "\n")
            out.flush()
            p = subprocess.run(list(map(str, argv)), cwd=run, stdout=out,
                               stderr=subprocess.STDOUT)
        if p.returncode:
            with log.open("rb") as inp:
                inp.seek(max(0, log.stat().st_size - 24_000))
                report["build"]["failure_tail"] = inp.read().decode("utf-8", "replace")
            raise RuntimeError(f"Isolated build stage {label} failed, code={p.returncode}")
    run_logged("SUBMODULES",
               ["git", "-C", str(source), "submodule", "update", "--init",
                "--recursive", "--jobs", "4"])
    common = ["docker", "run", "--rm",
              "--user", f"{os.getuid()}:{os.getgid()}",
              "--mount", f"type=bind,src={ESDE},dst={ESDE}",
              "--workdir", str(run), "--env", "HOME=" + str(run),
              image]
    cmake_args = ["cmake", "-S", str(source), "-B", str(build),
                  "-G", "Ninja", "-DCMAKE_C_COMPILER=clang-19",
                  "-DCMAKE_CXX_COMPILER=clang++-19",
                  "-DCMAKE_CXX_COMPILER_CLANG_SCAN_DEPS=/usr/bin/clang-scan-deps-19",
                  "-DCMAKE_BUILD_TYPE=Release", "-DENABLE_TESTS=OFF",
                  "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF"]
    run_logged("CMAKE_CONFIGURE", common + cmake_args)
    run_logged("NATIVE_LINK", common + ["cmake", "--build", str(build),
                                      "--target", "shadps4", "--parallel", "4"])
    exe = build / "shadps4"
    if not exe.is_file() or exe.is_symlink() or exe.read_bytes()[:5] != b"\x7fELF\x02":
        raise RuntimeError("Isolated executable is not a 64-bit ELF")
    deps = command("ldd", str(exe), check=False)
    if deps.returncode or "not found" in deps.stdout:
        raise RuntimeError("Isolated emulator is missing runtime libraries")
    smoke = command("timeout", "30", str(exe), "--help", cwd=run, check=False)
    if smoke.returncode:
        raise RuntimeError("Candidate CLI smoke failed: " + (smoke.stdout + smoke.stderr)[-1800:])
    report["build"] = {"status": "PASS_COMPILED_NOT_GAMEPLAY_TESTED",
                       "sha256": digest(exe),
                       "binary": str(exe),
                       "build_log": str(log)}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", action="store_true",
                    help="Also try an isolated GoW + Ghost source merge and conflict census")
    ap.add_argument("--build", action="store_true",
                    help="After conflict-free staging, compile one isolated binary, never install")
    args = ap.parse_args()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS.mkdir(parents=True, exist_ok=True)
    run = ESDE / ("fullstack-audit-" + stamp)
    run.mkdir(parents=True)
    record = run / "audit.json"
    archive = LOGS / ("esde-fullstack-audit-" + stamp + ".tar.gz")
    report = {
        "date": dt.datetime.now(dt.timezone.utc).isoformat(),
        "installed_binary": {"path": str(UNIFIED_BIN), "sha256": installed_digest()},
        "goW_proven_binary": {"path": str(GOW_BIN), "sha256": digest(GOW_BIN)},
        "source": {"unified": source_status(UNIFIED_SOURCE),
                   "gow": source_status(GOW_SOURCE),
                   "gow_hotpath": source_status(GOW_HOTPATH_SOURCE)},
        "ghost_v51": find_got_stencil_files(),
        "goW_checkpoint": GOW_REF, "goT_world_visible": GOT_VISUAL_REF,
        "official_upstream_20261019": UPSTREAM_SHA,
        "logs": recent_game_logs(),
        "checks": {},
        "note": "No game was launched; binary/source proof is not a diagnosis of the current crash."
    }
    report["checks"] = {
        "installed_unified_matches_known_sha": installed_digest() == UNIFIED_SHA,
        "gow_binary_matches_proven_sha": digest(GOW_BIN) == GOW_BIN_SHA,
        "gow_renderer_matches_proven_sha":
            any(digest(root / GOW_RENDERER) == GOW_RENDERER_SHA
                for root in (GOW_SOURCE, GOW_HOTPATH_SOURCE)),
        "got_v51_both_source_versions_found":
            all(h in report["ghost_v51"] for h in (GOT_V51_PARENT, GOT_V51_RESULT)),
        "unified_lacks_guest_cpuid_source":
            report["source"]["unified"].get("sha", {}).get("src/core/cpu_id.cpp") is None,
        "unified_lacks_guest_affinity_source":
            report["source"]["unified"].get("sha", {}).get("src/core/cpu_affinity.cpp") is None,
    }
    if args.stage or args.build:
        try:
            stage_preflight(report, run)
            if args.build:
                build_staged_source(report, run)
        except Exception as e:
            if "stage" not in report or "status" not in report["stage"]:
                report["stage"] = {"status": "STOPPED_WITHOUT_INSTALLING"}
            report["error"] = str(e)
    record.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    # Only bounded audit evidence, not saved games, full source trees or config.
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(record, arcname="esde-fullstack-audit/audit.json")
    print("FULLSTACK_AUDIT="+str(record))
    print("FULLSTACK_ARCHIVE="+str(archive))
    for key, ok in report["checks"].items():
        print(key.upper()+"="+str(ok))
    if args.stage or args.build:
        print("STAGE_STATUS="+report.get("stage", {}).get("status", "NOT_STARTED"))
        print("STAGE_CONFLICT_COUNT="+str(len(report.get("stage", {}).get("conflicts", []))))
    if args.build:
        print("BUILD_STATUS="+report.get("build", {}).get("status", "SKIPPED"))
        if report.get("build", {}).get("binary"):
            print("CANDIDATE_BINARY="+report["build"]["binary"])
    print("INSTALLED_EMULATOR_UNCHANGED=YES")
    print("SUNSHINE_SSH_SAVES_CONFIG_DRIVER_UNCHANGED=YES")


if __name__ == "__main__":
    main()
