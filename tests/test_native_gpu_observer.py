#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression for a native Vulkan game stuck uninterruptibly after SIGKILL.

Never sends a real signal; mocks the one child process group that an
observer may terminate. The real old observer lost *all* screenshots and
console evidence when the second subprocess.wait(timeout=3) raised.
"""
from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
import signal
import subprocess
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "tools/development/shadps4_native_gpu_observer.py"
spec = importlib.util.spec_from_file_location("native_gpu_observer", path)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class StuckChild:
    pid = 98765
    wait_timeouts = []
    def poll(self):
        return None
    def wait(self, timeout):
        self.wait_timeouts.append(timeout)
        raise subprocess.TimeoutExpired(["shadps4"], timeout)

child = StuckChild()
calls = []
with patch.object(module.os, "killpg", side_effect=lambda pid, sig: calls.append((pid, sig))), \
     patch.object(module, "pending_process_state", return_value={
         "pid":98765,"status":"D (disk sleep)","wait_channel":"wait_on_gpu"}), \
     redirect_stdout(io.StringIO()) as output:
    result = module.term_owned(child, "BOUND_ONLY_OWNED_PROCESS")
assert result is False
assert child.wait_timeouts == [2,3], child.wait_timeouts
assert calls == [(98765,signal.SIGTERM),(98765,signal.SIGKILL)], calls
assert "OWNED_GAME_PROCESS_STILL_ALIVE_AFTER_SIGKILL=98765" in output.getvalue()

class AlreadyExited:
    pid = 98766
    def poll(self):
        return 0
assert module.term_owned(AlreadyExited(), "ALREADY_EXITED") is True
assert len(calls) == 2

print("GENERIC_NATIVE_GPU_OBSERVER_TIMEOUT_RECOVERY=PASS"
      " no pkill, no display service signals, no uncaught timeout")


# Zombie after SIGKILL is no longer running even if waitpid did not reap it.
class ZombieChild(StuckChild):
    pid = 98767
    wait_timeouts = []
zombie=ZombieChild()
with patch.object(module.os,"killpg"), \
     patch.object(module,"pending_process_state",return_value={
         "pid":zombie.pid,"status":"Z (zombie)","wait_channel":"0"}), \
     redirect_stdout(io.StringIO()) as zombie_log:
    assert module.term_owned(zombie,"ZOMBIE") is False
assert zombie.wait_timeouts==[2,3]
assert "OWNED_GAME_EXITED_BUT_UNREAPED_AFTER_SIGKILL=98767" in zombie_log.getvalue()

# Prove the portable FFmpeg fallback actually issues a real X11grab command.
# No X11, emulator, or signal is invoked in this unit test.
import tempfile
from unittest.mock import Mock
with tempfile.TemporaryDirectory() as directory:
    report=Path(directory)
    screenshot=report/'frame-011s.png'
    cmd_seen=[]
    def fake_which(name):
        return '/usr/bin/'+name if name in ('ffmpeg','xdpyinfo') else None
    def fake_run(argv,**kwargs):
        cmd_seen.append(argv)
        if argv[0]=='xdpyinfo':
            return subprocess.CompletedProcess(argv,0,
                stdout=b'  dimensions:    1920x1080 pixels',stderr=b'')
        if argv[0]=='ffmpeg':
            screenshot.write_bytes(b'SYNTHETIC_PNG_FROM_MOCK')
            return subprocess.CompletedProcess(argv,0,stdout=b'',stderr=b'')
        raise AssertionError(argv)
    with patch.object(module.shutil,"which",side_effect=fake_which), \
         patch.object(module.subprocess,"run",side_effect=fake_run):
        assert module.take_picture(report,11,':0') is True
    assert len(cmd_seen)==2
    assert '-video_size' in cmd_seen[1]
    assert cmd_seen[1][cmd_seen[1].index('-video_size')+1]=='1920x1080'
    assert 'x11grab' in cmd_seen[1]
    assert not (report/'screenshot-errors.log').exists()

with tempfile.TemporaryDirectory() as directory:
    report=Path(directory)
    with patch.object(module.shutil,"which",return_value=None):
        assert module.take_picture(report,21,':0') is False
    errors=(report/'screenshot-errors.log').read_text()
    assert 'ImageMagick import unavailable' in errors
    assert 'FFmpeg unavailable' in errors

print("ZOMBIE_NOT_MISCLASSIFIED_AS_LIVE=PASS")
print("GENERIC_X11_SCREENSHOT_FALLBACK=PASS")
