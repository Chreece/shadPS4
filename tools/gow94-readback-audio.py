#!/usr/bin/env python3
"""Manual GoW94: existing rendering baseline plus reversible precise readbacks.
No compilation, automated input, output-device rerouting or microphone recording.
Adds per-game readbacks_mode=2 and asynchronous logging, then restores the exact
prior override. Short audio captures are limited to sink-inputs of our child PID.
"""
from __future__ import annotations
import argparse
import array
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import wave

ROOT = Path.home() / 'projects/shadps4/GodOfWarRagnarok'
MANUAL_SHA = '181a6e8d5a9fe2ec8bd47eecd50f8af295a8f6f90a4dbe1dbc0758c6159e6c1e'
MANUAL_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
    'd487271a04cee874e3847cba19da6236f0338dd0/tools/gow93-playable-check.py')
AUDIO_SECONDS = 8
AUDIO_START = 65
MAX_AUDIO_STREAMS = 3


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_manual():
    scripts = ROOT / 'scripts'; scripts.mkdir(parents=True, exist_ok=True)
    names = ['gow93.py', 'gow93-snapshot-cross.py', 'gow94-manual-base.py']
    path = next((scripts/n for n in names if (scripts/n).is_file()
                 and not (scripts/n).is_symlink()
                 and digest((scripts/n).read_bytes()) == MANUAL_SHA), None)
    if path is None:
        fd, name = tempfile.mkstemp(prefix='.gow94-manual-', dir=scripts); os.close(fd)
        temporary = Path(name)
        try:
            subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '20',
                '--max-time', '120', MANUAL_URL, '-o', name], check=True, timeout=380)
            if digest(temporary.read_bytes()) != MANUAL_SHA:
                raise RuntimeError('Manual launcher checksum mismatch')
            path = scripts/names[-1]
            if path.is_symlink():
                raise RuntimeError('Refusing symlinked launcher cache')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    raw = path.read_text()
    recovery = "for number in ('86','87','88','89','90','91','92','93'):"
    if raw.count(recovery) != 1:
        raise RuntimeError('Unknown recovery contract in pinned manual launcher')
    raw = raw.replace(recovery, "for number in ('86','87','88','89','90','91','92','93','94'):")
    raw = raw.replace('gow93', 'gow94').replace('GOW93', 'GOW94')
    manual = types.ModuleType('gow94_manual'); manual.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), manual.__dict__)
    return manual


def candidate_settings(raw, readbacks):
    """Change only GPU readback mode and logging behavior in an already-private copy."""
    cfg = json.loads(raw)
    if not isinstance(cfg, dict):
        raise RuntimeError('Per-game configuration must be an object')
    for group in ('GPU', 'Log'):
        if group in cfg and not isinstance(cfg[group], dict):
            raise RuntimeError('Unexpected configuration section: ' + group)
    cfg.setdefault('GPU', {})['readbacks_mode'] = readbacks
    cfg.setdefault('Log', {})['sync'] = False
    return (json.dumps(cfg, indent=2, sort_keys=True) + '\n').encode()


def install_transaction(core, readbacks):
    original = core.ProfileTransaction
    class Trial(original):
        def prepare(self):
            super().prepare()
            path = self.private/'override.applied'
            raw = candidate_settings(path.read_bytes(), readbacks)
            core.atomic(path, raw)
            self.data['applied_sha'] = digest(raw)
            self.data['readbacks_requested'] = readbacks
            self.data['logging_sync_requested'] = False
            core.save_json(self.journal, self.data)
    core.ProfileTransaction = Trial


def process_start(pid):
    raw = (Path('/proc')/str(pid)/'stat').read_text()
    fields = raw.rsplit(')', 1)[1].split()
    return fields[19]


