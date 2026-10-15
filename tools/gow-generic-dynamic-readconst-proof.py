#!/usr/bin/env python3
"""Upstream general dynamic ReadConst correctness: no user GPU-setting overrides.

Port one upstream-based generic shader fix into the already validated
upstream+general emulator tree. Retain all existing general SRT, memory, CPU,
and GCN fixes. Test compiler metadata and shader functions, then capture the
game with original settings. Original binary and config bytes are preserved.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
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
import traceback

HOME = Path.home()
ROOT = HOME/"projects/shadps4/GodOfWarRagnarok"
WORK = ROOT/"tests/main-proof-20261015-151529-3107006"
SRC = WORK/"source"
BUILD = WORK/"build"
TESTBUILD = WORK/"build-gcn-tests"
LOGROOT = HOME/"projects/shadps4/logs"
OUT = LOGROOT/("gow-generic-dynamic-readconst-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
TOPIC = "c583c658eb48770eeb3970b5f7ea4ce89a1a85ff"
UPSTREAM = "b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
STACK = "966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"
BIN_BEFORE = "c874d2038e00e5dd1c4158b1ce1ee3ddb638335f220eed297275b19908eddf69"
PATH_COLLECT = "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp"
PATH_EMIT = "src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp"
PATH_SERIAL = "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
PATH_TEST = "tests/gcn/test_gcn_instructions.cpp"
SHAS = {
    PATH_COLLECT: "007c3fd4146065607512887242802eff4cce79a4",
    PATH_EMIT: "9f6e81c66b13ae68f9e21a00604570b45ae08ee1",
    PATH_SERIAL: "176533bb980f91bbd372ea64474baef6c6f0768d",
    PATH_TEST: "fb87b0be8248d07833ba5bd230c3dd7da47611f6",
}
STAGE3 = ROOT/"scripts/gow-main-proof-stage3.py"
STAGE3_SHA = "f64e16d3f5fe70337b43f187f3802f9c0ae3dc81"
BACKUP = WORK/"preserved-before-general-readconst"
MANIFEST = WORK/".general-dynamic-readconst-proof.json"

def log(message):
    print("GENERIC_READCONST "+str(message),flush=True)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def file_sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()

def blob(raw):
    return hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()

def run(args, *, cwd=None, target=None, timeout=800, check=True):
    args=[str(x) for x in args]
    if args[0]=="git":
        args=["git","-c","http.version=HTTP/1.1",*args[1:]]
    if target:
        p=Path(target);p.parent.mkdir(parents=True,exist_ok=True)
        with p.open("wb") as stream:
            proc=subprocess.run(args,cwd=cwd,stdin=subprocess.DEVNULL,
                                stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
        if check and proc.returncode:
            raise RuntimeError(f"rc={proc.returncode} command={args[:6]}\n"+
                               p.read_text(errors="replace")[-3500:])
        return proc.returncode,""
    proc=subprocess.run(args,cwd=cwd,stdin=subprocess.DEVNULL,
                        capture_output=True,text=True,timeout=timeout)
    if check and proc.returncode:
        raise RuntimeError(f"rc={proc.returncode} command={args[:6]}\n"+
                           proc.stdout[-800:]+proc.stderr[-2400:])
    return proc.returncode,proc.stdout

def json_out(path,value):
    Path(path).write_text(json.dumps(value,sort_keys=True,indent=2)+"\n")

def download(path):
    url=f"https://raw.githubusercontent.com/Chreece/shadPS4/{TOPIC}/{path}"
    fd,tmp=tempfile.mkstemp(prefix=".readconst-",dir=OUT)
    os.close(fd)
    try:
        run(["curl","-4fsSL","--retry","3","--connect-timeout","20",
             "--max-time","120",url,"-o",tmp],timeout=300)
        data=Path(tmp).read_bytes()
        if blob(data)!=SHAS[path]:
            raise RuntimeError("unexpected published generic source: "+path)
        return data.decode()
    finally:
        Path(tmp).unlink(missing_ok=True)

def exactly(text,before,after,label):
    if text.count(before)!=1:
        raise RuntimeError(label+" expected one existing source anchor; found "+str(text.count(before)))
    return text.replace(before,after,1)

def preflight():
    if not (SRC/".git").is_dir() or SRC.is_symlink():
        raise RuntimeError("verified upstream source checkout missing")
    if run(["git","rev-parse","HEAD"],cwd=SRC)[1].strip() not in (
            "2e5ff4eb89a5c1ccf9519cd2da1e544e569a831b",):
        raise RuntimeError("upstream/general-fix provenance changed")
    if run(["git","ls-files","-u"],cwd=SRC)[1].strip():
        raise RuntimeError("unmerged source changes")
    binary=BUILD/"shadps4"
    if not binary.is_file() or binary.is_symlink():
        raise RuntimeError("known emulator candidate missing")
    if MANIFEST.exists():
        if MANIFEST.is_symlink() or not MANIFEST.is_file():
            raise RuntimeError("unsafe recovery manifest")
    elif file_sha(binary)!=BIN_BEFORE:
        raise RuntimeError("compiled emulator differs from uploaded A/B evidence")
    for rel in SHAS:
        if (SRC/rel).is_symlink() or not (SRC/rel).is_file():
            raise RuntimeError("missing/unsafe source: "+rel)
    if not TESTBUILD.is_dir():
        raise RuntimeError("independent GCN test build missing")
    if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                      stdout=subprocess.DEVNULL).returncode==0:
        raise RuntimeError("another emulator process is running; not disrupting it")

def apply_patch():
    if MANIFEST.exists():
        m=json.loads(MANIFEST.read_text())
        if m.get("topic")!=TOPIC or set(m.get("after",{}))!=set(SHAS):
            raise RuntimeError("existing dynamic-memory patch provenance invalid")
        for rel,expected in m["after"].items():
            if file_sha(SRC/rel)!=expected:
                raise RuntimeError("partially changed source differs: "+rel)
        if file_sha(BACKUP/"shadps4")!=BIN_BEFORE:
            raise RuntimeError("original executable checkpoint changed")
        log("SOURCE=previous generic correction verified")
        return m

    collected=(SRC/PATH_COLLECT).read_text()
    emit=(SRC/PATH_EMIT).read_text()
    serial=(SRC/PATH_SERIAL).read_text()
    tests=(SRC/PATH_TEST).read_text()
    if blob(emit.encode())!="e2f35b1afc4f8d74fbd48322ffdf9fc887403d9f":
        raise RuntimeError("unrecognized current ReadConst SPIR-V backend")
    if collected.count("if (!EmulatorSettings.IsDirectMemoryAccessEnabled())")!=1:
        raise RuntimeError("unsupported existing constant-read metadata logic")
    if tests.count("TEST_F(GcnTest, bitcmp1_b64_checks_both_halves_and_masked_index)")!=1:
        raise RuntimeError("earlier GCN bit-compare regressions not retained")
    if "dynamic_readconst_requires_guest_memory_backing" in tests:
        raise RuntimeError("new tests already present outside provenance")
    if "static constexpr u32 ShaderBinaryVersion = 11u;" not in serial or (
            "static constexpr u32 ShaderMetaVersion = 9u;" not in serial):
        raise RuntimeError("shader cache schema does not match previous general fixes")

    topic_collect=download(PATH_COLLECT)
    topic_emit=download(PATH_EMIT)
    topic_serial=download(PATH_SERIAL)
    topic_tests=download(PATH_TEST)

    # Reuse ONLY the exact upstream metadata correction; preserve existing
    # general DS/Wave/GDS changes from the retained CPU+GPU fix stack.
    clean_old=run(["git","show",UPSTREAM+":"+PATH_COLLECT],cwd=SRC)[1]
    metadata_deletion=(
        "    if (!EmulatorSettings.IsDirectMemoryAccessEnabled()) {\n"
        "        info.uses_dma = false;\n"
        "        info.readconst_types = Info::ReadConstType::None;\n"
        "    }\n\n")
    metadata_comment=(
        "    // An unresolved ReadConst cannot be represented by the flattened user-data\n"
        "    // buffer. Preserve the backing-memory requirement discovered from the IR\n"
        "    // regardless of the global optimization preference, or the SPIR-V backend\n"
        "    // would silently load an unrelated flattened word (often word zero).\n"
        "    // Already-resolved ReadConst operations still use the flattened buffer.\n\n")
    replacement=exactly(collected,metadata_deletion,metadata_comment,"dynamic metadata")
    replacement=exactly(replacement,'#include "core/emulator_settings.h"\n',"","unused settings header")
    compare=exactly(clean_old,metadata_deletion,metadata_comment,"topic semantic validation")
    compare=exactly(compare,'#include "core/emulator_settings.h"\n',"","topic include")
    if compare!=topic_collect or topic_emit!=download(PATH_EMIT):
        raise RuntimeError("published patch differs beyond general metadata and backend")

    # The backend was untouched by retained fixes, so an exact topic file is safe.
    binary_cache=exactly(serial,
        "static constexpr u32 ShaderBinaryVersion = 11u;",
        "static constexpr u32 ShaderBinaryVersion = 12u;","binary cache version")
    binary_cache=exactly(binary_cache,
        "static constexpr u32 ShaderMetaVersion = 9u;",
        "static constexpr u32 ShaderMetaVersion = 10u;","metadata cache version")
    if not all(token in topic_serial for token in
               ("ShaderBinaryVersion = 10u;", "ShaderMetaVersion = 7u;")):
        raise RuntimeError("published generic shader cache change unexpected")

    # Add only the two new constant-read CPU metadata regressions to the
    # already-verified 73-test GCN suite.
    start=topic_tests.index("TEST_F(GcnTest, dynamic_readconst_requires_guest_memory_backing)")
    end=topic_tests.index("struct F32x2 {",start)
    two=topic_tests[start:end]
    if two.count("TEST_F(GcnTest,")!=2:
        raise RuntimeError("unexpected published metadata test delta")
    imported=exactly(tests,'#include "translator.hpp"\n',
        '#include "translator.hpp"\n'
        '#include "shader_recompiler/ir/ir_emitter.h"\n'
        '#include "shader_recompiler/ir/microinstruction.h"\n'
        '#include "shader_recompiler/ir/passes/ir_passes.h"\n'
        '#include "shader_recompiler/ir/program.h"\n'
        '#include "shader_recompiler/profile.h"\n'
        '#include "shader_recompiler/recompiler.h"\n',"GCN imports")
    new_tests=exactly(imported,"struct F32x2 {",two+"struct F32x2 {","metadata tests")
    planned={
        PATH_COLLECT: replacement.encode(),
        PATH_EMIT: topic_emit.encode(),
        PATH_SERIAL: binary_cache.encode(),
        PATH_TEST: new_tests.encode(),
    }

    BACKUP.mkdir(parents=True,exist_ok=False)
    shutil.copy2(BUILD/"shadps4",BACKUP/"shadps4")
    if file_sha(BACKUP/"shadps4")!=BIN_BEFORE:
        raise RuntimeError("preserved original binary checksum mismatch")
    for rel in planned:
        target=BACKUP/rel
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(SRC/rel,target)
    # All four source updates are reversible with checkpointed original bytes.
    try:
        for rel,raw in planned.items():
            (SRC/rel).write_bytes(raw)
        run(["git","diff","--check"],cwd=SRC,timeout=90)
        record={"topic":TOPIC,"original_binary":BIN_BEFORE,
                "before":{rel:file_sha(BACKUP/rel) for rel in planned},
                "after":{rel:sha(raw) for rel,raw in planned.items()},
                "cache_binary_version":12,"cache_meta_version":10,
                "new_gcn_tests":2,
                "game_specific_runtime_fix":False,
                "global_dma_setting_edited":False}
        json_out(MANIFEST,record)
        json_out(OUT/"source-proof.json",record)
        log("SOURCE=general IR memory backing retained; no game-specific code")
        return record
    except BaseException:
        for rel in planned:
            shutil.copy2(BACKUP/rel,SRC/rel)
        raise

def current_config():
    profile=HOME/".local/share/shadPS4"
    files=(profile/"config.json",
           profile/"custom_configs/CUSA34384.json")
    data={}
    for p in files:
        if p.is_symlink():
            raise RuntimeError("unsafe profile config symlink")
        if p.exists() and (not p.is_file() or p.stat().st_size>5_000_000):
            raise RuntimeError("unsafe profile config file")
        data[p]=(p.read_bytes(),stat.S_IMODE(p.stat().st_mode)) if p.exists() else None
    return data

def safe_restore(before,result):
    restored=[]
    for path,entry in before.items():
        data=entry[0] if entry else None
        old=(path.read_bytes() if path.is_file() and not path.is_symlink() else None)
        if old==data:continue
        if path.is_symlink():raise RuntimeError("profile config replaced with symlink")
        if data is None:
            path.unlink(missing_ok=True)
        else:
            fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".shader-profile-restore-")
            try:
                os.fchmod(fd,entry[1])
                with os.fdopen(fd,"wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp,path)
            finally:
                Path(tmp).unlink(missing_ok=True)
        restored.append(str(path))
    result["config_files_restored_to_identical_bytes"]=restored
    result["config_identical_after_run"]=all(
        ((p.read_bytes() if p.is_file() else None)==(v[0] if v else None))
        for p,v in before.items())
    if not result["config_identical_after_run"]:
        raise RuntimeError("could not restore original configuration bytes")

def capture(binary,result):
    if not STAGE3.is_file() or STAGE3.is_symlink() or blob(STAGE3.read_bytes())!=STAGE3_SHA:
        raise RuntimeError("prior unchanged-settings capture helper unavailable")
    spec=importlib.util.spec_from_file_location("dynamic_readconst_capture",STAGE3)
    old=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    base=old.load_base()
    helper=old.normal_config_capture_script(base)
    if "patch_config(CFG, args.readbacks_mode)" in helper.read_text():
        raise RuntimeError("capture would modify game GPU configuration")
    log("CAPTURE=original user settings; no forced readbacks or DMA")
    rc,_=run([sys.executable,helper,"--binary",binary,"--sha256",file_sha(binary),
              "--seconds","135","--interval","10","--after-delay","0.5"],
             target=OUT/"gameplay-capture.log",timeout=300,check=False)
    result["capture_runner_exit"]=rc
    found=[]
    for line in (OUT/"gameplay-capture.log").read_text(errors="replace").splitlines():
        if line.startswith("UPLOAD="):
            p=Path(line[7:].strip())
            if p.is_file() and not p.is_symlink() and p.parent.resolve()==LOGROOT.resolve():
                found.append(p)
    if not found:
        result["status"]="CAPTURE_MISSING"
        return
    cap=found[-1]
    result["capture_archive"]=str(cap)
    with tarfile.open(cap,"r:gz") as tar:
        names=tar.getmembers()
        if len(names)>1500:
            raise RuntimeError("unexpectedly large capture archive")
        results=[m for m in names if m.isfile() and m.name.endswith("/result.json")]
        if len(results)!=1 or results[0].size>100_000:
            raise RuntimeError("invalid capture metadata")
        meta=json.load(tar.extractfile(results[0]))
        result["guest_exit"]=meta.get("returncode")
        result["presses"]=meta.get("cross_presses")
        result["captures"]=len([m for m in names if m.isfile() and m.name.lower().endswith((".jpg",".png",".jpeg"))])
        result["capture_failure"]=meta.get("failure")
        result["status"]="VISUAL_CAPTURE_PENDING_REVIEW"
    result["rendered_gameplay_verified"]=False

def archive(result):
    json_out(OUT/"result.json",result)
    dest=LOGROOT/(OUT.name+".tar.gz")
    with tarfile.open(dest,"w:gz",compresslevel=3) as t:
        for p in sorted(OUT.rglob("*")):
            if p.is_file() and not p.is_symlink():
                t.add(p,arcname=OUT.name+"/"+str(p.relative_to(OUT)))
        cap=result.get("capture_archive")
        if cap:
            cp=Path(cap)
            if cp.is_file() and not cp.is_symlink():
                t.add(cp,arcname=OUT.name+"/gameplay-capture.tar.gz")
    log("UPLOAD="+str(dest))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--build-only",action="store_true",
                        help="compile and run all GCN regressions without launching any game")
    opts=parser.parse_args()
    LOGROOT.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700)
    result={"status":"STARTED","phase":"PREFLIGHT","upstream_main":UPSTREAM,
            "general_fix_commit":TOPIC,"installed_emulator_modified":False,
            "source_game_specific_conditions":False,
            "dma_setting_edited":False,"readback_setting_edited":False,
            "rendered_gameplay_verified":False}
    original_profile=None
    try:
        preflight()
        result["source"]=apply_patch()
        result["phase"]="GCN"
        log("BUILD=75 independent GCN tests and dynamic-read metadata coverage")
        run(["cmake","--build",TESTBUILD,"--target","shadps4_gcn_test",
             "--parallel","4"],target=OUT/"gcn-build.log",timeout=7200)
        gcn=[p for p in TESTBUILD.rglob("shadps4_gcn_test")
             if p.is_file() and os.access(p,os.X_OK) and not p.is_symlink()]
        if len(gcn)!=1:raise RuntimeError("GCN binary missing or ambiguous")
        rc,_=run([gcn[0],"--gtest_output=json:"+str(OUT/"gcn.json")],
                 target=OUT/"gcn-run.log",timeout=1800,check=False)
        if rc or not (OUT/"gcn.json").is_file():
            raise RuntimeError("GCN suite failed")
        stats=json.loads((OUT/"gcn.json").read_text())
        if int(stats.get("tests",0))!=75 or int(stats.get("failures",1))!=0:
            raise RuntimeError("GCN suite did not pass all 75 cases")
        result["gcn_tests_passed"]=75
        result["phase"]="EMULATOR_BUILD"
        log("BUILD=incremental upstream+general source; prior binary preserved")
        run(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
            target=OUT/"emulator-build.log",timeout=7200)
        binary=BUILD/"shadps4"
        result["new_binary_sha256"]=file_sha(binary)
        if result["new_binary_sha256"]==BIN_BEFORE:
            raise RuntimeError("emulator did not relink with new shader translation")
        run(["ldd",binary],target=OUT/"ldd.log",timeout=60)
        if "not found" in (OUT/"ldd.log").read_text(errors="replace"):
            raise RuntimeError("linked emulator has missing libraries")
        if opts.build_only:
            result["status"]="BUILD_AND_75_GCN_PASS_GAME_NOT_TESTED"
            return 0
        original_profile=current_config()
        result["profile_config_sha256_before"]={str(p):sha(v[0]) if v else None
                                                for p,v in original_profile.items()}
        result["phase"]="GAMEPLAY"
        capture(binary,result)
        return 0 if result["status"]=="VISUAL_CAPTURE_PENDING_REVIEW" else 1
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if original_profile is not None:
            try:
                safe_restore(original_profile,result)
            except BaseException as e:
                result["status"]="FAILED_PROFILE_RESTORE"
                result["profile_restore_error"]=repr(e)
        archive(result)

if __name__=="__main__":
    raise SystemExit(main())
