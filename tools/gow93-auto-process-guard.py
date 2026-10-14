#!/usr/bin/env python3
"""GoW93 automatic Cross confirmation: verify the launched process across CPU translation.

Reuses the pinned notice matcher, input mapping, two-attempt limit and original
Phase93 launcher. No compilation, emulation setting change or new menu automation.
An exec wrapper is accepted only for the same live child/start time/session/owner,
with the exact checksum-pinned application mapped and the expected CPU-ID runtime.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types

ROOT = Path.home() / 'projects/shadps4/GodOfWarRagnarok'
AUTO_SHA = '8ec81d11f916d1cba00ac1cf66a3ad15b0e83c9d7825f66d8ed69c53f118d071'
AUTO_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
            'ebb4aa92725f99f5541610142942963e410832fa/tools/gow93-auto-confirm.py')
SERIAL = 'CUSA34384'


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_auto():
    scripts = ROOT/'scripts'; scripts.mkdir(parents=True, exist_ok=True)
    paths = [scripts/'gow93-auto.py', scripts/'gow93-auto-base.py']
    source = next((p for p in paths if p.is_file() and not p.is_symlink()
                   and file_sha(p) == AUTO_SHA), None)
    if source is None:
        fd, temporary = tempfile.mkstemp(prefix='.gow93-guard-', dir=scripts)
        os.close(fd)
        try:
            subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '20',
                '--max-time', '120', AUTO_URL, '-o', temporary], check=True, timeout=380)
            if file_sha(temporary) != AUTO_SHA:
                raise RuntimeError('Original automatic launcher checksum mismatch')
            source = paths[1]
            if source.is_symlink():
                raise RuntimeError('Refusing a symlinked helper destination')
            os.replace(temporary, source)
        finally:
            Path(temporary).unlink(missing_ok=True)
    module = types.ModuleType('gow93_pinned_auto'); module.__file__ = str(source)
    exec(compile(source.read_bytes(), str(source), 'exec'), module.__dict__)
    return module


def file_identity(path):
    st = Path(path).stat()
    return {'device_major': os.major(st.st_dev), 'device_minor': os.minor(st.st_dev),
            'inode': st.st_ino, 'size': st.st_size,
            'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns}


def same_inode(left, right):
    return all(left.get(k) == right.get(k) for k in ('device_major', 'device_minor', 'inode'))


def parse_stat(text):
    # comm may contain spaces/parentheses. Fields after the closing ')' start at 3.
    closing = text.rfind(')')
    if closing < 0:
        raise RuntimeError('Malformed process status')
    fields = text[closing+1:].split()
    if len(fields) < 20:
        raise RuntimeError('Incomplete process status')
    return {'state': fields[0], 'parent_pid': int(fields[1]),
            'process_group': int(fields[2]), 'session_id': int(fields[3]),
            'start_ticks': int(fields[19])}


def parse_maps(text):
    rows = []
    for line in text.splitlines():
        fields = line.split(None, 5)
        if len(fields) < 5:
            raise RuntimeError('Malformed process mapping')
        major, minor = fields[3].split(':')
        name = fields[5] if len(fields) == 6 else ''
        name = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), name)
        rows.append({'device_major': int(major, 16), 'device_minor': int(minor, 16),
            'inode': int(fields[4]), 'offset': int(fields[2], 16),
            'permissions': fields[1], 'path': name})
    return rows


def assess(launch, current):
    """Pure validation: no input, PID search, file writes or relaxed name-only match."""
    for key in ('pid', 'owner_uid', 'parent_pid', 'process_group', 'session_id', 'start_ticks'):
        if current.get(key) != launch.get(key):
            raise RuntimeError('Input process identity changed: '+key)
    if current.get('state') in ('Z', 'X', 'x'):
        raise RuntimeError('Input target has exited')
    if current['binary_identity'] != launch['binary_identity']:
        raise RuntimeError('Pinned application changed after launch')
    if same_inode(current['exe_identity'], launch['binary_identity']):
        return {'mode': 'direct_application', 'application_mappings': []}
    runtime = next((item for item in launch['runtime_executables']
                    if same_inode(current['exe_identity'], item['identity'])), None)
    if runtime is None:
        raise RuntimeError('Live child executable is neither the pinned application nor its CPU-ID runtime')
    if current['exe_identity'] != runtime['identity']:
        raise RuntimeError('CPU-ID runtime file changed after launch')
    mapped = [item for item in current['mappings']
              if same_inode(item, launch['binary_identity']) and item['permissions'].startswith('r')]
    if not any(item['offset'] == 0 for item in mapped):
        raise RuntimeError('Pinned application is not mapped in the translated child')
    return {'mode': 'cpu_translated_application', 'runtime_executable': runtime['path'],
            'application_mappings': mapped}


class ProcessGuard:
    def __init__(self, auto, core, binary, expected_sha):
        self.auto, self.core = auto, core
        self.binary = Path(binary).resolve(strict=True)
        self.expected_sha = expected_sha
        self.child = None; self.launch = None; self.out = None; self.blocked = None
        self.events = []; self.checked = 0
        # Retain the original key/focus handling; only wrap its process validation.
        self.original_focus = auto.focus_game
        self.original_press = auto.press_cross

    def summary(self):
        return {'policy': 'launched_child_and_mapped_pinned_application',
            'launch': self.launch, 'blocked': self.blocked,
            'verification_count': self.checked, 'events': self.events,
            'changes_to_binary_or_cpu_mode': False}

    def record(self):
        if self.out is not None:
            self.core.save_json(self.out/'automatic-process-identity.json', self.summary())

    def prepare(self, args, kwargs):
        if self.child is not None:
            raise RuntimeError('More than one emulator launch requested')
        values = list(map(str, args))
        required = ['--cpu-id-mode', 'auto', '--game', SERIAL, '--fullscreen', 'true']
        if values != [str(self.binary), *required]:
            raise RuntimeError('Unrecognized automatic-confirmation launch command')
        if kwargs.get('start_new_session') is not True or kwargs.get('shell', False):
            raise RuntimeError('Input requires a dedicated non-shell emulator session')
        self.out = Path(kwargs['stdout'].name).parent
        if file_sha(self.binary) != self.expected_sha:
            raise RuntimeError('Application checksum does not match the preserved Phase91 executable')
        identity = file_identity(self.binary)
        # These are the exact runtime executable locations used by launcher.cpp.
        # Symlinks in the existing runtime packaging are resolved, not rejected.
        root = self.binary.parent/'cpu-id-runtime'
        if not root.is_dir():
            root = self.binary.parent/'../libexec/shadps4/cpu-id-runtime'
        candidates = [root/'bin64/drrun', root/'lib64/release/libdynamorio.so']
        runtimes = [{'path': str(p.resolve(strict=True)), 'identity': file_identity(p)}
                    for p in candidates if p.is_file()]
        self.launch = {'command': values, 'binary_sha256': self.expected_sha,
            'binary_identity': identity, 'runtime_executables': runtimes,
            'owner_uid': os.getuid(), 'parent_pid': os.getpid()}

    def bind(self, child):
        self.child = child
        if self.blocked or self.launch is None:
            return
        stat = parse_stat((Path('/proc')/str(child.pid)/'stat').read_text())
        if (stat['parent_pid'] != os.getpid() or stat['process_group'] != child.pid
                or stat['session_id'] != child.pid):
            raise RuntimeError('Child did not enter its dedicated session')
        self.launch.update(pid=child.pid, start_ticks=stat['start_ticks'],
                           process_group=child.pid, session_id=child.pid)
        self.record()

    def snapshot(self, pid):
        folder = Path('/proc')/str(pid)
        current = parse_stat((folder/'stat').read_text())
        current.update(pid=pid, owner_uid=folder.stat().st_uid,
            exe_path=os.readlink(folder/'exe'), exe_identity=file_identity(folder/'exe'),
            binary_identity=file_identity(self.binary), mappings=parse_maps((folder/'maps').read_text()))
        after = parse_stat((folder/'stat').read_text())
        if after['start_ticks'] != current['start_ticks']:
            raise RuntimeError('Process changed during identity inspection')
        return current

    def check(self, pid, stage):
        proof = {'stage': stage, 'pid': pid}
        try:
            if self.blocked:
                raise RuntimeError(self.blocked)
            if self.child is None or self.launch is None or pid != self.child.pid:
                raise RuntimeError('Input target was not launched by this capture')
            if self.child.poll() is not None:
                raise RuntimeError('Launched emulator has already exited')
            current = self.snapshot(pid)
            proof.update(exe_path=current['exe_path'], start_ticks=current['start_ticks'])
            result = assess(self.launch, current)
            # Recheck the child after procfs reads; never act on a recycled PID.
            if self.child.poll() is not None:
                raise RuntimeError('Emulator exited during input validation')
            proof.update(result, verified=True)
            self.checked += 1
            return result
        except (OSError, RuntimeError, ValueError) as exc:
            self.blocked = str(exc); proof.update(verified=False, error=str(exc))
            raise RuntimeError('Automatic input stopped: '+str(exc)) from exc
        finally:
            self.events.append(proof)
            self.record()

    def window(self, pid, binary, env):
        if Path(binary).resolve(strict=True) != self.binary:
            raise RuntimeError('Unexpected application requested an input window')
        self.check(pid, 'window_lookup')
        raw = self.auto.xcommand(['search', '--onlyvisible', '--pid', pid], env)
        valid = []
        for wid in raw.splitlines():
            if not wid.isdecimal():
                continue
            if self.auto.xcommand(['getwindowpid', wid], env) != str(pid):
                continue
            title = self.auto.xcommand(['getwindowname', wid], env)
            if SERIAL in title or 'God of War Ragnar' in title:
                valid.append(wid)
        if len(valid) != 1:
            raise RuntimeError('Game window is missing or ambiguous; no input sent')
        return valid[0]

    def focus(self, wid, pid, env):
        self.check(pid, 'before_focus')
        self.original_focus(wid, pid, env)
        self.check(pid, 'after_focus')

    def press(self, wid, pid, key, env):
        self.check(pid, 'before_cross')
        self.original_press(wid, pid, key, env)


def install(auto, base, manual, seconds):
    auto.install(base, manual, seconds)
    factory = base.core_module
    original_focus, original_press = auto.focus_game, auto.press_cross
    def core(recover=True):
        c = factory(recover)
        # Multiple recovery/core factories must not chain prior session guards.
        auto.focus_game, auto.press_cross = original_focus, original_press
        guard = ProcessGuard(auto, c, manual.BINARY, manual.BINARY_SHA)
        auto.game_window, auto.focus_game, auto.press_cross = guard.window, guard.focus, guard.press
        sp = c.subprocess
        def spawn(args, **kwargs):
            is_game = bool(args) and str(args[0]) == str(manual.BINARY)
            if is_game:
                try:
                    guard.prepare(args, kwargs)
                except (OSError, RuntimeError, ValueError) as exc:
                    guard.blocked = str(exc)
            process = sp.Popen(args, **kwargs)
            if is_game:
                try:
                    guard.bind(process)
                except (OSError, RuntimeError, ValueError) as exc:
                    guard.blocked = str(exc)
                guard.record()
            return process
        c.subprocess = types.SimpleNamespace(**dict(vars(sp), Popen=spawn))
        save = c.save_json
        def evidence(path, value):
            if path.name == 'result.json' and path.parent.parent == base.LOGS:
                value['automatic_input_process_guard'] = guard.summary()
                print('GOW93_PROCESS_GUARD_CHECKS='+str(guard.checked), flush=True)
                if guard.blocked:
                    print('GOW93_PROCESS_GUARD_STOPPED='+guard.blocked, flush=True)
            save(path, value)
        c.save_json = evidence
        return c
    base.core_module = core


def identity_self_test():
    import copy
    binary = dict(device_major=8, device_minor=1, inode=100, size=4096, mtime_ns=10, ctime_ns=10)
    runtime = dict(binary, inode=200)
    launch = dict(pid=123, owner_uid=1000, parent_pid=99, process_group=123,
        session_id=123, start_ticks=456, binary_identity=binary,
        runtime_executables=[{'path': '/runtime/libdynamorio.so', 'identity': runtime}])
    snapshot = dict(launch, state='S', exe_identity=binary, mappings=[])
    assert assess(launch, snapshot)['mode'] == 'direct_application'
    translated = dict(snapshot, exe_identity=runtime, mappings=[dict(binary,
        permissions='r--p', offset=0, path='/app/shadps4')])
    assert assess(launch, translated)['mode'] == 'cpu_translated_application'
    failures = []
    for field in ('pid', 'owner_uid', 'parent_pid', 'process_group', 'session_id', 'start_ticks'):
        bad = copy.deepcopy(translated); bad[field] += 1; failures.append(bad)
    failures.extend([dict(translated, state='Z'), dict(translated, mappings=[]),
        dict(translated, exe_identity=dict(runtime, inode=201)),
        dict(translated, binary_identity=dict(binary, mtime_ns=11))])
    for bad in failures:
        try:
            assess(launch, bad)
        except RuntimeError:
            continue
        raise AssertionError('Unsafe process identity was accepted')
    own = parse_stat((Path('/proc')/str(os.getpid())/'stat').read_text())
    assert own['parent_pid'] == os.getppid() and own['start_ticks'] > 0
    assert parse_maps('1000-2000 r--p 00000000 08:01 100 /app/my\\040game\n')[0]['path']=='/app/my game'
    print('GOW93_IDENTITY_SELF_TEST=PASS direct/translated identity, 10 rejection cases, live proc-stat parsing; no key sent')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if not 120 <= args.seconds <= 900:
        parser.error('--seconds must be between 120 and 900')
    auto = load_auto(); manual = auto.load_base(); base = manual.load_helpers()
    if args.self_test:
        auto.self_test(manual, base); identity_self_test(); return 0
    install(auto, base, manual, args.seconds)
    previous = sys.argv[:]
    try:
        sys.argv = ['gow93-auto-process'] + (['--restore'] if args.restore else [])
        return base.main()
    finally:
        sys.argv = previous


if __name__ == '__main__':
    raise SystemExit(main())
