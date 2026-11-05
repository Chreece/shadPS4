#!/usr/bin/env python3
"""Observe the host-verified candidate with an isolated profile and no input."""
import datetime as dt
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request

PIN = os.environ.get('SHADPS4_TEST_SOURCE_COMMIT', 'ed0fe5be055ee712756351479ac5411366e5385b')
ELF = '2866fdd415a3e3a82c9fa216530acb26fd4a4f4ef388e0aeec2b7f3ce8abc548'
MIN_FREE = 8 * 1024**3
OBSERVER_SHA256 = '316df1e2ac4bd5c0a3609eefb7238f843730abb183bccb0af3539876dff35eaf'
GPU_JOURNAL_PATTERN = r'(?i)\b(amdgpu|i915|xe|nouveau|nvidia|drm|gpu|kfd|xorg)\b|blocked for more than|Workqueue:'


def digest(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def save(p, value):
    p.write_text(json.dumps(value, indent=2) + '\n')


def collect_shader_diagnostics(runtime, out, console, captures):
    blocked = []
    recent = []
    recorded_work_hashes = []
    waiting_work_hashes = []
    first_wait_work_hashes = []
    first_wait_window = None
    in_first_wait = False
    first_wait_hashes_truncated = False
    work_reason = None
    current_hash = None
    capture_completed = False
    validation_hashes = []
    validation_compilation_hashes = []
    resource_warning_hashes = []
    readbacks_modes = set()
    automatic_coherence = False
    compilation_by_thread = {}
    validation_log = out / 'capture/vulkan-validation.log'
    if validation_log.is_file():
        for match in re.finditer(r'\b(?:cs|fs|vs|gs|hs|ls|es)_0x([0-9a-fA-F]{1,16})',
                                 validation_log.read_text(errors='replace')):
            shader_hash = format(int(match.group(1), 16), '016x')
            if shader_hash not in validation_hashes:
                validation_hashes.append(shader_hash)
            if len(validation_hashes) == 16:
                break
    if console.is_file():
        with console.open(errors='replace') as f:
            for line in f:
                line = re.sub(r'\x1b\[[0-9;]*m', '', line)
                work_begin = re.match(r'GRAPHICS_WORK_BEGIN reason=(\S+)', line)
                if work_begin:
                    work_reason = work_begin.group(1)
                    in_first_wait = (first_wait_window is None and
                                     work_reason in ('wait-timeout', 'wait-error'))
                    if in_first_wait:
                        first_wait_window = line.strip()
                elif line.startswith('GRAPHICS_WORK_END'):
                    work_reason = None
                    in_first_wait = False
                work = re.match(r'GRAPHICS_WORK seq=\d+ .*\bshader=([0-9a-fA-F]+)\b', line)
                if work:
                    shader_hash = format(int(work.group(1), 16), '016x')
                    if in_first_wait and shader_hash not in first_wait_work_hashes:
                        if len(first_wait_work_hashes) < 64:
                            first_wait_work_hashes.append(shader_hash)
                        else:
                            first_wait_hashes_truncated = True
                    if shader_hash in recorded_work_hashes:
                        recorded_work_hashes.remove(shader_hash)
                    recorded_work_hashes.append(shader_hash)
                    recorded_work_hashes = recorded_work_hashes[-16:]
                    if work_reason in ('wait-timeout', 'wait-error'):
                        if shader_hash in waiting_work_hashes:
                            waiting_work_hashes.remove(shader_hash)
                        waiting_work_hashes.append(shader_hash)
                        waiting_work_hashes = waiting_work_hashes[-16:]
                automatic_coherence |= 'GPU memory coherence: automatic' in line
                mode = re.search(r'GPU readbacksMode: (\d+)\b', line)
                if mode:
                    readbacks_modes.add(int(mode.group(1)))
                thread = re.search(r'<\w+> \(([^)]+)\)', line)
                match = re.search(r'Compiling \w+ shader (0x[0-9a-fA-F]+)', line)
                if match:
                    current_hash = format(int(match.group(1), 16), '016x')
                    if thread:
                        compilation_by_thread[thread.group(1)] = current_hash
                    if current_hash in recent:
                        recent.remove(current_hash)
                    recent.append(current_hash)
                    recent = recent[-8:]
                if ('<Error>' in line and 'VUID-VkShaderModuleCreateInfo-pCode-' in line and
                        thread and thread.group(1) in compilation_by_thread):
                    shader_hash = compilation_by_thread[thread.group(1)]
                    if (shader_hash not in validation_compilation_hashes and
                            len(validation_compilation_hashes) < 16):
                        validation_compilation_hashes.append(shader_hash)
                if current_hash and ('Unexpected instruction for offset computation' in line or
                                     'Ordered GDS preflight:' in line):
                    if current_hash in blocked:
                        blocked.remove(current_hash)
                    blocked.append(current_hash)
                match = re.search(r'BindBuffers: Clamped size .* for stage (0x[0-9a-fA-F]+)', line)
                if match:
                    shader_hash = format(int(match.group(1), 16), '016x')
                    if shader_hash in resource_warning_hashes:
                        resource_warning_hashes.remove(shader_hash)
                    resource_warning_hashes.append(shader_hash)
                    resource_warning_hashes = resource_warning_hashes[-8:]
                capture_completed |= 'PR117_REAL_GCN_CAPTURE_DONE epoch=' in line
        with console.open('rb') as f:
            start = max(0, console.stat().st_size - 16 * 1024**2)
            f.seek(start)
            (out / 'console-tail.log').write_bytes(f.read(16 * 1024**2))
    priority = validation_hashes.copy()
    priority.extend(h for h in validation_compilation_hashes if h not in priority)
    for capture in captures:
        match = re.fullmatch(r'ordered-gds-([0-9a-f]{16})-\d+\.bin', capture['file'])
        if match and match.group(1) not in priority:
            priority.append(match.group(1))
    priority.extend(h for h in reversed(blocked) if h not in priority)
    priority.extend(h for h in first_wait_work_hashes if h not in priority)
    priority.extend(h for h in reversed(waiting_work_hashes) if h not in priority)
    priority.extend(h for h in reversed(recorded_work_hashes) if h not in priority)
    priority.extend(h for h in reversed(resource_warning_hashes) if h not in priority)
    priority.extend(h for h in reversed(recent) if h not in priority)
    dumps = runtime / 'user/shader/dumps'
    groups = []
    binary_groups = []
    for shader_hash in priority:
        paths = [p for p in dumps.glob('*' + shader_hash + '*')
                 if p.is_file() and not p.is_symlink()]
        groups.append(sorted((p for p in paths if p.name.endswith('irprogram.txt')),
                             key=lambda p: ('.pre-' in p.name, p.name)))
        variants = {}
        for path in paths:
            if path.suffix in ('.bin', '.spv'):
                variants.setdefault(path.stem, []).append(path)
        variant_order = sorted(variants, key=lambda stem:
                               ({p.suffix for p in variants[stem]} != {'.bin', '.spv'},
                                stem.endswith('.fetch'),
                                -max(p.stat().st_mtime_ns for p in variants[stem]), stem))
        binary_groups.append([sorted(variants[stem]) for stem in variant_order])
    saved_ir = []
    total_ir = 0
    for depth in range(max(map(len, groups), default=0)):
        for group in groups:
            if depth >= len(group):
                continue
            path = group[depth]
            size = path.stat().st_size
            if len(saved_ir) >= 48 or total_ir + size > 20 * 1024**2:
                continue
            target = out / 'blocked-shader-ir' / path.name
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, target)
            saved_ir.append(path.name)
            total_ir += size
    saved_binary = []
    total_binary = 0
    for depth in range(max(map(len, binary_groups), default=0)):
        for group in binary_groups:
            if depth >= len(group):
                continue
            variant = group[depth]
            sizes = [path.stat().st_size for path in variant]
            if len(saved_binary) + len(variant) > 128 or total_binary + sum(sizes) > 8 * 1024**2:
                continue
            for path, size in zip(variant, sizes):
                target = out / 'blocked-shader-binary' / path.name
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(path, target)
                saved_binary.append({'file': path.name, 'bytes': size, 'sha256': digest(target)})
                total_binary += size
    saved_gcn = {Path(item['file']).stem for item in saved_binary
                 if item['file'].endswith('.bin') and not item['file'].endswith('.fetch.bin')}
    saved_spirv = {Path(item['file']).stem for item in saved_binary
                   if item['file'].endswith('.spv')}
    compiled_pairs = saved_gcn & saved_spirv
    return {'dump_enabled': True, 'blocked_hashes': sorted(set(blocked)),
            'validation_named_hashes': validation_hashes,
            'validation_compilation_hashes': validation_compilation_hashes,
            'resource_warning_hashes': list(reversed(resource_warning_hashes)),
            'observed_readbacks_modes': sorted(readbacks_modes),
            'automatic_coherence_enabled': automatic_coherence,
            'recorded_gpu_work_hashes': list(reversed(recorded_work_hashes)),
            'waiting_gpu_work_hashes': list(reversed(waiting_work_hashes)),
            'first_wait_window': first_wait_window,
            'first_wait_work_hashes': first_wait_work_hashes,
            'first_wait_hashes_truncated': first_wait_hashes_truncated,
            'first_wait_missing_binary_hashes': [h for h in first_wait_work_hashes
                if not any(h in stem for stem in compiled_pairs)],
            'recent_compiled_hashes': list(reversed(recent)),
            'priority_hashes': priority, 'saved_ir': saved_ir, 'saved_bytes': total_ir,
            'limit_bytes': 20 * 1024**2, 'limit_files': 48,
            'saved_binary': saved_binary, 'saved_binary_bytes': total_binary,
            'binary_unrepresented_hashes': [h for h in priority
                if not any(h in stem for stem in compiled_pairs)],
            'binary_limit_bytes': 8 * 1024**2, 'binary_limit_files': 128,
            'capture_completed_marker': capture_completed,
            'console_tail_complete': console.is_file() and console.stat().st_size <= 16 * 1024**2}


