#!/usr/bin/env python3
"""One non-halting UMR wave-interface probe; no game launch or system changes.

Uses only the locally verified binary/database. Captures backend source alongside
results so an unsupported kernel interface does not require another source dump.
An empty scan is NOT evidence that faulting-wave capture works.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

PIN = '4ec6294f3204fee024adfb4aa00555c7c8973cc5'
UMR_SHA = '6c6a33ec03091783138b31d229c368f16851f4c5cf0407befe78e8a9be3951cc'
PCI = '0000:03:00.0'


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_file(path: Path, root: Path) -> bool:
    return path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve())


def classify(rc: int, output: str) -> dict:
    # Include UMR errors even when its process exits zero. Empty stdout is not PASS.
    text = re.sub(r'\x1b\[[0-9;]*m', '', output)
    errors = [line for line in text.splitlines() if re.search(
        r'error|failed|failure|unsupported|not supported|permission denied|operation not permitted|'
        r'inappropriate ioctl|invalid argument|cannot|could not|couldn.t|no such file', line, re.I)]
    pcs = list(re.finditer(r'(?:ix|reg)?SQ_WAVE_PC_LO\s*[:=]\s*(?:0x)?([0-9a-f]{8})\b', text, re.I))
    if rc in (124, 137):
        result = 'WAVE_PROBE_TIMED_OUT'
    elif rc:
        result = 'WAVE_PROBE_FAILED'
    elif errors:
        result = 'WAVE_PROBE_REPORTED_ERRORS'
    elif any(int(m.group(1), 16) for m in pcs):
        result = 'WAVE_PC_OBSERVED_NOT_A_GAME_CAPTURE'
    elif not text.strip():
        result = 'EMPTY_SCAN_COMPATIBILITY_NOT_PROVEN'
    else:
        result = 'NO_USABLE_WAVE_PC_COMPATIBILITY_NOT_PROVEN'
    return {'result': result, 'exit_code': rc, 'wave_pc_records': len(pcs),
            'diagnostic_lines': errors[:30], 'game_fault_identified': False}


def collect_context(source: Path, project: Path, out: Path, git: str) -> None:
    """Only explicitly selected source/helpers; never user profiles or keys."""
    listing = subprocess.run([git, '-C', str(source), 'ls-files', '-z', '--', 'src'],
                             capture_output=True, check=True, timeout=15).stdout
    manifest = []
    total = 0
    for raw in listing.split(b'\0'):
        if not raw:
            continue
        rel = Path(os.fsdecode(raw))
        p = source / rel
        if p.suffix not in ('.c', '.h') or not safe_file(p, source):
            continue
        if p.stat().st_size > 600_000:
            continue
        data = p.read_bytes()
        name = p.name.lower()
        if not ('wave' in name or 'gpr' in name or b'amdgpu_gprwave' in data or
                str(rel) in ('src/umr.h', 'src/app/main.c')):
            continue
        if len(manifest) >= 48 or total + len(data) > 4_000_000:
            break
        dest = out / 'umr-source' / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        manifest.append({'path': str(rel), 'sha256': hashlib.sha256(data).hexdigest()})
        total += len(data)
    (out / 'source-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    # Read the actual preserved launcher, not a guessed rewrite of a newer one.
    launcher = project / 'scripts/got-v37-descriptor-visual.sh'
    if safe_file(launcher, project) and launcher.stat().st_size < 100_000:
        dest = out / 'launcher-source'; dest.mkdir(exist_ok=True)
        shutil.copyfile(launcher, dest / launcher.name)
    # Extract only UMR command templates, not the driver binary or user memory.
    lib = Path('/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so')
    if lib.is_file() and lib.stat().st_size < 150_000_000:
        strings = re.findall(rb'[\x20-\x7e]{5,}', lib.read_bytes())
        matches = [s.decode('ascii') for s in strings if b'umr' in s.lower()]
        (out / 'mesa-umr-command-templates.txt').write_text('\n'.join(matches[:40]) + '\n')


def main() -> int:
    os.umask(0o077)
    home = Path.home()
    project = home / 'projects/shadps4/GhostOfTsushima'
    source = project / 'tools/umr'
    binary = source / 'build/src/app/umr'
    logroot = home / 'projects/shadps4/logs'
    logroot.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix='got-umr-wave-probe-' +
                              dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-', dir=logroot))
    state = {'result': 'PREFLIGHT', 'game_launched': False, 'halt_requested': False,
             'gpu_reset_requested': False, 'permission_changes': False,
             'started': dt.datetime.now().astimezone().isoformat(), 'pci': PCI}
    exit_code = 0
    try:
        if os.geteuid() == 0:
            raise RuntimeError('Run as your normal user; only the one UMR command uses sudo.')
        tools = {name: shutil.which(name) for name in ('git', 'sudo', 'timeout')}
        if not all(tools.values()):
            raise RuntimeError('Missing tool: ' + ', '.join(k for k, v in tools.items() if not v))
        if not safe_file(binary, source) or not os.access(binary, os.X_OK):
            raise RuntimeError('Missing or unsafe UMR executable')
        state['umr_sha256'] = digest(binary)
        if state['umr_sha256'] != UMR_SHA:
            raise RuntimeError('UMR binary differs from your verified archive; no probe executed')
        commit = subprocess.run([tools['git'], '-C', str(source), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        state['umr_commit'] = commit
        if commit != PIN:
            raise RuntimeError('UMR source revision differs from the verified build')
        subprocess.run([tools['git'], '-C', str(source), 'diff', '--exit-code', 'HEAD', '--', 'src', 'database'],
                       capture_output=True, check=True, timeout=15)
        if not (source / 'database/ip').is_dir():
            raise RuntimeError('Local register database missing')
        try:
            collect_context(source, project, out, tools['git'])
        except Exception as exc:
            state['context_collection_warning'] = str(exc)
        # Do not run a probe alongside a separate shadPS4 playtest.
        for p in Path('/proc').glob('[0-9]*'):
            try:
                if (p / 'exe').resolve().name.lower() == 'shadps4':
                    raise RuntimeError('A shadPS4 process is running; no probe executed')
            except (FileNotFoundError, PermissionError):
                pass
        command = [tools['sudo'], '-n', tools['timeout'], '--kill-after=3s', '25s', str(binary),
                   '--database-path', str(source / 'database'), '--by-pci', PCI,
                   '-O', 'bits,skip_gprs,no_disasm', '--waves', 'none']
        state['command'] = command
        print('WAVE_PROBE=ONE_SCAN; GAME=NOT_LAUNCHED; HALT=NOT_REQUESTED', flush=True)
        # Timeout runs under sudo too. No halt_waves: terminating this helper cannot
        # leave an SQ halt requested by us. Kernel recovery/driver settings unchanged.
        env = dict(os.environ, LC_ALL='C')
        with (out / 'waves.txt').open('wb') as output:
            run = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output,
                                 stderr=subprocess.STDOUT, cwd=out, env=env, timeout=40)
        state.update(classify(run.returncode, (out / 'waves.txt').read_text(errors='replace')))
        state['umr_unchanged'] = digest(binary) == UMR_SHA
        if not state['umr_unchanged']:
            state['result'] = 'UMR_BINARY_CHANGED_DURING_PROBE'
            exit_code = 1
        elif run.returncode or state['diagnostic_lines']:
            exit_code = 1
    except Exception as exc:
        state.update(result='PROBE_STOPPED', error=str(exc))
        exit_code = 1
    finally:
        state['finished'] = dt.datetime.now().astimezone().isoformat()
        (out / 'result.json').write_text(json.dumps(state, indent=2) + '\n')
        summary = ['RESULT=' + state['result'], 'GAME_LAUNCHED=NO', 'HALT_REQUESTED=NO']
        if 'exit_code' in state:
            summary.append('UMR_EXIT=' + str(state['exit_code']))
        if 'wave_pc_records' in state:
            summary.append('WAVE_PC_RECORDS=' + str(state['wave_pc_records']))
        if 'error' in state:
            summary.append('DETAIL=' + state['error'])
        print('\n'.join(summary), flush=True)
        archive = out.with_name(out.name + '.tar.gz')
        partial = archive.with_name(archive.name + '.partial')
        with tarfile.open(partial, 'w:gz') as tar:
            for p in sorted(out.rglob('*')):
                if p.is_file() and not p.is_symlink():
                    tar.add(p, arcname=str(p.relative_to(out)), recursive=False)
        os.replace(partial, archive)
        print('UPLOAD=' + str(archive), flush=True)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
