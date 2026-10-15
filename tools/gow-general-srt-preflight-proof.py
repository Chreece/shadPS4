#!/usr/bin/env python3
"""General graphics CPU fix: pre-classify unflattenable SRT offsets.

Keep the exact generic shadPS4 rendering/audio executable and all earlier
fixes. Unsupported dynamic pointer expressions are preserved for the real
guest-memory ReadConst path, not emitted as unusable SRT JIT code. This is
source correctness and avoided repeated work, not a log filter or game tweak.

No game/shader-ID checks, forced offsets, artificial caps, configuration edits,
DMA/readback toggles, driver changes, installed-launcher writes or SSH exit.
"""
from __future__ import annotations

import datetime as dt
import difflib
import hashlib
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
LOGS=HOME/"projects/shadps4/logs"
OUT=LOGS/("gow-general-srt-preflight-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
TOPIC="d2905c62104d581abefc5272e6393f6828b5af85"
BASE="b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
STACK="966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"
BEFORE_SHA="9526f71635c4a20b604fe33494168f16151e70da9498da39fa48f3e38a0a9de9"
OLD_AUDIO_SHA="8fb9a3c478b9229dc478b68703cc303ac1520f7af7c3e42a1ac467910ea68ae6"
OLD_GRAPHICS_SHA="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
OLD_GOOD_SHA="7ee847328072f9d65772fc85ee19f93143cbaaafe69e381d70379479ddd81a30"
SRT="src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
HEADER="src/shader_recompiler/ir/passes/srt_offset_preflight.h"
TEST="tests/gcn/test_gcn_instructions.cpp"
CACHE="src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp"
SOURCE_PINS={
    SRT:"3ecd1ed4c74b339cc5eb8cd34ed8527624a8be9a65f46c2690dc86697c88e82d",
    TEST:"3b04c518d3a366a4875f0fda04bdd9332527d3e0049389b6402ff3a39a04a75e",
    CACHE:"2126773cc721ad040bf1b67db3375aeedd49b450053d4ff57e0ff16ede7f7c40",
}
TOPIC_BLOBS={
    SRT:"a4b677a47d610a5013f9d35819de0d6d3580508f",
    HEADER:"06618418180ae9baac6d0e1054d9f7c704fa5f7b",
    TEST:"3c24dfb57fe58800b597f87fd84b26540d27bb2f",
}
JIT_TOPIC="9165f7e9bbab0d3fb846c980f63dcdfa272d4f53"
JIT_TEST_BLOB="00fded003c4f0ec076f06a8d36e6187c9d59e560"
PERF=ROOT/"scripts/gow-generic-performance-profile.py"
PERF_BLOB="99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
PROFILE_BASE_SHA="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
SNAP=WORK/"preserved-before-general-srt-preflight"
MANIFEST=WORK/".general-srt-preflight.json"
BASELINE_ARCHIVE=LOGS/"gow-generic-performance-20261015-201341-2660904.tar.gz"
INSTALLED=HOME/"Applications/shadps4/shadps4"

def announce(s):
    print("GOW_SRT_PREFLIGHT "+str(s),flush=True)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(1048576),b""):
            h.update(chunk)
    return h.hexdigest()

