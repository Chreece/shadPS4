#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Isolated PES scalar reciprocal A/B capture. Does not install a build."""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.request
import zipfile

BASE = '731ababdb7819486eca8dcc7a7f622efa3b75cc7'
CPU_SHA = '553de5682e840e4d83d636c4bba8678a5b36cc515ae65b3929359c2e3497c79c'
FFMPEG_SHA = 'aacbbfb8e622b684bc5d3b4cd6c9f9f77f5def64ae8d83c0c5b3ebe657aa33dd'
ASSETS = {'debugger.py': '40e8963f82479030bcfa204858f56b21ad73c60a8b637170a9b11f47600c862a', 'gdb_capture.py': '5a86dbaf1ef3ef3bbd364f66bf43119f20bcf33564c9f59e47cbe8c0ffc17fd8', 'cpu-baseline.patch': 'a2fabe0c86b899319b4aaf7a80ad405ca7ae2e6b80668093d7ae33e5b65ae4a1', 'cpu-baseline.json': 'f38686786b8ccec5d0eb87ed14c5bc5f93da6c69ca1ea36e31ba250b148f028f', 'cpu-baseline.patch.license': '8f3edffe7a0fb2b41cbc2f26f4349ef68f80835aedb7073c56aa102c952486b0', 'cpu-baseline.json.license': '8f3edffe7a0fb2b41cbc2f26f4349ef68f80835aedb7073c56aa102c952486b0', 'manual.py': 'dc867c3152984ece82a5a309a026f853dff20d867231cbab29098a32c7a8842f', 'profile.py': '00f25e0ffc6f6311f155b64219def15bb5583da1126cc54d3cd9ab62ead90746', 'instrumentation.patch': '6ad36fda634f19900134d380586af1bbd9ca04d0fc6df63535a67e31d9480707', 'scalar_ab.h': 'cc825d23425371955071e97e0975657bfaeee09daecca5acd75936a956d47a2b', 'README.md': '32ddf585cb21f4f94001967f8e20df94079d60e17b917f1090e55fedb0e8f6af', 'README.md.license': '8f3edffe7a0fb2b41cbc2f26f4349ef68f80835aedb7073c56aa102c952486b0', 'instrumentation.patch.license': '8f3edffe7a0fb2b41cbc2f26f4349ef68f80835aedb7073c56aa102c952486b0', 'validation.txt': '6612110d48f31247505317566e2e6fca34d6e65e426bf0dd62f81401138a8185', 'validation.txt.license': '8f3edffe7a0fb2b41cbc2f26f4349ef68f80835aedb7073c56aa102c952486b0'}
USE_LANDLOCK = False
MODES = ('native', 'fixed', 'fixed', 'native', 'diagnostic')
SERIAL = 'CUSA18676'
HERE = Path(__file__).resolve().parent


def say(value):
    print(value, flush=True)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def assets(url):
    say('Checking the pinned runner package')
    for name, expected in ASSETS.items():
        path = HERE / name
        if not path.is_file():
            if not url or not re.fullmatch(
                    r'https://raw\.githubusercontent\.com/Chreece/shadPS4/[0-9a-f]{40}/hardware/reciprocal_review/pes-ab', url):
                raise RuntimeError('Missing packaged assets and no pinned asset URL')
            say('Downloading helper: ' + name)
            with urllib.request.urlopen(url + '/' + name, timeout=45) as response:
                data = response.read(512 * 1024)
            path.write_bytes(data)
        if digest(path) != expected:
            raise RuntimeError('Package checksum mismatch: ' + name)
    say('Runner package verified')


def landlock_available():
    lib = ctypes.CDLL(None, use_errno=True)
    lib.syscall.restype = ctypes.c_long
    return lib.syscall(444, 0, 0, 1) >= 3


def child_setup(root):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if USE_LANDLOCK:
        restrict_writes(root)


