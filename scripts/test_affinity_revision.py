#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build an isolated affinity candidate and collect the OpenOrbis test results."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time


SOURCE_COMMIT = "82d07380b6090149e4df5d8092fd2e7925ad5007"
REPOSITORY = "https://github.com/Chreece/shadPS4.git"
SUITE_SHA256 = "b167f7d0e4cdbca03b7c9ba69cc47ec74773da968ea751032dee3c9d7ceee559"


def say(message):
    print(message, flush=True)


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def command(args, log, *, cwd=None, env=None, timeout=3600):
    say("STEP=" + " ".join(map(str, args)))
    with log.open("a") as output:
        output.write("\nCOMMAND=" + repr(list(map(str, args))) + "\n")
        output.flush()
        process = subprocess.Popen(
            list(map(str, args)), cwd=cwd, env=env, stdout=output,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
        )
        started = update = time.monotonic()
        try:
            while process.poll() is None:
                now = time.monotonic()
                if now - started > timeout:
                    raise RuntimeError("Command timed out: " + str(args[0]))
                if now - update >= 15:
                    tail = log.read_text(errors="replace").splitlines()[-1:]
                    say(f"  running {int(now-started)}s: " + (tail[0][-200:] if tail else ""))
                    update = now
                time.sleep(0.25)
        finally:
            stop(process)
        if process.returncode != 0:
            raise RuntimeError(f"Command failed ({process.returncode}); see {log.name}")


def compiler(work, log):
    smoke = work / "compiler-smoke.cpp"
    smoke.write_text(
        '#include <format>\n#include <ranges>\n#include <vector>\n'
        'int main(){ auto v=std::views::iota(0,2)|std::ranges::to<std::vector>();'
        'return std::format("{}",v.size())!="2";}\n'
    )
    pairs = [(f"clang-{n}", f"clang++-{n}") for n in [20, 19]]
    pairs += [(f"gcc-{n}", f"g++-{n}") for n in [15, 14]]
    pairs += [("clang", "clang++"), ("gcc", "g++")]
    for cc, cxx in pairs:
        cc_path, cxx_path = shutil.which(cc), shutil.which(cxx)
        if not cc_path or not cxx_path:
            continue
        with log.open("a") as out:
            result = subprocess.run(
                [cxx_path, "-std=c++23", str(smoke), "-o", str(work / "compiler-smoke")],
                stdout=out, stderr=out, timeout=60,
            )
        if result.returncode == 0:
            result = subprocess.run([str(work / "compiler-smoke")], timeout=10)
            if result.returncode == 0:
                return cc_path, cxx_path
    raise RuntimeError("No working C++23 toolchain found (Clang 19 or GCC 14 with matching C++ headers required)")


def display_environment():
    env = os.environ.copy()
    wanted = {"DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR"}
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                if proc.stat().st_uid != os.getuid():
                    continue
                name = (proc / "comm").read_text().strip().lower()
                if name not in {"sunshine", "gnome-shell", "kwin_wayland", "xfce4-session"}:
                    continue
                entries = (proc / "environ").read_bytes().split(b"\0")
                for entry in entries:
                    key, sep, value = entry.partition(b"=")
                    if sep and key.decode(errors="replace") in wanted:
                        env[key.decode()] = value.decode()
                if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
                    break
            except (OSError, UnicodeError):
                continue
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        sockets = sorted(Path("/tmp/.X11-unix").glob("X[0-9]*"))
        if sockets:
            env["DISPLAY"] = ":" + sockets[0].name[1:]
            authority = Path.home() / ".Xauthority"
            if authority.is_file():
                env["XAUTHORITY"] = str(authority)
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        raise RuntimeError("No running display found for the emulator tests")
    env["ALSOFT_DRIVERS"] = "null"
    env["SDL_AUDIODRIVER"] = "dummy"
    return env


def existing_emulators():
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid == os.getuid() and (proc / "comm").read_text().strip().lower() in {
                "shadps4", "shadps4-bin", "shadps4.exe"
            }:
                found.append(proc.name)
        except OSError:
            pass
    return found


def host_threads(pid):
    records = []
    for path in Path(f"/proc/{pid}/task").glob("*/status"):
        try:
            fields = dict(line.split(":", 1) for line in path.read_text().splitlines() if ":" in line)
            records.append({"tid": path.parent.name, "name": fields.get("Name", "").strip(),
                            "host_cpus": fields.get("Cpus_allowed_list", "").strip()})
        except OSError:
            pass
    return records


