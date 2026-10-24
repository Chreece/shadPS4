#!/usr/bin/env python3
"""Collect existing native GoT crash stack and Mesa build identity.

No game relaunch, ptrace, compilation, source modification, Vulkan override,
DynamoRIO, driver installation, ES-DE change, or raw core upload.
"""
from __future__ import annotations
import hashlib, json, os, re, shlex, shutil, subprocess, tarfile, tempfile, traceback
from pathlib import Path

ROOT=Path.home()/"projects/shadps4"
REV="f5abd9511cb3b38ba6b1eea5bbc84d5476b0677c"
SHA="8b5089b0b6aab5485f55b2aa1e1da53c560e1f3a16b22c327f651131f2dfe096"
BIN=ROOT/"GhostOfTsushima/candidates"/(REV+"-"+SHA)/"shadps4"
RADV=Path("/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so")
LAST=ROOT/"logs/got-untraced-gpu-maps-ssx7w81a"
LIMIT=1700000

def capture(argv,out,timeout=25):
    if not shutil.which(argv[0]):
        out.write_text("TOOL_MISSING="+argv[0]+"\n")
        return 127,""
    try:
        p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
        text="EXIT="+str(p.returncode)+"\n"+p.stdout+"\n"+p.stderr
        out.write_text(text[-LIMIT:])
        return p.returncode,text
    except Exception as e:
        out.write_text(type(e).__name__+": "+str(e)+"\n")
        return 124,""

def verify(folder):
    with BIN.open("rb") as handle:
        value=hashlib.file_digest(handle,"sha256").hexdigest()
    if value!=SHA:raise RuntimeError("PINNED_NATIVE_BINARY_CHANGED")
    previous=LAST/"fault-analysis.json"
    if previous.is_file():
        x=json.loads(previous.read_text())
        if (x.get("mapped_object")!=str(RADV) or x.get("elf_offset")!="0x106da8"
            or x.get("fault_address")!="0x68"):
            raise RuntimeError("UNTRACED_CRASH_SIGNATURE_CHANGED")
        (folder/"crash.json").write_text(json.dumps(x,indent=2)+"\n")
    (folder/"source-identity.txt").write_text(
        "NATIVE_BINARY="+str(BIN)+"\nSHA256="+value+
        "\nDYNAMORIO=REJECTED\nES_DE=UNCHANGED\nRELAUNCH=NO\n")

def driver(folder):
    if not RADV.is_file():
        (folder/"driver-error.txt").write_text("RADV_NOT_FOUND\n")
        return
    capture(["dpkg-query","-W","mesa-vulkan-drivers","libvulkan1"],
            folder/"mesa-packages.txt")
    capture(["readelf","-n",str(RADV)],folder/"radv-build-id.txt")
    _,asm=capture(["objdump","-d","-M","intel",
                   "--start-address=0x106d90","--stop-address=0x106e10",
                   str(RADV)],folder/"radv-insn.txt")
    matched=bool(re.search(r"movdqu\s+xmm0,\s*XMMWORD PTR \[rax\+0x68\]",
                           asm,re.I))
    (folder/"driver-match.txt").write_text(
        "RADV_PREVIOUS_FAULT_INSTRUCTION="+
        ("MATCH" if matched else "UNVERIFIED_OR_DRIVER_CHANGED")+"\n")
    print("RADV_INSTRUCTION="+("MATCH" if matched else "UNVERIFIED"),flush=True)

