#!/usr/bin/env python3
"""One-setting UI diagnostic on the already-built font/readback candidate.

No source, compiler, Docker, binary, shader cache, launcher or save changes.
'arm' selects Precise for a manual runtime comparison. 'finish' restores Relaxed
and collects the existing trial logs. This is not a proposed permanent fix.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

RUNNER_SHA = '0d6150ef18802940d35018893a4a2594b272a1f110502e1e2e2100ab8df5eb6d'
REVISION = 'b7cdf433167ddf7cdf3bde8fdd93d70ca09868a1'
BINARY_SHA = 'b0a51fed60dbb8e53e521040ca810b6bfb7978e27b36bb9ccf8fcf575495dc3a'


def load_runner(path: Path):
    if hashlib.sha256(path.read_bytes()).hexdigest() != RUNNER_SHA:
        raise RuntimeError('Existing v3 runner differs; no settings changed')
    spec = importlib.util.spec_from_file_location('ui_readback_existing_runner', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Cannot load the existing runner')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def check_config(path: Path, *, arming: bool) -> int:
    data = json.loads(path.read_text())
    mode = data.get('GPU', {}).get('readbacks_mode')
    if type(mode) is not int or mode not in (1, 2):
        raise RuntimeError('Unexpected readback mode; no settings changed')
    if arming:
        custom = path.parent / 'custom_configs/CUSA03745.json'
        if custom.exists() and 'readbacks_mode' in json.loads(custom.read_text()).get('GPU', {}):
            raise RuntimeError('A game configuration overrides the mode; no settings changed')
    return mode


def operate(m, action: str) -> None:
    m.no_game()  # Never change settings while the game can save them back.
    state = json.loads(m.STATE.read_text())
    if not state.get('ready') or state.get('phase') != 'candidate':
        raise RuntimeError('The tested candidate is not selected; no settings changed')
    if state.get('candidate_head') != REVISION or state.get('candidate_sha') != BINARY_SHA:
        raise RuntimeError('Trial revision differs from the captured UI-failing build')
    if m.sha(m.DST) != BINARY_SHA:
        raise RuntimeError('Installed executable changed; no settings changed')
    config = Path(state['config'])
    record_path = m.TRIAL / 'ui-readback-mode-control.json'
    report_path = m.REPORT / 'ui-readback-mode-control.json'
    record = json.loads(record_path.read_text()) if record_path.exists() else None
    if record and (record.get('revision') != REVISION or record.get('config') != str(config)):
        raise RuntimeError('An unrelated control record exists; left unchanged')
    mode = check_config(config, arming=action == 'arm')
    if action == 'arm':
        if record and record.get('status') in ('prepared', 'armed'):
            if mode == 2:
                print('READY=UI_PRECISE_CONTROL_ALREADY_ARMED')
                return
        else:
            if mode != 1:
                raise RuntimeError('Expected Relaxed=1 before starting this control')
            record = dict(revision=REVISION, binary_sha256=BINARY_SHA, config=str(config),
                          saved_mode=1, trial_mode=2, started=time.time(), status='prepared',
                          purpose='Same-binary read-protection timing comparison; not a UI fix')
            # Local recovery snapshot only: do not copy full configuration to the report archive.
            m.atomic_bytes(m.TRIAL / 'config-before-ui-mode-control.json', config.read_bytes())
            m.write_json(record_path, record)
        m.change_mode(config, 2, {1})
        record['status'] = 'armed'
        m.write_json(record_path, record)
        m.write_json(report_path, record)
        print('READY=UI_PRECISE_CONTROL')
        print('READBACKS_MODE=2 (temporary)')
        print('BINARY_UNCHANGED=' + BINARY_SHA)
        print('Launch Guardian through the same Moonlight -> ES-DE entry.')
        print('Check speech subtitles and the selected-menu marker, then close the game.')
        print('Run this helper with finish to restore Relaxed and collect the log archive.')
    else:
        if record is None:
            raise RuntimeError('No mode control was armed; no settings changed')
        m.change_mode(config, 1, {1, 2})
        record['status'] = 'restored'
        record['finished'] = time.time()
        m.write_json(record_path, record)
        m.write_json(report_path, record)
        print('RESTORED_READBACKS_MODE=1')
        print('BINARY_UNCHANGED=' + BINARY_SHA)
        m.collect(state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('arm', 'finish'))
    args = parser.parse_args()
    try:
        m = load_runner(Path.home() / '.cache/shadps4_readback_docker_trial.py')
        with (m.TRIAL / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            operate(m, args.action)
        return 0
    except (Exception, KeyboardInterrupt) as e:
        print('STOPPED=' + str(e), file=sys.stderr)
        print('No source or binary was changed. With the game closed, finish restores an armed control.')
        return 1
    finally:
        print('SSH_SESSION=REMAINS_OPEN')


if __name__ == '__main__':
    raise SystemExit(main())
