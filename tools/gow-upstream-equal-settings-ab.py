#!/usr/bin/env python3
"""Read-only-source A/B rendering reproduction with IDENTICAL user settings.

Compare an SHA-256-verified previously rendering shadPS4 binary with the
current fresh-upstream proof binary. Both are launched through the same pinned
input/screenshot helper, using the same untouched global/per-game config.
No DMA toggle, readback override, game-specific emulator fix, source edit,
rebuild, driver setting, installed-launcher change, or manual workaround.
A normal game run may create ordinary in-game autosaves.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import traceback

HOME=Path.home()
PROJECT=HOME/"projects/shadps4/GodOfWarRagnarok"
LOGROOT=HOME/"projects/shadps4/logs"
WORK=PROJECT/"tests/main-proof-20261015-151529-3107006"
GOOD=PROJECT/"tests/gow91-build-20261015-125214-1308309/shadps4"
NEW=WORK/"build/shadps4"
GOOD_SHA="7ee847328072f9d65772fc85ee19f93143cbaaafe69e381d70379479ddd81a30"
NEW_SHA="c874d2038e00e5dd1c4158b1ce1ee3ddb638335f220eed297275b19908eddf69"
STAGE3=PROJECT/"scripts/gow-main-proof-stage3.py"
STAGE3_BLOB="f64e16d3f5fe70337b43f187f3802f9c0ae3dc81"
STAMP=dt.datetime.now().strftime("%Y%m%d-%H%M%S")
OUT=LOGROOT/f"gow-equal-settings-ab-{STAMP}-{os.getpid()}"
SERIAL="CUSA34384"
PROFILE=HOME/".local/share/shadPS4"

def say(s):
    print("GOW_AB "+str(s),flush=True)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1048576),b""):h.update(block)
    return h.hexdigest()

def git_blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def config_state():
    state={}
    for p in (PROFILE/"config.json",PROFILE/"custom_configs"/f"{SERIAL}.json"):
        if p.is_symlink():
            raise RuntimeError("refusing a symlinked configuration path")
        if p.exists() and (not p.is_file() or p.stat().st_size>5000000):
            raise RuntimeError("unsafe configuration path")
        state[str(p)] = file_sha(p) if p.exists() else None
    return state

def require_binary(path,expected):
    if path.is_symlink() or not path.is_file() or not os.access(path,os.X_OK):
        raise RuntimeError("comparison candidate unavailable: "+str(path))
    if file_sha(path)!=expected:
        raise RuntimeError("candidate fingerprint changed: "+str(path))
    runtime=path.parent/"cpu-id-runtime"
    if not runtime.is_dir():
        other=path.parent/"../libexec/shadps4/cpu-id-runtime"
        if not other.is_dir():raise RuntimeError("CPU-ID runtime missing: "+str(path))
    return {"path":str(path),"sha256":expected}

def load_capture_script():
    if not STAGE3.is_file() or STAGE3.is_symlink() or git_blob(STAGE3.read_bytes())!=STAGE3_BLOB:
        raise RuntimeError("pinned unchanged-config launcher differs from Stage 3")
    spec=importlib.util.spec_from_file_location("ab_verified_stage3",STAGE3)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load known capture runner")
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    old=mod.load_base()
    capture=mod.normal_config_capture_script(old)
    source=capture.read_text()
    if "patch_config(CFG, args.readbacks_mode)" in source:
        raise RuntimeError("capture script would modify user GPU settings")
    if "Leave the actual per-game configuration completely unchanged" not in source:
        raise RuntimeError("capture script lacks original-config contract")
    result=subprocess.run([sys.executable,capture,"--self-test"],
                          capture_output=True,text=True,timeout=80)
    if result.returncode:
        raise RuntimeError("capture helper self-test failed: "+result.stderr[-1000:])
    return capture

def screenshot_fraction(image_data):
    ffmpeg=shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        proc=subprocess.run(
            [ffmpeg,"-hide_banner","-loglevel","error",
             "-i","pipe:0","-vf","scale=160:90:flags=area,format=gray",
             "-frames:v","1","-pix_fmt","gray","-f","rawvideo","pipe:1"],
            input=image_data,capture_output=True,timeout=12)
        if proc.returncode or len(proc.stdout)!=160*90:
            return None
        # White pointer fragments cannot pass the 8% visibly-rendered gate.
        return round(sum(v>32 for v in proc.stdout)/(160*90),6)
    except (OSError,subprocess.TimeoutExpired):
        return None

def read_capture(archive):
    if archive.is_symlink() or not archive.is_file() or archive.parent.resolve()!=LOGROOT.resolve():
        raise RuntimeError("unsafe or missing capture archive")
    with tarfile.open(archive,"r:gz") as t:
        members=t.getmembers()
        if len(members)>1500:
            raise RuntimeError("unexpectedly large capture archive")
        meta=[m for m in members if m.isfile() and m.name.endswith("/result.json")]
        console=[m for m in members if m.isfile() and m.name.endswith("/console.log")]
        if len(meta)!=1 or meta[0].size>100000 or len(console)!=1 or console[0].size>100000000:
            raise RuntimeError("malformed gameplay capture")
        data=json.load(t.extractfile(meta[0]))
        log=t.extractfile(console[0]).read().decode(errors="replace")
        log=re.sub(r"\x1b\[[0-9;]*m","",log)
        images=[]
        for m in members:
            if not m.isfile() or not m.name.lower().endswith((".jpg",".jpeg",".png")):
                continue
            if m.size>12000000:
                raise RuntimeError("screenshot too large")
            frame=t.extractfile(m).read()
            images.append({"filename":m.name.rsplit("/",1)[-1],
                           "sha256":sha(frame),
                           "visible_fraction":screenshot_fraction(frame)})
        valid=[r["visible_fraction"] for r in images if r["visible_fraction"] is not None]
        result={
            "guest_exit":data.get("returncode"),
            "runner_failure":data.get("failure"),
            "cycles":data.get("cycles"),
            "cross_presses":data.get("cross_presses"),
            "screenshots":images,
            "max_visible_fraction":max(valid) if valid else None,
            "visual_measurements_available":bool(valid),
            "radv_device_lost":("ErrorDeviceLost" in log or
                               "context is lost" in log),
            "srt_failures":log.count("Failed to compute offset for SRT walker"),
            "first_gpu_flags":[x.strip() for x in log.splitlines()
                               if any(n in x for n in
                                      ("GPU directMemoryAccess:","GPU readbacksMode:",
                                       "Vulkan PipelineCacheEnabled:"))][:3],
            "console_bytes":len(log),
        }
        return result

def collect_kernel_snapshot(label,then):
    if not shutil.which("journalctl"):
        return
    try:
        r=subprocess.run(["journalctl","-k","--since",then,
                          "--no-pager","-n","250"],
                         capture_output=True,text=True,timeout=25)
        (OUT/(label+"-kernel.log")).write_text(
            r.stdout[-45000:] if r.returncode==0 else
            "kernel journal unavailable: "+r.stderr[-500:])
    except (OSError,subprocess.TimeoutExpired) as e:
        (OUT/(label+"-kernel.log")).write_text(repr(e)+"\n")

def launch(label,binary,binary_sha,capture_script):
    log=OUT/(label+"-capture-runner.log")
    before=config_state()
    started=dt.datetime.now().isoformat()
    say("RUN="+label+" original config, binary="+binary_sha[:16])
    args=[sys.executable,str(capture_script),
          "--binary",str(binary),"--sha256",binary_sha,
          "--seconds","140","--interval","10","--after-delay","0.5"]
    with log.open("wb") as f:
        try:
            rc=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=f,
                              stderr=subprocess.STDOUT,timeout=260).returncode
        except subprocess.TimeoutExpired:
            raise RuntimeError("bounded "+label+" capture timed out")
    if config_state()!=before:
        raise RuntimeError("user config changed during "+label)
    collect_kernel_snapshot(label,started)
    candidate=None
    for line in log.read_text(errors="replace").splitlines():
        if line.startswith("UPLOAD="):
            path=Path(line[7:].strip())
            if (path.is_file() and not path.is_symlink() and
                    path.parent.resolve()==LOGROOT.resolve() and
                    path.name.startswith("gow-auto-gameplay-")):
                candidate=path
    if candidate is None:
        raise RuntimeError("no bounded capture archive from "+label)
    result=read_capture(candidate)
    result.update({"label":label,"binary_sha256":binary_sha,
                   "runner_exit":rc,"config_hashes_unchanged":True,
                   "capture_archive":str(candidate)})
    say(label+" visible_fraction="+str(result["max_visible_fraction"])+
        " guest_exit="+str(result["guest_exit"])+
        " device_lost="+str(result["radv_device_lost"]))
    return result

def save(outcome):
    (OUT/"result.json").write_text(json.dumps(outcome,indent=2,sort_keys=True)+"\n")
    tarpath=LOGROOT/(OUT.name+".tar.gz")
    with tarfile.open(tarpath,"w:gz",compresslevel=3) as tar:
        for file in sorted(OUT.rglob("*")):
            if file.is_file() and not file.is_symlink():
                tar.add(file,arcname=OUT.name+"/"+str(file.relative_to(OUT)))
        for case in outcome.get("runs",[]):
            capture=case.get("capture_archive")
            if capture:
                c=Path(capture)
                if c.is_file() and not c.is_symlink() and c.parent.resolve()==LOGROOT.resolve():
                    tar.add(c,arcname=OUT.name+"/"+case["label"]+"-capture.tar.gz")
    say("UPLOAD="+str(tarpath))

def main():
    LOGROOT.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={
        "status":"STARTED","method":"identical-config old/new emulator replay",
        "upstream_candidate_sha256":NEW_SHA,"proven_previous_sha256":GOOD_SHA,
        "source_mutated":False,"binary_mutated":False,
        "game_configuration_changed_by_runner":False,
        "DMA_or_readback_override":False,
        "old_binary_not_recommended_for_installation":True,
        "cross_title_verified":False,"rendering_verified":False,
        "runs":[],
    }
    before=None
    try:
        result["original_bin"]=require_binary(GOOD,GOOD_SHA)
        result["fresh_upstream_bin"]=require_binary(NEW,NEW_SHA)
        if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                          capture_output=True).returncode==0:
            raise RuntimeError("another shadps4 is running; not disrupting it")
        before=config_state()
        result["original_config_sha256"]=before
        capture_script=load_capture_script()
        first=launch("previous-rendered",GOOD,GOOD_SHA,capture_script)
        result["runs"].append(first)
        fraction=first["max_visible_fraction"]
        if (fraction is None or fraction<0.08 or first["radv_device_lost"]):
            result["status"]="OLD_RENDERING_NOT_REPRODUCED_WITH_UNCHANGED_SETTINGS"
            say("STOP: old known binary does not render stably with current settings; not risking second GPU loss")
            return 1
        result["old_rendering_reproduced"]=True
        if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                          capture_output=True).returncode==0:
            raise RuntimeError("previous emulator process still running; refusing second launch")
        second=launch("fresh-upstream",NEW,NEW_SHA,capture_script)
        result["runs"].append(second)
        result["status"]="COMPARISON_CAPTURED_REQUIRES_SOURCE_CORRECTION"
        return 0
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if before is not None:
            now=config_state()
            result["configs_unchanged_after_both"]=now==before
            if now!=before:result["status"]="FAILED_CONFIG_CHANGED"
        save(result)

if __name__=="__main__":
    raise SystemExit(main())
