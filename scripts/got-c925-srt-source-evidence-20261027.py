#!/usr/bin/env python3
"""Preserve GoT's newly working menu/gameplay ELF and capture real unresolved SRT IR.

The generic source patch changes logging ONLY, never guest execution, shader
semantics, device resource counts, CPU identity, or Vulkan descriptor handling.
No production launcher, original saves, Ollama or graphics services are reset.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

HOME = Path.home()
PROJECT = HOME / "projects/shadps4"
ORIGINAL = PROJECT / "scripts/got-c925-integrated-native-ebea63b.py"
ORIGINAL_BLOB = "5ec32ffdccd7e2a4dba29b56f8fe8f53343b348b"
SOURCE_COMMIT = "c925523b49ef39434024b16b2b014d120471ee1a"
PROGRESS_ELF = "be3e8c12cf91117917d355d41a03e227a45cd338981d672de4f0ca2c57b68f28"
SRT_SOURCE_SHA = "431484e293eb66c76b10a6956ba72e2a4cf334d3f52a8d7e1878202fe7219877"
SRT_DIAGNOSTIC_SHA = "41f88d8f96d59d2479d4552f92e6593e711af45c0a7e1bdacbbf9b6e35bcd187"
PATCH_COMMIT = "372bf1c1446b0b00fc4643ac5178be85ce1f5a0f"
PATCH_BLOB = "4643592937adae95d7557a75c0ddbd0feb4d2b61"
PATCH_PATH = "scripts/patchsets/c925-srt-unresolved-provenance-20261027.patch"
RASTER_SHA = "c599ad215ee9ffc233b4c3ee0d0bec5615d2acb7bb0681b3291e119179880e15"
RASTER_HEADER_SHA = "5a99989156ae9310a1812fd5400a0cff0d12f2ed6d618efe98199b6fb5d122ed"


def base_runner():
    if ORIGINAL.is_symlink() or not ORIGINAL.is_file():
        raise RuntimeError("Pinned previous runner not present")
    blob = subprocess.check_output(
        ["git", "hash-object", str(ORIGINAL)], text=True, timeout=15).strip()
    if blob != ORIGINAL_BLOB:
        raise RuntimeError("Pinned previous runner has changed - refusing test")
    spec = importlib.util.spec_from_file_location("got_c925_previous", ORIGINAL)
    if not spec or not spec.loader:
        raise RuntimeError("Cannot load source-verification helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def preserve_progress(m, elf: Path) -> Path:
    target = m.ROOT / "candidates/c925-20261027-154043-descriptor-typed-proven-progress/shadps4"
    if target.exists():
        if target.is_symlink() or not target.is_file() or m.sha256(target) != PROGRESS_ELF:
            raise m.Abort("PROGRESS_BINARY_BACKUP_EXISTS_WITH_WRONG_SHA", 2)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.parent.is_symlink():
            raise m.Abort("UNSAFE_PROVEN_BINARY_BACKUP_PATH", 2)
        temp = target.with_name(target.name + ".saving")
        if temp.exists():
            raise m.Abort("EXISTING_PROVEN_BINARY_TEMP_BACKUP", 2)
        shutil.copy2(elf, temp)
        if m.sha256(temp) != PROGRESS_ELF:
            temp.unlink(missing_ok=True)
            raise m.Abort("PROGRESS_ELF_BACKUP_SHA_MISMATCH", 2)
        temp.replace(target)
        target.chmod(0o555)
    m.STATE["proven_progress_elf_path"] = str(target)
    m.STATE["proven_progress_elf_sha256"] = PROGRESS_ELF
    m.STATE["original_menu_elf_still_verified"] = (
        m.PROVEN.is_file() and m.sha256(m.PROVEN) == m.MENU_ELF)
    if not m.STATE["original_menu_elf_still_verified"]:
        raise m.Abort("ORIGINAL_MENU_POSITIVE_ELF_CHANGED", 2)
    print("PROVEN_PROGRESS_ELF_PRESERVED=" + str(target), flush=True)
    return target


def preflight(m):
    m.stage("VERIFY_REAL_C925_154043_GAMEPLAY_POSITIVE_SOURCE_BINARY")
    root = m.ROOT / "experiments/c925-integrated-gpu-20261027-122701"
    m.EXP, m.SRC, m.BUILD = root, root / "source", root / "build"
    if any(p.is_symlink() for p in (m.SRC, m.BUILD, m.EXP)):
        raise m.Abort("REFUSE_SYMLINKED_EXPERIMENT_ROOT", 2)
    if (not (m.BUILD / "build.ninja").is_file() or
            not (m.BUILD / "user").is_dir() or
            not (m.BUILD / "CMakeCache.txt").is_file()):
        raise m.Abort("EXISTING_C925_BUILD_OR_ISOLATED_PROFILE_MISSING", 2)
    if ("CMAKE_HOME_DIRECTORY:INTERNAL=" + str(m.SRC)
            not in (m.BUILD / "CMakeCache.txt").read_text()):
        raise m.Abort("CMAKE_BUILD_ROOT_MISMATCH", 2)
    if m.output("source-head", ["git", "-C", str(m.SRC), "rev-parse", "HEAD"]) != SOURCE_COMMIT:
        raise m.Abort("EXPERIMENT_SOURCE_COMMIT_NOT_EXACT_C925", 2)
    srt = m.SRC / "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
    raster = m.SRC / "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
    header = m.SRC / "src/video_core/renderer_vulkan/vk_rasterizer.h"
    if any(path.is_symlink() for path in (srt, raster, header)):
        raise m.Abort("SRT_OR_RENDERER_SYMLINK_NOT_ALLOWED", 2)
    if (m.sha256(srt) != SRT_SOURCE_SHA or
            m.sha256(raster) != RASTER_SHA or
            m.sha256(header) != RASTER_HEADER_SHA):
        raise m.Abort("SOURCE_NOT_PREVIOUS_SUCCESSFUL_C925_SNAPSHOT", 2)
    elf = m.BUILD / "shadps4"
    if elf.is_symlink() or not elf.is_file() or m.sha256(elf) != PROGRESS_ELF:
        raise m.Abort("CURRENT_BUILD_BINARY_NO_LONGER_GAMEPLAY_POSITIVE", 2)
    if not m.GAME.is_file():
        raise m.Abort("GOT_GAME_IMAGE_MISSING", 2)
    if subprocess.run(["pgrep", "-x", "shadps4"], capture_output=True).returncode == 0:
        raise m.Abort("ANOTHER_SHADPS4_INSTANCE_IS_RUNNING", 2)
    m.output("git-diff-check-before", ["git", "-C", str(m.SRC), "diff", "--check"])
    m.STATE.update({
        "record_type": "UNIVERSAL_SRT_UNRESOLVED_PROVENANCE_C925",
        "source_base": SOURCE_COMMIT, "candidate_before": PROGRESS_ELF,
        "rendering_semantics_changed": False, "no_auto_input": True,
        "tested_games": [], "original_srt_sha256": SRT_SOURCE_SHA,
    })
    return elf, srt


def capture_game(m, elf):
    m.stage("RUN_GOT_118S_WITH_UNCHANGED_GRAPHICS_AND_SRT_IR_EVIDENCE")
    env = dict(os.environ)
    if not env.get("DISPLAY"):
        sockets = sorted(Path("/tmp/.X11-unix").glob("X[0-9]*"))
        if sockets:
            env["DISPLAY"] = ":" + sockets[0].name[1:]
    env["XDG_DATA_HOME"] = str(m.EXP / "xdg-data")
    (m.EXP / "xdg-data").mkdir(exist_ok=True, parents=True)
    command = ["timeout", "-s", "TERM", "-k", "2s", "118s",
               str(elf), "--game", str(m.GAME), "--fullscreen", "true", "--show-fps"]
    (m.OUT / "game-command.json").write_text(json.dumps(command) + "\n")
    with (m.OUT / "game-console.log").open("w") as stream:
        process = subprocess.Popen(command, cwd=m.BUILD, env=env,
                                   stdout=stream, stderr=subprocess.STDOUT)
        m.STATE["tested_games"] = ["CUSA11456"]
        start = time.monotonic()
        for sec in (16, 35, 55, 85, 112):
            while process.poll() is None and time.monotonic() < start + sec:
                time.sleep(min(0.5, max(0.01, start + sec - time.monotonic())))
            if process.poll() is not None:
                break
            m.capture_screen(sec, env)
        try:
            exit_code = process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                exit_code = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                exit_code = process.wait(timeout=5)
    m.STATE["game_exit"] = exit_code
    log = (m.OUT / "game-console.log").read_text(errors="replace")
    patterns = ("SRT_UNRESOLVED_PROVENANCE", "SRT_UNRESOLVED_SUMMARY",
                "SRT dependency resolution stalled", "Sharp source was not flatenned",
                "GuestIndexedImageDimensions:", "Indexed sampler table:",
                "Invalid Vulkan descriptor write", "ErrorDeviceLost", "context is lost")
    counts = {term: log.count(term) for term in patterns}
    (m.OUT / "root-cause-key-events.log").write_text("\n".join(
        line[:1500] for line in log.splitlines()
        if any(term in line for term in patterns))[:120000] + "\n")
    m.STATE["shader_root_evidence_counts"] = counts
    m.STATE["screenshots"] = {p.name: m.sha256(p) for p in m.OUT.glob("menu-*.png")}
    (m.OUT / "root-cause-counts.json").write_text(json.dumps(counts, indent=2) + "\n")
    print("SRT_UNRESOLVED_PROVENANCE_EVENTS=" +
          str(counts["SRT_UNRESOLVED_PROVENANCE"]), flush=True)
    print("GAME_EXIT=" + str(exit_code), flush=True)
    print("SCREENSHOTS=" + str(len(m.STATE["screenshots"])), flush=True)
    if exit_code not in (0, 124):
        raise m.Abort("GOT_CRASH_IN_SRT_PROVENANCE_TRIAL", 10)
    if counts["SRT dependency resolution stalled"] and not counts["SRT_UNRESOLVED_PROVENANCE"]:
        raise m.Abort("NO_SRT_ROOT_PROVENANCE_EMITTED", 10)
    m.stage("SRT_SOURCE_PROVENANCE_CAPTURE_COMPLETE_NO_GFX_SEMANTIC_CHANGE")


def run(m):
    old, srt = preflight(m)
    backup = preserve_progress(m, old)
    m.stage("DOWNLOAD_BYTE_PINNED_READ_ONLY_SRT_SOURCE_PATCH")
    local_patch = m.OUT / "srt-provenance.patch"
    url = ("https://raw.githubusercontent.com/Chreece/shadPS4/" +
           PATCH_COMMIT + "/" + PATCH_PATH)
    m.command("fetch-patch", ["curl", "-4", "--http1.1", "-fLsS", "--retry", "3",
                               url, "-o", str(local_patch)], timeout=120)
    if m.output("patch-git-blob", ["git", "hash-object", str(local_patch)]) != PATCH_BLOB:
        raise m.Abort("SRT_PATCH_GIT_BLOB_MISMATCH", 2)
    m.command("source-patch-check", ["git", "-C", str(m.SRC),
                                      "apply", "--check", str(local_patch)])
    m.stage("APPLY_SRT_LOGGING_ONLY_TO_ISOLATED_SOURCE")
    m.command("source-patch-apply", ["git", "-C", str(m.SRC), "apply", str(local_patch)])
    if m.sha256(srt) != SRT_DIAGNOSTIC_SHA:
        raise m.Abort("SRT_PROVENANCE_SOURCE_HASH_MISMATCH", 2)
    m.output("source-patch-diff-check", ["git", "-C", str(m.SRC), "diff", "--check"])
    m.STATE["srt_instrumented_sha256"] = SRT_DIAGNOSTIC_SHA
    m.stage("NATIVE_INCREMENTAL_CPP_COMPILE_AND_LINK")
    m.command("native-build-srt-source", [
        "cmake", "--build", str(m.BUILD), "--parallel", "8", "--", "-k", "0"
    ], timeout=3600, show=True)
    elf = m.BUILD / "shadps4"
    if not elf.is_file() or not os.access(elf, os.X_OK):
        raise m.Abort("SRT_INSTRUMENTED_BINARY_NOT_LINKED", 6)
    m.STATE["diagnostic_candidate_sha256"] = m.sha256(elf)
    if m.sha256(backup) != PROGRESS_ELF:
        raise m.Abort("PRESERVED_GAMEPLAY_ELF_CHANGED_AFTER_LINK", 2)
    m.stage("NATIVE_SRT_SOURCE_LINK_PASS_PRIOR_PLAYABLE_ELF_VERIFIED")
    m.offload_if_needed()
    capture_game(m, elf)


def main():
    m = None
    result = 0
    try:
        m = base_runner()
        run(m)
    except BaseException as error:
        if m is not None and isinstance(error, m.Abort):
            m.STATE["status"] = error.status
            result = error.code
        else:
            result = 9
            if m is not None:
                m.STATE["status"] = "SRT_RUNNER_UNEXPECTED_EXCEPTION"
                (m.OUT / "unexpected-python-error.txt").write_text(traceback.format_exc())
        print("TRIAL_STOP=" + str(error), file=sys.stderr, flush=True)
    finally:
        if m is not None:
            m.finish(result)
    return result


if __name__ == "__main__":
    sys.exit(main())
