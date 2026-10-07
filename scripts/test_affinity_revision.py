#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later

"""Build an isolated affinity candidate and collect the OpenOrbis test results."""

import argparse
import fcntl
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
import urllib.request


SOURCE_COMMIT = "72b19918e262154fca7065b2568976d6a04990c8"
REPOSITORY = "https://github.com/Chreece/shadPS4.git"
SUITE_COMMIT = "72b19918e262154fca7065b2568976d6a04990c8"
SUITE_SHA256 = "cb997d875b09d2a6429c9cf23566ed5a59193dfb0b44e91fa722298cd8ead393"


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
    env["SHADPS4_ENABLE_IPC"] = "false"
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


def prepare_runtime(runtime):
    user = runtime / "user"
    user.mkdir(parents=True)
    users = []
    for index in range(4):
        user_id = 1000 + index
        for directory in ("savedata", "trophy", "inputs"):
            (user / "home" / str(user_id) / directory).mkdir(parents=True)
        users.append({"user_id": user_id, "user_name": f"Affinity Test {index + 1}",
                      "user_color": index + 1, "player_index": index + 1,
                      "shadnet_enabled": False})
    (user / "users.json").write_text(json.dumps({"Users": {"user": users}}))
    (user / "config.json").write_text(json.dumps({
        "GPU": {"null_gpu": True, "full_screen": False, "window_width": 640, "window_height": 360},
        "Log": {"filter": "*:Info", "flush_level": "info", "sync": True, "skip_duplicate": False},
        "General": {"show_splash": False, "home_dir": str(user / "home")},
    }))


def capture_process(pid, destination):
    lines = []
    paths = [Path(f"/proc/{pid}/status"), Path(f"/proc/{pid}/maps")]
    for task in Path(f"/proc/{pid}/task").glob("*"):
        paths += [task / name for name in ("status", "wchan", "syscall", "stack")]
    for path in paths:
        try:
            content = path.read_text(errors="replace")
        except OSError as error:
            content = str(error)
        lines.append(str(path) + "\n" + content)
    destination.write_text("\n\n".join(lines))


