#!/usr/bin/env python3
"""Build exact four-file GoW permutation fix on protected checkout source revision.
Isolated Git worktree, independent pinned dependency objects, no protected binary,
save, emulator, Sunshine, SSH or Ollama mutation. No game execution.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import traceback

HOME = Path.home()
ROOT = HOME / "projects/shadps4"
GOW = ROOT / "GodOfWarRagnarok"
BASE = GOW / "gow-stage25-restored-20261011-7bpufvsl/source"
BASE_PIN = "3965a497532edb7b18027692ab04eb97fa4c145b"
PATCH_PIN = "e6ca28e7386faa0624c1421a5dc0585eeb028532"
PROTECTED_SHA = "8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
PROTECTED = GOW / "build/shadps4"
REPO_URL = "https://github.com/Chreece/shadPS4.git"
REF = "refs/gow-permutation-protected/four-file-patch"
TARGETS = {
    "src/video_core/renderer_vulkan/vk_pipeline_cache.cpp",
    "src/video_core/renderer_vulkan/vk_pipeline_cache.h",
    "src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp",
    "src/shader_recompiler/specialization.h",
}
STAMP = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
PROJECT = GOW / "tests" / ("permutation-protected-" + STAMP)
STAGE = PROJECT / "source"
BUILD = PROJECT / "build"
LOGS = ROOT / "logs"
MAIN_LOG = LOGS / ("gow-permutation-protected-build-" + STAMP + ".log")
CMAKE_LOG = LOGS / ("gow-permutation-protected-cmake-" + STAMP + ".log")
COMPILE_LOG = LOGS / ("gow-permutation-protected-compile-" + STAMP + ".log")
SUMMARY = LOGS / ("gow-permutation-protected-build-" + STAMP + ".json")
ARCHIVE = LOGS / ("gow-permutation-protected-build-" + STAMP + ".tar.gz")

def note(*args):
    msg = " ".join(map(str,args))
    print(msg,flush=True)
    with MAIN_LOG.open("a",encoding="utf-8") as out: out.write(msg+"\n")

def run(args, *, cwd=None, logfile=None, env=None, fail=True):
    cmd=list(map(str,args))
    if logfile:
        with logfile.open("w",encoding="utf-8") as output:
            p=subprocess.run(cmd,cwd=cwd,env=env,stdout=output,stderr=subprocess.STDOUT)
        if p.returncode:
            note("LAST_COMPILER_LINES_START", "\n".join(
                 logfile.read_text(errors="replace").splitlines()[-55:]),
                 "LAST_COMPILER_LINES_END")
    else:
        p=subprocess.run(cmd,cwd=cwd,env=env,capture_output=True,text=True,errors="replace")
        if p.returncode:
            note("FAILED_COMMAND",cmd[:6],"ERROR_TAIL",str(p.stdout)[-1000:]+str(p.stderr)[-1000:])
    if fail and p.returncode:
        raise RuntimeError(f"COMMAND_FAILED_EXIT_{p.returncode}: {cmd[:6]}")
    return p

def git(repo,*args):
    return run(["git","-C",repo,*args]).stdout.strip()

def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda:f.read(2**20),b""):
            h.update(part)
    return h.hexdigest()

def check_protected():
    if not PROTECTED.is_file() or PROTECTED.is_symlink():
        raise RuntimeError("PROTECTED_BINARY_MISSING_OR_SYMLINK")
    if digest(PROTECTED)!=PROTECTED_SHA:
        raise RuntimeError("PROTECTED_BINARY_SHA_MISMATCH")
    if not BASE.is_dir() or git(BASE,"rev-parse","HEAD")!=BASE_PIN:
        raise RuntimeError("ORIGINAL_SOURCE_HEAD_MISMATCH")
    note("PROTECTED_CHECKOUT_AND_BINARY_SHA=PASS")

def staging():
    if not (BASE / ".git").exists():
        raise RuntimeError("ORIGINAL_GIT_METADATA_MISSING")
    remote_args=["git","-C",BASE,"-c","gc.auto=0","-c","http.version=HTTP/1.1",
                 "fetch","--no-tags","--depth=12","--no-recurse-submodules",
                 REPO_URL,"+refs/heads/diagnostic/gow-permutation-protected-base-20261019:"+REF]
    note("FETCHING_PROTECTED_BASE_FOUR_FILE_CANDIDATE")
    run(remote_args)
    branch_tip=git(BASE,"rev-parse",REF+"^{commit}")
    if run(["git","-C",BASE,"cat-file","-e",PATCH_PIN+"^{commit}"],fail=False).returncode:
        raise RuntimeError("EXACT_FOUR_FILE_CANDIDATE_COMMIT_MISSING="+PATCH_PIN)
    if run(["git","-C",BASE,"merge-base","--is-ancestor",PATCH_PIN,branch_tip],
           fail=False).returncode:
        raise RuntimeError("EXACT_PATCH_COMMIT_NOT_ANCESTOR_OF_FETCHED_BRANCH")
    note("EXACT_FOUR_FILE_PATCH_PIN="+PATCH_PIN)
    changes=set(git(BASE,"diff","--name-only",BASE_PIN,PATCH_PIN).splitlines())
    if changes!=TARGETS:
        raise RuntimeError("FOUR_FILE_DIFF_CONTRACT_FAILED="+repr(sorted(changes)))
    note("EXACT_FOUR_SOURCE_FILES_CHANGED=PASS")
    note("CHANGED_SOURCE_FILES="+",".join(sorted(changes)))
    if PROJECT.exists():
        raise RuntimeError("ISOLATED_PROJECT_ALREADY_EXISTS")
    PROJECT.parent.mkdir(parents=True,exist_ok=True)
    run(["git","-C",BASE,"worktree","add","--detach",STAGE,PATCH_PIN])
    if git(STAGE,"rev-parse","HEAD")!=PATCH_PIN:
        raise RuntimeError("STAGED_HEAD_NOT_PINNED")
    return changes

def stage_submodules():
    # Superproject's gitlinks point to the SAME 45 source revisions as the
    # protected checkout. Archive exactly those tracked files, not the old
    # working directory, so no untracked or stale build files are copied.
    raw=subprocess.check_output(["git","-C",str(STAGE),"ls-tree","-rz","HEAD"])
    entries=[entry for entry in raw.split(b"\0") if entry.startswith(b"160000 ")]
    note("PINNED_SUBMODULES="+str(len(entries)))
    if len(entries)!=45:
        raise RuntimeError("UNEXPECTED_SUBMODULE_COUNT="+str(len(entries)))
    all_rows=[]
    for line in entries:
        md, pathbytes=line.split(b"\t",1)
        expected=md.split()[-1].decode("ascii")
        rel=os.fsdecode(pathbytes)
        source=BASE/rel
        dest=STAGE/rel
        if not source.is_dir() or source.is_symlink():
            raise RuntimeError("ORIGINAL_SUBMODULE_NOT_AVAILABLE="+rel)
        if git(source,"rev-parse","HEAD")!=expected:
            raise RuntimeError("SUBMODULE_PIN_MISMATCH="+rel)
        if dest.is_symlink():
            raise RuntimeError("STAGE_SUBMODULE_SYMLINK="+rel)
        dest.mkdir(parents=True,exist_ok=True)
        if any(dest.iterdir()):
            raise RuntimeError("STAGE_SUBMODULE_DIRECTORY_NOT_EMPTY="+rel)
        # git archive fills the EXACT tracked tree including previously missing
        # SDL3 and zstd CMake source templates.
        producer=subprocess.Popen(["git","-C",str(source),"archive","--format=tar",expected],
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        extracted=subprocess.run(["tar","-xf","-","-C",str(dest)],stdin=producer.stdout,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        producer.stdout.close()
        upstream_errors=producer.stderr.read()
        producer.wait()
        if extracted.returncode or producer.returncode:
            raise RuntimeError("PINNED_ARCHIVE_EXTRACT_FAILED="+rel+" "+repr(upstream_errors[:120]))
        run(["git","init","-q","-b","pinned",dest])
        gitdir=Path(git(source,"rev-parse","--absolute-git-dir"))
        odb=gitdir/"objects"
        if not odb.is_dir():
            raise RuntimeError("SUBMODULE_SOURCE_OBJECTS_MISSING="+rel)
        alt=dest/".git/objects/info/alternates"
        alt.parent.mkdir(parents=True,exist_ok=True)
        alt.write_text(str(odb.resolve())+"\n",encoding="utf-8")
        run(["git","-C",dest,"update-ref","refs/heads/pinned",expected])
        run(["git","-C",dest,"read-tree",expected])
        # Export-ignore paths omitted from archives can be restored exactly
        # from their pinned objects; nothing is restored in the source checkout.
        missing=subprocess.check_output(["git","-C",str(dest),"ls-files","--deleted","-z"])
        names=[os.fsdecode(x) for x in missing.split(b"\0") if x]
        for i in range(0,len(names),100):
            run(["git","-C",dest,"restore","--source=HEAD","--worktree","--",*names[i:i+100]])
        if git(dest,"rev-parse","HEAD")!=expected:
            raise RuntimeError("STAGED_SUBMODULE_PIN_FAILED="+rel)
        if git(dest,"status","--porcelain","--untracked-files=no"):
            raise RuntimeError("STAGED_SUBMODULE_TRACKED_CONTENT_NOT_CLEAN="+rel)
        note("PINNED_DEPENDENCY_READY="+rel+" restored_archive_exclusions="+str(len(names)))
        all_rows.append({"path":rel,"commit":expected,"restored_archive_exclusions":len(names)})
    for target in ("externals/sdl3/include/build_config/SDL_build_config.h.cmake",
                   "externals/sdl3/include/build_config/SDL_revision.h.cmake",
                   "externals/zstd/build/cmake/CMakeLists.txt"):
        if not (STAGE/target).is_file():
            raise RuntimeError("PREVIOUSLY_MISSING_DEPENDENCY_STILL_MISSING="+target)
    note("PINNED_SUBMODULES_AND_THREE_CMAKE_TEMPLATES=PASS")
    return all_rows

def reuse_ffmpeg():
    ffi=STAGE/"externals/ffmpeg-core"
    if not ffi.is_dir():
        raise RuntimeError("EXPECTED_FFMPEG_CORE_NOT_FOUND")
    sha=git(ffi,"rev-parse","--short","HEAD")
    candidates=[
        GOW/"build/externals"/f"ffmpeg-{sha}.zip",
        GOW/"tests/permutation-scene-20261019-201332/build/externals"/f"ffmpeg-{sha}.zip",
    ]
    target=BUILD/"externals"/f"ffmpeg-{sha}.zip"
    for src in candidates:
        if src.is_file() and src.stat().st_size>10000:
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(src,target)
            if digest(src)!=digest(target):
                raise RuntimeError("FFMPEG_PREBUILT_COPY_CHECKSUM_FAILED")
            note("REUSED_PINNED_FFMPEG_PREBUILT="+str(src))
            return True
    note("EXACT_FFMPEG_PREBUILT_NOT_LOCAL=CAN_DOWNLOAD_BY_CMAKE")
    return False

def cmake_flags():
    original=GOW/"build/CMakeCache.txt"
    opts=["-DENABLE_TESTS=OFF"]
    if original.is_file():
        for line in original.read_text(errors="replace").splitlines():
            if line.startswith(("#","//")) or "=" not in line or ":" not in line:
                continue
            kt,val=line.split("=",1)
            k,t=kt.split(":",1)
            keep=k in {"CMAKE_BUILD_TYPE","CMAKE_C_COMPILER",
                       "CMAKE_CXX_COMPILER","CMAKE_C_FLAGS","CMAKE_CXX_FLAGS"}
            keep |= k.startswith("ENABLE_") and t=="BOOL" and val in {"ON","OFF"}
            if keep and k!="ENABLE_TESTS":
                opts.append("-D"+k+"="+val)
    return opts

def configure_build():
    options=cmake_flags()
    note("CMAKE_EXPLICIT_OPTIONS="+repr(options))
    rv=run(["cmake","-S",STAGE,"-B",BUILD,"-G","Ninja",*options],
           logfile=CMAKE_LOG)
    note("ISOLATED_CMAKE_CONFIGURE=PASS")
    env=dict(os.environ,CMAKE_BUILD_PARALLEL_LEVEL="4")
    run(["cmake","--build",BUILD,"--target","shadps4","--parallel","4"],
        logfile=COMPILE_LOG,env=env)
    executable=BUILD/"shadps4"
    if not executable.is_file() or executable.stat().st_size<1024:
        raise RuntimeError("ISOLATED_FULL_BUILD_EXITED_WITHOUT_BINARY")
    note("ISOLATED_FULL_LINK_BUILD=PASS")
    note("CANDIDATE_BINARY="+str(executable))
    note("CANDIDATE_BINARY_SHA256="+digest(executable))
    return {"candidate_binary":str(executable),"candidate_sha256":digest(executable)}

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    note("=== GOW FOUR-FILE PROTECTED-BASE CANDIDATE BUILD ===")
    note("DATE="+dt.datetime.now().astimezone().isoformat())
    details={"protected_source_commit":BASE_PIN,"candidate_source_commit":PATCH_PIN,
             "protected_binary_sha256":PROTECTED_SHA,"outcome":"FAIL",
             "tested_gameplay":False,"compared_scene":False}
    code=2
    try:
        for c in ("git","cmake","ninja","tar"):
            if shutil.which(c) is None:raise RuntimeError("TOOL_NOT_INSTALLED="+c)
        check_protected()
        if shutil.disk_usage(GOW).free<6*1024**3:
            raise RuntimeError("DISK_AVAILABLE_LESS_THAN_6_GIB")
        changed=staging()
        details["changed_source_files"]=sorted(changed)
        rows=stage_submodules()
        details["submodules"]=rows
        details["ffmpeg_reused"]=reuse_ffmpeg()
        binary=configure_build()
        details.update(binary)
        details["outcome"]="PASS"
        code=0
    except Exception as exc:
        details["error"]=str(exc)
        details["exception"]=type(exc).__name__
        note("BUILD_BLOCKER="+str(exc))
    finally:
        try:
            protected_unmodified=(PROTECTED.is_file() and digest(PROTECTED)==PROTECTED_SHA)
            baseline_unmodified=(git(BASE,"rev-parse","HEAD")==BASE_PIN)
            details.update(protected_binary_unchanged=protected_unmodified,
                           original_source_head_unchanged=baseline_unmodified)
            note("PROTECTED_BINARY_SHA_UNCHANGED="+str(protected_unmodified))
            note("ORIGINAL_CHECKOUT_HEAD_UNCHANGED="+str(baseline_unmodified))
            if not protected_unmodified or not baseline_unmodified:
                code=3
        except Exception as exc:
            details["integrity_error"]=str(exc)
            code=3
        SUMMARY.write_text(json.dumps(details,indent=2)+"\n")
        with tarfile.open(ARCHIVE,"w:gz") as result:
            for file in (MAIN_LOG,CMAKE_LOG,COMPILE_LOG,SUMMARY):
                if file.is_file():
                    result.add(file,arcname="logs/"+file.name)
        note("UPLOAD_ARCHIVE="+str(ARCHIVE))
        note("GAME_LAUNCHED=NO")
        note("SSH_SESSION=REMAINS_OPEN")
        note("BUILD_EXIT_CODE="+str(code))
    return code

if __name__=="__main__":
    sys.exit(main())