def restrict_writes(root):
    """Only this child is restricted; no privileges or global policy are changed."""
    lib = ctypes.CDLL(None, use_errno=True)
    lib.syscall.restype = ctypes.c_long
    abi = lib.syscall(444, 0, 0, 1)
    if abi < 3:
        raise RuntimeError('Linux Landlock ABI 3+ is required for write isolation')
    mask = (1 << 1) | sum(1 << n for n in range(4, 15))
    class Ruleset(ctypes.Structure):
        _fields_ = [('access', ctypes.c_uint64)]
    class Rule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [('access', ctypes.c_uint64), ('parent', ctypes.c_int32)]
    ruleset = Ruleset(mask)
    fd = lib.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), 'create Landlock ruleset')
    try:
        for path, allowed in [(root, mask), (Path('/dev'), (1 << 1) | (1 << 14)),
                              (Path('/dev/shm'), mask)]:
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = Rule(allowed, parent)
                if lib.syscall(445, fd, 1, ctypes.byref(rule), 0) < 0:
                    raise OSError(ctypes.get_errno(), 'add Landlock rule')
            finally:
                os.close(parent)
        if lib.prctl(38, 1, 0, 0, 0) or lib.syscall(446, fd, 0):
            raise OSError(ctypes.get_errno(), 'restrict child writes')
    finally:
        os.close(fd)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def probe(root, protected):
    restrict_writes(root)
    (root / 'allowed-write').write_text('ok')
    try:
        with protected.open('a'):
            pass
    except PermissionError:
        return
    raise RuntimeError('Write isolation did not reject an outside write')


def snapshot(roots):
    result = {}
    for root in roots:
        say('Hashing files: ' + str(root))
        result[str(root)] = {'exists': root.exists()}
        paths = [root] + (sorted(root.rglob('*')) if root.is_dir() else [])
        for path in paths:
            if path.is_symlink():
                raise RuntimeError('Unexpected symlink in protected settings/saves: ' + str(path))
            if path.is_file():
                st = path.stat()
                result[str(path)] = {'sha256': digest(path), 'mode': st.st_mode,
                                     'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
    return result


def require_idle():
    found = profile.emulators()
    if found:
        raise RuntimeError('A shadPS4 game is already running. It was left alone; close it and rerun.')


def clean_env(root, desktop=None):
    env = dict(os.environ if desktop is None else desktop)
    for key in list(env):
        if key.startswith('SHADPS4_'):
            env.pop(key)
    for key, name in [('XDG_CACHE_HOME', 'xdg-cache'), ('TMPDIR', 'tmp'),
                      ('CCACHE_DIR', 'ccache'), ('MESA_SHADER_CACHE_DIR', 'mesa-cache'),
                      ('__GL_SHADER_DISK_CACHE_PATH', 'nvidia-cache'),
                      ('AMD_VK_PIPELINE_CACHE_PATH', 'amd-cache')]:
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    count = int(env.get('GIT_CONFIG_COUNT', '0'))
    for offset, (key, value) in enumerate((('http.lowSpeedLimit', '1024'), ('http.lowSpeedTime', '30'))):
        env[f'GIT_CONFIG_KEY_{count + offset}'] = key
        env[f'GIT_CONFIG_VALUE_{count + offset}'] = value
    env['GIT_CONFIG_COUNT'] = str(count + 2)
    env.update(GIT_TERMINAL_PROMPT='0', PYTHONDONTWRITEBYTECODE='1')
    return env


def stop(process, stage=None):
    forced = False
    requested = False
    if process.poll() is None and stage:
        requested = True
        control(stage, 'quit')
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
    if process.poll() is None:
        forced = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    # The process group is ours even if its leader already exited.
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        pass
    else:
        forced = True
        os.killpg(process.pid, signal.SIGKILL)
    return {'returncode': process.returncode, 'forced_cleanup': forced,
            'normal_exit_requested': requested,
            'normal_exit': process.returncode == 0 and not forced}


def command(args, log, root, cwd=None, timeout=3600, env=None):
    say('Running: ' + ' '.join(map(str, args)))
    with log.open('a') as output:
        child = subprocess.Popen(list(map(str, args)), cwd=cwd, env=env or clean_env(root),
                                 stdin=subprocess.DEVNULL, stdout=output,
                                 stderr=subprocess.STDOUT, start_new_session=True,
                                 preexec_fn=lambda: child_setup(root))
        started = time.monotonic()
        try:
            while child.poll() is None:
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    say(f'Working ({int(time.monotonic()-started)}s); log: {log}')
                    require_idle()
                    if time.monotonic() - started > timeout:
                        raise RuntimeError('Command timed out; see ' + str(log))
            if child.returncode:
                say(log.read_text(errors='replace')[-2500:])
                raise RuntimeError('Command failed; see ' + str(log))
        finally:
            stop(child)


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args],
                                   stderr=subprocess.STDOUT, timeout=60).decode().strip()


