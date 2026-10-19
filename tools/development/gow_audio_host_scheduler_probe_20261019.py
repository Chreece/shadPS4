#!/usr/bin/env python3
"""Passive Linux scheduler probe for GoW AK::AudioOutThread.

No sudo, ptrace, signals, priorities, affinity writes, controller input,
game-file changes, or screenshots. Correlates kernel run-queue delay with
shadPS4's existing Audio3D guest-buffer delivery telemetry.
"""
import argparse
import collections
import json
import os
from pathlib import Path
import re
import statistics
import time

GAME = (Path.home() / "projects/shadps4/GodOfWarRagnarok/tests/"
        "gow-renderer-log-hotpath-20261017/build/"
        "shadps4.fullgfx-sparse-cpu-upload-gow-sparse-upload-20261018-201012")
AUDIO = re.compile(r"AUDIO_PRODUCER_TIMING port=126 .*?ready=(\d+) no_guest_buffer=(\d+)")
GUEST = re.compile(r"AUDIO_GUEST_SUBMIT port=22 .*?max_entry_gap_us=(\d+)")
INTERVAL = 0.2

def find_game():
    result = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if proc.stat().st_uid != os.getuid() or os.readlink(proc/"exe") != str(GAME):
                continue
            args = [x.decode(errors="replace") for x in
                    (proc/"cmdline").read_bytes().split(b"\0") if x]
            if "--game" not in args or args[args.index("--game")+1] != "CUSA34384":
                continue
            if (proc/"stat").read_text().rsplit(") ",1)[1][0] not in "ZX":
                result.append(int(proc.name))
        except (OSError,IndexError,ValueError):
            pass
    return result

def alive(pid):
    p = Path(f"/proc/{pid}")
    try:
        return os.readlink(p/"exe")==str(GAME) and (
            p/"stat").read_text().rsplit(") ",1)[1][0] not in "ZX"
    except (OSError,IndexError):
        return False

def task_data(pid,tid):
    p=Path(f"/proc/{pid}/task/{tid}")
    name=(p/"comm").read_text().strip()
    if not name.startswith(("AK::AudioOut","AK::BankManager","shadPS4:Audio")):
        return None
    vals=[int(x) for x in (p/"schedstat").read_text().split()[:3]]
    if len(vals)!=3:raise ValueError("INCOMPLETE_SCHEDSTAT")
    st=(p/"stat").read_text().rsplit(") ",1)[1].split()
    status=(p/"status").read_text()
    mask=re.search(r"^Cpus_allowed_list:\s*(\S+)",status,re.M)
    try:wchan=(p/"wchan").read_text().strip()
    except OSError:wchan="UNAVAILABLE"
    return dict(tid=tid,name=name,state=st[0],run_ns=vals[0],
                wait_ns=vals[1],slices=vals[2],cpu=int(st[36]),
                policy=os.sched_getscheduler(tid),
                nice=os.getpriority(os.PRIO_PROCESS,tid),
                allowed=mask.group(1) if mask else "?",
                wchan=wchan)

def console(root,started):
    files=[]
    try:
        for f in root.glob("gow-ssh-safe-*/A_DENSE_CPU_UPLOAD/full-console.log"):
            if f.stat().st_mtime >= started-10: files.append(f)
    except OSError:pass
    return max(files,key=lambda f:f.stat().st_mtime) if files else None

def read_new(path,offset):
    if path is None:return offset,[]
    try:
        with path.open("rb") as f:
            f.seek(offset)
            items=f.readlines()
            if items and not items[-1].endswith(b"\n"):
                f.seek(-len(items.pop()),os.SEEK_CUR)
            offset=f.tell()
    except OSError:return offset,[]
    events=[]
    for raw in items:
        line=raw.decode(errors="replace")
        if m:=AUDIO.search(line):
            ready,empty=map(int,m.groups())
            events.append(dict(kind="audio",ready=ready,empty=empty,
                               ready_pct=round(100*ready/max(ready+empty,1),2)))
        if m:=GUEST.search(line):
            events.append(dict(kind="guest",max_gap_ms=round(int(m.group(1))/1000,2)))
    return offset,events

