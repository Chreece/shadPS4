#!/usr/bin/env python3
"""Resume the SHA-verified PR96 ELF. No build or emulator-source/config writes."""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess as sp
import sys
import tarfile
import time
import threading
from contextlib import contextmanager
import traceback
import urllib.parse
import urllib.request

EXPECTED = '21a26822390451d4b21bde8feae4712b2bd66bf068096ad246055db914122fde'
PREVIOUS = 'got-c925-indexed-revalidation-20261028T133609110195Z'
MIN_VRAM = 8 * 1024**3
MAX_RESPONSE = 2 * 1024**2


def emit(s):
    print(time.strftime('%H:%M:%S') + ' ' + s, flush=True)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(4 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def stop_owned(p):
    if p is None or p.poll() is not None:
        return
    try:
        os.killpg(p.pid, signal.SIGTERM)
        try:
            p.wait(timeout=2)
        except sp.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=5)
    except ProcessLookupError:
        pass


def command(args, data=None, timeout=10):
    """No shell. Always reap only this helper's own process group."""
    p = sp.Popen(args, stdin=sp.PIPE if data is not None else sp.DEVNULL,
                 stdout=sp.PIPE, stderr=sp.PIPE, start_new_session=True)
    try:
        out, err = p.communicate(data, timeout=timeout)
        if p.returncode:
            raise RuntimeError(f'COMMAND_EXIT_{p.returncode}: ' + err.decode(errors='replace')[-400:])
        return out
    finally:
        stop_owned(p)


def free_vram():
    values = []
    for p in Path('/sys/class/drm').glob('card*/device/mem_info_vram_total'):
        try:
            total = int(p.read_text()); used = int(p.with_name('mem_info_vram_used').read_text())
            if total >= MIN_VRAM and 0 <= used <= total:
                values.append((total, total - used))
        except (OSError, ValueError):
            pass
    return max(values)[1] if values else -1


def emulator_pids():
    found = []
    for p in Path('/proc').glob('[0-9]*'):
        try:
            if (p/'comm').read_text().strip().lower() == 'shadps4':
                found.append(int(p.name))
        except OSError:
            pass
    return found


@contextmanager
def heartbeat(label):
    done = threading.Event()
    def report():
        elapsed = 0
        while not done.wait(15):
            elapsed += 15
            emit(f'{label}_ACTIVE elapsed={elapsed}s')
    worker = threading.Thread(target=report, daemon=True)
    worker.start()
    try:
        yield
    finally:
        done.set()
        worker.join(timeout=1)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('LOCAL_OLLAMA_REDIRECT_REFUSED')


