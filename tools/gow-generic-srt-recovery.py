#!/usr/bin/env python3
"""Recovery: apply ONLY proven general SRT arithmetic semantics to upstream proof.

The exact GoW108 renderer and current upstream proof binaries are verified before
any source write. Saves, per-game/global settings, drivers, and installed emulator
are never modified. The old proof executable is preserved byte-for-byte.
"""
from __future__ import annotations
import datetime as dt
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import traceback

HOME = Path.home()
ROOT = HOME/"projects/shadps4/GodOfWarRagnarok"
WORK = ROOT/"tests/main-proof-20261015-151529-3107006"
SRC = WORK/"source"
BUILD = WORK/"build"
GCN_BUILD = WORK/"build-gcn-tests"
LOGROOT = HOME/"projects/shadps4/logs"
STAMP = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = LOGROOT/f"gow-generic-srt-recovery-{STAMP}-{os.getpid()}"
TOPIC = "9165f7e9bbab0d3fb846c980f63dcdfa272d4f53"
GOOD = ROOT/"tests/gow91-build-20261015-125214-1308309/shadps4"
GOOD_SHA = "7ee847328072f9d65772fc85ee19f93143cbaaafe69e381d70379479ddd81a30"
BINARY_SHA = "5e9ea0381be1db8c1d98f944e7ec2e6a0842a9af194056f25a99773fc659c272"
SOURCE_SHA = "c077947c7b8b28a0a9af49c175dc9e573dc2ca7b792dc492cee805785f0b3e77"
RELPATH = "src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
HEADER = "src/shader_recompiler/ir/passes/srt_offset_codegen.h"
TEST = "tests/srt/test_srt_offset_codegen.cpp"
PINS = {
    HEADER: "b92c1b56ed80ffa4b86cfbb2bcf4a8b8be76376c",
    TEST: "00fded003c4f0ec076f06a8d36e6187c9d59e560",
}
STAGE3 = ROOT/"scripts/gow-main-proof-stage3.py"
STAGE3_BLOB = "f64e16d3f5fe70337b43f187f3802f9c0ae3dc81"
SNAPSHOT = WORK/"srt-recovery-checkpoint"
MANIFEST = WORK/".generic-srt-recovery.json"

def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()

def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()

def blob(raw):
    return hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()

def note(message):
    print("SRT_RECOVERY "+str(message),flush=True)