def selected_streams(inputs, sinks, pid):
    """Never fall back to a microphone, default source, or whole-sink recording."""
    if not isinstance(inputs, list) or not isinstance(sinks, list):
        raise RuntimeError('PulseAudio object listing is not an array')
    by_id = {s.get('index'): s for s in sinks if isinstance(s, dict)}
    selected = []
    for stream in inputs:
        if not isinstance(stream, dict):
            continue
        prop = stream.get('properties', {})
        if not isinstance(prop, dict) or str(prop.get('application.process.id', '')) != str(pid):
            continue
        index, sink_id = stream.get('index'), stream.get('sink')
        if type(index) is not int or type(sink_id) is not int or min(index, sink_id) < 0:
            continue
        sink = by_id.get(sink_id, {})
        monitor = sink.get('monitor_source_name')
        if not isinstance(monitor, str) or not monitor or '\0' in monitor or '\n' in monitor:
            continue
        # Only a sink's monitor_source_name, paired with this sink-input index,
        # can reach the capture command. No freely supplied recording source.
        selected.append({'index': index, 'sink': sink_id, 'monitor': monitor,
            'owner_pid': pid, 'corked': stream.get('corked'), 'mute': stream.get('mute'),
            'sample_specification': stream.get('sample_specification'),
            'channel_map': stream.get('channel_map'),
            'buffer_latency_usec': stream.get('buffer_latency_usec'),
            'sink_latency_usec': stream.get('sink_latency_usec'),
            'sink_name': sink.get('name'), 'sink_state': sink.get('state'),
            'sink_mute': sink.get('mute'), 'sink_sample_specification': sink.get('sample_specification')})
    return sorted(selected, key=lambda x: x['index'])[:MAX_AUDIO_STREAMS]


def capture_args(stream):
    return ['parec', '--raw', '--format=s16le', '--rate=48000', '--channels=2',
            '--device='+stream['monitor'], '--monitor-stream='+str(stream['index']),
            '--client-name=GoW94 game-only audio probe', '--stream-name=GoW94 game-only sample']