def run_case(binary, case, suite, cpus, label, work, evidence, env):
    name = f"{label}-{case['directory']}"
    runtime = work / name
    (runtime / "user").mkdir(parents=True)
    (runtime / "user/config.json").write_text(json.dumps({
        "GPU": {"null_gpu": True, "full_screen": False, "window_width": 640, "window_height": 360},
        "Log": {"filter": "*:Info", "sync": True, "skip_duplicate": False},
        "General": {"show_splash": False},
    }))
    output = evidence / f"{name}.log"
    args = ["taskset", "-c", ",".join(map(str, cpus)), str(binary),
            "--ignore-game-patch", str(suite / case["directory"] / "eboot.bin")]
    samples = []
    timed_out = False
    with output.open("w") as log:
        process = subprocess.Popen(args, cwd=runtime, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if time.monotonic() - started > 45:
                    timed_out = True
                    break
                samples.append(host_threads(process.pid))
                time.sleep(0.1)
        finally:
            stop(process)
    logs = list((runtime / "user/log").glob("*"))
    for path in logs:
        if path.is_file():
            shutil.copy2(path, evidence / f"{name}-{path.name}")
    content = output.read_text(errors="replace")
    if "[Test passed!]" not in content and "AFFINITY_RESULT" not in content:
        content += "\n" + "\n".join(p.read_text(errors="replace") for p in logs if p.is_file())
    passes = content.count("[Test passed!]")
    failures = content.count("[Test FAILED!]")
    extended = re.search(r"AFFINITY_RESULT failures=(\d+) samples=(\d+)", content)
    checks_ok = (bool(extended) and int(extended[1]) == 0 and int(extended[2]) > 0
                 if case.get("extended") else failures == 0 and passes == case["expected_passes"])
    ok = checks_ok and process.returncode == 0 and not timed_out
    result = {"name": name, "host_cpus": cpus, "returncode": process.returncode,
              "timeout": timed_out, "passes": passes, "failures": failures, "ok": ok,
              "extended": extended.group(0) if extended else None}
    (evidence / f"{name}-threads.json").write_text(json.dumps(samples, indent=2))
    say(f"TEST={name} RESULT={'PASS' if ok else 'FAIL'}")
    if passes == 0 and failures == 0 and extended is None:
        result["infrastructure_failure"] = True
    return result


def main():
    cache = Path.home() / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-affinity-evidence-", dir=Path.home()))
    work = Path(tempfile.mkdtemp(prefix="runtime-", dir=cache))
    summary = {"source_commit": SOURCE_COMMIT, "tests": [], "installed_binary_changed": False}
    try:
        active = existing_emulators()
        if active:
            raise RuntimeError("Close the running shadPS4 session and rerun this command; PIDs=" + ",".join(active))
        allowed = sorted(os.sched_getaffinity(0))
        summary["initial_host_cpus"] = allowed
        summary["uname"] = list(os.uname())
        env = display_environment()
        log = evidence / "build.log"
        cc, cxx = compiler(work, log)
        summary["compiler"] = cxx
        source = cache / "source"
        if not source.exists():
            source.mkdir()
            command(["git", "init", source], log)
            command(["git", "remote", "add", "origin", REPOSITORY], log, cwd=source)
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source, text=True)
        if dirty.strip():
            raise RuntimeError("The isolated source checkout has local changes; preserved")
        command(["git", "fetch", "--depth", "1", "--no-tags", "--no-recurse-submodules",
                 "origin", SOURCE_COMMIT], log, cwd=source)
        command(["git", "checkout", "--detach", SOURCE_COMMIT], log, cwd=source)
        command(["git", "submodule", "update", "--init", "--recursive", "--jobs", "4"], log, cwd=source)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        if head != SOURCE_COMMIT:
            raise RuntimeError("Source revision mismatch")
        build = cache / ("build-" + SOURCE_COMMIT[:12])
        configure = ["cmake", "-S", source, "-B", build,
                     "-DCMAKE_BUILD_TYPE=Release", "-DENABLE_TESTS=OFF",
                     "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF",
                     "-DCMAKE_CXX_SCAN_FOR_MODULES=OFF",
                     "-DCMAKE_C_COMPILER=" + cc, "-DCMAKE_CXX_COMPILER=" + cxx,
                     "-DENABLE_UPDATER=OFF"]
        if shutil.which("ninja") and not (build / "CMakeCache.txt").exists():
            configure += ["-G", "Ninja"]
        command(configure, log)
        command(["cmake", "--build", build, "--target", "shadps4", "--parallel", str(min(8, len(allowed)))], log)
        binary = build / "shadps4"
        summary["binary"] = str(binary)
        summary["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        suite_archive = source / "hardware/affinity_revision/suite.tar.gz"
        if hashlib.sha256(suite_archive.read_bytes()).hexdigest() != SUITE_SHA256:
            raise RuntimeError("Homebrew suite checksum mismatch")
        suite = work / "suite"
        suite.mkdir()
        with tarfile.open(suite_archive) as archive:
            for member in archive.getmembers():
                target = (suite / member.name).resolve()
                if not target.is_relative_to(suite.resolve()) or not member.isfile():
                    raise RuntimeError("Unexpected suite entry")
            archive.extractall(suite, filter="data")
        cases = json.loads((suite / "cases.json").read_text())
        profiles = [("all", allowed), ("four", allowed[-4:]), ("two", allowed[-2:]),
                    ("one", allowed[-1:]), ("sparse", allowed[1::2][:4] or allowed[:1])]
        seen = set()
        for label, cpus in profiles:
            if tuple(cpus) in seen:
                continue
            seen.add(tuple(cpus))
            selected = cases if label in {"all", "two", "one"} else [cases[-1]]
            for case in selected:
                result = run_case(binary, case, suite, cpus, label, work, evidence, env)
                summary["tests"].append(result)
                if result.get("infrastructure_failure"):
                    raise RuntimeError("The homebrew did not reach its checks; startup evidence collected")
        summary["ok"] = bool(summary["tests"]) and all(r["ok"] for r in summary["tests"])
        say("AFFINITY_SUITE=" + ("PASS" if summary["ok"] else "FAIL"))
    except Exception as error:
        summary["ok"] = False
        summary["error"] = str(error)
        say("AFFINITY_TEST_ERROR=" + str(error))
    finally:
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        archive_path = evidence.with_suffix(".tar.gz")
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(evidence, arcname=evidence.name)
        shutil.rmtree(work)
        say("UPLOAD_ONLY=" + str(archive_path))
        say("Your installed emulator was not replaced. The SSH session remains open.")
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
