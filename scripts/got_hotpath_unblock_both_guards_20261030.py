#!/usr/bin/env python3
# GoT: verified one-run GPU profiler with consistent private VRAM policy.
from __future__ import annotations
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path.home()/'projects/shadps4/scripts'
SOURCE=ROOT/'got_pr5467_revert_hotpath_resume_gitblob_20261030.py'
TARGET=ROOT/'got_pr5467_revert_hotpath_profile_7g_20261030.py'
ENTRY="stage='INIT'\ntry:\n"
MARKERS=("def make_runtime(","def git_blob_sha(",
         "check(git_blob_sha(RUNNER)==BASE_RUNNER_SHA,'BASELINE_RUNNER_CHANGED')")
OLD_GUARD=(" u.MIN_VRAM=8*1024**3-128*1024**2\n"
           " result['required_free_vram_gib']=7.875\n"
           " result['vram_guard_tolerance_mib']=128")
NEW_GUARD=(" u.MIN_VRAM=7*1024**3\n"
           " result['required_free_vram_gib']=7\n"
           " result['temporary_diagnostic_profile']=True")
OLD_HELPER="bounded=out/'bounded_helper.py';bounded.write_text(helper_text)"
NEW_HELPER='''if helper_text.count('MIN_VRAM = 8 * 1024**3') != 1:
 raise RuntimeError('VERIFIED_RUNTIME_HELPER_POLICY_CHANGED')
helper_text=helper_text.replace('MIN_VRAM = 8 * 1024**3',
                                'MIN_VRAM = 7 * 1024**3',1)
# Check GPU VRAM every five seconds; leave renderer timings unperturbed.
old_init='    process = None; captures = []; frames = []; start = time.monotonic(); last = start; timed = False'
new_init='    process = None; captures = []; frames = []; start = time.monotonic(); last = start; last_vram = start; timed = False'
if helper_text.count(old_init)!=1:
 raise RuntimeError('PROFILE_HELPER_GAME_INIT_CHANGED')
helper_text=helper_text.replace(old_init,new_init,1)
old_tick='                now=time.monotonic(); elapsed=now-start'
new_tick=('                now=time.monotonic(); elapsed=now-start\\n'
          '                if now-last_vram>=5:\\n'
          '                    available=free_vram()\\n'
          '                    emit(f"PROFILE_VRAM_FREE_BYTES={available}")\\n'
          '                    if available<2*1024**3:\\n'
          "                        raise RuntimeError('PROFILE_VRAM_CRITICAL_ABORT')\\n"
          '                    last_vram=now')
if helper_text.count(old_tick)!=1:
 raise RuntimeError('PROFILE_HELPER_GAME_TICK_CHANGED')
helper_text=helper_text.replace(old_tick,new_tick,1)
bounded=out/'bounded_helper.py';bounded.write_text(helper_text)'''
OVERRIDE=r'''
_base_make_runtime=make_runtime
def make_runtime(*args,**kwargs):
    generated=_base_make_runtime(*args,**kwargs)
    before=generated.read_text()
    old_guard={old_guard!r}
    new_guard={new_guard!r}
    old_helper={old_helper!r}
    new_helper={new_helper!r}
    if before.count(old_guard)!=1 or before.count(old_helper)!=1:
        raise RuntimeError('GENERATED_PROFILER_GUARD_CONTEXT_CHANGED')
    after=before.replace(old_guard,new_guard,1).replace(old_helper,new_helper,1)
    start="python3 -u - \"$HELP\" <<'PY'\n"
    end="\nPY\nRUN\n"
    if after.count(start)!=1 or after.count(end)!=1:
        raise RuntimeError('GENERATED_PROFILER_HEREDOC_CHANGED')
    embedded=after.split(start,1)[1].rsplit(end,1)[0]
    compile(embedded,'got-timed-profile-embedded','exec')
    generated.write_text(after)
    subprocess.run(['bash','-n',str(generated)],check=True)
    print('PROFILE_GUARDS_CONSISTENT=7GiB CRITICAL_ABORT=2GiB',flush=True)
    return generated

'''
def apply(source):
    for anchor in MARKERS:
        if source.count(anchor)!=1:raise RuntimeError('EXACT_PROFILER_SOURCE_CHANGED:'+anchor)
    if source.count(ENTRY)!=1:raise RuntimeError('PROFILER_ENTRYPOINT_CHANGED')
    inserted=OVERRIDE.format(old_guard=OLD_GUARD,new_guard=NEW_GUARD,
                             old_helper=OLD_HELPER,new_helper=NEW_HELPER)
    patched=source.replace(ENTRY,inserted+ENTRY,1)
    compile(patched,str(TARGET),'exec')
    return patched

def preflight():
    with tempfile.TemporaryDirectory(prefix='.got-hotpath-check-',dir=ROOT) as temp:
        fixture=Path(temp)/'runner.sh'
        fixture.write_text('bash <<\'RUN\'\npython3 -u - "$HELP" <<\'PY\'\n'
                           "helper_text='MIN_VRAM = 8 * 1024**3'\n"
                           +OLD_HELPER+'\nif True:\n'+OLD_GUARD+'\nPY\nRUN\n')
        original=fixture.read_text()
        if original.count(OLD_HELPER)!=1 or original.count(OLD_GUARD)!=1:
            raise RuntimeError('PROFILE_TEST_FIXTURE_BROKEN')
        fixed=original.replace(OLD_HELPER,NEW_HELPER,1).replace(OLD_GUARD,NEW_GUARD,1)
        fixture.write_text(fixed)
        subprocess.run(['bash','-n',str(fixture)],check=True)
        body=fixed.split('python3 -u - "$HELP" <<\'PY\'\n',1)[1].rsplit('\nPY\nRUN\n',1)[0]
        compile(body,'profile-guard-fixture','exec')
    print('DUAL_GUARD_SYNTAX_TEST=PASS',flush=True)

def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    preflight()
    if not SOURCE.is_file() or SOURCE.is_symlink():
        raise RuntimeError('BASELINE_PROFILE_SOURCE_MISSING')
    changed=apply(SOURCE.read_text())
    if TARGET.is_symlink() or (TARGET.exists() and TARGET.read_text()!=changed):
        raise RuntimeError('PROFILE_DESTINATION_CHANGED')
    if not TARGET.exists():
        pending=TARGET.with_name(TARGET.name+'.pending')
        if pending.exists() or pending.is_symlink():raise RuntimeError('PENDING_PROFILE_EXISTS')
        pending.write_text(changed)
        pending.replace(TARGET)
    print('STAGE=TIMED_95S_TEXTURE_BIND_AND_GPU_READBACK',flush=True)
    return subprocess.call([sys.executable,'-u',str(TARGET)])

if __name__=='__main__':
    sys.exit(main())
