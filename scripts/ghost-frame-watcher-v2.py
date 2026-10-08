#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# One-session headless frame and overlay capture. Does not stop the emulator.
import ctypes
import ctypes.util
import json
import os
import re
import shutil
import tarfile
import time
from datetime import datetime
from pathlib import Path
import subprocess

home = Path.home()
state = home / '.local/state/shadps4-playtest-logs'
start = time.time()
stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
out = home / f'ghost-fullstack-proof-{stamp}.tar.gz'
tmp = home / '.cache' / f'ghost-proof-{stamp}'
(tmp / 'screenshots').mkdir(parents=True, exist_ok=True)
status = []

def report(line):
    print(line, flush=True)
    status.append(line)

def get_pid(meta):
    match = re.search(r'^launcher_pid=(\d+)$', meta, re.M)
    return int(match.group(1)) if match else None

def collect_process_snapshot(seconds):
    """Capture thread health without attaching a debugger or pausing the game."""
    binary = home / 'Applications/shadps4/shadps4'
    found = []
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():
            continue
        try:
            if (item / 'exe').resolve() == binary.resolve():
                found.append(int(item.name))
        except (OSError, PermissionError):
            continue
    chunks = []
    for pid in found[:3]:
        try:
            proc = subprocess.run(
                ['ps', '-L', '-p', str(pid), '-o',
                 'pid,tid,stat,pcpu,wchan:28,comm'],
                text=True, capture_output=True, timeout=6)
            chunks.append(f'PID {pid}\n{proc.stdout}{proc.stderr}')
            status = Path(f'/proc/{pid}/status').read_text(errors='replace')
            chunks.append(status[:10000])
        except Exception as exc:
            chunks.append(f'PID {pid}: {exc}')
    (tmp / f'process-{seconds:03d}s.txt').write_text(
        '\n'.join(chunks) if chunks else 'No running shadps4 process found')
    report(f'+{seconds}s thread snapshot: {found[:3]}')


def focus_capture(display, authority, overlays):
    """Send the documented screenshot shortcut to active X11 window.
    Does not focus, grab or change the game window."""
    original_display = os.environ.get('DISPLAY')
    original_xauth = os.environ.get('XAUTHORITY')
    try:
        os.environ['DISPLAY'] = display or ':0'
        if authority:
            os.environ['XAUTHORITY'] = authority
        x11 = ctypes.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
        xtst = ctypes.CDLL(ctypes.util.find_library('Xtst') or 'libXtst.so.6')
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        x11.XFlush.argtypes = [ctypes.c_void_p]
        xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.c_int, ctypes.c_ulong]
        xd = x11.XOpenDisplay(os.environ['DISPLAY'].encode())
        if not xd:
            raise RuntimeError('Cannot access active X11 display')
        try:
            f12 = x11.XKeysymToKeycode(xd, 0xFFC9)
            alt = x11.XKeysymToKeycode(xd, 0xFFE9)
            if not f12 or (overlays and not alt):
                raise RuntimeError('X11 keycode unavailable')
            if overlays:
                xtst.XTestFakeKeyEvent(xd, alt, 1, 0)
            xtst.XTestFakeKeyEvent(xd, f12, 1, 0)
            x11.XFlush(xd)
            time.sleep(0.15)
            xtst.XTestFakeKeyEvent(xd, f12, 0, 0)
            if overlays:
                xtst.XTestFakeKeyEvent(xd, alt, 0, 0)
            x11.XFlush(xd)
            return True
        finally:
            x11.XCloseDisplay(xd)
    except Exception as exc:
        report(f'XTest shortcut failed: {exc}')
        return False
    finally:
        if original_display is None:
            os.environ.pop('DISPLAY', None)
        else:
            os.environ['DISPLAY'] = original_display
        if original_xauth is None:
            os.environ.pop('XAUTHORITY', None)
        else:
            os.environ['XAUTHORITY'] = original_xauth

