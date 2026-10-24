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
import threading
import tarfile
import tempfile
import time
import traceback

ROOT=Path.home()/"projects/shadps4"
SRC=ROOT/"GhostOfTsushima/source"
WORK=ROOT/"GhostOfTsushima/fresh-source/ca69b6509427-3967624"
BUILD=ROOT/"GhostOfTsushima/fresh-builds/ca69b6509427-3967624"
BASE_REV="4d7fcd69d9a173b6277a47476a9b300f6bb52cf1"
BASE_TREE="a0af016ecdf697f79d0465adf1679bea24493e9c"
BASE_SHA="5ea9183f3353d87085fb27b319bd0bdaf55ff45f7b21a7793679ffe747c1607e"
BASE_CAND=ROOT/"GhostOfTsushima/candidates"/(BASE_REV+"-"+BASE_SHA)/"shadps4"
BASE_REPORT=ROOT/"logs/got-valid-samplers-20261024-162742-wt762t7k/info/build-result.json"
COMPOSER=ROOT/"scripts/build_run_generic_large_indexed_resources_20261023.py"
COMPOSER_BLOB="eac62a2550104a7493be5464f73cc3b188780580"
NATIVE=ROOT/"scripts/build_run_got_upstream_native_20261024.py"
NATIVE_BLOB="96c6b84bde950513c237a9daec6177999fb3ef92"
PATCH=ROOT/"scripts/patches/generic-sharp-mask-recovery-20261024.patch"
PATCH_BLOB="ebf4a7759148127f1b07df6ad7ebe8ab0d3c4d98"
TEST=ROOT/"scripts/test_proven_sharp_masks_20261024.py"
TEST_BLOB="6acd7c47d31b4b3787274357c10153a37e5c889b"
SOURCE_BLOBS={
 "src/shader_recompiler/ir/passes/resource_discover_pass.cpp":
  ("14b72b0bf9fb9b432c61e08434f8e6a7aa465537","daea11b7bc2f6339ba859043a3fd6a29450268b1"),
 "src/shader_recompiler/ir/passes/resource_pass.h":
  ("d6fb3bbad80f557dd2e0c25101054349e7d000b5","7810280d121da09883af6c297e9ebbcc8da43970"),
 "src/shader_recompiler/ir/passes/resource_patching_pass.cpp":
  ("f7ee878953c884803bc7ced888b549e41949d6c3","45e63856a6b896d593a2b7d57b2e258a88197ea0"),
 "src/shader_recompiler/resource.h":
  ("00ab609901c98fbd314d6ae424e05454a3287aee","313b4743de07de317fdea9312aaac195e0f109d4"),
 "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp":
  ("8a801147c89eead5344a8934941a9115f5e1c4de","e94a3b23e7083dc584ce383b79bade399f308275"),
}
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
    composer=load(COMPOSER,COMPOSER_BLOB,"sharp_mask_generic_composer")
    native=load(NATIVE,NATIVE_BLOB,"sharp_mask_native_runtime")
    test=load(TEST,TEST_BLOB,"sharp_mask_production_test")
    if not PATCH.is_file() or blob(PATCH)!=PATCH_BLOB:
        raise RuntimeError("PINNED_SHARP_MASK_PATCH_CHANGED")
    composer.BASE=composer.git("rev-parse","HEAD").decode().strip()
    composer.BASE_STATUS=composer.source_status()
    composer.validate_source_status(composer.BASE_STATUS)
    info=json.loads(BASE_REPORT.read_text())
    check={
        "status":"VALID_NATIVE_SAMPLER_DESCRIPTORS_BUILD_PASS",
        "revision":BASE_REV,"tree":BASE_TREE,
        "candidate_sha256":BASE_SHA,"dynamorio":False,
        "es_de_changed":False,"index_sampler_validity_repaired":True,
        "native_abi_fix_preserved":True,"generic_gpu_fixes_preserved":True,
        "source_untouched":True,
    }
    for name,value in check.items():
        if info.get(name)!=value:
            raise RuntimeError("VERIFIED_MENU_REACHING_BUILD_DRIFT="+name)
    if Path(info["candidate"])!=BASE_CAND or digest(BASE_CAND)!=BASE_SHA:
        raise RuntimeError("VERIFIED_MENU_REACHING_BINARY_CHANGED")
    if composer.git("rev-parse",BASE_REV+"^{tree}").decode().strip()!=BASE_TREE:
        raise RuntimeError("VERIFIED_MENU_REACHING_TREE_CHANGED")
    for path,(expected,_) in SOURCE_BLOBS.items():
        actual=composer.git("rev-parse",BASE_REV+":"+path).decode().strip()
        if actual!=expected:
            raise RuntimeError("SHADER_SOURCE_PREIMAGE_CHANGED="+path+" "+actual)
    if any(composer.exists(BASE_REV+":"+name) for name in (
        "cmake/CpuIdTranslation.cmake",
        "src/core/cpu_id_translation/client.c",
        "src/core/cpu_id_translation/launcher.cpp")):
        raise RuntimeError("REJECTED_CPU_TRANSLATOR_IN_BASE")
    return composer,native,test

