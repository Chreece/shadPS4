# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Close only processes carrying this launch's private ownership token."""

import ctypes as C
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import uuid

TOKEN_KEY = 'SHADPS4_PES_TEST_OWNER'

DEBUGGER_STOP = r'''
import json, os, signal, sys
from pathlib import Path
work = Path(sys.argv[1])
target = json.loads((work / 'identity.json').read_text())
sent = []
for name in ('debugger.json', 'debugger-launch.json'):
    path = work / name
    if not path.exists():
        continue
    identity = json.loads(path.read_text())
    proc = Path('/proc') / str(identity['pid'])
    fd = None
    try:
        fd = os.pidfd_open(identity['pid'])
        ticks = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        args = (proc / 'cmdline').read_bytes().split(b'\0')
        exe = (proc / 'exe').readlink().name
        if (ticks != identity['start_ticks'] or
                exe not in ('gdb', 'sudo') or b'--batch' not in args or
                ('source ' + str(work / 'probe.py')).encode() not in args or
                target['executable'].encode() not in args):
            raise RuntimeError('Pinned debugger identity changed; no signal sent')
        signal.pidfd_send_signal(fd, getattr(signal, sys.argv[2]))
        sent.append(identity['pid'])
    except (FileNotFoundError, ProcessLookupError):
        pass
    finally:
        if fd is not None:
            os.close(fd)
print(json.dumps({'signal': sys.argv[2], 'pids': sent}))
'''


def stop_debugger(child, work, prefix):
    actions = []
    if child is None:
        return {'complete': True, 'actions': actions}
    def active():
        if child.poll() is None:
            return True
        try:
            identity = json.loads((work / 'debugger.json').read_text())
            fields = (Path('/proc') / str(identity['pid']) / 'stat').read_text().rsplit(')', 1)[1].split()
            return fields[19] == identity['start_ticks'] and fields[0] not in ('Z', 'X')
        except FileNotFoundError:
            return False
    for name in ('SIGTERM', 'SIGKILL'):
        if not active():
            break
        try:
            result = subprocess.run(prefix + [sys.executable, '-c', DEBUGGER_STOP, str(work), name],
                                    capture_output=True, text=True, timeout=5, start_new_session=True)
            actions.append({'signal': name, 'returncode': result.returncode,
                            'output': (result.stdout + result.stderr)[-3000:]})
            child.wait(timeout=3)
            deadline = time.monotonic() + 3
            while active() and time.monotonic() < deadline:
                time.sleep(0.1)
        except (OSError, subprocess.TimeoutExpired) as error:
            actions.append({'error': str(error)})
    return {'complete': not active(), 'actions': actions}


def process_fields(proc):
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': int(proc.name), 'start_ticks': fields[19], 'session': int(fields[3])}


class OwnedLaunch:
    def __init__(self, proc_root=Path('/proc')):
        self.token = uuid.uuid4().hex
        self.proc_root = proc_root
        self.owned = {}
        self.launcher = None
        self.actions = []
        self.errors = []

    def environment(self, environment):
        return dict(environment, **{TOKEN_KEY: self.token})

    def remember(self, pid, expected_ticks=None):
        if pid in self.owned:
            return
        proc = self.proc_root / str(pid)
        before = process_fields(proc)
        if (pid in (1, os.getpid(), os.getppid()) or before['session'] == os.getsid(0) or
                (expected_ticks is not None and before['start_ticks'] != expected_ticks)):
            raise RuntimeError('Process identity does not belong to an isolated test launch')
        fd = os.pidfd_open(pid)
        try:
            if process_fields(proc) != before:
                raise RuntimeError('Process changed while opening its pidfd')
            self.owned[pid] = dict(before, fd=fd)
        except BaseException:
            os.close(fd)
            raise

    def adopt_launcher(self, launcher):
        self.launcher = launcher
        try:
            self.remember(launcher.pid)
        except (FileNotFoundError, ProcessLookupError):
            pass

    def discover(self):
        marker = (TOKEN_KEY + '=' + self.token).encode()
        for proc in self.proc_root.iterdir():
            if not proc.name.isdigit() or int(proc.name) in self.owned:
                continue
            try:
                if proc.stat().st_uid != os.getuid():
                    continue
                before = process_fields(proc)
                if marker not in (proc / 'environ').read_bytes().split(b'\0'):
                    continue
                self.remember(int(proc.name), before['start_ticks'])
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue

    def alive(self):
        return [item for item in self.owned.values()
                if not select.select([item['fd']], [], [], 0)[0]]

    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            self.discover()
            if not self.alive() or time.monotonic() >= deadline:
                return
            time.sleep(0.1)

    def send(self, item, sig):
        try:
            signal.pidfd_send_signal(item['fd'], sig)
            self.actions.append({'pid': item['pid'], 'signal': signal.Signals(sig).name})
        except ProcessLookupError:
            pass
        except OSError as error:
            self.errors.append(str(error))

    def close(self, identity=None, environment=None, grace=5):
        previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            self.discover()
            if identity and identity['pid'] in self.owned:
                item = self.owned[identity['pid']]
                if item['start_ticks'] == identity['start_ticks'] and item in self.alive():
                    try:
                        result = subprocess.run(
                            [sys.executable, __file__, '--close-window', json.dumps(identity)],
                            env=environment, capture_output=True, text=True, timeout=3,
                            start_new_session=True)
                        self.actions.append({'pid': identity['pid'], 'window_close': result.returncode,
                                             'output': (result.stdout + result.stderr)[-2000:]})
                        if result.returncode == 0:
                            self.wait(grace)
                    except (OSError, subprocess.TimeoutExpired) as error:
                        self.actions.append({'window_close_unavailable': str(error)})
            for sig, seconds in ((signal.SIGTERM, grace), (signal.SIGKILL, 2)):
                self.discover()
                for item in self.alive():
                    self.send(item, sig)
                self.wait(seconds)
            if self.launcher is not None and self.launcher.poll() is not None:
                self.launcher.wait()
            remaining = [{k: v for k, v in item.items() if k != 'fd'} for item in self.alive()]
            return {'complete': not remaining and not self.errors, 'remaining': remaining,
                    'actions': self.actions, 'errors': self.errors,
                    'processes': [{k: v for k, v in item.items() if k != 'fd'}
                                  for item in self.owned.values()]}
        finally:
            for item in self.owned.values():
                os.close(item['fd'])
            signal.signal(signal.SIGINT, previous_handler)