def diagnose_startup(args, runtime, evidence, name, env):
    debugger = shutil.which("gdb")
    if debugger is None:
        (evidence / f"{name}-backtrace.log").write_text("gdb is not installed\n")
        return
    commands = runtime / "diagnostic.gdb"
    commands.write_text(
        "set pagination off\nset confirm off\nset debuginfod enabled off\n"
        "set disable-randomization off\nset startup-with-shell off\n"
        "set print thread-events off\nset print frame-arguments none\n"
        "handle SIGSEGV SIGBUS SIGILL SIGFPE SIGSYS SIGUSR1 SIGUSR2 nostop noprint pass\n"
        "run\nthread apply all bt 24\nkill\nquit\n"
    )
    say(f"DIAGNOSTIC={name} capturing startup backtrace")
    with (evidence / f"{name}-backtrace.log").open("w") as log:
        process = subprocess.Popen(
            [debugger, "--nx", "--nh", "--batch", "-x", str(commands), "--args", *map(str, args)],
            cwd=runtime, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            stop(process)


def run_case(binary, case, suite, cpus, label, work, evidence, env):
    name = f"{label}-{case['directory']}"
    runtime = work / name
    prepare_runtime(runtime)
    output = evidence / f"{name}.log"
    args = ["taskset", "-c", ",".join(map(str, cpus)), str(binary),
            "--ignore-game-patch", str(suite / case["directory"] / "eboot.bin")]
    samples = []
    timed_out = False
    changes = []
    dynamic_started = None
    next_change = 0
    change_masks = [cpus[-1:], sorted(set(cpus[::2] + cpus[-1:])), cpus[:1], cpus] * 3
    with output.open("w") as log:
        process = subprocess.Popen(args, cwd=runtime, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if time.monotonic() - started > 45:
                    timed_out = True
                    capture_process(process.pid, evidence / f"{name}-process.txt")
                    break
                threads = host_threads(process.pid)
                samples.append(threads)
                if case.get("dynamic"):
                    now = time.monotonic()
                    if dynamic_started is None and "AFFINITY_DYNAMIC_READY" in output.read_text(errors="replace"):
                        dynamic_started = now
                    if (dynamic_started is not None and next_change < len(change_masks)
                            and now - dynamic_started >= next_change * 0.4):
                        mask = change_masks[next_change]
                        tids = [int(t["tid"]) for t in threads if t["name"] == "affinity-live"]
                        if len(tids) != case["expected_workers"]:
                            raise RuntimeError("Dynamic homebrew workers were not found; no host masks changed")
                        for tid in tids:
                            os.sched_setaffinity(tid, mask)
                        changes.append({"phase": next_change, "seconds": now - dynamic_started,
                                        "host_cpus": mask, "tids": tids})
                        next_change += 1
                time.sleep(0.1)
        finally:
            stop(process)
            (evidence / f"{name}-threads.json").write_text(json.dumps(samples, indent=2))
            (evidence / f"{name}-host-changes.json").write_text(json.dumps(changes, indent=2))
    logs = list((runtime / "user/log").glob("*"))
    for path in logs:
        if path.is_file():
            shutil.copy2(path, evidence / f"{name}-{path.name}")
    content = output.read_text(errors="replace")
    if not any(marker in content for marker in ("[Test passed!]", "AFFINITY_RESULT", "AFFINITY_DYNAMIC_RESULT")):
        content += "\n" + "\n".join(p.read_text(errors="replace") for p in logs if p.is_file())
    passes = content.count("[Test passed!]")
    failures = content.count("[Test FAILED!]")
    extended = re.search(r"AFFINITY_RESULT failures=(\d+) samples=(\d+)", content)
    initial_mask = re.search(r"AFFINITY_START mask=([0-9a-fA-F]+)", content)
    dynamic = re.search(r"AFFINITY_DYNAMIC_RESULT failures=(\d+) samples=(\d+) workers=(\d+)", content)
    if case.get("dynamic"):
        checks_ok = (bool(dynamic) and int(dynamic[1]) == 0 and int(dynamic[2]) > 0
                     and int(dynamic[3]) == case["expected_workers"]
                     and len(changes) == len(change_masks))
    elif case.get("extended"):
        checks_ok = (bool(extended) and int(extended[1]) == 0
                     and int(extended[2]) == case["expected_samples"]
                     and bool(initial_mask) and int(initial_mask[1], 16) == case["expected_mask"])
    else:
        checks_ok = failures == 0 and passes == case["expected_passes"]
    ok = checks_ok and process.returncode == 0 and not timed_out
    result = {"name": name, "host_cpus": cpus, "returncode": process.returncode,
              "timeout": timed_out, "passes": passes, "failures": failures, "ok": ok,
              "extended": extended.group(0) if extended else None,
              "dynamic": dynamic.group(0) if dynamic else None,
              "host_mask_changes": changes,
              "guest_mask": int(initial_mask[1], 16) if initial_mask else None}
    say(f"TEST={name} RESULT={'PASS' if ok else 'FAIL'}")
    if passes == 0 and failures == 0 and extended is None and dynamic is None:
        result["infrastructure_failure"] = True
        diagnose_startup(args, runtime, evidence, name, env)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse-build", action="store_true",
                        help="Reuse the cached candidate without configuring or building")
    parser.add_argument("--binary-sha256", help="Required checksum when reusing a verified build")
    parser.add_argument("--case", action="append", help="Run only the named homebrew case")
    args = parser.parse_args()
    if args.reuse_build and not re.fullmatch(r"[0-9a-f]{64}", args.binary_sha256 or ""):
        parser.error("--reuse-build requires --binary-sha256 from the previous evidence archive")
    cache = Path.home() / ".cache/shadps4-affinity-20261007"
    cache.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="shadps4-affinity-evidence-", dir=Path.home()))
    work = Path(tempfile.mkdtemp(prefix="runtime-", dir=cache))
    summary = {"source_commit": SOURCE_COMMIT, "suite_commit": SUITE_COMMIT,
               "tests": [], "installed_binary_changed": False,
               "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    lock = (cache / "homebrew.lock").open("a")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another affinity homebrew test is already running") from None
        active = existing_emulators()
        if active:
            raise RuntimeError("Close the running shadPS4 session and rerun this command; PIDs=" + ",".join(active))
        allowed = sorted(os.sched_getaffinity(0))
        summary["initial_host_cpus"] = allowed
        summary["uname"] = list(os.uname())
        env = display_environment()
        log = evidence / "build.log"
        binary = cache / ("build-" + SOURCE_COMMIT[:12]) / "shadps4"
        if args.reuse_build:
            if hashlib.sha256(binary.read_bytes()).hexdigest() != args.binary_sha256:
                raise RuntimeError("Cached binary differs from the verified build; preserved")
            summary["reused_build"] = True
        else:
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
        suite_archive = work / "suite.tar.gz"
        suite_url = (f"https://raw.githubusercontent.com/Chreece/shadPS4/{SUITE_COMMIT}/"
                     "hardware/affinity_revision/suite.tar.gz")
        with urllib.request.urlopen(suite_url, timeout=30) as response:
            suite_archive.write_bytes(response.read())
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
        if args.case:
            unknown = set(args.case) - {case["directory"] for case in cases}
            if unknown:
                raise RuntimeError("Unknown homebrew case: " + ",".join(sorted(unknown)))
            cases = [case for case in cases if case["directory"] in args.case]
        startup = work / "startup-check"
        prepare_runtime(startup)
        startup_args = [binary, "--add-game-folder", suite]
        try:
            command(startup_args, evidence / "startup-check.log", cwd=startup, env=env, timeout=15)
        except Exception:
            diagnose_startup(startup_args, startup, evidence, "startup-check", env)
            raise
        summary["startup_check"] = "passed"
        profiles = [("all", allowed), ("four", allowed[-4:]), ("two", allowed[-2:]),
                    ("one", allowed[-1:]), ("sparse", allowed[1::2][:4] or allowed[:1])]
        seen = set()
        for label, cpus in profiles:
            if tuple(cpus) in seen:
                continue
            seen.add(tuple(cpus))
            selected = (cases if label in {"all", "two", "one"}
                        else [case for case in cases if case.get("extended")])
            for case in selected:
                result = run_case(binary, case, suite, cpus, label, work, evidence, env)
                summary["tests"].append(result)
                if result.get("infrastructure_failure"):
                    raise RuntimeError("The homebrew did not reach its checks; startup evidence collected")
        summary["ok"] = bool(summary["tests"]) and all(r["ok"] for r in summary["tests"])
        say("AFFINITY_SUITE=" + ("PASS" if summary["ok"] else "FAIL"))
    except KeyboardInterrupt:
        summary["ok"] = False
        summary["error"] = "Interrupted by user; test process stopped"
        say("AFFINITY_TEST_INTERRUPTED=Evidence collected")
    except Exception as error:
        summary["ok"] = False
        summary["error"] = str(error)
        say("AFFINITY_TEST_ERROR=" + str(error))
    finally:
        lock.close()
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
