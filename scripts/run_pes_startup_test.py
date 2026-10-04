#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Launch the installed PES build in a separate session and collect startup evidence."""

from contextlib import nullcontext
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

import collect_pes_runtime_context as context
import trace_video_progress as trace

GUI_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS", "PULSE_SERVER", "PIPEWIRE_REMOTE",
            "SDL_AUDIODRIVER", "SDL_VIDEODRIVER")


def checksum(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def running_emulators():
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid == os.getuid() and (proc / "exe").readlink().name.lower() == "shadps4":
                found.append(int(proc.name))
        except OSError:
            pass
    return found


def require_idle():
    if running_emulators():
        raise RuntimeError("Close the current game normally, leave ES-DE open, then rerun this script")


def choose_desktop(candidates, inherited):
    """Use a unique GUI session; never silently choose an SSH-forwarded display."""
    if candidates:
        priority = min(item[0] for item in candidates)
        selected = [item for item in candidates if item[0] == priority]
        sessions = {(item[2].get("DISPLAY"), item[2].get("WAYLAND_DISPLAY"),
                     item[2].get("XAUTHORITY"), item[2].get("XDG_RUNTIME_DIR"))
                    for item in selected}
        if len(sessions) != 1:
            raise RuntimeError("Multiple desktop sessions found; leave only the intended ES-DE session open")
        _, source, values = selected[0]
    else:
        source, values = "current shell", {k: inherited[k] for k in GUI_KEYS if k in inherited}
    display = values.get("DISPLAY", "")
    if display and not re.fullmatch(r"(?:unix)?:\d+(?:\.\d+)?", display):
        raise RuntimeError("Only a local desktop is supported; connect Moonlight and leave ES-DE open")
    if not display and not values.get("WAYLAND_DISPLAY"):
        raise RuntimeError("No desktop session found; connect Moonlight and leave ES-DE open")
    env = dict(inherited)
    for key in GUI_KEYS:
        env.pop(key, None)
    env.update(values)
    return env, source


def desktop_environment():
    candidates = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            name = (proc / "exe").readlink().name.lower()
            if name not in ("es-de", "emulationstation", "sunshine"):
                continue
            raw = (proc / "environ").read_bytes().split(b"\0")
            values = dict(item.decode(errors="replace").split("=", 1) for item in raw if b"=" in item)
            values = {key: values[key] for key in GUI_KEYS if values.get(key)}
            if values.get("DISPLAY") or values.get("WAYLAND_DISPLAY"):
                candidates.append((1 if name == "sunshine" else 0, name + ":" + proc.name, values))
        except OSError:
            pass
    env, source = choose_desktop(candidates, os.environ)
    if env.get("DISPLAY"):
        number = re.search(r":(\d+)", env["DISPLAY"])[1]
        if not Path("/tmp/.X11-unix/X" + number).exists():
            raise RuntimeError("The selected X display is unavailable; connect Moonlight and leave ES-DE open")
    elif not (Path(env.get("XDG_RUNTIME_DIR", "/nonexistent")) / env["WAYLAND_DISPLAY"]).exists():
        raise RuntimeError("The selected Wayland socket is unavailable")
    return env, source


def selected_launch(home):
    binary = home / "Applications/shadps4/shadps4"
    wrapper = home / ".local/bin/shadps4-esde"
    if checksum(binary) != trace.BINARY_SHA256:
        raise RuntimeError("Installed emulator differs from the pinned build; preserved")
    content = wrapper.read_text()
    expected = 'exec ' + str(binary) + ' --game "$game" --fullscreen true'
    if ("# SHADPS4_SESSION_GUARD_V1\n" not in content or
            "# SHADPS4_DEFAULT_MAIN_V1\n" not in content or
            expected not in content.splitlines() or
            not (home / ".local/lib/shadps4-session-guard/guard.py").is_file()):
        raise RuntimeError("The existing guarded launcher is not recognized; preserved")
    return wrapper, checksum(wrapper)


def settings(home):
    result = {}
    root = home / ".local/share/shadPS4"
    for path in (root / "config.json", root / "custom_configs/CUSA18676.json"):
        if path.is_file():
            data = json.loads(path.read_text())
            result[str(path)] = {k: data.get(k) for k in ("GPU", "Vulkan", "Audio")}
    return result


def launch_game(wrapper, env, home, log):
    return subprocess.Popen([str(wrapper), "CUSA18676"], cwd=home, env=env,
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)


def descendant_of(pid, ancestor):
    seen = set()
    while pid > 1 and pid not in seen:
        if pid == ancestor:
            return True
        seen.add(pid)
        try:
            pid = int((Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return False
    return False


def await_game(launcher, *, seconds=45):
    deadline = time.monotonic() + seconds
    identity = None
    while time.monotonic() < deadline:
        processes = running_emulators()
        if processes:
            if len(processes) != 1 or not descendant_of(processes[0], launcher.pid):
                raise RuntimeError("Emulator does not uniquely belong to this launch; no debugger attached")
            if identity is None:
                identity = trace.find_process()
            proc = Path("/proc") / str(identity["pid"])
            if (proc / "stat").read_text().rsplit(")", 1)[1].split()[19] != identity["start_ticks"]:
                raise RuntimeError("Launched emulator identity changed")
            try:
                if any((thread / "comm").read_text().strip() == "Game:Main"
                       for thread in (proc / "task").iterdir()):
                    return identity
            except FileNotFoundError:
                pass
        if launcher.poll() is not None and not processes:
            raise RuntimeError("Guarded launcher exited before PES started: " + str(launcher.returncode))
        time.sleep(0.1)
    raise RuntimeError("PES did not create Game:Main within 45 seconds; inspect emulator.log")


def debugger_prefix():
    if shutil.which("gdb") is None:
        raise RuntimeError("gdb is not installed")
    scope_file = Path("/proc/sys/kernel/yama/ptrace_scope")
    scope = int(scope_file.read_text()) if scope_file.exists() else 0
    if scope >= 3:
        raise RuntimeError("Debugger attachment is disabled; no security settings changed")
    if scope:
        print("Authenticating debugger access before launching PES.", flush=True)
        subprocess.run(["sudo", "-v"], check=True)
        return ["sudo", "-n"]
    return []


def collect_screenshots(home, work, previous):
    root = home / ".local/share/shadPS4/screenshots"
    captured = []
    for path in sorted(root.glob("CUSA18676_*.png")):
        if path.name in previous:
            continue
        if not re.fullmatch(r"CUSA18676_\d{8}_\d{6}_\d{3}_(game|hud)_\d+\.png", path.name):
            continue
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or not 20 <= info.st_size <= 32 * 1024 * 1024:
                raise RuntimeError("Invalid native screenshot: " + path.name)
            data = source.read(32 * 1024 * 1024 + 1)
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or not data.endswith(b"\0\0\0\0IEND\xaeB`\x82"):
            raise RuntimeError("Incomplete native screenshot: " + path.name)
        if len(captured) >= 4:
            raise RuntimeError("Unexpected extra screenshots; bounded collection stopped")
        target = work / "screenshots" / path.name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
        captured.append({"file": str(target.relative_to(work)), "bytes": len(data)})
    return captured


def run(home, *, profile="video", reuse_existing=False, screenshots=False, graphics=False,
        validation=False):
    if validation and not graphics:
        raise RuntimeError("Vulkan validation requires the native graphics launcher")
    trace.capture_profile(profile)
    existing = trace.find_process() if reuse_existing and running_emulators() else None
    if existing is None:
        require_idle()
    if graphics and existing is not None:
        raise RuntimeError("Native packet capture requires a fresh game launch")
    wrapper, wrapper_sha = selected_launch(home)
    env, desktop_source = (None, "existing PES") if existing else desktop_environment()
    if screenshots:
        if existing is not None or profile != "frames":
            raise RuntimeError("Native image test requires a fresh launch and the frames profile")
    prefix = debugger_prefix()
    before = settings(home)
    if existing is None:
        require_idle()
    elif trace.find_process() != existing:
        raise RuntimeError("PES identity changed during preparation; preserved")
    if checksum(wrapper) != wrapper_sha:
        raise RuntimeError("Launcher changed during preparation; preserved")
    work = Path(tempfile.mkdtemp(prefix="shadps4-pes-startup-", dir=home))
    record = {"revision": trace.REVISION, "desktop_source": desktop_source,
              "baseline": getattr(trace, "BASELINE_METADATA", None),
              "capture_profile": profile, "reused_existing": existing is not None,
              "settings_before": before, "errors": [], "visual_result": "unverified"}
    print("PES_STARTUP_DIRECTORY=" + str(work), flush=True)
    print("Observing the existing PES session." if existing else
          "Launching PES in the background; automatic collection takes about one minute.", flush=True)
    identity = existing
    if graphics:
        import pes_graphics_launch as native
    screenshots_before = {p.name for p in (home / ".local/share/shadPS4/screenshots").glob("CUSA18676_*.png")}
    try:
        if validation:
            import pes_vulkan_validation as vulkan
            vulkan.preflight(work)
            record["vulkan_preflight_passed"] = True
        started = time.monotonic()
        if existing is None:
            launch_context = (native.enabled_launch(home, wrapper, wrapper_sha, work,
                                                    **({'validation': True} if validation else {}))
                              if graphics else nullcontext())
            with launch_context:
                with (work / "emulator.log").open("wb") as log:
                    launcher = launch_game(wrapper, env, home, log)
                record["launcher_pid"] = launcher.pid
                identity = await_game(launcher)
            if graphics:
                native.verify_environment(identity, **({'validation': True} if validation else {}))
                record["graphics_environment_verified"] = True
                if validation:
                    record["vulkan_layer_loaded"] = True
                print("PES_NATIVE_GRAPHICS=enabled; original launcher restored", flush=True)
        else:
            identity = existing
        record["identity"] = identity
        capture = work / profile
        capture.mkdir()
        context.save_json(capture / "identity.json", identity)
        trace.write_probe(identity, capture / "probe.py", profile=profile, screenshots=screenshots)
        print("PES_PID=" + str(identity["pid"]) + "; starting 20-second " + profile + " observation", flush=True)
        with (capture / "gdb.txt").open("w") as output:
            debugger = subprocess.Popen(trace.debugger_command(identity, capture / "probe.py", prefix),
                                        stdout=output, stderr=subprocess.STDOUT,
                                        start_new_session=True)
            outcome = trace.wait_for_debugger(debugger, capture, prefix)
        report = trace.build_report((capture / "gdb.txt").read_text(errors="replace"),
                                    outcome, trace.inspect_target(identity), profile=profile)
        context.save_json(capture / (profile + "-trace.json"), report)
        record[profile + "_capture_passed"] = trace.report_passed(report)
        print(profile.upper() + "_CAPTURE_STATUS=" + report["status"], flush=True)
        if profile == "frames":
            for api, stats in sorted(report["apis"].items()):
                print(f"{api}: entries={stats['calls']} returns={stats.get('returns')} "
                      f"errors={stats.get('errors')} capped={stats.get('capped')}", flush=True)
            selected = {}
            for item in report['records']:
                api = item.get('api', '')
                if api.startswith('scePlayGo') or api in ('sceKernelStat', 'Rasterizer::FilterDraw',
                                                         'Rasterizer::ResetBindings'):
                    fields = {k: v for k, v in item.items() if k not in
                              ('seq', 'thread', 'seconds', 'elapsed_ms', 'caller')}
                    key = json.dumps(fields, sort_keys=True)
                    selected[key] = selected.get(key, 0) + 1
            for fields, count in list(selected.items())[:40]:
                print('PES_RENDER_DETAIL=' + fields + ' samples=' + str(count), flush=True)
            if report.get('unavailable_optional'):
                print('PES_RENDER_OPTIONAL_UNAVAILABLE=' + ','.join(report['unavailable_optional']), flush=True)
        if not report["cleanup_verified"]:
            raise RuntimeError("Debugger cleanup not verified; preserve this capture directory")
        if not trace.report_passed(report):
            raise RuntimeError("Capture failed: " + "; ".join(report.get("errors", [])))
        observation_seconds = 20 if existing else 60
        if not existing:
            print("Collecting startup activity through 60 seconds; keep the Moonlight session connected.", flush=True)
        proc = Path("/proc") / str(identity["pid"])
        index = 0
        while True:
            state = trace.inspect_target(identity)
            if state.get("status") != "observed" or state.get("state") in ("Z", "X"):
                raise RuntimeError("PES exited or changed during startup: " + json.dumps(state))
            context.save_json(work / f"activity-{index}.json", context.sample(proc))
            index += 1
            if time.monotonic() - started >= observation_seconds:
                break
            time.sleep(min(5, max(0, observation_seconds - (time.monotonic() - started))))
        (work / "maps.txt").write_text((proc / "maps").read_text())
        record["target_after"] = trace.inspect_target(identity)
        record["observed_seconds"] = round(time.monotonic() - started, 3)
    except (Exception, KeyboardInterrupt) as error:
        record["errors"].append(type(error).__name__ + ": " + (str(error) or "Interrupted"))
        print("PES_STARTUP_ERROR=" + record["errors"][-1], flush=True)
    finally:
        if screenshots:
            try:
                record["screenshots"] = collect_screenshots(home, work, screenshots_before)
                kinds = {"game" if "_game_" in item["file"] else "hud" for item in record["screenshots"]}
                record["screenshots_complete"] = kinds == {"game", "hud"}
                if not record["screenshots_complete"]:
                    record["errors"].append("Both native screenshots were not saved; inspect capture and logs")
                print("PES_NATIVE_IMAGES=" + str(len(record["screenshots"])), flush=True)
            except Exception as error:
                record["errors"].append("screenshots: " + str(error))
        try:
            record["settings_after"] = settings(home)
            record["settings_unchanged"] = record["settings_after"] == before
            record["launcher_unchanged"] = checksum(wrapper) == wrapper_sha
        except Exception as error:
            record["errors"].append("final checks: " + str(error))
        # Snapshot a bounded log for the archive. The background game retains its own log FD.
        try:
            if existing is None:
                record["console_capture"] = context.copy_regular_log(
                    work / "emulator.log", work / "console", limit=32 * 1024 * 1024)
            if identity is not None and trace.inspect_target(identity).get("status") == "observed":
                context.collect_outputs(Path("/proc") / str(identity["pid"]), work)
        except OSError as error:
            record["errors"].append("console capture: " + str(error))
        if graphics:
            try:
                record["graphics"] = native.analyze(sorted(work.glob("console.*.log")))
                if not record["graphics"]["started"]:
                    record["errors"].append("Native GPU startup marker missing")
                print("PES_NATIVE_PACKET_COUNTS=" + json.dumps(
                    record["graphics"]["event_count_lower_bounds"], sort_keys=True), flush=True)
            except Exception as error:
                record["errors"].append("native graphics: " + str(error))
        if validation:
            try:
                record["vulkan_validation"] = vulkan.analyze(sorted(work.glob("console.*.log")))
                print("PES_VULKAN_MESSAGES=" + json.dumps(record["vulkan_validation"],
                                                         sort_keys=True), flush=True)
            except Exception as error:
                record["errors"].append("Vulkan validation: " + str(error))
        context.save_json(work / "startup.json", record)
        archive = work.with_suffix(".tar.gz")
        with tarfile.open(archive, "w:gz") as output:
            for path in sorted(work.rglob("*")):
                if (path.is_file() and path.name != "emulator.log" and
                        not {'validation-package', 'validation-runtime'}.intersection(
                            path.relative_to(work).parts)):
                    output.add(path, arcname=str(path.relative_to(work)), recursive=False)
        print("PES_STARTUP_ARCHIVE=" + str(archive), flush=True)
        print("Collection ended. PES is left running if it has not exited; close it normally when finished.", flush=True)
    return record


def main():
    if os.geteuid() == 0 or sys.argv[1:]:
        raise RuntimeError("Run as chreece, without sudo or arguments")
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    with (cache / "shadps4-video-trace.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(Path.home())


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print("PES_STARTUP_ERROR=" + (str(error) or "Interrupted"), flush=True)
    finally:
        print("Returning to your existing SSH prompt.", flush=True)
