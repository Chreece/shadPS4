#!/usr/bin/env python3
"""Repair the proven native GoT Vulkan sampler descriptor fault and run the game.

The exact Mesa 25.0.7 crash was radv_UpdateDescriptorSets reading
sampler->state through a null VkSampler (radv descriptor image_info sampler).
The previous generic source composition emitted vk::Sampler{} for invalid
indexed guest S# table slots, despite VK_DESCRIPTOR_TYPE_SAMPLER requiring a
valid sampler handle. Restore cached Vulkan default samplers as in the earlier
generic reference, with one renderer cpp changed and no other emulation changes.

Uses the existing compiled native base, preserves the old candidate, the
original Git worktree, test saves and ES-DE, and never invokes DynamoRIO.
"""
from __future__ import annotations

import argparse
import fcntl
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
import time
import traceback

ROOT=Path.home()/"projects/shadps4"
SRC=ROOT/"GhostOfTsushima/source"
WORK=ROOT/"GhostOfTsushima/fresh-source/ca69b6509427-3967624"
BUILD=ROOT/"GhostOfTsushima/fresh-builds/ca69b6509427-3967624"
BASE_REV="f5abd9511cb3b38ba6b1eea5bbc84d5476b0677c"
BASE_TREE="d967e6cad04cd35c11798abb1adf8c47f6505962"
BASE_SHA="8b5089b0b6aab5485f55b2aa1e1da53c560e1f3a16b22c327f651131f2dfe096"
BASE_CAND=ROOT/"GhostOfTsushima/candidates"/(BASE_REV+"-"+BASE_SHA)/"shadps4"
BASE_REPORT=ROOT/"logs/got-restored-srt-20261024-150712-k7mozuu9/info/build-result.json"
COMPOSER=ROOT/"scripts/build_run_generic_large_indexed_resources_20261023.py"
COMPOSER_BLOB="eac62a2550104a7493be5464f73cc3b188780580"
NATIVE=ROOT/"scripts/build_run_got_upstream_native_20261024.py"
NATIVE_BLOB="96c6b84bde950513c237a9daec6177999fb3ef92"
REPAIR=ROOT/"scripts/repair_null_sampler_descriptors_20261024.py"
REPAIR_BLOB="bd8fff85ee3cbd39d7bcc23bd325e13c61b80ba8"
GAME=Path("/mnt/roms-all/ps4-games/CUSA11456/eboot.bin")
SESSION=ROOT/"test-profiles/got-generic-world-20261024"

def blob(path:Path)->str:
    buf=path.read_bytes()
    return hashlib.sha1(b"blob "+str(len(buf)).encode()+b"\0"+buf).hexdigest()

def digest(path:Path)->str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("EXPECTED_REGULAR_FILE="+str(path))
    with path.open("rb") as fp:return hashlib.file_digest(fp,"sha256").hexdigest()

def load(path,expected,name):
    if not path.is_file() or blob(path)!=expected:
        raise RuntimeError("PINNED_MODULE_BLOB_CHANGED="+str(path))
    import importlib.util
    spec=importlib.util.spec_from_file_location(name,path)
    if spec is None or spec.loader is None:
        raise RuntimeError("PYTHON_MODULE_IMPORT_FAILED="+name)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module

