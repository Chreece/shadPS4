#!/usr/bin/env python3
"""No-game regression: disconnected SSH stdout cannot prevent emulator cleanup."""
import builtins
import importlib.util
from pathlib import Path
import signal
import subprocess
import sys
from unittest import mock

root=Path(__file__).resolve().parent
target=root/"gow_ollama_vram_gameplay_20261018.py"
spec=importlib.util.spec_from_file_location("gow_hup_cleanup_source",target)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class DummyGame:
    pid=650534
    def __init__(self,late=False,done=False):
        self.signals=[]
        self.late=late
        self.done=done
    def poll(self):
        return 0 if self.done else None
    def wait(self,timeout):
        if not self.signals:
            raise AssertionError("WAIT_CALLED_BEFORE_KILL_SIGNAL")
        if self.late and self.signals[-1] == signal.SIGTERM:
            raise subprocess.TimeoutExpired(["fake_emulator"],timeout)
        self.done=True
        return 0

for late in (False,True):
    game=DummyGame(late=late)
    def killpg(pid,signum):
        assert pid==650534
        game.signals.append(signum)
    with mock.patch.object(module.os,"killpg",side_effect=killpg):
        with mock.patch.object(builtins,"print",side_effect=BrokenPipeError(32,"Broken pipe")):
            module.stop_game(game)
    assert game.done
    assert game.signals == (
        [signal.SIGTERM,signal.SIGKILL] if late else [signal.SIGTERM])
    print("HUP_DISCONNECTED_STDOUT_CLEANUP_PASS="+
          ("SIGTERM_THEN_SIGKILL" if late else "SIGTERM"))
preexited=DummyGame(done=True)
with mock.patch.object(module.os,"killpg",side_effect=AssertionError("MUST_NOT_KILL")):
    module.stop_game(preexited)
    module.stop_game(None)
print("PREEXITED_AND_NO_GAME_SAFE=PASS")
print("NO_EMULATOR_LAUNCH_NO_CONTROLLER_INPUT_NO_SSH_CHANGE=YES")