class Ollama:
    """Discover local read-only API access first; never start/stop a container."""
    def __init__(self, evid):
        self.evid = evid
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.selected = None
        self.probes = []

    def record(self, endpoint, result):
        self.probes.append({'endpoint': endpoint, 'result': result})
        save(self.evid/'ollama-access.json', self.probes)

    def request_at(self, endpoint, path, body=None, timeout=5):
        if path not in ('/api/ps', '/api/generate'):
            raise ValueError('UNAPPROVED_OLLAMA_ENDPOINT')
        raw = None if body is None else json.dumps(body).encode()
        cid, origin = endpoint
        if cid is None:
            req = urllib.request.Request(origin + path, data=raw,
                                          headers={'Content-Type': 'application/json'})
            with self.opener.open(req, timeout=timeout) as response:
                payload = response.read(MAX_RESPONSE + 1)
        else:
            args = ['docker', 'exec', '-i', cid, 'curl', '--silent', '--show-error',
                    '--fail', '--noproxy', '*', '--connect-timeout', '3',
                    '--max-time', str(timeout), '--header', 'Content-Type: application/json']
            if raw is not None:
                args += ['--data-binary', '@-']
            payload = command(args + [origin + path], raw, timeout + 3)
        if len(payload) > MAX_RESPONSE:
            raise RuntimeError('OLLAMA_RESPONSE_TOO_LARGE')
        obj = json.loads(payload)
        if not isinstance(obj, dict) or obj.get('error'):
            raise RuntimeError('OLLAMA_INVALID_RESPONSE')
        if path == '/api/ps' and (not isinstance(obj.get('models'), list) or
                                 any(not isinstance(m, dict) for m in obj['models'])):
            raise RuntimeError('OLLAMA_PS_SCHEMA_INVALID')
        return obj

    def endpoints(self):
        # Environment values are accepted only for literal loopback addresses.
        raw = os.environ.get('OLLAMA_HOST', '')
        if raw:
            try:
                u = urllib.parse.urlsplit(raw if '://' in raw else 'http://' + raw)
                host = u.hostname
                local = host == 'localhost' or ipaddress.ip_address(host).is_loopback
                if local and u.scheme == 'http' and not u.username and not u.password:
                    h = '127.0.0.1' if host == 'localhost' else host
                    yield (None, 'http://' + ('['+h+']' if ':' in h else h) + ':' + str(u.port or 11434))
            except (ValueError, TypeError):
                pass
        yield (None, 'http://127.0.0.1:11434')
        yield (None, 'http://[::1]:11434')
        if not shutil.which('docker'):
            return
        try:
            rows = command(['docker', 'ps', '--format', '{{.ID}}\t{{.Names}}\t{{.Image}}']).decode().splitlines()
        except Exception as exc:
            self.record(('docker', 'discovery'), type(exc).__name__ + ': ' + str(exc)[:300])
            return
        for row in rows[:32]:
            parts = row.split('\t')
            if len(parts) != 3 or 'ollama' not in (' '.join(parts[1:])).lower():
                continue
            cid = parts[0]
            if not cid.isalnum():
                continue
            try:
                # Only network metadata: do not retrieve container secrets/env.
                net = json.loads(command(['docker', 'inspect', '--format', '{{json .NetworkSettings}}', cid]))
            except Exception as exc:
                self.record((cid, 'inspect-network'), type(exc).__name__)
                net = {}
            ports = {11434}
            for port, binds in (net.get('Ports') or {}).items():
                if port.endswith('/tcp') and port.split('/')[0].isdigit():
                    internal = int(port.split('/')[0])
                    if 1 <= internal <= 65535:
                        ports.add(internal)
                    for binding in binds or []:
                        host = binding.get('HostIp', '')
                        published = binding.get('HostPort', '')
                        if published.isdigit() and 1 <= int(published) <= 65535:
                            if host in ('', '0.0.0.0', '127.0.0.1'):
                                yield (None, 'http://127.0.0.1:' + published)
                            elif host in ('::', '::1'):
                                yield (None, 'http://[::1]:' + published)
            # Bypass a broken published port using the local container's bridge IP.
            for network in (net.get('Networks') or {}).values():
                for field in ('IPAddress', 'GlobalIPv6Address'):
                    address = network.get(field)
                    try:
                        ip = ipaddress.ip_address(address)
                    except ValueError:
                        continue
                    if not ip.is_private or ip.is_unspecified:
                        continue
                    h = '['+str(ip)+']' if ip.version == 6 else str(ip)
                    for port in sorted(ports)[:4]:
                        yield (None, f'http://{h}:{port}')
            # Last fallback executes only curl inside the already-running Ollama container.
            for port in sorted(ports)[:4]:
                yield (cid, f'http://127.0.0.1:{port}')

    def discover(self):
        seen = set(); empty = None
        for endpoint in self.endpoints():
            if endpoint in seen:
                continue
            seen.add(endpoint)
            if len(seen) > 32:
                break
            emit('OLLAMA_PROBE=' + str(endpoint))
            try:
                data = self.request_at(endpoint, '/api/ps', timeout=3)
                self.record(endpoint, 'OK models=' + str(len(data['models'])))
                if data['models']:
                    self.selected = endpoint
                    return data
                empty = (endpoint, data)
            except Exception as exc:
                self.record(endpoint, type(exc).__name__ + ': ' + str(exc)[:250])
        if empty:
            self.selected = empty[0]
            return empty[1]
        raise RuntimeError('OLLAMA_LOCAL_AND_DOCKER_API_UNAVAILABLE_NO_GAME')

    def request(self, path, body=None, timeout=30):
        if self.selected is None:
            raise RuntimeError('OLLAMA_TRANSPORT_NOT_SELECTED')
        # Keep unloading/restoring tied to the same server, not another discovered instance.
        with heartbeat('OLLAMA_REQUEST'):
            return self.request_at(self.selected, path, body, timeout)