def selftest():
    assert AUDIO.search("AUDIO_PRODUCER_TIMING port=126 period_us=21333 ready=80 no_guest_buffer=150")
    assert GUEST.search("AUDIO_GUEST_SUBMIT port=22 calls=256 max_entry_gap_us=131000")
    assert "AK::AudioOutThr".startswith("AK::AudioOut")
    print("GO_W_HOST_SCHED_PROBE_SELFTEST=PASS cases=3")

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--self-test",action="store_true")
    a.add_argument("--logs",type=Path)
    a.add_argument("--out",type=Path)
    a.add_argument("--wait-seconds",type=int,default=110)
    a.add_argument("--max-seconds",type=int,default=260)
    opts=a.parse_args()
    if opts.self_test:return selftest()
    if not opts.logs or not opts.out: a.error("--logs and --out required")
    opts.out.parent.mkdir(parents=True,exist_ok=True)
    started=time.time()
    t0=time.monotonic()
    found=[]
    while time.monotonic()-t0<opts.wait_seconds:
        found=find_game()
        if found:break
        time.sleep(.4)
    if len(found)!=1:
        print("NO_UNIQUE_PINNED_GOW_PROCESS="+str(found),flush=True)
        opts.out.with_suffix(".summary.json").write_text(
            json.dumps({"status":"NO_UNIQUE_GAME","pids":found})+"\n")
        return
    pid=found[0]
    print("PINNED_GOW_PROCESS_FOUND="+str(pid),flush=True)
    begin=time.monotonic();last={};states=collections.Counter()
    wchans=collections.Counter();summary=[];offset=0;con=None
    accum_run=accum_wait=largest_wait=0
    boundary=begin;last_audio_pct=None;last_guest_gap_ms=None
    saw_audio=False
    last_sched_state={}
    with opts.out.open("w") as out:
        while alive(pid) and time.monotonic()-begin<opts.max_seconds:
            now=time.monotonic()
            if con is None:con=console(opts.logs,started)
            offset,events=read_new(con,offset)
            for event in events:
                event["elapsed_s"]=round(now-begin,2)
                out.write(json.dumps(event)+"\n")
                if event["kind"]=="audio":last_audio_pct=event["ready_pct"]
                if event["kind"]=="guest":last_guest_gap_ms=event["max_gap_ms"]
            try:
                tids=[int(x.name) for x in Path(f"/proc/{pid}/task").iterdir()
                      if x.name.isdigit()]
            except OSError:break
            for tid in tids:
                try:info=task_data(pid,tid)
                except (OSError,ValueError,ProcessLookupError):continue
                if not info or not info["name"].startswith("AK::AudioOut"):continue
                saw_audio=True
                before=last.get(tid)
                if before and info["run_ns"]>=before["run_ns"] and info["wait_ns"]>=before["wait_ns"]:
                    dr=info["run_ns"]-before["run_ns"]
                    dw=info["wait_ns"]-before["wait_ns"]
                    accum_run+=dr
                    accum_wait+=dw
                    largest_wait=max(largest_wait,dw)
                last[tid]=info
                last_sched_state={key: info[key] for key in
                                  ("tid","name","policy","nice","allowed","state","cpu","wchan")}
                states[info["state"]]+=1
                wchans[info["wchan"]]+=1
                out.write(json.dumps(dict(kind="thread",elapsed_s=round(now-begin,2),**info))+"\n")
            if now-boundary>=5:
                row=dict(t=round(now-begin,2),audio_ready_pct=last_audio_pct,
                         max_guest_gap_ms=last_guest_gap_ms,
                         host_cpu_run_ms=round(accum_run/1e6,2),
                         host_runnable_wait_ms=round(accum_wait/1e6,2),
                         max_single_sample_wait_ms=round(largest_wait/1e6,2))
                summary.append(row)
                print("SCHED_5S="+json.dumps(row),flush=True)
                out.flush()
                accum_run=accum_wait=largest_wait=0;boundary=now
            time.sleep(.20)
        out.flush()
    healthy=[w for w in summary if w["audio_ready_pct"] is not None and
             w["audio_ready_pct"]>=90]
    starved=[w for w in summary if w["audio_ready_pct"] is not None and
             w["audio_ready_pct"]<=65]
    def median_ms(windows,key):
        return round(statistics.median([w[key] for w in windows]),3) if windows else None
    rq_total=sum(w["host_runnable_wait_ms"] for w in summary)
    comparison=dict(
        healthy_windows=len(healthy),starved_windows=len(starved),
        healthy_median_host_rq_wait_ms_per_5s=median_ms(healthy,"host_runnable_wait_ms"),
        starved_median_host_rq_wait_ms_per_5s=median_ms(starved,"host_runnable_wait_ms"),
        starved_max_single_sample_rq_wait_ms=max(
            (w["max_single_sample_wait_ms"] for w in starved),default=None),
        interpretation=(
            "SCHEDSTAT_ZERO_OR_DISABLED_CANNOT_EXCLUDE_HOST_SCHED_STARVATION"
            if rq_total == 0 else
            "READ_PER_WINDOW_RQ_WAIT_AND_WCHAN; DO_NOT_INFER_CAUSALITY_FROM_ONE_RUN"))
    result=dict(pid=pid,observed_AK_AudioOutThread=saw_audio,
                last_audio_thread_host_state=last_sched_state,
                starvation_vs_healthy=comparison,
                windows=summary,thread_states=dict(states),
                top_wait_channels=dict(wchans.most_common(12)),
                last_audio_ready_pct=last_audio_pct,
                last_guest_gap_ms=last_guest_gap_ms,
                console=str(con) if con else None,
                caveat="schedstat may be zero when Linux schedstats are disabled; "
                       "runqueue delay is not time blocked in futex or condition variables",
                read_only=True,changed_thread_priorities=False)
    report=opts.out.with_suffix(".summary.json")
    report.write_text(json.dumps(result,indent=2)+"\n")
    print("HOST_SCHED_REPORT="+str(report),flush=True)
    print("HOST_SCHED_RAW="+str(opts.out),flush=True)
    print("AUDIO_THREAD_OBSERVED="+str(saw_audio),flush=True)
    print("NO_GAME_OR_SCHEDULER_MODIFICATIONS=YES",flush=True)

if __name__=="__main__":
    main()