def build(root, report, home):
    source, build_dir = root / 'source', root / 'build'
    log = report / 'build.log'
    say('Preparing an independent source checkout; build log: ' + str(log))
    command(['git', 'init', '--template=', source], log, root)
    command(['git', '-C', source, 'remote', 'add', 'origin',
             'https://github.com/Chreece/shadPS4.git'], log, root)
    command(['git', '-C', source, '-c', 'http.lowSpeedLimit=1024', '-c', 'http.lowSpeedTime=30',
             'fetch', '--recurse-submodules=no', '--depth=1', 'origin', BASE], log, root, timeout=600)
    command(['git', '-C', source, 'checkout', '--detach', BASE], log, root)
    if digest(source / 'src/core/cpu_patches.cpp') != CPU_SHA:
        raise RuntimeError('Pinned CPU baseline does not match the verified source')
    say('Applying the CPU baseline: affinity, CPU identity, SSE4a, feature filtering and bundled translation')
    command(['git', '-C', source, 'apply', '--check', HERE / 'cpu-baseline.patch'], log, root)
    command(['git', '-C', source, 'apply', HERE / 'cpu-baseline.patch'], log, root)
    prerequisites = json.loads((HERE / 'cpu-baseline.json').read_text())
    if prerequisites['base_commit'] != BASE:
        raise RuntimeError('CPU prerequisite manifest has a different baseline')
    for relative, expected in prerequisites['files'].items():
        if digest(source / relative) != expected:
            raise RuntimeError('CPU prerequisite verification failed: ' + relative)
    write_json(report / 'cpu-baseline.json', prerequisites)
    say('CPU prerequisites verified; both modes use this same source')
    command(['git', '-C', source, '-c', 'http.lowSpeedLimit=1024', '-c', 'http.lowSpeedTime=30',
             'submodule', 'update', '--init', '--recursive', '--depth=1', '--jobs=4'],
            log, root, timeout=1800)
    command(['git', '-C', source, 'apply', '--check', HERE / 'instrumentation.patch'], log, root)
    command(['git', '-C', source, 'apply', HERE / 'instrumentation.patch'], log, root)
    shutil.copy2(HERE / 'scalar_ab.h', source / 'src/core/scalar_ab.h')
    # Reuse a verified download, never the installed executable or build outputs.
    short = git(source / 'externals/ffmpeg-core', 'rev-parse', '--short', 'HEAD')
    cache = home / '.cache/shadps4-affinity-20261007'
    candidates = sorted(cache.glob('*/externals/ffmpeg-94dde08*.zip'))
    cached = next((p for p in candidates if digest(p) == FFMPEG_SHA), None)
    if cached and short.startswith('94dde08'):
        dest = build_dir / 'externals' / ('ffmpeg-' + short + '.zip')
        dest.parent.mkdir(parents=True)
        shutil.copy2(cached, dest)
        libraries=dest.with_suffix('')/'lib'
        libraries.mkdir(parents=True)
        with zipfile.ZipFile(dest) as archive:
            for name in ('avformat','avcodec','swscale','avutil','avfilter','swresample'):
                filename='lib'+name+'.a'
                content=archive.read(filename)
                if not content.startswith(b'!<arch>\n'):
                    raise RuntimeError('Invalid FFmpeg library')
                (libraries/filename).write_bytes(content)
    compiler_c, compiler_cxx = shutil.which('gcc-14'), shutil.which('g++-14')
    if not compiler_c or not compiler_cxx:
        raise RuntimeError('The previously tested GCC 14 compilers are unavailable; nothing installed')
    configure = ['cmake', '-S', source, '-B', build_dir, '-G', 'Ninja',
                 '-DCMAKE_BUILD_TYPE=Release', '-DENABLE_TESTS=OFF', '-DENABLE_UPDATER=OFF',
                 '-DENABLE_DISCORD_RPC=OFF', '-DCMAKE_CXX_SCAN_FOR_MODULES=OFF',
                 '-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF', '-DENABLE_CPU_ID_TRANSLATION=ON',
                 '-DCMAKE_C_COMPILER=' + compiler_c, '-DCMAKE_CXX_COMPILER=' + compiler_cxx]
    command(configure, log, root)
    command(['cmake', '--build', build_dir, '--target', 'shadps4', '--parallel',
             str(max(1, min(4, len(os.sched_getaffinity(0)))))], log, root, timeout=5400)
    binary = build_dir / 'shadps4'
    help_text = subprocess.check_output([binary, '--help'], stderr=subprocess.STDOUT, timeout=30).decode()
    if '--cpu-id-mode' not in help_text:
        raise RuntimeError('CPU identity startup is missing from the built emulator')
    runtime = build_dir / 'cpu-id-runtime'
    runtime_files = ['bin64/drrun', 'lib64/release/libdynamorio.so',
                     'lib64/release/libdrpreload.so', 'ext/lib64/release/libdrmgr.so',
                     'ext/lib64/release/libdrwrap.so', 'libshadps4_cpu_id.so']
    if not all((runtime / name).is_file() for name in runtime_files):
        raise RuntimeError('The built CPU identity runtime is incomplete')
    runtime_hashes = {name: digest(runtime / name) for name in runtime_files}
    say('Built emulator and bundled CPU identity runtime verified')
    record = {'base_commit': BASE, 'binary_sha256': digest(binary),
              'cpu_prerequisites': prerequisites, 'cpu_id_mode': 'translated',
              'cpu_baseline_patch_sha256': digest(HERE / 'cpu-baseline.patch'),
              'runtime_hashes': runtime_hashes,
              'instrumentation_sha256': digest(HERE / 'instrumentation.patch'),
              'compiler_c': compiler_c, 'compiler_cxx': compiler_cxx,
              'compiler_version': subprocess.check_output([compiler_cxx,'--version'],text=True).splitlines()[0],
              'source_hashes': {str(p.relative_to(source)):digest(p) for p in
                                [source/'src/core/cpu_patches.cpp',source/'src/core/scalar_ab.h',
                                 source/'src/sdl_window.cpp',source/'src/video_core/renderer_vulkan/vk_presenter.cpp',
                                 source/'src/imgui/notifications_layer.cpp']},
              'build_flags': [line for line in (build_dir/'CMakeCache.txt').read_text().splitlines()
                              if re.match(r'CMAKE_(CXX_FLAGS|C_FLAGS|BUILD_TYPE|INTERPROCEDURAL_OPTIMIZATION)',line)],
              'submodules': git(source, 'submodule', 'status', '--recursive')}
    write_json(report / 'build.json', record)
    return binary