def verify_inputs():
    composer=load(COMPOSER,COMPOSER_BLOB,"sampler_generic_composer")
    native=load(NATIVE,NATIVE_BLOB,"sampler_native_runtime")
    repair=load(REPAIR,REPAIR_BLOB,"sampler_descriptor_repair")
    if "const AmdGpu::Sampler resolved_guest_sampler =" not in COMPOSER.read_text():
        raise RuntimeError("PERMANENT_COMPOSER_SAMPLER_FIX_MISSING")
    composer.BASE=composer.git("rev-parse","HEAD").decode().strip()
    composer.BASE_STATUS=composer.source_status()
    composer.validate_source_status(composer.BASE_STATUS)
    info=json.loads(BASE_REPORT.read_text())
    check={
        "status":"RESTORED_PROVEN_GENERIC_SRT_BUILD_PASS",
        "revision":BASE_REV,"tree":BASE_TREE,
        "candidate_sha256":BASE_SHA,"dynamorio":False,
        "es_de_changed":False,"shader_fallback_restored":True,
        "uniform_lane_restored":True,"source_unchanged":True,
    }
    for name,value in check.items():
        if info.get(name)!=value:
            raise RuntimeError("VERIFIED_NATIVE_BASE_REPORT_MISMATCH="+name)
    if Path(info["candidate"])!=BASE_CAND or digest(BASE_CAND)!=BASE_SHA:
        raise RuntimeError("VERIFIED_NATIVE_BASE_BINARY_CHANGED")
    if composer.git("rev-parse",BASE_REV+"^{tree}").decode().strip()!=BASE_TREE:
        raise RuntimeError("VERIFIED_NATIVE_BASE_TREE_CHANGED")
    if any(composer.exists(BASE_REV+":"+name) for name in (
        "cmake/CpuIdTranslation.cmake",
        "src/core/cpu_id_translation/client.c",
        "src/core/cpu_id_translation/launcher.cpp")):
        raise RuntimeError("REJECTED_CPU_TRANSLATOR_IN_BASE")
    return composer,native,repair

def compose(composer,repair,info,folder):
    idx=folder/"one-file-sampler.index"
    env=composer.env({"GIT_INDEX_FILE":str(idx)})
    composer.git("read-tree",BASE_REV,e=env)
    name=repair.PATH
    old=composer.index_text(idx,name)
    new=repair.transform(old)
    if old==new:
        raise RuntimeError("NULL_SAMPLER_REPAIR_DID_NOT_CHANGE_SOURCE")
    composer.update_index_text(idx,name,new)
    repair.verify(composer.index_text(idx,name))
    tree=composer.git("write-tree",e=env).decode().strip()
    rev=composer.commit_tree(tree,BASE_REV,
        b"Fix Vulkan mutable sampler descriptor validity for all indexed resource tables\n")
    paths=composer.git("diff","--name-only",BASE_REV,rev).decode().splitlines()
    if paths!=[name]:
        raise RuntimeError("UNEXPECTED_SOURCE_DIFF="+repr(paths))
    if composer.git("rev-parse",rev+"^").decode().strip()!=BASE_REV:
        raise RuntimeError("NATIVE_GENERIC_SOURCE_PARENT_CHANGED")
    if composer.git("rev-parse",rev+"^{tree}").decode().strip()!=tree:
        raise RuntimeError("NATIVE_GENERIC_SOURCE_TREE_CHANGED")
    if any(composer.exists(rev+":"+x) for x in (
        "cmake/CpuIdTranslation.cmake",
        "src/core/cpu_id_translation/client.c")):
        raise RuntimeError("DYNAMORIO_REAPPEARED")
    (info/"corrected-sampler.patch").write_bytes(composer.git("diff","--binary",BASE_REV,rev,"--",name))
    (info/"source-change.json").write_text(json.dumps({
        "parent":BASE_REV,"parent_tree":BASE_TREE,
        "revision":rev,"tree":tree,
        "changed_files":paths,"changes_in_renderer_only":True,
        "gpu_fixes_and_hle_abi_preserved":True,
        "legacy_guest_sampler_semantics_preserved":True,
        "cached_valid_sampler_for_unpopulated_slots":True,
        "vk_sampler_null_submitted":False,
        "dynamorio":False,"es_de_modified":False,
        "game_specific_shaders_or_ids":False
    },indent=2)+"\n")
    return rev,tree

