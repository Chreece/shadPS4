#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Audit omitted proven GoW/GoT sources; optionally A/B a protected GoW ELF.

No source edits, GPU resets, services, installed ES-DE changes, or process kills.
This is a binary/source provenance check, not a claim of restored gameplay.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import tarfile
import time

ROOT = Path.home() / "projects/shadps4"
ESDE = ROOT / "ESDE"
LOGS = ROOT / "logs"
SRC = ESDE / "fullstack-audit-20261019-165723/source"
NEW = ESDE / "fullstack-audit-20261019-165723/build/shadps4"
OLD = ROOT / ("GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/"
              "shadps4.fullgfx-uffd-batched-read-gow-uffd-batched-read-20261017-231412")
OBSERVER = ROOT / "scripts/esde_unified_game_observer_20261019.py"
GOW_ROOT = ROOT / "GodOfWarRagnarok"
GHOST_REF = "e9f470b359845a456139a865aa1d7b3001f0dbfd"
RENDERER = "src/video_core/renderer_vulkan/vk_rasterizer.cpp"
RENDERER_SHA = "bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a"
NEW_SHA = "48ae4ac2ec8f42e2155425521c8699359b466dd6459905948762de539865b5bc"
OLD_SHA = "8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef"
INPUT_PROFILE = ESDE / "playtest-CUSA34384-20261019-185051/user"

