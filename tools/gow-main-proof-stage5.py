#!/usr/bin/env python3
"""Stage 5: restore a previously tested generic guest-to-host ABI correction.

Keep the exact 73/73 GCN-tested upstream build and all its graphics changes.
Only realign the native x86-64 stack at sceKernelLoadStartModule. Compare
startup to the prior no-alignment baseline without changing guest settings,
saves, the installed emulator, GPU drivers, or other emulator behavior.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import traceback

HOME = Path.home()
ROOT = HOME / "projects/shadps4/GodOfWarRagnarok"
WORK = ROOT / "tests/main-proof-20261015-151529-3107006"
SRC = WORK / "source"
BUILD = WORK / "build"
LOGS = HOME / "projects/shadps4/logs"
OUT = LOGS / ("gow-upstream-stage5-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
              + f"-{os.getpid()}")
RUNNER = ROOT / "scripts/gow-main-proof-stage3.py"
RUNNER_BLOB = "f64e16d3f5fe70337b43f187f3802f9c0ae3dc81"
UPSTREAM = "b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
CURRENT_SOURCE_DIFF_SHA = "b0044088565024240e2827c1d7d77c5bdaf8475e74ee5a2293936fa706cad057"
CURRENT_BINARY_SHA = "68a1e91cad00745fa0f9dd4cf2c9962c3e35c77f4c69cc459ee8f9a21a9a0d4e"
PROCESS = "src/core/libraries/kernel/process.cpp"
OLD_PROCESS_BLOB = "05acbce16dc315c5abaccb5190011ceed6ad848a"
UPSTREAM_PROCESS_BLOB = "276318a89f2f7e9aad4c883854cdf98dcec0681f"
FIX_PROCESS_BLOB = "3b4ab0d50798f47cbac240ed10e22acc54ae9f8b"
FIX_COMMIT = "c76e11c0fa4e60338acc427457599fad8e0d4863"
MANIFEST = WORK / ".generic-stack-alignment-stage5.json"
ANCHOR = "s32 PS4_SYSV_ABI sceKernelLoadStartModule("
DECORATION = (
    "// Guest modules can enter HLE with a non-native stack alignment. Realign the\n"
    "// native x86-64 stack before calling C++ code and host libraries.\n"
    "#if defined(__x86_64__) && (defined(__GNUC__) || defined(__clang__))\n"
    "__attribute__((force_align_arg_pointer))\n"
    "#endif\n"
)

def note(value):
    print("GOW_STAGE5 " + str(value), flush=True)

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for part in iter(lambda: f.read(1048576), b""):
            h.update(part)
    return h.hexdigest()

def git_blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def run(command, *, cwd=None, log=None, timeout=120, check=True):
    args = [str(x) for x in command]
    if args and args[0] == "git":
        args = ["git", "-c", "http.version=HTTP/1.1", *args[1:]]
    if log:
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("wb") as out:
            proc = subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL,
                                  stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
            result = proc.returncode, ""
        if check and result[0]:
            raise RuntimeError(f"{args[:5]} failed rc={result[0]}\n"+
                               log.read_text(errors="replace")[-3500:])
        return result
    proc = subprocess.run(args, cwd=cwd, stdin=subprocess.DEVNULL,
                          text=True, capture_output=True, timeout=timeout)
    if check and proc.returncode:
        raise RuntimeError(f"{args[:5]} failed rc={proc.returncode}\n"+
                           proc.stdout[-1000:]+proc.stderr[-2200:])
    return proc.returncode, proc.stdout

def write_json(path, value):
    path = Path(path)
    temp = path.with_name("."+path.name+".tmp")
    try:
        temp.write_text(json.dumps(value, indent=2, sort_keys=True)+"\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def source_diff_sha():
    diff = run(["git", "diff", "--binary", UPSTREAM, "--",
                "src", "CMakeLists.txt", "cmake"], cwd=SRC)[1]
    return hashlib.sha256(diff.encode()).hexdigest()

def configs():
    out={}
    for p in (HOME/".local/share/shadPS4/config.json",
              HOME/".local/share/shadPS4/custom_configs/CUSA34384.json"):
        if p.is_symlink():
            raise RuntimeError("refusing symlinked user configuration")
        if p.exists() and (not p.is_file() or p.stat().st_size>5000000):
            raise RuntimeError("unsafe user configuration: "+str(p))
        out[str(p)] = sha256(p) if p.exists() else None
    return out

def load_preserved_runner():
    if RUNNER.is_symlink() or not RUNNER.is_file() or git_blob(RUNNER.read_bytes()) != RUNNER_BLOB:
        raise RuntimeError("previously executed Stage 3 runner changed")
    spec = importlib.util.spec_from_file_location("verified_stage3", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load pinned Stage 3 runner")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner

def check_source():
    if WORK.is_symlink() or SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("preserved upstream source checkout missing")
    if not (BUILD/"shadps4").is_file() or (BUILD/"shadps4").is_symlink():
        raise RuntimeError("preserved candidate executable unavailable")
    ps = run(["git", "rev-list", "--parents", "-n", "1", "HEAD"], cwd=SRC)[1].split()
    if len(ps)!=3 or ps[1]!=UPSTREAM or ps[2]!="966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe":
        raise RuntimeError("source ancestry changed; refusing to update another build")
    if run(["git", "ls-files", "-u"], cwd=SRC)[1].strip():
        raise RuntimeError("source has unresolved merge conflicts")
    if MANIFEST.exists():
        if MANIFEST.is_symlink() or not MANIFEST.is_file():
            raise RuntimeError("unsafe existing Stage 5 provenance")
    else:
        if source_diff_sha()!=CURRENT_SOURCE_DIFF_SHA or sha256(BUILD/"shadps4")!=CURRENT_BINARY_SHA:
            raise RuntimeError("Stage 4 source/binary no longer match the exact 73/73 baseline")

def apply_previous_general_fix():
    original = SRC/PROCESS
    if original.is_symlink() or not original.is_file():
        raise RuntimeError("kernel HLE source missing")
    old = original.read_bytes()
    if MANIFEST.exists():
        evidence=json.loads(MANIFEST.read_text())
        if evidence.get("topic_commit")!=FIX_COMMIT or evidence.get("baseline_diff")!=CURRENT_SOURCE_DIFF_SHA:
            raise RuntimeError("kernel ABI fix manifest invalid")
        if git_blob(old)!=evidence["after_blob"] or source_diff_sha()!=evidence["after_diff"]:
            raise RuntimeError("applied HLE fix changed since first run")
        note("KERNEL_HLE_ABI=previous general fix verified")
        return evidence

    if git_blob(old)!=OLD_PROCESS_BLOB or source_diff_sha()!=CURRENT_SOURCE_DIFF_SHA:
        raise RuntimeError("original HLE source is not the unchanged generic stack")
    base_file = run(["git", "show", UPSTREAM+":"+PROCESS], cwd=SRC)[1].encode()
    if git_blob(base_file)!=UPSTREAM_PROCESS_BLOB:
        raise RuntimeError("verified upstream HLE file not available")
    if base_file.count(ANCHOR.encode())!=1:
        raise RuntimeError("upstream module-load function is ambiguous")
    pinned = run(["curl", "-4fsSL", "--retry", "3", "--connect-timeout", "20",
                  "--max-time", "120",
                  "https://raw.githubusercontent.com/Chreece/shadPS4/"+
                  FIX_COMMIT+"/"+PROCESS], timeout=360)[1].encode()
    if git_blob(pinned)!=FIX_PROCESS_BLOB or (
            pinned!=base_file.replace(ANCHOR.encode(),
                                      (DECORATION+ANCHOR).encode(),1)):
        raise RuntimeError("published generic ABI patch has unexpected modifications")
    if old.count(ANCHOR.encode())!=1 or DECORATION.encode() in old:
        raise RuntimeError("ABI source already modified without tracked proof")
    newer=old.replace(ANCHOR.encode(), (DECORATION+ANCHOR).encode(),1)
    if newer.replace((DECORATION+ANCHOR).encode(), ANCHOR.encode(),1)!=old:
        raise RuntimeError("kernel ABI patch cannot be independently reversed")
    original.write_bytes(newer)
    run(["git", "diff", "--check"], cwd=SRC)
    evidence={
        "topic_commit":FIX_COMMIT, "baseline_diff":CURRENT_SOURCE_DIFF_SHA,
        "before_blob":OLD_PROCESS_BLOB, "after_blob":git_blob(newer),
        "before_sha256":hashlib.sha256(old).hexdigest(),
        "after_sha256":hashlib.sha256(newer).hexdigest(),
        "after_diff":source_diff_sha(),
        "changed_file":PROCESS, "only_native_stack_alignment_changed":True,
        "shader_graphics_and_guest_code_unchanged":True,
        "game_specific_behavior":False
    }
    write_json(MANIFEST,evidence)
    note("KERNEL_HLE_ABI=restored earlier tested alignment fix")
    return evidence

def capture_with_unchanged_settings(runner, result):
    stage2 = runner.load_base()
    auto = runner.normal_config_capture_script(stage2)
    if "patch_config(CFG, args.readbacks_mode)" in auto.read_text():
        raise RuntimeError("diagnostic runner would modify user settings")
    run([sys.executable, "-m", "py_compile", auto], timeout=45)
    run([sys.executable, auto, "--self-test"], log=OUT/"auto-selftest.log", timeout=90)
    binary=BUILD/"shadps4"
    note("GAMEPLAY=normal user configuration, Cross input, screenshots")
    rc=run([sys.executable, auto, "--binary", binary,
            "--sha256", sha256(binary), "--seconds", "300",
            "--interval", "10", "--after-delay", "0.5"],
           log=OUT/"gameplay.log", timeout=540, check=False)[0]
    result["automation_exit"]=rc
    capture=None
    for line in (OUT/"gameplay.log").read_text(errors="replace").splitlines():
        if line.startswith("UPLOAD="):
            target=Path(line[7:].strip())
            if target.is_file() and not target.is_symlink() and (
                    target.parent.resolve()==LOGS.resolve()) and (
                    target.name.startswith("gow-auto-gameplay-")):
                capture=target
    if capture:
        with tarfile.open(capture,"r:gz") as archive:
            members=archive.getmembers()
            records=[m for m in members if m.isfile() and m.name.endswith("/result.json")]
            if len(records)!=1 or records[0].size>100000:
                raise RuntimeError("unexpected gameplay result archive")
            summary=json.load(archive.extractfile(records[0]))
            result.update({
                "guest_returncode":summary.get("returncode"),
                "cross_presses":summary.get("cross_presses"),
                "capture_cycles":summary.get("cycles"),
                "capture_error":summary.get("failure"),
                "screenshot_count":sum(
                    m.isfile() and m.name.lower().endswith((".jpg",".jpeg",".png"))
                    for m in members)
            })
    else:
        result.update({"archive_missing":True,"screenshot_count":0})
    return capture

def package(result, capture=None):
    write_json(OUT/"result.json", result)
    tarpath=LOGS/(OUT.name+".tar.gz")
    with tarfile.open(tarpath,"w:gz",compresslevel=3) as archive:
        for p in sorted(OUT.rglob("*")):
            if p.is_file() and not p.is_symlink():
                archive.add(p,arcname=OUT.name+"/"+str(p.relative_to(OUT)))
        if capture is not None and capture.is_file() and not capture.is_symlink():
            archive.add(capture, arcname=OUT.name+"/gameplay-capture.tar.gz")
    note("UPLOAD="+str(tarpath))

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700)
    result={"phase":"PREFLIGHT","status":"STARTED","upstream_main":UPSTREAM,
            "baseline_binary_sha256":CURRENT_BINARY_SHA,
            "previous_gcn_73_pass":True,
            "guest_code_or_graphics_overrides":False,
            "installed_emulator_modified":False,
            "rendered_gameplay_verified":False,
            "cross_title_verified":False}
    capture=None
    before=None
    try:
        check_source()
        runner=load_preserved_runner()
        before=configs()
        result["previous_user_configs_sha256"]=before
        result["patch_proof"]=apply_previous_general_fix()
        result["phase"]="BUILD"
        note("BUILD=incremental native ABI source and relink only")
        run(["cmake", "--build", BUILD, "--target", "shadps4", "--parallel", "4"],
            log=OUT/"build.log", timeout=7200)
        result["new_binary_sha256"]=sha256(BUILD/"shadps4")
        if result["new_binary_sha256"]==CURRENT_BINARY_SHA:
            raise RuntimeError("emulator executable did not relink with restored native ABI")
        run(["ldd", BUILD/"shadps4"], log=OUT/"ldd.log", timeout=60)
        if "not found" in (OUT/"ldd.log").read_text(errors="replace"):
            raise RuntimeError("rebuilt executable has unresolved shared libraries")
        run(["git", "diff", "--binary", "--", PROCESS], cwd=SRC,
            log=OUT/"kernel-hle-patch.diff", timeout=90)
        result["phase"]="GAMEPLAY"
        capture=capture_with_unchanged_settings(runner,result)
        result["status"]=("STARTUP_PASSED_CAPTURE_REQUIRES_VISUAL_REVIEW"
                          if result.get("screenshot_count",0)>2 and
                             result.get("guest_returncode") not in (-6,-11)
                          else "STARTUP_NOT_YET_PROVEN")
        return 0 if result["status"].startswith("STARTUP_PASSED") else 1
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if before is not None:
            after=configs()
            result["user_configs_unchanged"]=before==after
            if before!=after:
                result["status"]="FAILED_CONFIG_MUTATION"
        package(result,capture)

if __name__=="__main__":
    raise SystemExit(main())