def pcm_to_wav(raw_path, wav_path):
    raw = raw_path.read_bytes()
    # Bound the artefact and trim a partial interleaved stereo sample frame.
    raw = raw[:min(len(raw), 48000*4*(AUDIO_SECONDS+3))]
    raw = raw[:len(raw)//4*4]
    if not raw:
        return {'captured': False, 'reason': 'No PCM bytes; silence has not been established'}
    with wave.open(str(wav_path), 'wb') as out:
        out.setnchannels(2); out.setsampwidth(2); out.setframerate(48000); out.writeframes(raw)
    samples = array.array('h'); samples.frombytes(raw)
    if sys.byteorder != 'little':
        samples.byteswap()
    peak = max(abs(x) for x in samples)
    square = sum(x*x for x in samples)
    return {'captured': True, 'file': wav_path.name, 'sha256': digest(wav_path.read_bytes()),
        'seconds': len(raw)/192000, 'peak_s16': peak,
        'rms_s16': (square/len(samples))**.5,
        'clipped_sample_count': sum(x in (-32768, 32767) for x in samples),
        'zero_sample_fraction': sum(x == 0 for x in samples)/len(samples),
        'note': 'Isolated output stream, not microphone; these statistics do not establish correct audio.'}


class GameAudioProbe:
    def __init__(self, child, env, output):
        self.child, self.env, self.output = child, dict(env), Path(output)/'audio'
        self.output.mkdir(parents=True, exist_ok=True)
        self.cancel = threading.Event(); self.thread = None
        self.result = {'status': 'WAITING', 'owner_pid': child.pid, 'microphone_used': False,
            'whole_desktop_audio': False, 'routing_changed': False, 'recordings': []}
        self.start_id = process_start(child.pid)
        self.tools = bool(shutil.which('pactl') and shutil.which('parec'))

    def live(self):
        try:
            return self.child.poll() is None and process_start(self.child.pid) == self.start_id
        except OSError:
            return False

    def listing(self, kind):
        completed = subprocess.run(['pactl', '--format=json', 'list', kind], env=self.env,
            capture_output=True, text=True, timeout=3, check=True)
        if len(completed.stdout) > 2_000_000:
            raise RuntimeError('Unexpectedly large audio listing')
        return json.loads(completed.stdout)

    def streams(self):
        return selected_streams(self.listing('sink-inputs'), self.listing('sinks'), self.child.pid)

    @staticmethod
    def stop_recorders(recorders):
        for record in recorders:
            proc = record['process']
            if proc.poll() is None:
                proc.terminate()  # Only the recorder, never the game/SSH process group.
        for record in recorders:
            proc = record['process']
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=2)
            record['stream'].close(); record['err'].close()

    def run(self):
        recorders = []
        try:
            if not self.tools:
                self.result.update(status='UNAVAILABLE', reason='pactl/parec not installed; no alternate recording used')
                return
            # Routing metadata early, short per-stream samples once the scene has begun.
            if self.cancel.wait(min(15, AUDIO_START)) or not self.live():
                self.result['status'] = 'SESSION_ENDED_BEFORE_AUDIO_PROBE'; return
            self.result['early_routes'] = self.streams()
            if self.cancel.wait(max(0, AUDIO_START-15)) or not self.live():
                self.result['status'] = 'SESSION_ENDED_BEFORE_RECORDING'; return
            streams = self.streams()
            self.result['routes_at_capture'] = streams
            if not streams:
                self.result.update(status='UNAVAILABLE', reason='No audio sink-input belongs to this child PID')
                return
            for stream in streams:
                # Revalidate the same stream immediately before recording it.
                current = {(x['index'], x['sink'], x['monitor']) for x in self.streams()}
                if not self.live() or (stream['index'], stream['sink'], stream['monitor']) not in current:
                    continue
                raw = self.output/f"stream-{stream['index']}.pcm"
                error = self.output/f"stream-{stream['index']}-recorder.log"
                handle = raw.open('wb'); err = error.open('wb')
                try:
                    p = subprocess.Popen(capture_args(stream), env=self.env,
                        stdin=subprocess.DEVNULL, stdout=handle, stderr=err, start_new_session=True)
                except BaseException:
                    handle.close(); err.close(); raise
                recorders.append({'process':p, 'stream':handle, 'err':err,
                                  'raw':raw, 'route':stream, 'owner_changed':False})
            if not recorders:
                self.result.update(status='UNAVAILABLE', reason='Streams ended before recording'); return
            start = time.monotonic()
            while time.monotonic()-start < AUDIO_SECONDS and not self.cancel.wait(.5) and self.live():
                try:
                    current = {(x['index'], x['sink'], x['monitor']) for x in self.streams()}
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                    for r in recorders: r['owner_changed'] = True
                    break
                for r in recorders:
                    s = r['route']
                    if (s['index'], s['sink'], s['monitor']) not in current:
                        r['owner_changed'] = True
                if any(r['owner_changed'] for r in recorders):
                    break
            self.stop_recorders(recorders)
            for r in recorders:
                if r['owner_changed']:
                    entry = {'captured':False, 'reason':'Stream ownership ended/changed; recording discarded'}
                else:
                    entry = pcm_to_wav(r['raw'], r['raw'].with_suffix('.wav'))
                entry.update(sink_input=r['route']['index'], recorder_returncode=r['process'].returncode)
                self.result['recordings'].append(entry)
                r['raw'].unlink(missing_ok=True)
            recorders = []
            self.result['status'] = 'GAME_STREAM_SAMPLES_CAPTURED' if any(
                x['captured'] for x in self.result['recordings']) else 'NO_CONFIRMED_AUDIO_SAMPLE'
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            self.result.update(status='UNAVAILABLE', error=str(exc))
        finally:
            self.stop_recorders(recorders)
            for r in recorders: r['raw'].unlink(missing_ok=True)
            (self.output/'audio-probe.json').write_text(json.dumps(self.result, indent=2)+'\n')

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=False); self.thread.start()

    def finish(self):
        self.cancel.set()
        if self.thread is not None:
            # Each external query is bounded and recording processes are always reaped.
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                raise RuntimeError('Audio evidence worker did not finish; preserving diagnostics')


def confirmation(log, readbacks):
    log = re.sub(r'\x1b\[[0-9;]*m', '', log)
    rb = re.search(r'GPU readbacksMode:\s*(\d+)', log)
    sy = re.search(r'Log sync:\s*(true|false)', log)
    return {'requested_readbacks_mode':readbacks,
        'effective_readbacks_mode':int(rb[1]) if rb else None,
        'readbacks_confirmed':bool(rb and int(rb[1]) == readbacks),
        'async_logging_confirmed':bool(sy and sy[1] == 'false'),
        'audio_backend_changed':False, 'output_device_changed':False,
        'audio_route_lines':[l for l in log.splitlines() if (
            'sdl_audio_out.cpp:' in l or 'openal_audio_out.cpp:' in l or 'sceAudioOutOpen:' in l)],
        'audio_queue_clear_messages':log.count('Clearing backed up audio queue'),
        'note':'Rendering/audio correctness requires visual and listening review; no synthetic input.'}