def profile_seed(source, game, seed, active, home):
    say('Copying settings and PES saves into the temporary profile')
    config = json.loads((source / 'config.json').read_text())
    custom_file = source / 'custom_configs' / (SERIAL + '.json')
    custom = json.loads(custom_file.read_text()) if custom_file.is_file() else {}
    effective = dict(config.get('General', {}))
    effective.update(custom.get('General', {}))
    user = seed / 'user'
    user.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file() and path.suffix.lower() in {'.json', '.toml', '.ini', '.txt'}:
            profile.copy_path(path, user / path.name)
    for name in ['custom_configs', 'custom_modules', 'licenses', 'patches', 'cheats',
                 'custom_trophy', 'trophy', 'data', 'shader', 'cache']:
        say('Preparing profile directory: ' + name)
        profile.copy_path(source / name, user / name)
    profile.copy_path(source / 'game_data' / SERIAL, user / 'game_data' / SERIAL)
    protected = [p for p in source.iterdir()
                 if p.is_file() and p.suffix.lower() in {'.json', '.toml', '.ini', '.txt'}]
    protected += [source / 'custom_configs', source / 'patches', source / 'cheats']
    save_home = profile.absolute(effective['home_dir'], home) if effective.get('home_dir') else source / 'home'
    if not save_home.is_dir():
        raise RuntimeError('The existing save home is unavailable; refusing an empty-save comparison')
    for u in save_home.iterdir():
        if not u.is_dir() or not u.name.isdigit():
            continue
        for serial in {game['serial'], game['save_serial']}:
            save = u / 'savedata' / serial
            protected.append(save)
            say('Copying PES saves for user ' + u.name)
            profile.copy_path(save, user / 'home' / u.name / 'savedata' / serial)
        for name in ['inputs', 'trophy']:
            profile.copy_path(u / name, user / 'home' / u.name / name)
    users = json.loads((source / 'users.json').read_text())['Users']['user']
    for user_id in {str(u['user_id']) for u in users} | {'1000','1001','1002','1003'}:
        if not user_id.isdigit():
            raise RuntimeError('Invalid user ID')
        for name in ['savedata', 'trophy', 'inputs']:
            (user / 'home' / user_id / name).mkdir(parents=True, exist_ok=True)
    paths = {'home_dir': str(active / 'user/home')}
    for key, default in [('sys_modules_dir','sys_modules'), ('font_dir','fonts'),
                         ('addon_install_dir','addcont')]:
        say('Preparing profile resources: ' + default)
        original = profile.absolute(effective[key], home) if effective.get(key) else source / default
        if key == 'addon_install_dir':
            profile.copy_path(original / SERIAL, user / default / SERIAL)
        else:
            profile.copy_path(original, user / default)
        paths[key] = str(active / 'user' / default)
    for cfg, destination in [(config, user / 'config.json'), (custom, user / 'custom_configs' / (SERIAL + '.json'))]:
        cfg.setdefault('General', {}).update(paths)
        destination.parent.mkdir(exist_ok=True)
        write_json(destination, cfg)
    if any(p.is_symlink() for p in user.rglob('*')):
        raise RuntimeError('Profile copy contains a symlink')
    return protected, {name: custom.get(name, config.get(name)) for name in ('GPU','Vulkan','Audio')}


