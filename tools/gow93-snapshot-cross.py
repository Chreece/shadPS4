#!/usr/bin/env python3
"""GoW93: one mapped Cross press before and after each scheduled window snapshot.
No screen recognition, per-menu limits, compilation or emulation changes.
Uses the existing launched-child/DynamoRIO guard and five-minute capture.
Automatic confirmations may select defaults, accept dialogs, or create autosaves.
"""
from __future__ import annotations
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path.home() / 'projects/shadps4/GodOfWarRagnarok'
GUARD_SHA = '4f7a209be51b62f3d336e7b8fdddcc1b5b230320cc381a1eb0343c2897b04007'
GUARD_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
    '1d1667a699480274a2eaa605d379ed8bc3801e4d/tools/gow93-auto-process-guard.py')
SETTLE_SECONDS = 0.35
POLICY = 'cross_before_and_after_every_scheduled_snapshot'


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_guard():
    scripts = ROOT / 'scripts'; scripts.mkdir(parents=True, exist_ok=True)
    candidates = [scripts/'gow93-auto-fixed.py', scripts/'gow93-snapshot-guard.py']
    source = next((p for p in candidates if p.is_file() and not p.is_symlink()
                   and file_sha(p) == GUARD_SHA), None)
    if source is None:
        fd, temporary = tempfile.mkstemp(prefix='.gow93-snapshot-', dir=scripts)
        os.close(fd)
        try:
            subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '20',
                '--max-time', '120', GUARD_URL, '-o', temporary], check=True, timeout=380)
            if file_sha(temporary) != GUARD_SHA:
                raise RuntimeError('Pinned process-guard checksum mismatch')
            source = candidates[1]
            if source.is_symlink():
                raise RuntimeError('Refusing a symlinked process helper')
            os.replace(temporary, source)
        finally:
            Path(temporary).unlink(missing_ok=True)
    module = types.ModuleType('gow93_snapshot_process_guard'); module.__file__ = str(source)
    exec(compile(source.read_bytes(), str(source), 'exec'), module.__dict__)
    return module


class SnapshotCross:
    """Input is timed around snapshots, not gated on image content or menu labels."""
    def __init__(self, auto, core, guard, binary, sleep=time.sleep, clock=time.monotonic):
        self.auto, self.core, self.guard, self.binary = auto, core, guard, binary
        self.picture = core.photograph
        self.sleep, self.clock = sleep, clock
        self.events = []; self.snapshots = 0; self.attempts = 0; self.completed = 0

    def summary(self):
        return {'policy': POLICY, 'button': 'Cross / X', 'screen_recognition': False,
                'per_menu_limits': None, 'snapshots_processed': self.snapshots,
                'press_attempts': self.attempts, 'completed_press_release_pairs': self.completed,
                'key_hold_seconds': 0.22, 'settle_before_snapshot_seconds': SETTLE_SECONDS,
                'input_maps_modified': False, 'emulator_recompiled': False, 'events': self.events}

    def record(self, folder):
        self.core.save_json(Path(folder).parent/'snapshot-cross-input.json', self.summary())

    def tap(self, position, folder, pid, env, elapsed, sequence):
        event = {'position': position, 'snapshot': sequence, 'seconds': round(elapsed, 3)}
        self.events.append(event)
        try:
            # Recheck the mapped application, window owner and focus for EACH press.
            # No valid game window means no key is sent to another application.
            window = self.guard.window(pid, self.binary, env)
            key, mapping = self.auto.read_mapping(self.core.PROFILE)
            event.update(window=window, mapping=mapping)
            self.guard.focus(window, pid, env)
            self.attempts += 1
            self.guard.press(window, pid, key, env)  # existing key-up-in-finally implementation
            self.completed += 1
            event['status'] = 'PRESSED_AND_RELEASED'
            print(f'GOW93_CROSS={position} snapshot={sequence} key={key}', flush=True)
            return True
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            event.update(status='NOT_COMPLETED', error=str(exc))
            print(f'GOW93_CROSS={position} snapshot={sequence} skipped: {exc}', flush=True)
            return False
        finally:
            self.record(folder)

    def photograph(self, folder, pid, env, elapsed):
        self.snapshots += 1; sequence = self.snapshots; start = self.clock()
        if self.tap('before', folder, pid, env, elapsed, sequence):
            self.sleep(SETTLE_SECONDS)
        try:
            shot = self.picture(folder, pid, env, elapsed+self.clock()-start)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            shot = {'seconds': round(elapsed+self.clock()-start, 2),
                    'status': 'UNAVAILABLE', 'reason': str(exc)}
        self.events.append({'position': 'snapshot', 'snapshot': sequence, 'image': shot})
        self.tap('after', folder, pid, env, elapsed+self.clock()-start, sequence)
        # No recursive extra screenshots, two-attempt limit, or stop-on-screen-change.
        self.record(folder)
        return shot


