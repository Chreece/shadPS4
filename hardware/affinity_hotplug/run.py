#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Retest physical CPU hotplug with the already verified affinity bitset binary."""

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback

AFFINITY_COMMIT = "9811afcdcfd14172adc7a13514b91442c5e5ef88"
BINARY_SHA = "b1d3288a1be06865bf597e88b354ae73f27172a626a9c5c0bbe089d20275f0db"
CANDIDATE_TREE = "b523e04f1643cb0774e294ddd40609710d3b6bf3"
HELPER_REVISION = "953df38d698b92cbc8fbad5cefc5d976640f30eb"
BASE_SHA = "cdfc210d92bc54758eb820ff23dba0b2d21f9ab70ae5ab02bc3245292201d724"
GUARD_SHA = "96ff1c6813c24958d505840fb8d2f802810b3d96460bc741549400827a497482"
SUITE_REVISION = "3341bc772037912f11702695d69a0627b182309f"
SUITE_SHA = "02d358e53e90e045dcbb9f5cebafc1f3c33769c8d9fd3cd8bf48bf4d7e1c3550"
CPU_ROOT = Path("/sys/devices/system/cpu")
EVIDENCE = None
LAST_STAGE = "starting"
HERE = None
base = None

def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stage(message):
    global LAST_STAGE
    LAST_STAGE = message
    print("STEP=" + message, flush=True)
    if EVIDENCE is not None:
        with (EVIDENCE / "startup.log").open("a") as output:
            output.write(time.strftime("%Y-%m-%dT%H:%M:%S%z ") + message + "\n")
        (EVIDENCE / "stage.json").write_text(json.dumps({"stage": message}) + "\n")