def close_window(identity):
    """Send WM_DELETE_WINDOW to the isolated test process, without changing focus."""
    proc = Path('/proc') / str(identity['pid'])
    if process_fields(proc)['start_ticks'] != identity['start_ticks']:
        raise RuntimeError('PES identity changed before window close')
    marker = (TOKEN_KEY + '=' + os.environ.get(TOKEN_KEY, '')).encode()
    if not os.environ.get(TOKEN_KEY) or marker not in (proc / 'environ').read_bytes().split(b'\0'):
        raise RuntimeError('Window owner does not carry this test token')
    lib = C.CDLL('libX11.so.6')
    P, U, I = C.c_void_p, C.c_ulong, C.c_int
    class ClientEvent(C.Structure):
        _fields_ = [('type', I), ('serial', U), ('send_event', I), ('display', P),
                    ('window', U), ('message_type', U), ('format', I), ('data', C.c_long * 5)]
    class Event(C.Union):
        _fields_ = [('client', ClientEvent), ('pad', C.c_long * 24)]
    for name, result, args in (
            ('XOpenDisplay', P, [C.c_char_p]), ('XCloseDisplay', I, [P]),
            ('XDefaultRootWindow', U, [P]), ('XInternAtom', U, [P, C.c_char_p, I]),
            ('XQueryTree', I, [P, U, C.POINTER(U), C.POINTER(U), C.POINTER(C.POINTER(U)), C.POINTER(C.c_uint)]),
            ('XGetWindowProperty', I, [P, U, U, C.c_long, C.c_long, I, U, C.POINTER(U), C.POINTER(I), C.POINTER(U), C.POINTER(U), C.POINTER(P)]),
            ('XFree', I, [P]), ('XSendEvent', I, [P, U, I, C.c_long, C.POINTER(Event)]),
            ('XSync', I, [P, I])):
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = result, args
    display = lib.XOpenDisplay(os.fsencode(os.environ.get('DISPLAY', '')))
    if not display:
        raise RuntimeError('No X11 display available for graceful close')
    try:
        pid_atom = lib.XInternAtom(display, b'_NET_WM_PID', 0)
        pending = [(lib.XDefaultRootWindow(display), 0)]
        matches = []
        visited = set()
        while pending:
            window, depth = pending.pop()
            if window in visited:
                continue
            visited.add(window)
            if len(visited) > 4096:
                raise RuntimeError('Too many X11 windows')
            actual, count, after, fmt, data = U(), U(), U(), I(), P()
            status = lib.XGetWindowProperty(display, window, pid_atom, 0, 1, 0, 6,
                C.byref(actual), C.byref(fmt), C.byref(count), C.byref(after), C.byref(data))
            if not status and fmt.value == 32 and count.value == 1 and data.value:
                if C.cast(data, C.POINTER(U))[0] == identity['pid']:
                    matches.append(window)
            if data.value:
                lib.XFree(data)
            if depth < 3:
                root, parent, size, children = U(), U(), C.c_uint(), C.POINTER(U)()
                if lib.XQueryTree(display, window, C.byref(root), C.byref(parent), C.byref(children), C.byref(size)):
                    pending.extend((child, depth + 1) for child in children[:size.value])
                if children:
                    lib.XFree(children)
        if len(matches) != 1 or process_fields(proc)['start_ticks'] != identity['start_ticks']:
            raise RuntimeError('PES window is missing or ambiguous')
        event = Event(client=ClientEvent(type=33, send_event=1, display=display, window=matches[0],
                      message_type=lib.XInternAtom(display, b'WM_PROTOCOLS', 0), format=32))
        event.client.data[0] = lib.XInternAtom(display, b'WM_DELETE_WINDOW', 0)
        if not lib.XSendEvent(display, matches[0], 0, 0, C.byref(event)):
            raise RuntimeError('X11 close request failed')
        lib.XSync(display, 0)
        print('PES_WINDOW_CLOSE_REQUESTED', flush=True)
    finally:
        lib.XCloseDisplay(display)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3 or sys.argv[1] != '--close-window':
            raise RuntimeError('Internal test-session helper')
        close_window(json.loads(sys.argv[2]))
    except Exception as error:
        print(str(error), flush=True)
        sys.exit(1)
