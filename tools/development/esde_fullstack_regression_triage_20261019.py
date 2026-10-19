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
import shutil
import subprocess
import sys
import tarfile

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-gow", action="store_true",
                        help="Run SHA-pinned proven GoW ELF using the failed build's cloned profile")
    args = parser.parse_args()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    LOGS.mkdir(parents=True, exist_ok=True)
    report = {"gameplay_claim":False, "installed_esde_changed":False,
              "candidate_sha":sha(NEW), "candidate_sha_expected":NEW_SHA,
              "gow_reference_sha":sha(OLD), "gow_reference_sha_expected":OLD_SHA,
              "missing_proven_renderer_sha256":RENDERER_SHA}
    result = 0
    try:
        report["goW_proven_gpu_contract_parity"] = check_missing_proven_gpu_contracts()
        report["goW_exact_fullgfx_renderer_recovery"] = reconstruct_renderer_from_proven_sources(stamp)
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
