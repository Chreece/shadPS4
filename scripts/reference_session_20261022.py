#!/usr/bin/env python3
"""Continue the preserved reference binary without shader dumps or profile resets.

This changes the diagnostic launcher only. It retains a separate writable test
profile, does not promote the old emulator, and never copies saves back to the
normal installation. Default duration 0 means manual exit, not a timed kill.
"""
from __future__ import annotations
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import types

REFERENCE_REV = '3e96d6de1ee76e18a6572c62b2a85f808a02c57f'
REFERENCE_FILE = 'scripts/reference_replay_20261022.py'
REFERENCE_SHA = '76cf0f9e020e942d534569d198c90f85669c51ce8fcf4a7b9aa5f9d4cf7fc008'
SEED_RUN = 'shadps4-reference-replay-20261022-174621-779f3c'
SESSION_NAME = 'got-reference-progress'
EXCLUDED = {'log', 'logs', 'shader', 'cache', 'screenshots', 'captures', 'temp', 'custom_configs'}


def once(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError('LAUNCHER_ANCHOR_CHANGED=' + before[:100])
    return text.replace(before, after, 1)


def check_profile(path):
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError('PROFILE_DIRECTORY_MISSING_OR_UNSAFE=' + str(path))
    for item in path.rglob('*'):
        mode = item.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise RuntimeError('PROFILE_SPECIAL_FILE_REJECTED=' + str(item))


def persistent_profile(root, seed, config, revision, binary_sha):
    parent = root / 'test-profiles'
    parent.mkdir(exist_ok=True)
    if parent.is_symlink() or not parent.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('TEST_PROFILE_PARENT_UNSAFE')
    session = parent / SESSION_NAME
    identity = {'schema': 1, 'seed_run': SEED_RUN, 'reference_revision': revision,
                'reference_binary_sha256': binary_sha, 'normal_profile_modified': False}
    created = False
    if session.exists() or session.is_symlink():
        marker = session / 'session.json'
        if session.is_symlink() or not session.is_dir() or marker.is_symlink() or not marker.is_file():
            raise RuntimeError('SESSION_DESTINATION_OCCUPIED')
        if json.loads(marker.read_bytes()) != identity:
            raise RuntimeError('SESSION_IDENTITY_CHANGED')
        check_profile(session / 'user')
    else:
        if seed.is_symlink() or not seed.is_dir():
            raise RuntimeError('RETAINED_PROFILE_MISSING=' + str(seed))
        if not seed.resolve().is_relative_to((root / 'logs' / SEED_RUN).resolve()):
            raise RuntimeError('SEED_OUTSIDE_RETAINED_RUN')
        temporary = Path(tempfile.mkdtemp(prefix='.reference-progress-', dir=parent))
        try:
            # Preserve links as links, then refuse them: never follow a save/profile
            # link into the normal user directory or copy from an unknown location.
            shutil.copytree(seed, temporary / 'user', symlinks=True,
                ignore=lambda directory, names: EXCLUDED.intersection(names)
                    if Path(directory) == seed else set())
            check_profile(temporary / 'user')
            (temporary / 'user/config.json').write_text(json.dumps(config, indent=2) + '\n')
            (temporary / 'session.json').write_text(json.dumps(identity, indent=2) + '\n')
            if session.exists() or session.is_symlink():
                raise RuntimeError('SESSION_APPEARED_DURING_COPY')
            os.rename(temporary, session)
            created = True
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    profile = session / 'user'
    conf = profile / 'config.json'
    if conf.is_symlink() or not conf.is_file():
        raise RuntimeError('SESSION_CONFIG_MISSING')
    active = json.loads(conf.read_bytes())
    if not isinstance(active, dict) or not isinstance(active.get('GPU'), dict):
        raise RuntimeError('SESSION_CONFIG_INVALID')
    if active.get('General', {}).get('home_dir', '') != '':
        raise RuntimeError('EXTERNAL_HOME_PATH_NOT_ALLOWED_IN_ISOLATED_SESSION')
    # Do not reset any saved game or unrelated settings on subsequent runs.
    active['GPU']['dump_shaders'] = False
    if active['GPU'].get('readbacks_mode') != 2 or active['GPU'].get('patch_shaders') is not False:
        raise RuntimeError('SESSION_RENDER_POLICY_CHANGED')
    conf.write_text(json.dumps(active, indent=2) + '\n')
    return profile, created


def inventory(profile):
    """Hash persistent files without including logs/caches or sharing save contents."""
    result = []
    for path in sorted(profile.rglob('*')):
        relative = path.relative_to(profile)
        if relative.parts[0] in EXCLUDED or relative == Path('config.json'):
            continue
        if path.is_symlink():
            raise RuntimeError('SESSION_SYMLINK_FOUND=' + str(relative))
        if path.is_file():
            with path.open('rb') as stream:
                sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            result.append({'path': str(relative), 'bytes': path.stat().st_size, 'sha256': sha})
    return result


def runtime_text(text):
    text = once(text, 'cwd=self.run / "profile", env=env',
                'cwd=Path(self.meta["PROFILE_PATH"]).parent, env=env')
    text = once(text, 'if elapsed >= self.args.seconds:',
                'if self.args.seconds and elapsed >= self.args.seconds:')
    text = once(text, 'if not 40 <= args.seconds <= 900:',
                'if args.seconds != 0 and not 40 <= args.seconds <= 900:')
    text = once(text, 'parser.error("--seconds must be between 40 and 900")',
                'parser.error("--seconds must be 0 (manual exit) or between 40 and 900")')
    text = once(text,
        'self.say("Use your controller to choose Continue and play the affected scene. No buttons or keys are sent automatically.")',
        'self.say("Complete brightness/setup and the opening scene normally. Exit the emulator normally after a save/checkpoint. Test-profile progress is retained; no automatic input.")')
    text = once(text,
        'self.say(f"RUN_DURATION={self.args.seconds}s SCREENSHOTS=every_20s_max15")',
        'self.say("RUN_DURATION=" + (str(self.args.seconds) + "s" if self.args.seconds else "UNTIL_NORMAL_EXIT") + " SCREENSHOTS=every_20s_max15")')
    text = once(text,
        'self.say(f"GAME_RUNNING={elapsed}s/{self.args.seconds}s FREE_VRAM={free / GIB:.2f}GiB GPU_BUSY={busy}%")',
        'self.say(f"GAME_RUNNING={elapsed}s FREE_VRAM={free / GIB:.2f}GiB GPU_BUSY={busy}%")')
    ast.parse(text)
    return text


def install_hooks(reference):
    prepare = reference.prepare_text
    configure = reference.configure_runtime

    def prepare_session(text):
        return runtime_text(prepare(text))

    def configure_session(runtime, root, candidate, records, features, config, saves, record_path):
        wanted = copy.deepcopy(config)
        wanted['GPU']['dump_shaders'] = False
        seed = root / 'logs' / SEED_RUN / 'profile/user'
        record = json.loads(record_path.read_bytes())
        record.update(purpose='reference_first_run_progress_not_emulator_fix',
            shader_dumping=False, seed_profile=str(seed),
            persistent_profile=str(root / 'test-profiles' / SESSION_NAME / 'user'),
            save_policy='seed once, then reuse writable isolated test profile; never copy back',
            duration_policy='manual exit by default; fault/display/VRAM guards retained',
            differences_from_historical_run=['indirect_snapshot_disabled',
                'persistent_profile_seeded_from_174621_replay'],
            expected_result='FIRST_RUN_SETUP_THEN_INTRO_PER_USER; NOT_ASSUMED_MAIN_MENU')
        record_path.write_text(json.dumps(record, indent=2) + '\n')
        configure(runtime, root, candidate, records, features, wanted, saves, record_path)
        Parent = runtime.Runtime

        class SessionRuntime(Parent):
            def clone_profile(self):
                profile, created = persistent_profile(root, seed, wanted,
                                                      reference.OLD_REV, reference.OLD_SHA)
                self.session_profile = profile
                (self.run / 'info/reference-replay.json').write_bytes(record_path.read_bytes())
                (self.run / 'info/reference-config.json').write_bytes((profile / 'config.json').read_bytes())
                for name, data in records.items():
                    (self.run / 'info' / ('historical-' + Path(name).name)).write_bytes(data)
                (self.run / 'info/session-files-before.json').write_text(
                    json.dumps(inventory(profile), indent=2) + '\n')
                self.meta.update(PROFILE_PATH=str(profile),
                    PROFILE_POLICY='PERSISTENT_ISOLATED_REFERENCE_SESSION',
                    SESSION_SEEDED_NOW='YES' if created else 'NO_REUSED',
                    SESSION_SEED=str(seed), REFERENCE_ONLY='YES_NOT_A_PRODUCTION_FIX',
                    REBUILD='NO', READBACK_POLICY='PRECISE_HISTORICAL_REFERENCE',
                    COMPILER_DIAGNOSTICS='DISABLED_NO_NEW_SHADER_DUMPS',
                    EXPENSIVE_DIAGNOSTICS='OFF_NO_INDIRECT_GPU_SNAPSHOT',
                    EXPERIMENTAL_RENDER_FLAGS='RECORDED_HISTORICAL_IMPLEMENTATION_FLAGS',
                    CAPTURED_SPIRV_VALIDATION='NOT_RUN_NO_NEW_SHADER_DUMPS',
                    ORIGINAL_SAVES='NORMAL_PROFILE_UNTOUCHED_TEST_PROFILE_PERSISTS',
                    RUN_LIMIT='MANUAL_EXIT' if self.args.seconds == 0 else str(self.args.seconds))
                self.say('PERSISTENT_TEST_PROFILE=' + str(profile))
                self.say('SHADER_DUMPING=OFF; SAVES_PERSIST=YES; ORIGINAL_SAVES=UNTOUCHED')
                self.check_stop()

            def monitor_vram(self, elapsed, free):
                # The inherited guard checks a new empty capture directory; shader
                # dumping is off. Keep display and GPU-memory guards unchanged.
                super().monitor_vram(elapsed, free)
                if elapsed >= getattr(self, 'session_disk_check_due', 0):
                    self.session_disk_check_due = elapsed + 10
                    if shutil.disk_usage(root).free < 2 * 1024**3:
                        self.meta['STOP_REASON'] = 'DISK_FREE_BELOW_2_GIB'
                        raise RuntimeError('LOW_DISK_SPACE_STOP_OWNED_TEST')

            def collect_indirect_snapshot(self):
                # This name belongs to the inherited collector. No new shader
                # capture is requested: do not report 0/0 as validation PASS.
                self.meta['INDIRECT_ARGUMENT_CAPTURE'] = 'DISABLED_NO_ENVIRONMENT_TRIGGER'
                self.meta['COMPILER_ARTIFACTS'] = 'NOT_COLLECTED_DUMPING_DISABLED'
                self.meta['CAPTURED_SPIRV_VALIDATION'] = 'NOT_RUN_NO_NEW_SHADER_DUMPS'
                self.meta['REFERENCE_BINARY_UNCHANGED'] = (
                    'PASS' if reference.digest(candidate) == reference.OLD_SHA else 'FAIL')
                self.meta['READBACK_MODE_VERIFIED'] = 'PASS' if self.log.precise.is_set() else 'NOT_CONFIRMED'
                if hasattr(self, 'session_profile'):
                    (self.run / 'info/session-files-after.json').write_text(
                        json.dumps(inventory(self.session_profile), indent=2) + '\n')
                    self.meta['SESSION_RETAINED_AT'] = str(self.session_profile)
                    self.say('NEXT_RUN_USES_SAME_TEST_PROFILE=' + str(self.session_profile))

        runtime.Runtime = SessionRuntime

    reference.prepare_text = prepare_session
    reference.configure_runtime = configure_session


def load_reference(raw):
    if hashlib.sha256(raw).hexdigest() != REFERENCE_SHA:
        raise RuntimeError('PINNED_REFERENCE_LAUNCHER_CHANGED')
    text = raw.decode()
    text = once(text, "parser.add_argument('--seconds', type=int, default=180)",
                "parser.add_argument('--seconds', type=int, default=0)")
    text = once(text, 'if not 40 <= args.seconds <= 300:',
                'if args.seconds != 0 and not 40 <= args.seconds <= 900:')
    text = once(text, "parser.error('seconds must be 40..300')",
                "parser.error('seconds must be 0 (manual exit) or 40..900')")
    module = types.ModuleType('reference_session_parent')
    module.__file__ = 'pinned_reference_replay.py'
    sys.modules[module.__name__] = module
    exec(compile(text, module.__file__, 'exec'), module.__dict__)
    install_hooks(module)
    return module


def main():
    root = Path.home() / 'projects/shadps4'
    raw = subprocess.check_output(['git', '-C', str(root / 'clean-rebase-verified/source'),
        'show', REFERENCE_REV + ':' + REFERENCE_FILE], stderr=subprocess.PIPE, timeout=30)
    return load_reference(raw).main()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print('REFERENCE_SESSION_FAILED=' + str(exc), file=sys.stderr)
        logs = Path.home() / 'projects/shadps4/logs'
        logs.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='reference-session-preflight-', dir=logs))
        (folder / 'failure.txt').write_text(type(exc).__name__ + ': ' + str(exc) + '\n')
        archive = folder.with_suffix('.tar.gz')
        with tarfile.open(archive, 'w:gz') as packed:
            packed.add(folder, arcname='launch')
        print('UPLOAD_THIS=' + str(archive), flush=True)
        raise SystemExit(2)
