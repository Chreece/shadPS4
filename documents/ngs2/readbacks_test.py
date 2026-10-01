#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reversible RDR readback comparison or Vulkan validation on the existing core."""

import argparse
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import time

REVISION = '10ff9e19a7d94340aaedd1e333f1a11abeeb9e75'
COLLECTOR_SHA256 = '13ca1e6004bfaed5e0a916282d27a1f46109a0ac29b9b13e975aa7c86a24150a'
VALIDATION_SETTINGS = {'vkvalidation_enabled': True, 'vkvalidation_core_enabled': True,
                       'vkvalidation_sync_enabled': True, 'vkvalidation_gpu_enabled': False}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require_validation_layer():
    class LayerProperties(ctypes.Structure):
        _fields_ = [('name', ctypes.c_char * 256), ('spec_version', ctypes.c_uint32),
                    ('implementation_version', ctypes.c_uint32),
                    ('description', ctypes.c_char * 256)]

    loader = ctypes.CDLL('libvulkan.so.1')
    enumerate_layers = loader.vkEnumerateInstanceLayerProperties
    enumerate_layers.argtypes = [ctypes.POINTER(ctypes.c_uint32),
                                ctypes.POINTER(LayerProperties)]
    enumerate_layers.restype = ctypes.c_int32
    for _ in range(3):
        count = ctypes.c_uint32()
        if enumerate_layers(ctypes.byref(count), None) != 0 or count.value > 4096:
            raise RuntimeError('Could not enumerate Vulkan layers; nothing changed.')
        properties = (LayerProperties * count.value)()
        result = enumerate_layers(ctypes.byref(count), properties)
        if result == 5:  # VK_INCOMPLETE: the layer list changed between calls.
            continue
        if result != 0:
            raise RuntimeError('Could not read Vulkan layers; nothing changed.')
        if any(item.name == b'VK_LAYER_KHRONOS_validation' for item in properties):
            return
        raise RuntimeError('Vulkan validation layer is missing; nothing changed. Run '
                           '`sudo apt-get install vulkan-validationlayers`, then rerun this test.')
    raise RuntimeError('Vulkan layer list kept changing; nothing changed.')


def no_running_core():
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            target = (process / 'exe').readlink()
        except OSError:
            continue
        if target.name.lower().removesuffix(' (deleted)') in ('shadps4', 'shadps4.exe'):
            raise RuntimeError(f'Close shadPS4 normally first (PID {process.name}), then rerun.')


def profile_path(home):
    root = home / '.local/share/shadPS4'
    profile = root / 'custom_configs/CUSA36843.json'
    for path in (root, profile.parent, profile):
        if path.is_symlink():
            raise RuntimeError('Unexpected configuration symlink: ' + str(path))
    return profile