def control(stage, value):
    temporary = stage / 'control.new'
    temporary.write_text(value)
    temporary.replace(stage / 'control')


def process_sample(pid, started):
    p = Path('/proc') / str(pid)
    fields = (p / 'stat').read_text().rsplit(')',1)[1].split()
    if fields[0] == 'Z':
        raise ProcessLookupError(pid)
    sample = {'elapsed_s': time.monotonic()-started, 'monotonic_ns': time.monotonic_ns(),
              'cpu_seconds': (int(fields[11])+int(fields[12])) / os.sysconf('SC_CLK_TCK'),
              'rss_bytes': int(fields[21])*os.sysconf('SC_PAGE_SIZE'),
              'threads': int(fields[17]), 'system_loadavg': os.getloadavg(),
              'affinity': sorted(os.sched_getaffinity(pid))}
    sample['system_cpu_ticks'] = Path('/proc/stat').read_text().splitlines()[0].split()[1:]
    sample['thermal_millidegrees'] = {}
    for file in list(Path('/sys/class/thermal').glob('thermal_zone*/temp'))[:16]:
        try:
            sample['thermal_millidegrees'][str(file)] = int(file.read_text())
        except (OSError, ValueError):
            pass
    sample['gpu_global'] = {}
    for file in Path('/sys/class/drm').glob('card[0-9]*/device/gpu_busy_percent'):
        try:
            sample['gpu_global'][str(file)] = int(file.read_text())
        except (OSError, ValueError):
            pass
    return sample


def percentile(values, q):
    ordered = sorted(values)
    point = (len(ordered)-1)*q
    low = int(point)
    return ordered[low] + (ordered[min(low+1,len(ordered)-1)]-ordered[low])*(point-low)


def metrics(stage, started_ns, begin=30, end=120, counters_enabled=False):
    timestamps = []
    frames = stage / 'frames.csv'
    if frames.exists():
        for line in frames.read_text().splitlines():
            try:
                t, n = map(int,line.split(','))
                timestamps.append((t-started_ns)/1e9)
            except ValueError:
                continue
    selected = [t for t in timestamps if begin <= t < end]
    intervals = [(b-a)*1000 for a,b in zip(selected,selected[1:])]
    result = {'window_s': [begin,end], 'frames': len(selected),
              'fps_over_full_window': len(selected)/(end-begin),
              'first_frame_s': timestamps[0] if timestamps else None,
              'last_frame_s': timestamps[-1] if timestamps else None}
    if intervals:
        result.update(frame_ms_median=statistics.median(intervals),
                      frame_ms_p95=percentile(intervals,.95), frame_ms_p99=percentile(intervals,.99),
                      frame_ms_max=max(intervals), frames_over_33ms=sum(v>1000/30 for v in intervals),
                      frames_over_50ms=sum(v>50 for v in intervals))
    rows = [json.loads(s) for s in (stage / 'process.jsonl').read_text().splitlines()]
    rows = [r for r in rows if begin <= r['elapsed_s'] < end]
    if len(rows)>1:
        a,b=rows[0],rows[-1]
        result.update(cpu_percent=100*(b['cpu_seconds']-a['cpu_seconds'])/(b['elapsed_s']-a['elapsed_s']),
                      max_rss_mib=max(r['rss_bytes'] for r in rows)/(1024**2))
    counters = stage / 'counters.csv'
    if counters.exists():
        valid = [s for s in counters.read_text().splitlines() if len(s.split(','))==13]
        if valid:
            values = list(map(int,valid[-1].split(',')))
            result['scalar_counter_sample_s']=(values[0]-started_ns)/1e9
            names = ('rcpss','vrcpss','rsqrtss','vrsqrtss')
            result['scalar'] = {name: {'generated_sites':values[1+i],
                                      'executions':values[5+i] if counters_enabled else None,
                                      'changed_results':values[9+i] if counters_enabled else None} for i,name in enumerate(names)}
    return result


