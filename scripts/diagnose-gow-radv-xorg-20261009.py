#!/usr/bin/env python3
"""One bounded unattended GoW/RADV/Xorg failure reproduction.

Do not use sudo, change ES-DE, modify saves, change drivers, or replace source.
Collect read-only previous/current GPU reset evidence, screenshots, and a RADV
hang report if the system can produce one. Use only the isolated proven trial.
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
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
import threading
import time
import traceback

HOME = Path.home()
TRIAL = HOME / "Applications/shadps4-gow-stackalign-trial-20261009-203151/shadps4"
INSTALLED = HOME / "Applications/shadps4/shadps4"
TRIAL_SHA = "2e49283a3ef69d300c3a89ccc51e24f255ed777036125b1fc5e4b13440513839"
INSTALLED_SHA = "faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
REPORT = HOME / f"shadps4-gow-radv-xorg-{STAMP}.tar.gz"
WORK = Path(tempfile.mkdtemp(prefix=".gow-radv-xorg-", dir=HOME))
SCREEN_TIMES = (0, 2, 4, 6, 8, 10, 11, 12, 13, 14, 15, 16, 18, 21, 26, 33, 42, 54, 68)
PREVIOUS_BEGIN = "2026-10-09 20:56:25"
PREVIOUS_END = "2026-10-09 20:58:25"
BEGIN_WALL = time.time()
RESULT = "PRECHECK_FAILED"
PHASE = "preflight"
RUN = {"attempted": False, "visual": "NOT_REVIEWED", "menu_confirmed": False,
       "radv_debug": "hang,noumr", "screenshots": 0}
WATCH_PATTERN = re.compile(
    r"sunshine|es-de|shadps4|amdgpu|radv|xorg|drm|display|gpu.*(reset|hang)|"
    r"vulkan|recover|supervisor|watchdog|segfault|general protection|surface.lost|"
    r"out of memory|oom.kill", re.I)
MAX_REPORT_MB = 80
LOG_LIMIT = 2_500_000
GPU_BUSY_THRESHOLD = 99

def note(s):
    print(s, flush=True)

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def cmd_capture(args, outfile: Path, *, seconds=12, env=None, filter_lines=False):
    """Capture bounded read-only command output. No shell or sudo."""
    if not shutil.which(args[0]):
        outfile.write_text("COMMAND_NOT_INSTALLED " + str(args[0]) + "\n")
        return False
    try:
        proc = subprocess.run(args, capture_output=True, timeout=seconds,
                              env=env, check=False)
        data = proc.stdout.decode(errors="replace")
        err = proc.stderr.decode(errors="replace")
        if filter_lines:
            data = "\n".join(x for x in data.splitlines() if WATCH_PATTERN.search(x))
        outfile.write_text(
            f"COMMAND={args!r}\nEXIT_CODE={proc.returncode}\n"
            + data[-LOG_LIMIT:] + "\nSTDERR:\n" + err[-12000:])
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as e:
        outfile.write_text(f"COMMAND={args!r}\nERROR={e}\n")
        return False

def copy_last(src: Path, dest: Path, limit=LOG_LIMIT):
    try:
        if not src.is_file():
            return
        n = src.stat().st_size
        with src.open("rb") as f:
            if n > limit:
                f.seek(-limit, os.SEEK_END)
            data = f.read(limit)
        dest.parent.mkdir(exist_ok=True, parents=True)
        dest.write_bytes(data)
    except OSError as e:
        (WORK/"read-errors.txt").open("a").write(f"{src}: {e}\n")

def snap_logs(dirname: str, since: str, until: str | None = None):
    root = WORK / dirname
    root.mkdir(exist_ok=True)
    modes = [
        ("system.log", ["journalctl", "--no-pager", "--since", since,
                        "-o", "short-iso"], True),
        ("kernel.log", ["journalctl", "-k", "--no-pager", "--since", since,
                        "-o", "short-iso"], False),
        ("user.log", ["journalctl", "--user", "--no-pager", "--since", since,
                      "-o", "short-iso"], True),
        ("reset-watch.log", ["journalctl", "--no-pager", "-u",
                             "sunshine-amdgpu-reset-watch.service",
                             "--since", since], False),
    ]
    for filename, args, filtering in modes:
        if until:
            args.extend(["--until", until])
        cmd_capture(args, root/filename, seconds=8, filter_lines=filtering)
    # Preserve Xorg logs by timestamp; include rotated logs from old X session.
    candidates = [
        HOME/".local/share/xorg/Xorg.0.log",
        HOME/".local/share/xorg/Xorg.0.log.old",
        Path("/var/log/Xorg.0.log"),
        Path("/var/log/Xorg.0.log.old"),
        HOME/".xsession-errors",
    ]
    for path in candidates:
        if path.is_file():
            copy_last(path, root / (str(path).replace("/", "_").strip("_") + ".txt"),
                      limit=1_200_000)
    # Read-only service definitions and relevant supervisor/watchdog source lines.
    for unit in ("sunshine-amdgpu-reset-watch.service", "display-manager.service",
                 "sunshine.service", "sunshine-session-supervisor.service"):
        cmd_capture(["systemctl", "status", unit, "--no-pager", "--full"],
                    root/f"system-{unit}.txt", seconds=5)
        cmd_capture(["systemctl", "--user", "status", unit,
                     "--no-pager", "--full"],
                    root/f"user-{unit}.txt", seconds=5)
    for label, path in (
        ("amdgpu-reset-watch", Path("/usr/local/sbin/sunshine-amdgpu-reset-watch")),
        ("session-supervisor", Path("/usr/local/sbin/sunshine-session-supervisor")),
        ("kms-guard", HOME/".local/bin/sunshine-kms-guard.sh"),
        ("session-preflight", HOME/".local/bin/sunshine-session-preflight"),
    ):
        if not path.is_file():
            continue
        try:
            txt = path.read_text(errors="replace")
            filtered = [(n+1, line[:300]) for n, line in enumerate(txt.splitlines())
                        if re.search(r"amdgpu|hang|reset|xorg|sunshine|kill|restart|recover|"
                                     r"drm|journal|sleep|systemctl|pkill", line, re.I)]
            (root/f"source-{label}.txt").write_text(
                f"PATH={path}\nSHA256={sha256(path)}\nLINES={len(txt.splitlines())}\n"
                + "\n".join(f"{n}: {line}" for n, line in filtered[:160]))
        except OSError:
            pass

def service_pids() -> dict:
    pids = {"Xorg":[], "sunshine":[], "watchdog":[], "session_supervisor":[]}
    for fp in glob.glob("/proc/[0-9]*/comm"):
        try:
            pid = int(fp.split("/")[2])
            name = Path(fp).read_text(errors="replace").strip().lower()
            if "xorg" in name:
                pids["Xorg"].append(pid)
            if name == "sunshine":
                pids["sunshine"].append(pid)
            if name in ("bash", "python3"):
                try:
                    data = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00",b" ").decode(errors="replace")
                    if "sunshine-amdgpu-reset-watch" in data:
                        pids["watchdog"].append(pid)
                    if "sunshine-session-supervisor" in data:
                        pids["session_supervisor"].append(pid)
                except OSError:
                    pass
        except (OSError, ValueError):
            continue
    return {k:sorted(v) for k,v in pids.items()}

def gpu_stats() -> dict:
    stats = {}
    for path in Path("/sys/class/drm").glob("card*/device"):
        try:
            if (path/"vendor").read_text().strip().lower()!="0x1002":
                continue
            for name in ("gpu_busy_percent","mem_info_vram_used","mem_info_gtt_used"):
                try:stats[name]=int((path/name).read_text().strip())
                except (ValueError,OSError):pass
            return stats
        except OSError:
            pass
    return stats

def active_game_pids() -> list:
    pids = []
    for fp in glob.glob("/proc/[0-9]*/comm"):
        try:
            pid = int(fp.split("/")[2])
            if Path(fp).read_text().strip().lower() not in ("shadps4","drrun"):
                continue
            if Path(f"/proc/{pid}").stat().st_uid == os.getuid():
                pids.append(pid)
        except (OSError,ValueError):
            continue
    return pids

def graphical_environment() -> dict:
    env = os.environ.copy()
    picks = []
    for fp in glob.glob("/proc/[0-9]*/comm"):
        try:
            pid=int(fp.split("/")[2])
            if os.stat(fp).st_uid!=os.getuid():continue
            name=Path(fp).read_text().strip().lower()
            if "sunshine" not in name and "es-de" not in name:continue
            values={}
            for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\x00"):
                if b"=" in item:
                    k,v=item.split(b"=",1)
                    values[k.decode(errors="replace")]=v.decode(errors="surrogateescape")
            if values.get("DISPLAY"):
                picks.append((0 if "es-de" in name else 1,pid,name,values))
        except (OSError, UnicodeError, ValueError):
            continue
    picks.sort()
    if picks:
        _,pid,name,vals=picks[0]
        allowed=re.compile(r"^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|"
                           r"XDG_DATA_HOME|DBUS_SESSION_BUS_ADDRESS|XDG_SESSION_TYPE|"
                           r"PATH|LD_LIBRARY_PATH|PULSE_SERVER|SDL_.*|RADV_.*|"
                           r"MESA_.*|VK_.*|AMD_.*)$")
        for k,v in vals.items():
            if allowed.fullmatch(k):env[k]=v
        source=f"{name} PID={pid}"
    else:
        source="SSH_FALLBACK"
        if not env.get("DISPLAY") and Path("/tmp/.X11-unix/X0").exists():
            env["DISPLAY"]=":0"
        env.setdefault("XDG_RUNTIME_DIR",f"/run/user/{os.getuid()}")
    (WORK/"graphics-source.txt").write_text(
        f"ENV_SOURCE={source}\nDISPLAY={env.get('DISPLAY','<unset>')}\n"
        f"WAYLAND_DISPLAY={env.get('WAYLAND_DISPLAY','<unset>')}\n")
    if not env.get("DISPLAY"):
        raise RuntimeError("No X11 display available: no game will be launched")
    env["SHADPS4_CPU_ID_MODE"]="auto"
    env.pop("SHADPS4_CPU_ID_RESTART",None)
    return env

def shot(path: Path, env: dict, errorlog: Path, timeout=3.0) -> bool:
    ffmpeg=shutil.which("ffmpeg")
    if not ffmpeg:
        errorlog.write_text("ffmpeg missing; stopped before launch.\n")
        return False
    args=[ffmpeg,"-hide_banner","-nostdin","-loglevel","error",
          "-f","x11grab","-framerate","1","-i",env["DISPLAY"],
          "-frames:v","1","-vf","scale=1152:-2","-q:v","6",
          "-y",str(path)]
    try:
        p=subprocess.run(args,env=env,stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE,timeout=timeout,check=False)
        if p.returncode==0 and path.is_file() and path.stat().st_size>1500:
            return True
        with errorlog.open("a") as f:
            f.write(f"TIME={time.time():.3f} rc={p.returncode} error={p.stderr[-1200:]!r}\n")
    except (OSError,subprocess.TimeoutExpired) as e:
        with errorlog.open("a") as f:
            f.write(f"TIME={time.time():.3f} capture_error={str(e)[:300]}\n")
    path.unlink(missing_ok=True)
    return False

def make_contact_sheet(folder: Path):
    try:
        from PIL import Image, ImageDraw
        files=sorted((folder/"screenshots").glob("*.jpg"))
        if not files:return
        w,h,cols=400,250,3
        canvas=Image.new("RGB",(w*cols,h*((len(files)+cols-1)//cols)),(12,14,19))
        d=ImageDraw.Draw(canvas)
        for index,path in enumerate(files):
            with Image.open(path) as inp:
                im=inp.convert("RGB")
                im.thumbnail((w-8,h-32))
                x=(index%cols)*w+4
                y=(index//cols)*h+27
                canvas.paste(im,(x,y))
                d.text((x+2,y-23),path.stem,fill=(245,245,245))
        canvas.save(folder/"contact-sheet.jpg",quality=80)
    except (ImportError,OSError) as e:
        (folder/"contact-sheet-error.txt").write_text(str(e))

def collect_radv_dumps(existing: set[str]):
    target=WORK/"radv-hang-dumps"
    target.mkdir(exist_ok=True)
    remaining=32_000_000
    metadata=[]
    for root in sorted(HOME.glob("radv_dumps_*")):
        if not root.is_dir() or str(root) in existing:
            continue
        for file in sorted(root.rglob("*")):
            if not file.is_file():continue
            try:
                sz=file.stat().st_size
                relative=file.relative_to(root)
                metadata.append({"file":str(file),"size":sz})
                if remaining<=0:continue
                # Exclude very large binary dumps; preserve useful ring/pipeline logs first.
                if sz>remaining or sz>8_000_000:continue
                dest=target/root.name/relative
                dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(file,dest)
                remaining-=sz
            except (OSError,ValueError) as e:
                metadata.append({"file":str(file),"error":str(e)})
    (target/"manifest.json").write_text(json.dumps(metadata,indent=2))

def stop_only_own_session(proc):
    for sig,wait in ((signal.SIGINT,5),(signal.SIGTERM,5),(signal.SIGKILL,3)):
        if proc.poll() is not None:return
        try:
            # Popen(... start_new_session=True): group's PGID == its PID.
            if os.getpgid(proc.pid)!=proc.pid:return
            os.killpg(proc.pid,sig)
            proc.wait(timeout=wait)
        except ProcessLookupError:return
        except subprocess.TimeoutExpired:continue

def classifier(file:Path):
    txt=file.read_bytes()[-10_000_000:].decode(errors="replace")
    patterns = {
        "first_frame_marker":r"STARTUP_UI event=first_game_frame",
        "black_frame_marker":r"STARTUP_UI event=black_game_frame",
        "gpu_device_lost":r"Device lost during submit|VK_ERROR_DEVICE_LOST",
        "x11_connection_lost":r"XIO:.*fatal IO error|ErrorSurfaceLostKHR",
        "fmt_fault":r"parse_format_string.*Unhandled",
        "srt_walker_failures":r"Failed to compute offset for SRT walker",
        "findilsb32_failures":r"Unexpected instruction for offset computation, FindILsb32",
        "phi_failures":r"Unexpected instruction for offset computation, Phi",
        "buffer_clamps":r"Clamped size from",
    }
    return {k:len(re.findall(v,txt,re.I)) for k,v in patterns.items()}

def one_instrumented_run(env):
    global PHASE,RESULT
    folder=WORK/"01-radv-instrumented"
    (folder/"screenshots").mkdir(parents=True)
    record=folder/"events.jsonl"
    log=folder/"emulator-stdout.log"
    errors=folder/"screenshot-errors.txt"
    captures=[]
    snapshots=[]
    stop=threading.Event()
    screenshot_results=[]
    own_display=service_pids()
    before_gpu=gpu_stats()
    (folder/"initial-pids.json").write_text(json.dumps(own_display,indent=2))
    (folder/"initial-gpu.json").write_text(json.dumps(before_gpu,indent=2))
    env=env.copy()
    env["RADV_DEBUG"]="hang,noumr"
    # Mesa hang changes GPU synchronization. This is a *diagnostic* run,
    # not comparable to an ordinary performance/playability run.
    begin=time.monotonic()
    done_reason=None
    PHASE="automatic GPU-hang-instrumented game run"
    cmd=[str(TRIAL),"--cpu-id-mode","auto","--game","CUSA34384","--fullscreen","true"]
    with log.open("wb") as stdout:
        p=subprocess.Popen(cmd,env=env,stdout=stdout,stderr=subprocess.STDOUT,
                           start_new_session=True)
        RUN["attempted"]=True
        RUN["pid"]=p.pid
        RUN["command"]=cmd

        def shutter():
            # Independent capture worker avoids 2+ second capture hangs blocking
            # the crash/display process telemetry loop.
            for at in SCREEN_TIMES:
                while not stop.is_set() and time.monotonic()-begin < at:
                    time.sleep(0.09)
                if stop.is_set():break
                elapsed=time.monotonic()-begin
                file=folder/"screenshots"/f"{len(screenshot_results):02d}_t{at:03d}.jpg"
                ok=shot(file,env,errors,timeout=2.8)
                screenshot_results.append({"intended_seconds":at,"actual_seconds":round(elapsed,2),
                                           "ok":ok,"name":file.name if ok else None})
                if ok:note(f"Screenshot t={at:02d}s: captured")
                else:note(f"Screenshot t={at:02d}s: X11 capture unavailable")
            stop.set() if False else None

        worker=threading.Thread(target=shutter,name="GoWScreenshots",daemon=True)
        worker.start()
        last_pids=own_display
        xorg_changed=False
        xorg_miss=None
        gpu_100_at=None
        t_next=0.0
        show_state=[]
        with record.open("w") as timeline:
            while True:
                elapsed=time.monotonic()-begin
                if elapsed>=t_next:
                    pids=service_pids()
                    gpu=gpu_stats()
                    xorg=pids.get("Xorg",[])
                    if pids!=last_pids:
                        show_state.append({"time_s":round(elapsed,2),
                                           "type":"SERVICE_PID_CHANGE",
                                           "previous":last_pids,"current":pids})
                        note(f"Service PID change at {elapsed:.1f}s: Xorg {last_pids.get('Xorg')} -> {xorg}, "
                             f"Sunshine {last_pids.get('sunshine')} -> {pids.get('sunshine')}")
                        if xorg!=last_pids.get("Xorg"):
                            xorg_changed=True
                            if xorg_miss is None:xorg_miss=elapsed
                        last_pids=pids
                    if gpu.get("gpu_busy_percent",0)>=GPU_BUSY_THRESHOLD:
                        if gpu_100_at is None:gpu_100_at=elapsed
                    else:
                        gpu_100_at=None
                    event={"elapsed_s":round(elapsed,2),"utc":dt.datetime.now(dt.timezone.utc).isoformat(),
                           "gpu":gpu,"services":pids,"trial_exit_code":p.poll(),
                           "screenshot_count":len(screenshot_results),
                           "screenshot_errors":sum(not x["ok"] for x in screenshot_results)}
                    timeline.write(json.dumps(event)+"\n")
                    timeline.flush()
                    t_next=elapsed+1.0
                if p.poll() is not None:
                    done_reason="EMULATOR_EXITED"
                    break
                if xorg_miss is not None and elapsed-xorg_miss>=14:
                    done_reason="XORG_CHANGED_WAITED_FOR_RADV_DUMP"
                    break
                if elapsed>=95:
                    done_reason="TIME_LIMIT_REACHED"
                    break
                time.sleep(0.15)
        if p.poll() is None:
            stop_only_own_session(p)
        p.wait()
        stop.set()
        worker.join(timeout=4)
        RUN["exit_code"]=p.returncode
        RUN["duration_seconds"]=round(time.monotonic()-begin,2)
        RUN["ended_because"]=done_reason
        RUN["xorg_pid_changed"]=xorg_changed
        RUN["observed_service_changes"]=show_state
        RUN["screenshot_count"]=sum(x["ok"] for x in screenshot_results)
        RUN["screenshots"]=screenshot_results
        RUN["start_pids"]=own_display
        RUN["end_pids"]=service_pids()
        RUN["gpu_at_end"]=gpu_stats()
        (folder/"screenshot-timeline.json").write_text(json.dumps(screenshot_results,indent=2))
        (folder/"run-result.json").write_text(json.dumps(RUN,indent=2))
    make_contact_sheet(folder)
    RUN["log_findings"]=classifier(log)
    # Keep head and tail of verbose shader output without losing milestones.
    with log.open("rb") as inp:
        head=inp.read(240_000)
        inp.seek(max(0,log.stat().st_size-1_800_000))
        tail=inp.read(1_800_000)
    (folder/"emulator-stdout-first.log").write_bytes(head)
    (folder/"emulator-stdout-last.log").write_bytes(tail)
    log.unlink(missing_ok=True)
    for root in (HOME/".local/share/shadPS4/log",TRIAL.parent/"user/log"):
        for name in ("CUSA34384.log","shadps4.log"):
            if (root/name).is_file():
                copy_last(root/name,folder/f"game-{name}",limit=1_500_000)
    (folder/"run-result.json").write_text(json.dumps(RUN,indent=2))
    note("Trial exit=%s, after %.1fs. Photos=%d. Xorg changed=%s" %
         (p.returncode,RUN["duration_seconds"],RUN["screenshot_count"],xorg_changed))

def write_manifest():
    manifest={
        "report_kind":"GoW_RADV_hang_and_X11_reset",
        "result":RESULT,
        "phase":PHASE,
        "run":RUN,
        "intro_menu_confirmed":False,
        "moonlight_needed_now":False,
        "manual_visual_review_required":True,
        "trial_sha_actual":sha256(TRIAL) if TRIAL.is_file() else None,
        "production_sha_actual":sha256(INSTALLED) if INSTALLED.is_file() else None,
        "original_production_binary_unchanged": (
            INSTALLED.is_file() and sha256(INSTALLED)==INSTALLED_SHA),
        "ssh_session_preserved":True,
    }
    (WORK/"manifest.json").write_text(json.dumps(manifest,indent=2))

def main():
    global PHASE,RESULT
    note("=== GOD OF WAR: UNATTENDED GPU HANG + XORG RESET FORENSICS ===")
    note("No Moonlight needed. No sudo, system changes, or ES-DE modifications.")
    PHASE="collect historical evidence from prior failing launch"
    snap_logs("00-prior-incident",PREVIOUS_BEGIN,PREVIOUS_END)
    PHASE="verify isolated trial and running services"
    if not TRIAL.is_file() or not INSTALLED.is_file():
        raise RuntimeError("Trial or installed emulator binary not found")
    if TRIAL.resolve()==INSTALLED.resolve():
        raise RuntimeError("Refusing to run: trial resolves to live ES-DE binary")
    if sha256(TRIAL)!=TRIAL_SHA or sha256(INSTALLED)!=INSTALLED_SHA:
        raise RuntimeError("Checksum mismatch; preserving current binaries")
    for name in ("bin64/drrun","libshadps4_cpu_id.so",
                 "lib64/release/libdynamorio.so"):
        if not (TRIAL.parent/"cpu-id-runtime"/name).is_file():
            raise RuntimeError("Trial CPU-ID runtime missing: "+name)
    running=active_game_pids()
    if running:
        raise RuntimeError(f"A game is already running (PID {running}); refusing to interrupt it")
    env=graphical_environment()
    pre=WORK/"before-launch"
    pre.mkdir()
    old_display=service_pids()
    (pre/"pids.json").write_text(json.dumps(old_display,indent=2))
    (pre/"gpu.json").write_text(json.dumps(gpu_stats(),indent=2))
    # Ensure X can still capture quickly; don't repeat a GPU crash against a dead X server.
    if not shot(pre/"display-before.jpg",env,pre/"capture-error.txt",timeout=4):
        raise RuntimeError("Current X11 display can't be captured: no game launched")
    time.sleep(3)
    if old_display.get("Xorg")!=service_pids().get("Xorg"):
        raise RuntimeError("Xorg restarted during preflight: no game launched")
    if gpu_stats().get("gpu_busy_percent",0)>=95:
        raise RuntimeError("GPU remained busy (>95%) during preflight: no game launched")
    PHASE="check previously generated RADV reports"
    existing={str(p) for p in HOME.glob("radv_dumps_*") if p.is_dir()}
    PHASE="run ONE isolated diagnostic, with visual and Xorg telemetry"
    one_instrumented_run(env)
    PHASE="capture recovery evidence after trial"
    time.sleep(4)
    snap_logs("02-after-trial",dt.datetime.fromtimestamp(BEGIN_WALL).strftime("%Y-%m-%d %H:%M:%S"))
    cmd_capture(["ps","-eo","pid,ppid,stat,etimes,comm,args"],
                WORK/"processes-after.txt", seconds=6, filter_lines=True)
    cmd_capture(["vulkaninfo","--summary"], WORK/"vulkan-after.txt", seconds=10)
    PHASE="collect RADV GPU hang report if generated"
    collect_radv_dumps(existing)
    PHASE="verify unchanged installed emulator"
    if sha256(INSTALLED)!=INSTALLED_SHA:
        raise RuntimeError("ALERT: installed ES-DE executable unexpectedly differs")
    RESULT="REPORT_READY_FOR_REVIEW"
    note("No menu or intro claimed; screenshots need manual visual review.")

try:
    main()
except KeyboardInterrupt:
    RESULT="INTERRUPTED_WITH_REPORT"
    (WORK/"error.txt").write_text("User cancelled; only our own trial process group may be stopped.\n")
except BaseException:
    RESULT="SAFE_STOP_WITH_REPORT"
    (WORK/"error.txt").write_text(traceback.format_exc())
finally:
    PHASE_END=PHASE
    try:write_manifest()
    except BaseException as e:note("Could not finalize manifest: "+str(e))
    try:
        with tarfile.open(REPORT,"w:gz",compresslevel=6) as archive:
            for f in sorted(WORK.iterdir()):
                archive.add(f,arcname=f.name)
        note(f"\nRESULT={RESULT}")
        note(f"LAST_PHASE={PHASE_END}")
        note(f"REPORT={REPORT}")
        note(f"TRIAL_RUNS={int(RUN['attempted'])}")
        note("OPEN_MOONLIGHT=NO")
        note("UPLOAD_ARCHIVE_FOR_IMAGE_AND_LOG_REVIEW=YES")
        note("ESDE_INSTALLATION_AND_SSH=UNCHANGED")
    finally:
        shutil.rmtree(WORK,ignore_errors=True)
sys.exit(0 if RESULT=="REPORT_READY_FOR_REVIEW" else 1)
