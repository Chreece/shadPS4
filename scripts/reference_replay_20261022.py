#!/usr/bin/env python3
"""Capture a previously rendering binary as a regression reference, never promote it.

This is NOT an emulator correction or a single-variable source bisection. It reuses
historical implementation flags and the current precise-readback profile, disables
the old indirect snapshot, and saves CPU-side shader output for comparison.
"""
from __future__ import annotations
import argparse
import ast
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import types

LAUNCH_REV = 'e5db2b9967e4a69e76ff3fb7e8dcf1139ae058b3'
LAUNCH_FILE = 'scripts/run_verified_copy_candidate_20261022.py'
LAUNCH_SHA = '6dffa58152ee047a52ed63e648b5b90a094de9a8d08101c094393d382b0ac63c'
RUNTIME_SHA = 'b688234b4dfbd690399f0fec2f4c55351379cfd71d6cebb4484318e83175510f'
OLD_REV = '94ed29b7c5767718ead87164558d6ec7523bcc9a'
OLD_SHA = 'f807d65f2a8c08efd8f66d4fdad0b4e7cdb5d69183c1fb945e62b68b0b6929c8'
OLD_ARCHIVE = 'got-world-graphics-runtime-20261021-090301-7e1dde.tar.gz'
OLD_ARCHIVE_SHA = 'd0961580270fc5a5910a312655e3526f8542a78bfd29c3bbc3756e881b03ef23'
CONTROL = 'shadps4-readback-control-runtime-20261022-172533-da18e6'
CONTROL_CONFIG_SHA = '7201dfea4b024053e8c48ea8d00e91b4514f68632a6d092994d76b8f6b215d1b'
OLD_CANDIDATE = ('GhostOfTsushima/tests/got-upstream-menu-main-20261017-cached/'
                 'bin-backups/shadps4-got-indexed-sample-feedback-' + OLD_REV)
EXPECTED_FEATURES = {'SHADPS4_STRIDED_GUEST_TSHARP': '1',
                     'SHADPS4_GUEST_TSHARP_LARGE_ARRAY': '1',
                     'SHADPS4_TEXTURE_SLOT_PRESSURE': '1',
                     'SHADPS4_RESOURCE_BINDING_DIAGNOSTICS': '1'}


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('EXPECTED_REGULAR_FILE=' + str(path))
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def archive_members(path, names):
    result = {}
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            if member.name not in names:
                continue
            if member.name in result or not member.isfile() or member.size > 1024*1024:
                raise RuntimeError('UNSAFE_OR_DUPLICATE_REFERENCE_MEMBER=' + member.name)
            result[member.name] = archive.extractfile(member).read()
    if set(result) != set(names):
        raise RuntimeError('REFERENCE_ARCHIVE_MEMBERS_MISSING')
    return result


def references(root):
    archive = root / 'logs' / OLD_ARCHIVE
    if digest(archive) != OLD_ARCHIVE_SHA:
        raise RuntimeError('HISTORICAL_CAPTURE_CHECKSUM_MISMATCH')
    records = archive_members(archive, {'info/final.txt', 'info/host-provenance.json',
                                       'info/render-environment.json'})
    final = dict(x.split('=', 1) for x in records['info/final.txt'].decode().splitlines() if '=' in x)
    host = json.loads(records['info/host-provenance.json'])
    candidate = root / OLD_CANDIDATE
    if (final.get('SOURCE_REVISION') != OLD_REV or final.get('CANDIDATE_SHA256') != OLD_SHA
            or host.get('sha256') != OLD_SHA or Path(host['candidate']) != candidate
            or host.get('host_final', {}).get('STATUS') != 'HOST_ONLY_BUILD_PASS'
            or host.get('host_start', {}).get('PINNED_CANDIDATE') != OLD_REV):
        raise RuntimeError('HISTORICAL_BINARY_PROVENANCE_MISMATCH')
    if digest(candidate) != OLD_SHA or not os.access(candidate, os.X_OK):
        raise RuntimeError('HISTORICAL_BINARY_MISSING_OR_CHANGED')
    env = json.loads(records['info/render-environment.json'])
    if not isinstance(env, dict) or env.get('SHADPS4_INDIRECT_ARGUMENT_SNAPSHOT_SECONDS') != '60':
        raise RuntimeError('HISTORICAL_ENVIRONMENT_MISMATCH')
    # Never set the snapshot time to zero: zero means capture immediately.
    del env['SHADPS4_INDIRECT_ARGUMENT_SNAPSHOT_SECONDS']
    if {k: v for k, v in env.items() if v != '0'} != EXPECTED_FEATURES:
        raise RuntimeError('UNREVIEWED_HISTORICAL_FEATURE_SWITCH')
    if any(not k.startswith('SHADPS4_') or v not in ('0', '1') for k, v in env.items()):
        raise RuntimeError('UNSAFE_HISTORICAL_ENVIRONMENT')
    control = root / 'logs' / CONTROL
    config_path = control / 'info/readback-config-requested.json'
    if digest(config_path) != CONTROL_CONFIG_SHA:
        raise RuntimeError('PRECISE_CONTROL_PROFILE_CHANGED')
    config = json.loads(config_path.read_bytes())
    if config.get('GPU', {}).get('readbacks_mode') != 2:
        raise RuntimeError('PRECISE_PROFILE_REQUIRED')
    saves = control / 'profile/user'
    if saves.is_symlink() or not saves.is_dir():
        raise RuntimeError('CONTROL_SAVE_COPY_MISSING')
    return candidate, records, env, config, saves