try:
    report('ARMED: Launch Ghost of Tsushima via Moonlight > ES-DE.')
    session = None
    while time.time() - start < 600:
        candidates = []
        for item in state.glob('*Ghost_of_Tsushima.ps4/session.meta'):
            try:
                if item.stat().st_mtime >= start - 2:
                    candidates.append(item)
            except OSError:
                pass
        if candidates:
            session = max(candidates, key=lambda p: p.stat().st_mtime).parent
            break
        time.sleep(1.0)

    if session:
        report(f'SESSION={session}')
        meta = (session / 'session.meta').read_text(errors='replace')
        pid = get_pid(meta)
        display = re.search(r'^display=(.*)$', meta, re.M)
        display = display.group(1) if display else ':0'
        auth = None
        if pid and Path(f'/proc/{pid}/environ').exists():
            try:
                for line in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
                    if line.startswith(b'XAUTHORITY='):
                        auth = line[11:].decode(errors='replace')
            except OSError:
                pass
        if not auth:
            default = home / '.Xauthority'
            if default.exists():
                auth = str(default)

        (tmp / 'session.meta').write_text(meta)
        detected = time.time()
        for delay in (4, 7, 10, 15, 25, 55, 90):
            while time.time() - detected < delay:
                if pid and not Path(f'/proc/{pid}').exists():
                    break
                time.sleep(0.5)
            if pid and not Path(f'/proc/{pid}').exists():
                report(f'Launcher exited before +{delay}s capture.')
                break
            if delay in (7, 25, 55, 90):
                collect_process_snapshot(delay)
            report(f'+{delay}s F12 game-only: {focus_capture(display, auth, False)}')
            time.sleep(1.2)
            report(f'+{delay}s Alt+F12 overlay: {focus_capture(display, auth, True)}')

        while time.time() - detected < 420:
            if pid and not Path(f'/proc/{pid}').exists():
                report('Launcher exited; collecting session evidence.')
                break
            time.sleep(1.0)
        else:
            report('Observation window ended; no emulator processes were stopped.')
        time.sleep(3)

        log_path = session / 'runtime.log'
        screen_candidates = set()
        if log_path.is_file():
            raw = log_path.read_text(errors='replace')
            log_bytes = log_path.read_bytes()
            (tmp / 'runtime.log').write_bytes(log_bytes[-80 * 1024 * 1024:])
            counts = {}
            for term in ('GHOST_TRACE', 'shader=',
                         'Unexpected instruction for offset computation',
                         'Phi', 'ReadLane', 'Clamped size from',
                         'Saved screenshot:', 'Unhandled Exception',
                         'Quit', 'FP64', 'SPIR-V'):
                counts[term] = raw.count(term)
            (tmp / 'issue-counts.json').write_text(json.dumps(counts, indent=2))
            for value in re.findall(r'Saved screenshot:\s*(.+?\.png)', raw):
                screen_candidates.add(Path(value.strip()))

        bases = [home / '.local/share/shadPS4',
                 home / 'Applications/shadps4/user',
                 home / '.cache/shadps4-esde-latest-pending/source/user']
        if os.environ.get('XDG_DATA_HOME'):
            bases.append(Path(os.environ['XDG_DATA_HOME']) / 'shadPS4')
        for base in bases:
            for screenshot in (base / 'screenshots').glob('CUSA11456_*.png'):
                try:
                    if screenshot.stat().st_mtime >= start - 3:
                        screen_candidates.add(screenshot)
                except OSError:
                    pass
        count = 0
        for screenshot in sorted(screen_candidates):
            if screenshot.is_file() and count < 24:
                try:
                    shutil.copy2(screenshot, tmp / 'screenshots' / screenshot.name)
                    count += 1
                except OSError as exc:
                    report(f'Screenshot unavailable: {exc}')
        report(f'SCREENSHOTS_COLLECTED={count}')
    else:
        report('No new Ghost of Tsushima session within the observation window.')

    marker = state / 'current-verified-deployment.txt'
    if marker.is_file():
        shutil.copy2(marker, tmp / 'deployment.txt')
except Exception as exc:
    report(f'WATCHER_ERROR={type(exc).__name__}: {exc}')
finally:
    (tmp / 'capture-status.txt').write_text('\n'.join(status) + '\n')
    try:
        with tarfile.open(out, 'w:gz') as archive:
            archive.add(tmp, arcname=tmp.name)
        report(f'ARCHIVE={out}')
    except OSError as exc:
        report(f'ARCHIVE_ERROR={exc}')
    shutil.rmtree(tmp, ignore_errors=True)