def command(args, *, cwd=None, log=None, timeout=720, check=True):
    items=[str(v) for v in args]
    if items[0]=="git":
        items=["git","-c","http.version=HTTP/1.1",*items[1:]]
    if log is None:
        q=subprocess.run(items,cwd=cwd,stdin=subprocess.DEVNULL,
                         text=True,capture_output=True,timeout=timeout)
        if check and q.returncode:
            raise RuntimeError("command failed "+str(items[:6])+"\n"+q.stderr[-2300:])
        return q.returncode,q.stdout
    log=Path(log);log.parent.mkdir(parents=True,exist_ok=True)
    with log.open("wb") as f:
        q=subprocess.run(items,cwd=cwd,stdin=subprocess.DEVNULL,
                         stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
    if check and q.returncode:
        raise RuntimeError("command failed "+str(items[:6])+"\n"+
                           log.read_text(errors="replace")[-3500:])
    return q.returncode,""

def write_json(path, data):
    Path(path).write_text(json.dumps(data,indent=2,sort_keys=True)+"\n")

def download(rel):
    url=f"https://raw.githubusercontent.com/Chreece/shadPS4/{TOPIC}/{rel}"
    fd,temp=tempfile.mkstemp(prefix=".srt-topic-",dir=OUT)
    os.close(fd)
    try:
        command(["curl","-4fsSL","--retry","3","--connect-timeout","20",
                 "--max-time","120",url,"-o",temp],timeout=360)
        data=Path(temp).read_bytes()
        if blob(data)!=PINS[rel]:
            raise RuntimeError("pinned general topic source hash changed: "+rel)
        return data
    finally:
        Path(temp).unlink(missing_ok=True)

def before_configs():
    profile=HOME/".local/share/shadPS4"
    files=[profile/"config.json",profile/"custom_configs/CUSA34384.json"]
    seen={}
    for p in files:
        if p.is_symlink():
            raise RuntimeError("symlinked configuration")
        if p.exists() and (not p.is_file() or p.stat().st_size>5000000):
            raise RuntimeError("unsafe configuration path "+str(p))
        seen[str(p)] = sha(p) if p.exists() else None
    return seen

def preflight():
    if SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("pinned upstream proof checkout missing")
    if (SRC/RELPATH).is_symlink():
        raise RuntimeError("unsafe SRT source")
    h=command(["git","rev-parse","HEAD"],cwd=SRC,timeout=30)[1].strip()
    if h!="2e5ff4eb89a5c1ccf9519cd2da1e544e569a831b":
        raise RuntimeError("working source HEAD changed; aborting instead of guessing")
    if sha(GOOD)!=GOOD_SHA:
        raise RuntimeError("previous rendered GoW108 binary is not the exact known one")
    if not (BUILD/"shadps4").is_file():
        raise RuntimeError("upstream proof emulator absent")
    current=sha(SRC/RELPATH)
    if MANIFEST.exists():
        if MANIFEST.is_symlink() or not MANIFEST.is_file():
            raise RuntimeError("unsafe partial-run manifest")
        record=json.loads(MANIFEST.read_text())
        if record.get("topic")!=TOPIC or record.get("before_source")!=SOURCE_SHA:
            raise RuntimeError("existing SRT recovery manifest does not match")
        if current!=record.get("after_source"):
            raise RuntimeError("SRT source changed since previously verified recovery")
        if not SNAPSHOT.joinpath("shadps4").is_file() or sha(SNAPSHOT/"shadps4")!=BINARY_SHA:
            raise RuntimeError("checkpoint of original upstream executable missing")
        note("SOURCE=previous SRT correction verified; resuming")
    else:
        if current!=SOURCE_SHA or sha(BUILD/"shadps4")!=BINARY_SHA:
            raise RuntimeError("neither current SRT source nor emulator matches source-parity archive")
        SNAPSHOT.mkdir(parents=True,exist_ok=False)
        shutil.copy2(BUILD/"shadps4",SNAPSHOT/"shadps4")
        if sha(SNAPSHOT/"shadps4")!=BINARY_SHA:
            raise RuntimeError("original emulator snapshot not byte-identical")
        note("ORIGINAL_EXECUTABLE=checkpointed before all source changes")

def replace_one(text,old,new,label):
    if text.count(old)!=1:
        raise RuntimeError(f"{label} anchor not unique ({text.count(old)})")
    return text.replace(old,new,1)

def patch_srt(original):
    text=original.decode()
    text=replace_one(text,'#include "shader_recompiler/ir/passes/srt.h"\n',
          '#include "shader_recompiler/ir/passes/srt.h"\n'
          '#include "shader_recompiler/ir/passes/srt_offset_codegen.h"\n',"SRT include")
    # Scope changes to their exact functions, not shared C++ text elsewhere.
    start=text.index("static bool EmitComputeOffsetShiftLeftLogical32(")
    stop=text.index("static bool EmitComputeOffsetShiftRightLogical32(",start)
    part=text[start:stop]
    part=replace_one(part,
        "        c.mov(ecx, reg);\n"
        "        c.mov(reg, inst->Arg(0).U32());\n"
        "        c.shl(reg, cl);",
        "        SrtCodegen::EmitConstantLeftShiftByDynamicCount("
        "c, reg, inst->Arg(0).U32());","constant-left dynamic shift")
    text=text[:start]+part+text[stop:]
    start=text.index("static bool EmitComputeOffsetBitFieldUExtract(")
    stop=text.index("static bool IsAllowedOffsetInstruction(",start)
    part=text[start:stop]
    part=replace_one(part,
        "        if (width > 32) return false;\n"
        "        if (width < 32) c.and_(reg, (1U << width) - 1);",
        "        if (width < 32) {\n"
        "            c.and_(reg, width == 0 ? 0u : (1u << width) - 1);\n"
        "        }","immediate width 32+")
    part=replace_one(part,
        "        c.mov(ecx, reg);\n"
        "        c.mov(edx, 1);\n"
        "        c.shl(edx, cl);\n"
        "        c.dec(edx);\n"
        "        c.and_(dword[rsp], edx);",
        "        SrtCodegen::EmitLowBitMask(c, reg, edx);\n"
        "        c.and_(dword[rsp], edx);","variable width mask")
    text=text[:start]+part+text[stop:]
    if any(b in text for b in ("SHADPS4_GOW_","GOW_SRT_","CUSA34384")):
        raise RuntimeError("unexpected game-specific behavior in SRT source")
    if text.count("SrtCodegen::EmitLowBitMask(")!=1:
        raise RuntimeError("variable-width correction not wired exactly once")
    return text.encode()

def install():
    source=SRC/RELPATH
    header=SRC/HEADER
    header_bytes=download(HEADER)
    test_bytes=download(TEST)
    if header.is_symlink() or (header.exists() and blob(header.read_bytes())!=PINS[HEADER]):
        raise RuntimeError("source already has an unrelated SRT offset helper")
    (OUT/"test_srt_offset_codegen.cpp").write_bytes(test_bytes)
    if MANIFEST.exists():
        record=json.loads(MANIFEST.read_text())
        if not header.is_file() or blob(header.read_bytes())!=PINS[HEADER]:
            raise RuntimeError("SRT helper lost after prior run")
        write_json(OUT/"source-proof.json",record)
        return
    original=source.read_bytes()
    if sha_bytes(original)!=SOURCE_SHA:
        raise RuntimeError("original active SRT bytes changed")
    patched=patch_srt(original)
    if patched==original:
        raise RuntimeError("SRT transform made no effective change")
    diff="".join(difflib.unified_diff(
        original.decode().splitlines(keepends=True),
        patched.decode().splitlines(keepends=True),
        fromfile="before/"+RELPATH,tofile="after/"+RELPATH,n=3))
    (OUT/"generic-srt-arithmetic.patch").write_text(diff)
    backup=SNAPSHOT/"flatten_extended_userdata_pass.cpp"
    shutil.copy2(source,backup)
    if sha(backup)!=SOURCE_SHA:
        raise RuntimeError("SRT original source checkpoint failed")
    try:
        header.parent.mkdir(parents=True,exist_ok=True)
        header.write_bytes(header_bytes)
        source.write_bytes(patched)
        command(["git","diff","--check"],cwd=SRC,timeout=60)
    except BaseException:
        shutil.copy2(backup,source)
        header.unlink(missing_ok=True)
        raise
    record={
        "topic":TOPIC,"before_source":SOURCE_SHA,
        "after_source":sha_bytes(patched),"old_good_binary":GOOD_SHA,
        "old_upstream_executable":BINARY_SHA,
        "restored_generic_semantics":[
            "dynamic-left shift count preserved",
            "immediate bit-field extraction of width 32+",
            "dynamic bit-field extraction of width 32+",
        ],
        "header_blob":PINS[HEADER],"test_blob":PINS[TEST],
        "game_specific_source_changes":False,
    }
    write_json(MANIFEST,record)
    write_json(OUT/"source-proof.json",record)
    note("SOURCE=three general SRT arithmetic corrections restored")

def test_jit():
    compiler=shutil.which("g++") or shutil.which("clang++")
    if not compiler:
        raise RuntimeError("C++ compiler missing")
    executable=OUT/"test_srt_offset_codegen"
    include=SRC/"externals/xbyak"
    if not include.joinpath("xbyak/xbyak.h").is_file():
        raise RuntimeError("pinned Xbyak external not initialized")
    note("TEST=independent SRT Xbyak JIT boundary cases")
    command([compiler,"-std=c++20","-O2","-Wall","-Wextra",
             "-I",SRC,"-I",include,OUT/"test_srt_offset_codegen.cpp",
             "-o",executable],log=OUT/"jit-build.log",timeout=240)
    command([executable],log=OUT/"jit-test.log",timeout=90)
    report=(OUT/"jit-test.log").read_text()
    if "GENERAL_SRT_OFFSET_JIT=PASS cases=126" not in report:
        raise RuntimeError("SRT JIT regression did not report all 126 checks")
    note("SRT_JIT=PASS 126 cases")

def gcn():
    note("TEST=independent GCN suite after SRT fix")
    command(["cmake","--build",GCN_BUILD,"--target","shadps4_gcn_test",
             "--parallel","4"],log=OUT/"gcn-build.log",timeout=7200)
    matches=[p for p in GCN_BUILD.rglob("shadps4_gcn_test")
             if p.is_file() and os.access(p,os.X_OK) and not p.is_symlink()]
    if len(matches)!=1:
        raise RuntimeError("GCN regression binary missing/ambiguous")
    rc,_=command([matches[0],"--gtest_output=json:"+str(OUT/"gcn-tests.json")],
                 log=OUT/"gcn-tests.log",timeout=1800,check=False)
    if rc:
        raise RuntimeError("full GCN regression suite failed; see gcn-tests.log")
    report=json.loads((OUT/"gcn-tests.json").read_text())
    if int(report.get("tests",0))!=73 or int(report.get("failures",1))!=0:
        raise RuntimeError("GCN suite did not pass all previously verified 73 cases")
    note("GCN=PASS 73/73")

def game(result):
    stage3=STAGE3
    if not stage3.is_file() or stage3.is_symlink() or blob(stage3.read_bytes())!=STAGE3_BLOB:
        raise RuntimeError("known unchanged-settings capture helper is unavailable")
    spec=importlib.util.spec_from_file_location("srt_rec_stage3",stage3)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base=module.load_base()
    auto=module.normal_config_capture_script(base)
    if "patch_config(CFG, args.readbacks_mode)" in auto.read_text():
        raise RuntimeError("capture still changes per-game settings")
    pre=before_configs()
    binary=BUILD/"shadps4"
    result["new_executable_sha256"]=sha(binary)
    if result["new_executable_sha256"]==BINARY_SHA:
        raise RuntimeError("new candidate did not relink")
    note("GAMEPLAY=unchanged user configuration; screenshot/Cross evidence")
    rc,_=command([sys.executable,auto,"--binary",binary,
                  "--sha256",result["new_executable_sha256"],
                  "--seconds","180","--interval","10","--after-delay","0.5"],
                 log=OUT/"gameplay.log",timeout=450,check=False)
    result["gameplay_runner_exit"]=rc
    found=[]
    for line in (OUT/"gameplay.log").read_text(errors="replace").splitlines():
        if not line.startswith("UPLOAD="):
            continue
        p=Path(line[7:].strip())
        if p.is_file() and not p.is_symlink() and p.parent.resolve()==LOGROOT.resolve() \
                and p.name.startswith("gow-auto-gameplay-"):
            found.append(p)
    if found:
        archive=found[-1]
        with tarfile.open(archive,"r:gz") as tf:
            meta=[m for m in tf if m.isfile() and m.name.endswith("/result.json")]
            if len(meta)!=1 or meta[0].size>100_000:
                raise RuntimeError("invalid gameplay capture metadata")
            stats=json.load(tf.extractfile(meta[0]))
            screenshots=sum(x.isfile() and x.name.lower().endswith((".jpg",".jpeg",".png"))
                            for x in tf.getmembers())
            result.update({"gameplay_guest_exit":stats.get("returncode"),
                           "gameplay_failure":stats.get("failure"),
                           "cross_presses":stats.get("cross_presses"),
                           "screenshot_count":screenshots})
        result["nested_capture"]=str(archive)
    else:
        result["screenshot_count"]=0
        result["nested_capture"]=None
    result["user_configs_unchanged"]=before_configs()==pre
    if not result["user_configs_unchanged"]:
        raise RuntimeError("user config changed unexpectedly")
    result["rendered_gameplay_verified"]=False
    result["status"]="CAPTURED_UNREVIEWED" if result["screenshot_count"] else "GAMEPLAY_CAPTURE_INCOMPLETE"

def package(result):
    write_json(OUT/"result.json",result)
    dest=LOGROOT/(OUT.name+".tar.gz")
    with tarfile.open(dest,"w:gz",compresslevel=3) as tf:
        for f in sorted(OUT.rglob("*")):
            if f.is_file() and not f.is_symlink() and f.name!="test_srt_offset_codegen":
                tf.add(f,arcname=OUT.name+"/"+str(f.relative_to(OUT)))
        capture=result.get("nested_capture")
        if capture:
            candidate=Path(capture)
            if candidate.is_file() and not candidate.is_symlink():
                tf.add(candidate,arcname=OUT.name+"/gameplay-capture.tar.gz")
    note("UPLOAD="+str(dest))

def main():
    LOGROOT.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={
        "status":"STARTED","topic":TOPIC,
        "general_fix_only":True,"previous_good_untouched":True,
        "installed_emulator_untouched":True,"global_readback_override":False,
        "per_game_configuration_change":False,"rendered_gameplay_verified":False,
    }
    try:
        preflight()
        result["original_configuration"]=before_configs()
        install()
        test_jit()
        note("BUILD=incremental original upstream tree; original candidate checkpointed")
        command(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
                log=OUT/"build.log",timeout=7200)
        binary=BUILD/"shadps4"
        result["new_executable_sha256"]=sha(binary)
        command(["ldd",binary],log=OUT/"ldd.log",timeout=60)
        if "not found" in (OUT/"ldd.log").read_text(errors="replace"):
            raise RuntimeError("rebuilt candidate missing shared libraries")
        gcn()
        result["srt_jit_pass"]=True
        result["gcn_pass"]=True
        game(result)
        return 0 if result["status"]=="CAPTURED_UNREVIEWED" else 1
    except BaseException as error:
        result.update({"status":"FAILED","error":repr(error)})
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if "original_configuration" in result:
            result["configs_unchanged_after_run"]=(
                before_configs()==result["original_configuration"])
            if not result["configs_unchanged_after_run"]:
                result["status"]="FAILED_USER_CONFIG_CHANGE"
        package(result)

if __name__=="__main__":
    raise SystemExit(main())
