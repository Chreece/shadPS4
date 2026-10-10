#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual ES-DE launch and scene selection for the isolated PES comparison."""
import fcntl
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import signal
import subprocess
import sys
import termios
import time
import traceback

import run as r
import profile
import debugger
r.profile = profile


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.' + str(os.getpid()))
    r.write_json(temporary, value)
    temporary.replace(path)


def identity(pid):
    proc = Path('/proc') / str(pid)
    if proc.stat().st_uid != os.getuid():
        raise ProcessLookupError(pid)
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] == 'Z':
        raise ProcessLookupError(pid)
    return {'pid': int(pid), 'start': fields[19]}


def alive(owner):
    try:
        return identity(owner['pid']) == owner
    except (OSError, KeyError, TypeError):
        return False


class Route:
    def __init__(self, wrapper, installed, root, report):
        if wrapper.is_symlink():
            raise RuntimeError('The launcher is a symlink; left untouched')
        self.path, self.root = wrapper, root
        self.original, self.stat = wrapper.read_bytes(), wrapper.stat()
        self.backup = report / 'launcher-original'
        self.backup.write_bytes(self.original)
        expected = 'exec ' + str(installed) + ' --game "$game" --fullscreen true'
        text = self.original.decode()
        if text.splitlines().count(expected) != 1:
            raise RuntimeError('Launcher routing anchor changed; left untouched')
        launch = ' '.join(shlex.quote(str(v)) for v in
                          [sys.executable, root/'manual.py', '--launch', root])
        hook = ('# PES_SCALAR_MANUAL_CAPTURE\n'
                'if [[ "$game" == CUSA18676 && -f ' + shlex.quote(str(root/'manual.py')) + ' ]]; then\n'
                '    if ' + launch + '; then\n'
                '        exit 0\n'
                '    else\n'
                '        _pes_ab_status=$?\n'
                '        if [[ "$_pes_ab_status" != 75 ]]; then exit "$_pes_ab_status"; fi\n'
                '    fi\n'
                'fi\n')
        self.patched = text.replace(expected, hook + expected).encode()
        self.installed = False

    def replace(self, data, original_times=False):
        temporary = self.path.with_name(self.path.name + '.pes-ab-' + str(os.getpid()))
        try:
            with temporary.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, self.stat.st_mode & 0o7777)
            if original_times:
                os.utime(temporary, ns=(self.stat.st_atime_ns, self.stat.st_mtime_ns))
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def install(self):
        if self.path.read_bytes() != self.original:
            raise RuntimeError('Launcher changed before routing; left untouched')
        self.replace(self.patched)
        self.installed = True

    def restore(self):
        if not self.installed:
            return
        current = self.path.read_bytes()
        if current == self.patched:
            self.replace(self.original, original_times=True)
        elif current != self.original:
            raise RuntimeError('Launcher was edited externally; original retained at ' + str(self.backup))
        self.installed = False


