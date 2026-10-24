#!/usr/bin/env python3
"""Build/test the retained universal shader Phi-resource reconstruction.

Starts from the exact previously built native, no-DynamoRIO GoT candidate.
Applies only the old proven generic shader IR buffer/image Phi selection and
the corresponding cache-version bump. No title-based logic or skipped draws.
All five prior SHARP mask/resource fixes and native HLE stack fix remain.
"""
from __future__ import annotations

import argparse, fcntl, hashlib, importlib.util, json, os, re, shutil
import signal, stat, subprocess, sys, tarfile, tempfile, threading, time, traceback
from pathlib import Path

ROOT=Path.home()/"projects/shadps4"
SRC=ROOT/"GhostOfTsushima/source"
WORK=ROOT/"GhostOfTsushima/fresh-source/ca69b6509427-3967624"
BUILD=ROOT/"GhostOfTsushima/fresh-builds/ca69b6509427-3967624"
BASE_REV="8780b183634e6a3a67c3bdab3c684a9ecf263668"
BASE_TREE="156d29f47893924934dad6b3ccc3feb7f90db577"
BASE_SHA="21faa0a6c3b329ee1c52b872a7fedc6d4d6033e66de0ebe3b8ddc593253edfb9"
BASE_BIN=ROOT/"GhostOfTsushima/candidates"/(BASE_REV+"-"+BASE_SHA)/"shadps4"
BASE_REPORT=ROOT/"logs/got-sharp-masks-20261024-172320-hoj_coyg/info/build-result.json"
COMPOSER=ROOT/"scripts/build_run_generic_large_indexed_resources_20261023.py"
COMPOSER_BLOB="eac62a2550104a7493be5464f73cc3b188780580"
NATIVE=ROOT/"scripts/build_run_got_upstream_native_20261024.py"
NATIVE_BLOB="96c6b84bde950513c237a9daec6177999fb3ef92"
PORT=ROOT/"scripts/restore_native_phi_descriptors_20261024.py"
PORT_BLOB="__PORT_BLOB__"
REMOTE="https://github.com/Chreece/shadPS4.git"

def git_blob(path):
    b=path.read_bytes()
    return hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()

