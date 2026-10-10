#!/usr/bin/env python3
"""Build and capture the guarded GoW Phi/SRT/DMA trial without disturbing ES-DE or Ghost.

Standard library only. Changes to the verified source/build tree are temporary and
restored in finally, with the previous executable and source contents backed up.
GitHub full tests are never invoked. Refuses to start with an active emulator/build.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.request

HOME = Path.home()
SOURCE = HOME / "shadps4-esde-verified-builds/20261009-173836/source"
BUILD_ROOT = HOME / "shadps4-esde-verified-builds"
GAME = "CUSA34384"
SHADER = "57b077ac"
BASE_SHA = "aa5b281c0016d64844e784566ef9dd092655ba8b"
HEAD_SHA = "0213806ffc77403b412dfed939f975914d85b032"
PATCH_URL = (f"https://api.github.com/repos/Chreece/shadPS4/compare/"
             f"{BASE_SHA}...{HEAD_SHA}")
REQUIRED = {
    "src/core/libraries/kernel/process.cpp",
    "src/shader_recompiler/backend/spirv/emit_spirv.cpp",
    "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp",
    "src/shader_recompiler/frontend/translate/data_share.cpp",
    "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp",
    "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp",
}
TIME_LIMIT = 120

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()

def run(args, *, cwd=None, timeout=30):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)

def processes_in_use(exclude=(), exclude_group=None):
    problems = []
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        pid = int(item.name)
        if pid == os.getpid() or pid in exclude:
            continue
        if exclude_group is not None:
            try:
                if os.getpgid(pid) == exclude_group:
                    continue
            except (ProcessLookupError, PermissionError):
                pass
        try:
            if item.stat().st_uid != os.getuid():
                continue
            cmd = (item / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            name = (item / "comm").read_text().strip().lower()
            if name.startswith("shadps4") or re.search(r"(?<![\w-])shadps4(?:\s|$)", cmd.lower()):
                problems.append({"pid": pid, "reason": "emulator", "command": cmd[:140]})
            elif name in ("ninja", "cmake", "c++", "cc1plus") and "shadps4" in cmd.lower():
                problems.append({"pid": pid, "reason": "build", "command": cmd[:140]})
        except (OSError, PermissionError, RuntimeError):
            pass
    return problems

def find_build():
    caches = sorted(BUILD_ROOT.glob("*/build/CMakeCache.txt"), reverse=True)
    for cache in caches:
        try:
            match = re.search(r"^CMAKE_HOME_DIRECTORY:INTERNAL=(.*)$",
                              cache.read_text(errors="replace"), flags=re.MULTILINE)
            if match and Path(match.group(1)).resolve() == SOURCE.resolve():
                path = cache.parent
                if (path / "build.ninja").is_file() or (path / "Makefile").is_file():
                    return path
        except OSError:
            pass
    raise RuntimeError("No existing CMake build configured for the verified source; nothing modified")

def get_patch():
    req = urllib.request.Request(PATCH_URL,
                                 headers={"User-Agent": "gow-phi-dma-diagnostics",
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=25) as res:
        payload = json.load(res)
    if payload.get("base_commit", {}).get("sha") != BASE_SHA:
        raise RuntimeError("Unexpected base SHA from GitHub")
    # GitHub's compare REST response exposes the compared head as the final
    # commit, not as a top-level head_commit object. Keep the pin strict.
    commits = payload.get("commits", [])
    if not commits or commits[-1].get("sha") != HEAD_SHA:
        raise RuntimeError("Unexpected last commit SHA in pinned compare")
    if payload.get("total_commits") != len(commits):
        raise RuntimeError("Incomplete or paginated pinned compare result")
    if payload.get("merge_base_commit", {}).get("sha") != BASE_SHA:
        raise RuntimeError("Unexpected merge base for pinned patch")
    files = payload.get("files", [])
    # The pinned comparison now includes this diagnostic runner because it
    # was committed before the stack-alignment fix. Never apply runner edits to
    # the verified emulator tree: allow ONLY this known extra file.
    changed = {f["filename"] for f in files}
    if changed != REQUIRED | {"tools/gow_phi_dma_onepaste.py"}:
        raise RuntimeError("Unexpected changed-file set; refusing patch")
    parts = []
    for f in files:
        path = f["filename"]
        if path == "tools/gow_phi_dma_onepaste.py":
            continue
        if f.get("status") != "modified" or not f.get("patch"):
            raise RuntimeError("Missing/unsafe diff for " + path)
        if not path.startswith("src/") or ".." in Path(path).parts:
            raise RuntimeError("Unsafe patch filename")
        parts.append(f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
                     + f["patch"].rstrip("\n") + "\n")
    return "".join(parts).encode()

def stop_owned(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        group = os.getpgid(proc.pid)
        if group == os.getpgrp():
            raise RuntimeError("Refusing to signal the SSH process group")
        os.killpg(group, signal.SIGTERM)
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(group, signal.SIGKILL)
            proc.wait(timeout=8)
    except ProcessLookupError:
        pass

def do_build(build, patch, temp, result):
    backup = temp / "original"
    backup.mkdir()
    originals = {}
    for rel in sorted(REQUIRED):
        src = SOURCE / rel
        if not src.is_file() or src.is_symlink():
            raise RuntimeError(f"Missing or symlinked source: {rel}")
        originals[rel] = (src.read_bytes(), src.stat().st_mode)
    result["source_hashes_before"] = {
        rel: hashlib.sha256(blob).hexdigest() for rel, (blob, _) in originals.items()
    }
    target = build / "shadps4"
    executable_backup = backup / "shadps4"
    had_target = target.is_file()
    if had_target:
        shutil.copy2(target, executable_backup)
        result["build_binary_sha256_before"] = sha(target)
    (temp / "patch.diff").write_bytes(patch)
    patch_path = temp / "patch.diff"
    changed = False
    proc = None
    trial_binary = temp / "trial" / "shadps4"
    try:
        check = run(["git", "apply", "--check", "--whitespace=nowarn",
                     str(patch_path)], cwd=SOURCE)
        if check.returncode:
            raise RuntimeError("Patch does not apply cleanly to verified source: "
                               + check.stderr[-2600:])
        changed = True  # Restore originals even if applying a checked patch partially fails.
        applied = run(["git", "apply", "--whitespace=nowarn", str(patch_path)], cwd=SOURCE)
        if applied.returncode:
            raise RuntimeError("git apply failed: " + applied.stderr[-2600:])
        command = ["cmake", "--build", str(build), "--target", "shadps4", "--parallel", "4"]
        with (temp / "build.log").open("wb") as logfile:
            proc = subprocess.Popen(command, cwd=SOURCE, stdout=logfile,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
            result["build_pid"] = proc.pid
            result["build_exit_code"] = proc.wait(timeout=900)
        if result["build_exit_code"] != 0:
            raise RuntimeError("Focused local build failed (see build.log)")
        if not target.is_file():
            raise RuntimeError("Build completed but shadps4 binary missing")
        # The Linux CPU-identity loader finds DynamoRIO next to /proc/self/exe.
        # An emulator binary without its matching cpu-id-runtime package cannot
        # reliably start --cpu-id-mode auto. Copy the bundle produced by this build.
        runtime = build / "cpu-id-runtime"
        required_runtime = ("bin64/drrun", "libshadps4_cpu_id.so",
                            "lib64/release/libdynamorio.so")
        missing = [rel for rel in required_runtime if not (runtime / rel).is_file()]
        if missing:
            raise RuntimeError("Bundled CPU identity runtime missing: " + ", ".join(missing))
        trial_binary.parent.mkdir(parents=True)
        shutil.copy2(target, trial_binary)
        shutil.copytree(runtime, trial_binary.parent / "cpu-id-runtime", symlinks=False)
        result["trial_binary_sha256"] = sha(trial_binary)
        result["cpu_runtime_bundled"] = True
        result["cpu_runtime_sha256"] = {
            rel: sha(trial_binary.parent / "cpu-id-runtime" / rel)
            for rel in required_runtime
        }
        return trial_binary
    finally:
        stop_owned(proc)
        # Exact-byte restoration, regardless of build status or interruption.
        if changed:
            for rel, (data, mode) in originals.items():
                path = SOURCE / rel
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".gow-restore-",
                                                 delete=False) as temp_file:
                    os.fchmod(temp_file.fileno(), mode & 0o7777)
                    temp_file.write(data)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                    tmp_path = Path(temp_file.name)
                os.replace(tmp_path, path)
        result["sources_restored"] = all(
            (SOURCE / rel).read_bytes() == data for rel, (data, _) in originals.items()
        )
        if had_target:
            shutil.copy2(executable_backup, target)
            result["build_binary_restored"] = sha(target) == result["build_binary_sha256_before"]
        else:
            if target.exists():
                target.unlink()
            result["build_binary_restored"] = not target.exists()

def validate_spv(directory, evidence, result):
    files = sorted(directory.glob("*57b077ac*.spv"))
    result["spv"] = []
    for item in files:
        data = item.read_bytes()
        valid_header = len(data) >= 20 and len(data) % 4 == 0 and \
                       struct.unpack_from("<I", data, 0)[0] == 0x07230203
        result["spv"].append({"file": item.name, "size": len(data),
                              "sha256": sha(item), "header_valid": valid_header})
        if valid_header:
            for tool in ("spirv-val", "spirv-dis"):
                if not shutil.which(tool):
                    continue
                cmd = ([tool, "--target-env", "vulkan1.3", str(item)]
                       if tool == "spirv-val"
                       else [tool, str(item), "-o", str(evidence / (item.name + ".spvasm"))])
                try:
                    p = run(cmd, timeout=30)
                    (evidence / (item.name + "." + tool + ".txt")).write_text(
                        f"exit={p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}")
                    result.setdefault("spv_tools", {})[tool] = p.returncode
                except Exception as exc:
                    result.setdefault("spv_tools", {})[tool] = str(exc)

def trial_run(binary, temp, result):
    # Native settings can be rewritten on startup when config versions differ.
    # Preserve the exact user file while retaining the verified native profile.
    config_path = HOME / ".local/share/shadPS4/config.json"
    if config_path.is_symlink() or not config_path.is_file():
        raise RuntimeError("Native config is missing or symlinked; refusing trial")
    original_config = config_path.read_bytes()
    original_mode = config_path.stat().st_mode & 0o7777
    result["native_config_sha256_before"] = hashlib.sha256(original_config).hexdigest()
    evidence = temp / "evidence"
    dump_dir = evidence / "target_shader"
    dump_dir.mkdir(parents=True)
    log_dir = HOME / ".local/share/shadPS4/log"
    before = {}
    if log_dir.is_dir():
        for p in log_dir.glob("*.log"):
            if p.name in ("shadps4.log", GAME + ".log") and p.is_file():
                st = p.stat()
                before[p.name] = (st.st_ino, st.st_size)
    env = os.environ.copy()
    env.pop("XDG_DATA_HOME", None)
    env.pop("XDG_CACHE_HOME", None)
    env.pop("SHADPS4_CPU_ID_RESTART", None)
    env.pop("SHADPS4_CPU_ID_MODE", None)
    env.pop("SHADPS4_GOW_SAFE_COMPUTE_ONESHOT", None)
    env.update({
        "SHADPS4_ENABLE_IPC": "false",
        "SHADPS4_GOW_ONE_SHADER_DMA_COMPILE": "1",
        "SHADPS4_GOW_SPV_DUMP_DIR": str(dump_dir.resolve()),
        "SHADPS4_GOW_BIND_PROBE": "1",
        "SHADPS4_GOW_IMAGE_TABLE_AUDIT": "1",
        "SHADPS4_GOW_SUPPRESS_GPU_COMPUTE": "1",
        # A single 2x1x1 compute dispatch may pass strict in-emulator guards.
        "SHADPS4_GOW_SAFE_COMPUTE_ONESHOT": "1",
        "SHADPS4_GOW_COMPUTE_CENSUS": "1",
        "SHADPS4_GOW_DIAGNOSTIC_GDS_NONEXECUTING": "1",
    })
    env.setdefault("DISPLAY", ":0")
    command = [str(binary), "--cpu-id-mode", "auto", "--game", GAME, "--fullscreen", "true"]
    proc = None
    start = time.monotonic()
    wall_start = dt.datetime.now()
    last_spv = None
    try:
        with (evidence / "console.log").open("wb") as output:
            proc = subprocess.Popen(command, env=env, cwd=temp, stdout=output,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
            result["trial_pid"] = proc.pid
            while time.monotonic() - start < TIME_LIMIT:
                time.sleep(2)
                spv = list(dump_dir.glob("*57b077ac*.spv"))
                if spv and all(f.stat().st_size >= 20 for f in spv):
                    last_spv = last_spv or time.monotonic()
                    if time.monotonic() - last_spv >= 6:
                        result["end_reason"] = "TARGET_SHADER_CAPTURED"
                        break
                else:
                    last_spv = None
                others = processes_in_use(exclude=(proc.pid,), exclude_group=os.getpgid(proc.pid))
                if others:
                    result["end_reason"] = "ANOTHER_EMULATOR_OR_BUILD_STARTED"
                    result["other_processes"] = others
                    break
                if proc.poll() is not None:
                    result["end_reason"] = "PROCESS_EXIT"
                    break
                if time.monotonic() - start > 45 and not last_spv:
                    raw_console = (evidence / "console.log").read_text(errors="replace")
                    if "Starting shadps4 emulator" not in raw_console:
                        result["end_reason"] = "EARLY_STARTUP_STALL"
                        try:
                            task_dir = Path("/proc") / str(proc.pid) / "task"
                            result["startup_thread_wchans"] = {
                                t.name: (t / "wchan").read_text().strip()
                                for t in list(task_dir.iterdir())[:48] if t.name.isdigit()
                            }
                        except (OSError, PermissionError) as exc:
                            result["startup_thread_wchans"] = {"error": str(exc)}
                        break
            else:
                result["end_reason"] = "TIME_LIMIT"
    finally:
        stop_owned(proc)
        result["trial_return_code"] = proc.poll() if proc else None
        result["trial_elapsed_s"] = round(time.monotonic() - start, 2)
        try:
            if config_path.read_bytes() != original_config:
                fd, name = tempfile.mkstemp(prefix=".gow-restore-", dir=config_path.parent)
                try:
                    os.fchmod(fd, original_mode)
                    with os.fdopen(fd, "wb") as fp:
                        fp.write(original_config)
                        fp.flush()
                        os.fsync(fp.fileno())
                    os.replace(name, config_path)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
            result["native_config_restored"] = (config_path.read_bytes() == original_config)
        except Exception as exc:
            result["native_config_restored"] = False
            result["native_config_restore_error"] = str(exc)
    # Read-only kernel evidence for a GPU reset/hang; no sudo, driver or
    # display changes. Some Debian accounts cannot read the kernel journal.
    if shutil.which("journalctl"):
        try:
            kernel_log = run(
                ["journalctl", "-k", "--since",
                 wall_start.strftime("%Y-%m-%d %H:%M:%S"),
                 "--no-pager", "-o", "short-iso", "-n", "900"],
                timeout=15)
            if kernel_log.returncode == 0:
                suspicious = [ln for ln in kernel_log.stdout.splitlines()
                              if re.search(r"amdgpu|drm|gpu reset|ring timeout|gpu hang",
                                           ln, re.I)]
                (evidence / "kernel-gpu.txt").write_text("\n".join(suspicious[-200:]))
                result["kernel_gpu_lines"] = len(suspicious)
                result["kernel_gpu_hang_logged"] = any(
                    re.search(r"gpu reset|ring.*timeout|gpu hang|job timed out",
                              ln, re.I) for ln in suspicious)
            else:
                result["kernel_gpu_evidence_unavailable"] = True
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["kernel_gpu_evidence_error"] = str(exc)
    joined = (evidence / "console.log").read_text(errors="replace")
    if log_dir.is_dir():
        for p in log_dir.glob("*.log"):
            if p.name not in ("shadps4.log", GAME + ".log") or not p.is_file():
                continue
            old = before.get(p.name)
            with p.open("rb") as f:
                if old and p.stat().st_ino == old[0] and p.stat().st_size >= old[1]:
                    f.seek(old[1])
                data = f.read()[-16000000:]
            (evidence / ("new-" + p.name)).write_bytes(data)
            joined += data.decode("utf-8", "replace")
    # Census the other dispatches while allowing only one guarded direct dispatch.
    census_pattern = re.compile(
        r"GOW_COMPUTE_CENSUS_DIRECT shader=(0x[0-9a-fA-F]+) "
        r"grid=(\d+)x(\d+)x(\d+) groups=(\d+) "
        r"buffers=(\d+) images=(\d+) samplers=(\d+) "
        r"invalid_buffers=(\d+) invalid_images=(\d+) invalid_samplers=(\d+) "
        r"uses_dma=(true|false) gds=(true|false) shared=(true|false) "
        r"small_grid=(true|false)")
    census = {}
    for entry in census_pattern.finditer(joined):
        (shader, x, y, z, groups, buffers, images, samplers,
         bad_buffers, bad_images, bad_samplers, dma, gds, shared,
         small_grid) = entry.groups()
        key = (shader, int(x), int(y), int(z))
        if key in census:
            continue  # console and game logs can report the same dispatch
        census[key] = {
            "shader": shader, "grid": [int(x), int(y), int(z)],
            "workgroups": int(groups),
            "buffers": int(buffers), "images": int(images),
            "samplers": int(samplers), "invalid_buffers": int(bad_buffers),
            "invalid_images": int(bad_images), "invalid_samplers": int(bad_samplers),
            "uses_dma": dma == "true", "gds": gds == "true",
            "shared": shared == "true", "small_grid": small_grid == "true",
        }
    result["compute_census"] = list(census.values())
    result["compute_census_count"] = len(census)
    result["small_grid_candidates"] = [
        item for item in census.values()
        if item["small_grid"] and not item["uses_dma"]
        and not item["gds"] and not item["shared"]
        and not (item["invalid_buffers"] or item["invalid_images"]
                 or item["invalid_samplers"])
    ]
    result["compute_execution_enabled"] = True
    result["one_shot_target"] = {"shader": "0x6e9a8b98", "grid": [2, 1, 1]}
    result["one_shot_candidate"] = (
        next((line[:1500] for line in joined.splitlines()
              if "GOW_COMPUTE_ONE_SHOT_CANDIDATE" in line), None))
    result["one_shot_result"] = (
        next((line[:1500] for line in joined.splitlines()
              if "GOW_COMPUTE_ONE_SHOT_RESULT" in line), None))
    result["one_shot_submitted"] = bool(
        result["one_shot_result"] and "result=SUBMITTED" in result["one_shot_result"])
    result["one_shot_bind_failed"] = bool(
        result["one_shot_result"] and "result=BIND_FAILED" in result["one_shot_result"])
    # Keep command recording separate from GPU completion: the scheduler's
    # Vulkan timeline is the source of truth and is never waited on here.
    gpu_tick_match = re.search(
        r"GOW_COMPUTE_ONE_SHOT_GPU_TICK shader=0x6e9a8b98 tick=(\\d+) "
        r"result=WAITING_FOR_NORMAL_SUBMISSION", joined)
    gpu_complete_match = re.search(
        r"GOW_COMPUTE_ONE_SHOT_GPU_COMPLETE shader=0x6e9a8b98 tick=(\\d+) "
        r"result=TIMELINE_SIGNALED", joined)
    result["one_shot_gpu_tick"] = (
        int(gpu_tick_match.group(1)) if gpu_tick_match else None)
    result["one_shot_gpu_completed_tick"] = (
        int(gpu_complete_match.group(1)) if gpu_complete_match else None)
    result["one_shot_gpu_timeline_completed"] = bool(
        result["one_shot_submitted"] and
        result["one_shot_gpu_tick"] is not None and
        result["one_shot_gpu_tick"] == result["one_shot_gpu_completed_tick"])
    result["one_shot_gpu_timeline_mismatch"] = bool(
        gpu_complete_match and not result["one_shot_gpu_timeline_completed"])
    result["gpu_device_lost_logged"] = bool(
        re.search(r"VK_ERROR_DEVICE_LOST|ErrorDeviceLost|device lost|GPU hang",
                  joined, re.IGNORECASE))
    result["dma_info_logged"] = "GOW_TARGET_DMA_INFO" in joined
    result["dma_codegen_logged"] = "GOW_TARGET_DMA_DYNAMIC_CODEGEN" in joined
    result["spv_dump_logged"] = "GOW_TARGET_SPV_DUMP" in joined
    result["compute_suppression_logged"] = "GOW_DIAG_COMPUTE_SUPPRESSED" in joined
    result["bind_probe_logged"] = "GOW_TARGET_BIND_PROBE" in joined
    result["bind_probe_passed"] = bool(re.search(
        r"GOW_TARGET_BIND_PROBE[^\n]*bound=true[^\n]*uses_dma=true[^\n]*"
        r"writes=18[^\n]*bda_valid=true[^\n]*fault_valid=true[^\n]*dispatch=SKIPPED",
        joined))
    result["gds_placeholder_logged"] = "GOW_GDS_DIAG_TRANSLATED_ONLY" in joined
    audit = re.search(
        r"GOW_TARGET_RESOURCE_AUDIT shader=0x57b077ac "
        r"invalid_guest_buffers=(\d+) invalid_images=(\d+) "
        r"invalid_samplers=(\d+) dispatch=SKIPPED", joined)
    result["resource_audit_logged"] = bool(audit)
    result["resource_audit_counts"] = (
        dict(zip(("guest_buffers", "images", "samplers"),
                 (int(value) for value in audit.groups())))
        if audit else None)
    result["resource_audit_passed"] = (
        all(value == 0 for value in result["resource_audit_counts"].values())
        if audit else False)
    if result["resource_audit_counts"] and not result["resource_audit_passed"]:
        result["resource_integrity_warning"] = (
            "Some guest descriptor sources are unresolved; GPU dispatch must remain disabled.")
    # The IR's IMul/IAdd calculate byte displacements, then SHR 2 supplies
    # ReadConst's dword index. Accept only the corrected byte-address probe.
    table_pattern = re.compile(
        r"GOW_IMAGE_TABLE_AUDIT group=(A|B) stride_bytes=(\d+) "
        r"image_offset_bytes=(\d+) mask_dw=(\d+) bound_dw=(\d+) "
        r"raw_bound=(\d+) bounded_limit=(\d+) slots=32 "
        r"sampled=(\d+) populated=(\d+) type_valid=(\d+) "
        r"sampled_mask=(0x[0-9a-fA-F]+) populated_mask=(0x[0-9a-fA-F]+) "
        r"valid_mask=(0x[0-9a-fA-F]+) address_mask=(0x[0-9a-fA-F]+) "
        r"selected_mask=(0x[0-9a-fA-F]+) "
        r"bounded_selection=(0x[0-9a-fA-F]+) "
        r"selected_valid_mask=(0x[0-9a-fA-F]+) "
        r"selected_nonzero_addr_mask=(0x[0-9a-fA-F]+) "
        r"selected_unresolved_mask=(0x[0-9a-fA-F]+) "
        r"valid_type_mask=(0x[0-9a-fA-F]+) "
        r"selected_type_mask=(0x[0-9a-fA-F]+) "
        r"mask_source_nonzero=(true|false) "
        r"bound_source_nonzero=(true|false) dispatch=SKIPPED")
    table_audits = {}
    for match in table_pattern.finditer(joined):
        (group, stride_bytes, image_offset_bytes, mask_dw, bound_dw,
         raw_bound, bounded_limit, sampled, populated, type_valid,
         sampled_mask, populated_mask, valid_mask, address_mask,
         selected_mask, bounded_selection, selected_valid_mask,
         selected_nonzero_addr_mask, selected_unresolved_mask,
         valid_type_mask, selected_type_mask, mask_source_nonzero,
         bound_source_nonzero) = match.groups()
        table_audits[group] = {
            "stride_bytes": int(stride_bytes),
            "image_offset_bytes": int(image_offset_bytes),
            "mask_dw": int(mask_dw),
            "bound_dw": int(bound_dw),
            "raw_bound": int(raw_bound),
            "bounded_limit": int(bounded_limit),
            "slots": 32,
            "sampled": int(sampled),
            "populated": int(populated),
            "type_valid": int(type_valid),
            "sampled_mask": sampled_mask,
            "populated_mask": populated_mask,
            "valid_mask": valid_mask,
            "address_mask": address_mask,
            "selected_mask": selected_mask,
            "bounded_selection": bounded_selection,
            "selected_valid_mask": selected_valid_mask,
            "selected_nonzero_addr_mask": selected_nonzero_addr_mask,
            "selected_unresolved_mask": selected_unresolved_mask,
            "valid_type_mask": valid_type_mask,
            "selected_type_mask": selected_type_mask,
            "mask_source_nonzero": mask_source_nonzero == "true",
            "bound_source_nonzero": bound_source_nonzero == "true",
            "selected_type_mixed": int(selected_type_mask, 16).bit_count() > 1,
            "bounded_selected_count": int(bounded_selection, 16).bit_count(),
            "selected_unresolved_count": int(selected_unresolved_mask, 16).bit_count(),
            "selected_nonzero_texture_count": int(selected_nonzero_addr_mask, 16).bit_count(),
        }
    result["image_table_audits"] = table_audits
    result["image_table_audit_complete"] = set(table_audits) == {"A", "B"}
    result["image_table_audit_expected_layout"] = (
        table_audits.get("A", {}).get("stride_bytes") == 776 and
        table_audits.get("A", {}).get("image_offset_bytes") == 544 and
        table_audits.get("A", {}).get("mask_dw") == 5899 and
        table_audits.get("A", {}).get("bound_dw") == 0 and
        table_audits.get("B", {}).get("stride_bytes") == 264 and
        table_audits.get("B", {}).get("image_offset_bytes") == 3536 and
        table_audits.get("B", {}).get("mask_dw") == 5900 and
        table_audits.get("B", {}).get("bound_dw") == 1)
    result["image_live_masks_captured"] = (
        result["image_table_audit_complete"] and
        all(item["mask_source_nonzero"] and item["bound_source_nonzero"]
            for item in table_audits.values()))
    result["image_selected_slot_counts"] = {
        group: {
            "candidate_count": item["bounded_selected_count"],
            "type_invalid_count": item["selected_unresolved_count"],
            "nonzero_texture_count": item["selected_nonzero_texture_count"],
            "mixed_image_types": item["selected_type_mixed"],
            "index_upper_bound": item["bounded_limit"],
        }
        for group, item in table_audits.items()
    }
    relevant = [line[:1600] for line in joined.splitlines() if
                re.search(r"GOW_|failed|error|shader 0x57b077ac|Vulkan|CPU identity", line, re.I)]
    (evidence / "key-events.txt").write_text("\n".join(relevant[-3500:]))
    validate_spv(dump_dir, evidence, result)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    archive = HOME / ("gow-phi-dma-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                      + ".tar.gz")
    report = {"branch_head": HEAD_SHA, "baseline": BASE_SHA,
              "source": str(SOURCE), "installed_emulator_modified": False,
              "native_config_modified_by_helper": False, "user_session_preserved": True}
    lock_path = HOME / ".cache/gow-phi-dma-diagnostic.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock, tempfile.TemporaryDirectory(prefix="gow-phi-", dir=HOME) as tmp:
        tmp = Path(tmp)
        evidence = tmp / "evidence"
        evidence.mkdir()
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            busy = processes_in_use()
            if busy:
                report["busy_processes"] = busy
                raise RuntimeError("Another emulator/build active: refusing to interfere with Ghost")
            if not SOURCE.is_dir() or not (SOURCE / ".git").exists():
                raise RuntimeError("Verified shadPS4 source tree is unavailable")
            check = run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=SOURCE)
            if check.returncode or check.stdout.strip():
                raise RuntimeError("Verified source has modifications; refusing concurrent changes")
            build = find_build()
            report["build_directory"] = str(build)
            patch = get_patch()
            (evidence / "pinned-code.diff").write_bytes(patch)
            report["patch_sha256"] = hashlib.sha256(patch).hexdigest()
            verify = run(["git", "apply", "--check", "--whitespace=nowarn",
                          str(evidence / "pinned-code.diff")], cwd=SOURCE)
            if verify.returncode:
                raise RuntimeError("Branch patch incompatible with verified source: "
                                   + verify.stderr[-2600:])
            report["patch_applies"] = True
            if args.preflight_only:
                report["result"] = "PREFLIGHT_PASS"
            else:
                trial = do_build(build, patch, tmp, report)
                shutil.copy2(tmp / "build.log", evidence / "build.log")
                if not report.get("sources_restored") or not report.get("build_binary_restored"):
                    raise RuntimeError("Source/build restore verification failed; no game launched")
                report["result"] = "BUILD_PASS"
                trial_run(trial, tmp, report)
                report["result"] = report.get("end_reason", "TRIAL_COMPLETE")
                if report.get("resource_audit_logged") and not report.get("resource_audit_passed"):
                    report["result"] = (
                        "DYNAMIC_IMAGE_MASKS_CAPTURED"
                        if report.get("image_live_masks_captured")
                        else "DYNAMIC_IMAGE_MASKS_NOT_CAPTURED")
                # Priority: don't disguise a rejected one-shot as a census success.
                if report.get("one_shot_gpu_timeline_completed"):
                    report["result"] = "ONE_SHOT_GPU_TIMELINE_COMPLETED"
                elif report.get("one_shot_gpu_tick") is not None:
                    report["result"] = "ONE_SHOT_GPU_TIMELINE_PENDING"
                elif report.get("one_shot_submitted"):
                    report["result"] = "ONE_SHOT_COMMAND_RECORDED"
                elif report.get("one_shot_bind_failed"):
                    report["result"] = "ONE_SHOT_BIND_FAILED"
                elif report.get("one_shot_candidate"):
                    report["result"] = "ONE_SHOT_ELIGIBILITY_CHECKED"
                elif report.get("compute_census_count"):
                    report["result"] = "COMPUTE_CENSUS_ONLY"
        except KeyboardInterrupt:
            report["result"] = "INTERRUPTED"
        except Exception as exc:
            report["result"] = "FAIL"
            report["error"] = str(exc)
            report["traceback"] = traceback.format_exc()[-9000:]
        finally:
            if (tmp / "build.log").is_file() and not (evidence / "build.log").exists():
                shutil.copy2(tmp / "build.log", evidence / "build.log")
            report["source_tree_clean_after"] = (
                run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=SOURCE).stdout.strip() == ""
                if SOURCE.is_dir() and (SOURCE / ".git").exists() else None)
            (evidence / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            with tarfile.open(archive, "w:gz") as result_archive:
                for p in evidence.rglob("*"):
                    if p.is_file() and p.stat().st_size < 32_000_000:
                        result_archive.add(p, arcname=str(p.relative_to(tmp)))
    print("GOW_PHI_DMA_RESULT=" + report.get("result", "UNKNOWN"))
    print("ARCHIVE=" + str(archive))
    print("TARGET_SPV_COUNT=" + str(len(report.get("spv", []))))
    print("BIND_PROBE_PASSED=" + str(report.get("bind_probe_passed", False)))
    print("COMPUTE_CENSUS_COUNT=" + str(report.get("compute_census_count", 0)))
    print("SMALL_GRID_CANDIDATE_COUNT=" + str(len(report.get("small_grid_candidates", []))))
    print("GPU_COMPUTE_ONE_SHOT_ENABLED=" + str(report.get("compute_execution_enabled", False)))
    print("ONE_SHOT_TARGET=" + str(report.get("one_shot_target")))
    print("ONE_SHOT_ELIGIBILITY=" + str(report.get("one_shot_candidate")))
    print("ONE_SHOT_RESULT=" + str(report.get("one_shot_result")))
    print("ONE_SHOT_COMMAND_RECORDED=" + str(report.get("one_shot_submitted", False)))
    print("ONE_SHOT_GPU_TICK=" + str(report.get("one_shot_gpu_tick")))
    print("ONE_SHOT_GPU_TIMELINE_COMPLETED=" +
          str(report.get("one_shot_gpu_timeline_completed", False)))
    print("AMDGPU_KERNEL_HANG_LOGGED=" + str(report.get("kernel_gpu_hang_logged", False)))
    print("GPU_DEVICE_LOST_LOGGED=" + str(report.get("gpu_device_lost_logged", False)))
    print("RESOURCE_AUDIT_PASSED=" + str(report.get("resource_audit_passed", False)))
    print("RESOURCE_AUDIT_COUNTS=" + str(report.get("resource_audit_counts")))
    print("IMAGE_TABLE_AUDITS=" + str(report.get("image_table_audits")))
    print("IMAGE_TABLE_LAYOUT_MATCH=" + str(report.get("image_table_audit_expected_layout", False)))
    print("IMAGE_LIVE_MASKS_CAPTURED=" + str(report.get("image_live_masks_captured", False)))
    print("SOURCE_RESTORED=" + str(report.get("sources_restored")))
    print("BUILD_BINARY_RESTORED=" + str(report.get("build_binary_restored")))
    if report.get("error"):
        print("ERROR=" + report["error"])

if __name__ == "__main__":
    main()
