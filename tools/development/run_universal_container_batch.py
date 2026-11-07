#!/usr/bin/env python3
"""Hold local Ollama containers stopped across a guarded native capture batch."""
import datetime as dt
import fcntl
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time


INSPECT = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},'
           '"image_id":{{json .Image}},"created":{{json .Created}},'
           '"state":{{json .State}},"restart":{{json .HostConfig.RestartPolicy}},'
           '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
           '"swarm":{{json (index .Config.Labels "com.docker.swarm.service.id")}},'
           '"network_mode":{{json .HostConfig.NetworkMode}},'
           '"networks":{{json .NetworkSettings.Networks}}}')


def save(path, value):
    temporary = path.with_suffix('.pending')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Docker:
    def __init__(self):
        executable = shutil.which('docker')
        if not executable:
            raise RuntimeError('DOCKER_REQUIRED_FOR_CONTAINER_ISOLATION')
        endpoint = os.environ.get('DOCKER_HOST')
        if not endpoint:
            endpoint = subprocess.check_output(
                [executable, 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'],
                text=True, timeout=10).strip()
        if not endpoint.startswith('unix:///') or '\n' in endpoint:
            raise RuntimeError('LOCAL_UNIX_DOCKER_ENDPOINT_REQUIRED')
        self.prefix = [executable, '--host', endpoint]
        try:
            self.call('info', '--format', '{{.ID}}')
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self.prefix = ['sudo', '-n', '--', *self.prefix]
            self.call('info', '--format', '{{.ID}}')
        self.endpoint = endpoint

    def call(self, *args, timeout=10):
        result = subprocess.run([*self.prefix, *args], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError('DOCKER_' + args[0].upper() + '_FAILED:' + result.stderr[:400])
        return result.stdout

    def inspect(self, ids, timeout=10):
        if not ids or any(not re.fullmatch(r'[0-9a-f]{64}', item) for item in ids):
            raise RuntimeError('FULL_CONTAINER_IDS_REQUIRED')
        rows = [json.loads(line) for line in self.call(
            'container', 'inspect', '--format', INSPECT, *ids, timeout=timeout).splitlines()]
        if {row['id'] for row in rows} != set(ids):
            raise RuntimeError('CONTAINER_INSPECTION_INCOMPLETE')
        for row in rows:
            row['state'] = {key: row['state'][key] for key in
                            ('Status', 'Running', 'Paused', 'Restarting', 'Pid',
                             'StartedAt', 'FinishedAt')}
        return rows

    def candidates(self, explicit):
        ids = self.call('container', 'ls', '--all', '--no-trunc', '--format', '{{.ID}}').split()
        if not set(explicit).issubset(ids):
            raise RuntimeError('EVIDENCE_CONTAINER_MISSING; do not substitute a different container')
        rows = self.inspect(ids) if ids else []
        selected = []
        for row in rows:
            image = row['image'].split('@')[0].rsplit('/', 1)[-1].split(':')[0]
            if (row['id'] in explicit or row['name'].lstrip('/') == 'ollama' or
                    row['service'] == 'ollama' or image == 'ollama'):
                if row['swarm'] or row['state']['Paused'] or row['state']['Restarting']:
                    raise RuntimeError('UNSTABLE_OR_ORCHESTRATED_OLLAMA_CONTAINER')
                selected.append(row)
        if not selected:
            raise RuntimeError('NO_OLLAMA_CONTAINER_IDENTIFIED')
        return selected


def endpoints(row):
    result = []
    if row['network_mode'] == 'host':
        result.append('http://127.0.0.1:11434')
    for network in (row['networks'] or {}).values():
        address = ipaddress.ip_address(network.get('IPAddress') or '0.0.0.0')
        if address.version == 4 and address.is_private and not address.is_unspecified:
            result.append('http://' + str(address) + ':11434')
    return list(dict.fromkeys(result))


def identity(row):
    return tuple(row[key] for key in ('id', 'name', 'image_id', 'created'))


def stopped(row):
    state = row['state']
    return not any(state[key] for key in ('Running', 'Paused', 'Restarting', 'Pid'))


class ContainerLease:
    def __init__(self, docker, runner, path, state=None):
        self.docker, self.runner, self.path = docker, runner, path
        self.state = state or {'version': 1, 'guard_pid': os.getpid(),
                              'docker_endpoint': docker.endpoint, 'containers': [],
                              'result': 'PREPARING', 'models': []}

    def persist(self):
        save(self.path, self.state)

    def acquire(self, explicit):
        rows = self.docker.candidates(explicit)
        for row in rows:
            item = {key: row[key] for key in
                    ('id', 'name', 'image', 'image_id', 'created', 'state', 'restart')}
            item['was_running'] = row['state']['Running']
            item['status'] = 'UNCHANGED'
            self.state['containers'].append(item)
            if not item['was_running']:
                continue
            for endpoint in endpoints(row):
                try:
                    models = self.runner.ollama('ps', base_url=endpoint, timeout=2)['models']
                except (OSError, ValueError, KeyError, TypeError) as error:
                    item.setdefault('api_errors', []).append(str(error)[:200])
                    continue
                for model in models:
                    name, context = model.get('name') or model.get('model'), model.get('context_length')
                    expiry = dt.datetime.fromisoformat(model['expires_at'].replace('Z', '+00:00'))
                    if (not name or not isinstance(context, int) or isinstance(context, bool) or
                            context <= 0 or expiry.tzinfo is None):
                        raise RuntimeError('MODEL_RESTORE_METADATA_INCOMPLETE')
                    self.state['models'].append({'container_id': row['id'], 'endpoint': endpoint,
                        'model': name, 'context_length': context, 'expires_at': expiry.isoformat(),
                        'digest': model.get('digest'), 'size_vram': model.get('size_vram'),
                        'status': 'CAPTURED'})
                item['api_endpoint'] = endpoint
                break
        self.persist()
        for item in self.state['containers']:
            if not item['was_running']:
                continue
            current = self.docker.inspect([item['id']])[0]
            if identity(current) != identity(item) or current['state'] != item['state']:
                raise RuntimeError('CONTAINER_CHANGED_BEFORE_STOP')
            item['status'] = 'STOP_REQUESTED'
            self.persist()
            print('STOPPING_OLLAMA_CONTAINER=' + item['name'] + ' ID=' + item['id'], flush=True)
            self.docker.call('container', 'stop', '--time', '10', item['id'], timeout=15)
            current = self.docker.inspect([item['id']])[0]
            if not stopped(current):
                raise RuntimeError('OLLAMA_CONTAINER_DID_NOT_STOP')
            item['stopped_state'] = current['state']
            item['status'] = 'STOPPED'
            self.persist()
        self.check()
        self.state['result'] = 'HELD_STOPPED'
        self.persist()

    def check(self):
        items = self.state['containers']
        rows = {row['id']: row for row in self.docker.inspect([i['id'] for i in items], timeout=2)}
        with (self.path.parent / 'ollama-container-during.jsonl').open('a') as stream:
            stream.write(json.dumps({'monotonic_seconds': time.monotonic(),
                'containers': [{'id': row['id'], 'state': row['state']} for row in rows.values()]}) + '\n')
        for item in items:
            row = rows[item['id']]
            if identity(row) != identity(item) or not stopped(row):
                raise RuntimeError('OLLAMA_CONTAINER_RESTARTED_OR_CHANGED:' + item['id'])
        return rows

    def restore(self, observer, unsafe):
        if not any(i['status'] != 'UNCHANGED' for i in self.state['containers']):
            self.state['result'] = 'NO_CONTAINERS_CHANGED'
            self.persist()
            return True
        if unsafe:
            self.state['result'] = 'RESTORE_DEFERRED_GPU_FAILURE_OR_LIVE_PROCESS'
            self.persist()
            return False
        complete = True
        for item in self.state['containers']:
            if not item['was_running'] or item['status'] == 'UNCHANGED':
                continue
            try:
                current = self.docker.inspect([item['id']])[0]
                if identity(current) != identity(item):
                    raise RuntimeError('CONTAINER_IDENTITY_CHANGED_NOT_RESTORED')
                expected = item.get('restored_state') or item.get('stopped_state')
                if expected and current['state'] != expected:
                    if current['state']['Running']:
                        item['status'] = 'RUNNING_EXTERNALLY'
                        continue
                    raise RuntimeError('CONTAINER_STATE_CHANGED_EXTERNALLY')
                if not current['state']['Running']:
                    item['status'] = 'START_REQUESTED'
                    self.persist()
                    self.docker.call('container', 'start', item['id'], timeout=20)
                current = self.docker.inspect([item['id']])[0]
                if not current['state']['Running'] or current['state']['Restarting']:
                    raise RuntimeError('OLLAMA_CONTAINER_START_NOT_CONFIRMED')
                item['status'] = 'RESTORED'
                item['restored_state'] = current['state']
                leases = [m for m in self.state['models'] if m['container_id'] == item['id']]
                if leases:
                    until = time.monotonic() + 20
                    endpoint = None
                    while endpoint is None and time.monotonic() < until:
                        for candidate in endpoints(current):
                            try:
                                self.runner.ollama('ps', base_url=candidate, timeout=1)
                                endpoint = candidate
                                break
                            except (OSError, ValueError):
                                pass
                        if endpoint is None:
                            time.sleep(.5)
                    if endpoint is None:
                        raise RuntimeError('RESTORED_OLLAMA_API_NOT_READY')
                    for model in leases:
                        model['endpoint'] = endpoint
                    record = {'ollama_restore_leases': leases, 'game_launched': False}
                    self.runner.restore_models(self.path.parent, record, observer)
                    if any(m['status'] in ('RESTORE_FAILED', 'DEFERRED_GPU_FAILURE_OR_LIVE_PROCESS')
                           for m in leases):
                        raise RuntimeError('MODEL_RESTORATION_INCOMPLETE')
            except Exception as error:
                item['restore_error'] = str(error)
                complete = False
            self.persist()
        self.state['result'] = 'RESTORED' if complete else 'RESTORE_INCOMPLETE'
        self.persist()
        return complete


def unresolved(state):
    return state.get('result') not in ('RESTORED', 'NO_CONTAINERS_CHANGED')


def recover(path):
    path = path.resolve(strict=True)
    out = path.parent
    runner = load_module('graphics', out / 'graphics.py')
    observer = load_module('observer', out / 'observer.py')
    with (out.parent.parent / 'tests/.universal-ollama-container.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.seek(0)
        if lock.read().strip() != str(path):
            raise RuntimeError('RECOVERY_LEASE_IS_NOT_THE_CURRENT_LEASE')
        state = json.loads(path.read_text())
        if not unresolved(state):
            print('OLLAMA_CONTAINER_RESTORATION=' + state['result'], flush=True)
            return 0
        if observer.existing_games():
            raise RuntimeError('LIVE_EMULATOR_PREVENTS_CONTAINER_RESTORATION')
        check = out / ('restore-check-' + str(time.time_ns()))
        check.mkdir()
        began = time.monotonic() - 2
        os.environ.setdefault('SHADPS4_KERNEL_JOURNAL_SUDO', '1')
        runner.collect_display_health(check, os.environ.get('DISPLAY') or ':0',
                                      query_display=False, since_epoch=time.time() - 2)
        journals = [json.loads(p.read_text()) for p in check.glob('kernel-journal*.json')]
        readable = any(j.get('exit_code') == 0 and not j.get('stdout_truncated') and
                       'not seeing messages' not in j.get('stderr', '').lower() for j in journals)
        if (not readable or runner.blocked_display_tasks() or
                runner.kernel_gpu_failures(check, began, time.monotonic())):
            raise RuntimeError('GPU_RECOVERY_NOT_CONFIRMED; container restoration remains deferred')
        docker = Docker()
        if docker.endpoint != state['docker_endpoint']:
            raise RuntimeError('DOCKER_ENDPOINT_CHANGED')
        lease = ContainerLease(docker, runner, path, state)
        result = lease.restore(observer, False)
        print('OLLAMA_CONTAINER_RESTORATION=' + lease.state['result'], flush=True)
        return 0 if result else 1


def run_game(out, game, pin, runner_pin, lease, interrupted, first_game=False):
    env = os.environ.copy()
    env.update(SHADPS4_OLLAMA_CONTAINER_LEASE=str(lease.path),
               SHADPS4_CAPTURE_SHADER_DIAGNOSTICS='1' if (
                   env.get('SHADPS4_TEST_GPU_TRACE') == '1' or
                   first_game and env.get('SHADPS4_TEST_CAPTURE_FIRST_SHADERS') == '1') else '0',
               SHADPS4_TEST_SOURCE_COMMIT=pin, SHADPS4_TEST_RUNNER_COMMIT=runner_pin,
               SHADPS4_TEST_GAME='/mnt/roms-all/ps4-games/' + game + '/eboot.bin')
    child = subprocess.Popen([sys.executable, str(out / 'graphics.py')], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, start_new_session=True)
    def copy_output():
        with (out / (game + '-runner.log')).open('w') as stream:
            for line in child.stdout:
                stream.write(line)
                stream.flush()
                print(line, end='', flush=True)
    reader = threading.Thread(target=copy_output, daemon=True)
    reader.start()
    problem = None
    try:
        while child.poll() is None:
            if interrupted.is_set():
                raise RuntimeError('INTERRUPTED_BY_USER')
            lease.check()
            interrupted.wait(1)
    except Exception as error:
        problem = str(error)
        child.terminate()
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            problem += '; RUNNER_STOP_UNCONFIRMED'
    reader.join(timeout=2)
    return child.returncode, problem


def main():
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == '--restore':
        return recover(Path(sys.argv[2]))
    out, pin, runner_pin, *games = sys.argv[1:]
    out = Path(out).resolve(strict=True)
    if (not all(re.fullmatch(r'[a-f0-9]{40}', x) for x in (pin, runner_pin)) or
            not games or any(not re.fullmatch(r'CUSA\d{5}', x) for x in games)):
        raise RuntimeError('INVALID_BATCH_ARGUMENTS')
    runner = load_module('graphics', out / 'graphics.py')
    observer = load_module('observer', out / 'observer.py')
    project = out.parent.parent
    lock = (project / 'tests/.universal-ollama-container.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    lock.seek(0)
    previous = lock.read().strip()
    if previous and unresolved(json.loads(Path(previous).read_text())):
        lock.close()
        raise RuntimeError('PREVIOUS_CONTAINER_LEASE_NEEDS_RECOVERY:' + previous)
    docker = Docker()
    lease = ContainerLease(docker, runner, out / 'ollama-containers.json')
    lock.seek(0)
    lock.truncate()
    lock.write(str(lease.path))
    lock.flush()
    os.fsync(lock.fileno())
    lease.persist()
    interrupted = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: interrupted.set())
    records, rc, uncertain = [], 0, False
    try:
        if observer.existing_games():
            raise RuntimeError('AN_EMULATOR_IS_ALREADY_RUNNING')
        explicit = os.environ.get('SHADPS4_OLLAMA_CONTAINER_IDS', '').split()
        if any(not re.fullmatch(r'[a-f0-9]{64}', value) for value in explicit):
            raise RuntimeError('INVALID_EXPLICIT_CONTAINER_ID')
        lease.acquire(explicit)
        for game in games:
            if interrupted.is_set():
                rc = 130
                break
            lease.check()
            print('TESTING=' + game + ' MAX_SECONDS=120 AUTOMATIC_INPUT=NO', flush=True)
            uncertain = True
            result, problem = run_game(out, game, pin, runner_pin, lease, interrupted,
                                       first_game=game == games[0])
            log = (out / (game + '-runner.log')).read_text()
            archives = [Path(line.removeprefix('EVIDENCE=')) for line in log.splitlines()
                        if line.startswith('EVIDENCE=')]
            if not archives or archives[-1].parent.resolve() != out.parent:
                raise RuntimeError('NATIVE_RESULT_MISSING')
            record = json.loads(Path(str(archives[-1]).removesuffix('.tar.gz')).joinpath('result.json').read_text())
            records.append(record)
            uncertain = False
            rc = rc or result or (1 if problem else 0)
            if problem:
                lease.state['error'] = problem
            if problem or record.get('stop_remaining_tests'):
                break
    except Exception as error:
        lease.state['error'] = str(error)
        print('CONTAINER_BATCH_STOP=' + str(error), flush=True)
        rc = 1
    finally:
        unsafe = uncertain or any(r.get('kernel_gpu_timeouts') or r.get('kernel_gpu_failures')
            or r.get('preflight_kernel_gpu_failures')
            or r.get('native', {}).get('gpu_failure') or r.get('native', {}).get('gpu_memory_headroom_failure')
            or r.get('game_launched') and not r.get('native', {}).get('owned_child_terminated') for r in records)
        try:
            unsafe = unsafe or bool(observer.existing_games())
        except Exception:
            unsafe = True
        if not lease.restore(observer, unsafe):
            rc = 1
        lease.state['batch_exit_code'] = rc
        lease.persist()
        print('OLLAMA_CONTAINER_RESTORATION=' + lease.state['result'], flush=True)
        print('OLLAMA_CONTAINER_LEASE=' + str(lease.path), flush=True)
        if unresolved(lease.state):
            print('RECOVERY_COMMAND=' + shlex.join([
                'python3', str(Path(__file__).resolve()), '--restore', str(lease.path)]), flush=True)
        lock.close()
    return rc


if __name__ == '__main__':
    sys.exit(main())
