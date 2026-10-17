#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""SHA-pinned, fail-closed gameplay observation after an upstream GoW build.

Launch exactly one candidate, with no automatic game inputs or game termination.
A user may control the game through their existing Moonlight session.
"""
import argparse
from collections import Counter, deque
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

LOG_MARKERS = {
    "shader_unflattened": "sharp source was not flattened",
    "legacy_unflattened": "Sharp source was not flatenned",
    "buffer_clamps": "Clamped size from",
    "queue_register": "SetQueueReg",
    "shader_dma": "Enabling DMA for shader",
    "invalid_texture": "Rejecting invalid T#",
    "pm4_out_of_range": "out-of-range",
    "vulkan_fault": "VK_ERROR_DEVICE_LOST",
    "audio3d_initialized": "sceAudio3dInitialize: called",
    "guest_audio_initialized": "sceAudioOutInit: Audio system initialized",
    "audio_main_port": "sceAudioOutOpen: opened port_type=Main",
    "audio_sdl_device_open": "Opened audio device:",
    "audio_pcm_detected": "SDL audio: first non-silent PCM",
    "audio_pcm_route": "SDL audio route:",
    "audio_sdl_failure": "SDL audio port has no usable output stream",
    "audio_openal_failure": "OpenAL audio port has no usable source or device",
    "audio_device_intentional_mute": "audio output intentionally disabled",
    "audio_backend_sink_error": "Failed to resume audio stream",
    "audio3d_shutdown": "sceAudio3dTerminate: called",
    "audio_unload_error": "Failed sceSysmoduleUnloadModule for SCE_SYSMODULE_AUDIO_3D",
}
THREAD_NAMES = ("GpuComm", "Uffd", "Game:Main", "VideoOut", "Render", "TaskManager")
SHADER_ID = re.compile(r"(?:stage|shader)\s+(?:0x)?([a-f0-9]{6,16})", re.I)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def active_emulators():
    matches = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            args = (proc / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            if not args or not Path(os.fsdecode(args[0])).name.lower().startswith("shadps4"):
                continue
            state = (proc / "stat").read_text().rsplit(") ", 1)[1][0]
            if state not in ("Z", "X"):
                matches.append({"pid": int(proc.name), "argv0": os.fsdecode(args[0]), "state": state})
        except (OSError, ValueError, IndexError):
            continue
    return matches


def sample_threads(pid):
    results = {}
    try:
        tasks = list(Path(f"/proc/{pid}/task").iterdir())
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return {}
    for proc in tasks:
        if not proc.name.isdecimal():
            continue
        try:
            name = (proc / "comm").read_text().strip()
            if not any(key in name for key in THREAD_NAMES):
                continue
            stat = (proc / "stat").read_text().rsplit(") ", 1)[1].split()
            results[proc.name] = {
                "name": name,
                "cpu_ticks": int(stat[11]) + int(stat[12]),
                "state": stat[0],
                "wchan": (proc / "wchan").read_text().strip(),
            }
        except (OSError, ValueError, IndexError):
            continue
    return results


def summarize_log(path, out):
    first, last = [], deque(maxlen=350)
    shader_ids, category = Counter(), Counter()
    total_lines = 0
    if not path.is_file():
        return {"console_missing": True}
    try:
        with path.open("rb", buffering=1024 * 1024) as stream:
            for raw in stream:
                total_lines += 1
                line = raw.decode("utf-8", "replace").rstrip()[:1800]
                tags = [kind for kind, substring in LOG_MARKERS.items() if substring in line]
                if " <Error>" in line or " <Warning>" in line or tags:
                    for tag in tags:
                        category[tag] += 1
                    if " <Error>" in line:
                        category["errors"] += 1
                    if " <Warning>" in line:
                        category["warnings"] += 1
                    if len(first) < 350:
                        first.append(line)
                    last.append(line)
                if tags:
                    match = SHADER_ID.search(line)
                    if match:
                        shader_ids[match.group(1).lower()] += 1
        (out / "console-diagnostics.txt").write_text(
            "\n".join(first) + "\n\n... SELECTED DIAGNOSTIC TAIL ...\n\n" +
            "\n".join(last) + "\n", encoding="utf-8")
        # Cap upload size. Full growing console remains on the user's server.
        with path.open("rb") as stream:
            if path.stat().st_size > 3 * 1024 * 1024:
                prefix = stream.read(512 * 1024)
                stream.seek(-2 * 1024 * 1024, 2)
                excerpt = prefix + b"\n...[MIDDLE OMITTED]...\n" + stream.read()
            else:
                excerpt = stream.read()
        (out / "console-excerpt.log").write_bytes(excerpt)
    except OSError as error:
        return {"console_read_error": str(error)}
    return {"lines": total_lines, "counts": dict(category),
            "top_shader_hashes": shader_ids.most_common(32),
            "full_console_path": str(path), "full_console_bytes": path.stat().st_size}


def capture_deadlock_stacks(pid, out):
    if not shutil.which("gdb") or not shutil.which("timeout"):
        (out / "gdb-not-present.txt").write_text("GDB or timeout not installed\n")
        return
    try:
        result = subprocess.run(
            ["timeout", "23s", "gdb", "-nx", "-batch", "-q",
             "-ex", "set pagination off", "-ex", "set confirm off",
             "-ex", "info threads", "-ex", "thread apply all bt 18",
             "-ex", "detach", "-p", str(pid)],
            capture_output=True, text=True, errors="replace", timeout=28)
        (out / "deadlock-stacks.txt").write_text(
            f"return_code={result.returncode}\n{result.stdout}\n{result.stderr}\n")
    except (OSError, subprocess.TimeoutExpired) as error:
        (out / "gdb-error.txt").write_text(repr(error) + "\n")


def capture_crash_metadata(pid, out):
    """Read existing systemd core metadata, optionally symbolize it; never dump a core to SSD.

    A crashing guest can be multiple GiB. Deliberately never run
    `coredumpctl dump` or modify coredump limits, sysctl or system services.
    """
    if not isinstance(pid, int) or pid < 1:
        return
    notes = {"pid": pid, "coredumpctl_available": bool(shutil.which("coredumpctl")),
             "gdb_available": bool(shutil.which("gdb"))}
    if shutil.which("coredumpctl"):
        # systemd-coredump may take a moment to index the just-exited process.
        # No blanket 'last crash' lookup; only the exact PID is accepted.
        for attempt in range(3):
            try:
                query = subprocess.run(
                    ["coredumpctl", "--no-pager", "info", str(pid)],
                    capture_output=True, text=True, errors="replace", timeout=12)
                (out / "systemd-coredump-info.txt").write_text(
                    f"exit={query.returncode} attempt={attempt + 1}\n"
                    f"{query.stdout[:160000]}\n{query.stderr[:8000]}\n")
                notes["coredump_info_status"] = query.returncode
                if query.returncode == 0:
                    break
            except (OSError, subprocess.TimeoutExpired) as error:
                notes["coredump_info_error"] = repr(error)
                break
            time.sleep(1)
        if notes.get("coredump_info_status") == 0 and shutil.which("gdb"):
            try:
                # Uses an existing core supplied by systemd; does not save or
                # extract a new core file. Cap debugger runtime.
                trace = subprocess.run(
                    ["coredumpctl",
                     "--debugger-arguments=-batch -ex 'set pagination off'"
                     " -ex 'thread apply all bt 20' -ex 'info registers'",
                     "debug", str(pid)],
                    capture_output=True, text=True, errors="replace", timeout=40)
                (out / "systemd-core-gdb-stacks.txt").write_text(
                    f"exit={trace.returncode}\n{trace.stdout[:300000]}\n"
                    f"{trace.stderr[:25000]}\n")
                notes["gdb_stack_status"] = trace.returncode
            except (OSError, subprocess.TimeoutExpired) as error:
                notes["gdb_stack_error"] = repr(error)
    if shutil.which("journalctl"):
        try:
            log = subprocess.run(
                ["journalctl", "-k", "--no-pager", "-n", "140"],
                capture_output=True, text=True, errors="replace", timeout=10)
            lines = [line for line in log.stdout.splitlines()
                     if any(x in line.lower() for x in ("shadps4", "segfault", "trap", "fault"))]
            (out / "kernel-crash-lines.txt").write_text(
                f"journal_exit={log.returncode}\n" + "\n".join(lines[-75:]) + "\n")
            notes["kernel_log_status"] = log.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            notes["kernel_log_error"] = repr(error)
    (out / "crash-metadata.json").write_text(json.dumps(notes, indent=2) + "\n")


def capture_audio_route(out, label, env):
    """Record host playback sink selection without changing audio settings.

    PipeWire's Pulse compatibility and native PipeWire managers can coexist.
    Snapshotting both avoids guessing why a valid SDL/OpenAL stream is silent
    to Sunshine. No pactl set-*, wpctl set-*, or service modifications.
    """
    commands = {
        "pactl_info": ["pactl", "info"],
        "pactl_sinks": ["pactl", "list", "short", "sinks"],
        "pactl_sink_inputs": ["pactl", "list", "short", "sink-inputs"],
        "pactl_input_details": ["pactl", "list", "sink-inputs"],
        "pactl_default_mute": ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
        "pactl_default_volume": ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
        "wpctl_status": ["wpctl", "status"],
    }
    status = {}
    for name, args in commands.items():
        if not shutil.which(args[0]):
            status[name] = {"available": False}
            continue
        try:
            result = subprocess.run(
                args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                errors="replace", timeout=4, env=env)
            status[name] = {
                "return_code": result.returncode,
                "stdout": result.stdout[:65536],
                "stderr": result.stderr[:1600],
            }
        except (OSError, subprocess.TimeoutExpired) as err:
            status[name] = {"error": repr(err)}
    (out / f"host-audio-route-{label}.json").write_text(
        json.dumps(status, indent=2) + "\n")
    return {name: value.get("return_code") for name, value in status.items()
            if "return_code" in value}


def main():
    arg = argparse.ArgumentParser()
    arg.add_argument("--binary", type=Path, required=True)
    arg.add_argument("--sha256", required=True)
    arg.add_argument("--report", type=Path, required=True)
    arg.add_argument("--cwd", type=Path, required=True)
    arg.add_argument("--seconds", type=int, default=180)
    args = arg.parse_args()
    if args.seconds < 45 or args.seconds > 360:
        arg.error("observation --seconds must be between 45 and 360")
    if not re.fullmatch(r"[a-f0-9]{64}", args.sha256):
        arg.error("--sha256 must be an exact lowercase SHA-256 hex digest")

    out = args.report
    out.mkdir(parents=True, exist_ok=False)
    report = {"game": "CUSA34384", "binary": str(args.binary), "seconds": args.seconds,
              "game_terminated_by_runner": False, "source_changed": False,
              "sunshine_x11_changed": False, "ssh_session_changed": False}
    samples = []
    process = None
    status = 1
    console_file = None
    audio_environment = os.environ.copy()
    try:
        exe = args.binary.resolve(strict=True)
        if exe != args.binary or args.binary.is_symlink() or not exe.is_file():
            raise RuntimeError("CANDIDATE_PATH_MUST_BE_EXACT_REGULAR_FILE")
        measured = file_sha256(exe)
        report["binary_sha256"] = measured
        if measured != args.sha256:
            raise RuntimeError("GAME_CANDIDATE_SHA256_CHANGED_AFTER_LINK")
        existing = active_emulators()
        (out / "existing-emulators.json").write_text(json.dumps(existing, indent=2) + "\n")
        if existing:
            raise RuntimeError("EXISTING_SHADPS4_PROCESS_PRESENT; WILL_NOT_LAUNCH_ANOTHER")

        if not args.cwd.is_dir():
            raise RuntimeError("GAME_LAUNCH_CWD_MISSING")
        env = os.environ.copy()
        if not env.get("DISPLAY"):
            env["DISPLAY"] = ":0"
        if not env.get("XDG_RUNTIME_DIR"):
            env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        if not env.get("DBUS_SESSION_BUS_ADDRESS"):
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{os.getuid()}/bus"
        for key in list(env):
            if key.startswith(("SHADPS4_GOW_", "SHADPS4_CPU_ID_")):
                env.pop(key, None)
        audio_environment = env
        report["host_audio_prelaunch"] = capture_audio_route(out, "prelaunch", env)
        command = [str(exe), "--cpu-id-mode", "builtin", "--userfaultfd",
                   "--game", "CUSA34384", "--fullscreen", "true", "--show-fps"]
        (out / "launch-command.json").write_text(json.dumps(command, indent=2) + "\n")
        console_file = (out / "full-console.log").open("wb", buffering=0)
        process = subprocess.Popen(
            command, cwd=args.cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=console_file, stderr=subprocess.STDOUT, start_new_session=True)
        report["pid"] = process.pid
        print(f"GAME_LAUNCHED=YES PID={process.pid} GAME=CUSA34384", flush=True)
        print("USE_EXISTING_MOONLIGHT_TO_CONTROL_GAME=YES; NO_AUTO_INPUT", flush=True)
        print("NO_GAME_PROCESS_TERMINATION_BY_RUNNER=YES", flush=True)
        started = time.monotonic()
        previous = sample_threads(process.pid)
        tick_hz = os.sysconf("SC_CLK_TCK")
        consecutive = 0
        for elapsed in range(15, args.seconds + 1, 15):
            target = started + elapsed
            while time.monotonic() < target:
                if process.poll() is not None:
                    break
                time.sleep(min(0.5, max(0.0, target - time.monotonic())))
            if process.poll() is not None:
                report["exit_code"] = process.returncode
                raise RuntimeError(f"GAME_EXITED_EARLY_WITH_CODE_{process.returncode}")
            current = sample_threads(process.pid)
            if elapsed in (15, 60, 120, 180):
                report[f"host_audio_t{elapsed}"] = capture_audio_route(
                    out, f"{elapsed}s", audio_environment)
            gpu_fault, uffd_wait = False, False
            rows = []
            for tid, item in current.items():
                before = previous.get(tid)
                delta = None if not before else max(
                    0, round((item["cpu_ticks"] - before["cpu_ticks"]) * 1000 / tick_hz))
                rows.append({"tid": tid, "delta_cpu_ms": delta, **item})
                gpu_fault |= "GpuComm" in item["name"] and "handle_userfault" in item["wchan"] and delta == 0
                uffd_wait |= "Uffd" in item["name"] and "futex" in item["wchan"] and delta == 0
            deadlock_signature = gpu_fault and uffd_wait
            samples.append({"seconds": elapsed, "threads": rows,
                            "gpu_userfault": gpu_fault, "uffd_wait": uffd_wait,
                            "deadlock_signature": deadlock_signature})
            consecutive = consecutive + 1 if deadlock_signature else 0
            print(f"GAME_T={elapsed}s GPU_USERFAULT={int(gpu_fault)} UFFD_WAIT={int(uffd_wait)} "
                  f"DEADLOCK_STREAK={consecutive}", flush=True)
            if consecutive >= 3:
                report["old_uffd_deadlock_signature"] = True
                capture_deadlock_stacks(process.pid, out)
                raise RuntimeError("OLD_GPU_UFFD_DEADLOCK_REPRODUCED")
            previous = current

        if process.poll() is not None:
            raise RuntimeError(f"GAME_EXITED_BEFORE_CAPTURE_COMPLETED_{process.returncode}")
        report["alive_at_capture_end"] = True
        report["old_uffd_deadlock_signature"] = False
        print("GAME_OBSERVATION=COMPLETED_ALIVE; GRAPHICS_VISUAL_CHECK=MANUAL", flush=True)
        status = 0
    except Exception as error:
        report["failure"] = str(error)
        (out / "observer-error.txt").write_text(traceback.format_exc())
        print(f"GAME_OBSERVATION=FAILED; REASON={error}", flush=True)
    finally:
        if console_file is not None:
            console_file.close()
        if process is not None and process.poll() is not None and process.returncode is not None:
            if process.returncode < 0:
                # Guest process exited due to signal; capture crash proof
                # rather than treating an ELF/module warning as the cause.
                try:
                    capture_crash_metadata(process.pid, out)
                except Exception as crash_error:
                    (out / "crash-capture-error.txt").write_text(
                        repr(crash_error) + "\n")
        if process is not None:
            try:
                report["host_audio_at_end"] = capture_audio_route(
                    out, "at-end", audio_environment)
            except Exception as audio_error:
                (out / "audio-route-capture-error.txt").write_text(
                    repr(audio_error) + "\n")
        report["process_alive_when_reported"] = bool(process and process.poll() is None)
        report["result"] = "OBSERVED_ALIVE" if status == 0 else "FAILED"
        report["thread_samples"] = len(samples)
        (out / "thread-samples.json").write_text(json.dumps(samples, indent=2) + "\n")
        report["console"] = summarize_log(out / "full-console.log", out)
        c = report["console"].get("counts", {})
        report["audio_pipeline"] = {
            "initialization_reached": bool(c.get("guest_audio_initialized")),
            "audio3d_init_reached": bool(c.get("audio3d_initialized")),
            "sdl_device_open": bool(c.get("audio_sdl_device_open")),
            "guest_pcm_detected": bool(c.get("audio_pcm_detected")),
            "pcm_route_observations": c.get("audio_pcm_route", 0),
            "backend_failures": c.get("audio_sdl_failure", 0) +
                                c.get("audio_openal_failure", 0),
            "teardown_count": c.get("audio3d_shutdown", 0),
        }
        (out / "gameplay-result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"GAME_REPORT={out}", flush=True)
        print(f"GAME_PROCESS_LEFT_RUNNING={report['process_alive_when_reported']}", flush=True)
        print("SSH_SESSION=UNCHANGED; SUNSHINE_X11=UNCHANGED", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
