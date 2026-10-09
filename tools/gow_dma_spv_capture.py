#!/usr/bin/env python3
"""Native-profile GoW shader SPIR-V diagnostic; restores temporary overrides and dumps."""
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
TIMEOUT = 105

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
    archive = HOME / ("gow-target-spv-native-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".tar.gz")
    result = {"game": GAME, "shader": SHADER, "native_profile": True,
              "installed_binary_unchanged": True, "global_config_modified": False,
              "ssh_session_unchanged": True, "limit_seconds": TIMEOUT}
    with tempfile.TemporaryDirectory(prefix="gow-spv-", dir=HOME) as directory:
        temp = Path(directory)
        out = temp / "evidence"
        out.mkdir()
        proc = None
        profile = HOME / ".local/share/shadPS4"
        game_override = profile / "custom_configs" / (GAME + ".json")
        dumps = profile / "shader" / "dumps"
        previous_dumps = dumps.parent / (".gow-spv-prior-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid()) + "-dumps")
        override_data = b'{"GPU":{"dump_shaders":true}}\n'
        override_inode = None
        override_created = False
        dump_created = False
        dump_backed_up = False
        config_bytes = None
        config_mode = None
        binary_hash = None
        previous_log_sizes = {}
        try:
            source = HOME / ".local/share/shadPS4/config.json"
            binary = HOME / "Applications/shadps4-gow-dma-codegen-trial-20261010-004956/shadps4"
            if not source.is_file() or not binary.is_file():
                raise RuntimeError(f"Required config={source.is_file()} or trial={binary.is_file()} absent; no game launched")
            running = active_emulators()
            if running:
                raise RuntimeError(f"An emulator is already running: {running}; no game launched")
            config_bytes = source.read_bytes()
            original_hash = hashlib.sha256(config_bytes).hexdigest()
            config_mode = source.stat().st_mode
            config = json.loads(config_bytes.decode("utf-8"))
            binary_hash = hashfile(binary)
            if not isinstance(config.get("GPU"), dict):
                raise RuntimeError("Unexpected GPU config schema; no game launched")
            exact_game, game_resolution = resolve_exact_game(config)
            result["game_resolution"] = game_resolution
            if exact_game is None:
                raise RuntimeError("Game executable not located within enabled install directories; no launch attempted")
            result["game_executable_confirmed"] = True
            if game_override.is_symlink() or game_override.exists():
                raise RuntimeError("Existing GoW game-specific configuration; refusing to overwrite")
            if dumps.is_symlink():
                raise RuntimeError("Shader dump directory is a symlink; refusing to move")
            if dumps.parent.is_dir() and list(dumps.parent.glob(".gow-spv-prior-*-dumps")):
                raise RuntimeError("Previous shader backup exists; refusing to overwrite (see report)")
            if dumps.exists():
                if not dumps.is_dir() or previous_dumps.exists():
                    raise RuntimeError("Unexpected shader dump directory/backup state")
                dumps.rename(previous_dumps)
                dump_backed_up = True
            dumps.mkdir(parents=True, exist_ok=False)
            dump_created = True
            game_override.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(game_override, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(override_data)
                f.flush()
                os.fsync(f.fileno())
            override_created = True
            override_inode = game_override.stat().st_ino
            log_root = profile / "log"
            if log_root.is_dir():
                for fp in log_root.glob("*.log"):
                    if fp.is_file() and fp.name in ("shadps4.log", GAME + ".log"):
                        st = fp.stat()
                        previous_log_sizes[fp.name] = (st.st_ino, st.st_size)
            result["original_config_sha256"] = original_hash
            result["trial_sha256"] = hashfile(binary)
            env = os.environ.copy()
            env.pop("XDG_DATA_HOME", None)
            env.pop("XDG_CACHE_HOME", None)
            env.update({
                "SHADPS4_ENABLE_IPC": "false",
                "SHADPS4_GOW_ONE_SHADER_DMA_COMPILE": "1",
                "SHADPS4_GOW_SUPPRESS_GPU_COMPUTE": "1",
                "SHADPS4_GOW_DIAGNOSTIC_GDS_NONEXECUTING": "1",
                "SHADPS4_GOW_DIAGNOSTIC_ALIAS_GUARD": "1",
            })
            env.setdefault("DISPLAY", ":0")
            command = [str(binary), "--cpu-id-mode", "auto",
                       "--game", GAME, "--fullscreen", "true"]
            with (out / "console.log").open("w") as log:
                proc = subprocess.Popen(command, cwd=HOME if not (HOME / "user").exists() else temp, env=env, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                result["trial_pid"] = proc.pid
                started = time.monotonic()
                first_seen = None
                while time.monotonic() - started < TIMEOUT:
                    time.sleep(2)
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
            result["original_config_unchanged_before_restore"] = hashfile(source) == original_hash
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
            files = []
            if dump_created and dumps.is_dir():
                for path in dumps.iterdir():
                    if path.is_file() and SHADER in path.name.lower():
                        shutil.copy2(path, out / path.name)
                        files.append(path.name)
            result["target_shader_files"] = files
            result["target_spv_count"] = sum(x.endswith(".spv") for x in files)
            logs = [out / "console.log"]
            log_dir = profile / "log"
            if proc is not None and log_dir.is_dir():
                logs += [p for p in log_dir.glob("*.log") if p.is_file() and p.name in ("shadps4.log", GAME + ".log")]
            joined = ""
            for path in logs:
                if not path.is_file():
                    continue
                if path.parent != out:
                    old = previous_log_sizes.get(path.name)
                    with path.open("rb") as fp:
                        if old and path.stat().st_ino == old[0] and path.stat().st_size >= old[1]:
                            fp.seek(old[1])
                        raw = fp.read()[-12000000:]
                    (out / ("captured-" + path.name)).write_bytes(raw)
                else:
                    raw = path.read_bytes()[-12000000:]
                joined += raw.decode("utf-8", "replace") + "\n"
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
            # Restore the exact native settings and the original shader dump directory.
            if override_created:
                try:
                    if game_override.stat().st_ino == override_inode and game_override.read_bytes() == override_data:
                        game_override.unlink()
                        result["temporary_game_config_restored"] = True
                    else:
                        result["temporary_game_config_restored"] = False
                        result["restore_warning"] = "GoW override was externally changed; left untouched"
                except OSError as exc:
                    result["game_config_restore_error"] = str(exc)
            if config_bytes is not None:
                try:
                    if source.read_bytes() != config_bytes:
                        import stat
                        fd, replacement = tempfile.mkstemp(prefix=".gow-restore-", dir=source.parent)
                        try:
                            os.fchmod(fd, stat.S_IMODE(config_mode))
                            with os.fdopen(fd, "wb") as stream:
                                stream.write(config_bytes)
                                stream.flush()
                                os.fsync(stream.fileno())
                            os.replace(replacement, source)
                            result["global_config_reverted_after_emulator_write"] = True
                        finally:
                            if os.path.exists(replacement):
                                os.unlink(replacement)
                    result["global_config_restored"] = source.read_bytes() == config_bytes
                except Exception as exc:
                    result["global_config_restore_error"] = str(exc)
            if dump_created:
                try:
                    other_emulators = active_emulators()
                    if other_emulators:
                        result["dumps_restore_error"] = "Another emulator active; original backup retained"
                        result["remaining_emulators"] = other_emulators
                    else:
                        shutil.rmtree(dumps)
                        if dump_backed_up:
                            previous_dumps.rename(dumps)
                        result["original_shader_dumps_restored"] = True
                except Exception as exc:
                    result["dumps_restore_error"] = str(exc)
                    if dump_backed_up:
                        result["shader_backup_location"] = str(previous_dumps)
            if binary_hash:
                result["trial_binary_restored"] = hashfile(binary) == binary_hash
            (out / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
            with tarfile.open(archive, "w:gz") as package:
                for path in sorted(out.iterdir()):
                    package.add(path, arcname="gow-target-spv/" + path.name)
    print("GOW_SPV_RESULT=" + ("FAIL" if "error" in result else result.get("end_reason","COLLECTED")))
    print("GLOBAL_CONFIG_RESTORED=" + str(result.get("global_config_restored",False)))
    print("ORIGINAL_DUMPS_RESTORED=" + str(result.get("original_shader_dumps_restored",False)))
    print("ARCHIVE=" + str(archive))
    print("SPV_COUNT=" + str(result.get("target_spv_count", 0)))
    print("DUMP_ENABLED=" + str(result.get("shader_dump_enabled_logged", False)))
    print("DMA_INFO=" + str(result.get("target_dma_info_logged", False)))
    print("DMA_CODEGEN=" + str(result.get("target_dma_codegen_logged", False)))
    if "error" in result:
        print("ERROR=" + result["error"])

if __name__ == "__main__":
    main()