def stage_run(binary, root, report, seed, active, desktop, game, number, mode,
              duration=135, window=(30,120), screenshot_points=(123,129), sample_interval=1):
    require_idle()
    if active.exists():
        shutil.rmtree(active)
    shutil.copytree(seed,active)
    stage = report / f'{number:02d}-{mode}'
    stage.mkdir()
    env=clean_env(active,desktop)
    env.update(SHADPS4_SCALAR_AB_DIR=str(stage), SHADPS4_SCALAR_AB_MODE=mode,
               SHADPS4_CPU_ID_MODE='translated')
    record={'mode':mode,'diagnostic_excluded_from_speed':mode=='diagnostic',
            'profile_before':snapshot([active/'user/config.json',active/'user/users.json',
                                       active/'user/custom_configs',active/'user/home'])}
    # Hashes prove every run began with the same profile. Original save bytes are not archived.
    write_json(stage/'run.json',record)
    say(f'PES run {number}/5: {mode}; automated capture, {duration} seconds')
    with (stage/'console.log').open('w') as log, (stage/'process.jsonl').open('w') as samples:
        started_ns=time.monotonic_ns()
        child=subprocess.Popen([str(binary),'--game',str(game['boot_path']),'--fullscreen','true'],
                               cwd=active,env=env,stdin=subprocess.DEVNULL,stdout=log,
                               stderr=subprocess.STDOUT,start_new_session=True,
                               preexec_fn=lambda: child_setup(root))
        started=started_ns/1e9
        captured=set()
        last_progress=-1
        try:
            while child.poll() is None and time.monotonic()-started < duration:
                elapsed=time.monotonic()-started
                others=[p for p in profile.emulators() if p['pid']!=child.pid]
                if others:
                    raise RuntimeError('Another emulator started during capture; this run was stopped')
                for point in screenshot_points:
                    if elapsed>=point and point not in captured:
                        control(stage,'screenshot')
                        captured.add(point)
                try:
                    samples.write(json.dumps(process_sample(child.pid,started))+'\n')
                    samples.flush()
                except InterruptedError:
                    raise
                except (OSError,ProcessLookupError) as exc:
                    errors=record.setdefault('sampling_errors',[])
                    if len(errors)<10:
                        errors.append(str(exc))
                if int(elapsed)//15!=last_progress:
                    last_progress=int(elapsed)//15
                    say(f'Run {number}/5 {mode}: {int(elapsed)}/{duration}s')
                time.sleep(sample_interval)
            record['reached_capture_end']=time.monotonic()-started>=duration
        finally:
            record.update(stop(child,stage))
            record['elapsed_s']=time.monotonic()-started
            for dirname in ('log','screenshots'):
                path=active/'user'/dirname
                if path.exists():
                    shutil.copytree(path,stage/dirname)
            record['metrics']=metrics(stage,started_ns,*window,counters_enabled=mode=='diagnostic')
            record['started_monotonic_ns']=started_ns
            record['requested_duration_s']=duration
            record['unexpected_input']=(stage/'input.csv').exists()
            record['screenshots']=len(list((stage/'screenshots').glob('*.png')))
            record['speed_valid']=bool(record.get('reached_capture_end') and
                                       record['metrics']['frames'] and record['metrics'].get('last_frame_s',0)>=window[1]-1
                                       and not record['unexpected_input']
                                       and record['screenshots']==len(screenshot_points))
            write_json(stage/'run.json',record)
    return record


def comparison(records):
    timed=[r for r in records if r['mode']!='diagnostic']
    outcomes={mode:{'completed_observations':sum(r.get('reached_capture_end',False) for r in timed if r['mode']==mode),
                    'returncodes':[r['returncode'] for r in timed if r['mode']==mode],
                    'forced_cleanup':[r['forced_cleanup'] for r in timed if r['mode']==mode]}
              for mode in ('native','fixed')}
    if len(timed)!=4 or not all(r['speed_valid'] for r in timed):
        return {'valid':False,'outcomes':outcomes,
                'reason':'Incomplete run, missing game frames/screenshots, unexpected input, or early exit; no speed claim'}
    result={'valid':True,'outcomes':outcomes,'scope':'unattended boot/title/menu; gameplay not established',
            'scene_equivalence':'requires inspection of captured game-only screenshots',
            'caveat':'ABBA reduces ordering effects; two runs per mode do not establish statistical significance'}
    for mode in ('native','fixed'):
        values=[r['metrics']['fps_over_full_window'] for r in timed if r['mode']==mode]
        result[mode]={'fps_runs':values,'median_fps':statistics.median(values),
                      'run_spread_percent':100*(max(values)-min(values))/statistics.mean(values)}
    result['fixed_fps_change_percent']=100*(result['fixed']['median_fps']/result['native']['median_fps']-1)
    return result