def model_expiry(m):
    t = dt.datetime.fromisoformat(str(m['expires_at']).replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('MODEL_EXPIRY_TIMEZONE_MISSING')
    return t


def ensure_headroom(api, evid, unloaded, result):
    current = free_vram(); result['vram_before_bytes'] = current
    emit(f'VRAM_FREE={current} REQUIRED={MIN_VRAM}')
    if current < 0:
        raise RuntimeError('VRAM_COUNTER_UNAVAILABLE')
    if current < MIN_VRAM:
        data = api.discover()
        save(evid/'ollama-before.json', data)
        # Complete verification before unloading any model.
        for m in data['models']:
            if not (m.get('name') or m.get('model')) or not isinstance(m.get('context_length'), int) or m['context_length'] <= 0:
                raise RuntimeError('OLLAMA_MODEL_CONTEXT_UNKNOWN_NO_UNLOAD')
            model_expiry(m)
        for m in data['models']:
            if free_vram() >= MIN_VRAM:
                break
            unloaded.append(m)
            save(evid/'ollama-restore-pending.json', {'endpoint': api.selected, 'models': unloaded})
            emit('UNLOADING_MODEL=' + (m.get('name') or m['model']))
            api.request('/api/generate', {'model': m.get('name') or m['model'], 'keep_alive': 0, 'stream': False})
            for _ in range(12):
                if free_vram() >= MIN_VRAM:
                    break
                time.sleep(0.5)
    result['vram_at_launch_bytes'] = free_vram()
    emit('VRAM_FINAL=' + str(result['vram_at_launch_bytes']))
    if result['vram_at_launch_bytes'] < MIN_VRAM:
        raise RuntimeError('VRAM_STILL_BELOW_8GIB_NO_GAME')


def restore_models(api, unloaded, evid):
    out = []
    for m in unloaded:
        name = m.get('name') or m['model']; entry = {'model': name, 'context_length': m['context_length']}
        try:
            remaining = (model_expiry(m) - dt.datetime.now(dt.timezone.utc)).total_seconds()
            if remaining <= 0:
                entry['result'] = 'ORIGINAL_EXPIRY_ELAPSED'
            else:
                # Negative keep_alive in Ollama produces an expiry centuries ahead.
                keep_alive = -1 if remaining > 100*365*86400 else math.ceil(remaining)
                emit('RESTORING_MODEL=' + name)
                api.request('/api/generate', {'model': name, 'stream': False,
                            'options': {'num_ctx': m['context_length']}, 'keep_alive': keep_alive}, 90)
                now = api.request('/api/ps')['models']
                ok = any((x.get('name') or x.get('model')) == name and x.get('context_length') == m['context_length'] for x in now)
                entry['result'] = 'RESTORED_VERIFIED' if ok else 'RESTORE_NOT_VERIFIED'
        except Exception as exc:
            entry.update(result='RESTORE_FAILED', error=str(exc))
        out.append(entry); save(evid/'ollama-restore.json', out)
    return out


def run_game(binary, eboot, work, evid, screen):
    env = dict(os.environ, DISPLAY=screen)
    process = None; captures = []; frames = []; start = time.monotonic(); last = start; timed = False
    marks = iter((40,55,85,112)); mark = next(marks, None)
    emit('STAGE=MENU_TEST MAX_SECONDS=118+2 INPUT=NONE')
    try:
        with (evid/'game.log').open('wb') as log, (evid/'capture.log').open('ab') as caplog:
            process = sp.Popen([str(binary),'--game',str(eboot),'--fullscreen','true','--show-fps'],
                               cwd=work,env=env,stdout=log,stderr=sp.STDOUT,start_new_session=True)
            while process.poll() is None:
                now=time.monotonic(); elapsed=now-start
                if elapsed>=118:
                    timed=True; stop_owned(process); break
                if mark is not None and elapsed>=mark:
                    target=evid/f'menu-{mark}.png'
                    args=['import','-window','root',str(target)] if shutil.which('import') else ['maim',str(target)]
                    cap=sp.Popen(args,env=env,stdout=caplog,stderr=sp.STDOUT,start_new_session=True)
                    captures.append((cap,now,{'requested_seconds':mark,'started_seconds':elapsed,'file':target.name}))
                    mark=next(marks,None)
                for cap, then, item in captures[:]:
                    if cap.poll() is not None or now-then>6:
                        stop_owned(cap); item.update(exit_code=cap.returncode,finished_seconds=time.monotonic()-start,
                                                    game_alive_at_finish=process.poll() is None)
                        frames.append(item); captures.remove((cap,then,item))
                if now-last>=15:
                    emit(f'MENU_ACTIVE elapsed={elapsed:.0f}s pid={process.pid}'); last=now
                time.sleep(.05)
            return {'exit_code':process.wait(timeout=3),'time_limit_reached':timed,
                    'seconds':round(time.monotonic()-start,3),'graphics':'PENDING_VISUAL_REVIEW'}
    finally:
        stop_owned(process)
        for cap, then, item in captures:
            stop_owned(cap); item.update(exit_code=cap.returncode,game_alive_at_finish=False); frames.append(item)
        save(evid/'capture-times.json',frames)


def main():
    project=Path.home()/'projects/shadps4'; logs=project/'logs'
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    evid=logs/('got-c925-revalidation-resume-'+stamp); evid.mkdir(parents=True)
    previous=logs/PREVIOUS; result={'record_id':evid.name,'build':'REUSE_VERIFIED_PR96_NO_REBUILD',
        'game':{'result':'NOT_RUN'},'graphics_fixed':'UNVERIFIED','source_changes':False,
        'main_promotion':False,'resume_of':PREVIOUS,'expected_binary_sha256':EXPECTED}
    emit('EVIDENCE_DIRECTORY='+str(evid)); emit('BUILD=SKIPPED_ALREADY_PASSED')
    locks=[]; unloaded=[]; protected=None; binary=None; hashes={}; api=Ollama(evid); rc=1
    try:
        for name in ('.got-pr95-scalar-bounds-build.lock','.got-indexed-revalidation.lock'):
            f=(logs/name).open('a+'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB); locks.append(f)
        prior=json.loads((previous/'trial.json').read_text())
        if prior.get('build')!='PASS' or prior.get('candidate_sha256')!=EXPECTED:
            raise RuntimeError('PREVIOUS_NATIVE_BUILD_NOT_VERIFIED')
        candidate=Path(prior['candidate']).resolve()
        if not candidate.is_relative_to((project/'GhostOfTsushima/experiments').resolve()):
            raise RuntimeError('CANDIDATE_PATH_OUTSIDE_PROJECT')
        binary=candidate/'build/shadps4'
        if sha(binary)!=EXPECTED:
            raise RuntimeError('CANDIDATE_CHANGED_NO_GAME')
        manifest=json.loads((previous/'source-manifest.json').read_text())
        for rel, values in manifest.items():
            p=candidate/'source'/rel
            if not p.resolve().is_relative_to((candidate/'source').resolve()) or sha(p)!=values['after']:
                raise RuntimeError('CANDIDATE_SOURCE_CHANGED:'+rel)
        result['source_manifest']=manifest
        profile=Path(prior['isolated_profile']).resolve(); work=profile.parent
        if profile.name!='user' or not profile.is_relative_to(candidate/'runs') or not (profile/'config.json').is_file():
            raise RuntimeError('PRIVATE_PROFILE_MISSING')
        for path in profile.rglob('*'):
            if path.is_symlink():
                raise RuntimeError('PRIVATE_PROFILE_SYMLINK_REFUSED')
        for config in (profile/'config.json',profile/'custom_configs/CUSA11456.json'):
            if config.is_file():
                home=json.loads(config.read_text()).get('General',{}).get('home_dir','')
                if home and not Path(home).expanduser().resolve().is_relative_to(profile):
                    raise RuntimeError('PRIVATE_PROFILE_EXTERNAL_SAVE_ROOT')
        protection=json.loads((previous/'profile-protection.json').read_text())
        # Record current originals, not an obsolete expected hash from another thread.
        originals=[Path(p) for p in protection.get('protected_sha256',{})]
        protected=project/'GhostOfTsushima/experiments/c925-integrated-gpu-20261027-122701/build/shadps4'
        originals.append(protected)
        hashes={str(p):sha(p) if p.is_file() else None for p in originals}
        save(evid/'original-files-before.json',hashes)
        result['private_profile']=str(profile)
        if emulator_pids():
            raise RuntimeError('OTHER_EMULATOR_RUNNING_NO_GAME')
        screen=os.environ.get('DISPLAY') or ':0'
        if not shutil.which('xdpyinfo') or not (shutil.which('import') or shutil.which('maim')):
            raise RuntimeError('DISPLAY_CAPTURE_TOOL_MISSING')
        (evid/'display.log').write_bytes(command(['env','DISPLAY='+screen,'xdpyinfo'],timeout=8))
        eboot=Path('/mnt/roms-all/ps4-games/CUSA11456/eboot.bin')
        if not eboot.is_file():
            raise RuntimeError('EBOOT_MISSING')
        ensure_headroom(api,evid,unloaded,result)
        if emulator_pids() or sha(binary)!=EXPECTED:
            raise RuntimeError('PREFLIGHT_CHANGED_NO_GAME')
        result['game']=run_game(binary,eboot,work,evid,screen)
        text=(evid/'game.log').read_text(errors='replace')
        result['sample_limited_events']={s:text.count(s) for s in ('INDEXED_TABLE_REVALIDATE',
            'INDEXED_MATERIAL_BIND_REJECT','Sharp source was not flatenned',
            'GuestIndexedImageDimensions','VK_ERROR_DEVICE_LOST','Device lost during submit')}
        result['status']='BOUNDED_MENU_CAPTURE_FINISHED' if result['game']['time_limit_reached'] else 'EARLY_GAME_EXIT'
        rc=0 if result['game']['time_limit_reached'] or result['game']['exit_code']==0 else 1
    except (Exception,KeyboardInterrupt) as exc:
        result['status']=str(exc) or type(exc).__name__; emit('STOP='+result['status'])
        (evid/'failure.txt').write_text(traceback.format_exc()); rc=130 if isinstance(exc,KeyboardInterrupt) else 1
    finally:
        if unloaded:
            result['ollama_restore']=restore_models(api,unloaded,evid)
            if any(x['result'] in ('RESTORE_FAILED','RESTORE_NOT_VERIFIED') for x in result['ollama_restore']):rc=1
        if hashes:
            changed=[p for p,d in hashes.items() if (sha(p) if Path(p).is_file() else None)!=d]
            result['original_files_changed']=changed
            if changed:rc=1
        if binary and binary.is_file():
            result['candidate_after_sha256']=sha(binary)
            if result['candidate_after_sha256']!=EXPECTED:rc=1
        result['runner_exit_code']=rc; save(evid/'trial.json',result)
        archive=Path(str(evid)+'.tar.gz')
        with tarfile.open(archive,'w:gz') as tf:tf.add(evid,arcname=evid.name)
        emit('RESULT='+result.get('status','UNKNOWN'));emit('EVIDENCE='+str(archive))
        for f in locks:f.close()
    return rc


if __name__=='__main__':
    def terminated(signum,frame):raise KeyboardInterrupt('TERMINATED_BY_USER')
    signal.signal(signal.SIGTERM,terminated)
    sys.exit(main())
