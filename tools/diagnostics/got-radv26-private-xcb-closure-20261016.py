#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private Debian XCB development dependency closure for unmodified Mesa RADV26.

This fixes the Mesa 26.2.5 build preflight failure:
  Package 'xcb-render', required by 'xcb-randr', not found
by downloading and unpacking Debian development .debs WITHOUT installing them.
It also provides xcb-shape, another common xcb-randr dependency.

Only files under ~/projects/shadps4/GhostOfTsushima/tests/
mesa-upstream-radv-26.2.5 are created/copied. The existing Mesa 26.2.5
runner, libdrm 2.4.133 build, installed system Mesa, shadPS4 executable,
saves, Xorg and SSH sessions remain untouched. The existing runner is then
reinvoked with private build include, pkg-config and linker paths.

The runner may queue a detached worker, which creates its usual
got-upstream-radv26-*.tar.gz archive. We never claim successful
gameplay unless the runner actually reports it.
"""
from __future__ import annotations
import datetime as dt
import hashlib
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
GO_T = HOME / "projects/shadps4/GhostOfTsushima"
TREE = GO_T / "tests/mesa-upstream-radv-26.2.5"
TOOLS = TREE / "local-build-tools"
WORK = TREE / "private-xcb-closure-20261016"
DEBS = WORK / "packages"
SCRIPT = GO_T / "scripts/mesa-radv26-upstream-gpu-ab-20261015.sh"
LOGS = HOME / "projects/shadps4/logs"
OUT = LOGS / ("got-radv26-private-xcb-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
              + "-" + str(os.getpid()))
PACKAGES = {
    "xcb-render": "libxcb-render0-dev",
    "xcb-shape": "libxcb-shape0-dev",
}

def say(message):
    print("GOT_XCB " + str(message), flush=True)

def hash_file(file):
    h = hashlib.sha256()
    with file.open("rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()

def run(args, *, logfile=None, cwd=None, env=None, timeout=300, check=True):
    args = [str(x) for x in args]
    if logfile:
        with Path(logfile).open("wb") as f:
            completed = subprocess.run(
                args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
        if check and completed.returncode:
            raise RuntimeError("Failed "+repr(args[:5])+" rc="+
                               str(completed.returncode)+"\n"+
                               Path(logfile).read_text(errors="replace")[-2500:])
        return completed.returncode, ""
    proc = subprocess.run(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,
                          capture_output=True,text=True,timeout=timeout)
    if check and proc.returncode:
        raise RuntimeError("Failed "+repr(args[:5])+" rc="+
                           str(proc.returncode)+"\n"+(proc.stdout+proc.stderr)[-2500:])
    return proc.returncode,proc.stdout

def initial_checks():
    if os.geteuid()==0:
        raise RuntimeError("Run as your normal user, not sudo/root")
    if not TREE.is_dir() or TREE.is_symlink():
        raise RuntimeError("Expected existing isolated Mesa26 build workspace is missing")
    if not SCRIPT.is_file() or SCRIPT.is_symlink():
        raise RuntimeError("Existing GoT RADV26 worker script is missing or symlinked: "+str(SCRIPT))
    if not TOOLS.is_dir() or TOOLS.is_symlink():
        raise RuntimeError("Private development-tool prefix is missing")
    for tool in ("apt-get", "dpkg-deb", "pkg-config", "bash"):
        if not shutil.which(tool):
            raise RuntimeError("Missing required build tool "+tool)
    run(["bash","-n",SCRIPT],logfile=OUT/"runner-shellcheck.log",timeout=25)
    return hash_file(SCRIPT)

def download_and_extract(module,package,records):
    package_dir=DEBS
    package_dir.mkdir(parents=True,exist_ok=True)
    # No apt install or apt update: the machine's existing signed Debian
    # package index resolves exactly the package version already selected.
    matching=[p for p in package_dir.glob(package+"_*.deb")
              if p.is_file() and not p.is_symlink()]
    if len(matching)>1:
        raise RuntimeError("Multiple cached package versions: "+package)
    if not matching:
        say("PRIVATE_APT_DOWNLOAD="+package)
        run(["apt-get","-o","Acquire::ForceIPv4=true","download",package],
            cwd=package_dir,logfile=OUT/(package+".download.log"),timeout=300)
        matching=[p for p in package_dir.glob(package+"_*.deb")
                  if p.is_file() and not p.is_symlink()]
    if len(matching)!=1:
        raise RuntimeError("Expected one Debian package archive: "+package)
    deb=matching[0]
    # With several field names dpkg-deb emits "Package: value" lines;
    # querying each field separately returns only its value.
    fields={}
    for field in ("Package","Version","Architecture"):
        fields[field]=run(["dpkg-deb","--field",deb,field],timeout=25)[1].strip()
    if fields["Package"]!=package or fields["Architecture"]!="amd64":
        raise RuntimeError("Unexpected .deb identity/architecture: "+str(fields))
    run(["dpkg-deb","-x",deb,TOOLS],
        logfile=OUT/(package+".unpack.log"),timeout=120)
    candidates=list((TOOLS/"usr").rglob(module+".pc"))
    candidates=[p for p in candidates if p.is_file() and not p.is_symlink()]
    if len(candidates)!=1:
        raise RuntimeError(f"Expected one extracted {module}.pc, got {len(candidates)}")
    records[package]={
        "deb_sha256":hash_file(deb),"version":fields["Version"],
        "pkg_config":str(candidates[0])
    }
    say("PRIVATE_DEB_EXTRACTED="+package)
    return candidates[0]

def gather_pkgconfig_dirs():
    directories=set()
    # The older worker copied xcb-randr metadata into a private directory.
    # Put its two missing transitive modules beside it, regardless of the
    # private prefix the worker chose, so the worker's own pkg-config lookup
    # resolves the same module closure on the next run.
    search=(
        TREE/"local-build-tools",
        TREE/"private-x11",
        TREE/"private-deps",
        TREE/"pkgconfig",
        TREE/"libdrm-upstream-2.4.133"/"install",
    )
    for base in search:
        if not base.is_dir() or base.is_symlink():
            continue
        for root,folder_names,file_names in os.walk(base,followlinks=False):
            folder_names[:]=[x for x in folder_names if x not in (
                ".git","build","__pycache__","meson-private","meson-logs")]
            if "xcb-randr.pc" in file_names:
                directories.add(Path(root))
    if not directories:
        raise RuntimeError("No existing private xcb-randr.pc found; refusing to guess the pkg-config prefix")
    if len(directories)>15:
        raise RuntimeError("Unexpected number of existing xcb-randr.pc installations")
    return sorted(directories)

def provide_to_existing_prefixes(metadata, directories, records):
    copied=[]
    for dest in directories:
        if dest.is_symlink() or not dest.is_dir():
            raise RuntimeError("Unsafe pkg-config destination: "+str(dest))
        randr=(dest/"xcb-randr.pc")
        if not randr.is_file() or randr.is_symlink():
            raise RuntimeError("Missing verified xcb-randr anchor in "+str(dest))
        for module,src in metadata.items():
            target=dest/(module+".pc")
            if target.is_symlink():
                raise RuntimeError("Unsafe symlink at private pkg-config target "+str(target))
            if target.exists():
                if not target.is_file():
                    raise RuntimeError("Unexpected private pkg-config item "+str(target))
                # A newer version of the Mesa script may have already fixed
                # this module and rewritten it for its own install prefix.
                # Never overwrite those existing files with generic metadata.
                copied.append({"path":str(target),"mode":"already_present"})
                continue
            shutil.copy2(src,target)
            copied.append({"path":str(target),"mode":"copied_from_private_deb"})
    records["pkgconfig_copies"]=copied

def context_paths(directories, metadata):
    env=os.environ.copy()
    pc_dirs=list(dict.fromkeys([str(p) for p in directories]+
                                [str(f.parent) for f in metadata.values()]))
    if env.get("PKG_CONFIG_PATH"):
        pc_dirs.append(env["PKG_CONFIG_PATH"])
    env["PKG_CONFIG_PATH"]=os.pathsep.join(pc_dirs)
    includes=[str(TOOLS/"usr/include")]
    libs=[str(TOOLS/"usr/lib/x86_64-linux-gnu")]
    if env.get("C_INCLUDE_PATH"):
        includes.append(env["C_INCLUDE_PATH"])
    if env.get("LIBRARY_PATH"):
        libs.append(env["LIBRARY_PATH"])
    # Compile/link search paths only, not LD_LIBRARY_PATH. The user's
    # original RADV/ICD runtime selection stays under the existing runner.
    env["C_INCLUDE_PATH"]=os.pathsep.join(includes)
    env["LIBRARY_PATH"]=os.pathsep.join(libs)
    return env

def preflight_pkgconfig(env,records):
    modules=("xcb","xcb-render","xcb-shape","xcb-randr")
    result={}
    for module in modules:
        status, version=run(["pkg-config","--modversion",module],env=env,
                            logfile=None,timeout=25,check=False)
        result[module]=version.strip() if not status else None
    records["private_pkgconfig_versions"]=result
    # If the private prefix lacks other pre-existing xcb-randr dependencies,
    # don't spend resources building Mesa; report the exact module needed.
    if not all(result.values()):
        raise RuntimeError("Private XCB closure still unresolved: "+str(result))
    say("PRIVATE_XCB_RANDR_REQUIRES_CLOSURE=PASS")

def pack(result):
    (OUT/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    archive=LOGS/(OUT.name+".tar.gz")
    with tarfile.open(archive,"w:gz",compresslevel=3) as tar:
        for file in sorted(OUT.iterdir()):
            if file.is_file() and not file.is_symlink():
                tar.add(file,arcname=OUT.name+"/"+file.name)
    say("PRECHECK_ARCHIVE="+str(archive))

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={
        "status":"STARTED","phase":"XCB_BUILD_DEPENDENCIES",
        "mesa_version":"26.2.5","libdrm_target":"2.4.133",
        "system_packages_installed":False,"system_mesa_modified":False,
        "emulator_binary_modified":False,"game_specific_shaders_modified":False,
        "SSH_session_modified":False
    }
    try:
        result["existing_runner_sha256"]=initial_checks()
        metadata={}
        packages={}
        for module,package in PACKAGES.items():
            metadata[module]=download_and_extract(module,package,packages)
        result["private_packages"]=packages
        dirs=gather_pkgconfig_dirs()
        provide_to_existing_prefixes(metadata,dirs,result)
        env=context_paths(dirs,metadata)
        preflight_pkgconfig(env,result)
        result["phase"]="CONTINUE_EXISTING_MESA_RUNNER"
        say("CONTINUE_EXISTING_RADV26_WORKER="+str(SCRIPT))
        say("SYSTEM_MESA=UNCHANGED; PRIVATE_XCB=READY")
        # The existing runner controls its own worker mode, screenshots,
        # build/gameplay archiving and the private Mesa driver selection.
        result["existing_runner_exit"],_ = run(
            ["bash",SCRIPT],env=env,logfile=OUT/"existing-runner.log",timeout=7200,
            check=False)
        result["status"]=(
            "XCB_RESOLVED_RUNNER_EXIT_0" if result["existing_runner_exit"]==0
            else "XCB_RESOLVED_RUNNER_NONZERO"
        )
        say("EXISTING_RUNNER_EXIT="+str(result["existing_runner_exit"]))
        return 0 if result["existing_runner_exit"]==0 else 1
    except BaseException as ex:
        result["status"]="FAILED"
        result["error"]=repr(ex)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        pack(result)

if __name__=="__main__":
    raise SystemExit(main())