def main(args):
    assets(args.assets_url)
    manual_mode = getattr(args, 'manual', False)
    debug_before = getattr(args, 'debug_before', False)
    debug_crash = getattr(args, 'debug_crash', False) or debug_before
    if debug_crash and not manual_mode:
        raise RuntimeError('GDB diagnostics require --manual')
    if manual_mode and not sys.stdin.isatty():
        raise RuntimeError('Manual capture needs an interactive SSH terminal')
    global profile
    sys.path.insert(0,str(HERE))
    import profile as profile_module
    profile=profile_module
    if manual_mode:
        import manual
        manual.r = sys.modules[__name__]
    home=Path.home()
    if os.geteuid()==0 or platform.machine()!='x86_64':
        raise RuntimeError('Run this from your usual x86-64 desktop user, not sudo')
    if 'GenuineIntel' not in Path('/proc/cpuinfo').read_text():
        raise RuntimeError('The scalar correction is gated to Intel hosts; refusing a no-op comparison')
    # Lock the directory inode without creating or changing a shared lock file.
    lock=os.open(home,os.O_RDONLY|os.O_DIRECTORY)
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    root=Path(tempfile.mkdtemp(prefix='shadps4-pes-scalar-work-',dir=home))
    report=root/'report'
    report.mkdir()
    say('PES test workspace: ' + str(root))
    for name in ['run.py',*ASSETS]:
        shutil.copy2(HERE/name,report/name)
    summary={'base_commit':BASE,'order':MODES,'records':[],'errors':[],
             'scope':'automated startup/title/menu, no gameplay input or match claim',
             'driver_cache_policy':'fresh empty temporary cache per run; same copied emulator caches',
             'measurement':'CPU-side new game-frame presentations; not GPU duration or display scanout',
             'cpu':next((s.split(':',1)[1].strip() for s in Path('/proc/cpuinfo').read_text().splitlines() if s.startswith('model name')),platform.processor()),'kernel':platform.release(),
             'runner_sha256':digest(__file__),'originals_unchanged':None}
    if manual_mode:
        summary.update(order=['native','fixed'], scope='manual ES-DE launch and user-selected scene')
    if debug_crash:
        summary.update(order=['native' if debug_before else 'fixed'],
                       scope='GDB crash diagnostic, not a performance comparison')
    protected=[]
    before=None
    archive=home/(root.name.replace('-work-','-')+'.tar.gz')
    try:
        say('Checking the desktop session, launcher, game path and build tools')
        require_idle()
        desktop,session=profile.desktop_environment()
        summary['desktop_source']=session
        source=home/'.local/share/shadPS4'
        if (home/'user').is_dir():
            raise RuntimeError('A portable profile exists in the original launch directory; needs reinspection')
        wrapper=home/'.local/bin/shadps4-esde'
        installed=home/'Applications/shadps4/shadps4'
        expected='exec '+str(installed)+' --game "$game" --fullscreen true'
        if expected not in wrapper.read_text().splitlines() or '# SHADPS4_SESSION_GUARD_V1' not in wrapper.read_text():
            raise RuntimeError('The launcher has changed from the captured setup; left untouched')
        if desktop.get('XDG_DATA_HOME') and Path(desktop['XDG_DATA_HOME']).resolve()!=home/'.local/share':
            raise RuntimeError('The desktop uses a different data directory; needs reinspection')
        context={'profile':source,'cwd':home,'args':[str(installed),'--game',SERIAL]}
        game=profile.find_game(context)
        if not game or game['serial']!=SERIAL or not game['boot_path'].is_file():
            raise RuntimeError('PES CUSA18676 was not resolved from the current configuration')
        summary['game']={k:str(v) for k,v in game.items()}
        for tool in ('git','cmake','ninja','gcc-14','g++-14'):
            if not shutil.which(tool):
                raise RuntimeError('Required existing tool is missing: '+tool)
        if debug_crash and not shutil.which('gdb'):
            raise RuntimeError('Existing GDB is required for crash capture; no packages were installed')
        if shutil.disk_usage(home).free<10*1024**3:
            raise RuntimeError('Less than 10 GiB free for the isolated build/profile')
        global USE_LANDLOCK
        USE_LANDLOCK=landlock_available()
        summary['write_isolation']='Landlock plus copied profile' if USE_LANDLOCK else 'copied portable profile; Landlock unavailable'
        if USE_LANDLOCK:
            say('Checking filesystem write isolation')
            outside=HERE/'isolation-probe'
            outside.write_text('preserved')
            try:
                subprocess.run([sys.executable,__file__,'--sandbox-probe',str(root),str(outside)],
                               check=True,timeout=15)
                if outside.read_text()!='preserved':
                    raise RuntimeError('Isolation probe modified its protected file')
            finally:
                outside.unlink(missing_ok=True)
        seed,active=root/'seed',root/'active'
        protected,settings=profile_seed(source,game,seed,active,home)
        say('Temporary profile ready; recording original file hashes')
        protected += [wrapper,installed,home/'.local/lib/shadps4-session-guard',game['sfo'],game['boot_path']]
        before=snapshot(protected)
        write_json(report/'original-state.json',before)
        summary['settings']=settings
        if debug_crash:
            import debugger
            debugger.preflight(sys.modules[__name__], manual, root, report,
                               fault_address=0x20 if debug_before else None)
        binary=build(root,report,home)
        require_idle()
        if snapshot(protected)!=before:
            raise RuntimeError('Original files changed during setup; stopping without overwriting them')
        if manual_mode:
            manual.sessions(binary,root,report,seed,active,game,wrapper,installed,summary,
                            debug_crash=debug_crash, debug_before=debug_before)
        else:
            for index,mode in enumerate(MODES,1):
                say('Cooling down for 20 seconds before the next run')
                time.sleep(20)
                record=stage_run(binary,root,report,seed,active,desktop,game,index,mode)
                summary['records'].append({k:v for k,v in record.items() if k!='profile_before'})
                if not record['reached_capture_end']:
                    say(f'Run {index} exited early; recorded. Continuing the other variant for comparison.')
            summary['comparison']=comparison(summary['records'])
    except BaseException as exc:
        summary['errors'].append(type(exc).__name__+': '+str(exc))
        (report/'error.txt').write_text(traceback.format_exc())
        say('Capture stopped: '+str(exc))
    finally:
        for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        if before is not None:
            try:
                after=snapshot(protected)
                summary['originals_unchanged']=before==after
                write_json(report/'original-state-after.json',after)
                if before!=after:
                    summary['errors'].append('Original-state hashes changed; no stale backup was written over them')
            except Exception as exc:
                summary['errors'].append('Original-state verification failed: '+str(exc))
        summary.setdefault('comparison',manual.comparison(summary['records']) if manual_mode
                           else comparison(summary['records']))
        summary['owned_processes_remaining']=[]
        try:
            summary['owned_processes_remaining']=[str(p['exe']) for p in profile.emulators()
                                                   if p['exe'].is_relative_to(root)]
        except Exception as exc:
            summary['errors'].append('Final process check: '+str(exc))
        if not summary['owned_processes_remaining'] and not summary.get('keep_work'):
            for path in root.iterdir():
                if path == report:
                    continue
                try:
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                except OSError as exc:
                    summary['errors'].append('Temporary cleanup failed: '+str(exc))
        archive_ok=False
        try:
            write_json(report/'summary.json',summary)
            partial=archive.with_suffix(archive.suffix+'.partial')
            with tarfile.open(partial,'w:gz') as output:
                output.add(report,arcname=archive.name.removesuffix('.tar.gz'))
            partial.replace(archive)
            archive_ok=True
        except Exception as exc:
            summary['errors'].append('Archive failed: '+str(exc))
            archive.with_suffix(archive.suffix+'.partial').unlink(missing_ok=True)
            try:
                write_json(report/'summary.json',summary)
            except OSError:
                pass
            say('Could not create the archive; evidence retained at '+str(report)+': '+str(exc))
        if archive_ok and not summary.get('keep_work') and not summary['owned_processes_remaining'] and not any(
                error.startswith('Temporary cleanup failed:') for error in summary['errors']):
            shutil.rmtree(root)
        elif archive_ok:
            say('Cleanup incomplete; work directory retained: '+str(root))
        os.close(lock)
        if archive_ok:
            say('PES_AB_ARCHIVE='+str(archive))
        say('Original emulator/settings/saves unchanged: '+str(summary['originals_unchanged']))
        say(json.dumps(summary.get('comparison',{'valid':False}),indent=2))
    return 1 if summary['errors'] or summary['owned_processes_remaining'] else 0


if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--sandbox-probe':
        probe(Path(sys.argv[2]),Path(sys.argv[3]))
        sys.exit(0)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets-url')
    parser.add_argument('--manual', action='store_true')
    debug_modes = parser.add_mutually_exclusive_group()
    debug_modes.add_argument('--debug-crash', action='store_true')
    debug_modes.add_argument('--debug-before', action='store_true')
    args=parser.parse_args()
    def interrupted(sig,frame):
        for handled in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
            signal.signal(handled,signal.SIG_IGN)
        raise InterruptedError('Received signal '+str(sig))
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
        signal.signal(sig,interrupted)
    sys.exit(main(args))
