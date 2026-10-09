#!/usr/bin/env python3
"""Capture the guest compute shader corresponding to the RADV GPU-hang SPIR-V.

Temporarily instruments only the already verified shadPS4 build source.
Builds and runs a SEPARATE trial with the proven stack-alignment fix.
Restores the untouched source files before launching. Never installs into ES-DE.
"""
from __future__ import annotations
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

HOME=Path.home()
SRC=HOME/"shadps4-esde-verified-builds/20261009-173836/source"
BLD=HOME/"shadps4-esde-verified-builds/20261009-174801/build"
LIVE=HOME/"Applications/shadps4/shadps4"
PROCESS=SRC/"src/core/libraries/kernel/process.cpp"
PIPELINE=SRC/"src/video_core/renderer_vulkan/vk_pipeline_cache.cpp"
EXPECTED_HEAD="4eb9fc5f92188abbb30eb2cc55980e922b2a0ec0"
EXPECTED_PROCESS="05acbce16dc315c5abaccb5190011ceed6ad848a"
EXPECTED_LIVE="faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834"
MESA_SPV_SIZE=68716
MESA_SPV_FNV=0xe76c71706f4182ec
STAMP=dt.datetime.now().strftime("%Y%m%d-%H%M%S")
REPORT=HOME/f"shadps4-gow-compute-shader-{STAMP}.tar.gz"
TRIAL=HOME/f"Applications/shadps4-gow-compute-trial-{STAMP}"
WORK=Path(tempfile.mkdtemp(prefix=".gow-compute-shader-",dir=HOME))
BACKUPS={}
RESULT="PRECHECK_FAILED"
PHASE="preflight"
TRIAL_PROCESS=None
SCREEN_TIMES=[0,3,6,9,11,12,13,14,16,18,20,24,29,38,48]
START_WALL=time.time()

def note(message):print(message,flush=True)
def run(args, *, timeout=None, env=None, logfile=None):
    if logfile:
        with open(logfile,"wb") as f:
            proc=subprocess.run(args,env=env,stdout=f,stderr=subprocess.STDOUT,
                                timeout=timeout,check=False)
    else:
        proc=subprocess.run(args,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                            timeout=timeout,check=False)
    return proc
def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()
def git(*args):
    return run(["git","-C",str(SRC),*args],timeout=15)
def verified_head():
    result=git("rev-parse","HEAD")
    return result.stdout.decode(errors="replace").strip() if result.returncode==0 else ""
def blob(path):
    result=git("hash-object",str(path))
    return result.stdout.decode(errors="replace").strip() if result.returncode==0 else ""
def die(reason):raise RuntimeError("SAFE_STOP: "+reason)
def restore_source():
    restored=[]
    for dest,original in BACKUPS.items():
        try:
            Path(dest).write_bytes(original)
            os.utime(dest,None)
            restored.append(str(dest))
        except OSError as e:
            (WORK/"RESTORE_ERROR.txt").open("a").write(f"{dest}: {e}\n")
    (WORK/"source-restoration.json").write_text(json.dumps({
        "expected":list(BACKUPS),"restored":restored,
        "process_hash":blob(PROCESS) if PROCESS.exists() else None,
        "pipeline_hash":blob(PIPELINE) if PIPELINE.exists() else None,
        "working_tree_clean": git("status","--porcelain","--untracked-files=no").stdout.decode(errors="replace").strip()=="",
    },indent=2))
    BACKUPS.clear()

