#!/usr/bin/env python3
"""CPU-ID translation metadata: general source fix, real A/B gameplay FPS.

No game/shader-specific conditions, guest patches, GPU flags, DMA changes,
readbacks, emulator binary replacement, installed launcher changes or forced
host performance settings. Retains the proven CPUID/RDTSCP/RDPID translations.
A normal game launch can trigger autosaves; SSH remains open.

1. Verify upstream+general source and exact rendering executable.
2. Compile 8 tests against production DynamoRIO client callback in test mocks.
3. Benchmark the EXISTING auto-translated runtime (identical settings).
4. Preserve byte-exact old CPU-ID runtime and original C source.
5. Change ONLY client.c; build shadps4_cpu_id_bundle.
6. Verify emulator and non-client runtime binaries unchanged.
7. Benchmark rebuilt client under SAME binary/config/input/FPS instrumentation.
8. Keep patch when verified; rollback original client on failure.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
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
LOGS=HOME/"projects/shadps4/logs"
OUT=LOGS/("gow-cpuid-translation-ab-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
TOPIC="c8e923219a3c127b20b502ac545a5b0948c41b59"
UPSTREAM="b2d7ec7d31f9ad9426db93afe079db8679b3e1e0"
GENERAL="966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe"
BINARY=BUILD/"shadps4"
BINARY_SHA="a4d93cf19f672cf8b6dc881f11564c04e8a6af3cdffeee38e8cd669286f81932"
EARLIER=WORK/"preserved-before-generic-hotpath/shadps4"
EARLIER_SHA="a22d1eab27f1b4dc1d0bcf384d00eca7118320affe7a82ec6e15b69cec047214"
CLIENT_REL="src/core/cpu_id_translation/client.c"
CLIENT_ORIG_BLOB="7087ff4fac43d771a88baea7beee7f08e33cf118"
CLIENT_NEW_BLOB="1b5edf93159b089d8a6d0b8b8ee30072633dadba"
CLIENT=SRC/CLIENT_REL
RUNTIME=BUILD/"cpu-id-runtime"
LIB=RUNTIME/"libshadps4_cpu_id.so"
PROFILER=ROOT/"scripts/gow-generic-performance-profile.py"
PROFILER_BLOB="99e9e14d1c0775a7d1c8b7c8a68e2b89d09360fd"
PROFILER_BASE_SHA="26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0"
CHECKPOINT=WORK/"preserved-before-cpuid-translation-selective"
INSTALL=HOME/"Applications/shadps4/shadps4"
TEST_FILES={
    "tests/cpu_id/test_selective_translation.c":"f977d5c40bd34b44f1cea1f5535c22df770ce767",
    "tests/cpu_id/mock/dr_api.h":"7d1181eb204c2e2e94b038e3b55d63cf545fcaa6",
    "tests/cpu_id/mock/drmgr.h":"1c45bff0f1f4bd14df3ad462756466bf0ef56c18",
    "tests/cpu_id/mock/drwrap.h":"90c7813513e1423292579ceacfbc12c5ef27eae9",
}
PACKAGE_FILES=[
    "bin64/drrun",
    "lib64/release/libdynamorio.so",
    "lib64/release/libdrpreload.so",
    "ext/lib64/release/libdrmgr.so",
    "ext/lib64/release/libdrwrap.so",
]
FPS_SECONDS=145

def note(value):
    print("CPU_ID_AB "+str(value),flush=True)

def sha_file(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1048576),b""): h.update(block)
    return h.hexdigest()

def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()

def blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def run(args,*,log=None,env=None,cwd=None,timeout=180,check=True):
    args=[str(x) for x in args]
    if args[0]=="git":
        args=["git","-c","http.version=HTTP/1.1",*args[1:]]
    if log:
        with Path(log).open("wb") as f:
            p=subprocess.run(args,stdout=f,stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,cwd=cwd,env=env,timeout=timeout)
        if check and p.returncode:
            raise RuntimeError("command failed rc="+str(p.returncode)+": "+
                Path(log).read_text(errors="replace")[-3000:])
        return p.returncode,""
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                     stdin=subprocess.DEVNULL,cwd=cwd,env=env,
                     text=True,timeout=timeout)
    if check and p.returncode:
        raise RuntimeError("command failed rc="+str(p.returncode)+": "+
                           (p.stdout+p.stderr)[-2500:])
    return p.returncode,p.stdout

def save_json(path,value):
    Path(path).write_text(json.dumps(value,indent=2,sort_keys=True)+"\n")

def download(rel,expected):
    uri=f"https://raw.githubusercontent.com/Chreece/shadPS4/{TOPIC}/{rel}"
    fd,tmp=tempfile.mkstemp(prefix=".cpu-id-source-",dir=OUT)
    os.close(fd)
    try:
        run(["curl","-4fsSL","--retry","3","--connect-timeout","25",
             "--max-time","120",uri,"-o",tmp],timeout=360)
        data=Path(tmp).read_bytes()
        if blob(data)!=expected or not data or len(data)>2_000_000:
            raise RuntimeError("pinned upstream-based CPU-ID file checksum mismatch: "+rel)
        return data
    finally:
        Path(tmp).unlink(missing_ok=True)

def installed():
    if not INSTALL.exists():return None
    if INSTALL.is_symlink():return {"symlink":True,"target":str(INSTALL.resolve())}
    return {"sha256":sha_file(INSTALL)} if INSTALL.is_file() else {"non_file":True}

def prerequisites():
    if SRC.is_symlink() or not (SRC/".git").is_dir():
        raise RuntimeError("original upstream+general checkout missing")
    history=run(["git","rev-list","--parents","-n","1","HEAD"],cwd=SRC)[1].split()
    if len(history)!=3 or history[1:]!=[UPSTREAM,GENERAL]:
        raise RuntimeError("checkout no longer preserves main+general-fix parentage")
    if run(["git","ls-files","-u"],cwd=SRC)[1].strip():
        raise RuntimeError("unresolved source merge")
    if not BINARY.is_file() or BINARY.is_symlink() or sha_file(BINARY)!=BINARY_SHA:
        raise RuntimeError("FPS-proven rendering executable changed")
    if not EARLIER.is_file() or EARLIER.is_symlink() or sha_file(EARLIER)!=EARLIER_SHA:
        raise RuntimeError("prior rendering checkpoint unavailable")
    if not CLIENT.is_file() or CLIENT.is_symlink() or blob(CLIENT.read_bytes())!=CLIENT_ORIG_BLOB:
        raise RuntimeError("CPU-ID translation client source differs from tested upstream")
    if not LIB.is_file() or LIB.is_symlink():
        raise RuntimeError("bundled DynamoRIO translation client missing")
    for rel in PACKAGE_FILES:
        f=RUNTIME/rel
        if not f.is_file() or f.is_symlink():
            raise RuntimeError("bundled DynamoRIO runtime missing: "+rel)
    if PROFILER.is_symlink() or not PROFILER.is_file() or blob(PROFILER.read_bytes())!=PROFILER_BLOB:
        raise RuntimeError("original-settings performance capture not verified")
    if subprocess.run(["pgrep","-u",str(os.getuid()),"-x","shadps4"],
                      stdout=subprocess.DEVNULL).returncode==0:
        raise RuntimeError("shadPS4 running; close it before the automatic A/B")
    if CHECKPOINT.exists():
        if CHECKPOINT.is_symlink() or not CHECKPOINT.is_dir():
            raise RuntimeError("unsafe existing CPU-ID checkpoint")
        manifest=CHECKPOINT/"manifest.json"
        original_source=CHECKPOINT/"client.c"
        original_lib=CHECKPOINT/"libshadps4_cpu_id.so"
        if any(not p.is_file() or p.is_symlink() for p in
               (manifest,original_source,original_lib)):
            raise RuntimeError("existing CPU-ID checkpoint incomplete")
        record=json.loads(manifest.read_text())
        if (record.get("client_source_blob")!=CLIENT_ORIG_BLOB or
            record.get("emulator_sha256")!=BINARY_SHA or
            blob(original_source.read_bytes())!=CLIENT_ORIG_BLOB or
            record.get("client_binary_sha256")!=sha_file(original_lib) or
            sha_file(LIB)!=sha_file(original_lib)):
            raise RuntimeError("existing checkpoint does not match the restored CPU-ID client")
    if not shutil.which("gcc") and not shutil.which("clang"):
        raise RuntimeError("C compiler unavailable")
    return {rel:sha_file(RUNTIME/rel) for rel in PACKAGE_FILES}

def regression(new_client):
    # Build the REAL production translation client with tiny deterministic
    # DynamoRIO API stubs. Tests exact return flags and replacement PCs.
    mock=OUT/"native-test-src"
    for rel,expected in TEST_FILES.items():
        f=mock/rel
        f.parent.mkdir(parents=True,exist_ok=True)
        f.write_bytes(download(rel,expected))
    source=mock/CLIENT_REL
    source.parent.mkdir(parents=True,exist_ok=True)
    source.write_bytes(new_client)
    compiler=shutil.which("gcc") or shutil.which("clang")
    binary=OUT/"cpu-id-regression"
    note("TEST=compile eight genuine callback cases against patched client.c")
    run([compiler,"-std=c11","-O2","-Wall","-Wextra",
         "-I",mock/"tests/cpu_id/mock",
         mock/"tests/cpu_id/test_selective_translation.c",
         "-o",binary],log=OUT/"native-build.log",timeout=160)
    run([binary],log=OUT/"native-test.log",timeout=25)
    if "CPU_ID_TRANSLATION_SELECTIVE=PASS cases=8" not in (OUT/"native-test.log").read_text():
        raise RuntimeError("client API regression suite did not pass")
    note("NATIVE_TEST=PASS 8/8")

def prepare_profiler():
    # Reuse the same original-config GPU/CPU/screenshot runner that powered
    # prior ~6 FPS measurements. Only its SHA pin is updated to the current
    # approved emulator executable; this is not a game behavior toggle.
    original=PROFILER.read_text()
    if original.count(PROFILER_BASE_SHA)!=1:
        raise RuntimeError("original performance profiler binary pin format changed")
    source=original.replace(PROFILER_BASE_SHA,BINARY_SHA,1)
    dest=OUT/"profile-original-settings.py"
    dest.write_text(source)
    run([sys.executable,"-m","py_compile",dest],
        log=OUT/"profile-compile.log",timeout=45)
    return dest

def percentile(values,q):
    if not values:return None
    sorted_values=sorted(values)
    p=(len(sorted_values)-1)*q
    low=int(p)
    high=min(low+1,len(sorted_values)-1)
    return sorted_values[low]+(sorted_values[high]-sorted_values[low])*(p-low)

def fps(prefix):
    streams={}
    for name in ("guest-flips","swapchain-presents"):
        f=Path(str(prefix)+"."+name+".csv")
        if f.is_symlink() or not f.is_file() or f.stat().st_size>8000000:
            raise RuntimeError("missing native FPS stream "+str(f))
        rows=f.read_text().splitlines()
        if not rows or rows[0]!="monotonic_us":
            raise RuntimeError("unexpected FPS stream header")
        stamps=[int(x) for x in rows[1:] if x.strip()]
        if stamps!=sorted(stamps):
            raise RuntimeError("unordered FPS samples")
        streams[name]=stamps
    host=streams["swapchain-presents"]
    if len(host)<3:
        raise RuntimeError("insufficient host presentation events")
    start=host[0]
    a=start+70_000_000
    b=start+130_000_000
    result={}
    for name,stamps in streams.items():
        stable=[t for t in stamps if a<=t<=b]
        diffs=[(r-l)/1000 for l,r in zip(stable,stable[1:]) if r>l]
        worst=sorted(diffs,reverse=True)
        fraction=math.ceil(len(worst)*.01)
        one_pct=statistics.mean(worst[:max(1,fraction)]) if worst else None
        result[name]={
            "steady_fps":round(len(stable)/60,3),
            "steady_frames":len(stable),
            "median_ms":round(percentile(diffs,.5),3) if diffs else None,
            "p95_ms":round(percentile(diffs,.95),3) if diffs else None,
            "p99_ms":round(percentile(diffs,.99),3) if diffs else None,
            "one_percent_low_fps":round(1000/one_pct,3) if one_pct else None,
            "frames_over_100ms":sum(t>100 for t in diffs),
        }
    return result

def read_profile(archive):
    with tarfile.open(archive,"r:gz") as t:
        entries=[m for m in t if m.isfile() and m.name.endswith("/result.json")]
        if len(entries)!=1 or entries[0].size>150000:
            raise RuntimeError("ambiguous original-settings profiler output")
        return json.load(t.extractfile(entries[0]))

def capture(label,profiler):
    prefix=OUT/("fps-"+label)
    environment=os.environ.copy()
    environment["SHADPS4_FRAME_METRICS_PREFIX"]=str(prefix)
    environment.pop("SHADPS4_CPU_TIMING_CSV",None)
    note("CAPTURE="+label+" / original unchanged configuration / 145s")
    logfile=OUT/("gameplay-"+label+".log")
    rc,_=run([sys.executable,profiler],env=environment,log=logfile,timeout=450,check=False)
    archives=[]
    for line in logfile.read_text(errors="replace").splitlines():
        if "UPLOAD=" not in line:continue
        path=Path(line.split("UPLOAD=",1)[1].strip())
        if path.is_symlink() or not path.is_file():continue
        if path.parent.resolve()==LOGS.resolve() and path.name.startswith("gow-generic-performance-"):
            archives.append(path)
    if not archives:
        raise RuntimeError(label+" game profiler has no archive, exit="+str(rc)+
                           ": "+logfile.read_text(errors="replace")[-1800:])
    archive=archives[-1]
    info=read_profile(archive)
    if (info.get("status")!="CPU_GPU_PROFILE_CAPTURED" or
        info.get("verified_binary_unchanged") is not True or
        info.get("original_config_hashes_unchanged") is not True or
        info.get("binary_sha256")!=BINARY_SHA):
        raise RuntimeError(label+" game/config parity failed: "+str(info.get("status")))
    values=fps(prefix)
    summary={
        "fps":values,
        "resource_summary":info.get("resource_summary"),
        "event_counts":info.get("event_counts"),
        "image_count":info.get("image_count"),
        "guest_exit":info.get("guest_exit"),
        "profile_archive":str(archive),
    }
    save_json(OUT/("summary-"+label+".json"),summary)
    note("FPS_"+label+"="+str(values["guest-flips"]["steady_fps"])+
         " guest frames/s and "+str(values["guest-flips"]["one_percent_low_fps"])+
         " FPS 1% low")
    return summary

def checkpoint(original_client,nonclient):
    if CHECKPOINT.exists():
        if (CHECKPOINT/"client.c").read_bytes()!=original_client or
            sha_file(CHECKPOINT/"libshadps4_cpu_id.so")!=sha_file(LIB)):
            raise RuntimeError("existing preserved CPU-ID client changed unexpectedly")
        note("CHECKPOINT=verified previous baseline for safe retry")
        return
    CHECKPOINT.mkdir(parents=True,exist_ok=False)
    (CHECKPOINT/"client.c").write_bytes(original_client)
    shutil.copy2(LIB,CHECKPOINT/"libshadps4_cpu_id.so")
    save_json(CHECKPOINT/"manifest.json",{
        "upstream":UPSTREAM,"general":GENERAL,
        "client_source_blob":CLIENT_ORIG_BLOB,
        "client_binary_sha256":sha_file(LIB),
        "nonclient_runtime_checksums":nonclient,
        "emulator_sha256":BINARY_SHA,
    })
    if sha_file(CHECKPOINT/"libshadps4_cpu_id.so")!=sha_file(LIB):
        raise RuntimeError("CPU-ID runtime copy did not match checksum")

def patch_build(raw,nonclient):
    if blob(CLIENT.read_bytes())!=CLIENT_ORIG_BLOB:
        raise RuntimeError("CPU identity client changed during baseline A/B")
    CLIENT.write_bytes(raw)
    note("BUILD=patch only DynamoRIO client and incrementally build its runtime bundle")
    run(["cmake","--build",BUILD,"--target","shadps4_cpu_id_bundle","--parallel","4"],
        log=OUT/"client-build.log",timeout=7200)
    if blob(CLIENT.read_bytes())!=CLIENT_NEW_BLOB:
        raise RuntimeError("built source differs from expected generic correction")
    if sha_file(LIB)==sha_file(CHECKPOINT/"libshadps4_cpu_id.so"):
        raise RuntimeError("client bundle did not relink the patched CPU-ID translation library")
    if sha_file(BINARY)!=BINARY_SHA:
        raise RuntimeError("core emulator executable unexpectedly changed")
    for rel,expected in nonclient.items():
        if sha_file(RUNTIME/rel)!=expected:
            raise RuntimeError("DynamoRIO core runtime unexpectedly changed: "+rel)
    # Test the *real* rebuilt client library against its own linker deps.
    run(["ldd",LIB],log=OUT/"client-ldd.log",timeout=50)
    if "not found" in (OUT/"client-ldd.log").read_text(errors="replace"):
        raise RuntimeError("rebuilt translation client has unresolved host libraries")

def rollback():
    if not CHECKPOINT.joinpath("libshadps4_cpu_id.so").is_file():
        return False
    # File-by-file rollback without touching the emulator executable.
    shutil.copy2(CHECKPOINT/"libshadps4_cpu_id.so",LIB)
    CLIENT.write_bytes((CHECKPOINT/"client.c").read_bytes())
    return (blob(CLIENT.read_bytes())==CLIENT_ORIG_BLOB and
            sha_file(LIB)==sha_file(CHECKPOINT/"libshadps4_cpu_id.so"))

def package(result):
    save_json(OUT/"result.json",result)
    dest=LOGS/(OUT.name+".tar.gz")
    with tarfile.open(dest,"w:gz",compresslevel=3) as t:
        for file in sorted(OUT.rglob("*")):
            if (file.is_file() and not file.is_symlink() and file.name not in
                ("cpu-id-regression","libshadps4_cpu_id.so")):
                # Do not include duplicated runtime/checkpoint binaries.
                if file.is_relative_to(OUT/"native-test-src"):
                    continue
                t.add(file,arcname=OUT.name+"/"+str(file.relative_to(OUT)))
        for label in ("before","after"):
            summary=result.get(label)
            if not summary:
                continue
            archive=Path(summary.get("profile_archive",""))
            if (archive.is_file() and not archive.is_symlink() and
                archive.parent.resolve()==LOGS.resolve()):
                t.add(archive,arcname=OUT.name+"/"+label+"-gameplay-capture.tar.gz")
    note("UPLOAD="+str(dest))

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(mode=0o700,exist_ok=False)
    result={
        "status":"STARTED","phase":"PREFLIGHT","upstream_main":UPSTREAM,
        "generic_stack":GENERAL,"source_commit":TOPIC,
        "emulator_sha256":BINARY_SHA,
        "game_specific_workarounds":False,
        "dma_or_readbacks_changed":False,
        "installed_emulator_modified":False,
        "cpu_id_mode_for_both_runs":"auto (unchanged)",
        "fix_scope":"DynamoRIO client per-basic-block translation metadata",
        "proven_performance_gain":False,
    }
    install_before=installed()
    patched=False
    successful=False
    try:
        nonclient=prerequisites()
        original=CLIENT.read_bytes()
        raw=download(CLIENT_REL,CLIENT_NEW_BLOB)
        if any(x in raw for x in (b"CUSA34384",b"SHADPS4_GOW_",b"RADV_DEBUG")):
            raise RuntimeError("game-specific behavior in generic CPU identity client")
        regression(raw)
        profiler=prepare_profiler()
        result["phase"]="BEFORE"
        result["before"]=capture("before",profiler)
        if sha_file(BINARY)!=BINARY_SHA:
            raise RuntimeError("rendering binary changed during baseline")
        checkpoint(original,nonclient)
        result["phase"]="BUILD"
        # Source and runtime change only after test and earlier A/B completed.
        patched=True
        patch_build(raw,nonclient)
        result["new_client_sha256"]=sha_file(LIB)
        result["previous_client_sha256"]=sha_file(CHECKPOINT/"libshadps4_cpu_id.so")
        result["phase"]="AFTER"
        result["after"]=capture("after",profiler)
        before_fps=result["before"]["fps"]["guest-flips"]["steady_fps"]
        after_fps=result["after"]["fps"]["guest-flips"]["steady_fps"]
        result["fps_change_percent"]=round(
            (after_fps-before_fps)/max(before_fps,.001)*100,2)
        result["gcn_tests_modified"]=False
        result["audio_fixes_modified"]=False
        result["status"]="A_B_CAPTURED_VISUAL_REVIEW_AND_MORE_RUNS_NEEDED"
        successful=True
        return 0
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        return 1
    finally:
        if patched and not successful:
            try:
                result["rollback_to_previous_client"]=rollback()
            except BaseException as restore_error:
                result["rollback_error"]=repr(restore_error)
        result["emulator_binary_unchanged"]=BINARY.is_file() and sha_file(BINARY)==BINARY_SHA
        result["installed_emulator_unchanged"]=install_before==installed()
        if not result["installed_emulator_unchanged"]:
            result["status"]="FAILED_INSTALLED_EMULATOR_CHANGED"
        package(result)

if __name__=="__main__":
    raise SystemExit(main())