def diagnostic_command(argv, timeout):
    started = time.monotonic()
    result = {'command': argv, 'timed_out': False}
    try:
        completed = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                   timeout=timeout)
        result['exit_code'] = completed.returncode
        stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        result.update(timed_out=True, exit_code=None)
        stdout, stderr = exc.stdout or b'', exc.stderr or b''
    except OSError as exc:
        result.update(exit_code=None, error=str(exc))
        stdout, stderr = b'', b''
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    result['stdout'] = stdout[-128 * 1024:].decode('utf-8', 'replace')
    result['stderr'] = stderr[-16 * 1024:].decode('utf-8', 'replace')
    return result


def collect_display_health(out, display, query_display=True):
    authority = Path(os.environ.get('XAUTHORITY') or str(Path.home() / '.Xauthority'))
    health = {'display': display, 'xauthority_path': str(authority),
              'xauthority_exists': authority.is_file(),
              'local_x_sockets': sorted(str(p) for p in Path('/tmp/.X11-unix').glob('X*')),
              'processes': []}
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal():
            continue
        try:
            name = (process / 'comm').read_text().strip()
            if name not in ('X', 'Xorg', 'Xwayland', 'sunshine') and not name.lower().startswith('shadps4'):
                continue
            state = {'pid': int(process.name), 'name': name}
            for line in (process / 'status').read_text().splitlines():
                key, _, value = line.partition(':')
                if key in ('State', 'PPid', 'Uid', 'Threads', 'VmRSS'):
                    state[key] = value.strip()
            try:
                state['wait_channel'] = (process / 'wchan').read_text().strip()
            except OSError:
                pass
            health['processes'].append(state)
        except OSError:
            continue
    save(out / 'display-host-state.json', health)
    if query_display:
        save(out / 'display-xset.json', diagnostic_command(['xset', '-display', display, 'q'], 3))
    save(out / 'kernel-journal.json', diagnostic_command(
        ['journalctl', '-k', '-b', '--no-pager', '-n', '600', '-o', 'short-monotonic'], 5))
    save(out / 'kernel-gpu-journal.json', diagnostic_command(
        ['journalctl', '-k', '-b', '--no-pager', '-n', '600', '-o', 'short-monotonic',
         '--grep', GPU_JOURNAL_PATTERN], 5))
    match = re.fullmatch(r':(\d+)(?:\.\d+)?', display)
    if match:
        name = 'Xorg.' + match.group(1) + '.log'
        for index, directory in enumerate((Path('/var/log'), Path.home() / '.local/share/xorg')):
            path = directory / name
            try:
                with path.open('rb') as f:
                    f.seek(max(0, path.stat().st_size - 128 * 1024))
                    (out / ('display-xorg-' + str(index) + '.log')).write_bytes(f.read())
            except OSError:
                pass