def checkout(composer,rev):
    if WORK.is_symlink():
        raise RuntimeError("DISPOSABLE_SOURCE_IS_SYMLINK")
    if not WORK.exists():
        temp_ref="refs/shadps4-composer/restore-"+BASE_REV[:12]
        composer.git("update-ref",temp_ref,BASE_REV)
        try:
            composer.add_fresh_source(WORK,BASE_REV,temp_ref)
            composer.submodules_at(WORK)
        finally:
            composer.git("update-ref","-d",temp_ref,check=False)
    if not (WORK/".git").is_file() and not (WORK/".git").is_dir():
        raise RuntimeError("DISPOSABLE_SOURCE_NOT_A_GIT_REPOSITORY")
    def git_work(*args):
        return subprocess.check_output(["git","-C",str(WORK),*args],timeout=40).decode().strip()
    head=git_work("rev-parse","HEAD")
    if head not in (BASE_REV,rev):
        raise RuntimeError("DISPOSABLE_SOURCE_UNEXPECTED_HEAD="+head)
    if git_work("diff","--name-only","HEAD","--") or git_work("diff","--cached","--name-only"):
        raise RuntimeError("DISPOSABLE_SOURCE_HAS_TRACKED_LOCAL_EDITS")
    if head!=rev:
        temp_ref="refs/shadps4-composer/valid-sampler-"+rev[:12]
        composer.git("update-ref",temp_ref,rev)
        try:
            result=composer.run(["git","-C",WORK,"fetch","--no-tags",
              "--recurse-submodules=no",str(SRC),
              temp_ref+":refs/remotes/composer/valid-sampler"],
              check=False,timeout=300)
            if result.returncode:
                raise RuntimeError("INCREMENTAL_CANDIDATE_FETCH_FAILED="+
                    (result.stderr or b"").decode(errors="replace")[-1400:])
            subprocess.run(["git","-C",str(WORK),"checkout","--detach",rev],
                           check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
        finally:
            composer.git("update-ref","-d",temp_ref,check=False)
    if git_work("rev-parse","HEAD")!=rev:
        raise RuntimeError("INCREMENTAL_CHECKOUT_REVISION_MISMATCH")
    for bad in ("cmake/CpuIdTranslation.cmake","src/core/cpu_id_translation"):
        if (WORK/bad).exists():
            raise RuntimeError("DYNAMORIO_SOURCE_PATH_PRESENT="+bad)
    if not (WORK/"externals/sirit").is_dir():
        composer.submodules_at(WORK)

def build(composer,repair,rev,tree,folder,info):
    composer.stage("configure_generic_non_null_sampler_native_build")
    composer.tee(["cmake","--fresh","-S",WORK,"-B",BUILD,"-G","Ninja",
      "-DCMAKE_BUILD_TYPE=Release","-DENABLE_TESTS=OFF",
      "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=ON"],folder/"configure.log")
    ninja=(BUILD/"build.ninja").read_text(errors="replace")
    for bad in ("ENABLE_CPU_ID_TRANSLATION","shadps4_cpu_id_bundle",
                "src/core/cpu_id_translation/launcher.cpp"):
        if bad in ninja:
            raise RuntimeError("EXTERNAL_CPU_TRANSLATION_FOUND_IN_BUILD="+bad)
    repair.verify((WORK/repair.PATH).read_text())
    composer.stage("compile_and_link_sampler_descriptor_correctness")
    composer.tee(["nice","-n","10","cmake","--build",BUILD,
                  "--target","shadps4","--parallel","2"],
                 folder/"full-build.log")
    exe=composer.exe(BUILD)
    if not exe:
        raise RuntimeError("VALID_SAMPLER_NATIVE_BINARY_NOT_FOUND")
    sha=digest(exe)
    if sha==BASE_SHA:
        raise RuntimeError("VALID_SAMPLER_FIX_PRODUCED_UNCHANGED_BINARY")
    dest=ROOT/"GhostOfTsushima/candidates"/(rev+"-"+sha)/"shadps4"
    dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(exe,dest)
    dest.chmod(dest.stat().st_mode|stat.S_IXUSR|stat.S_IXGRP)
    if digest(dest)!=sha:
        raise RuntimeError("VERIFIED_NATIVE_SAMPLER_COPY_CHANGED")
    if (dest.parent/"cpu-id-runtime").exists():
        raise RuntimeError("REJECTED_TRANSLATOR_RUNTIME_WITH_VALID_SAMPLER_CANDIDATE")
    composer.assert_source_unchanged()
    result={
        "status":"VALID_NATIVE_SAMPLER_DESCRIPTORS_BUILD_PASS",
        "revision":rev,"tree":tree,"parent":BASE_REV,
        "candidate":str(dest),"candidate_sha256":sha,
        "source_untouched":True,"dynamorio":False,
        "index_sampler_validity_repaired":True,"native_abi_fix_preserved":True,
        "generic_gpu_fixes_preserved":True,
        "es_de_changed":False,"gameplay_verified":False,
    }
    (info/"build-result.json").write_text(json.dumps(result,indent=2)+"\n")
    print("VALID_NATIVE_SAMPLER_BUILD=PASS",flush=True)
    print("CANDIDATE="+str(dest),flush=True)
    print("CANDIDATE_SHA256="+sha,flush=True)
    return dest

def archive(folder):
    # Every full build and runtime log remains on the host. The uploaded
    # archive retains only bounded head/tail excerpts plus source evidence.
    for name in ("configure.log","full-build.log","game-console.log"):
        f=folder/name
        if not f.is_file():continue
        with f.open("rb") as stream:
            (folder/(name+".head")).write_bytes(stream.read(768*1024))
            stream.seek(max(0,f.stat().st_size-8*1024*1024))
            (folder/(name+".tail")).write_bytes(stream.read(8*1024*1024))
    out=Path(str(folder)+".tar.gz")
    with tarfile.open(out,"w:gz") as tar:
        for p in folder.rglob("*"):
            if (p.is_file() and not p.is_symlink() and
                p.name not in ("full-build.log","configure.log","game-console.log")
                and p.stat().st_size<15*1024*1024):
                tar.add(p,arcname=str(p.relative_to(folder)))
    return out

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds",type=int,default=420)
    ap.add_argument("--build-only",action="store_true")
    args=ap.parse_args()
    if not 60<=args.seconds<=1200:
        ap.error("seconds must be 60..1200")
    (ROOT/"logs").mkdir(parents=True,exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix="got-valid-samplers-"+
             time.strftime("%Y%m%d-%H%M%S")+"-",dir=ROOT/"logs"))
    info=folder/"info";info.mkdir()
    exitcode=2
    try:
        composer,native,repair=verify_inputs()
        with (ROOT/"GhostOfTsushima/.build.lock").open("a") as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("ANOTHER_SHADPS4_BUILD_OWNS_LOCK")
            composer.stage("compose_exact_single_file_native_sampler_fix")
            rev,tree=compose(composer,repair,info,folder)
            print("CORRECTED_SOURCE_REVISION="+rev,flush=True)
            checkout(composer,rev)
            candidate=build(composer,repair,rev,tree,folder,info)
        if args.build_only:
            print("GAME_TEST=NOT_RUN",flush=True);exitcode=0
        else:
            print("GAME_TEST=GHOST_OF_TSUSHIMA_NATIVE_CPU",flush=True)
            print("At GoT menu, press controller X (keyboard N) to enter the world.",flush=True)
            print("DYNAMORIO=REJECTED ES_DE=UNCHANGED",flush=True)
            rc=native.run_game(candidate,info,folder,args.seconds)
            exitcode=0 if rc==0 else 2
    except BaseException as error:
        traceback.print_exc()
        (info/"failure.txt").write_text(traceback.format_exc())
        print("VALID_SAMPLER_BUILD_ERROR="+str(error),flush=True)
    finally:
        print("UPLOAD_THIS="+str(archive(folder)),flush=True)
    return exitcode

if __name__=="__main__":
    raise SystemExit(main())