def git_blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def command(args,*,cwd=None,log=None,timeout=120,check=True):
    args=[str(v) for v in args]
    if args[0]=="git":
        args=["git","-c","http.version=HTTP/1.1",*args[1:]]
    if log:
        p=Path(log);p.parent.mkdir(parents=True,exist_ok=True)
        with p.open("wb") as f:
            done=subprocess.run(args,cwd=cwd,stdin=subprocess.DEVNULL,
                                stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
        if check and done.returncode:
            raise RuntimeError(f"rc={done.returncode}: {args[:5]}\n"+
                               p.read_text(errors="replace")[-4500:])
        return done.returncode,""
    done=subprocess.run(args,cwd=cwd,stdin=subprocess.DEVNULL,
                        capture_output=True,text=True,timeout=timeout)
    if check and done.returncode:
        raise RuntimeError(f"rc={done.returncode}: {args[:5]}\n"+
                           (done.stderr+done.stdout)[-4500:])
    return done.returncode,done.stdout

def save_json(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix=".srt-proof-",dir=path.parent)
    try:
        with os.fdopen(fd,"w") as f:
            json.dump(obj,f,indent=2,sort_keys=True);f.write("\n")
            f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        Path(temp).unlink(missing_ok=True)

def one(s,old,new,label):
    n=s.count(old)
    if n!=1:
        raise RuntimeError(f"{label}: expected exactly one existing source anchor, got {n}")
    return s.replace(old,new,1)

def source_file(rel):
    p=SRC/rel
    if not p.is_file() or p.is_symlink():
        raise RuntimeError("unsafe/missing source file "+rel)
    return p

def fetch(rel,expected,commit=TOPIC):
    url=f"https://raw.githubusercontent.com/Chreece/shadPS4/{commit}/{rel}"
    fd,tmp=tempfile.mkstemp(dir=OUT,prefix=".topic-srt-")
    os.close(fd)
    try:
        command(["curl","-4fsSL","--retry","3","--connect-timeout","20",
                 "--max-time","120",url,"-o",tmp],timeout=360)
        data=Path(tmp).read_bytes()
        if git_blob(data)!=expected:
            raise RuntimeError("pinned topic Git blob mismatches: "+rel)
        return data
    finally:
        Path(tmp).unlink(missing_ok=True)

def preflight():
    if SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("original upstream+general source checkout missing")
    ancestry=command(["git","rev-list","--parents","-n","1","HEAD"],cwd=SRC)[1].split()
    if len(ancestry)!=3 or ancestry[1:]!=[BASE,STACK]:
        raise RuntimeError("source is not the preserved upstream+general merged tree")
    if command(["git","ls-files","-u"],cwd=SRC)[1].strip():
        raise RuntimeError("unmerged source conflicts")
    candidate=BUILD/"shadps4"
    if candidate.is_symlink() or not candidate.is_file():
        raise RuntimeError("rendered emulator binary missing")
    if MANIFEST.is_symlink():
        raise RuntimeError("unsafe existing recovery manifest")
    if not MANIFEST.exists() and file_sha(candidate)!=BEFORE_SHA:
        raise RuntimeError("current emulator differs from proven audio+graphics baseline")
    for rel in SOURCE_PINS:
        source_file(rel)
    if (SRC/HEADER).is_symlink():
        raise RuntimeError("SRT header path is a symlink")
    snapshots=[
        (WORK/"preserved-before-general-audio-buffer/shadps4",OLD_AUDIO_SHA),
        (WORK/"preserved-before-general-audio-lock/shadps4",OLD_GRAPHICS_SHA),
        (ROOT/"tests/gow91-build-20261015-125214-1308309/shadps4",OLD_GOOD_SHA),
    ]
    for path,expected in snapshots:
        if not path.is_file() or path.is_symlink() or file_sha(path)!=expected:
            raise RuntimeError("earlier rendering checkpoint missing or changed: "+str(path))
    if not GCN.is_dir():
        raise RuntimeError("existing separate GCN build missing")
    if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                      stdout=subprocess.DEVNULL).returncode==0:
        raise RuntimeError("shadPS4 already running; not interrupting your session")