def cancel_current(root):
    job_path = root / 'manual-job.json'
    if not job_path.exists():
        return
    job = json.loads(job_path.read_text())
    if job.get('state') not in ('starting', 'running'):
        return
    stage = Path(job['stage'])
    (stage/'cancel').touch()
    bridge = job.get('bridge', {})
    deadline = time.monotonic() + 25
    while alive(bridge) and time.monotonic() < deadline:
        time.sleep(.2)
    if alive(bridge):
        os.kill(bridge['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 5
        while alive(bridge) and time.monotonic() < deadline:
            time.sleep(.2)
    child = job.get('child', {})
    if alive(child) and os.getpgid(child['pid']) == child['pid']:
        os.killpg(child['pid'], signal.SIGKILL)
    if alive(bridge):
        os.kill(bridge['pid'], signal.SIGKILL)
    debug = job.get('debugger', {})
    if alive(debug):
        os.kill(debug['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 3
        while alive(debug) and time.monotonic() < deadline:
            time.sleep(.1)
        if alive(debug):
            os.kill(debug['pid'], signal.SIGKILL)


def check_waiting_launch(root):
    with (root/'launch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        current = json.loads((root/'manual-job.json').read_text())
        if current['state'] == 'ready':
            found = profile.emulators()
            if found:
                atomic_json(root/'report/observed-emulators.json',
                            [{k:str(v) if isinstance(v,Path) else v for k,v in p.items()}
                             for p in found])
                raise RuntimeError('An emulator started outside the armed PES route; evidence saved, process left alone')


def wait_for_session(root, stage, input_stream):
    prompted = False
    last_notice = 0
    while True:
        job = json.loads((root/'manual-job.json').read_text())
        if job['state'] == 'finished':
            return json.loads((stage/'run.json').read_text())
        if job['state'] in ('starting', 'running') and not alive(job.get('bridge')):
            raise RuntimeError('Capture launcher disappeared; partial evidence retained')
        if job['state'] == 'running' and not prompted:
            if job.get('auto_start'):
                r.say('PES startup diagnostic is recording automatically; no ENTER needed.')
            else:
                if input_stream.isatty():
                    termios.tcflush(input_stream.fileno(), termios.TCIFLUSH)
                r.say('PES is running. Reach the same match/replay scene, then press ENTER here to measure 90 seconds.')
            prompted = True
        if prompted and not (stage/'measure.json').exists():
            if select.select([input_stream], [], [], .25)[0]:
                if input_stream.readline() == '':
                    raise RuntimeError('SSH input closed; stopping and preserving evidence')
                atomic_json(stage/'measure.json', {'started_monotonic_ns': time.monotonic_ns()})
                r.say('Measuring now. Play the same scene for 90 seconds; screenshots and exit follow automatically.')
        else:
            time.sleep(.25)
        if time.monotonic() - last_notice >= 30:
            last_notice = time.monotonic()
            if job['state'] == 'ready':
                r.say('Waiting for your PES launch in ES-DE: ' + job['label'])
            elif (stage/'measure.json').exists():
                start = json.loads((stage/'measure.json').read_text())['started_monotonic_ns']/1e9
                label = 'Capture elapsed from launch: ' if job.get('auto_start') else 'Capture elapsed after your marker: '
                r.say(label + str(int(time.monotonic()-start)) + ' seconds')
        if job['state'] == 'ready':
            check_waiting_launch(root)


def comparison(records):
    result = {'valid': False, 'scope': 'manually selected scene, one run per mode',
              'reason': 'Scene equivalence requires screenshot/log review; no repeatability claim',
              'timing_complete': len(records) == 2 and all(v.get('speed_valid') for v in records)}
    if any(v.get('debugger_enabled') for v in records):
        result['reason'] = 'GDB diagnostic capture; timings are not benchmark results'
        result['scope'] = 'single scalar-' + ('OFF' if records[0]['mode'] == 'native' else 'ON') + ' run under GDB'
    for item in records:
        result[item['mode']] = {'metrics': item.get('metrics'), 'returncode': item.get('returncode'),
                                'errors': item.get('errors', [])}
    if result['timing_complete']:
        a, b = (next(x['metrics']['fps_over_full_window'] for x in records if x['mode'] == mode)
                for mode in ('native', 'fixed'))
        result['observed_fps_change_percent'] = 100*(b/a-1) if a else None
    return result


def sessions(binary, root, report, seed, active, game, wrapper, installed, summary,
             input_stream=None, duration=90, screenshots=(3,9), finish_after=15,
             debug_crash=False, debug_before=False):
    debug_crash = debug_crash or debug_before
    input_stream = input_stream or sys.stdin
    for name in ('manual.py','run.py','profile.py','debugger.py','gdb_capture.py'):
        shutil.copy2(r.HERE/name, root/name)
    route = Route(wrapper, installed, root, report)
    modes = (('fixed','DIAGNOSTIC: scalar fix ON with GDB'),) if debug_crash else (
        ('native','BEFORE: scalar fix OFF'), ('fixed','AFTER: scalar fix ON'))
    if debug_before:
        modes = (('native','DIAGNOSTIC: scalar fix OFF, startup write to 0x20'),)
    if debug_crash:
        screenshots = tuple(offset-duration for offset in (2,20,40))
    try:
        for number, (mode, label) in enumerate(modes, 1):
            r.require_idle()
            if active.exists():
                shutil.rmtree(active)
            r.say('Preparing identical starting profile for ' + label)
            shutil.copytree(seed, active)
            stage = report / f'{number:02d}-{mode}'
            stage.mkdir()
            record = {'mode': mode, 'label': label, 'manual_launch': True,
                      'debugger_enabled': debug_crash,
                      'profile_before': r.snapshot([active/'user/config.json',active/'user/users.json',
                                                     active/'user/custom_configs',active/'user/home'])}
            r.write_json(stage/'run.json', record)
            job = {'state': 'ready', 'mode': mode, 'label': label, 'owner': identity(os.getpid()),
                   'binary': str(binary), 'stage': str(stage), 'active': str(active),
                   'game': str(game['boot_path']), 'landlock': r.USE_LANDLOCK,
                   'debug_crash': debug_crash,
                   'auto_start': debug_before,
                   'fault_address': 0x20 if debug_before else None,
                   'guest_pc': 0x6c4dbe if debug_before else None,
                   'duration': duration, 'screenshots': list(screenshots), 'finish_after': finish_after}
            atomic_json(root/'manual-job.json', job)
            if not route.installed:
                route.install()
            r.say('\nREADY — ' + label + '. Launch PES normally from ES-DE.')
            result = wait_for_session(root, stage, input_stream)
            summary['records'].append({k:v for k,v in result.items() if k != 'profile_before'})
            r.say(label + ' saved.' + (' Return to ES-DE and wait for the next READY message.'
                                      if number < len(modes) else ' Capture finished.'))
        summary['comparison'] = comparison(summary['records'])
    finally:
        try:
            cancel_current(root)
        finally:
            try:
                route.restore()
                summary['launcher_restored'] = True
            except Exception:
                summary['keep_work'] = True
                summary['launcher_restored'] = False
                raise
            finally:
                state = root/'manual-job.json'
                if state.exists():
                    shutil.copy2(state, report/'capture-state.json')


def capture(root, job):
    stage, active = Path(job['stage']), Path(job['active'])
    record = json.loads((stage/'run.json').read_text())
    r.USE_LANDLOCK = job['landlock']
    r.require_idle()
    env = r.clean_env(active)
    env.update(SHADPS4_SCALAR_AB_DIR=str(stage), SHADPS4_SCALAR_AB_MODE=job['mode'],
               SHADPS4_CPU_ID_MODE='translated')
    started_ns = time.monotonic_ns()
    started = started_ns/1e9
    measure = None
    requested = set()
    next_sample = 0
    child = None
    debug = None
    try:
        with (stage/'console.log').open('w') as log, (stage/'process.jsonl').open('w') as samples:
            def setup():
                if job.get('debug_crash'):
                    debugger.permit_parent()
                r.child_setup(root)
            child = subprocess.Popen([job['binary'],'--game',job['game'],'--fullscreen','true'],
                                     cwd=active,env=env,stdin=subprocess.DEVNULL,stdout=log,
                                     stderr=subprocess.STDOUT,start_new_session=True,
                                     preexec_fn=setup)
            if job.get('debug_crash'):
                debug = debugger.start(child, identity(child.pid), stage,
                                       root/'gdb_capture.py', Path(job['binary']).parent,
                                       fault_address=job.get('fault_address'), guest_pc=job.get('guest_pc'))
                job.update(child=identity(child.pid), debugger=identity(debug.pid))
                atomic_json(root/'manual-job.json', job)
                debugger.wait_ready(debug, child, stage)
            if job.get('auto_start'):
                atomic_json(stage/'measure.json', {'started_monotonic_ns': started_ns, 'automatic': True})
            job.update(state='running',child=identity(child.pid))
            atomic_json(root/'manual-job.json', job)
            while child.poll() is None:
                if debug is not None and debug.poll() is not None:
                    if child.poll() is None:
                        raise RuntimeError('GDB stopped before the emulator; see gdb.log')
                    break
                if (stage/'cancel').exists() or not alive(job['owner']):
                    raise InterruptedError('Capture cancelled or coordinating SSH process ended')
                elapsed = time.monotonic()-started
                if measure is None and (stage/'measure.json').exists():
                    marker = json.loads((stage/'measure.json').read_text())
                    measure = max(0,marker['started_monotonic_ns']/1e9-started)
                    record['measurement_start_s'] = measure
                if measure is not None:
                    end = measure + job['duration']
                    for offset in job['screenshots']:
                        if elapsed >= end+offset and offset not in requested:
                            r.control(stage,'screenshot')
                            requested.add(offset)
                    if elapsed >= end+job['finish_after']:
                        record['reached_capture_end'] = True
                        break
                if elapsed >= next_sample:
                    next_sample = elapsed + 1
                    others = [p for p in profile.emulators() if p['pid'] != child.pid]
                    if others:
                        raise RuntimeError('Another emulator started; stopping only this test')
                    try:
                        samples.write(json.dumps(r.process_sample(child.pid, started))+'\n')
                        samples.flush()
                    except (OSError,ProcessLookupError) as exc:
                        errors=record.setdefault('sampling_errors',[])
                        if len(errors)<10: errors.append(str(exc))
                time.sleep(.25)
    except BaseException as exc:
        record.setdefault('errors',[]).append(type(exc).__name__+': '+str(exc))
        (stage/'error.txt').write_text(traceback.format_exc())
    finally:
        debugger.finish(debug)
        if child is not None:
            record.update(r.stop(child,stage))
        record['elapsed_s'] = time.monotonic()-started
        record['started_monotonic_ns'] = started_ns
        record['requested_measurement_s'] = job['duration']
        for dirname in ('log','screenshots'):
            path=active/'user'/dirname
            if path.exists(): shutil.copytree(path,stage/dirname,dirs_exist_ok=True)
        if (stage/'process.jsonl').exists():
            begin = measure if measure is not None else 0
            end = begin + job['duration'] if measure is not None else max(.001,record['elapsed_s'])
            record['metrics'] = r.metrics(stage,started_ns,begin,end)
            record['metrics']['window_complete'] = bool(
                measure is not None and record.get('reached_capture_end') and
                record['metrics'].get('last_frame_s', 0) >= end-1)
            if not record['metrics']['window_complete'] or job.get('debug_crash'):
                record['metrics']['fps_over_full_window'] = None
        record['screenshots'] = len(list((stage/'screenshots').glob('*.png')))
        record['input_allowed'] = True
        record['cpu_id_mode'] = 'translated'
        console = (stage/'console.log').read_text(errors='replace') if (stage/'console.log').exists() else ''
        record['cpu_translation_active'] = 'CPU identity translation active' in console
        configuration = stage/'scalar-config.json'
        record['scalar_configuration'] = json.loads(configuration.read_text()) if configuration.exists() else None
        record['scalar_mode_verified'] = record['scalar_configuration'] == {
            'fixed': job['mode'] != 'native', 'diagnostic': job['mode'] == 'diagnostic'}
        record['speed_valid'] = bool(measure is not None and record.get('reached_capture_end') and
                                    record.get('metrics',{}).get('frames') and
                                    record['metrics'].get('last_frame_s',0) >= end-1 and
                                    record['screenshots'] == len(job['screenshots']) and
                                    record['cpu_translation_active'] and record['scalar_mode_verified'] and
                                    not record.get('errors'))
        if job.get('debug_crash'):
            record['speed_valid'] = False
            record['debugger_enabled'] = True
            record['debugger_attached'] = (stage/'debug-ready.json').exists()
            evidence = stage/'signal-last.json'
            record['last_signal'] = json.loads(evidence.read_text()) if evidence.exists() else None
        r.write_json(stage/'run.json',record)
    return record


def launch(root):
    job_path = root/'manual-job.json'
    if not job_path.is_file(): return 75
    with (root/'launch.lock').open('a') as lock:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                current = json.loads(job_path.read_text())
                if not alive(current.get('owner')):
                    return 75
                if current['state'] != 'ready':
                    return 0
                if time.monotonic() >= deadline:
                    r.say('PES launch lock stayed busy; no emulator was started. Retry the ES-DE launch.')
                    return 1
                time.sleep(.05)
        job = json.loads(job_path.read_text())
        if not alive(job.get('owner')): return 75
        if job['state'] != 'ready': return 0
        job.update(state='starting',bridge=identity(os.getpid()))
        atomic_json(job_path,job)
        def interrupted(sig, frame):
            for handled in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
                signal.signal(handled,signal.SIG_IGN)
            raise InterruptedError('Launcher received signal '+str(sig))
        for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
            signal.signal(sig,interrupted)
        try:
            capture(root,job)
        except BaseException as exc:
            stage=Path(job['stage'])
            record=json.loads((stage/'run.json').read_text())
            record.setdefault('errors',[]).append(type(exc).__name__+': '+str(exc))
            record['speed_valid']=False
            r.write_json(stage/'run.json',record)
            (stage/'error.txt').write_text(traceback.format_exc())
        finally:
            job['state']='finished'
            atomic_json(job_path,job)
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--launch':
        raise SystemExit('This helper is launched by the temporary PES route')
    raise SystemExit(launch(Path(sys.argv[2]).resolve()))
