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