def hash_file(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("PINNED_BINARY_MISSING="+str(path))
    with path.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()

def module(path,sha,name):
    if not path.is_file() or git_blob(path)!=sha:
        raise RuntimeError("PINNED_PYTHON_MODULE_DRIFT="+str(path))
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:
        raise RuntimeError("PYTHON_MODULE_IMPORT_FAILED="+name)
    m=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=m;spec.loader.exec_module(m)
    return m

def load_baseline():
    c=module(COMPOSER,COMPOSER_BLOB,"got_generic_native_composer")
    n=module(NATIVE,NATIVE_BLOB,"got_generic_native_runner")
    p=module(PORT,PORT_BLOB,"got_shader_phi_restore")
    c.BASE=c.git("rev-parse","HEAD").decode().strip()
    c.BASE_STATUS=c.source_status()
    c.validate_source_status(c.BASE_STATUS)
    report=json.loads(BASE_REPORT.read_text())
    need={
        "status":"GENERIC_SHARP_MASK_RESTORE_BUILD_PASS",
        "revision":BASE_REV,"tree":BASE_TREE,
        "candidate_sha256":BASE_SHA,
        "dynamorio":False,"sharp_mask_repair":True,
        "index_sampler_validity_repaired":True,
        "native_abi_fix_preserved":True,
        "generic_gpu_fixes_preserved":True,
        "es_de_changed":False,"source_untouched":True,
    }
    for key,want in need.items():
        if report.get(key)!=want:
            raise RuntimeError("PREVIOUS_MENU_WORKING_BUILD_REPORT_MISMATCH="+key)
    if Path(report["candidate"])!=BASE_BIN or hash_file(BASE_BIN)!=BASE_SHA:
        raise RuntimeError("PREVIOUS_NATIVE_BINARY_CHANGED")
    if c.git("rev-parse",BASE_REV+"^{tree}").decode().strip()!=BASE_TREE:
        raise RuntimeError("PREVIOUS_NATIVE_SOURCE_TREE_CHANGED")
    if not c.exists(BASE_REV+"^{commit}"):
        raise RuntimeError("PREVIOUS_NATIVE_SOURCE_NOT_IN_LOCAL_REPOSITORY")
    for path,expected in p.EXPECTED.items():
        if c.git("rev-parse",BASE_REV+":"+path).decode().strip()!=expected:
            raise RuntimeError("RESOURCE_PHI_PREIMAGE_CHANGED="+path)
    for forbidden in ("cmake/CpuIdTranslation.cmake",
                      "src/core/cpu_id_translation/client.c",
                      "src/core/cpu_id_translation/launcher.cpp"):
        if c.exists(BASE_REV+":"+forbidden):
            raise RuntimeError("DYNAMORIO_REINTRODUCED_IN_PARENT")
    return c,n,p,report

def require_previous_generic_phi_source(c,p):
    ref=p.ORIGINAL_REF
    if not c.exists(ref+"^{commit}"):
        print("FETCH_PINNED_GENERIC_PHI_REFERENCE="+ref,flush=True)
        result=c.run(["git","-C",SRC,"fetch","--no-tags",
           "--recurse-submodules=no",REMOTE,ref],check=False,timeout=360)
        if result.returncode or not c.exists(ref+"^{commit}"):
            raise RuntimeError("PINNED_OLDER_GENERIC_PHI_SOURCE_NOT_REACHABLE="+
                 (result.stderr or b"").decode(errors="replace")[-1800:])
    src=c.show(ref,p.DISCOVER)
    hdr=c.show(ref,p.PREFLIGHT)
    if p.blob(src)!=p.ORIGINAL_DISCOVER_BLOB:
        raise RuntimeError("OLDER_SHADER_IR_PHI_SOURCE_HASH_CHANGED")
    if p.blob(hdr)!=p.ORIGINAL_HEADER_BLOB:
        raise RuntimeError("OLDER_GENERIC_PHI_PREFLIGHT_HEADER_CHANGED")
    return src.decode(),hdr.decode()

def compose(c,p,reference,header,info,folder):
    idx=folder/"native-phi.index"
    env=c.env({"GIT_INDEX_FILE":str(idx)})
    c.git("read-tree",BASE_REV,e=env)
    current=c.index_text(idx,p.DISCOVER)
    cache=c.index_text(idx,p.CACHE)
    new=p.transform(current,cache,reference,header)
    for path in p.OUT_PATHS:
        if path in (p.DISCOVER,p.CACHE) and c.index_text(idx,path)==new[path]:
            raise RuntimeError("PHI_SOURCE_NO_CHANGE="+path)
        c.update_index_text(idx,path,new[path])
    tree=c.git("write-tree",e=env).decode().strip()
    revision=c.commit_tree(tree,BASE_REV,
        b"Restore generic GPU-side Phi resource selection from validated shadPS4 reference\n")
    changed=c.git("diff","--name-only",BASE_REV,revision).decode().splitlines()
    if sorted(changed)!=sorted(p.OUT_PATHS):
        raise RuntimeError("UNREVIEWED_PHI_SOURCE_CHANGE="+repr(changed))
    if c.git("rev-parse",revision+"^").decode().strip()!=BASE_REV:
        raise RuntimeError("GENERIC_PHI_REVISION_LOST_PARENT")
    if c.git("rev-parse",revision+"^{tree}").decode().strip()!=tree:
        raise RuntimeError("GENERIC_PHI_SOURCE_TREE_MISMATCH")
    for forbidden in ("cmake/CpuIdTranslation.cmake",
                      "src/core/cpu_id_translation/client.c",
                      "src/core/cpu_id_translation/launcher.cpp"):
        if c.exists(revision+":"+forbidden):
            raise RuntimeError("DYNAMORIO_REINTRODUCED_IN_SHADER_REVISION")
    diff=c.git("diff","--binary",BASE_REV,revision,"--",*p.OUT_PATHS)
    (info/"native-phi-resource-recovery.patch").write_bytes(diff)
    (info/"exact-source-change.json").write_text(json.dumps({
        "parent":BASE_REV,"revision":revision,"tree":tree,"changed_files":changed,
        "old_reference":p.ORIGINAL_REF,
        "old_reference_shader_blob":p.ORIGINAL_DISCOVER_BLOB,
        "phi_is_gpu_control_flow":True,
        "cpu_guessed_descriptor_values":False,
        "no_dynamic_descriptor_replacement":True,
        "no_draw_skips":True,
        "no_title_or_shader_selectors":True,
        "all_sampler_mask_abi_fixes_preserved":True,
        "dynamorio":False,"es_de_modified":False,
    },indent=2)+"\n")
    return revision,tree

def checkout(c,rev):
    if WORK.is_symlink():raise RuntimeError("TEST_SOURCE_WORKDIR_SYMLINK_NOT_ALLOWED")
    if not WORK.exists():
        tmp_ref="refs/shadps4-composer/restore-native-"+BASE_REV[:12]
        c.git("update-ref",tmp_ref,BASE_REV)
        try:
            c.add_fresh_source(WORK,BASE_REV,tmp_ref)
            c.submodules_at(WORK)
        finally:
            c.git("update-ref","-d",tmp_ref,check=False)
    if not (WORK/".git").exists():
        raise RuntimeError("TEST_SOURCE_WORKDIR_NOT_REPO")
    def wg(*cmd):
        return subprocess.check_output(["git","-C",str(WORK),*cmd],
                                       timeout=45).decode().strip()
    old=wg("rev-parse","HEAD")
    if old not in (BASE_REV,rev):
        raise RuntimeError("UNEXPECTED_DISPOSABLE_SOURCE_HEAD="+old)
    if wg("diff","--name-only","HEAD","--") or wg("diff","--cached","--name-only"):
        raise RuntimeError("DISPOSABLE_WORKDIR_HAS_SOURCE_EDITS")
    if old!=rev:
        tmp="refs/shadps4-composer/got-native-phi-"+rev[:12]
        c.git("update-ref",tmp,rev)
        try:
            fetched=c.run(["git","-C",WORK,"fetch","--no-tags",
                   "--recurse-submodules=no",str(SRC),
                   tmp+":refs/remotes/composer/got-native-phi"],
                   check=False,timeout=180)
            if fetched.returncode:
                raise RuntimeError("PHI_RECONSTRUCTED_SOURCE_FETCH_FAILED="+
                    (fetched.stderr or b"").decode(errors="replace")[-1200:])
            subprocess.run(["git","-C",str(WORK),"checkout","--detach",rev],
                           check=True,stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,timeout=90)
        finally:
            c.git("update-ref","-d",tmp,check=False)
    if wg("rev-parse","HEAD")!=rev:
        raise RuntimeError("NATIVE_PHI_WORKDIR_REVISION_MISMATCH")
    if (WORK/"src/core/cpu_id_translation").exists():
        raise RuntimeError("DYNAMORIO_REAPPEARED_IN_WORKDIR")
    if not (WORK/"externals/sirit").is_dir():
        c.submodules_at(WORK)

def native_phi_preflight(p,info):
    # Compile original, source-owned capacity policy rather than rewriting
    # the policy as a Python mock. Verify normal and negative cases.
    code=r'''#include "shader_recompiler/ir/passes/image_phi_preflight.h"
using Shader::Optimization::CanSplitSharpImagePhi;
static_assert(CanSplitSharpImagePhi(2,true,true,1));
static_assert(CanSplitSharpImagePhi(3,true,true,2));
static_assert(!CanSplitSharpImagePhi(1,true,true,5));
static_assert(!CanSplitSharpImagePhi(4,true,true,5));
static_assert(!CanSplitSharpImagePhi(3,false,true,3));
static_assert(!CanSplitSharpImagePhi(2,true,false,3));
static_assert(!CanSplitSharpImagePhi(3,true,true,1));
int main(){return 0;}
'''
    with tempfile.TemporaryDirectory(prefix="native-gpu-phi-preflight-") as tmp:
        src=Path(tmp)/"test.cpp";src.write_text(code)
        results=[]
        for compiler in (shutil.which("g++"),shutil.which("clang++")):
            if not compiler:continue
            exe=Path(tmp)/("run-"+Path(compiler).name)
            cmd=[compiler,"-std=c++20","-O2","-Wall","-Wextra",
                 "-I"+str(WORK/"src"),str(src),"-o",str(exe)]
            comp=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
            if comp.returncode:
                raise RuntimeError("GENERIC_PHI_NATIVE_PRETEST_COMPILE_FAILED="+
                    compiler+"\n"+(comp.stdout+comp.stderr)[-3000:])
            test=subprocess.run([str(exe)],capture_output=True,text=True,timeout=20)
            if test.returncode:
                raise RuntimeError("GENERIC_PHI_NATIVE_PRETEST_FAILED="+compiler)
            results.append(Path(compiler).name)
            print("NATIVE_PHI_PRETEST_"+Path(compiler).name.upper().replace("+","P")+"=PASS",flush=True)
        if not results:
            raise RuntimeError("NO_NATIVE_CPP_COMPILER_FOR_PHI_PREFLIGHT")
        (info/"preflight.json").write_text(json.dumps({
            "status":"PASS","compilers":results,"checks":7},indent=2)+"\n")
    discovered=(WORK/p.DISCOVER).read_text()
    for marker in ("static void SplitBufferLoadBySharpPhi(",
                   "static bool SplitImageSampleBySharpPhi(",
                   "SplitBufferLoadBySharpPhi(*block, inst);",
                   "SplitImageSampleBySharpPhi(*block, inst, image_split_budget, program.info);",
                   "PeelSharpDwordMasks(", "MarkReadConstBufferSharpSources(",
                   "AnalyzeIndexedImageTable("):
        if marker not in discovered:
            raise RuntimeError("PORT_LOST_PREVIOUS_SHADER_SOURCE_FEATURE="+marker)
    if "static constexpr u32 ShaderBinaryVersion = 0xfu;" not in (
         WORK/p.CACHE).read_text():
        raise RuntimeError("NATIVE_PHI_SHADER_CACHE_EPOCH_NOT_BUMPED")
    print("NATIVE_GENERIC_PHI_PREFLIGHT=PASS",flush=True)

def build(c,p,rev,tree,folder,info):
    c.stage("configure_native_generic_gpu_phi_source")
    c.tee(["cmake","--fresh","-S",WORK,"-B",BUILD,"-G","Ninja",
      "-DCMAKE_BUILD_TYPE=Release","-DENABLE_TESTS=OFF",
      "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=ON"],
      folder/"configure.log")
    ninja=(BUILD/"build.ninja").read_text(errors="replace")
    for forbidden in ("ENABLE_CPU_ID_TRANSLATION",
        "shadps4_cpu_id_bundle","core/cpu_id_translation/launcher.cpp"):
        if forbidden in ninja:
            raise RuntimeError("EXTERNAL_TRANSLATOR_IN_NATIVE_GPU_BUILD="+forbidden)
    native_phi_preflight(p,info)
    c.stage("compile_native_generic_phi_resource_selection")
    c.tee(["nice","-n","10","cmake","--build",BUILD,
           "--target","shadps4","--parallel","2"],
           folder/"full-build.log")
    exe=c.exe(BUILD)
    if not exe:raise RuntimeError("NATIVE_GPU_PHI_BINARY_MISSING")
    sha=hash_file(exe)
    if sha==BASE_SHA:raise RuntimeError("NATIVE_PHI_SOURCE_GENERATED_OLD_BINARY")
    candidate=ROOT/"GhostOfTsushima/candidates"/(rev+"-"+sha)/"shadps4"
    candidate.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(exe,candidate)
    candidate.chmod(candidate.stat().st_mode|stat.S_IXUSR)
    if hash_file(candidate)!=sha:
        raise RuntimeError("NATIVE_PHI_BINARY_COPY_HASH_CHANGED")
    if (candidate.parent/"cpu-id-runtime").exists():
        raise RuntimeError("DYNAMORIO_RUNTIME_BUNDLED_WITH_NATIVE_CANDIDATE")
    c.assert_source_unchanged()
    report={
       "status":"NATIVE_GENERIC_GPU_PHI_BUILD_PASS",
       "revision":rev,"tree":tree,"parent":BASE_REV,
       "candidate":str(candidate),"candidate_sha256":sha,
       "native_cpu":True,"dynamorio":False,"es_de_modified":False,
       "sampler_and_mask_fixes_preserved":True,"generic_phi_recovery":True,
       "source_unchanged":True,"gameplay_verified":False,
    }
    (info/"build-result.json").write_text(json.dumps(report,indent=2)+"\n")
    print("NATIVE_GPU_PHI_BUILD=PASS",flush=True)
    print("NATIVE_GPU_PHI_CANDIDATE="+str(candidate),flush=True)
    print("NATIVE_GPU_PHI_SHA256="+sha,flush=True)
    return candidate

def run_game_with_early_frames(native,candidate,info,folder,seconds):
    done=threading.Event()
    def sample():
        try:
            env=native.prepare_display()
            t=time.monotonic()
            for point in (20,40,60,100):
                if done.wait(max(0,point-(time.monotonic()-t))):
                    return
                file=folder/("game-screen-"+str(point)+".png")
                for args in (["scrot","-o",str(file)],
                             ["import","-window","root",str(file)]):
                    if not shutil.which(args[0]):continue
                    try:
                        result=subprocess.run(args,env=env,
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            timeout=9)
                        if result.returncode==0 and file.is_file() and file.stat().st_size>1024:
                            print("GAME_SCREENSHOT="+str(file),flush=True)
                            break
                    except (OSError,subprocess.TimeoutExpired):pass
        except Exception as e:
            print("SCREENSHOT_DIAGNOSTIC_WARNING="+str(e),flush=True)
    thread=threading.Thread(target=sample,name="generic-gpu-phi-frames",daemon=True)
    thread.start()
    try:
        return native.run_game(candidate,info,folder,seconds)
    finally:
        done.set()
        thread.join(timeout=2)

def make_archive(folder):
    for name in ("configure.log","full-build.log","game-console.log"):
        f=folder/name
        if not f.is_file():continue
        with f.open("rb") as inp:
            (folder/(name+".head")).write_bytes(inp.read(768*1024))
            inp.seek(max(0,f.stat().st_size-8*1024*1024))
            (folder/(name+".tail")).write_bytes(inp.read(8*1024*1024))
    archive=Path(str(folder)+".tar.gz")
    with tarfile.open(archive,"w:gz") as tar:
        for file in folder.rglob("*"):
            if (file.is_file() and not file.is_symlink() and
                file.name not in ("configure.log","full-build.log","game-console.log")
                and file.stat().st_size<15*1024*1024):
                tar.add(file,arcname=str(file.relative_to(folder)))
    return archive

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--seconds",type=int,default=420)
    parser.add_argument("--build-only",action="store_true")
    args=parser.parse_args()
    if not 60<=args.seconds<=1200:
        parser.error("duration must be 60..1200 seconds")
    (ROOT/"logs").mkdir(parents=True,exist_ok=True)
    folder=Path(tempfile.mkdtemp(
      prefix="got-native-gpu-phi-"+time.strftime("%Y%m%d-%H%M%S")+"-",
      dir=ROOT/"logs"))
    info=folder/"info";info.mkdir()
    result=2
    try:
        c,n,p,_=load_baseline()
        require_source=c.exists(BASE_REV+"^{commit}")
        if not require_source:
            raise RuntimeError("MENU_REACHING_GENERIC_BASE_SOURCE_MISSING")
        require_reference=require_previous_generic_phi_source(c,p)
        with (ROOT/"GhostOfTsushima/.build.lock").open("a") as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError("ANOTHER_EMULATOR_BUILD_OWNS_LOCK")
            c.stage("compose_exact_generic_phi_shader_recovery")
            rev,tree=compose(c,p,*require_reference,info,folder)
            print("NATIVE_GPU_PHI_SOURCE="+rev,flush=True)
            checkout(c,rev)
            candidate=build(c,p,rev,tree,folder,info)
        if args.build_only:
            print("GAME_TEST=NOT_RUN",flush=True);result=0
        else:
            print("GOT_GAME_TEST=ORIGINAL_NATIVE_INPUTS",flush=True)
            print("At menu press X / keyboard N to Continue.",flush=True)
            rc=run_game_with_early_frames(n,candidate,info,folder,args.seconds)
            result=0 if rc==0 else 2
    except BaseException as error:
        traceback.print_exc()
        (info/"failure.txt").write_text(traceback.format_exc())
        print("NATIVE_GPU_PHI_ERROR="+str(error),flush=True)
    finally:
        print("UPLOAD_THIS="+str(make_archive(folder)),flush=True)
    return result

if __name__=="__main__":
    raise SystemExit(main())
