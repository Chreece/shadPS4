#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reproduce Ghost's 620-flip CPU stall using installed shadPS4. No builds/patches."""
from __future__ import annotations
import importlib.util, json, os, re, subprocess, sys, tarfile, time, traceback
from datetime import datetime
from pathlib import Path
HOME=Path.home(); STAMP=datetime.now().strftime('%Y%m%d-%H%M%S')
WORK=HOME/'.cache'/('ghost-baseline-stall-'+STAMP)
OUT=HOME/('ghost-baseline-stall-'+STAMP+'.tar.gz')
BASE='f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f'
FILES=(('ghost-auto-verified-cpu-pr-code-v5-20261009.py','66c4a346580fa565d51d52698b03e957dce7796c','ca85add43cc96cc2660abf8b2f9df39e2f5af8d7'),('ghost-stall-readonly-snapshot.py','ea52533bff6eabf47507336d73a8df37dc8f7f09','61d67bb323e5dbd396c22954cf3849e6627d53bf'))
EVENTS=[]; FRAMES=[]; STATUS='not_started'; ERROR=None

def say(s):
    line=f'[{datetime.now().isoformat(timespec="seconds")}] {s}'
    EVENTS.append(line);print(line,flush=True)

def download(name,ref,blob):
    p=WORK/name
    subprocess.run(['curl','-fsSL','--retry','2','--max-time','35',f'https://raw.githubusercontent.com/Chreece/shadPS4/{ref}/scripts/{name}','-o',str(p)],check=True,timeout=55)
    actual=subprocess.check_output(['git','hash-object',str(p)],text=True).strip()
    if actual!=blob:raise RuntimeError(f'Helper integrity mismatch: {name}: {actual}')
    subprocess.run([sys.executable,'-m','py_compile',str(p)],check=True,timeout=30)
    return p

def exact_pid(m):
    try:
        hit=re.search(r'(?m)^launcher_pid=(\d+)$',(m.SESSION_DIR/'session.meta').read_text())
        pid=int(hit[1]) if hit else 0
        return pid if pid>1 and m.exact_ghost(pid) else None
    except (OSError,ValueError,TypeError):return None

def selftest():
    assert len(FILES)==2 and all(len(b)==40 for _,_,b in FILES)
    assert len(BASE)==64 and re.fullmatch(r'[0-9a-f]{64}',BASE)
    assert 0<14<155 and 100>0
    from types import SimpleNamespace
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as t:
        p=Path(t);(p/'session.meta').write_text('launcher_pid=1234\n')
        fake=SimpleNamespace(SESSION_DIR=p,exact_ghost=lambda pid:pid==1234)
        assert exact_pid(fake)==1234
        fake.exact_ghost=lambda pid:False
        assert exact_pid(fake) is None
    say('SELFTEST_PASS no compilation, no source edits, exact test-game PID only')

