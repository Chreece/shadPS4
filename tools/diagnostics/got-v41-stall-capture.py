#!/usr/bin/env python3
"""Capture the new v40 live stall; no emulator build, driver patch, or shader skip.

Reuses the checksum-verified v40 capture stack. Records two controlled CPU
snapshots and one non-halting UMR scan when flips stop advancing. Unlike the
fatal-only collector, saves thread state before its deadline cleanup.
"""
from __future__ import annotations
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

LABEL = 'got-v41-stall-visual'
TREE = 'got-v40-cached-image-bindings-20261014'
BINARY_SHA = '0ed71b7dcc29e110b10073186785d4d463151ad2c6c316beabd2fd5bc0bfe808'
SOURCE_SHA = '4fe3da5ca6e77ccdbe9c25309cdb84cbcf11ad0d9b795829ef4ea83b21ba7f6f'
DRIVER_SHA = '87908e0c6833c0f6dc86cb079cb0f34de0dee7e970da89b5303f0e6b29e326f7'
BUILDER_SHA = '3cbe3f6c02639e081e4b1c0f1b4623e05548a735e171b4dd09135a2bf1e4d949'
MAX_CONSOLE = 24 * 1024 * 1024
SOURCE_FILES = (
    'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp',
    'src/video_core/renderer_vulkan/vk_pipeline_cache.h',
    'src/video_core/renderer_vulkan/vk_scheduler.cpp',
    'src/video_core/renderer_vulkan/vk_scheduler.h',
    'src/video_core/renderer_vulkan/vk_rasterizer.cpp',
    'src/video_core/amdgpu/liverpool.cpp', 'src/video_core/amdgpu/liverpool.h',
    'src/video_core/amdgpu/pm4_cmds.h',
    'src/video_core/buffer_cache/buffer_cache.cpp',
    'src/video_core/buffer_cache/fault_manager.cpp',
    'src/core/memory.cpp', 'src/core/memory.h',
    'src/core/libraries/kernel/equeue.cpp',
    'src/core/libraries/kernel/threads/pthread.cpp',
    'src/core/libraries/videoout/driver.cpp',
    'src/shader_recompiler/info.h', 'src/shader_recompiler/specialization.h',
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular(path: Path, limit: int = 128*1024*1024) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise RuntimeError('Missing or unsafe input: '+str(path))
    return path.read_bytes()


def store(path: Path, value: object) -> None:
    # Atomically published so the running supervisor never mistakes a partial
    # snapshot for a finished capture.
    data = (json.dumps(value, indent=2)+'\n').encode()
    temp = path.with_name('.'+path.name+'.pending')
    with temp.open('wb') as f:
        f.write(data)
    os.replace(temp, path)


def import_file(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load verified helper: '+str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Progress:
    """Detect stationary flips only after the corrected shader-binding path."""
    def __init__(self):
        self.pending = ''
        self.smoke_ready = False
        self.binding_seen = False
        self.binding_time = None
        self.last_flip = None
        self.changed_at = None
        self.last_seen = None
        self.samples = []
        self.targets = {}

    def feed(self, data: bytes, elapsed: float) -> None:
        text = self.pending + data.decode(errors='replace')
        parts = text.split('\n')
        self.pending = parts.pop()[-8192:]
        for raw in parts:
            line = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', raw)
            if 'GOT41_SMOKE_READY' in line:
                self.smoke_ready = True
            if 'GOT40_CACHED_BINDINGS' in line and 'corrected_end=384' in line:
                if not self.binding_seen:
                    self.binding_time = elapsed
                self.binding_seen = True
            m = re.search(r'GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)', line)
            if m:
                row = dict(zip(('vblank', 'flips', 'pending', 'queued'), map(int, m.groups())))
                row['elapsed'] = round(elapsed, 3)
                if row['flips'] != self.last_flip:
                    self.changed_at = elapsed
                self.last_flip = row['flips']
                self.last_seen = elapsed
                self.samples = (self.samples + [row])[-24:]
            if 'GHOST_C6_RELEASE' in line:
                fields = dict(re.findall(r'(\w+)=(0x[0-9a-fA-F]+|\d+)', line))
                try:
                    address = (int(fields['addr_hi'], 0) << 32) | int(fields['addr_lo'], 0)
                    if 0x10000 <= address < (1 << 47) and not address % 4:
                        self.targets[hex(address)] = {'address': hex(address),
                            'seq': fields.get('seq'), 'data_sel': fields.get('data_sel'),
                            'data_lo': fields.get('data_lo'), 'data_hi': fields.get('data_hi')}
                        self.targets = dict(list(self.targets.items())[-8:])
                except (KeyError, ValueError):
                    pass

    def stalled(self, elapsed: float) -> bool:
        if not self.binding_seen or self.last_seen is None or self.changed_at is None:
            return False
        recent = self.samples[-4:]
        return (len(recent) == 4 and self.last_flip > 0 and
                len({x['flips'] for x in recent}) == 1 and
                elapsed - max(self.changed_at, self.binding_time) >= 12 and
                elapsed - self.last_seen < 8 and
                recent[-1]['vblank'] > recent[0]['vblank'])

    def evidence(self) -> dict:
        return {'binding_fix_seen': self.binding_seen, 'samples': self.samples,
                'release_targets': list(self.targets.values()), 'gameplay_verified': False}


# Injected into GDB after the original native capture and driver-verification
# helpers. Does not call functions in the inferior or change guest memory.
GDB_STALL = r'''
def stall_write(name, obj):
    tmp = out / ('.' + name + '.pending')
    tmp.write_text(json.dumps(obj, indent=2) + '\n')
    os.replace(tmp, out / name)

def stall_request():
    try:
        return json.loads((Path(SESSION)/'stall-request.json').read_text())
    except Exception:
        return {}

def stall_snapshot(number, request):
    begin = time.monotonic()
    inf = gdb.selected_inferior()
    meta = {'snapshot': number, 'reason': request.get('reason'), 'pid': inf.pid,
            'event': dict(event), 'threads': [], 'gpu_wave_halt_requested': False,
            'inferior_memory_written': False, 'gameplay_verified': False}
    if not inf.pid or not inf.threads():
        meta['error'] = 'Inferior exited before snapshot'
        stall_write('stall-%02d.json' % number, meta)
        return False
    prefix = 'stall-%02d-' % number
    try:
        maps = Path('/proc/%d/maps' % inf.pid).read_text()
        (out / (prefix+'maps.txt')).write_text(maps)
    except Exception as exc:
        maps = ''; meta['maps_error'] = str(exc)
    # First persist a compact snapshot, before optional values or a GPU read.
    (out / (prefix+'all-threads.txt')).write_text(execute('thread apply all bt 24'))
    stall_write('stall-%02d.json' % number, meta)
    threads = list(inf.threads())
    def priority(t):
        name = t.name or ''
        return (not any(x in name for x in ('GpuComm', 'Game:Main', 'Proxy', 'GpuInterrupt')),
                t.num)
    threads.sort(key=priority)
    chosen = gdb.selected_thread()
    for t in threads[:128]:
        if time.monotonic() - begin > 10:
            meta['detail_time_limit'] = True
            break
        row = {'thread': t.num, 'ptid': list(t.ptid), 'name': t.name, 'frames': []}
        try:
            t.switch()
            frame = gdb.newest_frame()
            row['pc'] = hex(int(frame.pc())) if frame else None
            row['registers'] = {}
            if any(x in (t.name or '') for x in ('GpuComm', 'Game:Main', 'Proxy', 'GpuInterrupt')):
                for name in ('rip','rsp','rax','rbx','rcx','rdx','rdi','rsi','rbp','r8','r9','r10','r11','r12','r13','r14','r15'):
                    try: row['registers'][name] = hex(int(frame.read_register(name)))
                    except Exception: pass
                row['instructions'] = execute('x/16i $pc')[:6000]
                row['stack_words'] = execute('x/24gx $rsp')[:6000]
            for level in range(24):
                if frame is None: break
                detail = {'level': level, 'name': frame.name(), 'pc': hex(int(frame.pc())),
                          'module': gdb.solib_name(frame.pc())}
                # No pretty-printer/inferior method calls. Bound C++ values.
                name = frame.name() or ''
                if level < 10 and any(x in name for x in (
                        'Liverpool', 'Wait', 'wait', 'Memory', 'ProcessGraphics', 'ProcessCompute',
                        'Scheduler', 'ObtainBuffer', 'GetProgram', 'sceKernel')):
                    frame.select()
                    detail['args'] = execute('info args')[:5000]
                    detail['locals'] = execute('info locals')[:10000]
                row['frames'].append(detail)
                frame = frame.older()
        except Exception as exc:
            row['error'] = str(exc)
        meta['threads'].append(row)
    if chosen and chosen.is_valid(): chosen.switch()
    # Addresses are derived only from this run's RELEASE_MEM records. Record
    # raw bytes; do not assume any unverified CPU/GPU coherency semantics.
    meta['release_memory'] = []
    for target in request.get('release_targets', [])[:8]:
        record = dict(target)
        try:
            address = int(target['address'], 16)
            if not (0x10000 <= address < (1 << 47)) or address % 4:
                raise ValueError('Unsafe captured address')
            record['bytes_16'] = bytes(inf.read_memory(address, 16)).hex()
        except Exception as exc:
            record['read_error'] = str(exc)
        meta['release_memory'].append(record)
    meta['elapsed_capture'] = time.monotonic() - begin
    stall_write('stall-%02d.json' % number, meta)
    stall_write('stall-%02d-ready.json' % number, {'complete': True, 'snapshot': number})
    print('GOT41_CPU_SNAPSHOT_SAVED=%d' % number, flush=True)
    return True

def stall_run(initial='run'):
    command = initial
    count = 0
    while True:
        event.clear()
        caught = False
        try:
            gdb.execute(command)
        except KeyboardInterrupt:
            caught = True
        request = stall_request()
        controlled = (caught or event.get('signal') == 'SIGINT')
        if controlled and request.get('snapshot') == count + 1 and count < 2:
            count += 1
            if not stall_snapshot(count, request): break
            if count == 2:
                write('stop.json', {'reason': 'CONTROLLED_STALL_SNAPSHOTS', 'snapshots': count,
                                   'gameplay_verified': False, 'gpu_wave_halt_requested': False})
                break
            print('GOT41_RESUME_BETWEEN_SNAPSHOTS=YES', flush=True)
            command = 'continue'
            continue
        # Preserve original native-fault handling (including driver gate failure).
        dump_native()
        break
'''


def augment_gdb(source: str) -> str:
    anchor = 'def dump_native():\n'
    if source.count(anchor) != 1:
        raise RuntimeError('Native debugger template differs')
    source = source.replace(anchor, GDB_STALL + '\n' + anchor, 1)
    anchor = "    gdb.execute('run')\n    dump_native()\n"
    if source.count(anchor) != 1:
        raise RuntimeError('Final debugger run point differs; no game launched')
    source = source.replace(anchor,
        "    gdb.execute('handle SIGINT stop print nopass')\n"
        "    gdb.execute('set may-call-functions off')\n"
        "    gdb.execute('set max-value-size 16384')\n"
        "    gdb.execute('set print max-depth 3')\n"
        "    stall_run()\n", 1)
    compile(source, '<got41-gdb>', 'exec')
    return source


def proc_sample(native: Path, binary: Path) -> dict:
    result = {'tasks': []}
    try:
        pid = int(json.loads((native/'inferior.json').read_text())['pid'])
        process = Path('/proc')/str(pid)
        if (process/'exe').resolve() != binary.resolve():
            return {'error': 'Inferior identity unavailable'}
        result['pid'] = pid
        for task in sorted((process/'task').iterdir(), key=lambda p: int(p.name))[:192]:
            row = {'tid': int(task.name)}
            for name in ('comm', 'stat', 'wchan', 'syscall'):
                try: row[name] = (task/name).read_text()[:4096]
                except OSError as exc: row[name] = str(exc)
            result['tasks'].append(row)
    except Exception as exc:
        result['error'] = str(exc)
    return result


def drive(command: list[str], session: Path, checkpoint=None, smoke: bool = False,
          binary: Path | None = None) -> dict:
    """Only interrupt the child GDB we create; never an unrelated emulator/shell."""
    native = session/'native'; native.mkdir(exist_ok=True)
    progress = Progress()
    env = dict(os.environ, LC_ALL='C', DEBUGINFOD_URLS='')
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, env=env, start_new_session=True)
    start = time.monotonic()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1) if checkpoint else None
    future = None
    first_at = None; first_done = None; second_at = None
    cancelled = False; bytes_written = 0
    state = {'smoke': smoke, 'gameplay_verified': False, 'signals_sent': [], 'snapshot_count': 0}
    def cancel(_sig, _frame):
        nonlocal cancelled
        cancelled = True
    handlers = {s: signal.signal(s, cancel) for s in (signal.SIGTERM, signal.SIGINT)}
    def interrupt(number: int, reason: str, elapsed: float):
        request = {'snapshot': number, 'reason': reason, 'elapsed': elapsed, **progress.evidence()}
        if binary and not smoke:
            store(native/('stall-%02d-proc-before.json' % number), proc_sample(native, binary))
        store(session/'stall-request.json', request)
        child.send_signal(signal.SIGINT)
        state['signals_sent'].append({'snapshot': number, 'reason': reason, 'elapsed': elapsed})
    try:
        with selectors.DefaultSelector() as sel, (native/'debugger-console.log').open('wb') as output:
            sel.register(child.stdout, selectors.EVENT_READ)
            while not cancelled:
                elapsed = time.monotonic()-start
                if elapsed > (12 if smoke else 105):
                    state['deadline_reached'] = True; break
                for key, _ in sel.select(0.1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        sel.unregister(key.fileobj); continue
                    if not smoke:
                        sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush()
                    if bytes_written < MAX_CONSOLE:
                        part = chunk[:MAX_CONSOLE-bytes_written]
                        output.write(part); output.flush(); bytes_written += len(part)
                    progress.feed(chunk, elapsed)
                if child.poll() is not None and not sel.get_map(): break
                # Never send an interrupt once the debugger is processing a fatal stop.
                fatal_stop = (native/'stop.json').exists() or (native/'gdb-error.json').exists()
                if first_at is None and not fatal_stop:
                    ready = (smoke and progress.smoke_ready and elapsed > 0.5)
                    stagnant = not smoke and progress.stalled(elapsed)
                    forced = not smoke and elapsed >= 65
                    if ready or stagnant or forced:
                        reason = 'SMOKE_TEST' if smoke else ('STATIONARY_FLIPS_AFTER_BINDING_FIX' if stagnant else 'BOUNDED_DEADLINE_SAMPLE')
                        if checkpoint: future = pool.submit(checkpoint)
                        interrupt(1, reason, elapsed); first_at = time.monotonic()
                if first_at is not None and first_done is None and (native/'stall-01-ready.json').is_file():
                    first_done = time.monotonic(); state['snapshot_count'] = 1
                if first_done is not None and second_at is None and not fatal_stop:
                    waited = time.monotonic()-first_done
                    # Let the finite wave read complete while the application has
                    # resumed, so the second snapshot cannot pre-empt our GPU scan.
                    if waited >= (0.2 if smoke else 3) and (future is None or future.done()):
                        interrupt(2, 'SECOND_STATE_AFTER_RESUME', elapsed)
                        second_at = time.monotonic()
                if (native/'stall-02-ready.json').is_file(): state['snapshot_count'] = 2
                if first_at is not None and first_done is None and time.monotonic()-first_at > (5 if smoke else 20):
                    state['interrupt_failure'] = 'No first snapshot before bounded wait'; break
        if future and future.done():
            try: state['wave_result'] = future.result(timeout=0)
            except Exception as exc: state['wave_error'] = str(exc)
    finally:
        # Kill only our own new process group if normal GDB inferior cleanup
        # failed or the outer capture was cancelled. Never touch SSH or GoW.
        if child.poll() is None:
            try: os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try: os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                try: child.wait(timeout=2)
                except subprocess.TimeoutExpired: state['cleanup_warning'] = 'Child not reaped'
        if child.stdout: child.stdout.close()
        if pool: pool.shutdown(wait=False, cancel_futures=True)
        for sig, handler in handlers.items(): signal.signal(sig, handler)
        state['snapshot_count'] = sum((native/('stall-%02d-ready.json' % i)).is_file() for i in (1,2))
        state.update(debugger_exit=child.poll(), cancelled=cancelled, progress=progress.evidence())
        # Wave output is archived separately by the existing broker collector.
        if isinstance(state.get('wave_result'), dict):
            state['wave_result'] = {k:v for k,v in state['wave_result'].items() if k != 'output'}
        store(native/'stall-supervisor.json', state)
    return state


def smoke_gdb(project: Path) -> dict:
    """Validate real GDB interruption locally on sleep, before any GPU access."""
    gdb = shutil.which('gdb'); sleep = shutil.which('sleep')
    if not gdb or not sleep: raise RuntimeError('GDB/sleep unavailable')
    folder = Path(tempfile.mkdtemp(prefix='got41-gdb-smoke-', dir=project.parent/'logs'))
    (folder/'native').mkdir()
    script = folder/'smoke.py'
    prefix = ('import gdb, json, os, time\nfrom pathlib import Path\nSESSION='+repr(str(folder))+'\n'
              "out=Path(SESSION)/'native'\nevent={}\n"
              "def write(name, obj): (out/name).write_text(json.dumps(obj)+'\\n')\n"
              "def execute(cmd):\n"
              "    try: return gdb.execute(cmd, to_string=True)\n"
              "    except Exception as exc: return 'COMMAND_FAILED '+str(exc)\n"
              "def on_stop(e):\n"
              "    if isinstance(e,gdb.SignalEvent): event.update(signal=e.stop_signal)\n"
              "def dump_native(): write('unexpected-stop.json', event)\n"
              "gdb.events.stop.connect(on_stop)\n")
    tail = """
for cmd in ('set pagination off','set confirm off','set auto-load off',
            'set may-call-functions off','set debuginfod enabled off',
            'handle SIGINT stop print nopass'):
    gdb.execute(cmd)
gdb.execute('starti')
print('GOT41_SMOKE_READY', flush=True)
try:
    stall_run('continue')
finally:
    inf=gdb.selected_inferior()
    if inf.pid: execute('kill')
"""
    script.write_text(prefix+GDB_STALL+tail)
    result = drive([gdb,'-q','-nx','--batch','-x',str(script),'--args',sleep,'30'], folder, smoke=True)
    if result['snapshot_count'] != 2:
        archive = folder.with_name(folder.name+'.tar.gz')
        with tarfile.open(archive,'w:gz') as t:
            for p in sorted(folder.rglob('*')):
                if p.is_file() and not p.is_symlink(): t.add(p,arcname=str(p.relative_to(folder)),recursive=False)
        print('UPLOAD_GDB_SMOKE_FAILURE='+str(archive), flush=True)
        raise RuntimeError('GDB controlled snapshots did not pass on sleep; no game/UMR started')
    print('GDB_CONTROLLED_SNAPSHOTS=2_VERIFIED_ON_SLEEP; GAME_NOT_STARTED', flush=True)
    return {'result':'TWO_REAL_GDB_SNAPSHOTS_ON_SLEEP','report_directory':str(folder),
            'gpu_accessed':False, 'snapshot_count':2}


def verify_stack(project: Path):
    scripts = project/'scripts'
    builder_path = scripts/'got-v40-binding-layout-fix.py'
    if digest(regular(builder_path,100_000)) != BUILDER_SHA:
        raise RuntimeError('The exact v40 helper is missing/changed')
    builder = import_file(builder_path, 'got41_verified_v40_builder')
    source = project/'tests'/TREE/builder.SOURCE
    binary = project/'tests'/TREE/'build/shadps4'
    if builder.file_sha(binary) != BINARY_SHA or builder.file_sha(source) != SOURCE_SHA:
        raise RuntimeError('v40 binary or pipeline-cache source differs from your uploaded result')
    proof = json.loads(regular(binary.parent/'proof.json',32_000))
    if proof.get('binary_sha256') != BINARY_SHA or proof.get('private_driver_sha256') != DRIVER_SHA:
        raise RuntimeError('v40 build proof differs')
    raw = {}
    for name, expected in builder.HELPERS.items():
        data = regular(scripts/name,100_000)
        if digest(data) != expected: raise RuntimeError('Pinned original helper changed: '+name)
        raw[name] = data.decode()
    generated = builder.helper_sources(raw,BINARY_SHA)
    for name, expected in generated.items():
        if regular(scripts/name,100_000) != expected.encode():
            raise RuntimeError('Generated v40 capture helper changed: '+name)
    return builder, scripts/'got40-radv-nuw-capture.py'


def supervise(base, session: Path, binary: Path, arguments: list[str], dual) -> int:
    project = session.parent.parent
    if os.geteuid() == 0 or session.parent != project/'tools' or not session.name.startswith('umr-session-'):
        raise RuntimeError('Invalid user capture session')
    if binary != Path.cwd()/'shadps4' or not binary.resolve().is_relative_to(project/'tests'):
        raise RuntimeError('Not the private portable v40 executable')
    if not base.regular(binary) or base.sha(binary) != BINARY_SHA:
        raise RuntimeError('Wrong v40 game binary')
    expected = ['--cpu-id-mode=native','--game','/mnt/roms-all/ps4-games/CUSA11456/eboot.bin','--fullscreen','true']
    if arguments != expected: raise RuntimeError('Game arguments differ')
    native = session/'native'; native.mkdir(exist_ok=True)
    payload = session/'native-stall.py'
    payload.write_text(dual.gdb_source(session))
    command = [shutil.which('gdb'),'-q','-nx','--batch','-x',str(payload),'--args',str(binary)]+arguments
    store(native/'invocation.json', {'args':command,'binary_sha256':BINARY_SHA,
          'started':base.now(),'cpu_pause_reason':'two nonfatal stall snapshots; fatal hooks retained'})
    def checkpoint():
        answer = base.rpc(session,{'op':'umr','args':['--got-checkpoint','pipeline']})
        base.store(session/'pipeline.json', answer)
        return answer
    result = drive(command,session,checkpoint,binary=binary)
    try: reason = json.loads((native/'stop.json').read_text()).get('reason')
    except (OSError,ValueError): reason = None
    store(native/'result.json', {'kind':'V40_LIVE_STALL_CAPTURE','snapshot_count':result['snapshot_count'],
          'native_stop_reason':reason,'debugger_exit':result['debugger_exit'],'gameplay_verified':False})
    return 0 if result['snapshot_count']==2 else 124 if result.get('deadline_reached') else 2


def install(module, project: Path):
    previous_stack = module.load_stack
    def load_stack(p):
        dual,base = previous_stack(p)
        original_gdb = dual.gdb_source
        dual.gdb_source = lambda session: augment_gdb(original_gdb(session))
        dual.supervise = lambda b,s,exe,args: supervise(b,s,exe,args,dual)
        previous_preflight = dual.preflight_gdb
        def preflight(b):
            previous_preflight(b)
            smoke_gdb(p)
        dual.preflight_gdb = preflight
        old_gather = base.gather
        def gather(session, report):
            rc = old_gather(session,report)
            native=session/'native'
            result = json.loads((native/'stall-supervisor.json').read_text()) if (native/'stall-supervisor.json').is_file() else {'result':'NO_SUPERVISOR_RESULT'}
            base.store(report/'info/stall-capture.json', result)
            manifest=[]; total=0
            for relative in SOURCE_FILES:
                path = p/'tests'/TREE/relative
                if not base.regular(path) or path.stat().st_size > 800_000: continue
                data=path.read_bytes(); total+=len(data)
                if total>5_000_000: break
                dest=report/'info/stall-source'/relative
                dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(data)
                manifest.append({'path':relative,'sha256':digest(data)})
            base.store(report/'info/stall-source-manifest.json',manifest)
            return rc
        base.gather=gather
        return dual,base
    module.load_stack=load_stack
    return module


def main() -> int:
    os.umask(0o077)
    script=Path(__file__).resolve(); project=script.parent.parent
    if script.parent.name!='scripts' or project.name!='GhostOfTsushima':
        raise RuntimeError('Place this helper in ~/projects/shadps4/GhostOfTsushima/scripts')
    _,entry=verify_stack(project)
    nuw=import_file(entry,'got41_verified_nuw')
    nuw.LABEL=LABEL
    module=nuw.install(nuw.load_previous(script),project)
    module=install(module,project)
    return module.main()


if __name__=='__main__':
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
    except Exception as exc:
        print('STOP: '+str(exc),file=sys.stderr); sys.exit(1)
