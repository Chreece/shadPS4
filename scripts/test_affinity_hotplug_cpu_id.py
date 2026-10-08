#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Collect isolated Linux CPU hotplug and generated CPU identity test evidence."""

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
import urllib.request

AFFINITY_COMMIT = "53d152a07bf08438c5fba84e09deec014d9a891a"
AFFINITY_BINARY_SHA = "e924d095eebeb078d04b5182fc0406fa3624e0c68bec30e877692fa169962456"
CPU_ID_COMMIT = "bb4db504218ebf4502cace3cadb3cf581ec54525"
RUNTIME_COMMIT = "a522a505582076eb7f68363b5d301ddca44399e2"
TEST_COMMIT = "332ade4c6119898d6f2e02a5b3ff44212122ab7f"
TEST_SHA = "9af31829fe1831a9ee5ed9d5b598989961140a25949284c26abd8f4c5033f938"
FAULTING_SHA = "202fe4f4e0d7cadf40c5af525206acd34d2b6448f89f29df3e5a6135bd259dad"
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("affinity_base", HERE / "test_affinity_inheritance.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base_stop = base.stop


def stop_owned(process):
    handlers = {sig: signal.signal(sig, signal.SIG_IGN)
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        base_stop(process)
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


base.stop = stop_owned


def cancel(signum, frame):
    raise KeyboardInterrupt()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(url, expected, destination):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(4 * 1024 * 1024 + 1)
            if hashlib.sha256(data).hexdigest() != expected:
                raise RuntimeError("Download checksum mismatch: " + destination.name)
            destination.write_bytes(data)
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1)


def extract(archive_path, destination):
    destination.mkdir()
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            if not member.isfile() or not (destination / member.name).resolve().is_relative_to(destination.resolve()):
                raise RuntimeError("Unexpected homebrew archive entry")
        archive.extractall(destination, filter="data")


def checkout(source, repository, revision, log):
    if not (source / ".git").is_dir():
        if source.exists() and any(source.iterdir()):
            raise RuntimeError("Cached source is not a Git checkout; preserved: " + str(source))
        source.mkdir(parents=True, exist_ok=True)
        base.command(["git", "init", source], log)
        base.command(["git", "remote", "add", "origin", repository], log, cwd=source)
    current = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=source,
                             text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if current.returncode != 0:
        base.command(["git", "fetch", "--depth", "1", "--no-tags", "--no-recurse-submodules",
                      repository, revision], log, cwd=source)
        base.command(["git", "checkout", "--detach", revision], log, cwd=source)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if head != revision:
        raise RuntimeError("Cached source has a different revision; preserved: " + str(source))


def build_cpu_id(cache, work, evidence, allowed):
    log = evidence / "cpu-id-build.log"
    cc, cxx = base.compiler(work, log)
    source = cache / ("cpu-id-source-" + CPU_ID_COMMIT[:12])
    checkout(source, base.REPOSITORY, CPU_ID_COMMIT, log)
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source).strip():
        raise RuntimeError("CPU-ID source has local changes; preserved")
    base.command(["git", "submodule", "update", "--init", "--recursive", "--jobs", "4"], log, cwd=source)
    runtime_source = cache / ("runtime-source-" + RUNTIME_COMMIT[:12])
    runtime_build = cache / ("runtime-build-" + RUNTIME_COMMIT[:12])
    checkout(runtime_source, "https://github.com/DynamoRIO/dynamorio.git", RUNTIME_COMMIT, log)
    patch = source / "src/core/cpu_id_translation/dynamorio.patch"
    diff = subprocess.check_output(["git", "diff", "HEAD", "--"], cwd=runtime_source)
    if not diff:
        base.command(["git", "apply", "--check", patch], log, cwd=runtime_source)
        base.command(["git", "apply", patch], log, cwd=runtime_source)
    elif diff != patch.read_bytes():
        raise RuntimeError("Translation runtime contains other changes; preserved")
    base.command(["git", "submodule", "update", "--init", "third_party/elfutils"], log, cwd=runtime_source)
    jobs = str(min(6, len(allowed)))
    base.command(["cmake", "-S", runtime_source, "-B", runtime_build, "-G", "Ninja",
                  "-DCMAKE_BUILD_TYPE=RelWithDebInfo", "-DBUILD_TESTS=OFF", "-DBUILD_SAMPLES=OFF",
                  "-DBUILD_CLIENTS=OFF", "-DBUILD_DOCS=OFF", "-DBUILD_EXT=ON",
                  "-DDISABLE_DRGUI=ON", "-DDISABLE_WARNINGS=ON",
                  "-Dpreferred_base=0x710000000000", "-DPREFERRED_BASE=0x710040000000"], log)
    base.command(["cmake", "--build", runtime_build, "--target", "dynamorio", "drrun", "drmgr", "drwrap",
                  "--parallel", jobs], log)
    build = cache / ("build-cpu-id-" + CPU_ID_COMMIT[:12])
    base.command(["cmake", "-S", source, "-B", build, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                  "-DENABLE_TESTS=OFF", "-DENABLE_UPDATER=OFF", "-DCMAKE_CXX_SCAN_FOR_MODULES=OFF",
                  "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF", "-DCMAKE_C_COMPILER=" + cc,
                  "-DCMAKE_CXX_COMPILER=" + cxx, "-DENABLE_CPU_ID_TRANSLATION=ON",
                  "-DDynamoRIO_DIR=" + str(runtime_build / "cmake")], log)
    base.command(["cmake", "--build", build, "--target", "shadps4", "shadps4_cpu_id",
                  "--parallel", jobs], log)
    binary = build / "shadps4"
    client = build / "src/core/cpu_id_translation/libshadps4_cpu_id.so"
    drrun = runtime_build / "bin64/drrun"
    prefix = [str(drrun), "-disable_rseq", "-vm_base", "0x710020000000",
              "-no_vm_base_near_app", "-c", str(client), "--", str(binary)]
    return binary, prefix, {"commit": CPU_ID_COMMIT, "runtime_commit": RUNTIME_COMMIT,
                            "compiler": cxx, "binary": str(binary), "binary_sha256": digest(binary),
                            "client_sha256": digest(client), "launcher_sha256": digest(drrun),
                            "runtime_patch_sha256": digest(patch)}


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
                                        stderr=self.log)
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
        path = Path(f"/sys/devices/system/cpu/cpu{self.cpu}/online")
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
    base.require_idle()
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
        with output.open("w") as log:
            process = subprocess.Popen(["taskset", "-c", ",".join(map(str, cpus)), str(binary),
                                        "--ignore-game-patch", str(suite / "CPUM00007/eboot.bin")],
                                       cwd=runtime, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            start = heartbeat = time.monotonic()
            while process.poll() is None:
                now = time.monotonic()
                if now - start > 45:
                    timed_out = True
                    base.capture_process(process.pid, evidence / f"{name}-process.txt")
                    break
                threads = base.host_threads(process.pid)
                samples.append({"seconds": now - start, "threads": threads})
                if ready is None and "AFFINITY_DYNAMIC_READY workers=7" in output.read_text(errors="replace"):
                    ready = now
                    workers = [thread for thread in threads if thread["name"] == "affinity-live"]
                    if len(workers) != 7 or not any(cpu in parse_cpus(thread["host_cpus"]) for thread in workers):
                        raise RuntimeError("Hotplug target is not mapped to the homebrew workers")
                if ready is not None and len(changes) < len(phases):
                    delay, action = phases[len(changes)]
                    if now - ready >= delay:
                        answer = guard.request(action)
                        if (cpu in answer["online"]) != (action == "online"):
                            raise RuntimeError("Global CPU online mask did not match the hotplug operation")
                        changes.append({"seconds": time.monotonic() - ready, **answer})
                if now - heartbeat > 2:
                    guard.request("ping")
                    heartbeat = now
                time.sleep(0.05)
    finally:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            guard.close()
        finally:
            if process is not None:
                base.stop(process)
            (evidence / f"{name}-threads.json").write_text(json.dumps(samples))
            (evidence / f"{name}-hotplug.json").write_text(json.dumps(changes, indent=2))
            signal.signal(signal.SIGINT, previous)
    content = combined_logs(runtime, output, evidence, name)
    result = re.search(r"AFFINITY_DYNAMIC_RESULT failures=(\d+) samples=(\d+) workers=(\d+)", content)
    ok = (bool(result) and int(result[1]) == 0 and int(result[2]) > 0 and int(result[3]) == 7
          and len(changes) == 4 and process.returncode == 0 and not timed_out)
    base.say(f"TEST={name} RESULT={'PASS' if ok else 'FAIL'}")
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


def record_cpu_id(summary, result):
    summary["cpu_id"].append(result)
    if result.get("timeout") or result.get("infrastructure_failure") or (
            "result" in result and result["result"] is None):
        raise RuntimeError("CPU-ID homebrew could not finish its checks: " + result["name"])


def run_generated(prefix, case, suite, cpus, name, work, evidence, env, translated=False, no_faulting=False):
    base.require_idle()
    runtime = work / name
    base.prepare_runtime(runtime)
    output = evidence / f"{name}.log"
    args = ["taskset", "-c", ",".join(map(str, cpus)), *prefix,
            "--ignore-game-patch", str(suite / case["name"] / "eboot.bin")]
    timed_out = False
    with output.open("w") as log:
        process = subprocess.Popen(args, cwd=runtime, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                timed_out = True
                base.capture_process(process.pid, evidence / f"{name}-process.txt")
        finally:
            base.stop(process)
    content = combined_logs(runtime, output, evidence, name)
    if "samples" in case:
        match = re.search(r"GENERATED_CPU_ID_RESULT operation=(\w+) failures=(\d+) samples=(\d+) signals=(\d+)", content)
        ok = (bool(match) and match[1] == case["name"].upper() and int(match[2]) == 0
              and int(match[3]) == case["samples"] and int(match[4]) == case["signals"])
    else:
        label, field = ("THREAD_RECYCLE", "completed") if case["name"] == "thread_recycle" else ("SIGNAL_REQUEUE", "received")
        match = re.search(label + r"_RESULT " + field + r"=(\d+) failures=(\d+)", content)
        ok = bool(match) and int(match[1]) == case["expected"] and int(match[2]) == 0
    active = "CPU identity translation active" in content
    disabled = "CPUID_FAULTING=forced_unavailable" in content
    ok = (ok and process.returncode == 0 and not timed_out and active == translated
          and (not no_faulting or disabled))
    base.say(f"TEST={name} RESULT={'PASS' if ok else 'FAIL'}")
    return {"name": name, "ok": bool(ok), "result": match.group(0) if match else None,
            "returncode": process.returncode, "timeout": timed_out, "host_cpus": cpus,
            "translation_active": active, "cpuid_faulting_disabled": disabled}


def main():
    cache = Path.home() / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="hotplug-cpu-id-", dir=cache))
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-hotplug-cpu-id-", dir=Path.home()))
    summary = {"affinity_commit": AFFINITY_COMMIT, "cpu_id_commit": CPU_ID_COMMIT,
               "runner_sha256": digest(Path(__file__)), "installed_binary_changed": False,
               "guard_sha256": digest(HERE / "cpu_hotplug_guard.py"),
               "generated_suite_commit": TEST_COMMIT, "generated_suite_sha256": TEST_SHA,
               "affinity_suite_commit": base.SUITE_COMMIT, "affinity_suite_sha256": base.SUITE_SHA256,
               "hotplug": [], "cpu_id": []}
    lock = (cache / "homebrew.lock").open("a")
    handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        base.require_idle()
        allowed = sorted(os.sched_getaffinity(0))
        summary["host_cpus"] = allowed
        summary["uname"] = list(os.uname())
        env = base.display_environment()
        binary = cache / ("build-" + AFFINITY_COMMIT[:12]) / "shadps4"
        if not binary.is_file() or digest(binary) != AFFINITY_BINARY_SHA:
            raise RuntimeError("The exact affinity binary from your passing archive is missing or changed; preserved")
        summary["affinity_binary_sha256"] = digest(binary)
        affinity_archive = work / "affinity-suite.tar.gz"
        download(f"https://raw.githubusercontent.com/Chreece/shadPS4/{base.SUITE_COMMIT}/hardware/affinity_revision/suite.tar.gz",
                 base.SUITE_SHA256, affinity_archive)
        affinity_suite = work / "affinity-suite"
        extract(affinity_archive, affinity_suite)
        online = parse_cpus(Path("/sys/devices/system/cpu/online").read_text())
        targets = [cpu for cpu in allowed if cpu > 0 and cpu in online
                   and Path(f"/sys/devices/system/cpu/cpu{cpu}/online").is_file()]
        if len(allowed) < 2 or len(online) < 3 or not targets:
            summary["hotplug_error"] = "No suitable online CPU and spare controller CPUs; hotplug was not tested"
        else:
            cpu = targets[-1]
            summary["hotplug_cpu"] = cpu
            base.say(f"HOTPLUG=CPU {cpu}; two brief offline/online cycles per case, with restoration watchdog")
            base.say("SUDO=Only the CPU hotplug controller needs root; the build and emulator run as your user")
            try:
                if os.geteuid() != 0:
                    with open("/dev/tty") as terminal:
                        subprocess.run(["sudo", "-v"], stdin=terminal, check=True)
                support = next(value for value in reversed(allowed) if value != cpu)
                for name, cpus in [("hotplug-two", sorted([support, cpu])), ("hotplug-one", [cpu])]:
                    summary["hotplug"].append(hotplug_case(binary, affinity_suite, cpus, cpu, name, work, evidence, env))
            except Exception as error:
                summary["hotplug_error"] = str(error)
                base.say("HOTPLUG_ERROR=" + str(error))
                if Path(f"/sys/devices/system/cpu/cpu{cpu}/online").read_text().strip() != "1":
                    raise RuntimeError("CPU restoration failed; further tests stopped") from error
        base.say("CPU_ID_BUILD=" + CPU_ID_COMMIT + "; optional translation experiment")
        binary, translated, metadata = build_cpu_id(cache, work, evidence, allowed)
        summary["cpu_id_build"] = metadata
        generated_archive = work / "generated-suite.tar.gz"
        download(f"https://raw.githubusercontent.com/Chreece/shadPS4/{TEST_COMMIT}/hardware/generated_cpu_id/translation-suite.tar.gz",
                 TEST_SHA, generated_archive)
        suite = work / "generated-suite"
        extract(generated_archive, suite)
        cases = json.loads((suite / "cases.json").read_text())
        for case in cases:
            if digest(suite / case["name"] / "eboot.bin") != case["sha256"]:
                raise RuntimeError("Homebrew binary checksum mismatch")
        faulting = work / "without_cpuid_faulting.py"
        download(f"https://raw.githubusercontent.com/Chreece/shadPS4/{TEST_COMMIT}/hardware/generated_cpu_id/without_cpuid_faulting.py",
                 FAULTING_SHA, faulting)
        profiles = [("all", allowed), ("four", allowed[-4:]), ("two", allowed[-2:]),
                    ("one", allowed[-1:]), ("sparse", allowed[1::2][:4] or allowed[:1])]
        summary["expected_cpu_id_tests"] = 4 * len({tuple(cpus) for _, cpus in profiles}) + 9
        seen = set()
        for label, cpus in profiles:
            if tuple(cpus) in seen:
                continue
            seen.add(tuple(cpus))
            for case in cases:
                if "samples" in case:
                    record_cpu_id(summary, run_generated(translated, case, suite, cpus,
                        "translated-" + label + "-" + case["name"], work, evidence, env, translated=True))
        for case in cases:
            if case["name"] in {"cpuid", "rdtscp"}:
                record_cpu_id(summary, run_generated([sys.executable, str(faulting), *translated],
                    case, suite, allowed, "no-faulting-" + case["name"], work, evidence, env,
                    translated=True, no_faulting=True))
            if "expected" in case:
                for label, prefix in [("native", [str(binary)]), ("translated", translated)]:
                    record_cpu_id(summary, run_generated(prefix, case, suite, allowed,
                        label + "-" + case["name"], work, evidence, env, translated=label == "translated"))
        static_case = next(case for case in json.loads((affinity_suite / "cases.json").read_text()) if case.get("cpu_id"))
        for label, cpus in [("all", allowed), ("two", allowed[-2:]), ("one", allowed[-1:])]:
            record_cpu_id(summary, base.run_case(binary, static_case, affinity_suite, cpus,
                                                  "static-" + label, work, evidence, env))
        summary["ok"] = (len(summary["hotplug"]) == 2 and not summary.get("hotplug_error")
                         and len(summary["cpu_id"]) == summary["expected_cpu_id_tests"]
                         and all(case["ok"] for case in summary["hotplug"] + summary["cpu_id"]))
    except KeyboardInterrupt:
        summary["error"] = "Interrupted; owned test processes stopped and hotplug restoration requested"
        summary["ok"] = False
        base.say("INTERRUPTED=Collecting evidence")
    except Exception as error:
        summary["error"] = str(error)
        summary["ok"] = False
        base.say("TEST_ERROR=" + str(error))
    finally:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        for sig in handlers:
            signal.signal(sig, signal.SIG_IGN)
        try:
            if "hotplug_cpu" in summary:
                cpu = summary["hotplug_cpu"]
                summary["cpu_restored"] = Path(f"/sys/devices/system/cpu/cpu{cpu}/online").read_text().strip() == "1"
            for path in work.glob("*/user/log/*"):
                if path.is_file():
                    shutil.copy2(path, evidence / f"{path.parents[2].name}-{path.name}")
            (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            archive_path = evidence.with_suffix(".tar.gz")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(evidence, arcname=evidence.name)
            shutil.rmtree(work)
            base.say("HOTPLUG=" + str(sum(case["ok"] for case in summary["hotplug"])) + "/2 passed")
            base.say("CPU_ID=" + (str(sum(case["ok"] for case in summary["cpu_id"])) + "/" + str(len(summary["cpu_id"])) + " passed"
                                   if summary["cpu_id"] else "NOT_RUN"))
            base.say("RESULT=" + ("PASS" if summary.get("ok") else "INCOMPLETE_OR_FAILED"))
            base.say("UPLOAD_ONLY=" + str(archive_path))
            base.say("Installed emulator unchanged. SSH remains open.")
        finally:
            lock.close()
            signal.signal(signal.SIGINT, previous)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