def transform_srt(data):
    text=data.decode()
    if "CanLowerSrtOffsetStructurally" in text or "dynamic_offsets_deferred" in text:
        raise RuntimeError("SRT preflight already present outside checkpoint")
    text=one(text,
        '#include "shader_recompiler/ir/passes/srt_offset_codegen.h"\n',
        '#include "shader_recompiler/ir/passes/srt_offset_codegen.h"\n'
        '#include "shader_recompiler/ir/passes/srt_offset_preflight.h"\n',
        "SRT include")
    text=one(text,
        "    u16 dst_off_dw;\n",
        "    u16 dst_off_dw;\n"
        "    u32 dynamic_offsets_deferred{};\n"
        "    std::unordered_map<const IR::Inst*, bool> offset_support_cache;\n",
        "SRT pass state")

    begin=text.index("static void VisitPointer(")
    end=text.index("static void GenerateSrtProgram(",begin)
    prefix=text[:begin];fn=text[begin:end];suffix=text[end:]
    fn=one(fn,
       "    if (!PushPtr(c, pass_info, off_dw)) {\n",
       "    // Unsupported control-flow-dependent offsets stay in the actual\n"
       "    // dynamic ReadConst / buffer access path, never an incomplete JIT.\n"
       "    if (!CanLowerSrtOffsetStructurally(off_dw, pass_info.offset_support_cache)) {\n"
       "        ++pass_info.dynamic_offsets_deferred;\n"
       "        return;\n"
       "    }\n"
       "    if (!PushPtr(c, pass_info, off_dw)) {\n",
       "SRT parent pointer check")
    fn=one(fn,
        "            const size_t code_begin = c.getSize();\n"
        "            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {\n",
        "            if (!CanLowerSrtOffsetStructurally(src_off_dw,\n"
        "                                                pass_info.offset_support_cache)) {\n"
        "                ++pass_info.dynamic_offsets_deferred;\n"
        "                continue;\n"
        "            }\n"
        "            const size_t code_begin = c.getSize();\n"
        "            if (!ComputeOffset(c, r10d, pass_info, src_off_dw)) {\n",
        "SRT dynamic child check")
    text=prefix+fn+suffix
    text=one(text,
       "    info.srt_info.flattened_bufsize_dw = pass_info.dst_off_dw;\n",
       "    info.srt_info.flattened_bufsize_dw = pass_info.dst_off_dw;\n"
       "    if (pass_info.dynamic_offsets_deferred != 0) {\n"
       "        LOG_DEBUG(Render_Recompiler,\n"
       '                  "SRT dynamic offsets retained for guest-memory evaluation: {} in shader {:#x}",\n'
       "                  pass_info.dynamic_offsets_deferred, info.pgm_hash);\n"
       "    }\n",
       "SRT deferred summary")
    for marker in ("SHADPS4_GOW_","GOW_SRT_","CUSA34384"):
        if marker in text:raise RuntimeError("unexpected game-specific SRT code: "+marker)
    if text.count("CanLowerSrtOffsetStructurally(")!=2:
        raise RuntimeError("missing preflight call site")
    if "SrtCodegen::EmitLowBitMask" not in text or "MakeSrtRootDependencyPlan" not in text:
        raise RuntimeError("previous generic SRT arithmetic/root ordering not retained")
    return text.encode()

def transform_tests(data,topic_bytes):
    text=data.decode(); ref=topic_bytes.decode()
    start=ref.index("TEST_F(GcnTest, srt_phi_offset_deferred_without_native_codegen)")
    end=ref.index("struct F32x2 {",start)
    cases=ref[start:end]
    if cases.count("TEST_F(GcnTest,")!=3:
        raise RuntimeError("unexpected number of topic regression cases")
    if "srt_phi_offset_deferred_without_native_codegen" in text:
        raise RuntimeError("SRT preflight tests already present without manifest")
    text=one(text,"struct F32x2 {",cases+"struct F32x2 {","GCN insert cases")
    for include in (
        '#include "common/object_pool.h"\n',
        '#include "shader_recompiler/ir/basic_block.h"\n',
        '#include "shader_recompiler/ir/microinstruction.h"\n',
        '#include "shader_recompiler/ir/passes/srt_offset_preflight.h"\n',
    ):
        if include not in text:
            text=one(text,'#include "translator.hpp"\n',
                     '#include "translator.hpp"\n'+include,"GCN test include")
    if text.count("f64_phi_emits_float64_type_and_capability")!=1:
        raise RuntimeError("prior F64 Phi regression missing")
    return text.encode()

def transform_cache(data,topic):
    text=data.decode()
    if "ShaderBinaryVersion = 10u;" not in topic.decode() or "ShaderMetaVersion = 7u;" not in topic.decode():
        raise RuntimeError("unexpected published generic shader-cache version")
    text=one(text,"static constexpr u32 ShaderBinaryVersion = 13u;",
                  "static constexpr u32 ShaderBinaryVersion = 14u;","shader binary cache version")
    text=one(text,"static constexpr u32 ShaderMetaVersion = 11u;",
                  "static constexpr u32 ShaderMetaVersion = 12u;","shader metadata cache version")
    return text.encode()

