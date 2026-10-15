#!/usr/bin/env python3
"""Fresh-upstream general SPIR-V F64 Phi fix: compile, test, then screenshot.

No game/shader-identity branches, DMA toggles, readback overrides, guest
patches, driver changes, launcher replacement, or modifications to the
previously rendered GoW108 binary. Keeps all existing generic fixes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import traceback

HOME=Path.home()
ROOT=HOME/"projects/shadps4/GodOfWarRagnarok"
WORK=ROOT/"tests/main-proof-20261015-151529-3107006"
SRC=WORK/"source"
BUILD=WORK/"build"
GCN=WORK/"build-gcn-tests"
LOGROOT=HOME/"projects/shadps4/logs"
OUT=LOGROOT/("gow-general-f64-phi-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
UPSTREAM="b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
STACK="966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"
TOPIC="8b429401e903b5ee03698ea2546e48fc73ee4c29"
BIN_ORIGINAL="49d1bf0ba9e8efd9466a59ef22483875fc6c09382e671e734652b1a1a6744c54"
EMIT="src/shader_recompiler/backend/spirv/emit_spirv.cpp"
COLLECT="src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp"
TESTS="tests/gcn/test_gcn_instructions.cpp"
CACHE="src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
PIN={
    EMIT:"61440a46221d8b9277fbea83af99e82704b58b64",
    COLLECT:"e19e25dd01937f7dc22ce21b31b70145fcae213d",
    TESTS:"588decada47eab98f585ba241d0ff527f2fef3dd",
}
BEFORE={
    COLLECT:"621edf95e73ec07b2b5e7688d37929308e4db0887d37a41a09cdbcf135f85cc8",
    TESTS:"8425e74f4e6632fcfdb3ee42ca139858817a1338d6634d833fbec58c19373200",
    CACHE:"7094c3e2329f6995a7f36bf6ec43bf3267adf109168015f65198c93d1e5a427c",
}
EMIT_PREVIOUS_BLOB="12bf8dec080710706809b470803466281e0c7117"
STAGE3=ROOT/"scripts/gow-main-proof-stage3.py"
STAGE3_BLOB="f64e16d3f5fe70337b43f187f3802f9c0ae3dc81"
BACKUP=WORK/"preserved-general-f64-phi"
PROOF=WORK/".general-f64-phi-proof.json"
GOOD=ROOT/"tests/gow91-build-20261015-125214-1308309/shadps4"
GOOD_SHA="7ee847328072f9d65772fc85ee19f93143cbaaafe69e381d70379479ddd81a30"
SOURCE_LIST=(EMIT,COLLECT,TESTS,CACHE)

def note(*msg):
    print("GENERAL_F64_PHI",*msg,flush=True)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def file_sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()

def git_blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def run(args,*,cwd=None,log=None,timeout=900,check=True):
    args=[str(v) for v in args]
    if args[0]=="git":
        args=["git","-c","http.version=HTTP/1.1",*args[1:]]
    if log is not None:
        log=Path(log)
        log.parent.mkdir(parents=True,exist_ok=True)
        with log.open("wb") as f:
            q=subprocess.run(args,cwd=cwd,stdin=subprocess.DEVNULL,
                             stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
        if check and q.returncode:
            raise RuntimeError("rc="+str(q.returncode)+" "+str(args[:7])+"\n"+
                               log.read_text(errors="replace")[-3600:])
        return q.returncode,""
    q=subprocess.run(args,cwd=cwd,stdin=subprocess.DEVNULL,
                     text=True,capture_output=True,timeout=timeout)
    if check and q.returncode:
        raise RuntimeError("rc="+str(q.returncode)+" "+str(args[:7])+"\n"+
                           q.stderr[-2800:]+q.stdout[-1100:])
    return q.returncode,q.stdout

def write_json(path,value):
    p=Path(path)
    fd,tmp=tempfile.mkstemp(prefix=".phi-",dir=p.parent)
    try:
        with os.fdopen(fd,"w") as stream:
            json.dump(value,stream,indent=2,sort_keys=True)
            stream.write("\n")
        os.replace(tmp,p)
    finally:
        Path(tmp).unlink(missing_ok=True)

def safe_source(path):
    p=SRC/path
    if not p.is_file() or p.is_symlink() or p.stat().st_size>3_000_000:
        raise RuntimeError("missing/unsafe emulator source: "+path)
    return p

def get_topic(path):
    url=f"https://raw.githubusercontent.com/Chreece/shadPS4/{TOPIC}/{path}"
    fd,tmp=tempfile.mkstemp(prefix=".topic-",dir=OUT)
    os.close(fd)
    try:
        run(["curl","-4fsSL","--retry","3","--connect-timeout","20",
             "--max-time","120",url,"-o",tmp],timeout=360)
        raw=Path(tmp).read_bytes()
        if git_blob(raw)!=PIN[path]:
            raise RuntimeError("published generic source changed: "+path)
        return raw.decode()
    finally:
        Path(tmp).unlink(missing_ok=True)

def one(text,old,new,name):
    n=text.count(old)
    if n!=1:
        raise RuntimeError(name+": expected unique upstream source, got "+str(n))
    return text.replace(old,new,1)

def snapshot_configs():
    result={}
    profile=HOME/".local/share/shadPS4"
    for p in (profile/"config.json",profile/"custom_configs/CUSA34384.json"):
        if p.is_symlink():
            raise RuntimeError("unsafe config symlink: "+str(p))
        if p.exists() and (not p.is_file() or p.stat().st_size>5_000_000):
            raise RuntimeError("unsafe config path: "+str(p))
        result[p]=(p.read_bytes(),stat.S_IMODE(p.stat().st_mode)) if p.exists() else None
    return result

def restore_configs(original,result):
    changed=[]
    for p,record in original.items():
        wanted=record[0] if record else None
        if p.is_symlink():
            raise RuntimeError("config replaced by symlink: "+str(p))
        observed=p.read_bytes() if p.is_file() else None
        if observed==wanted:
            continue
        if record is None:
            p.unlink(missing_ok=True)
        else:
            fd,tmp=tempfile.mkstemp(prefix=".phi-profile-",dir=p.parent)
            try:
                os.fchmod(fd,record[1])
                with os.fdopen(fd,"wb") as f:
                    f.write(record[0])
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp,p)
            finally:
                Path(tmp).unlink(missing_ok=True)
        changed.append(str(p))
    result["config_files_restored"]=changed
    result["config_bytes_identical"]=all(
        (p.read_bytes() if p.is_file() else None)==(v[0] if v else None)
        for p,v in original.items())
    if not result["config_bytes_identical"]:
        raise RuntimeError("unable to preserve original config bytes")

def preflight():
    if SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("previous upstream source checkout missing")
    ancestors=run(["git","rev-list","--parents","-n","1","HEAD"],cwd=SRC)[1].split()
    if len(ancestors)!=3 or ancestors[1:]!=[UPSTREAM,STACK]:
        raise RuntimeError("main+general source history changed")
    if run(["git","ls-files","-u"],cwd=SRC)[1].strip():
        raise RuntimeError("unresolved source conflicts")
    if not GCN.is_dir() or not (BUILD/"shadps4").is_file():
        raise RuntimeError("previous emulator/GCN build missing")
    if not GOOD.is_file() or GOOD.is_symlink() or file_sha(GOOD)!=GOOD_SHA:
        raise RuntimeError("previous rendered GoW108 binary no longer verified")
    if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                      stdout=subprocess.DEVNULL).returncode==0:
        raise RuntimeError("another shadps4 process is running; no interruption")
    if PROOF.exists():
        if PROOF.is_symlink() or not PROOF.is_file():
            raise RuntimeError("unsafe previous work manifest")
    elif file_sha(BUILD/"shadps4")!=BIN_ORIGINAL:
        raise RuntimeError("emulator binary differs from latest 75/75 GCN-tested capture")
    for p in SOURCE_LIST:
        safe_source(p)

def plan():
    old={p:safe_source(p).read_bytes() for p in SOURCE_LIST}
    if git_blob(old[EMIT])!=EMIT_PREVIOUS_BLOB:
        raise RuntimeError("SPIR-V type resolver is not the verified upstream version")
    for p,expected in BEFORE.items():
        if sha(old[p])!=expected:
            raise RuntimeError("previous general emulator source differs: "+p)

    topic_emit=get_topic(EMIT)
    topic_collect=get_topic(COLLECT)
    topic_test=get_topic(TESTS)

    # Replace the whole resolver only because upstream and retained generic
    # stack have the EXACT same verified original blob.
    replacement={EMIT:topic_emit.encode()}
    collect=old[COLLECT].decode()
    begin=topic_collect.index("    case IR::Opcode::Phi: {")
    end=topic_collect.index("    case IR::Opcode::GetAttribute:",begin)
    phi_visit=topic_collect[begin:end]
    if phi_visit.count("info.uses_fp64 = true;")!=1:
        raise RuntimeError("Phi metadata source from topic is incomplete")
    replacement[COLLECT]=one(collect,
         "void Visit(Info& info, const IR::Inst& inst) {\n"
         "    switch (inst.GetOpcode()) {\n",
         "void Visit(Info& info, const IR::Inst& inst) {\n"
         "    switch (inst.GetOpcode()) {\n"+phi_visit,
         "general Phi type metadata").encode()

    test=old[TESTS].decode()
    start=topic_test.index("TEST_F(GcnTest, f64_phi_emits_float64_type_and_capability)")
    end=topic_test.index("struct F32x2 {",start)
    snippet=topic_test[start:end]
    if snippet.count("TEST_F(GcnTest,")!=1 or "Float64" not in snippet:
        raise RuntimeError("unexpected general Phi regression test payload")
    if "f64_phi_emits_float64_type_and_capability" in test:
        raise RuntimeError("Phi regression test already present outside plan")
    test=one(test,"struct F32x2 {",snippet+"struct F32x2 {",
             "append Phi regression")
    for header in (
        "shader_recompiler/backend/spirv/emit_spirv.h",
        "shader_recompiler/runtime_info.h",
    ):
        inc=f'#include "{header}"\n'
        if inc not in test:
            test=one(test,'#include "translator.hpp"\n',
                     '#include "translator.hpp"\n'+inc,"add "+header)
    replacement[TESTS]=test.encode()

    serial=old[CACHE].decode()
    serial=one(serial,"static constexpr u32 ShaderBinaryVersion = 12u;",
               "static constexpr u32 ShaderBinaryVersion = 13u;",
               "shader binary cache compatibility")
    serial=one(serial,"static constexpr u32 ShaderMetaVersion = 10u;",
               "static constexpr u32 ShaderMetaVersion = 11u;",
               "shader metadata cache compatibility")
    replacement[CACHE]=serial.encode()

    # No changes to other SRT, GPU memory, guest ABI, or renderer files.
    for p,raw in replacement.items():
        if any(marker in raw for marker in (b"SHADPS4_GOW_",b"GOW_SRT_")):
            raise RuntimeError("game-specific behavior appeared in changed source: "+p)
        if raw==old[p]:
            raise RuntimeError("no effective source correction: "+p)
    return old,replacement

def patch():
    if PROOF.exists():
        proof=json.loads(PROOF.read_text())
        if proof.get("topic")!=TOPIC or set(proof.get("after",{}))!=set(SOURCE_LIST):
            raise RuntimeError("previous proof metadata mismatch")
        for p,expected in proof["after"].items():
            if sha(safe_source(p).read_bytes())!=expected:
                raise RuntimeError("source changed since previous correction: "+p)
        if file_sha(BACKUP/"shadps4")!=BIN_ORIGINAL:
            raise RuntimeError("original emulator backup missing/changed")
        note("SOURCE=previous general Phi fix verified")
        write_json(OUT/"source-proof.json",proof)
        return

    before,after=plan()
    BACKUP.mkdir(parents=True,exist_ok=False)
    shutil.copy2(BUILD/"shadps4",BACKUP/"shadps4")
    if file_sha(BACKUP/"shadps4")!=BIN_ORIGINAL:
        raise RuntimeError("failed to preserve old proof executable")
    for p in SOURCE_LIST:
        dst=BACKUP/p
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(safe_source(p),dst)
    try:
        for p,raw in after.items():
            safe_source(p).write_bytes(raw)
        run(["git","diff","--check"],cwd=SRC,timeout=60)
        proof={
            "topic":TOPIC,
            "previous_executable":BIN_ORIGINAL,
            "before":{p:sha(b) for p,b in before.items()},
            "after":{p:sha(a) for p,a in after.items()},
            "changed_files":list(SOURCE_LIST),
            "general_phi_f64_fix":True,
            "cache_binary_version":13,
            "cache_meta_version":11,
            "expected_gcn_tests":76,
            "game_specific_code":False,
            "settings_changed":False,
        }
        write_json(PROOF,proof)
        write_json(OUT/"source-proof.json",proof)
        text="".join("".join(difflib.unified_diff(
            before[p].decode().splitlines(True),
            after[p].decode().splitlines(True),
            fromfile="before/"+p,tofile="after/"+p)) for p in SOURCE_LIST)
        (OUT/"general-phi-source.patch").write_text(text)
        note("SOURCE=F64 Phi typing and capability restored; cache versioned")
    except BaseException:
        for p in SOURCE_LIST:
            shutil.copy2(BACKUP/p,SRC/p)
        raise

def gcn_test(result):
    note("GCN=building and testing 76 independent emulator regressions")
    run(["cmake","--build",GCN,"--target","shadps4_gcn_test","--parallel","4"],
        log=OUT/"gcn-build.log",timeout=7200)
    binaries=[p for p in GCN.rglob("shadps4_gcn_test")
              if p.is_file() and not p.is_symlink() and os.access(p,os.X_OK)]
    if len(binaries)!=1:
        raise RuntimeError("GCN test executable missing or ambiguous")
    rc,_=run([binaries[0],"--gtest_output=json:"+str(OUT/"gcn.json")],
              log=OUT/"gcn-run.log",timeout=1800,check=False)
    if rc or not (OUT/"gcn.json").is_file():
        raise RuntimeError("GCN tests failed: "+str(rc))
    report=json.loads((OUT/"gcn.json").read_text())
    if int(report.get("tests",0))!=76 or int(report.get("failures",1))!=0:
        raise RuntimeError("GCN tests missing or failed: "+str(report))
    result["gcn_tests_passed"]=76
    note("GCN=PASS 76/76 including F64 Phi SPIR-V output")

def build(result):
    note("BUILD=incremental general emulator, no settings overrides")
    run(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
        log=OUT/"emulator-build.log",timeout=7200)
    binary=BUILD/"shadps4"
    digest=file_sha(binary)
    if digest==BIN_ORIGINAL:
        raise RuntimeError("new general correction did not relink")
    run(["ldd",binary],log=OUT/"ldd.log",timeout=60)
    if "not found" in (OUT/"ldd.log").read_text(errors="replace"):
        raise RuntimeError("new executable has missing shared libraries")
    result["candidate_binary"]=str(binary)
    result["candidate_binary_sha256"]=digest

def gameplay(result):
    if STAGE3.is_symlink() or not STAGE3.is_file() or git_blob(STAGE3.read_bytes())!=STAGE3_BLOB:
        raise RuntimeError("known unmodified-config capture helper unavailable")
    spec=importlib.util.spec_from_file_location("generic_phi_capture",STAGE3)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base=module.load_base()
    auto=module.normal_config_capture_script(base)
    if "patch_config(CFG, args.readbacks_mode)" in auto.read_text():
        raise RuntimeError("gameplay helper would change GPU setting")
    note("GAMEPLAY=unchanged user config; screenshots only")
    rc,_=run([sys.executable,auto,"--binary",BUILD/"shadps4",
              "--sha256",result["candidate_binary_sha256"],
              "--seconds","145","--interval","8","--after-delay","0.5"],
             log=OUT/"gameplay-runner.log",timeout=350,check=False)
    result["gameplay_runner_exit"]=rc
    captures=[]
    for line in (OUT/"gameplay-runner.log").read_text(errors="replace").splitlines():
        if line.startswith("UPLOAD="):
            p=Path(line[7:].strip())
            if p.is_file() and not p.is_symlink() and p.parent.resolve()==LOGROOT.resolve() and p.name.startswith("gow-auto-gameplay-"):
                captures.append(p)
    if not captures:
        result["status"]="NO_CAPTURE_ARCHIVE"
        return
    capture=captures[-1]
    result["capture_archive"]=str(capture)
    with tarfile.open(capture,"r:gz") as t:
        entries=t.getmembers()
        infos=[m for m in entries if m.isfile() and m.name.endswith("/result.json")]
        logs=[m for m in entries if m.isfile() and m.name.endswith("/console.log")]
        if len(infos)!=1 or infos[0].size>200000 or len(logs)!=1 or logs[0].size>150000000:
            raise RuntimeError("malformed capture archive")
        stats=json.load(t.extractfile(infos[0]))
        console=t.extractfile(logs[0]).read().decode(errors="replace")
        result["guest_exit"]=stats.get("returncode")
        result["cross_presses"]=stats.get("cross_presses")
        result["screenshot_count"]=sum(
            m.isfile() and m.name.lower().endswith((".jpg",".jpeg",".png"))
            for m in entries)
        result["capture_failure"]=stats.get("failure")
        result["phi_type_abort_recurred"]=("Phi node type F64" in console)
        result["device_lost"]=("ErrorDeviceLost" in console or "GPU hang detected" in console)
        result["radv_diagnostics"]={
            "SRT_offset_failures":console.count("Failed to compute offset for SRT walker"),
            "clamped_size_reports":console.count("Clamped size from"),
        }
    result["rendered_gameplay_verified"]=False
    result["status"]="FRAMES_COLLECTED_UNREVIEWED" if result["screenshot_count"] else "GAMEPLAY_FAILED_NO_IMAGES"

def package(result):
    write_json(OUT/"result.json",result)
    tarpath=LOGROOT/(OUT.name+".tar.gz")
    with tarfile.open(tarpath,"w:gz",compresslevel=3) as t:
        for p in sorted(OUT.rglob("*")):
            if p.is_file() and not p.is_symlink():
                t.add(p,arcname=OUT.name+"/"+str(p.relative_to(OUT)))
        nested=result.get("capture_archive")
        if nested:
            p=Path(nested)
            if p.is_file() and not p.is_symlink() and p.parent.resolve()==LOGROOT.resolve():
                t.add(p,arcname=OUT.name+"/gameplay-capture.tar.gz")
    note("UPLOAD="+str(tarpath))

def main():
    arg=argparse.ArgumentParser()
    arg.add_argument("--build-only",action="store_true")
    opts=arg.parse_args()
    LOGROOT.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={
        "status":"STARTED","phase":"PREFLIGHT","upstream_main":UPSTREAM,
        "general_fix_topic":TOPIC,"installed_emulator_changed":False,
        "previous_rendered_executable_changed":False,
        "dma_setting_modified":False,"readback_setting_modified":False,
        "game_specific_compiler_conditions":False,
        "rendered_gameplay_verified":False,
    }
    original=None
    try:
        preflight()
        patch()
        result["phase"]="GCN"
        gcn_test(result)
        result["phase"]="BUILD"
        build(result)
        if opts.build_only:
            result["status"]="76_GCN_PASS_EMULATOR_BUILT_GAME_UNTESTED"
            return 0
        original=snapshot_configs()
        result["config_hashes_before"]={str(p):sha(v[0]) if v else None for p,v in original.items()}
        result["phase"]="GAMEPLAY"
        gameplay(result)
        return 0 if result["status"]=="FRAMES_COLLECTED_UNREVIEWED" else 1
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if original is not None:
            try:
                restore_configs(original,result)
            except BaseException as exc:
                result["status"]="FAILED_CONFIGURATION_RESTORE"
                result["configuration_restore_error"]=repr(exc)
        package(result)

if __name__=="__main__":
    raise SystemExit(main())