def blocked_display_tasks(proc=Path('/proc')):
    blocked = []
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            owner = (process / 'comm').read_text().strip()
            if owner not in ('X', 'Xorg', 'Xwayland', 'sunshine'):
                continue
            for task in (process / 'task').iterdir():
                try:
                    stat = (task / 'stat').read_text()
                    fields = stat.rsplit(') ', 1)[1].split()
                    if fields[0] != 'D':
                        continue
                    blocked.append({'pid': int(process.name), 'tid': int(task.name),
                                    'start_time': int(fields[19]), 'process': owner,
                                    'thread': stat.split('(', 1)[1].rsplit(') ', 1)[0]})
                except (OSError, IndexError, ValueError):
                    continue
        except OSError:
            continue
    return blocked


def verify_display_tasks(out, record, display):
    first = blocked_display_tasks()
    if not first:
        return
    time.sleep(.25)
    second = blocked_display_tasks()
    identity = lambda task: (task['pid'], task['tid'], task['start_time'])
    prior = {identity(task) for task in first}
    persistent = [task for task in second if identity(task) in prior]
    save(out / 'display-blocked-tasks.json', {'first': first, 'second': second,
                                             'persistent': persistent})
    if not persistent:
        return
    record['preflight_failure'] = 'DISPLAY_TASKS_BLOCKED_IN_KERNEL'
    record['stop_remaining_tests'] = True
    record['blocked_display_tasks'] = persistent
    try:
        collect_display_health(out, display, query_display=False)
    except Exception as exc:
        record['display_diagnostic_error'] = str(exc)
    raise RuntimeError('DISPLAY_TASKS_BLOCKED_IN_KERNEL; host recovery required before GPU testing')