def install_patch():
    from_topic={rel:fetch(rel,pin) for rel,pin in TOPIC_BLOBS.items()}
    cache_ref=fetch(CACHE,"176533bb980f91bbd372ea64474baef6c6f0768d")
    if HEADER in SOURCE_PINS:
        raise RuntimeError("new header must not exist in original source")

    if MANIFEST.exists():
        record=json.loads(MANIFEST.read_text())
        if record.get("topic")!=TOPIC or record.get("before_executable")!=BEFORE_SHA:
            raise RuntimeError("existing SRT preflight checkpoint is unexpected")
        for rel,hash_ in record["after"].items():
            if not (SRC/rel).is_file() or sha((SRC/rel).read_bytes())!=hash_:
                raise RuntimeError("source changed since last preflight: "+rel)
        if file_sha(SNAP/"shadps4")!=BEFORE_SHA:
            raise RuntimeError("previous rendering executable was not preserved")
        save_json(OUT/"source-proof.json",record)
        announce("SOURCE=verified already applied; resuming existing build")
        return record

    before={rel:source_file(rel).read_bytes() for rel in SOURCE_PINS}
    for rel,pinned in SOURCE_PINS.items():
        if sha(before[rel])!=pinned:
            raise RuntimeError("original SRT/cache/GCN source is not the pinned rendering version: "+rel)
    if (SRC/HEADER).exists():
        raise RuntimeError("unexpected pre-existing SRT support header")
    after={
        SRT:transform_srt(before[SRT]),
        TEST:transform_tests(before[TEST],from_topic[TEST]),
        CACHE:transform_cache(before[CACHE],cache_ref),
        HEADER:from_topic[HEADER],
    }

    # The independently published upstream fix contains exactly the same
    # preflight entrypoints and structural JIT decision semantics.
    upstream_srt=from_topic[SRT].decode()
    for required in (
        "CanLowerSrtOffsetStructurally(off_dw, pass_info.offset_support_cache)",
        "CanLowerSrtOffsetStructurally(src_off_dw, pass_info.offset_support_cache)",
        "dynamic_offsets_deferred",):
        if required not in upstream_srt or required not in after[SRT].decode():
            raise RuntimeError("source patch diverged from general upstream fix: "+required)

    SNAP.mkdir(parents=True,exist_ok=False)
    shutil.copy2(BUILD/"shadps4",SNAP/"shadps4")
    if file_sha(SNAP/"shadps4")!=BEFORE_SHA:
        raise RuntimeError("cannot preserve current rendered executable")
    for rel,raw in before.items():
        dest=SNAP/rel
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(raw)

    try:
        for rel,raw in after.items():
            dest=SRC/rel;dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(raw)
        command(["git","diff","--check"],cwd=SRC,timeout=90)
        record={
           "topic":TOPIC,"upstream_main":BASE,"generic_stack":STACK,
           "before_executable":BEFORE_SHA,
           "before":{p:sha(v) for p,v in before.items()},
           "after":{p:sha(v) for p,v in after.items()},
           "new_tests":3,"expected_gcn_count":79,
           "old_126_srt_arithmetic_checks_retained":True,
           "game_specific_code":False,"shader_identifier_exclusions":False,
           "dma_or_readback_setting_changed":False,
           "graphics_shader_cache_binary_version":14,
           "graphics_shader_cache_meta_version":12,
        }
        save_json(MANIFEST,record)
        save_json(OUT/"source-proof.json",record)
        diff="".join("".join(difflib.unified_diff(
            before.get(p,b"").decode().splitlines(True),
            raw.decode().splitlines(True),
            fromfile="old/"+p,tofile="new/"+p)) for p,raw in after.items())
        (OUT/"general-srt-dynamic-preflight.patch").write_text(diff)
        announce("SOURCE=structural SRT preflight added; fallback remains dynamic guest-memory")
        return record
    except BaseException:
        for rel,raw in before.items():
            (SRC/rel).write_bytes(raw)
        (SRC/HEADER).unlink(missing_ok=True)
        raise

def run_srt_126():
    test=fetch("tests/srt/test_srt_offset_codegen.cpp",JIT_TEST_BLOB,JIT_TOPIC)
    p=OUT/"srt-previous-126.cpp";p.write_bytes(test)
    cc=shutil.which("g++") or shutil.which("clang++")
    if cc is None:raise RuntimeError("C++ compiler unavailable")
    xbyak=SRC/"externals/xbyak"
    if not (xbyak/"xbyak/xbyak.h").is_file():
        raise RuntimeError("pinned Xbyak external absent")
    binary=OUT/"srt-126-test"
    command([cc,"-std=c++20","-O2","-I",SRC/"src","-I",xbyak,p,"-o",binary],
            log=OUT/"srt-126-build.log",timeout=240)
    command([binary],log=OUT/"srt-126-result.log",timeout=120)
    if "GENERAL_SRT_OFFSET_JIT=PASS cases=126" not in (OUT/"srt-126-result.log").read_text():
        raise RuntimeError("previous verified native SRT arithmetic regressed")
    announce("SRT_ARITHMETIC=PASS 126/126")

