#!/usr/bin/env python3
"""GoW Sony-logo UFFD stall control. No builds, process termination, or config writes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime

ROOT = Path.home() / "projects/shadps4"
BASE = ROOT / "GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
PROVEN = BASE / "build/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543"
CANDIDATE = ROOT / "GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/shadps4.gow-log-hotpath-20261017"
EXPECTED_SHA = "e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa"
LOGS = ROOT / "logs"
TAG = "gow-uffd-stall-control-" + datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = LOGS / TAG


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def screenshot(name):
    if not shutil.which("import"):
        return
    display = os.environ.get("DISPLAY") or ":0"
    try:
        subprocess.run(
            ["import", "-display", display, "-window", "root", str(OUT / ("frame-" + name + ".png"))],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=7)
    except (OSError, subprocess.TimeoutExpired):
        pass


def collect_existing(observer, pid):
    print("EXISTING_GAME_PID=" + str(pid) + " ; ATTACH_NO_RELAUNCH=YES", flush=True)
    samples = []
    streak = 0
    traced = False
    start = time.monotonic()
    for i in range(31):
        rows = observer.sample_threads(pid)
        if not rows:
            print("EXISTING_GAME_EXITED_DURING_OBSERVATION", flush=True)
            break
        gpu = any("GpuComm" in x["name"] and "handle_userfault" in x["wchan"] for x in rows.values())
        uffd = any("Uffd" in x["name"] and "futex" in x["wchan"] for x in rows.values())
        streak = streak + 1 if gpu and uffd else 0
        samples.append({"elapsed": round(time.monotonic() - start, 2),
                        "gpu_userfault": gpu, "uffd_futex_wait": uffd,
                        "threads": rows})
        if i in (10, 20, 30):
            screenshot("existing-" + str(i * 2) + "s")
        if streak >= 5 and not traced:
            traced = True
            print("PERSISTENT_GPU_UFFD_WAIT=YES; GDB_BACKTRACE_ATTEMPT=START", flush=True)
            observer.capture_deadlock_stacks(pid, OUT)
        if i % 5 == 0:
            print("T={}s GPU_WAIT={} UFFD_WAIT={} STREAK={}".format(
                i * 2, int(gpu), int(uffd), streak), flush=True)
        if i < 30:
            time.sleep(2)
    (OUT / "attached-threads.json").write_text(json.dumps(samples, indent=2) + "\n")
    print("ATTACHED_SAMPLE_COUNT=" + str(len(samples)), flush=True)


def run_proven_control(observer_path):
    env = os.environ.copy()
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/" + str(os.getuid()))
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=" + env["XDG_RUNTIME_DIR"] + "/bus")
    print("AUTO_LAUNCH_PROVEN_BINARY=YES; GAME=CUSA34384; USER_INPUT=MOONLIGHT", flush=True)
    report_dir = OUT / "proven-observation"
    cmd = [sys.executable, "-B", str(observer_path), "--binary", str(PROVEN),
           "--sha256", EXPECTED_SHA, "--report", str(report_dir), "--cwd", str(BASE),
           "--seconds", "135"]
    (OUT / "observer-command.json").write_text(json.dumps(cmd, indent=2) + "\n")
    with (OUT / "observer-console.txt").open("w") as logfile:
        process = subprocess.Popen(
            cmd, env=env, stdin=subprocess.DEVNULL,
            stdout=logfile, stderr=subprocess.STDOUT)
        began = time.monotonic()
        frames = set()
        while process.poll() is None and time.monotonic() - began < 155:
            elapsed = int(time.monotonic() - began)
            for seconds in (20, 40, 60, 90, 120):
                if elapsed >= seconds and seconds not in frames:
                    frames.add(seconds)
                    screenshot(str(seconds) + "s")
                    print("SCREENSHOT_AT={}s".format(seconds), flush=True)
            time.sleep(1)
        if process.poll() is None:
            print("OBSERVER_TIMEOUT; WILL_NOT_KILL_GAME_OR_SSH", flush=True)
            return 2
        code = process.returncode
    print((OUT / "observer-console.txt").read_text(errors="replace")[-18000:], flush=True)
    if (report_dir / "gameplay-result.json").is_file():
        report = json.loads((report_dir / "gameplay-result.json").read_text())
        print("PROVEN_GAME_OBSERVATION=" + str(report.get("result")))
        print("PROVEN_DEADLOCK_SIGNATURE=" + str(report.get("old_uffd_deadlock_signature")))
        print("PROVEN_GAME_ALIVE=" + str(report.get("process_alive_when_reported")))
        (OUT / "control-summary.json").write_text(json.dumps(report, indent=2) + "\n")
    images = sorted(OUT.glob("frame-*s.png"))
    hashes = [sha256_file(img) for img in images]
    (OUT / "frame-hashes.json").write_text(
        json.dumps({img.name: hashval for img, hashval in zip(images, hashes)}, indent=2) + "\n")
    print("PROVEN_FRAME_IMAGES={} ALL_SAME={}".format(
        len(images), len(set(hashes)) == 1 if hashes else "UNKNOWN"), flush=True)
    return code


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=False)
    result = 2
    try:
        if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
            raise RuntimeError("PINNED_GAMEPLAY_OBSERVER_MISSING")
        observer_path = Path(sys.argv[1]).resolve()
        if not PROVEN.is_file() or PROVEN.is_symlink():
            raise RuntimeError("PROVEN_BINARY_INVALID")
        measured = sha256_file(PROVEN)
        if measured != EXPECTED_SHA:
            raise RuntimeError("PROVEN_BINARY_SHA_MISMATCH")
        print("PROVEN_BINARY_SHA256=PASS", flush=True)
        spec = importlib.util.spec_from_file_location("gow_control_observer", observer_path)
        observer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(observer)
        existing = observer.active_emulators()
        (OUT / "existing-emulators.json").write_text(json.dumps(existing, indent=2) + "\n")
        if len(existing) > 1:
            raise RuntimeError("MULTIPLE_EMULATORS_RUNNING_WILL_NOT_INTERRUPT")
        if len(existing) == 1:
            pid = existing[0]["pid"]
            exe = Path(os.readlink("/proc/{}/exe".format(pid))).resolve()
            if exe not in (PROVEN, CANDIDATE):
                raise RuntimeError("OTHER_GAME_ALREADY_RUNNING_WILL_NOT_INTERRUPT")
            (OUT / "attached-binary.txt").write_text(str(exe) + "\n")
            collect_existing(observer, pid)
            result = 0
        else:
            result = run_proven_control(observer_path)
    except Exception as exc:
        print("CONTROL_ERROR=" + repr(exc), flush=True)
        (OUT / "error.txt").write_text(repr(exc) + "\n")
    finally:
        (OUT / "control-result.txt").write_text("RESULT={}\n".format(result))
        if PROVEN.is_file():
            (OUT / "proven-after.txt").write_text(
                "SHA256={}\nEXPECTED={}\n".format(sha256_file(PROVEN), EXPECTED_SHA))
        archive = LOGS / (TAG + ".tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            for item in OUT.rglob("*"):
                if item.is_file() and item.name != "full-console.log":
                    tar.add(item, arcname=str(item.relative_to(LOGS)))
        print("ARCHIVE=" + str(archive), flush=True)
        print("BUILD_SOURCE_UNCHANGED=YES SSH=UNCHANGED SUNSHINE_X11=UNCHANGED", flush=True)
    return result


if __name__ == "__main__":
    sys.exit(main())