def prepare_text(text):
    if hashlib.sha256(text.encode()).hexdigest() != RUNTIME_SHA:
        raise RuntimeError('RUNTIME_VERSION_MISMATCH')
    pairs = {
        'f"shadps4-masked-wave-runtime-{stamp}-{uuid.uuid4().hex[:6]}"':
            'f"shadps4-reference-replay-{stamp}-{uuid.uuid4().hex[:6]}"',
        '        self.meta["READBACK_POLICY"] = "COPIED_GLOBAL_SETTING_NOT_FORCED"':
            '        self.meta["READBACK_POLICY"] = "PRECISE_HISTORICAL_REFERENCE"',
        '"NORMAL_RENDERER_NO_DRIVER_OVERRIDES"': '"HISTORICAL_REFERENCE_NOT_PRODUCTION_FIX"',
    }
    for old, new in pairs.items():
        if text.count(old) != 1:
            raise RuntimeError('RUNTIME_ANCHOR_CHANGED=' + old)
        text = text.replace(old, new, 1)
    ast.parse(text)
    return text


def configure_runtime(runtime, root, candidate, records, features, config, saves, record_path):
    Original = runtime.Runtime
    record_hash = digest(record_path)

    def verify_reference(report, binary, revision):
        if (report != record_path or digest(report) != record_hash or binary != candidate
                or revision != OLD_REV or digest(binary) != OLD_SHA):
            raise RuntimeError('REFERENCE_LAUNCH_IDENTITY_MISMATCH')
        return OLD_SHA, {'STATUS': 'HISTORICAL_NATIVE_BUILD_VERIFIED_FROM_CAPTURE'}, {
            'REVISION': OLD_REV, 'REFERENCE_ARCHIVE_SHA256': OLD_ARCHIVE_SHA,
            'NEW_BUILD_PERFORMED': False}

    class ReferenceRuntime(Original):
        def clone_profile(self):
            permanent = self.original_profile
            try:
                self.original_profile = saves
                super().clone_profile()
            finally:
                self.original_profile = permanent
            path = self.run / 'profile/user/config.json'
            if path.is_symlink() or not path.is_file():
                raise RuntimeError('DISPOSABLE_PROFILE_MISSING')
            # Exact requested config from the verified Precise control. It already
            # has shader dumping on and shader replacement/driver diagnostics off.
            path.write_text(json.dumps(config, indent=2) + '\n')
            if json.loads(path.read_text()) != config:
                raise RuntimeError('REFERENCE_CONFIG_MISMATCH')
            (self.run / 'info/reference-replay.json').write_bytes(record_path.read_bytes())
            (self.run / 'info/reference-config.json').write_bytes(path.read_bytes())
            for name, data in records.items():
                (self.run / 'info' / ('historical-' + Path(name).name)).write_bytes(data)
            self.meta.update(REFERENCE_ONLY='YES_NOT_A_PRODUCTION_FIX',
                             REBUILD='NO', EXPERIMENTAL_RENDER_FLAGS='RECORDED_HISTORICAL_IMPLEMENTATION_FLAGS',
                             EXPENSIVE_DIAGNOSTICS='CPU_SHADER_DUMPS;NO_INDIRECT_GPU_SNAPSHOT',
                             READBACK_POLICY='PRECISE_HISTORICAL_REFERENCE',
                             INDIRECT_ARGUMENT_CAPTURE='EXPLICITLY_DISABLED_NO_ENVIRONMENT_TRIGGER')

        def environment(self):
            env = super().environment()
            # Same GPU driver selection as the preceding controls; no RADV or
            # validation-layer flags. These switches belong only to the old binary.
            env.update(features)
            env.pop('SHADPS4_INDIRECT_ARGUMENT_SNAPSHOT_SECONDS', None)
            (self.run / 'info/render-environment.json').write_text(json.dumps({
                k: v for k, v in env.items() if k.startswith(('SHADPS4_', 'VK_', 'RADV_'))
            }, indent=2) + '\n')
            return env

        def monitor_vram(self, elapsed, free):
            super().monitor_vram(elapsed, free)
            if elapsed >= 20 and not self.log.precise.is_set():
                raise RuntimeError('PRECISE_MODE_NOT_CONFIRMED')

        def collect_indirect_snapshot(self):
            # The inherited routine collects compiler artifacts/validates them;
            # its misleading old name does not mean it performs GPU readbacks.
            try:
                return super().collect_indirect_snapshot()
            finally:
                self.meta['INDIRECT_ARGUMENT_CAPTURE'] = 'DISABLED_NO_ENVIRONMENT_TRIGGER'
                self.meta['REFERENCE_BINARY_UNCHANGED'] = 'PASS' if digest(candidate) == OLD_SHA else 'FAIL'
                self.meta['READBACK_MODE_VERIFIED'] = 'PASS' if self.log.precise.is_set() else 'NOT_CONFIRMED'

    runtime.verify_host = verify_reference
    runtime.Runtime = ReferenceRuntime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    if not 40 <= args.seconds <= 300:
        parser.error('seconds must be 40..300')
    root = Path.home() / 'projects/shadps4'
    folder = Path(tempfile.mkdtemp(prefix='reference-replay-launch-', dir=root / 'logs'))
    try:
        with (root / 'clean-rebase-verified/.build.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            candidate, records, features, config, saves = references(root)
            raw = subprocess.check_output(['git', '-C', str(root / 'clean-rebase-verified/source'),
                 'show', LAUNCH_REV + ':' + LAUNCH_FILE], stderr=subprocess.PIPE, timeout=30)
            if hashlib.sha256(raw).hexdigest() != LAUNCH_SHA:
                raise RuntimeError('LAUNCHER_VERSION_MISMATCH')
            launcher = types.ModuleType('verified_reference_launcher')
            launcher.__file__ = str(folder / 'pinned_launcher.py')
            exec(compile(raw, launcher.__file__, 'exec'), launcher.__dict__)
            info, source, current, corrected = launcher.verified_report(root)
            text, base = launcher.prepare_runtime(root, source)
            text = prepare_text(text)
            runtime_path = folder / 'reference_runtime.py'
            runtime_path.write_text(text)
            runtime = launcher.load_module(runtime_path, 'historical_reference_runtime')
            launch = folder / 'reference-provenance.json'
            launch.write_text(json.dumps({
                'purpose': 'recover_shader_reference_not_promote_legacy_build',
                'reference_archive': str(root / 'logs' / OLD_ARCHIVE),
                'reference_archive_sha256': OLD_ARCHIVE_SHA,
                'candidate': str(candidate), 'revision': OLD_REV, 'sha256': OLD_SHA,
                'current_candidate_preserved': str(current), 'precise_control': CONTROL,
                'config_sha256': CONTROL_CONFIG_SHA, 'legacy_feature_flags': features,
                'indirect_snapshot': 'DISABLED_BY_ABSENT_VARIABLE',
                'differences_from_historical_run': ['shader_dumping_enabled',
                    'indirect_snapshot_disabled', 'current_precise_control_profile_and_save_copy'],
                'interpretation': 'NOT_SINGLE_VARIABLE_CAUSAL_PROOF',
                'expected_result': 'UNVERIFIED_MENU_OR_WORLD_NOT_ASSUMED',
            }, indent=2) + '\n')
            configure_runtime(runtime, root, candidate, records, features, config, saves, launch)
            runtime.verify_host(launch, candidate, OLD_REV)
            print('HISTORICAL_BINARY_IDENTITY=PASS; CURRENT_BINARY=UNCHANGED', flush=True)
            print('REFERENCE_ONLY=YES; REBUILD=NO; GPU_SNAPSHOT=DISABLED', flush=True)
            if args.check_only:
                print('REFERENCE_PREFLIGHT=PASS; GAME_LAUNCHED=NO', flush=True)
                return 0
            sys.argv = [str(runtime_path), '--candidate', str(candidate), '--source-revision', OLD_REV,
                        '--host-report', str(launch), '--baseline-binary', str(current),
                        '--baseline-profile', str(Path.home() / '.local/share/shadPS4'),
                        '--game', '/mnt/roms-all/ps4-games/CUSA11456/eboot.bin',
                        '--seconds', str(args.seconds)]
            return runtime.main()
    except (Exception, KeyboardInterrupt) as error:
        (folder / 'failure.txt').write_text(type(error).__name__ + ': ' + str(error) + '\n')
        archive = folder.with_suffix('.tar.gz')
        with tarfile.open(archive, 'w:gz') as out:
            out.add(folder, arcname='launch')
        print('REFERENCE_REPLAY_FAILED=' + str(error), file=sys.stderr, flush=True)
        print('UPLOAD_THIS=' + str(archive), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