# This instrumentation logs only near-size compute SPIR-V modules and preserves
# the exact host SPIR-V plus original guest shader bytes for hash matching.
CAPTURE_CODE=r'''
    if (const char* trace_dir = std::getenv("SHADPS4_GOW_SHADER_TRACE_DIR");
        trace_dir && *trace_dir && info.hw_stage == HwStage::Compute) {
        const auto spv_bytes = spv.size() * sizeof(u32);
        if (spv_bytes >= 65000 && spv_bytes <= 75000) {
            const auto* ptr = reinterpret_cast<const unsigned char*>(spv.data());
            uint64_t fnv = UINT64_C(0xcbf29ce484222325);
            for (size_t n = 0; n < spv_bytes; ++n) {
                fnv = (fnv ^ ptr[n]) * UINT64_C(0x100000001b3);
            }
            const bool exact = fnv == UINT64_C(0xe76c71706f4182ec) &&
                               spv_bytes == 68716;
            const auto key = fmt::format("cs_{:016x}_{:016x}", info.pgm_hash, fnv);
            std::filesystem::path dir{trace_dir};
            std::error_code error;
            std::filesystem::create_directories(dir, error);
            if (!error) {
                std::ofstream spvfile(dir / (key + ".spv"), std::ios::binary);
                spvfile.write(reinterpret_cast<const char*>(spv.data()), spv_bytes);
                std::ofstream guestfile(dir / (key + ".bin"), std::ios::binary);
                guestfile.write(reinterpret_cast<const char*>(code.data()),
                                code.size_bytes());
                std::ofstream meta(dir / (key + ".txt"));
                meta << "shader_hash=" << std::hex << info.pgm_hash << "\n"
                     << "spirv_fnv=" << fnv << "\n"
                     << "spv_size=" << std::dec << spv_bytes << "\n"
                     << "guest_bin_size=" << code.size_bytes() << "\n"
                     << "exact_mesa_spirv_match=" << exact << "\n";
            }
            LOG_INFO(Render_Vulkan, "GOW_COMPUTE_SHADER_CANDIDATE pgm_hash={:#x} "
                     "spv_fnv={:#x} spv_bytes={} guest_bytes={} mesa_exact={}",
                     info.pgm_hash, fnv, spv_bytes, code.size_bytes(), exact);
        }
    }
'''

def patch_source():
    process_text=PROCESS.read_text()
    needle="""s32 PS4_SYSV_ABI sceKernelLoadStartModule(const char* moduleFileName, u64 args, const void* argp,
                                          u32 flags, const void* pOpt, s32* pRes) {"""
    replacement="""// Preserve SysV stack alignment when guest code enters this host callback.
__attribute__((force_align_arg_pointer))
"""+needle
    if process_text.count(needle)!=1:die("process.cpp signature changed")
    pipeline_text=PIPELINE.read_text()
    anchor='    DumpShader(spv, info.pgm_hash, info.hw_stage, perm_idx, "spv");'
    if pipeline_text.count(anchor)!=1:die("Compute shader emission site changed")
    BACKUPS[str(PROCESS)]=PROCESS.read_bytes()
    BACKUPS[str(PIPELINE)]=PIPELINE.read_bytes()
    # Keep recoverable backup copies in the report, even if source restoration fails.
    (WORK/"process.cpp.original").write_bytes(BACKUPS[str(PROCESS)])
    (WORK/"vk_pipeline_cache.cpp.original").write_bytes(BACKUPS[str(PIPELINE)])
    PROCESS.write_text(process_text.replace(needle,replacement))
    PIPELINE.write_text(
        pipeline_text.replace('#include <ranges>',
                              '#include <ranges>\n#include <cstdlib>\n#include <cstdint>\n#include <fstream>\n#include <filesystem>')
        .replace(anchor,CAPTURE_CODE+"\n"+anchor))
    (WORK/"source-patches.diff").write_bytes(git("diff","--",
        "src/core/libraries/kernel/process.cpp",
        "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp").stdout)
    check=git("diff","--check")
    if check.returncode!=0:die("Injected patch contains whitespace errors")
    note("SOURCE_PATCH=TEMPORARY (two functions, restored after build)")