def run_gcn_79():
    command(["cmake","--build",GCN,"--target","shadps4_gcn_test","--parallel","4"],
            log=OUT/"gcn-build.log",timeout=7200)
    test=GCN/"tests/shadps4_gcn_test"
    if not test.is_file() or test.is_symlink():
        raise RuntimeError("GCN test binary unavailable")
    result,_=command([test,"--gtest_output=json:"+str(OUT/"gcn-results.json")],
                     log=OUT/"gcn-run.log",timeout=1800,check=False)
    if result or not (OUT/"gcn-results.json").is_file():
        raise RuntimeError("GCN suite did not complete with success")
    report=json.loads((OUT/"gcn-results.json").read_text())
    if int(report.get("tests",0))!=79 or int(report.get("failures",1))!=0:
        raise RuntimeError("new dynamic SRT regressions failed/incomplete")
    announce("GCN=PASS 79/79 including SRT structural fallback")

def built_audio_test():
    p=SRC/"tests/audio/test_audio_output_batch_sync.cpp"
    if not p.is_file() or p.is_symlink() or "AUDIO_MULTI_PORT_SYNC=PASS cases=5" not in p.read_text():
        raise RuntimeError("existing 5 audio cases missing")
    cc=shutil.which("g++") or shutil.which("clang++")
    bin=OUT/"audio-test"
    command([cc,"-std=c++20","-O2","-pthread","-I",SRC/"src",p,"-o",bin],
            log=OUT/"audio-build.log",timeout=240)
    command([bin],log=OUT/"audio-result.log",timeout=60)
    if "AUDIO_MULTI_PORT_SYNC=PASS cases=5" not in (OUT/"audio-result.log").read_text():
        raise RuntimeError("audio buffer/concurrency code regressed")
    announce("AUDIO=PASS 5/5 previously proven concurrency tests")

def verify_installed():
    if not INSTALLED.exists():return {"exists":False}
    return {"exists":True,"is_symlink":INSTALLED.is_symlink(),
            "sha256":file_sha(INSTALLED) if INSTALLED.is_file() else None}

def profile_candidate(binary_hash):
    script=PERF
    if not script.is_file() or script.is_symlink() or git_blob(script.read_bytes())!=PERF_BLOB:
        raise RuntimeError("unchanged-settings CPU/GPU profiler differs from pinned source")
    original=script.read_text()
    if original.count(PROFILE_BASE_SHA)!=1:
        raise RuntimeError("pinned profiler previous binary SHA anchor changed")
    rewrite=original.replace(PROFILE_BASE_SHA,binary_hash,1)
    runner=WORK/"helpers/performance-after-srt-preflight.py"
    runner.parent.mkdir(parents=True,exist_ok=True)
    runner.write_text(rewrite)
    command([sys.executable,"-m","py_compile",runner],timeout=45)
    announce("PERFORMANCE=retest new general graphics build with exactly same original settings")
    rc,_=command([sys.executable,runner],log=OUT/"performance-runner.log",
                 timeout=440,check=False)
    archives=[]
    for line in (OUT/"performance-runner.log").read_text(errors="replace").splitlines():
        if "UPLOAD=" not in line:continue
        path=Path(line.split("UPLOAD=",1)[1].strip())
        if (path.is_file() and not path.is_symlink() and
            path.parent.resolve()==LOGS.resolve() and
            path.name.startswith("gow-generic-performance-")):
            archives.append(path)
    if not archives:
        raise RuntimeError(f"CPU/GPU profiler failed rc={rc}; "+
                           (OUT/"performance-runner.log").read_text(errors="replace")[-1200:])
    return archives[-1],read_perf(archives[-1])

def read_perf(path):
    with tarfile.open(path,"r:gz") as t:
        metas=[e for e in t.getmembers() if e.isfile() and e.name.endswith("/result.json")]
        if len(metas)!=1 or metas[0].size>150000:
            raise RuntimeError("performance archive has invalid summary")
        return json.load(t.extractfile(metas[0]))

