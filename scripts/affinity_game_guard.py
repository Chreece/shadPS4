#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Temporarily route one existing guarded game launch to an isolated candidate."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

import affinity_game_profile as profile


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def atomic_file(path, data, mode):
    fd, name = tempfile.mkstemp(prefix=path.name + ".affinity-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        if os.path.lexists(name):
            os.unlink(name)


def restore(job):
    wrapper = Path(job["wrapper"])
    if wrapper.is_symlink():
        raise RuntimeError("Launcher became a symlink; backup retained")
    current = profile.digest(wrapper)
    if current == job["original_sha256"]:
        return
    if current != job["patched_sha256"]:
        raise RuntimeError("Launcher changed during testing; backup retained without overwriting it")
    atomic_file(wrapper, Path(job["backup"]).read_bytes(), job["wrapper_mode"])


def owner_running(folder):
    with (folder / "owner.lock").open("r") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
    return False


def alive(info):
    try:
        return profile.process_info(info["pid"])["start"] == info["start"]
    except OSError:
        return False


def owned(job):
    result = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            info = profile.process_info(proc.name)
            if str(info["cwd"]) == job["runtime"] and str(info["exe"]) in job["executables"]:
                result.append(info)
        except OSError:
            pass
    return result


def stop_owned(job):
    for signum, duration in [(signal.SIGTERM, 4), (signal.SIGKILL, 2)]:
        for info in owned(job):
            try:
                fd = os.pidfd_open(info["pid"])
                try:
                    if alive(info):
                        signal.pidfd_send_signal(fd, signum)
                finally:
                    os.close(fd)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + duration
        while owned(job) and time.monotonic() < deadline:
            time.sleep(0.1)
        if not owned(job):
            return
    raise RuntimeError("Owned game survived termination; profile retained")


def hook(original, wrapper, job_file, game_id):
    installed = Path.home() / "Applications/shadps4/shadps4"
    native = f'exec {shlex.quote(str(installed))} --game "$game" --fullscreen true'
    guard = Path.home() / ".local/lib/shadps4-session-guard/guard.py"
    if (original.splitlines().count(native) != 1 or
            "# SHADPS4_SESSION_GUARD_V1" not in original or str(guard) not in original or
            '"${SHADPS4_GUARD_PARENT_PID:-}" != "$PPID"' not in original or
            "AFFINITY_HOOK=" in original or "affinity_game_guard.py" in original or
            "test_affinity_pes_guarded.py" in original):
        raise RuntimeError("Current launcher does not match the guarded command; evidence captured, no edit made")
    invocation = " ".join(shlex.quote(str(arg)) for arg in [
        sys.executable, Path(__file__).resolve(), "--launch", job_file])
    replacement = original.replace(native, (
        f'if [[ "$game" == {shlex.quote(game_id)} ]]; then\n'
        f'    exec {invocation}\n'
        'fi\n' + native))
    subprocess.run(["bash", "-n"], input=replacement, text=True, check=True, timeout=5)
    return replacement.encode()


def sample(job, output, started):
    for info in owned(job):
        proc = Path("/proc") / str(info["pid"])
        try:
            output.write(json.dumps({"seconds": time.monotonic() - started, **info,
                "stat": (proc / "stat").read_text(),
                "status": (proc / "status").read_text(),
                "threads": len(list((proc / "task").iterdir()))}, default=str) + "\n")
            output.flush()
        except OSError:
            pass


def launch(job_file):
    folder = job_file.parent
    job = json.loads(job_file.read_text())
    status_file = folder / "status.json"
    with (folder / "launch.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 1
        if status_file.exists():
            return 1
        result = {"phase": "preparing", "bridge": profile.process_info(os.getpid()),
                  "mode": job["mode"], "game_id": job["game_id"],
                  "playability": "requires user observation"}
        write_json(status_file, result)
        protected = None
        child = None
        try:
            if not owner_running(folder) or time.time() > job["expires"] or (folder / "cancel").exists():
                raise RuntimeError("Test controller stopped or expired")
            if "sunshine.service" not in Path("/proc/self/cgroup").read_text():
                raise RuntimeError("Launch is outside Sunshine; no detached game started")
            restore(job)
            for path, expected in job["checksums"].items():
                if profile.digest(path) != expected:
                    raise RuntimeError("Candidate executable changed: " + path)
            context = profile.launch_context(profile.process_info(os.getpid()))
            context["args"] = [job["installed_binary"], "--game", job["game_id"], "--fullscreen", "true", "--show-fps"]
            game = context["game"] = profile.find_game(context)
            if game is None or game["serial"] != job["game_id"]:
                raise RuntimeError("Guarded launch resolved a different game")
            runtime = Path(job["runtime"])
            protected, before, config_sha, users_sha = profile.prepare_profile(context, game, runtime)
            installed_sha = profile.digest(job["installed_binary"])
            result.update(game_title=game["title"], command=job["prefix"] + profile.candidate_arguments(context),
                          source_profile=str(context["profile"]), binary_sha256=job["checksums"][job["binary"]])
            write_json(folder / "preservation.json", {"protected": protected, "before": before,
                "profile": context["profile"], "config_sha256": config_sha,
                "users_sha256": users_sha, "installed_sha256": installed_sha})
            if not owner_running(folder) or (folder / "cancel").exists():
                raise RuntimeError("Test cancelled during profile copy")
            if profile.emulators():
                raise RuntimeError("Another emulator survived the session guard; parallel launch refused")
            env = os.environ.copy()
            env["SHADPS4_ENABLE_IPC"] = "false"
            started = time.monotonic()
            with (folder / "console.log").open("w") as output, (folder / "process-samples.jsonl").open("w") as samples:
                child = subprocess.Popen(result["command"], cwd=runtime, env=env,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                result.update(phase="running", candidate_pid=child.pid)
                write_json(status_file, result)
                while child.poll() is None or owned(job):
                    if not owner_running(folder) or (folder / "cancel").exists() or time.monotonic() - started > 1200:
                        result["cancelled"] = True
                        stop_owned(job)
                        break
                    sample(job, samples, started)
                    time.sleep(1)
                result.update(returncode=child.wait(timeout=5), elapsed_seconds=time.monotonic() - started)
        except BaseException as error:
            result["error"] = str(error) or type(error).__name__
        finally:
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(signum, signal.SIG_IGN)
            try:
                stop_owned(job)
                restore(job)
                if child is not None:
                    child.wait(timeout=5)
                if protected is not None:
                    result.update(source_files_unchanged=profile.snapshot(protected) == before,
                        source_config_unchanged=profile.digest(context["profile"] / "config.json") == config_sha,
                        source_users_unchanged=profile.digest(context["profile"] / "users.json") == users_sha,
                        installed_binary_unchanged=profile.digest(job["installed_binary"]) == installed_sha)
            except Exception as error:
                result["cleanup_error"] = str(error)
            result["phase"] = "finished"
            write_json(status_file, result)
        return 1 if "error" in result or "cleanup_error" in result else result.get("returncode", 1)


def run_stage(game_id, title, mode, binary, prefix, work, evidence):
    if profile.emulators():
        raise RuntimeError("Close the current emulator through the normal session before testing")
    folder = work / (game_id + "-" + mode)
    folder.mkdir()
    runtime = folder / "runtime"
    runtime.mkdir()
    destination = evidence / folder.name
    destination.mkdir()
    wrapper = Path.home() / ".local/bin/shadps4-esde"
    if wrapper.is_symlink():
        raise RuntimeError("ES-DE wrapper is a symlink; left untouched")
    original = wrapper.read_bytes()
    (destination / "launcher-before.sh").write_bytes(original)
    backup = folder / "launcher-original"
    backup.write_bytes(original)
    job_file = folder / "job.json"
    replacement = hook(original.decode(), wrapper, job_file, game_id)
    executables = [str(Path(arg).resolve()) for arg in (str(binary), prefix[0])]
    if mode == "translated":
        executables.append(str(Path(prefix[0]).parent.parent / "lib64/release/libdynamorio.so"))
    checksums = {path: profile.digest(path) for path in executables}
    if "-c" in prefix:
        client = prefix[prefix.index("-c") + 1]
        checksums[client] = profile.digest(client)
    job = {"wrapper": str(wrapper), "wrapper_mode": stat.S_IMODE(wrapper.stat().st_mode),
           "original_sha256": hashlib.sha256(original).hexdigest(),
           "patched_sha256": hashlib.sha256(replacement).hexdigest(), "backup": str(backup),
           "binary": str(binary), "prefix": prefix, "executables": executables, "checksums": checksums,
           "runtime": str(runtime), "game_id": game_id, "mode": mode, "expires": time.time() + 2400,
           "installed_binary": str(Path.home() / "Applications/shadps4/shadps4")}
    write_json(job_file, job)
    result = {"game_id": game_id, "mode": mode, "phase": "waiting"}
    lock = (folder / "owner.lock").open("x")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        if profile.digest(wrapper) != job["original_sha256"]:
            raise RuntimeError("Launcher changed while arming; left untouched")
        atomic_file(wrapper, replacement, job["wrapper_mode"])
        profile.say(f"READY={title} [{mode}]. Launch ONCE through Moonlight / ES-DE.")
        profile.say("Wait for RUNNING here. Play the same section, note FPS, then exit normally.")
        deadline = time.monotonic() + 600
        next_update = time.monotonic() + 20
        phase = "waiting"
        while time.monotonic() < deadline:
            status_file = folder / "status.json"
            if status_file.exists():
                result = json.loads(status_file.read_text())
                if result["phase"] != phase:
                    phase = result["phase"]
                    if phase == "running":
                        deadline = time.monotonic() + 1230
                        profile.say(f"RUNNING={title} [{mode}] PID {result['candidate_pid']}; exit normally when done")
                    elif phase == "finished":
                        break
                if not alive(result["bridge"]):
                    raise RuntimeError("Guarded launcher exited before recording completion")
            else:
                if profile.digest(wrapper) != job["patched_sha256"]:
                    raise RuntimeError("Launcher changed before the test started")
                if profile.emulators():
                    raise RuntimeError("A game bypassed the test hook; collecting launcher evidence, no second game started")
            if time.monotonic() >= next_update:
                profile.say(f"STATE={title} [{mode}] {phase}")
                next_update += 20
            time.sleep(0.25)
        else:
            raise RuntimeError("Game stage timed out")
    finally:
        handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            (folder / "cancel").touch()
            cleanup_errors = []
            for action in (restore, stop_owned):
                try:
                    action(job)
                except Exception as error:
                    cleanup_errors.append(str(error))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and (folder / "status.json").exists():
                result = json.loads((folder / "status.json").read_text())
                if result["phase"] == "finished" or not alive(result["bridge"]):
                    break
                time.sleep(0.1)
            if cleanup_errors:
                result["cleanup_error"] = "; ".join(cleanup_errors)
            for path in folder.iterdir():
                if path.is_file():
                    shutil.copy2(path, destination / path.name)
            for path in (runtime / "user/log").glob("*"):
                if path.is_file():
                    shutil.copy2(path, destination / path.name)
            content = "\n".join(path.read_text(errors="replace") for path in destination.iterdir()
                                if path.is_file() and path.suffix in {".log", ".txt"})
            result["translation_active"] = "CPU identity translation active" in content
            result["launcher_restored"] = profile.digest(wrapper) == job["original_sha256"]
            result["capture_complete"] = (result.get("phase") == "finished" and result.get("returncode") == 0
                and not result.get("cancelled") and not result.get("error") and not result.get("cleanup_error")
                and result["launcher_restored"] and all(result.get(key) is True for key in
                    ["source_files_unchanged", "source_config_unchanged", "source_users_unchanged", "installed_binary_unchanged"])
                and result["translation_active"] == (mode == "translated"))
            write_json(destination / "result.json", result)
        finally:
            lock.close()
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
    return result


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--launch":
        raise SystemExit(launch(Path(sys.argv[2])))
    raise SystemExit("Run through test_affinity_combined.py")