def sha(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def execute(*args, check=True, timeout=150):
    proc = subprocess.run(list(map(str, args)), text=True, capture_output=True,
                          timeout=timeout)
    if check and proc.returncode:
        raise RuntimeError(
            f"Command failed rc={proc.returncode}: {args[0]}; "
            f"{proc.stderr[-1000:]}")
    return proc

def git(repo, *args, **kw):
    return execute("git", "-C", repo, *args, **kw)

def renderer_from_workspaces():
    """Read exact local files only; skip build, profiles and enormous trees."""
    found = []
    examined = 0
    for top in (GOW_ROOT / "tests", GOW_ROOT / "builds"):
        if not top.is_dir():
            continue
        for base, dirs, files in os.walk(top, followlinks=False):
            # No traversing .git, submodules, shader caches or emulated data.
            dirs[:] = [d for d in dirs if d not in (
                ".git", "build", "logs", "play", "shader", "user",
                "externals", "_deps", "bin-backups", "data", "savedata",
                "cache", "generated", "screenshots", "captures")]
            examined += 1
            if examined > 16000:
                return found, "DIRECTORY_SCAN_LIMIT_REACHED"
            if "vk_rasterizer.cpp" not in files:
                continue
            file = Path(base) / "vk_rasterizer.cpp"
            if not file.is_file() or file.is_symlink():
                continue
            if file.stat().st_size > 3_000_000:
                continue
            digest = sha(file)
            if digest == RENDERER_SHA:
                found.append(str(file))
                if len(found) >= 10:
                    return found, "STOPPED_AT_TEN_MATCHES"
    return found, "COMPLETE"

def renderer_from_git_history():
    """Search only reachable Git blobs for the SHA of the proven renderer."""
    bases = [
        GOW_ROOT / "tests/native-rendered-gfx-20261016-152535-3014679/source",
        GOW_ROOT / "tests/gow-renderer-log-hotpath-20261017/source",
    ]
    matches = []
    searches = []
    for root in bases:
        if not (root / ".git").exists():
            continue
        commits = git(root, "log", "--all", "--format=%H",
                      "--", RENDERER, timeout=35).stdout.splitlines()
        seen = set()
        for rev in commits[:400]:
            try:
                blob = git(root, "rev-parse", rev + ":" + RENDERER).stdout.strip()
            except RuntimeError:
                continue
            if blob in seen:
                continue
            seen.add(blob)
            info = execute("git", "-C", root, "cat-file", "-s", blob)
            if int(info.stdout.strip()) > 3_000_000:
                continue
            sample = subprocess.run(["git", "-C", str(root), "cat-file", "blob", blob],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if sample.returncode == 0 and hashlib.sha256(sample.stdout).hexdigest() == RENDERER_SHA:
                matches.append({"git_root":str(root), "commit":rev, "blob":blob})
        searches.append({"git_root":str(root),
                         "commits_considered":min(len(commits),400),
                         "blobs_checked":len(seen)})
    return matches, searches

def check_missing_proven_gpu_contracts():
    """The old *rendered* build had five checksummed GPU/UFFD files.

    Source evidence: preserved gow-audio3d-build-worker-20261019-103251 log.
    A contract comparison is not proof that old code works unchanged with
    upstream's newer C++ API; missing requirements are an actionable port list.
    """
    def exists(relative):
        path = SRC / relative
        return path.read_text(errors="replace") if path.is_file() else ""
    raster = exists("src/video_core/renderer_vulkan/vk_rasterizer.cpp")
    raster_h = exists("src/video_core/renderer_vulkan/vk_rasterizer.h")
    page = exists("src/video_core/page_manager.cpp")
    page_h = exists("src/video_core/page_manager.h")
    gnm = exists("src/core/libraries/gnmdriver/gnmdriver.cpp")
    keys = {
        "gpu_deferred_invalidate_method":
            "Rasterizer::InvalidateMemoryOnGpuThread" in raster,
        "gpu_deferred_invalidate_decl":
            "InvalidateMemoryOnGpuThread(" in raster_h,
        "gpu_pending_invalidate_drain":
            "page_manager.DrainPendingInvalidations();" in raster,
        "gpu_drain_api_declaration":
            "void DrainPendingInvalidations();" in page_h,
        "gpu_pending_invalidate_memory_state":
            "pending_gpu_invalidations" in page,
        "gpu_lifetime_liverpool_before_presenter":
            (gnm.find("std::unique_ptr<AmdGpu::Liverpool> liverpool;") >= 0 and
             gnm.find("std::unique_ptr<Vulkan::Presenter> presenter;") >= 0 and
             gnm.find("std::unique_ptr<AmdGpu::Liverpool> liverpool;") <
             gnm.find("std::unique_ptr<Vulkan::Presenter> presenter;")),
        "shader_proven_write_extent":
            "ComputeProvenWriteSize(" in raster,
        "texture_gpu_dirty_tracking":
            "texture_cache.InvalidateMemoryFromGPU(" in raster,
    }
    return {"required_contracts":keys,
            "missing":sorted(k for k,v in keys.items() if not v),
            "renderer_current_sha256":sha(SRC/"src/video_core/renderer_vulkan/vk_rasterizer.cpp"),
            "historical_fullgfx_renderer_sha256":RENDERER_SHA,
            "historical_five_file_sources":{
                "renderer":"bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a",
                "page_manager":"8ddd21535bd66135f98fe8a855528e2e409048873a0fdcc41bcc5312f929ce21",
                "gnmdriver":"12ae19b6effe8e020e9c5a1f47b886dbc790818d1610566be6c28facd572f62e",
                "page_manager_header":"f5a661ef28f7b00b5c513da33897a31487990a55f2646f2bb4cab7883c4c7511",
                "rasterizer_header":"889759b700915f2076ee95ce4af50c5fc8c0c90fee5901c03ec73bff1b165992",
            }}

def reconstruct_renderer_from_proven_sources(tag):
    """Replay exact Oct17 working GoW graphics+UFFD files, but never install.

    Its already-vetted restoration helper is bundled by Git SHA.
    All four 3-way ports are computed in memory/plan files only.
    """
    run = ESDE / ("fullgfx-recovery-" + tag)
    run.mkdir(parents=True, exist_ok=True)
    before_path = (GOW_ROOT /
        "tests/native-rendered-gfx-20261016-152535-3014679/source/" / RENDERER)
    header = (GOW_ROOT /
        "tests/gow-renderer-log-hotpath-20261017/source/"
        "src/video_core/buffer_cache/buffer_cache.h")
    expected_old = "dd2a54cb630d118ea273fc92ac90af611ac2b84e9ec2cca7e65c357101818f58"
    if sha(before_path) != expected_old:
        return {"status":"ORIGINAL_PROVEN_GRAPHICS_PREIMAGE_DIFFERENT",
                "found":sha(before_path), "expected":expected_old}
    if not header.is_file() or header.is_symlink():
        return {"status":"ORIGINAL_BUFFER_HEADER_NOT_AVAILABLE"}
    # Three exact Git blobs from the vetted Oct19 preservation runner,
    # committed here without alteration and rechecked on this host.
    inputs = {
      "historical-renderer.cpp":(
        "tools/development/proven_sources_20261019/src/video_core/renderer_vulkan/vk_rasterizer_uffd_historical.cpp",
        "2c8cbda6454aaadd76c060158342e017d60d8127"),
      "restore.py":(
        "tools/development/proven_sources_20261019/tools/gow_restore_graphics_renderer_20261017.py",
        "0bb5acf3149c53e58fb4739845fc33538ed32ff8"),
      "support.py":(
        "tools/development/proven_sources_20261019/tools/gow_preserve_uffd_support_20261017.py",
        "5e8d17492c592421af95413ba8e00610b66aa255"),
    }
    commit = "e0cde5a0fb33ab29ac8f7e0ccaef01f8939f3c09"
    paths={}
    for name,(relative,blob) in inputs.items():
        url = "https://raw.githubusercontent.com/Chreece/shadPS4/" + commit + "/" + relative
        received=execute("curl","-4","-fLsS","--retry","3",
                         "--connect-timeout","20","--max-time","90",url,
                         timeout=105).stdout.encode("utf-8")
        observed=hashlib.sha1(("blob "+str(len(received))+"\0").encode()+received).hexdigest()
        if observed!=blob:
            raise RuntimeError("PROVEN_RECOVERY_BLOB_MISMATCH:"+name)
        loc=run/name
        loc.write_bytes(received)
        paths[name]=loc
    if sha(paths["historical-renderer.cpp"]) != (
        "a86fd050d18d3f5b942756bf4e74b69ccb856183f86d526c9346a07f78ab4feb"):
        raise RuntimeError("HISTORICAL_UFFD_RENDERER_HASH_MISMATCH")
    output=run/"reconstructed-fullgfx-renderer.cpp"
    # The original verified helper intentionally requires an existing isolated
    # output file; never pass a path from any protected source tree.
    output.touch(exist_ok=False)
    out=execute(sys.executable,"-B",paths["restore.py"],
                before_path,paths["historical-renderer.cpp"],header,output,
                check=False,timeout=90)
    (run/"reconstruction.log").write_text(out.stdout+out.stderr)
    if out.returncode or sha(output)!=RENDERER_SHA:
        return {"status":"SOURCE_RECONSTRUCTION_REJECTED",
                "reason":(out.stdout+out.stderr)[-7000:],
                "recovered_sha":sha(output),
                "workdir":str(run)}
    support_before={
        "src/core/libraries/gnmdriver/gnmdriver.cpp":
            "3760577a5851bb068e55db2e14346dfa1e62b65af60d3af68fd8458f8919d6a0",
        "src/video_core/page_manager.h":
            "e8e8bdb9bb8330e70a3d753ba626e781df34da5864cc537d078509fe044c0965",
        "src/video_core/renderer_vulkan/vk_rasterizer.h":
            "34703c31672f73dd93ede2a04577365f780fc0d5cd86fc8f8001ba282a3e571e",
    }
    support_after={
        "src/core/libraries/gnmdriver/gnmdriver.cpp":
            "12ae19b6effe8e020e9c5a1f47b886dbc790818d1610566be6c28facd572f62e",
        "src/video_core/page_manager.h":
            "f5a661ef28f7b00b5c513da33897a31487990a55f2646f2bb4cab7883c4c7511",
        "src/video_core/renderer_vulkan/vk_rasterizer.h":
            "889759b700915f2076ee95ce4af50c5fc8c0c90fee5901c03ec73bff1b165992",
    }
    original_root=GOW_ROOT/"tests/native-rendered-gfx-20261016-152535-3014679/source"
    before_run=run/"original"
    after_run=run/"preserved"
    for rel,want in support_before.items():
        src=original_root/rel
        if sha(src)!=want:
            return {"status":"PROVEN_UFFD_SUPPORT_PREIMAGE_CHANGED",
                    "path":rel,"found":sha(src),"expected":want,
                    "recovered_fullgfx_renderer":str(output)}
        for base in (before_run,after_run):
            dest=base/rel
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(src,dest)
    patched=execute(sys.executable,"-B",paths["support.py"],before_run,after_run,
                    run/"support-evidence.json",check=False,timeout=80)
    (run/"support-reconstruction.log").write_text(patched.stdout+patched.stderr)
    if patched.returncode:
        return {"status":"PROVEN_UFFD_SUPPORT_RECONSTRUCTION_FAILED",
                "reason":(patched.stdout+patched.stderr)[-4500:]}
    for rel,expected in support_after.items():
        if sha(after_run/rel)!=expected:
            raise RuntimeError("VERIFIED_UFFD_SUPPORT_POSTIMAGE_MISMATCH:"+rel)
    expected_ports={RENDERER:(before_path,output)}
    for rel in support_before:
        expected_ports[rel]=(before_run/rel,after_run/rel)
    records={}
    planned_root=run/"merged-source-proposals"
    for rel,(base_file,new_file) in expected_ports.items():
        current=SRC/rel
        if not current.is_file() or current.is_symlink():
            records[rel]={"status":"CURRENT_UPSTREAM_TARGET_MISSING"}
            continue
        ours=run/("ours-"+Path(rel).name)
        shutil.copyfile(current,ours)
        result=execute("git","merge-file","-p",ours,base_file,new_file,
                       check=False,timeout=80)
        merge=result.stdout
        item={
            "merge_rc":result.returncode,
            "current_sha256":sha(current),
            "old_sha256":sha(base_file),
            "proven_sha256":sha(new_file),
            "changed":merge.encode()!=current.read_bytes(),
        }
        if result.returncode or "<<<<<<<" in merge:
            item["status"]="THREEWAY_CONFLICT_NEEDS_EXACT_RECONCILIATION"
            item["conflict_excerpt"]=merge[-15000:]
        else:
            proposal=planned_root/rel
            proposal.parent.mkdir(parents=True,exist_ok=True)
            proposal.write_text(merge)
            item["status"]="PREVIEW_CLEAN_NOT_APPLIED"
            item["proposal_sha256"]=sha(proposal)
            item["proposal_path"]=str(proposal)
        records[rel]=item
    return {"status":"RECONSTRUCTED_VERIFIED_GO_W_RENDERER",
            "reconstructed_renderer_sha256":sha(output),
            "recovery_dir":str(run),
            "port_proposals":records,
            "all_proposals_clean":all(v["status"]=="PREVIEW_CLEAN_NOT_APPLIED"
                                      for v in records.values()),
            "compiled_candidate_changed":False,
            "full_graphics_source_exact":True}

def ghost_merge_preview():
    """Fetch pinned reference and preview conflicts; never merge source."""
    if not (SRC / ".git").exists():
        return {"status":"MISSING_FULLSTACK_GIT_SOURCE"}
    if git(SRC, "rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0:
        return {"status":"UNRESOLVED_GIT_MERGE_DO_NOT_PROBE"}
    before = git(SRC, "status", "--porcelain", "--untracked-files=no")
    got = git(SRC, "cat-file", "-e", GHOST_REF + "^{commit}", check=False)
    if got.returncode:
        # Object-only fetch; HEAD/worktree and the compiled source are unchanged.
        execute("git", "-C", SRC, "-c", "http.version=HTTP/1.1",
                "fetch", "--no-tags", "--no-recurse-submodules",
                "https://github.com/Chreece/shadPS4.git", GHOST_REF, timeout=180)
    head = git(SRC, "rev-parse", "HEAD").stdout.strip()
    tree = git(SRC, "merge-tree", "--write-tree", head, GHOST_REF,
               check=False, timeout=120)
    combined = tree.stdout + "\n" + tree.stderr
    entries = re.findall(r'^CONFLICT .*$', combined, re.M)
    after = git(SRC, "status", "--porcelain", "--untracked-files=no")
    if before.stdout != after.stdout:
        raise RuntimeError("Ghost source preview changed tracked worktree unexpectedly")
    return {"status":"MERGE_PREVIEW_ONLY",
            "fullstack_head":head, "ghost_world_ref":GHOST_REF,
            "rc":tree.returncode, "conflicts":entries[:150],
            "conflicts_count":len(entries),
            "output_excerpt":combined[-24000:],
            "compiled_worktree_has_local_overlays": bool(before.stdout.strip())}

def capture_old_gow(report, tag):
    if sha(OLD) != OLD_SHA or sha(NEW) != NEW_SHA:
        raise RuntimeError("One of the two GoW executables differs from pinned SHA")
    if not OBSERVER.is_file() or OBSERVER.is_symlink():
        raise RuntimeError("Pinned generic observer not available")
    if not INPUT_PROFILE.is_dir() or INPUT_PROFILE.is_symlink():
        raise RuntimeError("Original failed GoW portable profile is missing")
    # Historical GPUVM hangs make a reference run unsafe without enough VRAM.
    # Never stop Ollama or modify system services to create headroom.
    amd_gpu = []
    for vendor in Path("/sys/class/drm").glob("card[0-9]*/device/vendor"):
        if vendor.is_file() and vendor.read_text().strip() == "0x1002":
            root = vendor.parent
            total = root / "mem_info_vram_total"
            used = root / "mem_info_vram_used"
            if total.is_file() and used.is_file():
                amd_gpu.append((int(total.read_text()), int(used.read_text())))
    if not amd_gpu or all(total - used < 8 * 1024**3 for total, used in amd_gpu):
        raise RuntimeError("REFERENCE_TRIAL_REQUIRES_8_GIB_FREE_AMD_VRAM_NO_OLLAMA_STOP")
    # Recheck display without mutating Sunshine or running a headless game.
    preferred = os.environ.get("DISPLAY") or ":0"
    displays = [preferred, ":0", ":1", ":2"]
    display = next((d for d in dict.fromkeys(displays)
                    if execute("timeout", "4", "xdpyinfo", "-display", d,
                               check=False).returncode == 0), None)
    if not display:
        raise RuntimeError("NO_X11_DISPLAY_AVAILABLE_REFERENCE_TRIAL_SKIPPED")
    if execute("pgrep", "-u", os.getuid(), "-x", "shadps4",
               check=False).returncode == 0:
        raise RuntimeError("Another emulator is active; do not interrupt it")
    trial = ESDE / ("reference-gow-" + tag)
    tmp_user = trial / "user"
    trial.mkdir()
    # This is a new isolated copy of the SAME config the failed new candidate
    # used, so a difference is more attributable to the binary.
    excluded = {"shader", "log", "logs", "captures", "screenshots", "cache", "dumps"}
    def ignore(directory, names):
        base = Path(directory)
        return [name for name in names
                if (base / name).is_symlink() or name in excluded]
    shutil.copytree(INPUT_PROFILE, tmp_user, symlinks=False, ignore=ignore)
    env = os.environ.copy()
    env["DISPLAY"] = display
    report_dir = LOGS / ("esde-reference-gow-" + tag)
    print("TESTING_PROVEN_GOW_ELF=" + OLD_SHA, flush=True)
    print("CWD=" + str(trial), flush=True)
    print("CONTROLLER_INPUT=MANUAL; CURRENT_ESDE_INSTALL=UNCHANGED", flush=True)
    cmd = [sys.executable, "-B", str(OBSERVER), "--binary", str(OLD),
           "--sha256", OLD_SHA, "--game", "CUSA34384",
           "--cwd", str(trial), "--report", str(report_dir),
           "--seconds", "90"]
    with (trial / "launch-command.json").open("w") as f:
        json.dump(cmd, f, indent=2)
    rc = subprocess.run(cmd, env=env).returncode
    if report_dir.is_dir():
        archive = LOGS / ("esde-reference-gow-" + tag + ".tar.gz")
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(report_dir, arcname=report_dir.name)
        print("UPLOAD_REFERENCE=" + str(archive), flush=True)
        try:
            outcome = json.loads((report_dir / "gameplay-result.json").read_text())
            report["reference_result"] = {
                "observer_rc":rc, "game_exit":outcome.get("exit_code"),
                "process_alive_at_end":outcome.get("process_alive_when_reported"),
                "failure":outcome.get("failure"),
            }
        except (OSError, ValueError):
            report["reference_result"] = {"observer_rc":rc}
    return rc


def capture_old_got(report: dict, stamp: str):
    """Use the known horse-visible Oct 17 binary, but only if archive proves its SHA.

    This baseline predates --cpu-id-mode. Reusing the GoW observer's hardcoded
    --cpu-id-mode builtin would be an invalid comparison.
    """
    got = ROOT / "GhostOfTsushima"
    commit = "e9f470b359845a456139a865aa1d7b3001f0dbfd"
    binary = (got / "tests/got-upstream-menu-main-20261017-cached/bin-backups" /
              ("shadps4-got-graphics-and-hotplug-" + commit))
    evidence = LOGS / "got-gow-shreg-table-20261017-205408.tar.gz"
    game = Path("/mnt/roms-all/ps4-games/CUSA11456/eboot.bin")
    result = {"binary": str(binary), "archive": str(evidence), "commit": commit}
    report["ghost_reference"] = result
    if not evidence.is_file() or evidence.is_symlink():
        raise RuntimeError("HORSE_VISIBLE_OCT17_ARCHIVE_MISSING; no unverified binary fallback")
    # The old runner wrote sha256sum of its saved executable to info/restore.txt.
    # Inspect archived checksums, never execute archived shell programs.
    proofs = set()
    runner_pin = False
    with tarfile.open(evidence, "r:gz") as archive:
        for entry in archive:
            if not entry.isfile() or entry.size > 4_000_000:
                continue
            name = Path(entry.name).name
            if name not in ("restore.txt", "final.txt", "source-check.txt") and not name.endswith("worker.sh"):
                continue
            stream = archive.extractfile(entry)
            if stream is None:
                continue
            body = stream.read().decode("utf-8", "replace")
            if name.endswith("worker.sh") and commit in body:
                runner_pin = True
            for line in body.splitlines():
                if binary.name in line:
                    proofs.update(re.findall(r"\b[0-9a-f]{64}\b", line))
    result["archived_sha256_proofs"] = sorted(proofs)
    result["runner_commit_verified"] = runner_pin
    result["backup_exists"] = binary.is_file() and not binary.is_symlink()
    if not runner_pin:
        raise RuntimeError("OCT17_ARCHIVED_GHOST_RUNNER_DOES_NOT_PIN_EXPECTED_COMMIT")
    if not result["backup_exists"] or not os.access(binary, os.X_OK):
        raise RuntimeError("OCT17_HORSE_VISIBLE_EXECUTABLE_NOT_SAVED_AT_EXPECTED_BACKUP_PATH")
    actual = sha(binary)
    result["actual_sha256"] = actual
    if len(proofs) != 1 or actual not in proofs:
        raise RuntimeError("OCT17_BACKUP_SHA_DOES_NOT_MATCH_ARCHIVED_EXECUTABLE_PROOF")
    with binary.open("rb") as handle:
        if handle.read(5) != b"\x7fELF\x02":
            raise RuntimeError("OCT17_GHOST_BACKUP_IS_NOT_64BIT_ELF")
    result["binary_archive_provenance"] = "PASS"
    if not game.is_file() or game.is_symlink():
        raise RuntimeError("GHOST_CUSA11456_EBOOT_MISSING")
    if execute("pgrep", "-u", os.getuid(), "-x", "shadps4", check=False).returncode == 0:
        raise RuntimeError("ANOTHER_EMULATOR_RUNNING; do not disturb")
    # Safety based on the user's previous Ghost GPU hang/host-freeze logs.
    candidates = []
    for vendor in Path("/sys/class/drm").glob("card[0-9]*/device/vendor"):
        try:
            if vendor.read_text().strip() != "0x1002":
                continue
            d = vendor.parent
            candidates.append((int((d/"mem_info_vram_total").read_text()),
                               int((d/"mem_info_vram_used").read_text())))
        except (OSError, ValueError):
            continue
    if not candidates:
        raise RuntimeError("AMD_VRAM_TELEMETRY_UNAVAILABLE")
    total,used = max(candidates, key=lambda pair:pair[0])
    result["free_vram_gib"] = round((total-used)/1024**3,2)
    if total-used < 8*1024**3:
        raise RuntimeError("GHOST_REFERENCE_NEEDS_8_GIB_FREE_VRAM; OLLAMA_UNCHANGED")
    if shutil.disk_usage(ESDE).free < 3*1024**3:
        raise RuntimeError("GHOST_REFERENCE_NEEDS_3_GIB_FREE_DISK")
    display = None
    for d in dict.fromkeys([os.environ.get("DISPLAY"), ":0", ":1", ":2"]):
        if d and execute("timeout","5","xdpyinfo","-display",d,check=False).returncode==0:
            display=d
            break
    if not display:
        raise RuntimeError("NO_WORKING_X11_DISPLAY")
    # Reuse the old horse-visible run's profile; the source is never edited.
    source = got/"play/np-init-affinity-main-20261015-195552/user"
    if not source.is_dir() or source.is_symlink():
        source = ESDE/"playtest-CUSA11456-20261019-185128/user"
    if not source.is_dir() or source.is_symlink():
        raise RuntimeError("NO_KNOWN_GHOST_USER_PROFILE_TO_COPY")
    trial = ESDE/("got-horse-visible-reference-"+stamp)
    trial.mkdir()
    dest=trial/"user"
    dest.mkdir()
    count=bytes_total=0
    excluded={"log","logs","screenshots","captures","dumps","download","tmp"}
    for base, dirs, filenames in os.walk(source,followlinks=False):
        base=Path(base)
        dirs[:]=[name for name in dirs
                 if not (base/name).is_symlink()
                 and not (base==source and name.lower() in excluded)
                 and name not in (".git",".cache")]
        for filename in filenames:
            old=base/filename
            if not old.is_file() or old.is_symlink():
                continue
            count+=1
            bytes_total+=old.stat().st_size
            if count>100_000 or bytes_total>2*1024**3:
                raise RuntimeError("COPIED_PROFILE_SIZE_LIMIT_2GIB_REACHED")
            new=dest/old.relative_to(source)
            new.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(old,new)
    # Clear only cloned config's absolute guest HOME/firmware overrides.
    cfg=dest/"config.json"
    if cfg.is_file():
        config=json.loads(cfg.read_text())
        if not isinstance(config,dict) or not isinstance(config.get("General",{}),dict):
            raise RuntimeError("INVALID_GHOST_CONFIG_IN_CLONE")
        for key in ("home_dir","sys_modules_dir","font_dir","addon_install_dir"):
            if key in config.get("General",{}):
                if not isinstance(config["General"][key],str):
                    raise RuntimeError("UNKNOWN_ABSOLUTE_GHOST_CONFIG_PATH_TYPE")
                config["General"][key]=""
        if isinstance(config.get("Input",{}),dict):
            config.setdefault("Input",{})["background_controller_input"]=True
        cfg.write_text(json.dumps(config,ensure_ascii=False,indent=2)+"\n")
    result["isolated_profile"]={"source":str(source),"copy":str(dest),
                                 "files":count,"size_bytes":bytes_total}
    env=os.environ.copy()
    env["HOME"]=str(trial)
    env["DISPLAY"]=display
    env.setdefault("XDG_RUNTIME_DIR",f"/run/user/{os.getuid()}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS",f"unix:path=/run/user/{os.getuid()}/bus")
    env.update({"SHADPS4_GUEST_TSHARP_FEEDBACK":"1",
                "SHADPS4_GUEST_TSHARP_CENSUS":"1",
                "SHADPS4_RENDER_AUDIT":"0",
                "SHADPS4_RESOURCE_AUDIT":"1",
                "SHADPS4_IMAGE_PHI_SPLIT_AUDIT":"1",
                "SHADPS4_INDEXED_IMAGE_AUDIT":"1"})
    for name in ("VK_DRIVER_FILES","VK_ICD_FILENAMES","VK_INSTANCE_LAYERS","RADV_DEBUG"):
        env.pop(name,None)
    # October17 Ghost lacks --cpu-id-mode. Use its original launch form.
    command=[str(binary),"--game",str(game),"--fullscreen","true","--show-fps"]
    result["launch_command"]=command
    out=LOGS/("esde-reference-got-"+stamp)
    out.mkdir(exist_ok=False)
    (out/"launch-command.json").write_text(json.dumps(command,indent=2)+"\n")
    screenshots=out/"screenshots"
    screenshots.mkdir()
    recorded=set()
    started=time.monotonic()
    print("GOT_REFERENCE_BINARY_SHA256="+actual,flush=True)
    print("GOT_REFERENCE_SOURCE=e9f470b_PARTIAL_WORLD",flush=True)
    print("MANUAL_CONTROLLER=ENTER_WORLD_AND_CHECK_HORSE",flush=True)
    with (out/"full-console.log").open("wb",buffering=0) as log:
        process=subprocess.Popen(command,cwd=trial,env=env,stdin=subprocess.DEVNULL,
                                 stdout=log,stderr=subprocess.STDOUT,
                                 start_new_session=True)
        result["pid"]=process.pid
        for elapsed in range(135):
            if process.poll() is not None:
                break
            if elapsed in (30,60,90,120) and elapsed not in recorded:
                recorded.add(elapsed)
                if shutil.which("xdotool") and shutil.which("import"):
                    windows=execute("xdotool","search","--onlyvisible","--pid",
                                    process.pid,check=False)
                    ids=[x for x in windows.stdout.splitlines() if x.isdigit()]
                    if ids:
                        execute("timeout","8","import","-display",display,
                                "-window",ids[-1],screenshots/(str(elapsed)+".png"),
                                check=False)
            # Stop only THIS exact spawned process group if the GPU reports
            # device loss. Never signal other emulators, Sunshine, or Ollama.
            if elapsed%3==0:
                try:
                    with (out/"full-console.log").open("rb") as current:
                        current.seek(0,2)
                        current.seek(max(0,current.tell()-20000))
                        tail=current.read().lower()
                    if any(marker in tail for marker in
                           (b"gpuvm fault detected",b"vk_error_device_lost",
                            b"gpu hang detected",b"amdgpu ring timeout")):
                        result["gpu_guard"]="DEVICE_LOST_TERMINATE_OWN_TEST_PROCESS"
                        os.killpg(process.pid,signal.SIGTERM)
                        break
                except (FileNotFoundError,ProcessLookupError):
                    break
            time.sleep(1)
    result["process_alive_after_capture"]=process.poll() is None
    result["game_exit"]=process.poll()
    result["capture_seconds"]=round(time.monotonic()-started,2)
    result["screenshots"]=sorted(p.name for p in screenshots.glob("*.png"))
    result["visual_state"]="MANUAL_USER_ASSESSMENT_REQUIRED"
    archive=LOGS/("esde-reference-got-"+stamp+".tar.gz")
    with tarfile.open(archive,"w:gz") as tf:
        for member in ("full-console.log","launch-command.json"):
            tf.add(out/member,arcname=member)
        for shot in screenshots.glob("*.png"):
            tf.add(shot,arcname="screenshots/"+shot.name)
    print("UPLOAD_GOT_REFERENCE="+str(archive),flush=True)
    if result["process_alive_after_capture"]:
        print("REFERENCE_PROCESS_STILL_RUNNING_USE_HOME_PS_TO_EXIT",flush=True)
    print("GOT_MANUAL_VISUAL_CHECK=HORSE_WORLD_VS_BLACK_SCREEN",flush=True)
    return 0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-gow", action="store_true",
                        help="Run SHA-pinned proven GoW ELF using the failed build's cloned profile")
    parser.add_argument("--reference-got", action="store_true",
                        help="Run Oct17 horse-visible Ghost ELF only if original archive verifies its SHA")
    args = parser.parse_args()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS.mkdir(parents=True, exist_ok=True)
    report = {"gameplay_claim":False, "installed_esde_changed":False,
              "candidate_sha":sha(NEW), "candidate_sha_expected":NEW_SHA,
              "gow_reference_sha":sha(OLD), "gow_reference_sha_expected":OLD_SHA,
              "missing_proven_renderer_sha256":RENDERER_SHA}
    result = 0
    try:
        if args.reference_got:
            result=capture_old_got(report,stamp)
        else:
            report["goW_proven_gpu_contract_parity"] = check_missing_proven_gpu_contracts()
            recovery = reconstruct_renderer_from_proven_sources(stamp)
            report["goW_exact_fullgfx_renderer_recovery"] = recovery
            if recovery.get("status") == "RECONSTRUCTED_VERIFIED_GO_W_RENDERER":
                # The SHA-pinned historical/restorer replay is stronger proof than
                # hunting uncommitted/overwritten local worktrees. No slow scan.
                report["renderer_search"] = {
                    "local": [recovery.get("recovery_dir","") +
                              "/reconstructed-fullgfx-renderer.cpp"],
                    "status": "EXACT_CHECKSUM_RECONSTRUCTED_NO_HISTORY_SCAN",
                    "git": [], "git_stats": [],
                }
            else:
                found,status = renderer_from_workspaces()
                report["renderer_search"] = {"local":found, "status":status}
                found_git,stats = renderer_from_git_history()
                report["renderer_search"]["git"] = found_git
                report["renderer_search"]["git_stats"] = stats
            report["ghost_merge_preview"] = ghost_merge_preview()
            if args.reference_gow:
                result = capture_old_gow(report, stamp)
    except Exception as e:
        result = 2
        report["error"] = str(e)
        print("AUDIT_ERROR=" + str(e), flush=True)
    destination = LOGS / ("esde-fullstack-regression-triage-" + stamp + ".json")
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    archive = destination.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(destination, arcname="triage/manifest.json")
    print("RENDERER_SOURCE_MATCHES=" +
          str(len(report.get("renderer_search",{}).get("local",[])) +
              len(report.get("renderer_search",{}).get("git",[]))), flush=True)
    print("GHOST_MERGE_PREVIEW_CONFLICTS=" +
          str(report.get("ghost_merge_preview",{}).get("conflicts_count","UNAVAILABLE")),
          flush=True)
    print("UPLOAD_THIS=" + str(archive), flush=True)
    print("SOURCES_SAVES_ESDE_SUNSHINE_SSH_UNCHANGED=YES", flush=True)
    return result

if __name__ == "__main__":
    sys.exit(main())
