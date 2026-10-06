#!/usr/bin/env python3
"""Mode-zero memory-flow diagnostic. No settings changes and no GPU readback added.

prepare builds incrementally in the already successful Docker environment.
collect packages new runtime and memory-flow logs. restore only restores the binary.
The trace cannot prove pixel visibility or identify arbitrary guest CPU reads.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
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
import time
import uuid

BASE = '1bb72db9f2f7746e1487050ecb133ebdedd87272'
BASE_SHA = 'd1e83adce1b953bfa0060aab317ec1d0d1d62e59cd1f6520a998c83c1733d9c7'
IMAGE = 'sha256:d262a22dd3f7559e01d3048b1cdc3c1e50976edd74e6bce415ec4c25b008c076'
HEADER_REF = 'c429edd219d37d4c08df1c28c1bdc27b7ef424b8'
HEADER_SHA = 'a08751ff1647512d0702b0557731696728764b9a4a38ba14ce3d43e4daa9fc05'
HEADER_SOURCE = 'tools/diagnostics/memory_flow_trace_20261006.h'
HEADER = 'src/video_core/memory_flow_trace.h'
LIVERPOOL = 'src/video_core/amdgpu/liverpool.cpp'
BUFFER = 'src/video_core/buffer_cache/buffer_cache.cpp'
HOME = Path.home()
WORK = HOME / '.cache/shadps4-clean-verified-main'
SRC = WORK / 'source'
BUILD = SRC / 'build-docker-gcc14-v3'
ROOT = WORK / 'mode0-memory-trace'
REPORT = ROOT / 'reports'
STATE = ROOT / 'state.json'
DST = HOME / 'Applications/shadps4/shadps4'
CONFIG = HOME / '.local/share/shadPS4/config.json'
LOGROOT = HOME / '.local/state/shadps4-playtest-logs'
REPO = 'https://github.com/Chreece/shadPS4.git'


def digest(p):
    h = hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''): h.update(b)
    return h.hexdigest()


def atomic(path, data):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def save(path, obj): atomic(path, (json.dumps(obj, indent=2) + '\n').encode())


def run(args, check=True):
    p = subprocess.run(list(map(str, args)), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, timeout=120)
    if check and p.returncode: raise RuntimeError(p.stderr[-3000:] or p.stdout[-3000:])
    return p


def git(*args, check=True): return run(['git', '-C', SRC, *args], check).stdout.rstrip('\n')


def clean():
    git('diff', '--exit-code'); git('diff', '--cached', '--exit-code')
    if any(x.startswith(('-', '+', 'U')) for x in git('submodule', 'status', '--recursive').splitlines()):
        raise RuntimeError('Submodules changed; no source reset performed')


def no_game():
    for p in Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try: exe = Path(os.readlink(p / 'exe').removesuffix(' (deleted)')).name.lower()
        except OSError: continue
        if exe.startswith('shadps4'):
            raise RuntimeError('Close the emulator normally first; no process was killed. PID=' + p.name)


def config_bytes():
    b = CONFIG.read_bytes()
    mode = json.loads(b).get('GPU', {}).get('readbacks_mode')
    if type(mode) is not int or mode != 0:
        raise RuntimeError('This diagnostic requires readbacks_mode=0; it will not change it')
    return b


def replace_one(s, old, new):
    if s.count(old) != 1: raise RuntimeError('Source anchor mismatch: ' + old[:100])
    return s.replace(old, new, 1)


def instrument(liverpool, buffer):
    include = '#include "video_core/memory_flow_trace.h"\n'
    liverpool = replace_one(liverpool, '#include "core/memory.h"\n',
        '#include "core/memory.h"\n' + include + '#include "video_core/buffer_cache/buffer_cache.h"\n')
    start = liverpool.index('Liverpool::Task Liverpool::ProcessGraphics(')
    end = liverpool.index('template <bool is_indirect>', start)
    part = liverpool[start:end]
    anchor = '            const PM4ItOpcode opcode = header->type3.opcode;'
    part = replace_one(part, anchor, anchor + '''
            VideoCore::MemoryFlowTrace::Packet(0, static_cast<u32>(opcode),
                dcb.first(std::min<std::size_t>(dcb.size(), count + 1)));''')
    anchor = '                const auto skip = *cond_exec->Address() == false;'
    part = replace_one(part, anchor, anchor + '''
                if (rasterizer) {
                    const auto address = std::bit_cast<VAddr>(cond_exec->Address());
                    const bool dirty = rasterizer->GetBufferCache().IsRegionGpuModified(address, 1);
                    VideoCore::MemoryFlowTrace::Record(VideoCore::MemoryFlowTrace::Kind::CondExec,
                        {address, static_cast<u64>(skip), static_cast<u64>(dirty),
                         static_cast<u64>(cond_exec->exec_count.Value())});
                }''')
    liverpool = liverpool[:start] + part + liverpool[end:]
    start = liverpool.index('Liverpool::Task Liverpool::ProcessCompute(')
    part = liverpool[start:]
    anchor = '        const PM4ItOpcode opcode = header->type3.opcode;'
    part = replace_one(part, anchor, anchor + '''
        VideoCore::MemoryFlowTrace::Packet(vqid + 1, static_cast<u32>(opcode),
            std::span<const u32>(reinterpret_cast<const u32*>(header),
                                 header->type3.NumWords() + 1));''')
    liverpool = liverpool[:start] + part
    anchor = '#include "video_core/buffer_cache/buffer_cache.h"\n'
    # Some versions include their own header by local name.
    if anchor not in buffer: anchor = '#include "buffer_cache.h"\n'
    buffer = replace_one(buffer, anchor, anchor + include)
    anchor = '        gpu_modified_ranges.Add(device_addr, size);'
    buffer = replace_one(buffer, anchor, anchor + '''
        MemoryFlowTrace::Record(MemoryFlowTrace::Kind::GpuWrite,
                                {device_addr, size, static_cast<u64>(is_texel_buffer)});''')
    anchor = '''        copies.emplace_back(total_size_bytes, addr, size);
        total_size_bytes += size;'''
    buffer = replace_one(buffer, anchor, '''        gpu_modified_ranges.ForEachInRange(addr, size, [&](VAddr start, VAddr end) {
            MemoryFlowTrace::Record(MemoryFlowTrace::Kind::UploadOverlap,
                {addr, size, start, end, static_cast<u64>(is_written)});
        });
''' + anchor)
    return {LIVERPOOL: liverpool, BUFFER: buffer}


def logged(args, label, container_name=None, cwd=None, env=None):
    path = REPORT / (label + '.log')
    print('LOG=' + str(path), flush=True)
    with path.open('ab') as f:
        p = subprocess.Popen(list(map(str, args)), stdout=f, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, cwd=cwd, env=env)
        start = time.monotonic()
        try:
            while True:
                try: rc = p.wait(timeout=20); break
                except subprocess.TimeoutExpired:
                    print(f'  {label}: running {int(time.monotonic()-start)}s', flush=True)
        except BaseException:
            if container_name: run(['docker', 'stop', '--time', '5', container_name], check=False)
            p.terminate()
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
            raise
    if rc:
        with path.open('rb') as f:
            f.seek(max(0, path.stat().st_size - 8000)); tail = f.read().decode(errors='replace')
        raise RuntimeError(label + ' failed\n' + tail)


def docker(args, label):
    name = 'shad-memflow-' + uuid.uuid4().hex[:12]
    cmd = ['docker', 'run', '--rm', '--name', name, '--user', f'{os.getuid()}:{os.getgid()}',
           '--mount', f'type=bind,source={WORK},target={WORK}', '--workdir', SRC,
           '-e', 'HOME=/tmp', '-e', 'LC_ALL=C.UTF-8', '--entrypoint', '/usr/bin/env', IMAGE, *args]
    logged(cmd, label, name)


def deploy(binary, expected):
    if digest(binary) != expected: raise RuntimeError('Cached binary hash differs')
    fd, name = tempfile.mkstemp(prefix='.shadps4-memflow-', dir=DST.parent)
    os.close(fd)
    try:
        shutil.copyfile(binary, name); os.chmod(name, 0o755)
        if digest(name) != expected: raise RuntimeError('Staged binary hash differs')
        with open(name, 'rb') as f:
            if f.read(4) != b'\x7fELF': raise RuntimeError('Not an ELF executable')
            os.fsync(f.fileno())
        os.replace(name, DST)
    finally:
        if os.path.exists(name): os.unlink(name)


def prepare():
    no_game(); original_config = config_bytes(); clean()
    state = json.loads(STATE.read_text()) if STATE.exists() else None
    if state is None:
        if git('rev-parse', 'HEAD') != BASE or digest(DST) != BASE_SHA:
            raise RuntimeError('Not the retained 1bb72db9 binary/source; nothing reset or overwritten')
        if (SRC / HEADER).exists(): raise RuntimeError('Diagnostic header already exists')
        logged(['git', '-C', SRC, 'fetch', '--no-tags', '--no-recurse-submodules', REPO, HEADER_REF], 'fetch')
        header = git('show', HEADER_REF + ':' + HEADER_SOURCE) + '\n'
        if hashlib.sha256(header.encode()).hexdigest() != HEADER_SHA: raise RuntimeError('Header hash differs')
        old = {p: (SRC / p).read_text() for p in (LIVERPOOL, BUFFER)}
        new = instrument(old[LIVERPOOL], old[BUFFER]); new[HEADER] = header
        state = dict(base=BASE, original_sha=BASE_SHA, original_branch=git('symbolic-ref', '--short', 'HEAD'),
                     started=time.time(), image=IMAGE, config_sha256=hashlib.sha256(original_config).hexdigest(),
                     ready=False, original_binary=str(ROOT / 'original-shadps4'))
        shutil.copy2(DST, state['original_binary'])
        if digest(state['original_binary']) != BASE_SHA: raise RuntimeError('Backup did not match')
        branch = 'diagnostics/mode0-memory-local-' + time.strftime('%Y%m%d-%H%M%S')
        git('switch', '-c', branch)
        try:
            for p, s in new.items(): (SRC / p).write_text(s)
            git('diff', '--check')
            git('add', '--', *new)
            git('-c', 'user.name=Chris Chreece', '-c', 'user.email=68458228+Chreece@users.noreply.github.com',
                '-c', 'commit.gpgsign=false', 'commit', '-m', 'diag: mode-zero command and buffer-ownership trace')
        except BaseException:
            git('restore', '--source=' + BASE, '--staged', '--worktree', '--', LIVERPOOL, BUFFER)
            git('reset', '--', HEADER, check=False); (SRC / HEADER).unlink(missing_ok=True)
            git('switch', state['original_branch']); raise
        state.update(head=git('rev-parse', 'HEAD'), branch=branch); save(STATE, state)
    if state.get('base') != BASE or git('rev-parse', 'HEAD') != state['head']:
        raise RuntimeError('Source/state changed; not resetting it')
    if digest(DST) not in {BASE_SHA, state.get('sha256')}:
        raise RuntimeError('Installed binary changed; left unchanged')
    expected_files = sorted((HEADER, LIVERPOOL, BUFFER))
    if sorted(git('diff', '--name-only', BASE, state['head']).splitlines()) != expected_files:
        raise RuntimeError('Unexpected source delta')
    (REPORT / 'instrumentation.patch').write_text(git('diff', BASE, state['head']) + '\n')
    if not state['ready']:
        marker = json.loads((BUILD / '.docker-build-environment.json').read_text())
        if marker.get('image') != IMAGE or marker.get('contract') != 'docker-gcc14-sdl-complete-v3':
            raise RuntimeError('Not the successful Docker v3 cache; no new environment created')
        if run(['docker', 'image', 'inspect', '--format', '{{.Id}}', IMAGE]).stdout.strip() != IMAGE:
            raise RuntimeError('Pinned Docker image not available')
        docker(['cmake', '--build', BUILD, '--target', 'shadps4', '--parallel', '6'], 'incremental-build')
        binary = BUILD / 'shadps4'
        if not binary.is_file(): raise RuntimeError('Build produced no shadps4')
        with tempfile.TemporaryDirectory(prefix='shad-help-', dir=ROOT) as td:
            # Isolate even static path initialization performed by --help.
            user = Path(td) / 'user'; user.mkdir()
            env = dict(os.environ, HOME=td, XDG_DATA_HOME=td)
            logged(['timeout', '20', binary, '--help'], 'loader-check', cwd=td, env=env)
        cached = ROOT / 'memory-trace-shadps4'; shutil.copy2(binary, cached)
        state.update(binary=str(cached), sha256=digest(cached), ready=True); save(STATE, state)
    no_game()
    if config_bytes() != original_config: raise RuntimeError('Configuration changed during build; no deploy')
    if digest(DST) not in {BASE_SHA, state['sha256']}: raise RuntimeError('Binary changed during build')
    deploy(Path(state['binary']), state['sha256'])
    state['selected'] = True; save(STATE, state); save(REPORT / 'deployment.json', state)
    print('READY=MODE0_MEMORY_FLOW_DIAGNOSTIC\nGLOBAL_READBACKS_MODE=0\nCONFIGURATION=UNCHANGED')
    print('BINARY_SHA256=' + state['sha256'])
    print('Launch via Moonlight -> ES-DE. Reproduce the absent spear, then run collect.')


def collect():
    state = json.loads(STATE.read_text()) if STATE.exists() else {'started': time.time()}
    sessions = []; selected = []
    for d in sorted(LOGROOT.iterdir()):
        meta = d / 'session.meta'
        if not d.is_dir() or d.is_symlink() or not meta.is_file(): continue
        match = re.search(r'^started=(.+)$', meta.read_text(errors='replace'), re.M)
        if not match: continue
        try: stamp = dt.datetime.fromisoformat(match[1]).timestamp()
        except ValueError: continue
        if stamp >= state['started']: selected.append(d)
    archive = HOME / ('shadps4-mode0-memory-' + time.strftime('%Y%m%d-%H%M%S') + '.tar.gz')
    with tempfile.TemporaryDirectory(dir=ROOT, prefix='snapshot-') as td:
        snap = Path(td); budget = 96 * 1024 * 1024
        for d in selected[-5:]:
            dest = snap / d.name; dest.mkdir()
            entry = dict(session=d.name)
            for name in ('session.meta', 'runtime.log'):
                src = d / name
                if not src.is_file() or src.is_symlink() or budget <= 0: continue
                with src.open('rb') as f: content = f.read(min(src.stat().st_size, 32*1024*1024, budget))
                (dest / name).write_bytes(content); budget -= len(content)
                if name == 'runtime.log':
                    txt = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', content.decode(errors='replace'))
                    mode = re.search(r'GPU readbacksMode: (\d+)', txt)
                    rev = re.search(r'\bRevision ([0-9a-f]{40})', txt)
                    entry.update(mode=int(mode[1]) if mode else None, revision=rev[1] if rev else None,
                                 memory_flow_lines=txt.count('MEMFLOW '), criticals=txt.count('<Critical>'),
                                 truncated=len(content) < src.stat().st_size)
            sessions.append(entry)
        save(REPORT / 'runtime-audit.json', {'sessions': sessions, 'current_binary_sha256': digest(DST),
                                           'current_readbacks_mode': json.loads(CONFIG.read_text())['GPU']['readbacks_mode']})
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(REPORT, arcname='memory-flow/reports'); tar.add(snap, arcname='memory-flow/runtime')
    print('ARCHIVE=' + str(archive)); print(json.dumps(sessions, indent=2))


def restore():
    no_game(); cfg = config_bytes(); state = json.loads(STATE.read_text())
    if digest(DST) not in {BASE_SHA, state.get('sha256')}: raise RuntimeError('Different installed binary')
    deploy(Path(state['original_binary']), BASE_SHA)
    if git('rev-parse', 'HEAD') == state['head']:
        clean(); git('switch', state['original_branch'])
    if config_bytes() != cfg: raise RuntimeError('External configuration change detected')
    state['selected'] = False; save(STATE, state)
    print('RESTORED_BINARY=1bb72db9\nGLOBAL_READBACKS_MODE=0\nCONFIGURATION=UNCHANGED')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('prepare', 'collect', 'restore')); args = p.parse_args()
    REPORT.mkdir(parents=True, exist_ok=True)
    with (WORK / 'readback-docker-trial/lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            {'prepare': prepare, 'collect': collect, 'restore': restore}[args.action]()
            return 0
        except (Exception, KeyboardInterrupt) as e:
            (REPORT / 'error.txt').write_text(str(e) + '\n'); print('STOPPED=' + str(e), file=sys.stderr)
            if args.action != 'collect':
                try: collect()
                except Exception as failure: print('PACK_ERROR=' + str(failure), file=sys.stderr)
            return 1
        finally: print('SSH_SESSION=REMAINS_OPEN')

if __name__ == '__main__': raise SystemExit(main())