def verify_display(out, record, display):
    verify_display_tasks(out, record, display)
    probe = diagnostic_command(['xdpyinfo', '-display', display], 8)
    save(out / 'display-preflight.json', probe)
    record['display'] = display
    record['display_preflight'] = {'exit_code': probe['exit_code'],
                                   'timed_out': probe['timed_out']}
    if probe['exit_code'] == 0:
        return
    record['preflight_failure'] = ('DISPLAY_UNRESPONSIVE' if probe['timed_out']
                                   else 'DISPLAY_UNAVAILABLE')
    record['stop_remaining_tests'] = True
    try:
        collect_display_health(out, display)
    except Exception as exc:
        record['display_diagnostic_error'] = str(exc)
    raise RuntimeError(record['preflight_failure'] + ' display=' + display)


def free_vram():
    cards = []
    for device in Path('/sys/class/drm').glob('card[0-9]*/device'):
        try:
            total = int((device / 'mem_info_vram_total').read_text())
            used = int((device / 'mem_info_vram_used').read_text())
            cards.append((total, total - used))
        except (OSError, ValueError):
            pass
    if not cards:
        raise RuntimeError('VRAM_TELEMETRY_UNAVAILABLE')
    return max(cards)[1]


def ollama(path, body=None, base_url='http://127.0.0.1:11434', timeout=15):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base_url + '/api/' + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as response:
        return json.load(response)


