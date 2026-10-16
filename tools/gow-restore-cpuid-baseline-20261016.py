#!/usr/bin/env python3
"""Restore the FPS-proven general shadPS4 CPU-ID client after failed A/B.

Measured same-config FPS: previous 7.183, trial 3.867 (46% lower).
Preserve previous + experimental binaries/source evidence, verify exact hashes,
then atomically restore ONLY the CPU-ID translation client and changed bundle
files. This does not change shadPS4 executable, GCN/GPU/audio code, original
game config, installed ES-DE emulator, saves, or SSH session.

The original client remains verified by its Git object id and checkpoint SHA.
Idempotent; refuses to modify files on unexpected revision or running game.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import traceback

HOME=Path.home()
ROOT=HOME/"projects/shadps4/GodOfWarRagnarok"
WORK=ROOT/"tests/main-proof-20261015-151529-3107006"
SRC=WORK/"source/src/core/cpu_id_translation/client.c"
RUNTIME=WORK/"build/cpu-id-runtime"
CLIENT=RUNTIME/"libshadps4_cpu_id.so"
GAME=WORK/"build/shadps4"
GAME_SHA="a4d93cf19f672cf8b6dc881f11564c04e8a6af3cdffeee38e8cd669286f81932"
CHECKPOINT=WORK/"preserved-before-cpuid-translation-selective"
OLD_BLOB="7087ff4fac43d771a88baea7beee7f08e33cf118"
TRIAL_BLOB="1b5edf93159b089d8a6d0b8b8ee30072633dadba"
OLD_LIB="3b56a25afb0a1faf8d37704d166cc96c30fedc26332da7df6287956ee5007f50"
TRIAL_LIB="7061c86ce303f6669342d3dcc7ff4e56f61b46634c03c85a9e46b64685dc081a"
CHECK_FILES=(
    "bin64/drrun",
    "lib64/release/libdynamorio.so",
    "lib64/release/libdrpreload.so",
    "ext/lib64/release/libdrmgr.so",
    "ext/lib64/release/libdrwrap.so",
)
LOGS=HOME/"projects/shadps4/logs"
OUT=LOGS/("gow-cpuid-rollback-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid()))
INSTALLED=HOME/"Applications/shadps4/shadps4"

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1048576),b""):
            h.update(block)
    return h.hexdigest()

def blob(data):
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()

def safe(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("required regular checkpoint file missing: "+str(path))
    return path

def identity(path):
    try:
        p=Path(path)
        return (p.stat().st_dev,p.stat().st_ino)
    except OSError:
        return None

def still_running_game():
    # Identifies either the direct native executable or a DynamoRIO instance
    # that has mapped the verified emulator binary, without sending signals.
    want=str(GAME)
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            if proc.stat().st_uid!=os.getuid():
                continue
            executable=identity(proc/"exe")
            if executable==identity(GAME):
                return int(proc.name)
            maps=(proc/"maps").read_text(errors="replace")
            if want in maps:
                return int(proc.name)
        except (OSError,PermissionError):
            continue
    return None

def existing_install_hash():
    if not INSTALLED.exists():
        return None
    if INSTALLED.is_symlink():
        return {"symlink":str(INSTALLED.resolve())}
    return {"sha256":sha(INSTALLED)} if INSTALLED.is_file() else {"non_file":True}

def atomic_file_restore(dst,raw):
    dst=safe(dst)
    flags=stat.S_IMODE(dst.stat().st_mode)
    fd,temp=tempfile.mkstemp(prefix=".cpuid-restore-",dir=dst.parent)
    try:
        os.fchmod(fd,flags)
        with os.fdopen(fd,"wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp,dst)
    finally:
        Path(temp).unlink(missing_ok=True)

def restore():
    safe(SRC)
    safe(CLIENT)
    safe(GAME)
    if sha(GAME)!=GAME_SHA:
        raise RuntimeError("rendering executable no longer matches verified A/B executable")
    saved_source=safe(CHECKPOINT/"client.c")
    saved_library=safe(CHECKPOINT/"libshadps4_cpu_id.so")
    saved_record=safe(CHECKPOINT/"manifest.json")
    record=json.loads(saved_record.read_text())
    if (blob(saved_source.read_bytes())!=OLD_BLOB or
        sha(saved_library)!=OLD_LIB or
        record.get("client_source_blob")!=OLD_BLOB or
        record.get("client_binary_sha256")!=OLD_LIB or
        record.get("emulator_sha256")!=GAME_SHA):
        raise RuntimeError("original CPU-ID client backup/provenance is invalid")
    for rel in CHECK_FILES:
        path=safe(RUNTIME/rel)
        backup=safe(CHECKPOINT/"runtime"/rel)
        expected=record.get("nonclient_runtime_checksums",{}).get(rel)
        if not expected or sha(backup)!=expected or sha(path)!=expected:
            raise RuntimeError("DynamoRIO core runtime changed unexpectedly: "+rel)

    current_source=blob(SRC.read_bytes())
    current_library=sha(CLIENT)
    if current_source not in (OLD_BLOB,TRIAL_BLOB):
        raise RuntimeError("CPU-ID source unexpectedly modified: "+current_source)
    if current_library not in (OLD_LIB,TRIAL_LIB):
        raise RuntimeError("CPU-ID bundle unexpectedly modified: "+current_library)
    if current_source==OLD_BLOB and current_library==OLD_LIB:
        return {"status":"ALREADY_RESTORED","previous_client_sha":OLD_LIB}

    running=still_running_game()
    if running is not None:
        raise RuntimeError(f"shadPS4 still running (PID {running}); close it before replacing CPU-ID runtime")
    before={"source_blob":current_source,"library_sha256":current_library}
    # Restore the binary before the source so an interrupted restore remains
    # obvious from mismatched hashes and can safely be rerun.
    if current_library!=OLD_LIB:
        atomic_file_restore(CLIENT,saved_library.read_bytes())
    if current_source!=OLD_BLOB:
        atomic_file_restore(SRC,saved_source.read_bytes())
    if blob(SRC.read_bytes())!=OLD_BLOB or sha(CLIENT)!=OLD_LIB:
        raise RuntimeError("restored CPU-ID client failed final digest verification")
    if sha(GAME)!=GAME_SHA:
        raise RuntimeError("emulator unexpectedly changed during client rollback")
    return {"status":"RESTORED_PROVEN_BASELINE",
            "from":before,
            "restored_source_blob":OLD_BLOB,
            "restored_client_sha256":OLD_LIB}

def main():
    LOGS.mkdir(parents=True,exist_ok=True)
    OUT.mkdir(parents=True,exist_ok=False,mode=0o700)
    before_install=existing_install_hash()
    result={"status":"STARTED","emulator_executable_modified":False,
            "game_settings_modified":False,"graphics_code_modified":False,
            "installed_launcher_modified":False,"SSH_session_modified":False}
    try:
        result.update(restore())
        print("CPU_ID_ROLLBACK="+result["status"],flush=True)
        print("PREVIOUS_BASELINE_FPS=7.183",flush=True)
        print("UNSUCCESSFUL_EXPERIMENT_FPS=3.867",flush=True)
        return 0
    except BaseException as exc:
        result["status"]="FAILED"
        result["error"]=repr(exc)
        (OUT/"traceback.txt").write_text(traceback.format_exc())
        print("CPU_ID_ROLLBACK=FAILED "+str(exc),flush=True)
        return 1
    finally:
        result["installed_unchanged"]=existing_install_hash()==before_install
        (OUT/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        print("RESULT="+str(OUT/"result.json"),flush=True)

if __name__=="__main__":
    raise SystemExit(main())