def atomic_write(path, data, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(home, validation=False, collector=None):
    no_running_core()
    profile = profile_path(home)
    root = profile.parent.parent
    data_home = os.environ.get('XDG_DATA_HOME')
    if data_home and Path(data_home) / 'shadPS4' != root:
        raise RuntimeError('XDG_DATA_HOME differs from the captured installation; nothing changed.')
    wrapper = home / '.local/bin/shadps4-esde'
    if '# NGS2 isolated core selection: ' + REVISION not in wrapper.read_text():
        raise RuntimeError('The selected test build is not 10ff9e19; nothing changed.')
    global_config = json.loads((root / 'config.json').read_text())
    existed = profile.exists()
    original = profile.read_bytes() if existed else b''
    config = json.loads(original) if existed else {}
    if (not isinstance(config, dict) or not isinstance(config.get('GPU', {}), dict) or
            not isinstance(config.get('Vulkan', {}), dict)):
        raise RuntimeError('Unexpected per-game configuration format; nothing changed.')
    previous = config.get('GPU', {}).get('readbacks_mode',
                                        global_config.get('GPU', {}).get('readbacks_mode', 0))
    if not validation and previous == 2:
        print('READBACK_TEST_RESULT=UNCHANGED: precise readbacks are already selected.')
        return
    if validation:
        require_validation_layer()
        if collector is None or digest(collector.read_bytes()) != COLLECTOR_SHA256:
            raise RuntimeError('Missing or unexpected graphics collector; nothing changed.')
        config.setdefault('Vulkan', {}).update(VALIDATION_SETTINGS)
    else:
        config.setdefault('GPU', {})['readbacks_mode'] = 2
    installed = (json.dumps(config, indent=2) + '\n').encode()
    mode = stat.S_IMODE(profile.stat().st_mode) if existed else 0o600
    parent = home / '.local/state/shadps4-graphics-readbacks'
    parent.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='test-', dir=parent))
    helper = backup / 'readbacks_test.py'
    atomic_write(helper, Path(__file__).read_bytes())
    if validation:
        atomic_write(backup / 'collect_graphics.py', collector.read_bytes())
    record = backup / 'state.json'
    state = {'revision': REVISION, 'profile': str(profile), 'existed': existed,
             'original_base64': base64.b64encode(original).decode(),
             'original_sha256': digest(original), 'original_mode': mode,
             'installed_sha256': digest(installed), 'previous_effective_mode': previous,
             'purpose': 'validation' if validation else 'readbacks',
             'created_unix': time.time()}
    atomic_write(record, (json.dumps(state, indent=2) + '\n').encode())
    profile.parent.mkdir(parents=True, exist_ok=True)
    no_running_core()
    profile_path(home)
    if profile.exists() != existed or (existed and profile.read_bytes() != original):
        raise RuntimeError('Configuration changed during preparation; nothing was replaced.')
    atomic_write(profile, installed, mode)
    if validation:
        print('VALIDATION_TEST_RESULT=PASS: CUSA36843 core and synchronization validation enabled.')
        print('READBACK_MODE_UNCHANGED=' + str(previous))
        print('After a brief reproduction, exit the emulator normally and run:')
        print('FINISH=python3 ' + shlex.quote(str(helper)) + ' --finish-validation ' +
              shlex.quote(str(record)))
        print('FINISH collects the logs and restores the exact pre-test per-game configuration.')
    else:
        print('READBACK_TEST_RESULT=PASS: CUSA36843 GPU.readbacks_mode = 2 (Precise).')
    print('PREVIOUS_EFFECTIVE_MODE=' + str(previous))
    print('CURRENT_BUILD=' + REVISION)
    print('RESTORE=python3 ' + shlex.quote(str(helper)) + ' --restore ' + shlex.quote(str(record)))
    print('Start the same NGS2 trace entry and reproduce the missing character/scenery surfaces.')
    if validation:
        print('Validation may slow the game. A clean validation log would not rule out a GPU bug.')
    print('Global settings, audio, saves, launcher and executable were not modified.')


def restore(home, record):
    no_running_core()
    state = json.loads(record.read_text())
    profile = profile_path(home)
    if state['profile'] != str(profile) or state['revision'] != REVISION:
        raise RuntimeError('Restore record does not match this test installation.')
    original = base64.b64decode(state['original_base64'], validate=True)
    if digest(original) != state['original_sha256']:
        raise RuntimeError('Backup checksum mismatch; configuration preserved.')
    if ((state['existed'] and profile.exists() and profile.read_bytes() == original) or
            (not state['existed'] and not profile.exists())):
        print('READBACK_RESTORE_RESULT=PASS: already restored.')
        return
    if not profile.exists() or digest(profile.read_bytes()) != state['installed_sha256']:
        raise RuntimeError('Per-game settings changed after this test; preserved for review.')
    if state['existed']:
        atomic_write(profile, original, state['original_mode'])
    else:
        profile.unlink()
    print('READBACK_RESTORE_RESULT=PASS: original per-game configuration restored exactly.')


def finish_validation(home, record):
    no_running_core()
    state = json.loads(record.read_text())
    if (state.get('purpose') != 'validation' or state['revision'] != REVISION or
            state['profile'] != str(profile_path(home))):
        raise RuntimeError('This is not a validation session for the selected installation.')
    collector = record.parent / 'collect_graphics.py'
    if digest(collector.read_bytes()) != COLLECTOR_SHA256:
        raise RuntimeError('Collector checksum mismatch; use the printed RESTORE command.')
    try:
        result = subprocess.run([sys.executable, str(collector)], check=False)
    finally:
        restore(home, record)
    if result.returncode:
        raise RuntimeError('Log collection failed; pre-test configuration was restored.')
    print('VALIDATION_FINISH_RESULT=PASS: upload the GRAPHICS_REPORT file printed above.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--restore', type=Path)
    mode.add_argument('--validation', action='store_true')
    mode.add_argument('--finish-validation', type=Path)
    parser.add_argument('--collector', type=Path)
    args = parser.parse_args()
    try:
        if os.geteuid() == 0:
            raise RuntimeError('Run as your normal user, without sudo.')
        if args.restore:
            restore(Path.home(), args.restore)
        elif args.finish_validation:
            finish_validation(Path.home(), args.finish_validation)
        else:
            apply(Path.home(), args.validation, args.collector)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print('READBACK_TEST_RESULT=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
