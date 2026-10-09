#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only A/B of Ghost guest scheduler during rendering vs. confirmed stall.

Runs the exact already-installed, SHA-verified baseline shadPS4, not a CPU-PR
build. Takes only one GDB snapshot while flips advance, and another when flips
stop but vblank advances. GDB attaches briefly, reads memory and detaches.
Does not patch game, save, emulator, CPU configuration, or SSH session.
"""
from __future__ import annotations
import importlib.util
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import traceback
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

HOME = Path.home()
STAMP = datetime.now().strftime('%Y%m%d-%H%M%S')
WORK = HOME/'.cache'/('ghost-scheduler-before-after-'+STAMP)
OUT = HOME/('ghost-scheduler-before-after-'+STAMP+'.tar.gz')
BASE = 'f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f'
FILES = (
    ('ghost-auto-verified-cpu-pr-code-v5-20261009.py',
     '66c4a346580fa565d51d52698b03e957dce7796c',
     'ca85add43cc96cc2660abf8b2f9df39e2f5af8d7'),
    ('ghost-guest-timing-queue-snapshot-20261009.py',
     'ea52533bff6eabf47507336d73a8df37dc8f7f09',
     '6b760dc94b951395bb31914d4c574ea2d9ba7bb8'),
)
EVENTS = []
FRAMES = []
SNAPSHOTS = []
STATUS = 'not_started'
ERROR = None


def say(message: str) -> None:
    line = f'[{datetime.now().isoformat(timespec="seconds")}] {message}'
    EVENTS.append(line)
    print(line, flush=True)


def download(name: str, ref: str, blob: str) -> Path:
    p = WORK/name
    subprocess.run(['curl', '-fsSL', '--retry', '2', '--max-time', '35',
                    f'https://raw.githubusercontent.com/Chreece/shadPS4/{ref}/scripts/{name}',
                    '-o', str(p)], check=True, timeout=55)
    actual = subprocess.check_output(['git', 'hash-object', str(p)], text=True).strip()
    if actual != blob:
        raise RuntimeError(f'Helper integrity mismatch: {name}: {actual}')
    subprocess.run([sys.executable, '-m', 'py_compile', str(p)], check=True, timeout=30)
    return p


def exact_game_pid(mod) -> int | None:
    try:
        if mod.SESSION_DIR is None:
            return None
        meta = (mod.SESSION_DIR/'session.meta').read_text(errors='replace')
        m = re.search(r'(?m)^launcher_pid=(\d+)$', meta)
        pid = int(m[1]) if m else 0
        return pid if pid > 1 and mod.exact_ghost(pid) else None
    except (OSError, ValueError, TypeError):
        return None


def should_early_snapshot(previous: tuple[int, int] | None,
                          current: tuple[int, int], taken: bool) -> bool:
    if previous is None or taken:
        return False
    prev_vblank, prev_flips = previous
    vblank, flips = current
    return 200 <= flips <= 800 and flips > prev_flips and vblank > prev_vblank


def should_late_snapshot(current: tuple[int, int],
                         frozen_since: float | None,
                         frozen_vblank: int | None,
                         elapsed: float, early_taken: bool) -> bool:
    vblank, flips = current
    return bool(early_taken and frozen_since is not None and frozen_vblank is not None
                and flips >= 200 and elapsed - frozen_since >= 12
                and vblank - frozen_vblank >= 150)


def snapshot(helper: Path, pid: int, label: str, current: tuple[int, int]) -> bool:
    record = {'label': label, 'guest_pid': pid, 'frame': current, 'elapsed_at': time.time()}
    try:
        task = subprocess.run([sys.executable, '-I', str(helper), '--pid', str(pid),
                               '--outdir', str(WORK), '--label', label],
                              capture_output=True, text=True, timeout=30)
        record['returncode'] = task.returncode
        if task.stdout or task.stderr:
            (WORK/f'snapshot-{label}-controller.log').write_text(task.stdout+task.stderr)
        state = WORK/f'gdb-guest-stall-{label}.status'
        record['gdb_status'] = state.read_text(errors='replace') if state.exists() else 'none'
        record['completed'] = 'begin_marker=True' in record['gdb_status'] and 'end_marker=True' in record['gdb_status']
        say(f'SCHEDULER_SNAPSHOT_{label}=completed:{record["completed"]} flips:{current[1]} vblank:{current[0]}')
        return record['completed']
    except (OSError, subprocess.TimeoutExpired) as exc:
        record['error'] = repr(exc)
        say(f'SCHEDULER_SNAPSHOT_{label}_ERROR={exc}')
        return False
    finally:
        SNAPSHOTS.append(record)


def selftest() -> None:
    assert len(FILES) == 2 and all(len(blob) == 40 for _, _, blob in FILES)
    assert len(BASE) == 64 and re.fullmatch(r'[0-9a-f]{64}', BASE)
    with TemporaryDirectory() as d:
        p = Path(d)
        (p/'session.meta').write_text('launcher_pid=1234\n')
        fake = SimpleNamespace(SESSION_DIR=p, exact_ghost=lambda pid: pid == 1234)
        assert exact_game_pid(fake) == 1234
        fake.exact_ghost = lambda pid: False
        assert exact_game_pid(fake) is None
    assert should_early_snapshot((600, 260), (780, 350), False)
    assert not should_early_snapshot((600, 260), (780, 260), False)
    assert not should_early_snapshot((600, 260), (780, 350), True)
    assert should_late_snapshot((1500, 530), 20, 1180, 35, True)
    assert not should_late_snapshot((1500, 530), 20, 1180, 35, False)
    assert not should_late_snapshot((1500, 530), 30, 1180, 35, True)
    assert not should_late_snapshot((1250, 530), 20, 1180, 35, True)
    say('SELFTEST_PASS=prestall_rising_flips,poststall_vblank_clock,owned_guest_pid')


def main() -> int:
    global STATUS, ERROR
    if sys.argv[1:] == ['--self-test']:
        selftest()
        return 0
    WORK.mkdir(parents=True, exist_ok=False)
    (WORK/'screenshots').mkdir()
    mod = None
    try:
        controller = download(*FILES[0]); helper = download(*FILES[1])
        subprocess.run([sys.executable, '-I', str(helper), '--self-test'], check=True, timeout=20)
        spec = importlib.util.spec_from_file_location('ghost_control', controller)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.WORK = WORK
        mod.SCREEN_DIR = WORK/'screenshots'
        mod.OUT = OUT
        if mod.emulator_pids():
            raise RuntimeError('Existing shadPS4 running; refusing takeover.')
        if (not mod.LAUNCHER.is_file() or mod.LAUNCHER.is_symlink() or
                not os.access(mod.LAUNCHER, os.X_OK)):
            raise RuntimeError('Guarded ES-DE launcher missing.')
        launcher = mod.LAUNCHER.read_text(errors='replace')
        if not all(x in launcher for x in ('SHADPS4_SESSION_GUARD_V1', 'SHADPS4_DEFAULT_MAIN_V1')):
            raise RuntimeError('Launcher differs from verified setup.')
        if not mod.ENTRY.exists() or not mod.BINARY.is_file() or mod.checksum(mod.BINARY) != BASE:
            raise RuntimeError('Ghost or installed executable differs from verified baseline.')
        env = mod.display_probe()
        env['SHADPS4_GRAPHICS_DIAGNOSTICS'] = '1'
        env['SHADPS4_STARTUP_DIAGNOSTICS'] = '1'
        run = Path('/run/user') / str(os.getuid())
        if 'XDG_RUNTIME_DIR' not in env and run.is_dir():
            env['XDG_RUNTIME_DIR'] = str(run)
        for key in ('RADV_DEBUG', 'SHADPS4_CPU_ID_MODE', 'GHOST_CPU_RIP_LOG'):
            env.pop(key, None)
        say('BASELINE_SHA_VERIFIED no_CMake no_PR no_GPU_config_changes')
        mod.launch_test(env)
        STATUS = 'observing'
        started = time.monotonic()
        prev = None
        frozen_since = None
        frozen_vblank = None
        early_taken = False
        late_taken = False
        while time.monotonic() - started < 100:
            elapsed = time.monotonic() - started
            if mod.SESSION_DIR is None:
                mod.SESSION_DIR = mod.current_session()
            pid = exact_game_pid(mod)
            if mod.GAME_PROC.poll() is not None and pid is None:
                STATUS = 'game_exited'
                say(f'GAME_EXITED_AFTER={elapsed:.1f}s')
                break
            current = mod.latest_guest_vblank_flips(mod.SESSION_DIR/'runtime.log') if mod.SESSION_DIR else None
            if current:
                vb, flips = current
                if not FRAMES or elapsed - FRAMES[-1]['seconds'] >= 2:
                    FRAMES.append({'seconds': round(elapsed,1), 'vblank': vb, 'flips': flips})
                if pid and should_early_snapshot(prev, current, early_taken):
                    early_taken = True
                    say(f'RENDER_ACTIVE snapshot A before stall: vblank={vb} flips={flips}')
                    snapshot(helper, pid, 'A', current)
                if prev is None or flips != prev[1]:
                    frozen_since = elapsed
                    frozen_vblank = vb
                elif pid and should_late_snapshot(current, frozen_since, frozen_vblank, elapsed, early_taken):
                    late_taken = True
                    say(f'CONFIRMED_STALL snapshot B after stall: vblank={vb} flips={flips}')
                    snapshot(helper, pid, 'B', current)
                    STATUS = 'captured_before_and_after'
                    break
                prev = current
            time.sleep(.5)
        else:
            STATUS = 'timeout_no_stall'
            say('TIME_LIMIT=100s no_matching_before_after_stall')
        if not early_taken:
            say('EARLY_SNAPSHOT_MISSING: no active frame window found')
        if not late_taken:
            say('LATE_SNAPSHOT_MISSING: game did not meet the confirmed stall criteria')
    except BaseException:
        ERROR = traceback.format_exc()
        say('ERROR=' + ERROR.splitlines()[-1])
    finally:
        if mod and mod.GAME_PROC:
            try:
                mod.stop_launched_game()
            except Exception as exc:
                ERROR = ERROR or repr(exc)
                say('TEST_GAME_CLEANUP_ERROR=' + repr(exc))
        if mod:
            for fn in (mod.collect_screenshots, mod.report_session, mod.capture_readonly_gpu_state):
                try: fn()
                except Exception as exc: say('EVIDENCE_COLLECTION_ERROR=' + fn.__name__ + ':' + repr(exc))
        (WORK/'status.json').write_text(json.dumps({
            'status': STATUS, 'error': ERROR, 'snapshots': SNAPSHOTS,
            'installed_binary_sha256': BASE, 'no_source_edits': True,
            'frames': FRAMES, 'session': str(mod.SESSION_DIR) if mod and mod.SESSION_DIR else None,
        }, indent=2) + '\n')
        (WORK/'events.txt').write_text('\n'.join(EVENTS)+'\n')
        with tarfile.open(OUT, 'w:gz') as f:
            for p in sorted(WORK.iterdir()):
                if p.name not in (FILES[0][0], FILES[1][0], '__pycache__'):
                    f.add(p, arcname=p.name)
        say('UPLOAD_THIS_FILE=' + str(OUT))
        say('SSH_SESSION=REMAINS_OPEN')
    return 0 if ERROR is None else 1

if __name__ == '__main__':
    raise SystemExit(main())