def install(manual, base, seconds, readbacks):
    manual.configure(base, seconds)
    factory = base.core_module
    def core(recover=True):
        c = factory(recover)
        install_transaction(c, readbacks)
        probes = []
        sp = c.subprocess
        def launch(args, **kwargs):
            child = sp.Popen(args, **kwargs)
            if args and str(args[0]) == str(manual.BINARY):
                try:
                    probe = GameAudioProbe(child, kwargs['env'], Path(kwargs['stdout'].name).parent)
                    probe.start(); probes.append(probe)
                except (OSError, RuntimeError, ValueError) as exc:
                    print('GOW94_AUDIO_PROBE_UNAVAILABLE='+str(exc), flush=True)
            return child
        c.subprocess = types.SimpleNamespace(**dict(vars(sp), Popen=launch))
        previous_terminate = c.terminate
        def terminate(child):
            try:
                for probe in probes: probe.finish()
            finally:
                previous_terminate(child)
        c.terminate = terminate
        save = c.save_json
        def evidence(path, value):
            if path.name == 'result.json' and path.parent.parent == base.LOGS:
                log = path.parent/'console.log'
                settings = confirmation(log.read_text(errors='replace') if log.is_file() else '', readbacks)
                value['readback_audio_trial'] = settings
                value['automatic_controller_input'] = False
                value['audio_probes'] = [p.result for p in probes]
                if value.get('status') == 'DMA_TARGET_CAPTURED' and not settings['readbacks_confirmed']:
                    value['status'] = 'READBACK_SETTING_NOT_CONFIRMED'
            save(path, value)
        c.save_json = evidence
        return c
    base.core_module = core


def self_test(manual, base):
    core = base.core_module(recover=False)
    # Existing real transaction/recovery tests, not a simulated preservation claim.
    core.self_test()
    install_transaction(core, 2)
    with tempfile.TemporaryDirectory(prefix='gow94-profile-test-') as d:
        profile = Path(d)/'profile'; private = Path(d)/'private'
        cfg = profile/'custom_configs/CUSA34384.json'; cfg.parent.mkdir(parents=True)
        before = b'{"Input":{"use_unified_input_config":false},"Audio":{"openal_output_mode":4},"GPU":{"readbacks_mode":0}}\n'
        cfg.write_bytes(before)
        txn = core.ProfileTransaction(profile, private); txn.prepare(); txn.install()
        val = json.loads(cfg.read_text())
        assert val['GPU']['readbacks_mode'] == 2 and val['Log']['sync'] is False
        assert val['Audio'] == {'openal_output_mode':4} and val['Input']['use_unified_input_config'] is False
        assert txn.restore()['override_restored'] and cfg.read_bytes() == before
    fixtures = [{'index':4,'sink':8,'properties':{'application.process.id':'100'},'mute':False},
                {'index':5,'sink':8,'properties':{'application.process.id':'200'}}]
    sinks = [{'index':8,'monitor_source_name':'output.monitor'}]
    selected = selected_streams(fixtures, sinks, 100)
    assert len(selected) == 1 and selected[0]['index'] == 4
    assert '--monitor-stream=4' in capture_args(selected[0])
    assert selected_streams(fixtures,[{'index':8,'name':'microphone'}],100) == []
    assert selected_streams(fixtures,sinks,300) == []
    assert confirmation('GPU readbacksMode: 2\nLog sync: false',2)['readbacks_confirmed']
    assert not confirmation('Starting CPU identity translation.',2)['readbacks_confirmed']
    print('GOW94_SELF_TEST=PASS real transaction/restoration, precise-readback override, no Audio/Input edits, game-only audio selection; no game launched', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--readbacks', type=int, choices=(0,2), default=2,
                        help='2=Precise; 0=unchanged readback mode for comparison')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if not 120 <= args.seconds <= 900:
        parser.error('--seconds must be between 120 and 900')
    manual = load_manual(); base = manual.load_helpers()
    if args.self_test:
        self_test(manual,base); return 0
    install(manual,base,args.seconds,args.readbacks)
    previous = sys.argv[:]
    try:
        sys.argv = ['gow94'] + (['--restore'] if args.restore else [])
        return base.main()
    finally:
        sys.argv = previous


if __name__ == '__main__':
    raise SystemExit(main())