def capture(arguments, cwd=None, timeout=60):
    process = subprocess.Popen(list(map(str, arguments)), cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    started = time.monotonic()
    try:
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError(f"{LAST_STAGE} exceeded {timeout} seconds")
            try:
                output, _ = process.communicate(timeout=min(15, remaining))
                break
            except subprocess.TimeoutExpired:
                print(f"WAITING={LAST_STAGE} ({int(time.monotonic() - started)}s)", flush=True)
        if process.returncode:
            detail = output.decode(errors="replace")[-8000:]
            raise RuntimeError(f"{LAST_STAGE} failed ({process.returncode}):\n{detail}")
        return output.decode().strip()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()


def download(revision, relative, destination, expected, maximum=2 * 1024 * 1024):
    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError("curl is required for the bounded IPv4 download fallback")
    routes = (
        ("GitHub API", f"https://api.github.com/repos/Chreece/shadPS4/contents/{relative}?ref={revision}"),
        ("GitHub raw", f"https://raw.githubusercontent.com/Chreece/shadPS4/{revision}/{relative}"),
    )
    failures = []
    with tempfile.TemporaryDirectory(prefix="download-", dir=Path(destination).parent) as directory:
        temporary = Path(directory) / "payload"
        for label, url in routes:
            stage(f"Download {relative} via {label} (IPv4)")
            try:
                capture([curl, "-4", "--fail", "--location", "--silent", "--show-error",
                         "--connect-timeout", "8", "--max-time", "30", "--max-filesize", str(maximum),
                         "--header", "Accept: application/vnd.github.raw+json",
                         "--header", "User-Agent: shadps4-playtest", "--output", temporary, url], timeout=35)
                if temporary.stat().st_size > maximum or digest(temporary) != expected:
                    raise RuntimeError("Download checksum or size mismatch via " + label)
                Path(destination).write_bytes(temporary.read_bytes())
                return
            except (RuntimeError, TimeoutError) as error:
                failures.append(str(error))
                stage("Download attempt failed: " + str(error))
        raise RuntimeError("All download routes failed for " + relative + "\n" + "\n".join(failures))


def cancel(signum, frame):
    raise KeyboardInterrupt("Test interrupted")


class HotplugGuard:
    def __init__(self, cpu, evidence):
        self.cpu = cpu
        self.events = []
        self.pending = b""
        self.process = None
        self.log = (evidence / f"hotplug-cpu{cpu}-guard.log").open("a")

    def start(self):
        command = (["sudo", "-n"] if os.geteuid() else []) + [
            "/usr/bin/python3", "-I", str(HERE / "cpu_hotplug_guard.py"), str(self.cpu)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.log, start_new_session=True)
        answer = self.receive()
        if answer.get("event") != "ready":
            raise RuntimeError("Hotplug guard did not start: " + str(answer))

    def receive(self):
        deadline = time.monotonic() + 20
        while b"\n" not in self.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise RuntimeError("Timed out waiting for the hotplug guard")
            chunk = os.read(self.process.stdout.fileno(), 4096)
            if not chunk:
                raise RuntimeError("Hotplug guard exited; see its log")
            self.pending += chunk
        line, self.pending = self.pending.split(b"\n", 1)
        answer = json.loads(line)
        self.events.append(answer)
        self.log.write(json.dumps(answer) + "\n")
        self.log.flush()
        return answer

    def request(self, action):
        self.process.stdin.write((action + "\n").encode())
        self.process.stdin.flush()
        answer = self.receive()
        if answer.get("event") != action:
            raise RuntimeError("Hotplug request failed: " + str(answer))
        return answer

    def close(self):
        if self.process is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
            while select.select([self.process.stdout], [], [], 0)[0]:
                chunk = os.read(self.process.stdout.fileno(), 4096)
                if not chunk:
                    break
                self.pending += chunk
            if self.pending:
                self.log.write(self.pending.decode(errors="replace"))
            self.process.stdout.close()
        self.log.close()
        path = CPU_ROOT / f"cpu{self.cpu}" / "online"
        deadline = time.monotonic() + 15
        state = path.read_text().strip()
        while state != "1" and time.monotonic() < deadline:
            time.sleep(0.1)
            state = path.read_text().strip()
        if state != "1":
            raise RuntimeError(f"CPU {self.cpu} is still offline. Restore with: sudo sh -c 'echo 1 > /sys/devices/system/cpu/cpu{self.cpu}/online'")


def combined_logs(runtime, output, evidence, name):
    content = output.read_text(errors="replace")
    for path in (runtime / "user/log").glob("*"):
        if path.is_file():
            shutil.copy2(path, evidence / f"{name}-{path.name}")
            content += "\n" + path.read_text(errors="replace")
    return content


def hotplug_case(binary, suite, cpus, cpu, name, work, evidence, env):
    require_idle()
    runtime = work / name
    base.prepare_runtime(runtime)
    output = evidence / f"{name}.log"
    guard = HotplugGuard(cpu, evidence)
    process = None
    samples = []
    changes = []
    ready = None
    phases = [(0.5, "offline"), (1.5, "online"), (2.5, "offline"), (3.5, "online")]
    timed_out = False
    try:
        guard.start()
        require_idle()
        with output.open("w") as log:
            process = subprocess.Popen(["taskset", "-c", ",".join(map(str, cpus)), str(binary),
                                        "--ignore-game-patch", str(suite / "CPUM00007/eboot.bin")],
                                       cwd=runtime, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            start = heartbeat = update = time.monotonic()
            while process.poll() is None:
                now = time.monotonic()
                if now - start > 45:
                    timed_out = True
                    base.capture_process(process.pid, evidence / f"{name}-process.txt")
                    break
                require_idle(process.pid)
                if now - update >= 5:
                    stage(f"{name}: running {int(now - start)}s; changes={len(changes)}/4")
                    update = now
                threads = base.host_threads(process.pid)
                samples.append({"seconds": now - start, "threads": threads})
                if ready is None and "AFFINITY_DYNAMIC_READY workers=7" in output.read_text(errors="replace"):
                    ready = now
                    # READY may have appeared after the sample above was taken.
                    threads = base.host_threads(process.pid)
                    workers = [thread for thread in threads if thread["name"] == "affinity-live"]
                    if len(workers) != 7 or not any(cpu in parse_cpus(thread["host_cpus"]) for thread in workers):
                        raise RuntimeError("Hotplug target is not mapped to the homebrew workers")
                if ready is not None and len(changes) < len(phases):
                    delay, action = phases[len(changes)]
                    if now - ready >= delay:
                        stage(f"{name}: CPU {cpu} -> {action}")
                        answer = guard.request(action)
                        if (cpu in answer["online"]) != (action == "online"):
                            raise RuntimeError("Global CPU online mask did not match the hotplug operation")
                        changes.append({"seconds": time.monotonic() - ready, **answer})
                if now - heartbeat > 2:
                    guard.request("ping")
                    heartbeat = now
                time.sleep(0.05)
    finally:
        previous = {sig: signal.signal(sig, signal.SIG_IGN)
                    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            guard.close()
        finally:
            if process is not None:
                stop_owned(process)
            (evidence / f"{name}-threads.json").write_text(json.dumps(samples))
            (evidence / f"{name}-hotplug.json").write_text(json.dumps(changes, indent=2))
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    content = combined_logs(runtime, output, evidence, name)
    result = re.search(r"AFFINITY_DYNAMIC_RESULT failures=(\d+) samples=(\d+) workers=(\d+)", content)
    ok = (bool(result) and int(result[1]) == 0 and int(result[2]) > 0 and int(result[3]) == 7
          and len(changes) == 4 and process.returncode == 0 and not timed_out)
    stage(f"TEST={name} RESULT={'PASS' if ok else 'FAIL'}")
    return {"name": name, "ok": ok, "result": result.group(0) if result else None,
            "host_cpus": cpus, "cpu": cpu, "changes": changes, "returncode": process.returncode,
            "timeout": timed_out, "restored": True}


def parse_cpus(value):
    result = set()
    for item in value.strip().split(","):
        if item:
            first, _, last = item.partition("-")
            result.update(range(int(first), int(last or first) + 1))
    return result


def require_idle(owned_pid=None):
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == owned_pid:
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            name = (proc / "comm").read_text().strip().lower()
            executable = (proc / "exe").readlink().name.lower()
            if name.startswith("shadps4") or executable.startswith("shadps4"):
                found.append(proc.name)
        except (OSError, ValueError):
            continue
    if found:
        raise RuntimeError("Another shadPS4 is running (PIDs " + ", ".join(found) +
                           "); close it first. Only this test will be stopped.")


def stop_owned(process):
    if process.poll() is not None:
        return
    for sig, timeout in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=timeout)
            return
        except subprocess.TimeoutExpired:
            pass
    raise RuntimeError("The test process did not exit after SIGKILL: " + str(process.pid))


def load_base(work):
    path = work / "test_affinity_revision.py"
    download(HELPER_REVISION, "scripts/" + path.name, path, BASE_SHA, 256 * 1024)
    spec = importlib.util.spec_from_file_location("affinity_base", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    global EVIDENCE, HERE, base
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise SystemExit("This test requires Linux x86-64")
    cache = Path.home() / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "homebrew.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("BUSY=Another homebrew test is running; nothing started", flush=True)
            return 1
        evidence = Path(tempfile.mkdtemp(prefix="shadps4-hotplug-bitsets-", dir=Path.home()))
        EVIDENCE = evidence
        print("EVIDENCE=" + str(evidence), flush=True)
        summary = {"affinity_commit": AFFINITY_COMMIT, "candidate_tree": CANDIDATE_TREE,
                   "binary_sha256": BINARY_SHA, "runner_sha256": digest(Path(__file__)),
                   "suite_sha256": SUITE_SHA, "guard_sha256": GUARD_SHA,
                   "tests": [], "ok": False}
        installed = Path.home() / "Applications/shadps4/shadps4"
        before = None
        checked = False
        handlers = {sig: signal.signal(sig, cancel)
                    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            stage("Check running emulators and verify the tested candidate")
            require_idle()
            before = digest(installed) if installed.is_file() else None
            checked = True
            binary = cache / ("affinity-bitsets-build-" + AFFINITY_COMMIT[:12]) / "shadps4"
            if not binary.is_file() or digest(binary) != BINARY_SHA:
                raise RuntimeError("The exact tested candidate is missing or changed: " + str(binary))
            summary["binary"] = str(binary)
            initial_online = parse_cpus((CPU_ROOT / "online").read_text())
            allowed = sorted(os.sched_getaffinity(0) & initial_online)
            targets = [cpu for cpu in allowed if cpu > 0 and
                       (CPU_ROOT / f"cpu{cpu}/online").is_file() and
                       (CPU_ROOT / f"cpu{cpu}/online").read_text().strip() == "1"]
            if len(allowed) < 2 or len(initial_online) < 3 or not targets:
                raise RuntimeError("Need two permitted CPUs and three online CPUs, including a hotpluggable CPU")
            cpu = targets[-1]
            other = next(n for n in reversed(allowed) if n != cpu)
            summary.update(cpu=cpu, initial_online=sorted(initial_online), initial_allowed=allowed)
            with tempfile.TemporaryDirectory(prefix="hotplug-bitsets-", dir=cache) as folder:
                work = Path(folder)
                HERE = work
                base = load_base(work)
                download(HELPER_REVISION, "scripts/cpu_hotplug_guard.py",
                         work / "cpu_hotplug_guard.py", GUARD_SHA, 256 * 1024)
                download(SUITE_REVISION, "hardware/affinity_revision/suite.tar.gz",
                         work / "suite.tar.gz", SUITE_SHA)
                with tarfile.open(work / "suite.tar.gz") as archive:
                    archive.extractall(work / "suite", filter="data")
                stage("Find the display session")
                env = base.display_environment()
                require_idle()
                stage(f"sudo is needed to briefly offline and restore CPU {cpu}; watchdog enabled")
                if os.geteuid():
                    subprocess.run(["sudo", "-v"], check=True, timeout=120)
                require_idle()
                for name, cpus in (("two-cpu-hotplug", [other, cpu]), ("one-cpu-hotplug", [cpu])):
                    stage("Run " + name)
                    result = hotplug_case(binary, work / "suite", cpus, cpu, name, work, evidence, env)
                    summary["tests"].append(result)
                    (evidence / "progress.json").write_text(json.dumps(summary["tests"], indent=2))
                    if not result["ok"]:
                        raise RuntimeError("Hotplug homebrew failed: " + name)
                summary["final_online"] = sorted(parse_cpus((CPU_ROOT / "online").read_text()))
                summary["ok"] = (len(summary["tests"]) == 2 and
                                 summary["final_online"] == summary["initial_online"])
        except (Exception, KeyboardInterrupt) as error:
            summary["error"] = type(error).__name__ + ": " + str(error)
            summary["last_stage"] = LAST_STAGE
            (evidence / "error-traceback.log").write_text(traceback.format_exc())
            print("TEST_ERROR=" + summary["error"], flush=True)
            summary["ok"] = False
        finally:
            for sig in handlers:
                signal.signal(sig, signal.SIG_IGN)
            try:
                after = digest(installed) if installed.is_file() else None
                summary["installed_binary_unchanged"] = checked and before == after
                if "cpu" in summary:
                    summary["cpu_restored"] = (CPU_ROOT / f"cpu{summary['cpu']}/online").read_text().strip() == "1"
                    summary["final_online"] = sorted(parse_cpus((CPU_ROOT / "online").read_text()))
                summary["ok"] &= summary["installed_binary_unchanged"] and summary.get("cpu_restored", False)
                (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
                archive_path = evidence.with_suffix(".tar.gz")
                with tarfile.open(archive_path, "w:gz") as archive:
                    archive.add(evidence, arcname=evidence.name)
                print("HOTPLUG_SUITE=" + ("PASS" if summary["ok"] else "FAIL"), flush=True)
                print("UPLOAD_ONLY=" + str(archive_path), flush=True)
                print("SSH stays open. Installed emulator, saves and session guard were not edited.", flush=True)
            finally:
                EVIDENCE = HERE = None
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
        return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