class SnapshotEvidence(dict):
    """Keep mode evidence accurate after the manual launcher's result enrichment."""
    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self['automatic_controller_input'] = True
        self['automatic_input_policy'] = POLICY
        self['screen_recognition_required'] = False


def install(guard_module, auto, manual, base, seconds):
    # Reuse ONLY the manual renderer/session configuration plus ProcessGuard.
    # Do not install the previous notice/menu recognition controller at all.
    previous_print = getattr(manual, 'print', print)
    def message(*args, **kwargs):
        if args and str(args[0]).startswith('Use your controller:'):
            args = ('Automatic Cross: before -> snapshot -> after; no screen recognition.',) + args[1:]
        previous_print(*args, **kwargs)
    manual.print = message
    manual.configure(base, seconds)
    build = base.build_candidate
    def existing(*args, **kwargs):
        binary, proof = build(*args, **kwargs)
        return binary, dict(proof, automatic_input=True, automatic_input_policy=POLICY,
                            screen_recognition_required=False)
    base.build_candidate = existing
    factory = base.core_module
    def core(recover=True):
        c = factory(recover)
        guard = guard_module.ProcessGuard(auto, c, manual.BINARY, manual.BINARY_SHA)
        controller = SnapshotCross(auto, c, guard, manual.BINARY)
        c.photograph = controller.photograph
        sp = c.subprocess
        def spawn(args, **kwargs):
            is_game = bool(args) and str(args[0]) == str(manual.BINARY)
            if is_game:
                try:
                    guard.prepare(args, kwargs)
                except (OSError, RuntimeError, ValueError) as exc:
                    guard.blocked = str(exc)
            child = sp.Popen(args, **kwargs)
            if is_game:
                try:
                    guard.bind(child)
                except (OSError, RuntimeError, ValueError) as exc:
                    guard.blocked = str(exc)
                guard.record()
            return child
        c.subprocess = types.SimpleNamespace(**dict(vars(sp), Popen=spawn))
        save = c.save_json
        def evidence(path, value):
            if path.name == 'result.json' and path.parent.parent == base.LOGS:
                value = SnapshotEvidence(value)
                value.update(automatic_snapshot_input=controller.summary(),
                             automatic_input_process_guard=guard.summary())
                print('GOW93_CROSS_COMPLETED='+str(controller.completed), flush=True)
            save(path, value)
        c.save_json = evidence
        return c
    base.core_module = core


def self_test():
    """Test real scheduling code with fake input/window methods; sends no keys."""
    calls = []; saved = []
    core = types.SimpleNamespace(PROFILE=Path('/unused'),
        photograph=lambda *a: (calls.append('snapshot') or {'status': 'CAPTURED', 'filename': 'frame.jpg'}),
        save_json=lambda *a: saved.append(a))
    auto = types.SimpleNamespace(read_mapping=lambda p: ('n', {'button': 'Cross / X'}))
    guard = types.SimpleNamespace(window=lambda *a: '42', focus=lambda *a: None,
        press=lambda *a: calls.append('cross'))
    controller = SnapshotCross(auto, core, guard, '/unused/shadps4', sleep=lambda t: None, clock=lambda: 0)
    for i in range(4):
        controller.photograph(Path('/unused/screenshots'), 1, {}, i*15)
    if calls != ['cross', 'snapshot', 'cross']*4 or controller.completed != 8:
        raise RuntimeError('Before/snapshot/after ordering or multi-screen continuation failed')
    calls.clear()
    def no_window(*args):
        raise RuntimeError('No game window')
    guard.window = no_window
    controller.photograph(Path('/unused/screenshots'), 1, {}, 70)
    if calls != ['snapshot'] or controller.completed != 8:
        raise RuntimeError('Wrong-window rejection must preserve screenshot capture')
    value = SnapshotEvidence(); value.update(automatic_controller_input=False)
    if value['automatic_controller_input'] is not True:
        raise RuntimeError('Old launcher evidence incorrectly disabled automatic input')
    print('GOW93_SNAPSHOT_SELF_TEST=PASS before/capture/after, unlimited-per-menu continuation, '
          'wrong-window rejection and result evidence; no key sent', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if not 120 <= args.seconds <= 900:
        parser.error('--seconds must be between 120 and 900')
    if args.self_test:
        self_test(); return 0
    guards = load_guard(); auto = guards.load_auto()
    manual = auto.load_base(); base = manual.load_helpers()
    install(guards, auto, manual, base, args.seconds)
    old = sys.argv[:]
    try:
        sys.argv = ['gow93-snapshot-cross'] + (['--restore'] if args.restore else [])
        return base.main()
    finally:
        sys.argv = old


if __name__ == '__main__':
    raise SystemExit(main())