def collect_display():
    env=os.environ.copy()
    picks=[]
    for proc in glob.glob("/proc/[0-9]*/comm"):
        try:
            pid=int(proc.split("/")[2])
            if os.stat(proc).st_uid!=os.getuid():continue
            name=Path(proc).read_text().strip().lower()
            if "sunshine" not in name and "es-de" not in name:continue
            vals={}
            for entry in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
                if b"=" in entry:
                    k,v=entry.split(b"=",1)
                    vals[k.decode(errors="replace")]=v.decode(errors="surrogateescape")
            if vals.get("DISPLAY"):picks.append((0 if "es-de" in name else 1,pid,name,vals))
        except (OSError,ValueError,UnicodeError):pass
    picks.sort()
    if picks:
        _,pid,name,vals=picks[0]
        allow=re.compile(r"^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|"
                         r"XDG_DATA_HOME|XDG_SESSION_TYPE|DBUS_SESSION_BUS_ADDRESS|"
                         r"PATH|LD_LIBRARY_PATH|PULSE_SERVER|VK_.*|RADV_.*|"
                         r"MESA_.*|AMD_.*|SDL_.*)$")
        for k,v in vals.items():
            if allow.fullmatch(k):env[k]=v
        origin=f"{name} PID={pid}"
    else:
        origin="SSH_FALLBACK"
        if not env.get("DISPLAY") and Path("/tmp/.X11-unix/X0").exists():
            env["DISPLAY"]=":0"
        env.setdefault("XDG_RUNTIME_DIR",f"/run/user/{os.getuid()}")
    if not env.get("DISPLAY"):die("No X11 graphical session; Moonlight is not required")
    (WORK/"display-env.txt").write_text(f"SOURCE={origin}\nDISPLAY={env['DISPLAY']}\n")
    return env

def screenshot(path,env):
    ffmpeg=shutil.which("ffmpeg")
    if not ffmpeg:return False
    args=[ffmpeg,"-hide_banner","-nostdin","-loglevel","error",
          "-f","x11grab","-framerate","1","-i",env["DISPLAY"],"-frames:v","1",
          "-vf","scale=1152:-2","-q:v","6","-y",str(path)]
    try:
        proc=run(args,env=env,timeout=3)
        return proc.returncode==0 and path.is_file() and path.stat().st_size>1300
    except (OSError,subprocess.TimeoutExpired) as e:
        (WORK/"screenshots-errors.txt").open("a").write(repr(e)+"\n")
        return False

def pids():
    result={}
    for path in glob.glob("/proc/[0-9]*/comm"):
        try:
            pid=int(path.split("/")[2])
            name=Path(path).read_text().strip()
            if name.lower() in ("xorg","sunshine","shadps4","drrun"):
                result.setdefault(name,[]).append(pid)
        except (OSError,ValueError):pass
    return result

def gpu():
    result={}
    for d in Path("/sys/class/drm").glob("card*/device"):
        try:
            if (d/"vendor").read_text().strip().lower()!="0x1002":continue
            for field in ("gpu_busy_percent","mem_info_vram_used","mem_info_gtt_used"):
                try:result[field]=int((d/field).read_text())
                except (OSError,ValueError):pass
            break
        except OSError:pass
    return result

def stop_trial(proc):
    if not proc or proc.poll() is not None:return
    for sig,grace in ((signal.SIGINT,5),(signal.SIGTERM,4),(signal.SIGKILL,3)):
        if proc.poll() is not None:return
        try:
            if os.getpgid(proc.pid)!=proc.pid:return
            os.killpg(proc.pid,sig)
            proc.wait(timeout=grace)
        except ProcessLookupError:return
        except subprocess.TimeoutExpired:continue

