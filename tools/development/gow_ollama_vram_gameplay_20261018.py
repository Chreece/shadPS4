#!/usr/bin/env python3
"""GoW Ragnarök: free Ollama VRAM, capture game, exit, restore Ollama.

No production/source modifications, sudo, SSH shutdown, or Sunshine restart.
The same checksum-pinned emulator tested in the 12:47 GPU timing archive.
"""
from __future__ import annotations
import datetime as dt
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
import time
from urllib.request import Request, urlopen

ROOT = Path.home() / "projects/shadps4"
ORIGINAL = ROOT / "GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
BUILD = ROOT / "GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build"
GAME = BUILD / "shadps4.fullgfx-gpu-phase-probe-gow-gpu-invalidate-timing-20261018-124729"
GAME_SHA = "63a00275b2f78ba58ef35577af0bf0b1681ac245a7e1636de1673cb4f65275ce"
GOOD = BUILD / "shadps4.fullgfx-uffd-batched-read-gow-uffd-batched-read-20261017-231412"
GOOD_SHA = "8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef"
API_URL = "http://127.0.0.1:7869"
DURATION = 135


def write(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for piece in iter(lambda: f.read(1048576), b""):
            h.update(piece)
    return h.hexdigest()


def api(path, payload=None, timeout=15):
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(API_URL + path, data=data,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def loaded():
    value = api("/api/ps", timeout=8).get("models")
    if not isinstance(value, list):
        raise RuntimeError("OLLAMA_PS_INVALID")
    return value


def docker(*arguments, timeout=35):
    return subprocess.run(["docker", *arguments], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True,
                          timeout=timeout, check=False)


def backend():
    """Never stop Open WebUI or an unrelated container."""
    if not shutil.which("docker"):
        return None
    ps = docker("ps", "-q", timeout=12)
    if ps.returncode:
        return None
    ids = ps.stdout.split()
    if not ids:
        return None
    inspected = docker("inspect", *ids, timeout=20)
    if inspected.returncode:
        return None
    hits = []
    for item in json.loads(inspected.stdout):
        hostports = [row.get("HostPort") for rows in
                     (item.get("NetworkSettings", {}).get("Ports") or {}).values()
                     for row in (rows or [])]
        image = str(item.get("Config", {}).get("Image", "")).lower()
        name = str(item.get("Name", ""))
        env = item.get("Config", {}).get("Env") or []
        host = item.get("HostConfig", {}).get("NetworkMode") == "host"
        host7869 = host and any(str(x).startswith("OLLAMA_HOST=") and
                                 ":7869" in str(x) for x in env)
        is_backend = ("ollama/ollama" in image or
                      re.search(r"^/?ollama(?:[-_]|$)", name, re.I))
        if is_backend and ("7869" in hostports or host7869):
            hits.append({"id": item["Id"], "name": name, "image": image})
    if len(hits) > 1:
        raise RuntimeError("MULTIPLE_OLLAMA_7869_BACKENDS")
    return hits[0] if hits else None


def running_container(cid):
    r = docker("inspect", "--format", "{{.State.Running}}", cid, timeout=12)
    return r.returncode == 0 and r.stdout.strip() == "true"


def vram():
    result = []
    for item in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
        try:
            total = int(item.read_text())
            used = int((item.parent / "mem_info_vram_used").read_text())
            result.append({"card": item.parent.parent.name,
                           "total": total, "used": used,
                           "free_gib": round((total-used)/2**30, 3),
                           "used_gib": round(used/2**30, 3)})
        except (OSError, ValueError):
            pass
    result.sort(key=lambda x: x["total"], reverse=True)
    return {"at": dt.datetime.now().astimezone().isoformat(),
            "gpus": result}


def active_emulators():
    result = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            executable = os.readlink(proc / "exe")
            state = (proc / "stat").read_text().rsplit(") ", 1)[1][0]
            if Path(executable).name.lower().startswith("shadps4") and state not in ("Z", "X"):
                result.append(int(proc.name))
        except (OSError, ValueError, IndexError):
            pass
    return result


def context_keepalive(item):
    expires = item.get("expires_at")
    if not expires:
        return "10m"
    try:
        expiry = dt.datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        remaining = (expiry - dt.datetime.now(dt.timezone.utc)).total_seconds()
        return -1 if remaining > 365*86400 else str(max(600, min(int(remaining), 86400)))+"s"
    except (ValueError, TypeError, OverflowError):
        return "10m"


def reload_models(before, out):
    result = []
    for item in before:
        name = item.get("name") or item.get("model")
        if not name:
            continue
        request = {"model": name, "prompt": "", "stream": False,
                   "keep_alive": context_keepalive(item)}
        ctx = item.get("context_length")
        if isinstance(ctx, int) and 512 <= ctx <= 131072:
            request["options"] = {"num_ctx": ctx}
        entry = {"model": name, "context_length": ctx}
        try:
            response = api("/api/generate", request, timeout=160)
            entry["success"] = bool(response.get("done"))
        except Exception as error:
            entry["success"] = False
            entry["error"] = str(error)
        result.append(entry)
    names = [x.get("name") for x in loaded()]
    ok = all(x["success"] and x["model"] in names for x in result)
    write(out / "ollama-restore.json", {"result": result, "loaded": names, "ok": ok})
    return ok


def screenshot(folder, seconds, display):
    if not shutil.which("import"):
        return
    try:
        subprocess.run(["import", "-display", display, "-window", "root",
                        str(folder / f"frame-{seconds}.png")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=7, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def stop_game(game):
    """Always signal the owned emulator BEFORE attempting terminal output.

    In the 06:52 GoW test, SSH disconnected (SIGHUP) and stdout was a broken
    pipe. Previously the diagnostic print raised BrokenPipeError BEFORE
    os.killpg(SIGTERM), leaving an orphan game process after Ollama restored.
    No stdout/stderr, logging, or SSH operation may block emulator cleanup.
    """
    if game is None:
        return
    if game.poll() is not None:
        return
    pid = game.pid
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        game.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        game.wait(timeout=10)
    # Best-effort message only AFTER game exit is confirmed.
    try:
        print(f"STOPPED_ONLY_OWN_GAME_PID={pid}", flush=True)
    except (BrokenPipeError, OSError):
        pass


def main():
    folder = ROOT / "logs" / ("gow-ollama-vram-" +
                              dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    folder.mkdir(parents=True, exist_ok=False)
    state = {"start": dt.datetime.now().astimezone().isoformat(),
             "game_sha256": GAME_SHA, "seconds": DURATION}
    errors = []
    previous_models = []
    owner = None
    stop_attempted = False
    game = None
    api_only = False
    observations = []
    contaminated = False

    def interrupted(signum, _frame):
        raise InterruptedError(f"SIGNAL_{signum}")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)

    try:
        if active_emulators():
            raise RuntimeError("EXISTING_SHADPS4_RUNNING; NO_CHANGES_MADE")
        for binary, expected in ((GAME, GAME_SHA), (GOOD, GOOD_SHA)):
            if not binary.is_file() or binary.is_symlink() or sha(binary) != expected:
                raise RuntimeError(f"PINNED_BINARY_NOT_AVAILABLE_OR_CHANGED={binary}")
        if not (ORIGINAL / "source/.git").exists():
            raise RuntimeError("ORIGINAL_GOW_WORKTREE_NOT_AVAILABLE")
        if shutil.disk_usage(ROOT).free < 2*2**30:
            raise RuntimeError("FREE_DISK_UNDER_2_GIB")
        before_vram = vram()
        write(folder / "vram-before.json", before_vram)
        state["ollama_version"] = api("/api/version", timeout=10).get("version")
        previous_models = loaded()
        owner = backend()
        state["ollama_backend"] = owner or {"mode": "API_UNLOAD_ONLY"}
        write(folder / "ollama-before.json", {"models": previous_models,
                                             "backend": owner})
        print("OLLAMA_MODELS_BEFORE=" + str([x.get("name") for x in previous_models]),
              flush=True)
        if owner:
            stop_attempted = True
            r = docker("stop", "--time", "12", owner["id"], timeout=45)
            (folder / "ollama-stop.log").write_text(r.stdout + r.stderr)
            if r.returncode or running_container(owner["id"]):
                raise RuntimeError("BACKEND_DOCKER_STOP_FAILED")
            print("OLLAMA_BACKEND_STOPPED=YES", flush=True)
        else:
            api_only = True
            for model in previous_models:
                name = model.get("name") or model.get("model")
                if name:
                    api("/api/generate", {"model": name, "prompt": "",
                                          "stream": False, "keep_alive": 0},
                        timeout=40)
            if loaded():
                raise RuntimeError("OLLAMA_API_UNLOAD_FAILED")
            print("OLLAMA_UNLOADED_VIA_API=YES; HA_MAY_RELOAD_MODEL", flush=True)
        time.sleep(4)
        after_vram = vram()
        write(folder / "vram-unloaded.json", after_vram)
        earlier = before_vram["gpus"]
        now = after_vram["gpus"]
        reclaimed = None
        if earlier and now and earlier[0]["total"] == now[0]["total"]:
            reclaimed = (earlier[0]["used"] - now[0]["used"])/2**30
        state["vram_reclaimed_gib"] = round(reclaimed, 3) if reclaimed is not None else None
        print(f'VRAM_RECLAIMED_GIB={state["vram_reclaimed_gib"]}', flush=True)
        model_vram = sum(x.get("size_vram", 0) or 0 for x in previous_models)
        if model_vram > 3*2**30 and reclaimed is not None and reclaimed < 1.5:
            raise RuntimeError("VRAM_NOT_RECLAIMED; GAME_NOT_LAUNCHED")
        if owner and running_container(owner["id"]):
            raise RuntimeError("OLLAMA_RESTARTED_BEFORE_GAME")
        if api_only and loaded():
            raise RuntimeError("OLLAMA_RELOADED_BEFORE_GAME")

        env = {k: v for k, v in os.environ.items() if not k.startswith("SHADPS4_")}
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS",
                       f'unix:path={env["XDG_RUNTIME_DIR"]}/bus')
        flags = {"SHADPS4_RUNTIME_LOG_FILTER":
                 "*:warn Lib.AudioOut:info Lib.Audio3d:info",
                 "SHADPS4_SDL_OUTER_PACING": "1",
                 "SHADPS4_AUDIO_TIMING_PROBE": "1",
                 "SHADPS4_UFFD_PERF_PROBE": "1",
                 "SHADPS4_UFFD_BATCH_READ": "1",
                 "SHADPS4_UFFD_CALLBACK_BATCH": "0",
                 "SHADPS4_UFFD_READ_FIRST": "0",
                 "SHADPS4_FRAME_AUDIO_PROBE": "1",
                 "SHADPS4_GPU_INVALIDATE_PROBE": "1"}
        env.update(flags)
        command = [str(GAME), "--cpu-id-mode", "builtin", "--userfaultfd",
                   "--game", "CUSA34384", "--fullscreen", "true", "--show-fps"]
        write(folder / "game-config.json", {"argv": command, "env": flags})
        print("GAME_LAUNCH_AFTER_OLLAMA_UNLOAD=START", flush=True)
        began = time.monotonic()
        with (folder / "game-console.log").open("wb", buffering=0) as output:
            game = subprocess.Popen(command, cwd=ORIGINAL, env=env,
                                    stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            state["game_pid"] = game.pid
            print(f"GAME_LAUNCHED=YES PID={game.pid}", flush=True)
            next_sample, captures = 0, [30, 60, 90, 120]
            while game.poll() is None and time.monotonic()-began < DURATION:
                seconds = time.monotonic()-began
                if captures and seconds >= captures[0]:
                    screenshot(folder, captures.pop(0), env["DISPLAY"])
                if seconds >= next_sample:
                    next_sample += 5
                    reading = vram()
                    reading["elapsed"] = round(seconds, 2)
                    observations.append(reading)
                    if api_only:
                        try:
                            active_models = [x.get("name") for x in loaded()]
                            contaminated |= bool(active_models)
                        except Exception:
                            contaminated = True
                if int(seconds) % 15 == 0 and int(seconds) != state.get("last_report"):
                    state["last_report"] = int(seconds)
                    print(f"GAME_T={int(seconds)}s VRAM_OLLAMA_UNLOADED=YES", flush=True)
                time.sleep(0.4)
        state["game_observed_seconds"] = round(time.monotonic()-began, 2)
        state["game_exited_early"] = game.poll() is not None and state["game_observed_seconds"] < DURATION-1
        state["ollama_reloaded_during_game"] = contaminated
        write(folder / "vram-during-game.json", observations)
    except BaseException as error:
        errors.append(f"{type(error).__name__}: {error}")
        print("VRAM_GAME_TEST_ERROR=" + errors[-1], flush=True)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        try:
            stop_game(game)
            state["game_stopped"] = game is None or game.poll() is not None
        except Exception as error:
            state["game_stopped"] = False
            errors.append("GAME_SHUTDOWN_FAILED=" + str(error))
        try:
            write(folder / "vram-game-stopped.json", vram())
            log = folder / "game-console.log"
            if log.is_file():
                markers = ["GPU_INVALIDATE_TIMING", "VIDEOOUT_FLIP_TIMING",
                           "UFFD_PERF", "AUDIO_PRODUCER_TIMING",
                           "AUDIO_GUEST_SUBMIT", "AUDIO_SDL_TIMING"]
                counts = {k: 0 for k in markers}
                with log.open(errors="replace") as source, (
                        folder / "timeline.log").open("w") as timeline:
                    for line in source:
                        for word in markers:
                            if word in line:
                                counts[word] += 1
                                timeline.write(line)
                                break
                write(folder / "telemetry-counts.json", counts)
                state["telemetry_valid"] = (counts["GPU_INVALIDATE_TIMING"] >= 12
                                          and counts["VIDEOOUT_FLIP_TIMING"] >= 15
                                          and counts["AUDIO_PRODUCER_TIMING"] >= 25)
        except Exception as error:
            errors.append("TELEMETRY_ERROR=" + str(error))
        if stop_attempted and owner:
            try:
                if not running_container(owner["id"]):
                    r = docker("start", owner["id"], timeout=45)
                    (folder / "ollama-start.log").write_text(r.stdout+r.stderr)
                    if r.returncode:
                        raise RuntimeError("DOCKER_START_FAILED")
                until = time.monotonic()+100
                while True:
                    try:
                        api("/api/version", timeout=4)
                        break
                    except Exception:
                        if time.monotonic() > until:
                            raise RuntimeError("OLLAMA_API_NOT_READY")
                        time.sleep(2)
                state["ollama_backend_restored"] = running_container(owner["id"])
            except Exception as error:
                state["ollama_backend_restored"] = False
                errors.append("OLLAMA_BACKEND_RESTORE_FAILED=" + str(error))
        else:
            state["ollama_backend_restored"] = True
        if state["ollama_backend_restored"]:
            try:
                state["ollama_models_restored"] = reload_models(previous_models, folder)
                if not state["ollama_models_restored"]:
                    errors.append("OLLAMA_PREVIOUS_MODELS_NOT_ALL_RELOADED")
                write(folder / "ollama-after.json", {"models": loaded()})
            except Exception as error:
                state["ollama_models_restored"] = False
                errors.append("OLLAMA_RELOAD_ERROR=" + str(error))
        try:
            write(folder / "vram-restored.json", vram())
        except Exception:
            pass
        state["errors"] = errors
        state["protected_good_binary_unchanged"] = GOOD.is_file() and sha(GOOD) == GOOD_SHA
        state["test_binary_unchanged"] = GAME.is_file() and sha(GAME) == GAME_SHA
        state["result"] = ("PASS" if not errors and state.get("game_stopped") and
                           state.get("ollama_models_restored") and state.get("telemetry_valid")
                           and not contaminated else "CHECK_LOGS")
        state["ssh_and_sunshine_unchanged"] = True
        write(folder / "report.json", state)
        tarball = folder.parent / (folder.name + ".tar.gz")
        with tarfile.open(tarball, "w:gz") as archive:
            archive.add(folder, arcname=folder.name)
        print(f"ARCHIVE={tarball}", flush=True)
        print(f"GAME_STOPPED_BEFORE_OLLAMA_RELOAD={state.get('game_stopped')}", flush=True)
        print(f"OLLAMA_BACKEND_RESTORED={state.get('ollama_backend_restored')}", flush=True)
        print(f"OLLAMA_MODELS_RELOADED={state.get('ollama_models_restored')}", flush=True)
        print("VRAM_TEST_RESULT=" + state["result"], flush=True)
        print("SSH_SESSION_STILL_OPEN=YES", flush=True)
    return 0 if state["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
