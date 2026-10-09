#!/usr/bin/env python3
"""Isolated God of War Ragnarok one-shader DMA/SPIR-V capture for shadPS4."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import traceback

HOME = Path.home()
SHADER = "57b077ac"
GAME = "CUSA34384"
TIMEOUT = 150

def hashfile(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def active_emulators():
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            target = (proc / "exe").resolve()
            if target.name.lower().startswith("shadps4"):
                found.append({"pid": int(proc.name), "exe": str(target)})
        except (OSError, PermissionError, RuntimeError):
            pass
    return found

def resolve_exact_game(config, limit_seconds=28):
    """Resolve installed game once, avoiding shadPS4's unbounded ID-folder traversal."""
    entries = config.get("General", {}).get("install_dirs", [])
    if not isinstance(entries, list):
        return None, {"error": "Invalid install_dirs list"}
    roots = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("enabled", True):
            value = entry.get("path")
            if isinstance(value, str):
                p = Path(value)
                if p.is_dir():
                    roots.append(p)
    deadline = time.monotonic() + limit_seconds
    scanned = 0
    visited = set()
    for root in roots:
        queue = [(root, 0)]
        position = 0
        while position < len(queue):
            if time.monotonic() > deadline:
                return None, {"roots": len(roots), "scanned": scanned, "timeout": True}
            current, depth = queue[position]
            position += 1
            try:
                meta = current.stat()
                identity = (meta.st_dev, meta.st_ino)
                if identity in visited:
                    continue
                visited.add(identity)
                scanned += 1
                if current.name == "CUSA34384":
                    executable = current / "eboot.bin"
                    if executable.is_file() and (current / "sce_sys/param.sfo").is_file():
                        return executable.resolve(), {"roots": len(roots), "scanned": scanned, "kind": "eboot"}
                if depth >= 5:
                    continue
                with os.scandir(current) as children:
                    for child in children:
                        if child.name == "CUSA34384.zar" and child.is_file(follow_symlinks=True):
                            return Path(child.path).resolve(), {"roots": len(roots), "scanned": scanned, "kind": "zar"}
                        if child.is_dir(follow_symlinks=True):
                            queue.append((Path(child.path), depth + 1))
            except (OSError, PermissionError):
                continue
    return None, {"roots": len(roots), "scanned": scanned, "timeout": False}


