#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="$HOME/projects/shadps4/GhostOfTsushima"
LOGROOT="$HOME/projects/shadps4/logs"
[[ -d "$PROJECT/tests" && -d "$PROJECT/play" ]] || { echo 'STOP: preserved GoT workspace not found'; exit 2; }
command -v python3 >/dev/null || { echo 'STOP: missing python3'; exit 2; }
command -v tar >/dev/null || { echo 'STOP: missing tar'; exit 2; }
mkdir -p "$LOGROOT"
OUT="$LOGROOT/got-cpu-parity-$(date +%Y%m%d-%H%M%S)"
[[ ! -e "$OUT" ]] || { echo 'STOP: output already exists'; exit 2; }
mkdir -p "$OUT"/{source,info}

python3 - "$PROJECT" "$OUT" <<'PY'
from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess
import sys

project, out = map(Path, sys.argv[1:])
test = project / 'tests'
candidates = [
    ('working-v31', test / 'got-v31-nptoolkit-mspace-20261013'),
    ('working-v53', test / 'got-v53-invalid-image-fallback-20261014'),
    ('working-v55', test / 'got-v55-empty-dynamic-slot-null-20261014'),
    ('new-crashing-candidate', test / 'got-generic-srt-680029f3'),
]
names = [
    'src/core/cpu_patches.cpp',
    'src/core/cpu_patches.h',
    'src/core/module.cpp',
    'src/core/cpu_id.cpp',
    'src/core/libraries/kernel/process.cpp',
    'src/core/signals.cpp',
    'src/common/logging/log.cpp',
]
manifest = {'sources':[], 'runs':[], 'captures':[]}
for tag, root in candidates:
    # Earlier GoT snapshots keep their checked-out sources at root.
    # New candidate stores them under source/.
    possible = [root, root / 'source']
    for source in possible:
        if not source.is_dir():
            continue
        got = 0
        for rel in names:
            path = source / rel
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 2_000_000:
                continue
            dest = out / 'source' / tag / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            got += 1
            manifest['sources'].append({
                'tag':tag,'file':rel,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'source_path':str(path),
            })
        if got:
            git = subprocess.run(['git','-C',str(source),'rev-parse','HEAD'],
                                 text=True, capture_output=True, timeout=5)
            manifest['captures'].append({
                'tag':tag,'source_root':str(source),'files':got,
                'git_head':git.stdout.strip() if git.returncode==0 else None,
            })
            break
    for binary in [root/'build/shadps4', project/'play/manual-v55-direct/shadps4']:
        if tag != 'working-v55' and binary.name=='shadps4' and 'play' in binary.parts:
            continue
        if binary.is_file():
            digest=hashlib.sha256()
            with binary.open('rb') as stream:
                while chunk:=stream.read(2**20):
                    digest.update(chunk)
            manifest['runs'].append({'tag':tag,'binary':str(binary),
                                    'sha256':digest.hexdigest()})
            if 'play' in binary.parts:
                break

# Extract just the patcher and early-module evidence, not the full game log.
logs = sorted((Path.home()/'projects/shadps4/logs').glob('got-v55-manual-*/logs/game-stdout.log'))
logs += sorted((Path.home()/'projects/shadps4/logs').glob('got-generic-auto-*/game.log'))
for path in logs[-12:]:
    if not path.is_file():
        continue
    label = path.parent.parent.name if path.name=='game-stdout.log' else path.parent.name
    dest = out / 'info' / f'{label}-relevant.log'
    pattern = re.compile(r'Static CPU patching for|sceKernelLoadStartModule:|Loading libSceMsgDialog|SHAD_NATIVE_STACK_BEGIN|Unhandled native fault')
    matches = []
    with path.open('r',errors='replace') as f:
        for line in f:
            if pattern.search(line):
                matches.append(re.sub(r'\x1b\[[0-9;]*m','',line)[:750])
            if len(matches) >= 40:
                break
    dest.write_text(''.join(matches))
manifest['source_file_count']=len(manifest['sources'])
(out/'info'/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
if not any(item['file']=='src/core/cpu_patches.cpp' and item['tag'].startswith('working-')
           for item in manifest['sources']):
    raise SystemExit('STOP: original CPU patcher source not found; no archive generated')
print('BASELINE_SOURCE_FILES='+str(len(manifest['sources'])))
print('ORIGINAL_SOURCE_CAPTURED=YES')
PY
tar -czf "$OUT.tar.gz" -C "$OUT" source info
printf 'SOURCE_ARCHIVE=%s.tar.gz\n' "$OUT"
printf 'GAME_NOT_STARTED=YES\nBUILDS_AND_SAVES=UNCHANGED\nSSH=UNCHANGED\n'
