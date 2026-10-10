#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Attach GDB only to the capture's own child; perform a crash preflight."""
import ctypes
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def permit_parent():
    lib = ctypes.CDLL(None, use_errno=True)
    if lib.prctl(ctypes.c_int(0x59616d61), ctypes.c_ulong(os.getppid()),
                 ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0)):
        error = ctypes.get_errno()
        if error == errno.EINVAL and not Path('/proc/sys/kernel/yama/ptrace_scope').exists():
            return
        raise OSError(error, 'Allow parent-owned debugger')


def start(child, owner, stage, script, build_dir):
    (stage / 'debug-target.json').write_text(json.dumps({**owner, 'build_dir': str(build_dir)}))
    env = dict(os.environ, PES_GDB_STAGE=str(stage), DEBUGINFOD_URLS='')
    with (stage / 'gdb.log').open('w') as output:
        debug = subprocess.Popen(['gdb', '-nx', '-nh', '-q', '--batch',
                                  '-iex', 'set auto-load off', '-x', str(script)],
                                 env=env, cwd=stage, stdin=subprocess.DEVNULL, stdout=output,
                                 stderr=subprocess.STDOUT, start_new_session=True)
    debug.pes_stage = stage
    return debug


def wait_ready(debug, child, stage, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (stage / 'debug-ready.json').exists():
            if (stage / 'debug-error.txt').exists():
                raise RuntimeError('Debugger failed; see debug-error.txt')
            return
        if debug.poll() is not None or child.poll() is not None:
            raise RuntimeError('Debugger attach failed or target exited; see gdb.log')
        time.sleep(.1)
    raise RuntimeError('Debugger attach exceeded 20 seconds; see gdb.log')


def finish(debug):
    if debug is None:
        return
    (debug.pes_stage / 'debug-stop').touch()
    if debug.poll() is None:
        debug.send_signal(signal.SIGINT)
        try:
            debug.wait(timeout=5)
        except subprocess.TimeoutExpired:
            debug.terminate()
            try:
                debug.wait(timeout=3)
            except subprocess.TimeoutExpired:
                debug.kill()
                debug.wait(timeout=3)


def preflight(runner, manual, root, report):
    runner.say('Checking GDB capture on an isolated helper before building PES')
    folder = report / 'debugger-preflight'
    folder.mkdir()
    child = debug = None
    code = ('import os,signal,time,pathlib,sys; p=pathlib.Path(sys.argv[1]); '
            'deadline=time.monotonic()+30\n'
            'while not p.exists() and time.monotonic()<deadline: time.sleep(.05)\n'
            'os.kill(os.getpid(),signal.SIGBUS) if p.exists() else sys.exit(2)\n')
    try:
        def setup():
            permit_parent()
            runner.child_setup(root)
        child = subprocess.Popen([sys.executable, '-c', code, str(folder / 'fire')],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True,
                                 preexec_fn=setup)
        debug = start(child, manual.identity(child.pid), folder,
                      runner.HERE / 'gdb_capture.py', root / 'build')
        wait_ready(debug, child, folder)
        (folder / 'fire').touch()
        status = child.wait(timeout=20)
        debug.wait(timeout=10)
        evidence = json.loads((folder / 'signal-last.json').read_text())
        if status != -signal.SIGBUS or evidence['signal'] != 'SIGBUS' or (folder / 'debug-error.txt').exists():
            raise RuntimeError('GDB crash preflight failed; see debugger-preflight')
        runner.say('GDB preflight passed: SIGBUS registers, stack and memory maps preserved')
    finally:
        finish(debug)
        if child is not None:
            runner.stop(child)