def main():
    global STATUS,ERROR
    if sys.argv[1:]==['--self-test']:selftest();return 0
    WORK.mkdir(parents=True,exist_ok=False)
    (WORK/'screenshots').mkdir()
    mod=None
    try:
        ctrl=download(*FILES[0]); gdb=download(*FILES[1])
        subprocess.run([sys.executable,'-I',str(gdb),'--self-test'],check=True,timeout=20)
        spec=importlib.util.spec_from_file_location('ghost_control',ctrl)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        mod.WORK=WORK;mod.SCREEN_DIR=WORK/'screenshots';mod.OUT=OUT
        if mod.emulator_pids():raise RuntimeError('An emulator is already running; refuse takeover')
        if not mod.LAUNCHER.is_file() or mod.LAUNCHER.is_symlink() or not os.access(mod.LAUNCHER,os.X_OK):raise RuntimeError('Guarded ES-DE launcher missing')
        markers=mod.LAUNCHER.read_text(errors='replace')
        if not all(x in markers for x in ('SHADPS4_SESSION_GUARD_V1','SHADPS4_DEFAULT_MAIN_V1')):raise RuntimeError('Launcher verification failed')
        if not mod.ENTRY.exists() or not mod.BINARY.is_file() or mod.checksum(mod.BINARY)!=BASE:raise RuntimeError('Existing binary/game is not the verified baseline')
        env=mod.display_probe()
        env['SHADPS4_GRAPHICS_DIAGNOSTICS']='1';env['SHADPS4_STARTUP_DIAGNOSTICS']='1'
        if 'XDG_RUNTIME_DIR' not in env and (Path('/run/user')/str(os.getuid())).is_dir():env['XDG_RUNTIME_DIR']=str(Path('/run/user')/str(os.getuid()))
        for k in ('RADV_DEBUG','SHADPS4_CPU_ID_MODE','GHOST_CPU_RIP_LOG'):env.pop(k,None)
        say('BASELINE_VERIFIED: no CPU PR merge, no CMake, no install, no RADV timing overrides')
        mod.launch_test(env);STATUS='observing'
        start=time.monotonic();prev=None;stable=None;vbstart=None;threads=set();shots=set()
        while time.monotonic()-start<155:
            elapsed=time.monotonic()-start
            if mod.SESSION_DIR is None:mod.SESSION_DIR=mod.current_session()
            pid=exact_pid(mod) if mod.SESSION_DIR else None
            if mod.GAME_PROC.poll() is not None and pid is None:
                STATUS='game_exited';say(f'GAME_EXITED after={elapsed:.1f}s rc={mod.GAME_PROC.returncode}');break
            for sec in (12,35,55,85,115,135):
                if elapsed>=sec and sec not in threads:
                    threads.add(sec)
                    if pid:mod.lwp_sample(pid,sec)
            for sec in (15,45,90,130):
                if elapsed>=sec and sec not in shots:
                    shots.add(sec)
                    try:mod.root_screenshot(env,sec)
                    except Exception as exc:say('SCREENSHOT_ERROR='+repr(exc))
            if mod.SESSION_DIR:
                frame=mod.latest_guest_vblank_flips(mod.SESSION_DIR/'runtime.log')
                if frame:
                    vb,flips=frame
                    if prev!=flips:prev=flips;stable=elapsed;vbstart=vb
                    elif pid and flips>=100 and stable is not None and elapsed-stable>=14 and vb-vbstart>=100:
                        STATUS='confirmed_stall'
                        say(f'CONFIRMED_STALL flips={flips} vblank_progress={vb-vbstart} stalled={elapsed-stable:.1f}s')
                        mod.lwp_sample(pid,int(elapsed))
                        for label in ('A','B'):
                            if label=='B':time.sleep(4)
                            if not mod.exact_ghost(pid):break
                            task=subprocess.run([sys.executable,'-I',str(gdb),'--pid',str(pid),'--outdir',str(WORK),'--label',label],capture_output=True,text=True,timeout=29)
                            say(f'GDB_{label}_RC={task.returncode}')
                            if task.stdout or task.stderr:(WORK/f'gdb-run-{label}.txt').write_text(task.stdout+task.stderr)
                        break
                    if not FRAMES or elapsed-FRAMES[-1]['seconds']>=3:FRAMES.append({'seconds':round(elapsed,1),'vblank':vb,'flips':flips})
            time.sleep(.4)
        else:STATUS='timeout_no_confirmed_stall'
    except BaseException:
        ERROR=traceback.format_exc();say('ERROR='+ERROR.splitlines()[-1])
    finally:
        if mod:
            if mod.GAME_PROC:
                try:mod.stop_launched_game()
                except Exception as e:say('TEST_GAME_CLEANUP_ERROR='+repr(e))
            for fn in (mod.collect_screenshots,mod.report_session,mod.capture_readonly_gpu_state):
                try:fn()
                except Exception as e:say('EVIDENCE_COLLECTION_ERROR='+fn.__name__+':'+repr(e))
        (WORK/'status.json').write_text(json.dumps({'status':STATUS,'error':ERROR,'baseline_sha256':BASE,'no_source_changes':True,'frames':FRAMES,'session':str(mod.SESSION_DIR) if mod and mod.SESSION_DIR else None},indent=2)+'\n')
        (WORK/'events.txt').write_text('\n'.join(EVENTS)+'\n')
        with tarfile.open(OUT,'w:gz') as f:
            for p in sorted(WORK.iterdir()):
                if p.name not in (FILES[0][0],FILES[1][0],'__pycache__'):f.add(p,arcname=p.name)
        say('UPLOAD_THIS_FILE='+str(OUT))
        say('SSH_SESSION=REMAINS_OPEN')
    return 0 if ERROR is None else 1

if __name__=='__main__':raise SystemExit(main())