def archive(result):
    save_json(OUT/"result.json",result)
    dest=LOGS/(OUT.name+".tar.gz")
    with tarfile.open(dest,"w:gz",compresslevel=3) as t:
        for file in sorted(OUT.rglob("*")):
            if file.is_file() and not file.is_symlink() and file.name not in ("srt-126-test","audio-test"):
                t.add(file,arcname=OUT.name+"/"+str(file.relative_to(OUT)))
        if result.get("performance_archive"):
            perf=Path(result["performance_archive"])
            if perf.is_file() and not perf.is_symlink() and perf.parent.resolve()==LOGS.resolve():
                t.add(perf,arcname=OUT.name+"/new-performance-capture.tar.gz")
    announce("UPLOAD="+str(dest))

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={"status":"STARTED","phase":"PREFLIGHT",
            "upstream_main":BASE,"general_stack":STACK,"topic":TOPIC,
            "old_rendering_executable_sha256":BEFORE_SHA,
            "installed_emulator_changed":False,"game_specific_conditions":False,
            "DMA_or_readbacks_changed":False,"graphic_performance_gain_unproven":True}
    before_installed=verify_installed()
    try:
        preflight()
        result["source"]=install_patch()
        result["phase"]="REGRESSION_GATES"
        run_srt_126()
        run_gcn_79()
        built_audio_test()
        result["gcn_pass"]=79
        result["srt_arithmetic_pass"]=126
        result["audio_pass"]=5
        result["phase"]="INCREMENTAL_BUILD"
        announce("BUILD=preserved exact rendering executable; relink changed native SRT code")
        command(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
                log=OUT/"emulator-build.log",timeout=7200)
        new_sha=file_sha(BUILD/"shadps4")
        if new_sha==BEFORE_SHA or file_sha(SNAP/"shadps4")!=BEFORE_SHA:
            raise RuntimeError("new executable not relinked or preserved copy changed")
        result["new_executable_sha256"]=new_sha
        command(["ldd",BUILD/"shadps4"],log=OUT/"ldd.log",timeout=90)
        if "not found" in (OUT/"ldd.log").read_text(errors="replace"):
            raise RuntimeError("new emulator has unresolved shared libraries")
        result["phase"]="GPU_CPU_PERFORMANCE"
        perf,after=profile_candidate(new_sha)
        result["performance_archive"]=str(perf)
        result["after"]={
            k:after.get(k) for k in
            ("status","binary_sha256","resource_summary","event_counts",
             "original_config_hashes_unchanged","verified_binary_unchanged",
             "image_count","guest_exit")
        }
        if (after.get("status")!="CPU_GPU_PROFILE_CAPTURED" or
            after.get("binary_sha256")!=new_sha or
            after.get("original_config_hashes_unchanged") is not True):
            raise RuntimeError("new performance capture/setting parity not verified")
        if BASELINE_ARCHIVE.is_file() and not BASELINE_ARCHIVE.is_symlink():
            prior=read_perf(BASELINE_ARCHIVE)
            if prior.get("binary_sha256")==BEFORE_SHA:
                result["before"]={
                    k:prior.get(k) for k in
                    ("status","resource_summary","event_counts","image_count","guest_exit")
                }
                events0=prior.get("event_counts") or {}
                events1=after.get("event_counts") or {}
                result["SRT_Phi_attempts_before_after"]=[
                    events0.get("srt_unsupported_phi"),events1.get("srt_unsupported_phi")]
                result["emulator_CPU_before_after"]=[
                    (prior.get("resource_summary") or {}).get("emulator_cpu_average_percent_all_cores"),
                    (after.get("resource_summary") or {}).get("emulator_cpu_average_percent_all_cores")]
                result["GPU_comm_thread_cpu_before_after"]=[
                    next((x["cpu_seconds"] for x in (prior.get("resource_summary") or {}).get(
                          "threads_by_cpu_seconds",[]) if x["name"]=="shadPS4:GpuComm"),None),
                    next((x["cpu_seconds"] for x in (after.get("resource_summary") or {}).get(
                          "threads_by_cpu_seconds",[]) if x["name"]=="shadPS4:GpuComm"),None)]
        result["status"]="REGRESSIONS_PASS_GPU_PROFILE_CAPTURED_VISUAL_REVIEW_REQUIRED"
        return 0
    except BaseException as e:
        result["status"]="FAILED"
        result["error"]=repr(e)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if SNAP.joinpath("shadps4").is_file():
            result["rendering_executable_checkpoint_unchanged"]=file_sha(SNAP/"shadps4")==BEFORE_SHA
        if verify_installed()!=before_installed:
            result["status"]="FAILED_INSTALLED_EMULATOR_CHANGED"
        archive(result)

if __name__=="__main__":
    raise SystemExit(main())
