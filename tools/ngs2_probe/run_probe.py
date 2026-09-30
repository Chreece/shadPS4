#!/usr/bin/env python3
"""Run the separate diagnostic core and keep a small, live metadata report."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import subprocess
import sys

HOME = Path('/home/chreece')
DEST = HOME / 'Applications/shadps4/releases/ngs2-probe'
STATE = HOME / '.local/state/shadps4-ngs2-probe'
USER = HOME / '.local/share/shadPS4'
CORE = DEST / 'shadps4'

def effective_validation():
    result = {}
    for path in (USER/'config.json', USER/'custom_configs/CUSA36843.json'):
        if path.exists():
            data = json.loads(path.read_text())
            section = data.get('Vulkan', {})
            if not isinstance(section, dict): raise ValueError(f'Invalid Vulkan section: {path}')
            result.update(section)
    return bool(result.get('vkvalidation_enabled', False))

def snapshot(report):
    path = USER/'log/shad_log.txt'
    if not path.is_file(): return
    with path.open('rb') as stream:
        first = stream.read(65536).decode('utf-8','replace')
        stream.seek(max(0,path.stat().st_size-32768))
        tail = stream.read().decode('utf-8','replace')
    wanted = ('Revision ', 'Branch ', 'vkValidation:', 'queue family:', 'Game id:', 'App Version:',
              'Audio system initialized', 'Opened audio device:', 'sceAudioOutOpen:')
    report.write('\n=== EMULATOR STARTUP SNAPSHOT ===\n')
    for line in first.splitlines():
        if any(key in line for key in wanted): report.write(line+'\n')
    report.write('\n=== RECENT FATAL EVENTS (TAIL ONLY) ===\n')
    for line in tail.splitlines():
        if any(key in line for key in ('Device lost','VK_ERROR_DEVICE_LOST','Assertion Failed','Unhandled access violation')):
            report.write(line+'\n')
    report.flush()

def main():
    if not CORE.is_file(): raise ValueError(f'Probe binary missing: {CORE}')
    if effective_validation(): raise ValueError('Validation is enabled; no config was changed and no game was launched.')
    STATE.mkdir(parents=True,exist_ok=True)
    run = STATE / ('run-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+f'-{os.getpid()}')
    run.mkdir(mode=0o700)
    latest = STATE/'latest'
    link = STATE/f'.latest-{os.getpid()}'
    link.symlink_to(run, target_is_directory=True)
    if latest.exists() and not latest.is_symlink(): raise ValueError('latest exists and is not a symlink')
    os.replace(link,latest)
    env = os.environ.copy()
    env['SHADPS4_NGS2_TRACE']='1'
    with (run/'report.txt').open('w', buffering=1) as report, (run/'console.txt').open('w') as console:
        report.write('=== BOUNDED NGS2 METADATA TRACE ===\n')
        report.write('SOURCE_COMMIT='+ (DEST/'source-commit.txt').read_text().strip()+'\n')
        with CORE.open('rb') as binary:
            report.write('BINARY_SHA256='+hashlib.file_digest(binary,'sha256').hexdigest()+'\n')
        report.write('GPU_BASE=0a7790aaa11c5ec0009cc66976bb90a8ce7078e5\n')
        report.write('PROBE_ONLY=YES; AUDIO_IMPLEMENTATION_UNCHANGED=YES\n')
        report.write('Metadata only; render samples are counted, not written to this report.\n\n')
        proc = subprocess.Popen([str(CORE),'--game','CUSA36843','--fullscreen','true'],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace',bufsize=1)
        (run/'pid').write_text(str(proc.pid)+'\n')
        written=0
        assert proc.stdout is not None
        for line in proc.stdout:
            if written < 2_000_000:
                console.write(line); written += len(line)
            if line.startswith('NGS2_PROBE '): report.write(line)
        rc=proc.wait()
        report.write(f'\nEMULATOR_RETURN_CODE={rc}\n')
        snapshot(report)
        report.write('TRACE_COMPLETE=YES\n')
    return rc

if __name__ == '__main__':
    try:
        code=main()
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print(f'NGS2_PROBE_LAUNCH=FAIL: {exc}',file=sys.stderr)
        code=1
    sys.exit(code if 0<=code<=255 else 1)
