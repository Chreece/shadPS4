#!/usr/bin/env python3
"""Bounded automatic GoW gameplay probe: screenshot -> Cross -> screenshot.

The only injected input is the configured Cross/X button. Every input attempt is
preceded by a screenshot and followed by another screenshot. The target process
is checksum-pinned and validated across the CPU-ID translation runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import types

ROOT = Path.home() / "projects/shadps4/GodOfWarRagnarok"
LOGS = Path.home() / "projects/shadps4/logs"
PROFILE = Path.home() / ".local/share/shadPS4"
CFG = PROFILE / "custom_configs/CUSA34384.json"
CWD = ROOT / "runtime/gow91"
SERIAL = "CUSA34384"

AUTO_URL = ("https://raw.githubusercontent.com/Chreece/shadPS4/"
            "ebb4aa92725f99f5541610142942963e410832fa/tools/gow93-auto-confirm.py")
AUTO_SHA256 = "8ec81d11f916d1cba00ac1cf66a3ad15b0e83c9d7825f66d8ed69c53f118d071"


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def load_auto():
    scripts = ROOT / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    p = scripts / "gow-auto-input-base.py"
    if not p.is_file() or p.is_symlink() or sha256(p) != AUTO_SHA256:
        fd, tmp = tempfile.mkstemp(prefix=".gow-auto-", dir=scripts)
        os.close(fd)
        try:
            subprocess.run([
                "curl", "-4fsSL", "--retry", "2", "--connect-timeout", "20",
                "--max-time", "120", AUTO_URL, "-o", tmp
            ], check=True, timeout=380)
            q = Path(tmp)
            if sha256(q) != AUTO_SHA256:
                raise RuntimeError("Pinned input helper checksum mismatch")
            if p.is_symlink():
                raise RuntimeError("Refusing symlinked input helper")
            os.replace(tmp, p)
        finally:
            Path(tmp).unlink(missing_ok=True)
    m = types.ModuleType("gow_auto_input")
    m.__file__ = str(p)
    exec(compile(p.read_bytes(), str(p), "exec"), m.__dict__)
    return m


def safe_json(path: Path):
    if not path.exists():
        return {}
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 5_000_000:
        raise RuntimeError("Unsafe config file: " + str(path))
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError("Config root is not an object")
    return value


def patch_config(path: Path, readbacks_mode: int):
    obj = safe_json(path)
    for section in ("GPU", "Vulkan"):
        if section in obj and not isinstance(obj[section], dict):
            raise RuntimeError("Invalid config section: " + section)
    gpu = obj.setdefault("GPU", {})
    gpu["direct_memory_access_enabled"] = True
    gpu["dump_shaders"] = False
    gpu["readbacks_mode"] = readbacks_mode
    vk = obj.setdefault("Vulkan", {})
    vk["pipeline_cache_enabled"] = False
    vk["pipeline_cache_archived"] = False

    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()
    fd, tmp = tempfile.mkstemp(prefix=".gow-auto-config-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def identity(path: Path):
    st = path.stat()
    return (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)


def parse_stat(text: str):
    closing = text.rfind(")")
    if closing < 0:
        raise RuntimeError("Malformed process status")
    fields = text[closing + 1:].split()
    if len(fields) < 20:
        raise RuntimeError("Incomplete process status")
    return {
        "state": fields[0],
        "parent_pid": int(fields[1]),
        "process_group": int(fields[2]),
        "session_id": int(fields[3]),
        "start_ticks": int(fields[19]),
    }


def runtime_candidates(binary: Path):
    # This is the exact CPU-ID runtime layout used by launcher.cpp and by the
    # already-proven GOW93 process guard. libdynamorio.so can be /proc/<pid>/exe
    # even though it is not a top-level executable in cpu-id-runtime.
    root = binary.parent / "cpu-id-runtime"
    if not root.is_dir():
        root = (binary.parent / "../libexec/shadps4/cpu-id-runtime").resolve()
    candidates = [root / "bin64/drrun", root / "lib64/release/libdynamorio.so"]
    out = []
    for p in candidates:
        try:
            if p.is_file():
                out.append({"path": str(p.resolve(strict=True)), "identity": identity(p)})
        except OSError:
            pass
    if not out:
        raise RuntimeError("CPU-ID runtime executables are unavailable")
    return out


def capture_launch(proc: subprocess.Popen, binary: Path):
    folder = Path("/proc") / str(proc.pid)
    stat = parse_stat((folder / "stat").read_text())
    if stat["parent_pid"] != os.getpid():
        raise RuntimeError("Launched child parent changed")
    if stat["process_group"] != proc.pid or stat["session_id"] != proc.pid:
        raise RuntimeError("Launched child did not enter its dedicated session")
    return {
        "pid": proc.pid,
        "owner_uid": os.getuid(),
        "parent_pid": stat["parent_pid"],
        "process_group": stat["process_group"],
        "session_id": stat["session_id"],
        "start_ticks": stat["start_ticks"],
        "binary_identity": identity(binary),
        "runtime_executables": runtime_candidates(binary),
    }


def validate_process(pid: int, binary: Path, launch):
    if pid != launch["pid"]:
        raise RuntimeError("Input target PID differs from launched child")
    proc = Path("/proc") / str(pid)
    before = parse_stat((proc / "stat").read_text())
    current = {
        "pid": pid,
        "owner_uid": proc.stat().st_uid,
        "parent_pid": before["parent_pid"],
        "process_group": before["process_group"],
        "session_id": before["session_id"],
        "start_ticks": before["start_ticks"],
        "state": before["state"],
        "exe_identity": identity(proc / "exe"),
        "binary_identity": identity(binary),
    }
    for key in ("owner_uid", "parent_pid", "process_group", "session_id", "start_ticks"):
        if current[key] != launch[key]:
            raise RuntimeError("Input process identity changed: " + key)
    if current["state"] in ("Z", "X", "x"):
        raise RuntimeError("Input target has exited")
    if current["binary_identity"] != launch["binary_identity"]:
        raise RuntimeError("Pinned candidate changed after launch")

    if current["exe_identity"] == launch["binary_identity"]:
        mode = "direct"
    else:
        runtime = next((x for x in launch["runtime_executables"]
                        if current["exe_identity"] == x["identity"]), None)
        if runtime is None:
            raise RuntimeError("Live process is neither the pinned binary nor its CPU-ID runtime")

        wanted = launch["binary_identity"]
        mapped = False
        for row in (proc / "maps").read_text(errors="replace").splitlines():
            fields = row.split(None, 5)
            if len(fields) < 5:
                continue
            try:
                major, minor = fields[3].split(":")
                dev_id = (int(major, 16), int(minor, 16), int(fields[4]))
                offset = int(fields[2], 16)
            except (ValueError, IndexError):
                continue
            if dev_id == wanted and offset == 0 and fields[1].startswith("r"):
                mapped = True
                break
        if not mapped:
            raise RuntimeError("Pinned candidate is not mapped in the translated process")
        mode = "translated:" + Path(runtime["path"]).name

    after = parse_stat((proc / "stat").read_text())
    if after["start_ticks"] != launch["start_ticks"]:
        raise RuntimeError("Process changed during identity inspection")
    return mode


def clean_gow_behavior_env(env):
    removed = []
    for key in list(env):
        if key.startswith("SHADPS4_GOW"):
            removed.append(key)
            env.pop(key, None)
    return sorted(removed)


def xrun(args, env):
    cp = subprocess.run(["xdotool", *args], env=env, text=True,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        timeout=10)
    if cp.returncode:
        raise RuntimeError("xdotool failed: " + cp.stderr.strip())
    return cp.stdout.strip()


def game_window(pid: int, env):
    raw = xrun(["search", "--onlyvisible", "--pid", str(pid)], env)
    valid = []
    for wid in raw.splitlines():
        if not wid.isdecimal():
            continue
        if xrun(["getwindowpid", wid], env) != str(pid):
            continue
        title = xrun(["getwindowname", wid], env)
        if SERIAL in title or "God of War Ragnar" in title:
            valid.append(wid)
    if len(valid) != 1:
        raise RuntimeError("Game window missing or ambiguous")
    return valid[0]


def geometry(wid: str, env):
    raw = xrun(["getwindowgeometry", "--shell", wid], env)
    values = {}
    for row in raw.splitlines():
        if "=" not in row:
            continue
        k, v = row.split("=", 1)
        if k in {"X", "Y", "WIDTH", "HEIGHT"}:
            values[k] = int(v)
    if set(values) != {"X", "Y", "WIDTH", "HEIGHT"}:
        raise RuntimeError("Could not read game window geometry")
    if values["WIDTH"] < 128 or values["HEIGHT"] < 128:
        raise RuntimeError("Game window geometry is too small")
    return values


def screenshot(wid: str, env, path: Path):
    g = geometry(wid, env)
    display = env.get("DISPLAY", ":0")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "x11grab",
        "-video_size", f'{g["WIDTH"]}x{g["HEIGHT"]}',
        "-i", f'{display}+{g["X"]},{g["Y"]}',
        "-frames:v", "1", "-q:v", "3", str(path)
    ], stdin=subprocess.DEVNULL, timeout=20, check=True)
    if not path.is_file() or path.stat().st_size < 1024:
        raise RuntimeError("Screenshot capture failed")


def terminate(proc: subprocess.Popen):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 7
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(.2)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def package(out: Path, pid: int):
    radv = out / "radv"
    for d in Path.home().glob(f"radv_dumps_{pid}_*"):
        if not d.is_dir():
            continue
        radv.mkdir(exist_ok=True)
        shutil.copytree(d, radv / d.name, dirs_exist_ok=True)
    archive = out.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(out, arcname=out.name)
    return archive


def self_test():
    # Policy: every cycle is exactly capture -> Cross -> capture.
    seconds, interval = 60, 10
    cycles = math.ceil(seconds / interval)
    events = []
    for i in range(cycles):
        events.extend([f"before-{i}", f"cross-{i}", f"after-{i}"])
    assert len(events) == cycles * 3
    assert all(events[i].startswith("before-") and
               events[i + 1].startswith("cross-") and
               events[i + 2].startswith("after-")
               for i in range(0, len(events), 3))

    # Regression for the exact bug that broke the prior run: CPU-ID executables
    # are nested at bin64/drrun and lib64/release/libdynamorio.so.
    with tempfile.TemporaryDirectory(prefix="gow-auto-selftest-") as td:
        root = Path(td)
        binary = root / "shadps4"
        binary.write_bytes(b"x")
        runtime = root / "cpu-id-runtime"
        (runtime / "bin64").mkdir(parents=True)
        (runtime / "lib64/release").mkdir(parents=True)
        (runtime / "bin64/drrun").write_bytes(b"d")
        (runtime / "lib64/release/libdynamorio.so").write_bytes(b"l")
        got = runtime_candidates(binary)
        names = {Path(x["path"]).name for x in got}
        assert names == {"drrun", "libdynamorio.so"}

        cfg = root / "CUSA34384.json"
        patch_config(cfg, 0)
        assert json.loads(cfg.read_text())["GPU"]["readbacks_mode"] == 0
        fake_env = {"SHADPS4_GOW_SRT_AUTO_ROOTS": "2", "SHADPS4_GOW_FAKE": "1", "KEEP": "yes"}
        removed = clean_gow_behavior_env(fake_env)
        assert removed == ["SHADPS4_GOW_FAKE", "SHADPS4_GOW_SRT_AUTO_ROOTS"]
        assert fake_env == {"KEEP": "yes"}

    stat = parse_stat((Path("/proc") / str(os.getpid()) / "stat").read_text())
    assert stat["start_ticks"] > 0 and stat["process_group"] > 0
    print("GOW_AUTO_GAMEPLAY_SELF_TEST=PASS process guard + nested CPU-ID runtime + screenshot/Cross policy")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", type=Path)
    ap.add_argument("--sha256")
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--after-delay", type=float, default=1.0)
    ap.add_argument("--readbacks-mode", type=int, choices=(0, 1, 2), default=0)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return 0
    if args.binary is None or args.sha256 is None:
        ap.error("--binary and --sha256 are required")
    if not 30 <= args.seconds <= 900:
        ap.error("--seconds must be 30..900")
    if not 2 <= args.interval <= 60:
        ap.error("--interval must be 2..60")
    if not .25 <= args.after_delay <= 5:
        ap.error("--after-delay must be 0.25..5")

    binary = args.binary.expanduser().resolve(strict=True)
    if not binary.is_file() or binary.is_symlink() or not os.access(binary, os.X_OK):
        raise RuntimeError("Unsafe/missing candidate binary")
    if sha256(binary) != args.sha256.lower():
        raise RuntimeError("Candidate binary checksum mismatch")
    if not CWD.is_dir() or CWD.is_symlink():
        raise RuntimeError("Unsafe/missing runtime directory")
    if subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "shadps4"],
                      stdout=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError("shadps4 is already running")

    auto = load_auto()
    key, mapping = auto.read_mapping(PROFILE)

    LOGS.mkdir(parents=True, exist_ok=True)
    out = LOGS / f'gow-auto-gameplay-{time.strftime("%Y%m%d-%H%M%S")}-{os.getpid()}'
    shots = out / "screenshots"
    shots.mkdir(parents=True)
    txn = PROFILE / f".gow-auto-gameplay-{os.getpid()}"
    txn.mkdir(parents=True)
    had_cfg = CFG.exists()
    if had_cfg:
        if not CFG.is_file() or CFG.is_symlink():
            raise RuntimeError("Unsafe per-game config")
        shutil.copy2(CFG, txn / "config.before")

    env = os.environ.copy()
    removed_gow_env = clean_gow_behavior_env(env)
    env["RADV_DEBUG"] = "hang"
    env.setdefault("DISPLAY", ":0")
    xa = Path.home() / ".Xauthority"
    if xa.is_file():
        env.setdefault("XAUTHORITY", str(xa))

    proc = None
    launch = None
    events = []
    stopped = False
    failure = None
    rc = None
    console = (out / "console.log").open("wb")
    try:
        patch_config(CFG, args.readbacks_mode)
        (out / "runtime-contract.json").write_text(json.dumps({
            "binary": str(binary),
            "binary_sha256": args.sha256.lower(),
            "seconds": args.seconds,
            "interval": args.interval,
            "sequence": "screenshot -> Cross/X -> screenshot",
            "cross_key": key,
            "mapping": mapping,
            "automatic_controller_input": True,
            "other_controller_inputs": False,
            "readbacks_mode": args.readbacks_mode,
            "removed_SHADPS4_GOW_env": removed_gow_env,
            "active_SHADPS4_GOW_env": [],
            "dump_shaders": False,
            "srt_auto_roots": 2,
        }, indent=2) + "\n")

        proc = subprocess.Popen([
            str(binary), "--cpu-id-mode", "auto", "--game", SERIAL, "--fullscreen", "true"
        ], cwd=CWD, env=env, stdout=console, stderr=subprocess.STDOUT,
           start_new_session=True)
        launch = capture_launch(proc, binary)

        print(f"GOW_AUTO_GAMEPLAY_PID={proc.pid}", flush=True)
        print("GOW_AUTO_GAMEPLAY=SCREENSHOT_CROSS_SCREENSHOT", flush=True)

        start = time.monotonic()
        cycle = 0
        next_cycle = start + args.interval
        while proc.poll() is None and time.monotonic() - start < args.seconds:
            sleep_for = next_cycle - time.monotonic()
            if sleep_for > 0:
                time.sleep(min(sleep_for, .5))
                continue
            next_cycle += args.interval
            cycle += 1

            mode = validate_process(proc.pid, binary, launch)
            wid = game_window(proc.pid, env)
            auto.focus_game(wid, proc.pid, env)

            before = shots / f"{cycle:04d}-before.jpg"
            screenshot(wid, env, before)

            sent = False
            error = None
            try:
                auto.press_cross(wid, proc.pid, key, env)
                sent = True
                print(f"GOW_AUTO_GAMEPLAY cycle={cycle} Cross=X", flush=True)
            except Exception as exc:
                error = str(exc)
                print(f"GOW_AUTO_GAMEPLAY cycle={cycle} Cross=SKIPPED {error}", flush=True)

            time.sleep(args.after_delay)
            after = shots / f"{cycle:04d}-after.jpg"
            if proc.poll() is None:
                try:
                    validate_process(proc.pid, binary, launch)
                    wid2 = game_window(proc.pid, env)
                    screenshot(wid2, env, after)
                except Exception as exc:
                    if error is None:
                        error = "after screenshot: " + str(exc)

            events.append({
                "cycle": cycle,
                "elapsed": round(time.monotonic() - start, 3),
                "process_mode": mode,
                "before": before.name,
                "cross_sent": sent,
                "after": after.name if after.exists() else None,
                "error": error,
            })
            (out / "events.json").write_text(json.dumps(events, indent=2) + "\n")

        if proc.poll() is None:
            terminate(proc)
        rc = proc.wait(timeout=10)
    except KeyboardInterrupt:
        stopped = True
        if proc is not None:
            terminate(proc)
            rc = proc.wait(timeout=10)
    except BaseException as exc:
        failure = repr(exc)
        print("GOW_AUTO_GAMEPLAY_FAILURE=" + failure, flush=True)
        if proc is not None:
            terminate(proc)
            try:
                rc = proc.wait(timeout=10)
            except Exception:
                rc = None
    finally:
        console.close()
        if had_cfg and (txn / "config.before").is_file():
            shutil.copy2(txn / "config.before", CFG)
        elif not had_cfg:
            CFG.unlink(missing_ok=True)
        shutil.rmtree(txn, ignore_errors=True)

    (out / "result.json").write_text(json.dumps({
        "returncode": rc,
        "ctrl_c": stopped,
        "failure": failure,
        "cycles": len(events),
        "cross_presses": sum(bool(x["cross_sent"]) for x in events),
        "launch_identity": launch,
    }, indent=2) + "\n")
    archive = package(out, proc.pid if proc is not None else -1)
    print("UPLOAD=" + str(archive), flush=True)
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