def compose(composer,test,info,folder):
    idx=folder/"native-sharp-mask.index"
    env=composer.env({"GIT_INDEX_FILE":str(idx)})
    composer.git("read-tree",BASE_REV,e=env)
    apply=subprocess.run(["git","-c","core.hooksPath=/dev/null",
         "-C",str(SRC),"apply","--cached","--whitespace=error","-"],
         env=env,input=PATCH.read_bytes(),capture_output=True)
    if apply.returncode:
        raise RuntimeError("CHECKED_GENERIC_SHARP_MASK_APPLY_FAILED="+
                            apply.stderr.decode(errors="replace")[-2500:])
    tree=composer.git("write-tree",e=env).decode().strip()
    for path,(_,expected) in SOURCE_BLOBS.items():
        actual=composer.git("rev-parse",tree+":"+path).decode().strip()
        if actual!=expected:
            raise RuntimeError("SHARP_MASK_RESULT_BLOB_CHANGED="+path+" "+actual)
    rev=composer.commit_tree(tree,BASE_REV,
         b"Restore generic exact AND masks in shader descriptor sources\n")
    changed=composer.git("diff","--name-only",BASE_REV,rev).decode().splitlines()
    if sorted(changed)!=sorted(SOURCE_BLOBS):
        raise RuntimeError("UNREVIEWED_GENERIC_MASK_SOURCE_CHANGES="+repr(changed))
    if composer.git("rev-parse",rev+"^").decode().strip()!=BASE_REV:
        raise RuntimeError("NATIVE_GENERIC_SOURCE_PARENT_CHANGED")
    if composer.git("rev-parse",rev+"^{tree}").decode().strip()!=tree:
        raise RuntimeError("NATIVE_GENERIC_SOURCE_TREE_CHANGED")
    if any(composer.exists(rev+":"+x) for x in (
        "cmake/CpuIdTranslation.cmake",
        "src/core/cpu_id_translation/client.c")):
        raise RuntimeError("DYNAMORIO_REAPPEARED")
    (info/"restored-sharp-masks.patch").write_bytes(
        composer.git("diff","--binary",BASE_REV,rev,"--",*SOURCE_BLOBS.keys()))
    (info/"source-change.json").write_text(json.dumps({
        "parent":BASE_REV,"parent_tree":BASE_TREE,
        "revision":rev,"tree":tree,"changed_files":changed,
        "masked_descriptor_words_preserved":True,
        "incompatible_shader_caches_invalidated":True,
        "unrecognized_dynamic_sources_still_rejected":True,
        "masked_and_immediate_words_no_bulk_load":True,
        "sampler_and_native_abi_fixes_preserved":True,
        "all_prior_renderer_fixes_preserved":True,
        "dynamorio":False,"es_de_modified":False,
        "guest_title_or_shader_specific_logic":False,
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

def build(composer,test,rev,tree,folder,info):
    composer.stage("configure_generic_non_null_sampler_native_build")
    composer.tee(["cmake","--fresh","-S",WORK,"-B",BUILD,"-G","Ninja",
      "-DCMAKE_BUILD_TYPE=Release","-DENABLE_TESTS=OFF",
      "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=ON"],folder/"configure.log")
    ninja=(BUILD/"build.ninja").read_text(errors="replace")
    for bad in ("ENABLE_CPU_ID_TRANSLATION","shadps4_cpu_id_bundle",
                "src/core/cpu_id_translation/launcher.cpp"):
        if bad in ninja:
            raise RuntimeError("EXTERNAL_CPU_TRANSLATION_FOUND_IN_BUILD="+bad)
    for path,(_,expected) in SOURCE_BLOBS.items():
        if blob(WORK/path)!=expected:
            raise RuntimeError("CHECKOUT_SOURCE_BLOB_CHANGED="+path)
    test.run(WORK)
    composer.stage("compile_and_link_exact_generic_sharp_masks")
    composer.tee(["nice","-n","10","cmake","--build",BUILD,
                  "--target","shadps4","--parallel","2"],
                 folder/"full-build.log")
    exe=composer.exe(BUILD)
    if not exe:
        raise RuntimeError("SHARP_MASK_NATIVE_BINARY_NOT_FOUND")
    sha=digest(exe)
    if sha==BASE_SHA:
        raise RuntimeError("SHARP_MASK_CANDIDATE_IDENTICAL_TO_BROKEN_BUILD")
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
        "status":"GENERIC_SHARP_MASK_RESTORE_BUILD_PASS",
        "revision":rev,"tree":tree,"parent":BASE_REV,
        "candidate":str(dest),"candidate_sha256":sha,
        "source_untouched":True,"dynamorio":False,
        "index_sampler_validity_repaired":True,"native_abi_fix_preserved":True,
        "generic_gpu_fixes_preserved":True,"sharp_mask_repair":True,
        "es_de_changed":False,"gameplay_verified":False,
    }
    (info/"build-result.json").write_text(json.dumps(result,indent=2)+"\n")
    print("SHARP_MASK_NATIVE_BUILD=PASS",flush=True)
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

def run_game_with_early_screenshots(native,candidate,info,folder,seconds):
    # Capture the actual menu/frame before the prior ~52-second crash without
    # ptrace, modifying renderer flags, or injecting controller input.
    finished=threading.Event()
    def snapshots():
        try:
            env=native.prepare_display()
            started=time.monotonic()
            for when in (20,40,65,110):
                if finished.wait(max(0,when-(time.monotonic()-started))):
                    return
                path=folder/("game-frame-"+str(when)+"s.png")
                for args in (["scrot","-o",str(path)],
                             ["import","-window","root",str(path)]):
                    if not shutil.which(args[0]):
                        continue
                    try:
                        out=subprocess.run(args,env=env,stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL,timeout=9)
                        if out.returncode==0 and path.is_file() and path.stat().st_size>1024:
                            print("EARLY_GAME_SCREENSHOT="+str(path),flush=True)
                            break
                    except (OSError,subprocess.TimeoutExpired):
                        pass
        except Exception as exc:
            print("EARLY_SCREENSHOT_WARNING="+str(exc),flush=True)
    thread=threading.Thread(target=snapshots,name="got-menu-evidence",
                            daemon=True)
    thread.start()
    try:
        return native.run_game(candidate,info,folder,seconds)
    finally:
        finished.set()
        thread.join(timeout=2)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds",type=int,default=420)
    ap.add_argument("--build-only",action="store_true")
    args=ap.parse_args()
    if not 60<=args.seconds<=1200:
        ap.error("seconds must be 60..1200")
    (ROOT/"logs").mkdir(parents=True,exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix="got-sharp-masks-"+
             time.strftime("%Y%m%d-%H%M%S")+"-",dir=ROOT/"logs"))
    info=folder/"info";info.mkdir()
    exitcode=2
    try:
        composer,native,test=verify_inputs()
        with (ROOT/"GhostOfTsushima/.build.lock").open("a") as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("ANOTHER_SHADPS4_BUILD_OWNS_LOCK")
            composer.stage("compose_exact_shader_mask_and_cache_epoch_recovery")
            rev,tree=compose(composer,test,info,folder)
            print("SHARP_MASK_SOURCE_REVISION="+rev,flush=True)
            checkout(composer,rev)
            candidate=build(composer,test,rev,tree,folder,info)
        if args.build_only:
            print("GAME_TEST=NOT_RUN",flush=True);exitcode=0
        else:
            print("GAME_TEST=GHOST_OF_TSUSHIMA_NATIVE_CPU",flush=True)
            print("At GoT menu, press controller X (keyboard N) to enter the world.",flush=True)
            print("DYNAMORIO=REJECTED ES_DE=UNCHANGED",flush=True)
            rc=run_game_with_early_screenshots(native,candidate,info,folder,args.seconds)
            exitcode=0 if rc==0 else 2
    except BaseException as error:
        traceback.print_exc()
        (info/"failure.txt").write_text(traceback.format_exc())
        print("SHARP_MASK_BUILD_ERROR="+str(error),flush=True)
    finally:
        print("UPLOAD_THIS="+str(archive(folder)),flush=True)
    return exitcode

if __name__=="__main__":
    raise SystemExit(main())
