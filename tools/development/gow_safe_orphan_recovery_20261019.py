#!/usr/bin/env python3
"""Inspect and (opt in) end ONLY the orphaned 06:52 GoW test emulator.

No other PID, unrelated game, process group, Ollama, Sunshine, SSH,
game files or save files can be modified by this script.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import time
from urllib.request import urlopen

PID = 650534
EXE = (Path.home() / "projects/shadps4/GodOfWarRagnarok/tests/"
       "gow-renderer-log-hotpath-20261017/build/"
       "shadps4.fullgfx-sparse-cpu-upload-gow-sparse-upload-20261018-201012")
EARLIEST = dt.datetime.fromisoformat("2026-10-19T06:52:48+02:00").timestamp()
LATEST = dt.datetime.fromisoformat("2026-10-19T06:53:40+02:00").timestamp()


def alive():
    try:
        status=(Path("/proc")/str(PID)/"stat").read_text()
        return status.rsplit(") ",1)[1][0] not in ("Z", "X")
    except (OSError, IndexError):
        return False


def identity():
    proc=Path("/proc")/str(PID)
    if not alive():
        return "ABSENT", None
    try:
        if proc.stat().st_uid != os.getuid():
            return "OTHER_USER", None
        actual=os.readlink(proc/"exe")
        argv=[part.decode(errors="replace") for part in
              (proc/"cmdline").read_bytes().split(b"\0") if part]
        fields=(proc/"stat").read_text().rsplit(") ",1)[1].split()
        start_ticks=int(fields[19])
        boot_time=next(int(line.split()[1])
                       for line in Path("/proc/stat").read_text().splitlines()
                       if line.startswith("btime "))
        start=boot_time+start_ticks/os.sysconf("SC_CLK_TCK")
        pgid=os.getpgid(PID)
    except (OSError, ValueError, IndexError, StopIteration) as exc:
        return f"READ_ERROR={exc}", None
    valid=(actual==str(EXE) and pgid==PID and
           EARLIEST <= start <= LATEST and
           "--game" in argv and
           argv.index("--game")+1 < len(argv) and
           argv[argv.index("--game")+1]=="CUSA34384")
    info=dict(pid=PID,exe=actual,pgid=pgid,
              started_at=dt.datetime.fromtimestamp(start).astimezone().isoformat(),
              game="CUSA34384" if "CUSA34384" in argv else "OTHER")
    return ("EXACT_0652_TEST_PROCESS" if valid else "UNVERIFIED_NO_ACTION"),info


def ollama():
    try:
        with urlopen("http://127.0.0.1:7869/api/ps",timeout=5) as r:
            data=json.load(r)
        return [x.get("name") or x.get("model") for x in data.get("models",[])]
    except Exception as error:
        return "API_CHECK_FAILED="+str(error)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--cleanup",action="store_true",
                        help="SIGTERM only the exactly verified orphan test process")
    opts=parser.parse_args()
    status,detail=identity()
    print("ARCHIVED_RUN=INTERRUPTED_SIGHUP_1")
    print("TARGET_TEST_PID=650534 STATUS="+status)
    if detail:
        print("PROCESS_IDENTITY="+json.dumps(detail,sort_keys=True))
    if opts.cleanup and status=="EXACT_0652_TEST_PROCESS":
        # Strict PID, UID, image, start-time, game arguments AND PGID checked.
        # No force kill, no other emulator, and never current SSH process group.
        os.killpg(PID,signal.SIGTERM)
        for _ in range(40):
            if not alive():
                break
            time.sleep(.25)
        print("VERIFIED_TEST_GAME_AFTER_SIGTERM="+
              ("STILL_RUNNING" if alive() else "STOPPED"))
    elif opts.cleanup:
        print("NO_SIGNAL_SENT=PID_ABSENT_OR_NOT_EXACT_0652_GAME")
    else:
        print("READ_ONLY=YES")
    print("OLLAMA_LOADED_MODELS="+json.dumps(ollama(),ensure_ascii=False))
    print("SSH_SUNSHINE_AND_NONTEST_PROCESSES_UNTOUCHED=YES")


if __name__=="__main__":
    main()