def ollama_endpoints(record):
    yield 'http://127.0.0.1:11434'
    if not shutil.which('docker'):
        return
    try:
        listing = subprocess.run(
            ['docker', 'ps', '--format', '{{.ID}}\t{{.Image}}\t{{.Names}}'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        if listing.returncode:
            raise RuntimeError('DOCKER_PS_FAILED')
        for row in listing.stdout.splitlines():
            fields = row.split('\t')
            if len(fields) != 3 or 'ollama' not in (fields[1] + ' ' + fields[2]).lower():
                continue
            container = fields[0]
            if not re.fullmatch(r'[0-9a-f]{12,64}', container):
                continue
            result = subprocess.run(
                ['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', container],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
            if result.returncode:
                continue
            for network in json.loads(result.stdout).values():
                address = ipaddress.ip_address(network.get('IPAddress') or '0.0.0.0')
                if address.version == 4 and address.is_private and not address.is_unspecified:
                    yield 'http://' + str(address) + ':11434'
    except (OSError, ValueError, subprocess.TimeoutExpired, RuntimeError) as exc:
        record['ollama_attempts'].append({'transport': 'docker-discovery', 'error': str(exc)})


def unload_models(out, record):
    record['ollama_attempts'] = []
    record['ollama_restore_leases'] = []
    print('VRAM_LOW_SNAPSHOTTING_LOCAL_OLLAMA_MODELS', flush=True)
    for endpoint in ollama_endpoints(record):
        try:
            models = ollama('ps', base_url=endpoint).get('models', [])
            leases = []
            for model in models:
                name = model.get('name') or model.get('model')
                context = model.get('context_length')
                expiry = dt.datetime.fromisoformat(model['expires_at'].replace('Z', '+00:00'))
                if (not isinstance(name, str) or not name or not isinstance(context, int) or
                        isinstance(context, bool) or context <= 0 or expiry.tzinfo is None):
                    raise ValueError('MODEL_RESTORE_METADATA_INCOMPLETE')
                leases.append({'endpoint': endpoint, 'model': name, 'context_length': context,
                               'expires_at': expiry.isoformat(), 'digest': model.get('digest'),
                               'size_vram': model.get('size_vram'), 'status': 'CAPTURED'})
            save(out / 'ollama-before.json', models)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            record['ollama_attempts'].append({'transport': endpoint, 'error': str(exc)})
            continue
        for lease in leases:
            lease['status'] = 'UNLOAD_REQUESTED'
            record['ollama_restore_leases'].append(lease)
            save(out / 'ollama-restore.json', record['ollama_restore_leases'])
            ollama('generate', {'model': lease['model'], 'keep_alive': 0, 'stream': False},
                   base_url=endpoint)
            lease['status'] = 'UNLOADED'
            record['ollama_unloaded'].append(lease['model'])
            save(out / 'ollama-restore.json', record['ollama_restore_leases'])
        record['ollama_attempts'].append({'transport': endpoint, 'result': 'SUCCESS'})
        return
    raise RuntimeError('NO_LOCAL_OLLAMA_API_WITH_RESTORABLE_MODEL_METADATA')


def restore_models(out, record, observer):
    leases = record.get('ollama_restore_leases', [])
    if not leases:
        return
    native = record.get('native', {})
    unsafe = (native.get('gpu_failure') or
              record.get('game_launched') and not native.get('owned_child_terminated', False))
    if not unsafe:
        try:
            unsafe = observer is None or bool(observer.existing_games())
        except Exception:
            unsafe = True
    for lease in leases:
        if unsafe:
            lease['status'] = 'DEFERRED_GPU_FAILURE_OR_LIVE_PROCESS'
            continue
        try:
            models = ollama('ps', base_url=lease['endpoint']).get('models', [])
            current = next((model for model in models
                            if (model.get('name') or model.get('model')) == lease['model']), None)
            if current is not None:
                if (current.get('context_length') != lease['context_length'] or
                        lease.get('digest') and current.get('digest') != lease['digest']):
                    raise RuntimeError('MODEL_CHANGED_DURING_TEST_NOT_OVERWRITTEN')
                lease['status'] = 'ALREADY_LOADED'
                continue
            expiry = dt.datetime.fromisoformat(lease['expires_at'])
            remaining = int((expiry - dt.datetime.now(dt.timezone.utc)).total_seconds())
            if remaining <= 0:
                lease['status'] = 'ORIGINAL_EXPIRY_ELAPSED'
                continue
            if lease.get('digest'):
                installed = ollama('tags', base_url=lease['endpoint']).get('models', [])
                tagged = next((model for model in installed
                               if (model.get('name') or model.get('model')) == lease['model']), {})
                if tagged.get('digest') != lease['digest']:
                    raise RuntimeError('MODEL_TAG_CHANGED_DURING_TEST_NOT_LOADED')
            print('RESTORING_OLLAMA_MODEL=' + lease['model'], flush=True)
            ollama('generate', {'model': lease['model'], 'stream': False,
                               'keep_alive': str(remaining) + 's',
                               'options': {'num_ctx': lease['context_length']}},
                   base_url=lease['endpoint'], timeout=60)
            models = ollama('ps', base_url=lease['endpoint']).get('models', [])
            restored = next((model for model in models
                             if (model.get('name') or model.get('model')) == lease['model']), {})
            if (restored.get('context_length') != lease['context_length'] or
                    lease.get('digest') and restored.get('digest') != lease['digest']):
                raise RuntimeError('RESTORED_MODEL_CONTEXT_OR_DIGEST_NOT_CONFIRMED')
            lease['status'] = 'RESTORED'
            lease['restored_size_vram'] = restored.get('size_vram')
        except Exception as exc:
            lease['status'] = 'RESTORE_FAILED'
            lease['error'] = str(exc)
            record['stop_remaining_tests'] = True
    save(out / 'ollama-restore.json', leases)


def profile_source(project):
    candidates = [project / 'user', project / 'build/user',
                  Path.home() / '.local/share/shadPS4', project / 'GhostOfTsushima/user']
    for base in [project / 'GhostOfTsushima/experiments', project / 'tests']:
        candidates.extend(sorted(base.glob('*/user'), key=lambda p: p.stat().st_mtime,
                                 reverse=True))
        candidates.extend(sorted(base.glob('*/runtime/user'), key=lambda p: p.stat().st_mtime,
                                 reverse=True))
    for p in candidates:
        if (p / 'sys_modules').is_dir() and any((p / 'sys_modules').glob('*.sprx')):
            return p.resolve()
    raise RuntimeError('NO_EXISTING_PROFILE_WITH_SYSTEM_MODULES')


def copy_profile(old, new, diagnostics=False):
    allowed = {'sys_modules', 'custom_modules', 'fonts', 'licenses', 'home',
               'savedata', 'game_data', 'trophy', 'config.json'}
    files = []
    for name in allowed:
        src = old / name
        if not src.exists():
            continue
        paths = [src] if src.is_file() else list(src.rglob('*'))
        for p in paths:
            if p.is_symlink():
                raise RuntimeError('PROFILE_SYMLINK_REFUSED:' + str(p))
            if p.is_file():
                files.append(p)
    if sum(p.stat().st_size for p in files) > 2 * 1024**3:
        raise RuntimeError('PROFILE_COPY_EXCEEDS_2_GIB')
    new.mkdir(parents=True)
    for p in files:
        target = new / p.relative_to(old)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    config = new / 'config.json'
    if config.is_file():
        data = json.loads(config.read_text())
        def relocate(value):
            if isinstance(value, dict):
                return {k: relocate(v) for k, v in value.items()}
            if isinstance(value, list):
                return [relocate(v) for v in value]
            if isinstance(value, str) and (value == str(old) or value.startswith(str(old) + '/')):
                return str(new) + value[len(str(old)):]
            return value
        data = relocate(data)
        data.setdefault('General', {})['home_dir'] = str(new / 'home')
        data.setdefault('GPU', {})['dump_shaders'] = True
    else:
        data = {'GPU': {'dump_shaders': True}}
    if diagnostics:
        data.setdefault('Vulkan', {}).update(
            vkvalidation_enabled=True, vkvalidation_core_enabled=True,
            vkvalidation_sync_enabled=True, vkvalidation_gpu_enabled=False,
            vkhost_markers=True, vkcrash_diagnostic_enabled=False)
        log = data.setdefault('Log', {})
        log.update(enable=True, sync=True, flush_level='error')
        log['filter'] = (log.get('filter', '').strip() + ' Render.Vulkan:info').strip()
    save(config, data)
    return len(files)


def verify_validation_layer(out, record):
    import ctypes

    class LayerProperties(ctypes.Structure):
        _fields_ = [('name', ctypes.c_char * 256), ('spec_version', ctypes.c_uint32),
                    ('implementation_version', ctypes.c_uint32),
                    ('description', ctypes.c_char * 256)]

    try:
        loader = ctypes.CDLL('libvulkan.so.1')
        enumerate_layers = loader.vkEnumerateInstanceLayerProperties
        enumerate_layers.restype = ctypes.c_int32
        enumerate_layers.argtypes = [ctypes.POINTER(ctypes.c_uint32),
                                     ctypes.POINTER(LayerProperties)]
        for _ in range(3):
            count = ctypes.c_uint32()
            if enumerate_layers(ctypes.byref(count), None) != 0 or count.value > 1024:
                raise RuntimeError('VULKAN_LAYER_ENUMERATION_FAILED')
            capacity = count.value
            properties = (LayerProperties * capacity)()
            result = enumerate_layers(ctypes.byref(count), properties)
            if result == 5:
                continue
            if result != 0 or count.value > capacity:
                raise RuntimeError('VULKAN_LAYER_ENUMERATION_FAILED')
            layers = [{'name': p.name.decode('utf-8', 'replace'),
                       'spec_version': p.spec_version,
                       'implementation_version': p.implementation_version}
                      for p in properties[:count.value]]
            save(out / 'vulkan-layers.json', layers)
            record['vulkan_layers'] = layers
            if not any(p['name'] == 'VK_LAYER_KHRONOS_validation' for p in layers):
                raise RuntimeError('VULKAN_VALIDATION_LAYER_MISSING')
            return
        raise RuntimeError('VULKAN_LAYER_ENUMERATION_UNSTABLE')
    except (OSError, AttributeError, RuntimeError) as exc:
        record['preflight_failure'] = str(exc)
        record['stop_remaining_tests'] = True
        raise


def verify_upstream(source, record):
    upstream = subprocess.check_output(
        ['git', 'ls-remote', 'https://github.com/shadps4-emu/shadPS4.git', 'refs/heads/main'],
        text=True, timeout=30).split()
    if len(upstream) != 2 or not re.fullmatch(r'[0-9a-f]{40}', upstream[0]):
        raise RuntimeError('UPSTREAM_MAIN_CHECK_FAILED')
    record['upstream_main_checked'] = upstream[0]
    result = subprocess.run(['git', '-C', str(source), 'merge-base', '--is-ancestor',
                             upstream[0], PIN], capture_output=True, timeout=15)
    if result.returncode:
        record['stop_remaining_tests'] = True
        raise RuntimeError('BLOCKED_UPSTREAM_ADVANCED: integrate latest upstream before testing')


def recover_incomplete_captures(project, out, observer):
    recovered = []
    for prior in sorted((project / 'logs').glob('universal-graphics-*/result.json'), reverse=True):
        capture = prior.parent / 'capture'
        console = capture / 'full-console.log'
        if prior.parent == out or (capture / 'native-result.json').exists() or not console.is_file():
            continue
        if len(recovered) == 4:
            break
        target = out / 'recovered-captures' / prior.parent.name
        target.mkdir(parents=True)
        try:
            summary = observer.summarize(console, target)
            summary['original_result'] = json.loads(prior.read_text())
            summary['recovered_from'] = str(console)
            save(target / 'recovered-summary.json', summary)
            recovered.append(prior.parent.name)
        except (OSError, ValueError) as exc:
            save(target / 'recovery-error.json', {'error': str(exc)})
    return recovered


def main():
    os.umask(0o077)
    project = Path.home() / 'projects/shadps4'
    tag = 'universal-graphics-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
    out = project / 'logs' / tag
    out.mkdir(parents=True)
    runtime = project / 'tests' / tag
    base = project / 'tests' / ('universal-upstream-' + PIN[:12])
    source = base / 'source'
    binary = base / 'build/shadps4'
    record = {'schema_version': 2, 'record_id': tag, 'scope': 'UNIVERSAL_SHADPS4',
              'timestamp_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'source_commit': PIN, 'binary_sha256': ELF, 'game_launched': False,
              'graphics_verified': False, 'installed_binary_changed': False,
              'evidence': str(out) + '.tar.gz', 'result': 'PREFLIGHT_FAILED'}
    record['runner_commit'] = os.environ.get('SHADPS4_TEST_RUNNER_COMMIT', PIN)
    record['runner_sha256'] = digest(Path(__file__))
    diagnostics = os.environ.get('SHADPS4_VULKAN_DIAGNOSTICS') == '1'
    record['vulkan_diagnostics_requested'] = diagnostics
    rc = 1
    game = Path(os.environ.get('SHADPS4_TEST_GAME', '/mnt/roms-all/ps4-games/CUSA11456/eboot.bin'))
    record['context_games'] = [game.parent.name]
    expected_elf = ELF
    module = None
    lock = (project / 'tests/.universal-upstream-build.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not all(re.fullmatch(r'[0-9a-f]{40}', value)
                   for value in (PIN, record['runner_commit'])):
            raise RuntimeError('INVALID_SOURCE_PIN')
        if PIN != 'ed0fe5be055ee712756351479ac5411366e5385b':
            matches = []
            for result_path in (project / 'logs').glob('universal-upstream-*/result.json'):
                try:
                    build_record = json.loads(result_path.read_text())
                except (OSError, ValueError):
                    continue
                if (build_record.get('source_commit') == PIN and
                    build_record.get('result') == 'PASS_NATIVE_BUILD_CPU_AND_STARTUP_TESTS_GPU_NOT_RUN' and
                    build_record.get('candidate_binary') == str(binary) and
                    re.fullmatch(r'[0-9a-f]{64}', build_record.get('binary_sha256', ''))):
                    matches.append((result_path.stat().st_mtime, result_path, build_record))
            if not matches:
                raise RuntimeError('NO_SUCCESSFUL_BUILD_RECORD_FOR_SOURCE')
            _, result_path, build_record = max(matches)
            expected_elf = build_record['binary_sha256']
            record['build_record'] = str(result_path)
            record['binary_sha256'] = expected_elf
        if digest(binary) != expected_elf:
            raise RuntimeError('VERIFIED_BINARY_CHANGED')
        head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if head != PIN:
            raise RuntimeError('VERIFIED_SOURCE_CHANGED')
        verify_upstream(source, record)
        observer = source / 'tools/development/shadps4_native_120_observer_20261026.py'
        committed = subprocess.check_output(['git', '-C', str(source), 'show', PIN + ':' + str(observer.relative_to(source))])
        if observer.read_bytes() != committed:
            raise RuntimeError('OBSERVER_CHANGED')
        if record['runner_commit'] != PIN:
            url = ('https://raw.githubusercontent.com/Chreece/shadPS4/' + record['runner_commit'] +
                   '/tools/development/shadps4_native_120_observer_20261026.py')
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read(1024 * 1024)
            observer = out / 'observer.py'
            observer.write_bytes(data)
        if digest(observer) != OBSERVER_SHA256:
            raise RuntimeError('RUNNER_OBSERVER_HASH_MISMATCH')
        record['observer_sha256'] = OBSERVER_SHA256
        spec = importlib.util.spec_from_file_location('observer', observer)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        record['recovered_captures'] = recover_incomplete_captures(project, out, module)
        if module.existing_games():
            raise RuntimeError('AN_EMULATOR_IS_ALREADY_RUNNING')
        game = Path(os.environ.get('SHADPS4_TEST_GAME', '/mnt/roms-all/ps4-games/CUSA11456/eboot.bin'))
        if not game.is_file():
            raise RuntimeError('GAME_FILE_MISSING:' + str(game))
        display = os.environ.get('DISPLAY') or ':0'
        os.environ['DISPLAY'] = display
        if not shutil.which('import'):
            raise RuntimeError('SCREENSHOT_TOOL_IMPORT_MISSING')
        verify_display(out, record, display)
        if diagnostics:
            verify_validation_layer(out, record)
        old = profile_source(project)
        record['profile_source'] = str(old)
        record['profile_files_copied'] = copy_profile(old, runtime / 'user', diagnostics)
        profile_config = runtime / 'user/config.json'
        record['profile_config_sha256'] = digest(profile_config)
        settings = json.loads(profile_config.read_text())
        record['configured_readbacks_mode'] = settings.get('GPU', {}).get('readbacks_mode')
        if diagnostics:
            save(out / 'vulkan-diagnostic-settings.json',
                 {key: settings[key] for key in ('Vulkan', 'Log')})
        record['profile_policy'] = 'isolated saves; global config; no custom title configs, patches, cheats or old shader cache'
        record['vram_free_before'] = free_vram()
        record['ollama_unloaded'] = []
        if record['vram_free_before'] < MIN_FREE:
            unload_models(out, record)
            until = time.monotonic() + 20
            while free_vram() < MIN_FREE and time.monotonic() < until:
                time.sleep(.5)
        record['vram_free_at_launch'] = free_vram()
        if record['vram_free_at_launch'] < MIN_FREE:
            raise RuntimeError('LESS_THAN_8_GIB_VRAM_FREE')
        os.environ['SHADPS4_GRAPHICS_DIAGNOSTICS'] = '1'
        record['graphics_diagnostics_requested'] = True
        sys.argv = [str(observer), '--binary', str(binary.resolve()), '--sha256', expected_elf,
                    '--game', str(game), '--cwd', str(runtime), '--report', str(out / 'capture'),
                    '--seconds', '118']
        print('STARTING_VERIFIED_CANDIDATE NO_INPUT MAXIMUM_120_SECONDS', flush=True)
        rc = module.main()
        native = json.loads((out / 'capture/native-result.json').read_text())
        record['game_launched'] = (out / 'capture/launch.json').is_file()
        record['native'] = native
        record['tested_games'] = [game.parent.name] if record['game_launched'] else []
        if rc == 0 and len(native.get('screenshots', [])) < 2:
            rc = 1
            record['result'] = 'INSUFFICIENT_SCREENSHOTS'
        else:
            record['result'] = 'CAPTURE_COMPLETE_VISUAL_REVIEW_REQUIRED' if rc == 0 else 'GAME_OR_OBSERVER_FAILED'
    except (Exception, KeyboardInterrupt) as exc:
        record['error'] = str(exc) or type(exc).__name__
        print('STOP=' + record['error'], flush=True)
    finally:
        record['game_launched'] = (out / 'capture/launch.json').is_file()
        record['tested_games'] = [game.parent.name] if record['game_launched'] else []
        if record['game_launched'] and record['result'] == 'PREFLIGHT_FAILED':
            record['result'] = 'GAME_OR_OBSERVER_FAILED'
        native_path = out / 'capture/native-result.json'
        console = out / 'capture/full-console.log'
        if record['game_launched'] and 'native' not in record:
            try:
                if native_path.is_file():
                    record['native'] = json.loads(native_path.read_text())
                elif module is not None and console.is_file():
                    record['native'] = module.summarize(console, console.parent)
            except (OSError, ValueError) as exc:
                record['capture_error'] = str(exc)
        record['stop_remaining_tests'] = bool(record.get('stop_remaining_tests') or
            record['game_launched'] and (
            record.get('native', {}).get('gpu_failure') or
            not record.get('native', {}).get('graphics_diagnostics_active') or
            diagnostics and not record.get('native', {}).get('validation_layer_active') or
            not record.get('native', {}).get('owned_child_terminated', False)))
        if record.get('native', {}).get('gpu_failure'):
            try:
                collect_display_health(out, os.environ.get('DISPLAY') or ':0', query_display=False)
            except Exception as exc:
                record['display_diagnostic_error'] = str(exc)
        logs = runtime / 'user/log'
        if logs.is_dir():
            for log in logs.glob('*.txt'):
                if log.is_file() and not log.is_symlink():
                    with log.open('rb') as f:
                        f.seek(max(0, log.stat().st_size - 2 * 1024**2))
                        (out / ('emulator-' + log.name)).write_bytes(f.read())
        record['ordered_gds_capture_requested'] = os.environ.get('SHADPS4_PR117_CAPTURE_ONCE') == '1'
        record['ordered_gds_evidence'] = []
        if logs.is_dir():
            for capture in sorted(logs.glob('ordered-gds-*.bin')):
                if capture.is_symlink() or not capture.is_file():
                    continue
                size = capture.stat().st_size
                if len(record['ordered_gds_evidence']) >= 2 or not 48 <= size <= 4 * 1024**2:
                    continue
                target = out / capture.name
                shutil.copyfile(capture, target)
                record['ordered_gds_evidence'].append({
                    'file': target.name, 'bytes': size, 'sha256': digest(target),
                    'guest_continuation_executed': False})
        record['shader_diagnostics'] = collect_shader_diagnostics(
            runtime, out, out / 'capture/full-console.log', record['ordered_gds_evidence'])
        if (record['ordered_gds_capture_requested'] and record['ordered_gds_evidence'] and
                record['shader_diagnostics']['capture_completed_marker'] and
                not record['stop_remaining_tests']):
            record['result'] = 'ORDERED_GDS_FIRST_PASS_CAPTURED_CONTINUATION_PENDING'
        restore_models(out, record, module)
        record['exit_code'] = rc
        save(out / 'result.json', record)
        for folder in [project / 'docs/emulator', project / 'games' / game.parent.name / 'docs/emulator']:
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / 'TRIALS.jsonl').open('a') as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.write(json.dumps(record) + '\n')
                f.flush()
                os.fsync(f.fileno())
        with tarfile.open(str(out) + '.tar.gz', 'w:gz') as tar:
            for p in out.rglob('*'):
                if p.is_file() and p.name != 'full-console.log':
                    tar.add(p, arcname=str(Path(tag) / p.relative_to(out)))
        print('RESULT=' + record['result'], flush=True)
        print('EVIDENCE=' + str(out) + '.tar.gz', flush=True)
        lock.close()
    return rc


if __name__ == '__main__':
    def terminate(signum, frame):
        raise KeyboardInterrupt('TERMINATED_BY_USER')
    signal.signal(signal.SIGTERM, terminate)
    sys.exit(main())
