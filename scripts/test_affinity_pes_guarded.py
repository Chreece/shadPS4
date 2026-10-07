#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Run the affinity candidate through the existing ES-DE session guard."""

import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time


_spec = importlib.util.spec_from_file_location("affinity_base", Path(__file__).with_name("test_affinity_pes.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
GAME_ID = "CUSA18676"
WRAPPER_SHA256 = "d003214fe633df323469365f130ba7e21e175288940cdb97578913a8e019c65e"


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def identity():
    return base.process_info(Path("/proc/self").resolve().name)


def alive(info):
    try:
        return base.process_info(info["pid"])["start"] == info["start"]
    except OSError:
        return False


def group_path():
    return Path("/proc/self/cgroup").read_text().strip()


def in_sunshine_group():
    return any(line.split(":", 2)[-1] == "/system.slice/sunshine.service" or
               line.split(":", 2)[-1].startswith("/system.slice/sunshine.service/")
               for line in group_path().splitlines())


def restore_wrapper(job):
    wrapper = Path(job["wrapper"])
    if wrapper.is_symlink():
        raise RuntimeError("Launcher became a symlink; refusing to overwrite it")
    current = base.digest(wrapper)
    if current == job["original_sha256"]:
        return
    if current != job["patched_sha256"]:
        raise RuntimeError("Launcher changed during the test; saved original was not written over it")
    atomic_file(wrapper, Path(job["backup"]).read_bytes(), job["wrapper_mode"])


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


def hook(wrapper, script, job_file):
    native = f'exec {shlex.quote(str(Path.home() / "Applications/shadps4/shadps4"))} --game "$game" --fullscreen true'
    original = wrapper.read_text()
    if original.splitlines().count(native) != 1:
        raise RuntimeError("Expected guarded launcher command changed; no launcher modification made")
    invocation = f'python3 {shlex.quote(str(script))} --launch {shlex.quote(str(job_file))}'
    injected = (f'if [[ "$game" == {GAME_ID} && -f {shlex.quote(str(script))} ]]; then\n'
                f'    if {invocation}; then\n'
                '        exit 0\n'
                '    else\n'
                '        _affinity_status=$?\n'
                '        if [[ "$_affinity_status" != 75 ]]; then exit "$_affinity_status"; fi\n'
                '    fi\n'
                'fi\n' + native)
    replacement = original.replace(native, injected)
    subprocess.run(["bash", "-n"], input=replacement, text=True, check=True, timeout=5)
    return replacement.encode()


def test_cores(job):
    return [info for info in base.emulators()
            if str(info["exe"]) == job["binary"] and str(info["cwd"]) == job["runtime"]]


def stop_test(job):
    for sig, duration in [(signal.SIGTERM, 4), (signal.SIGKILL, 2)]:
        for info in test_cores(job):
            try:
                fd = os.pidfd_open(info["pid"])
                try:
                    current = base.process_info(info["pid"])
                    if (current["start"] == info["start"] and str(current["exe"]) == job["binary"] and
                            str(current["cwd"]) == job["runtime"]):
                        signal.pidfd_send_signal(fd, sig)
                finally:
                    os.close(fd)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and test_cores(job):
            time.sleep(0.1)
        if not test_cores(job):
            return
    raise RuntimeError("Test process survived termination; test profile retained")


def launch(job_file):
    job = json.loads(job_file.read_text())
    status_file = job_file.parent / "status.json"
    with (job_file.parent / "launch.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 1
        if not alive(job["owner"]) or time.time() > job["expires"] or (job_file.parent / "cancel").exists():
            restore_wrapper(job)
            return 75
        if status_file.exists():
            return 75
        result = {"phase": "preparing", "bridge": identity(), "source_commit": base.SOURCE,
                  "candidate_sha256": base.BINARY_SHA256, "cgroup": group_path(),
                  "installed_binary_replaced": False, "match_playable_user_report": "unconfirmed"}
        write_json(status_file, result)
        context = protected = before = None
        try:
            if not in_sunshine_group():
                raise RuntimeError("Launch did not come through sunshine.service; refusing detached gameplay")
            restore_wrapper(job)
            binary = Path(job["binary"])
            if base.digest(binary) != base.BINARY_SHA256:
                raise RuntimeError("Candidate binary changed")
            context = base.launch_context(identity())
            context["args"] = [job["installed_binary"], "--game", GAME_ID, "--fullscreen", "true"]
            context["exe"] = Path(job["installed_binary"])
            context["game"] = base.find_game(context)
            if context["game"] is None or context["game"]["serial"] != GAME_ID:
                raise RuntimeError("Guarded launch did not resolve the expected PES title")
            result.update(source_profile=str(context["profile"]), baseline_binary=job["installed_binary"],
                          baseline_sha256=base.digest(context["exe"]), game_id=GAME_ID,
                          game_title=context["game"]["title"], candidate_args=base.candidate_arguments(context))
            runtime = Path(job["runtime"])
            protected, before, config_hash, users_hash = base.prepare_profile(context, context["game"], runtime)
            write_json(job_file.parent / "preservation.json", {
                "protected": protected, "before": before, "profile": context["profile"],
                "config_sha256": config_hash, "users_sha256": users_hash,
                "installed_sha256": result["baseline_sha256"]})
            if not alive(job["owner"]) or (job_file.parent / "cancel").exists():
                raise RuntimeError("Test controller ended before candidate startup")
            if base.emulators():
                raise RuntimeError("Another emulator core survived the guard; refusing parallel shadPS4")
            env = os.environ.copy()
            env["SHADPS4_ENABLE_IPC"] = "false"
            result["phase"] = "starting"
            write_json(status_file, result)
            with (job_file.parent / "candidate-console.log").open("w") as output:
                child = subprocess.Popen([str(binary), *result["candidate_args"]], cwd=runtime, env=env,
                                         stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                try:
                    result.update(phase="running", candidate_pid=child.pid)
                    write_json(status_file, result)
                    while True:
                        initial_code = child.poll()
                        remaining = test_cores(job)
                        if initial_code is not None and not remaining:
                            break
                        if initial_code is not None and remaining:
                            result["relaunch_observed"] = True
                        if not alive(job["owner"]) or (job_file.parent / "cancel").exists():
                            stop_test(job)
                            break
                        time.sleep(0.2)
                    result["initial_process_returncode"] = child.wait(timeout=5)
                    if not result.get("relaunch_observed"):
                        result["returncode"] = result["initial_process_returncode"]
                finally:
                    stop_test(job)
                    child.wait(timeout=5)
        except BaseException as error:
            result["error"] = str(error) or type(error).__name__
        finally:
            if protected is not None:
                try:
                    result["source_files_unchanged"] = base.snapshot(protected) == before
                    result["source_config_unchanged"] = base.digest(context["profile"] / "config.json") == config_hash
                    result["source_users_unchanged"] = base.digest(context["profile"] / "users.json") == users_hash
                    result["installed_binary_unchanged"] = base.digest(context["exe"]) == result["baseline_sha256"]
                except OSError as error:
                    result["preservation_error"] = str(error)
            result["phase"] = "finished"
            write_json(status_file, result)
        return 1 if "error" in result else result.get("returncode", 0)


def run():
    home = Path.home()
    cache = home / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-affinity-guarded-", dir=home))
    work = Path(tempfile.mkdtemp(prefix="guarded-pes-", dir=cache))
    runtime = Path(tempfile.mkdtemp(prefix="pes-runtime-", dir=cache))
    wrapper = home / ".local/bin/shadps4-esde"
    status_file = work / "status.json"
    job = None
    summary = {"source_commit": base.SOURCE, "installed_binary_replaced": False,
               "match_playable_user_report": "unconfirmed"}
    try:
        if base.emulators():
            raise RuntimeError("An emulator is still running; close it through your normal session before testing")
        binary = cache / ("build-" + base.SOURCE[:12]) / "shadps4"
        if base.digest(binary) != base.BINARY_SHA256:
            raise RuntimeError("Candidate differs from the homebrew-tested binary")
        if wrapper.is_symlink() or base.digest(wrapper) != WRAPPER_SHA256:
            raise RuntimeError("ES-DE launcher differs from the supplied evidence; left untouched")
        original = wrapper.read_bytes()
        backup = work / "shadps4-esde.original"
        backup.write_bytes(original)
        for name in ["test_affinity_pes_guarded.py", "test_affinity_pes.py"]:
            shutil.copy2(Path(__file__).with_name(name), work / name)
        script = work / "test_affinity_pes_guarded.py"
        job_file = work / "job.json"
        replacement = hook(wrapper, script, job_file)
        import hashlib
        job = {"owner": identity(), "expires": time.time() + 2400, "wrapper": str(wrapper),
               "backup": str(backup), "wrapper_mode": stat.S_IMODE(wrapper.stat().st_mode),
               "original_sha256": base.digest(wrapper), "patched_sha256": hashlib.sha256(replacement).hexdigest(),
               "binary": str(binary), "installed_binary": str(home / "Applications/shadps4/shadps4"),
               "runtime": str(runtime), "evidence": str(evidence)}
        write_json(job_file, job)
        if base.digest(wrapper) != job["original_sha256"]:
            raise RuntimeError("Launcher changed while arming; left untouched")
        atomic_file(wrapper, replacement, job["wrapper_mode"])
        base.say("READY=Launch PES once through Moonlight / ES-DE. That launch will be the affinity candidate.")
        base.say("The existing session guard handles startup and exit. Ctrl+C here stops only this test.")
        deadline = time.monotonic() + 600
        phase = None
        next_update = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = json.loads(status_file.read_text()) if status_file.exists() else {"phase": "waiting"}
            if status["phase"] != phase:
                phase = status["phase"]
                if phase == "preparing":
                    base.say("COPYING_PROFILE=Copying settings and PES saves within the guarded launch")
                elif phase == "running":
                    deadline = time.monotonic() + 1200
                    base.say(f"CANDIDATE_RUNNING=PID {status['candidate_pid']}; play a match, then exit normally")
                elif phase == "finished":
                    summary.update(status)
                    break
            if "bridge" in status and not alive(status["bridge"]):
                summary.update(status)
                summary["session_ended"] = True
                break
            if time.monotonic() >= next_update:
                base.say("TEST_STATE=" + phase)
                next_update += 15
            time.sleep(0.25)
        else:
            summary["error"] = "Test timed out; collecting and stopping the isolated candidate"
        if status_file.exists():
            summary.update(json.loads(status_file.read_text()))
        if summary.get("candidate_pid") and "error" not in summary:
            summary["match_playable_user_report"] = base.ask_result()
    except KeyboardInterrupt:
        summary["stopped_from_terminal"] = True
    except Exception as error:
        summary["error"] = str(error)
        base.say("TEST_ERROR=" + str(error))
    finally:
        previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            (work / "cancel").touch()
            if job is not None:
                try:
                    restore_wrapper(job)
                    summary["launcher_restored"] = True
                except Exception as error:
                    summary["launcher_restore_error"] = str(error)
                try:
                    stop_test(job)
                    deadline = time.monotonic() + 15
                    while status_file.exists() and time.monotonic() < deadline:
                        status = json.loads(status_file.read_text())
                        if status["phase"] == "finished" or not alive(status["bridge"]):
                            break
                        time.sleep(0.1)
                    if status_file.exists():
                        status = json.loads(status_file.read_text())
                        answer = summary["match_playable_user_report"]
                        summary.update(status)
                        summary["match_playable_user_report"] = answer
                except Exception as error:
                    summary["cleanup_error"] = str(error)
            preservation = work / "preservation.json"
            if preservation.exists():
                try:
                    saved = json.loads(preservation.read_text())
                    profile = Path(saved["profile"])
                    summary["source_files_unchanged"] = base.snapshot([Path(p) for p in saved["protected"]]) == saved["before"]
                    summary["source_config_unchanged"] = base.digest(profile / "config.json") == saved["config_sha256"]
                    summary["source_users_unchanged"] = base.digest(profile / "users.json") == saved["users_sha256"]
                    summary["installed_binary_unchanged"] = base.digest(Path(job["installed_binary"])) == saved["installed_sha256"]
                except Exception as error:
                    summary["preservation_error"] = str(error)
            for path in [work / "candidate-console.log", *(runtime / "user/log").glob("*")]:
                if path.is_file():
                    with path.open("rb") as stream:
                        size = path.stat().st_size
                        (evidence / ("head-" + path.name)).write_bytes(stream.read(256 * 1024))
                        stream.seek(max(0, size - 8 * 1024 * 1024))
                        (evidence / ("tail-" + path.name)).write_bytes(stream.read(8 * 1024 * 1024))
            if job is not None:
                summary["live_test_pids"] = [info["pid"] for info in test_cores(job)]
            write_json(evidence / "summary.json", summary)
            archive_path = evidence.with_suffix(".tar.gz")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(evidence, arcname=evidence.name)
            bridge_alive = status_file.exists() and alive(json.loads(status_file.read_text())["bridge"])
            if not bridge_alive and not (job and test_cores(job)):
                shutil.rmtree(runtime)
                if summary.get("launcher_restored"):
                    shutil.rmtree(work)
            base.say("UPLOAD_ONLY=" + str(archive_path))
            base.say("SSH stays open. The installed emulator binary was not replaced.")
        finally:
            signal.signal(signal.SIGINT, previous_handler)
    return 1 if any(key in summary for key in ("error", "cleanup_error", "launcher_restore_error")) else 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--launch":
        sys.exit(launch(Path(sys.argv[2])))
    elif len(sys.argv) == 1:
        sys.exit(run())
    else:
        raise SystemExit("Unexpected arguments")