def make_contact():
    try:
        from PIL import Image,ImageDraw
        photos=sorted((WORK/"screenshots").glob("*.jpg"))
        if not photos:return
        w,h,cols=420,262,3
        composite=Image.new("RGB",(cols*w,((len(photos)+cols-1)//cols)*h),(10,10,15))
        pen=ImageDraw.Draw(composite)
        for i,path in enumerate(photos):
            with Image.open(path) as opened:
                im=opened.convert("RGB")
                im.thumbnail((w-6,h-28))
                x=(i%cols)*w
                y=(i//cols)*h
                composite.paste(im,(x,y+24))
                pen.text((x+7,y+5),path.stem,fill="white")
        composite.save(WORK/"contact-sheet.jpg",quality=85)
    except (ImportError,OSError,ValueError) as e:
        (WORK/"contact-sheet-error.txt").write_text(str(e))

def replay_once(env):
    global TRIAL_PROCESS
    screenshot_dir=WORK/"screenshots"
    screenshot_dir.mkdir()
    capture_dir=WORK/"compute-shaders"
    capture_dir.mkdir()
    env=env.copy()
    env["SHADPS4_GOW_SHADER_TRACE_DIR"]=str(capture_dir)
    env["SHADPS4_CPU_ID_MODE"]="auto"
    env.pop("SHADPS4_CPU_ID_RESTART",None)
    # Match the environment used by the definitive Mesa pipeline dump.
    env["RADV_DEBUG"]="hang,noumr"
    log=WORK/"emulator-stdout.log"
    start=time.monotonic()
    screenshot_rows=[]
    shots_done=threading.Event()
    stop_shots=threading.Event()
    def worker():
        for second in SCREEN_TIMES:
            while not stop_shots.is_set() and time.monotonic()-start < second:
                time.sleep(0.08)
            if stop_shots.is_set():break
            name=f"{len(screenshot_rows):02d}_t{second:03d}.jpg"
            path=screenshot_dir/name
            ok=screenshot(path,env)
            screenshot_rows.append({"planned_s":second,
                                    "actual_s":round(time.monotonic()-start,2),
                                    "captured":ok,"file":name if ok else None})
            note(f"PHOTO t={second:02d}s {'OK' if ok else 'UNAVAILABLE'}")
        shots_done.set()
    with log.open("wb") as stdout:
        TRIAL_PROCESS=subprocess.Popen(
            [str(TRIAL/"shadps4"),"--cpu-id-mode","auto","--game","CUSA34384",
             "--fullscreen","true"],env=env,stdout=stdout,
            stderr=subprocess.STDOUT,start_new_session=True)
        threading.Thread(target=worker,daemon=True).start()
        rows=[]
        last=None
        t_next=0
        xorg_initial=pids().get("Xorg",[])
        while True:
            elapsed=time.monotonic()-start
            if elapsed>=t_next:
                current=pids()
                rows.append({"elapsed_s":round(elapsed,2),"gpu":gpu(),
                             "pids":current,"trial_exit":TRIAL_PROCESS.poll()})
                if last is not None and last.get("Xorg",[])!=current.get("Xorg",[]):
                    note("XORG_PID_CHANGED: display recovery detected")
                last=current
                t_next=elapsed+1
            if TRIAL_PROCESS.poll() is not None:break
            if elapsed>=60:break
            time.sleep(0.14)
        if TRIAL_PROCESS.poll() is None:stop_trial(TRIAL_PROCESS)
        TRIAL_PROCESS.wait()
        rc=TRIAL_PROCESS.returncode
        TRIAL_PROCESS=None
        stop_shots.set()
        time.sleep(0.4)
    make_contact()
    (WORK/"screenshot-timeline.json").write_text(json.dumps(screenshot_rows,indent=2))
    (WORK/"gpu-process-timeline.json").write_text(json.dumps(rows,indent=2))
    (WORK/"trial.json").write_text(json.dumps({
        "exit_code":rc,"elapsed_s":round(time.monotonic()-start,2),
        "radv_debug":"hang,noumr",
        "xorg_pid_initial":xorg_initial,
        "xorg_pid_final":pids().get("Xorg",[]),
        "screenshots":len([x for x in screenshot_rows if x["captured"]]),
        "shader_dumps":len(list(capture_dir.glob("*.spv"))),
    },indent=2))
    # Leave detailed shader trace and GPU-hang report, but limit repetitive logs.
    with log.open("rb") as f:
        beginning=f.read(350_000)
        f.seek(max(0,log.stat().st_size-1_500_000))
        ending=f.read(1_500_000)
    (WORK/"emulator-start.log").write_bytes(beginning)
    (WORK/"emulator-end.log").write_bytes(ending)
    log.unlink()
    note(f"TRIAL_EXIT={rc}; CAPTURED_SHADER_SPV={len(list(capture_dir.glob('*.spv')))}")

def snapshot_logs():
    for dirname,command in (
        ("kernel-gpu.txt",["journalctl","-k","--no-pager","--since","-8 minutes"]),
        ("system-watch.txt",["journalctl","--no-pager","--since","-8 minutes"]),
    ):
        try:
            proc=run(command,timeout=12)
            lines=proc.stdout.decode(errors="replace").splitlines()
            filtered=[x for x in lines if re.search(
                r"amdgpu|gpu|radv|shadps4|sunshine|xorg|reset|hang|recovery|segfault",x,re.I)]
            (WORK/dirname).write_text("\n".join(filtered[-700:]))
        except (OSError,subprocess.TimeoutExpired) as e:
            (WORK/(dirname+".error")).write_text(str(e))

def enumerate_shader():
    found=[]
    for f in (WORK/"compute-shaders").glob("*.spv"):
        h=0xcbf29ce484222325
        data=f.read_bytes()
        for byte in data:h=((h^byte)*0x100000001b3)&((1<<64)-1)
        found.append({"name":f.name,"sha256":hashlib.sha256(data).hexdigest(),
                      "fnv64":hex(h),"bytes":len(data),
                      "matches_mesa_hung_compute":
                      len(data)==MESA_SPV_SIZE and h==MESA_SPV_FNV})
    (WORK/"shadps4-to-radv-shader-matches.json").write_text(json.dumps(found,indent=2))
    return found

def main():
    global RESULT,PHASE
    note("=== GOD OF WAR: MATCH HUNG RADV COMPUTE SPIR-V TO GUEST SHADER ===")
    note("One unattended trial; NO Moonlight, no ES-DE changes, no driver changes.")
    PHASE="verify exact integrated source and trial environment"
    for command in ("cmake","git","ffmpeg","sha256sum"):
        if not shutil.which(command):die("Required command missing: "+command)
    if not SRC.is_dir() or not BLD.is_dir() or not PROCESS.is_file() or not PIPELINE.is_file():
        die("Verified integrated source/build path missing")
    if not LIVE.is_file() or sha(LIVE)!=EXPECTED_LIVE:
        die("Installed ES-DE emulator differs from protected baseline")
    if verified_head()!=EXPECTED_HEAD:die("Source revision changed; refusing old patch")
    if blob(PROCESS)!=EXPECTED_PROCESS:die("process.cpp differs from verified source")
    if git("status","--porcelain","--untracked-files=no").stdout.strip():
        die("Source has uncommitted changes; preserving them")
    if pids().get("shadps4") or pids().get("drrun"):
        die("Another emulator is currently running")
    # Don't mutate the source tree while another build is using it.
    build_check=run(["ps","-eo","args="],timeout=8)
    if build_check.returncode==0:
        active_builds=[line for line in build_check.stdout.decode(errors="replace").splitlines()
                       if (("cmake --build" in line or "ninja " in line or "make -j" in line)
                           and not ("ps -eo" in line or "diagnose-gow-hung-compute-shader" in line))]
        if active_builds:
            die("Concurrent build detected; retry after other builds finish")
    for file in ("bin64/drrun","libshadps4_cpu_id.so","lib64/release/libdynamorio.so"):
        if not (BLD/"cpu-id-runtime"/file).is_file():die("Bundled CPU-ID runtime missing")
    env=collect_display()
    pre=WORK/"preflight-screen.jpg"
    if not screenshot(pre,env):die("X11 screenshot preflight failed; no game launched")
    PHASE="temporarily instrument compute SPIR-V and retain proven stack realignment"
    patch_source()
    try:
        PHASE="incremental targeted build of isolated shadPS4 test binary"
        built=run(["cmake","--build",str(BLD),"--target","shadps4","--parallel","5"],
                  logfile=WORK/"build.log",timeout=900)
        if built.returncode!=0:die("Build failed; inspect build.log")
        executable=BLD/"shadps4"
        if not executable.is_file():die("New binary missing after build")
        TRIAL.mkdir(parents=True,exist_ok=False)
        shutil.copy2(executable,TRIAL/"shadps4")
        shutil.copytree(BLD/"cpu-id-runtime",TRIAL/"cpu-id-runtime",
                        symlinks=True)
    finally:
        restore_source()
    PHASE="verify staged candidate and source restored before launching"
    if git("status","--porcelain","--untracked-files=no").stdout.strip():
        die("Original source was not restored. Do not start the game")
    if sha(TRIAL/"shadps4")==EXPECTED_LIVE:
        die("New binary is identical to production; instrumentation not built")
    smoke=run([str(TRIAL/"shadps4"),"--help"],timeout=20,logfile=WORK/"smoke.log")
    if smoke.returncode!=0:die("Staged binary CLI smoke failed")
    PHASE="one unattended RADV run with matching shader dump and screenshots"
    replay_once(env)
    PHASE="match Mesa compute shader hash and archive relevant telemetry"
    matches=enumerate_shader()
    snapshot_logs()
    (WORK/"mesahung-compute-signature.json").write_text(json.dumps({
        "pipeline_hash":"94e43a67e5dc6cff",
        "mesa_spv_bytes":MESA_SPV_SIZE,
        "mesa_spv_fnv64":hex(MESA_SPV_FNV),
        "mesa_spv_sha256":"86a1353e02db48eab8b0c29414ca0449fd0e3828207bb969c45201bedc1a82be",
        "matched_shader_count":sum(x["matches_mesa_hung_compute"] for x in matches),
        "total_candidate_shaders":len(matches),
        "shader_source":"RADV hang capture 2026-10-09 21:11:42",
    },indent=2))
    PHASE="verify production ES-DE executable remains unchanged"
    if sha(LIVE)!=EXPECTED_LIVE:die("Installed executable checksum changed unexpectedly")
    RESULT="SHADER_MATCHED" if any(x["matches_mesa_hung_compute"] for x in matches) else (
        "TRACE_COMPLETE_NO_EXACT_SPV_MATCH")
    note("SHADER_MATCHED="+str(RESULT=="SHADER_MATCHED"))
    note("No menu/intro claimed without screenshot review.")

try:
    main()
except KeyboardInterrupt:
    RESULT="INTERRUPTED_WITH_REPORT"
    if TRIAL_PROCESS is not None:stop_trial(TRIAL_PROCESS)
    (WORK/"error.txt").write_text("Operator interrupted; trial process group stopped.\n")
except BaseException:
    RESULT="SAFE_STOP_WITH_REPORT"
    if TRIAL_PROCESS is not None:stop_trial(TRIAL_PROCESS)
    (WORK/"error.txt").write_text(traceback.format_exc())
finally:
    if BACKUPS:restore_source()
    (WORK/"manifest.json").write_text(json.dumps({
        "result":RESULT,"phase":PHASE,
        "production_sha256":sha(LIVE) if LIVE.is_file() else None,
        "production_expected_sha256":EXPECTED_LIVE,
        "production_binary_unchanged": LIVE.is_file() and sha(LIVE)==EXPECTED_LIVE,
        "intro_menu_confirmed":False,"moonlight_needed":False,
        "trial":str(TRIAL/"shadps4"),
        "source_tree_clean":git("status","--porcelain","--untracked-files=no").stdout.strip()==b"" if SRC.is_dir() else None,
    },indent=2))
    with tarfile.open(REPORT,"w:gz",compresslevel=6) as tar:
        for file in sorted(WORK.iterdir()):
            tar.add(file,arcname=file.name)
    note("RESULT="+RESULT)
    note("REPORT="+str(REPORT))
    note("MOONLIGHT=NOT_REQUIRED_UNTIL_VISUAL_INTRO_OR_MENU")
    note("SSH_SESSION_AND_INSTALLED_ESDE=UNCHANGED")
    shutil.rmtree(WORK,ignore_errors=True)
sys.exit(0 if RESULT in ("SHADER_MATCHED","TRACE_COMPLETE_NO_EXACT_SPV_MATCH")
         else 1)