def resolve_radv_source_line(folder):
    """Look up the exact userspace driver's source at the known crash PC."""
    note=folder/"radv-build-id.txt"
    if not note.is_file():return
    m=re.search(r"Build ID:\s*([0-9a-f]{16,64})",note.read_text(errors="replace"),re.I)
    if not m:
        print("RADV_DEBUG_SYMBOLS=BUILD_ID_UNKNOWN",flush=True)
        return
    buildid=m.group(1).lower()
    (folder/"driver-build-id.txt").write_text(buildid+"\n")
    local=Path("/usr/lib/debug/.build-id")/buildid[:2]/(buildid[2:]+".debug")
    symbol_file=local if local.is_file() else None
    downloaded=folder/"temporary-driver-debug-elf"
    if symbol_file is None and shutil.which("curl"):
        address="https://debuginfod.debian.net/buildid/"+buildid+"/debuginfo"
        # Debug symbols are passive data only, never executed. Enforce a
        # network timeout and 300 MB maximum to protect a small root FS.
        rc,_=capture(["curl","-4","-fLsS","--retry","2",
             "--connect-timeout","15","--max-time","180",
             "--max-filesize","300000000","-o",str(downloaded),address],
             folder/"debuginfod-result.txt",timeout=205)
        if rc==0 and downloaded.is_file() and downloaded.stat().st_size<300000001:
            symbol_file=downloaded
    if symbol_file is None:
        print("RADV_DEBUG_SYMBOLS=NOT_AVAILABLE",flush=True)
        return
    try:
        rc,debug_notes=capture(["readelf","-n",str(symbol_file)],
                               folder/"debug-build-id.txt",timeout=35)
        if rc or not re.search(r"Build ID:\s*"+re.escape(buildid)+r"\b",
                               debug_notes,re.I):
            print("RADV_DEBUG_SYMBOLS=BUILD_ID_MISMATCH",flush=True)
            return
        rc,line=capture(["addr2line","-f","-i","-C","-e",
                         str(symbol_file),"0x106da8"],
                        folder/"radv-crash-source.txt",timeout=60)
        if rc==0 and "??:0" not in line:
            print("RADV_CRASH_SOURCE=RESOLVED",flush=True)
            print("RADV_FUNCTION_AND_LINE="+
                  " | ".join(line.splitlines()[1:6]),flush=True)
        else:
            print("RADV_CRASH_SOURCE=DEBUG_INFO_INCOMPLETE",flush=True)
    finally:
        if downloaded.is_file():downloaded.unlink()


def existing_core(folder):
    if not shutil.which("coredumpctl"):
        print("EXISTING_CORE=COREDUMPCTL_MISSING",flush=True)
        return
    status,list_text=capture(["coredumpctl","--no-pager","list",str(BIN)],
                              folder/"core-list.txt",timeout=25)
    status2,info_text=capture(["coredumpctl","--no-pager","info",str(BIN)],
                               folder/"core-info.txt",timeout=25)
    if status or status2:
        print("EXISTING_CORE=NONE_FOUND",flush=True)
        return
    print("EXISTING_CORE=JOURNAL_ENTRY_FOUND",flush=True)
    last=list_text.strip().splitlines()[-1]
    if not re.search(r"\b(present|journal|truncated)\b",last):
        print("EXISTING_CORE=MEMORY_UNAVAILABLE",flush=True)
        return
    if not shutil.which("gdb"):
        print("CORE_BACKTRACE=GDB_NOT_INSTALLED",flush=True)
        return
    gdb_args=[
        "-batch","-ex","set pagination off",
        "-ex","set print elements 0",
        "-ex","info threads",
        "-ex","thread apply all bt 16",
        "-ex","info registers",
        "-ex","x/16i $pc-16",
    ]
    status,_=capture([
        "coredumpctl","--no-pager",
        "--debugger-arguments="+shlex.join(gdb_args),
        "debug",str(BIN)],folder/"core-backtrace.txt",timeout=150)
    print("CORE_BACKTRACE="+("CAPTURED" if status==0 else "NOT_AVAILABLE"),flush=True)

def gpu_journal(folder):
    _,t=capture(["journalctl","-k","--no-pager","--since","-4 hours"],
                folder/"journal-raw.temp",timeout=30)
    filtered=[l for l in t.splitlines()
              if re.search(r"radv|amdgpu|libvulkan_radeon|shadps4|GPU reset|segfault|trap",l,re.I)]
    (folder/"gpu-kernel-events.txt").write_text("\n".join(filtered)[-400000:])
    (folder/"journal-raw.temp").unlink(missing_ok=True)

def archive(folder):
    out=Path(str(folder)+".tar.gz")
    with tarfile.open(out,"w:gz") as t:
        for p in folder.iterdir():
            if p.is_file() and p.stat().st_size<=LIMIT:
                t.add(p,arcname=p.name)
    return out

def main():
    (ROOT/"logs").mkdir(parents=True,exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix="got-radv-postmortem-",dir=ROOT/"logs"))
    try:
        verify(folder)
        driver(folder)
        resolve_radv_source_line(folder)
        existing_core(folder)
        gpu_journal(folder)
    except BaseException:
        (folder/"error.txt").write_text(traceback.format_exc())
        print("POSTMORTEM=ERROR",flush=True)
    finally:
        print("NO_GAME_RELAUNCH=YES",flush=True)
        print("UPLOAD_THIS="+str(archive(folder)),flush=True)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