def main():
    archive = HOME / ("gow-target-spv-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".tar.gz")
    result = {"game": GAME, "shader": SHADER, "isolated_xdg": True,
              "installed_binary_unchanged": True, "global_config_modified": False,
              "ssh_session_unchanged": True, "limit_seconds": TIMEOUT}
    with tempfile.TemporaryDirectory(prefix="gow-spv-", dir=HOME) as directory:
        temp = Path(directory)
        out = temp / "evidence"
        out.mkdir()
        proc = None
        try:
            source = HOME / ".local/share/shadPS4/config.json"
            binary = HOME / "Applications/shadps4-gow-dma-codegen-trial-20261010-004956/shadps4"
            if not source.is_file() or not binary.is_file():
                raise RuntimeError(f"Required config={source.is_file()} or trial={binary.is_file()} absent; no game launched")
            running = active_emulators()
            if running:
                raise RuntimeError(f"An emulator is already running: {running}; no game launched")
            original_hash = hashfile(source)
            config = json.loads(source.read_text())
            if not isinstance(config.get("GPU"), dict):
                raise RuntimeError("Unexpected GPU config schema; no game launched")
            exact_game, game_resolution = resolve_exact_game(config)
            result["game_resolution"] = game_resolution
            if exact_game is None:
                raise RuntimeError("Game executable not located within enabled install directories; no launch attempted")
            result["game_executable_confirmed"] = True
            config["GPU"]["dump_shaders"] = True
            config["GPU"]["direct_memory_access_enabled"] = False
            base = temp / "xdg"
            profile = base / "shadPS4"
            profile.mkdir(parents=True)
            # Never allow the copied config to redirect writes to original savedata/addons.
            if not isinstance(config.get("General"), dict):
                raise RuntimeError("Missing General configuration; refusing launch")
            (profile / "home").mkdir()
            (profile / "addons").mkdir()
            config["General"]["home_dir"] = str(profile / "home")
            config["General"]["addon_install_dir"] = str(profile / "addons")
            (profile / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False))
            result["original_config_sha256"] = original_hash
            result["trial_sha256"] = hashfile(binary)
            env = os.environ.copy()
            env.update({
                "XDG_DATA_HOME": str(base),
                "XDG_CACHE_HOME": str(temp / "cache"),
                "SHADPS4_ENABLE_IPC": "false",
                "SHADPS4_GOW_ONE_SHADER_DMA_COMPILE": "1",
                "SHADPS4_GOW_SUPPRESS_GPU_COMPUTE": "1",
                "SHADPS4_GOW_DIAGNOSTIC_GDS_NONEXECUTING": "1",
                "SHADPS4_GOW_DIAGNOSTIC_ALIAS_GUARD": "1",
            })
            env.setdefault("DISPLAY", ":0")
            command = [str(binary), "--cpu-id-mode", "auto",
                       "--game", str(exact_game), "--fullscreen", "true"]
            with (out / "console.log").open("w") as log:
                proc = subprocess.Popen(command, cwd=temp, env=env, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                result["trial_pid"] = proc.pid
                started = time.monotonic()
                first_seen = None
                while time.monotonic() - started < TIMEOUT:
                    time.sleep(2)
                    dumps = profile / "shader" / "dumps"
                    if dumps.is_dir():
                        size = 0
                        count = 0
                        target = []
                        for file in dumps.iterdir():
                            if not file.is_file():
                                continue
                            count += 1
                            size += file.stat().st_size
                            if SHADER in file.name.lower() and file.suffix == ".spv" and file.stat().st_size > 20:
                                target.append(file)
                        if target:
                            first_seen = first_seen or time.monotonic()
                            if time.monotonic() - first_seen >= 6:
                                result["end_reason"] = "TARGET_SPV_CAPTURED"
                                break
                        if size > 536870912 or count > 5000:
                            result.update(end_reason="DUMP_BUDGET_REACHED", dump_bytes=size, dump_files=count)
                            break
                    if proc.poll() is not None:
                        result["end_reason"] = "PROCESS_EXITED"
                        break
                    if time.monotonic() - started >= 40:
                        # Capture the real stop point instead of another silent 150s timeout.
                        partial_log = (out / "console.log").read_bytes()[-200000:]
                        if b"Starting shadps4 emulator" not in partial_log:
                            result["end_reason"] = "BLOCKED_BEFORE_EMULATOR_RUN"
                            try:
                                task_dir = Path("/proc") / str(proc.pid) / "task"
                                result["thread_wait_channels"] = {
                                    task.name: (task / "wchan").read_text().strip()
                                    for task in list(task_dir.iterdir())[:48] if task.name.isdigit()
                                }
                            except (OSError, PermissionError):
                                result["thread_wait_channels"] = {"error": "unavailable"}
                            break
                else:
                    result["end_reason"] = "TIME_LIMIT"
            result["duration_s"] = round(time.monotonic() - started, 2)
            result["original_config_unchanged"] = hashfile(source) == original_hash
        except Exception as exc:
            result["error"] = str(exc)
            result["traceback"] = traceback.format_exc()[-6000:]
        finally:
            # Terminate only the isolated trial process group that we started.
            if proc is not None:
                try:
                    if proc.poll() is None:
                        pgid = os.getpgid(proc.pid)
                        if pgid == os.getpgrp():
                            raise RuntimeError("Refusing to signal SSH process group")
                        os.killpg(pgid, signal.SIGTERM)
                        try:
                            proc.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            os.killpg(pgid, signal.SIGKILL)
                            proc.wait(timeout=8)
                    result["return_code"] = proc.poll()
                except Exception as exc:
                    result["cleanup_warning"] = str(exc)
            profile = temp / "xdg" / "shadPS4"
            dumps = profile / "shader" / "dumps"
            files = []
            if dumps.is_dir():
                for path in dumps.iterdir():
                    if path.is_file() and SHADER in path.name.lower():
                        shutil.copy2(path, out / path.name)
                        files.append(path.name)
            result["target_shader_files"] = files
            result["target_spv_count"] = sum(x.endswith(".spv") for x in files)
            logs = [out / "console.log"]
            log_dir = profile / "log"
            if log_dir.is_dir():
                logs += [f for f in log_dir.glob("*.log") if f.is_file()]
            joined = ""
            for path in logs:
                if path.is_file():
                    if path.parent != out:
                        shutil.copy2(path, out / path.name)
                    joined += path.read_text(errors="replace")[-12000000:] + "\n"
            result["shader_dump_enabled_logged"] = bool(re.search(r"shouldDumpShaders:\s*true", joined))
            result["target_dma_info_logged"] = "GOW_TARGET_DMA_INFO" in joined
            result["target_dma_codegen_logged"] = "GOW_TARGET_DMA_DYNAMIC_CODEGEN" in joined
            result["target_srt_logged"] = bool(re.search(r"GOW_SRT_FLAGGED_COMPUTE.*57b077ac", joined, re.I))
            result["compute_suppression_logged"] = "GOW_GPU_COMPUTE_SUPPRESSED" in joined
            matching = [s[:1500] for s in joined.splitlines() if re.search(
                r"GOW_|DMA|SRT|Phi node|<Error>|Validation", s, re.I)]
            (out / "key-events.txt").write_text("\n".join(matching[-3000:]) + "\n")
            for path in out.glob("*.spv"):
                for tool, args in (
                    ("spirv-val", ["--target-env", "vulkan1.3", str(path)]),
                    ("spirv-dis", [str(path), "-o", str(out / (path.name + ".spvasm"))]),
                ):
                    if shutil.which(tool):
                        try:
                            run = subprocess.run([tool, *args], capture_output=True, text=True, timeout=25)
                            (out / (path.name + "." + tool + ".txt")).write_text(
                                f"EXIT={run.returncode}\n{run.stdout[:30000]}\n{run.stderr[:30000]}")
                            result.setdefault("tool_status", {})[tool] = run.returncode
                        except Exception as exc:
                            result.setdefault("tool_status", {})[tool] = str(exc)
            (out / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
            with tarfile.open(archive, "w:gz") as package:
                for path in sorted(out.iterdir()):
                    package.add(path, arcname="gow-target-spv/" + path.name)
    print("GOW_SPV_RESULT=" + ("FAIL" if "error" in result else "COLLECTED"))
    print("ARCHIVE=" + str(archive))
    print("SPV_COUNT=" + str(result.get("target_spv_count", 0)))
    print("DUMP_ENABLED=" + str(result.get("shader_dump_enabled_logged", False)))
    print("DMA_INFO=" + str(result.get("target_dma_info_logged", False)))
    print("DMA_CODEGEN=" + str(result.get("target_dma_codegen_logged", False)))
    if "error" in result:
        print("ERROR=" + result["error"])

if __name__ == "__main__":
    main()
