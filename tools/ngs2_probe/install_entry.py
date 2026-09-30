#!/usr/bin/env python3
from pathlib import Path
import datetime
import os
import shlex
import shutil
import subprocess
import tempfile

HOME=Path('/home/chreece')
BIN=HOME/'.local/bin'
WRAPPER=BIN/'shadps4-esde'
RUNNER=HOME/'Applications/shadps4/releases/ngs2-probe/run_probe.py'
LAUNCHER=Path('/mnt/roms-all/ps4/Red Dead Redemption [NGS2 trace].ps4')
MARKER='# NGS2_PROBE_DISPATCH_V1'

def atomic_write(path, text, mode):
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream: stream.write(text)
        os.chmod(tmp,mode)
        if path==WRAPPER: subprocess.run(['bash','-n',tmp],check=True)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def main():
    if WRAPPER.is_symlink() or not WRAPPER.is_file():
        raise RuntimeError('Expected existing regular shadps4-esde wrapper; nothing replaced')
    source=WRAPPER.read_text()
    subprocess.run(['bash','-n',str(WRAPPER)],check=True)
    atomic_write(RUNNER,Path(__file__).with_name('run_probe.py').read_text(),0o755)
    if MARKER not in source:
        stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        backup=WRAPPER.with_name(WRAPPER.name+'.before-ngs2-probe.'+stamp)
        if backup.exists(): raise RuntimeError(f'Backup already exists: {backup}')
        shutil.copy2(WRAPPER,backup)
        text='''#!/usr/bin/env bash
# NGS2_PROBE_DISPATCH_V1
_ngs2_dispatch() {
    local token=""
    if [[ -f "${1:-}" ]]; then
        IFS= read -r token < "$1" || true
        token="${token%$'\\r'}"
    fi
    if [[ "$token" == "CUSA36843|ngs2probe" ]]; then
        python3 RUNNER "$@"
    else
        bash BACKUP "$@"
    fi
}
_ngs2_dispatch "$@"
'''.replace('RUNNER',shlex.quote(str(RUNNER))).replace('BACKUP',shlex.quote(str(backup)))
        atomic_write(WRAPPER,text,source_mode(backup))
        print(f'WRAPPER_BACKUP={backup}')
    LAUNCHER.parent.mkdir(parents=True,exist_ok=True)
    atomic_write(LAUNCHER,'CUSA36843|ngs2probe\n',0o644)
    print('NGS2_TRACE_ENTRY=READY')

def source_mode(path): return path.stat().st_mode & 0o777
if __name__=='__main__': main()
