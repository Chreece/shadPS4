#!/usr/bin/env python3
"""Protected runtime acceptance test for the exact host-built automatic-coherence candidate.

No rebuild, source modification, promotion, per-title rendering flags or shader dumps.
The reference profile is copied once to a separate persistent test session. The old
readback option is deliberately left Disabled in that copy: the executable, not
this launcher, must report automatic demand coherence. GPU/world recovery is unproven.
"""
from __future__ import annotations
import argparse
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import types

BUILD = 'masked-wave-build-20261022-203341-4161366'
REV = 'c938fd009e936dd4f59bf673890dda47d18ac409'
PARENT = '27e3a68a6a96f2aa6bab42df50a32f2cbf0f2d77'
TREE = '0c443ce3ddb300f4c33a8142b43ac140c2e71548'
PARENT_TREE = '40ad46ae118d3d6c2112eed0d821d0b4cd0d146e'
BASE = '04d891dc33bb1c8c89789b5ff039049901f95a41'
BINARY_SHA = 'e74db20f66748fb6040d36d2bae91a83017ba5931f0bf544f26c1c82a3913e25'
REPORT_SHA = '1bf961c393f7f288064466b5504fdf93bdccf391575830a7530a269d6be56aa9'
MANIFEST_SHA = '536b8428f174d0e821d90f5ae94e75017f103238da2626dd52fdb4f763c476a4'
SIRIT_OLD = 'fc2b42d5e57ee2f638676c0c02f7fd71934b596a'
SIRIT_NEW = '471ebbd5f08047a9ece7f5f3766c848c37c8b580'
LAUNCH_REV = 'e5db2b9967e4a69e76ff3fb7e8dcf1139ae058b3'
LAUNCH_FILE = 'scripts/run_verified_copy_candidate_20261022.py'
LAUNCH_SHA = '6dffa58152ee047a52ed63e648b5b90a094de9a8d08101c094393d382b0ac63c'
PREFLIGHT_SHA = '0507a7a27945d50004ef8d7e88647af471d5166ffcf608925a15ca7670ead873'
RUNTIME_SHA = 'b688234b4dfbd690399f0fec2f4c55351379cfd71d6cebb4484318e83175510f'
REFERENCE_REV = '94ed29b7c5767718ead87164558d6ec7523bcc9a'
REFERENCE_SHA = 'f807d65f2a8c08efd8f66d4fdad0b4e7cdb5d69183c1fb945e62b68b0b6929c8'
REFERENCE_BINARY = ('GhostOfTsushima/tests/got-upstream-menu-main-20261017-cached/'
                    'bin-backups/shadps4-got-indexed-sample-feedback-' + REFERENCE_REV)
SEED_SESSION = 'got-reference-progress'
SESSION = 'got-automatic-coherence-progress'
EXCLUDED = {'log', 'logs', 'shader', 'cache', 'screenshots', 'captures', 'temp', 'custom_configs'}
CHANGED = {'src/core/emulator_settings.h', 'src/video_core/page_manager.cpp', 'src/emulator.cpp'}
AUTOMATIC_LINE = 'GPU readbacksMode: 2 (automatic demand coherence; legacy option ignored)'


def digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('EXPECTED_REGULAR_FILE=' + str(path))
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git(source: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if k not in
           ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR')}
    return subprocess.check_output(
        ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
         '-c', 'gc.auto=0', '-C', str(source), *args],
        env=env, stderr=subprocess.PIPE, timeout=30)


def verify_build(root: Path):
    source = root / 'clean-rebase-verified/source'
    info = root / 'logs' / BUILD / 'info'
    report_path = info / 'build-result.json'
    if digest(report_path) != REPORT_SHA or digest(info / 'correction.json') != MANIFEST_SHA:
        raise RuntimeError('UPLOADED_BUILD_EVIDENCE_CHANGED')
    report = json.loads(report_path.read_bytes())
    expected = dict(status='MASKED_WAVE_BUILD_PASS', base=BASE, revision=REV, tree=TREE,
        source_parent=PARENT, candidate_sha256=BINARY_SHA, manifest_sha256=MANIFEST_SHA,
        full_build='PASS', exit=0, game_started=False, source_restored=True,
        build_binary_restored=True, sirit_restored=True, sirit_candidate=SIRIT_NEW,
        sirit_original=SIRIT_OLD, automatic_coherence='EXISTING_PRECISE_DEMAND_PATH_MANDATORY',
        spirv_validation='PASS', fault_spirv_validation='PASS',
        float_atomic_spirv_validation='PASS_8_MODULES_NEGATIVE_CONTROL_REJECTED')
    if any(report.get(k) != value for k, value in expected.items()):
        raise RuntimeError('COHERENCE_BUILD_NOT_EXPECTED')
    candidate = root / f'clean-rebase-verified/candidates/{REV}-{BINARY_SHA}/shadps4'
    if Path(report['candidate']) != candidate or Path(report['source']) != source:
        raise RuntimeError('BUILD_PATH_MISMATCH')
    if digest(candidate) != BINARY_SHA or not os.access(candidate, os.X_OK):
        raise RuntimeError('BUILT_CANDIDATE_CHANGED')
    if (info / 'prepared-runtime.sha256').read_text().strip() != PREFLIGHT_SHA:
        raise RuntimeError('BUILD_RUNTIME_PREFLIGHT_CHANGED')
    for ref, wanted in {
        REV + '^{tree}': TREE, REV + '^': PARENT, PARENT + '^{tree}': PARENT_TREE,
        REV + ':externals/sirit': SIRIT_NEW, 'HEAD': BASE,
    }.items():
        if git(source, 'rev-parse', ref).decode().strip() != wanted:
            raise RuntimeError('CANDIDATE_SOURCE_PROVENANCE_MISMATCH=' + ref)
    if set(git(source, 'diff', '--name-only', PARENT, REV).decode().splitlines()) != CHANGED:
        raise RuntimeError('AUTOMATIC_COHERENCE_DELTA_CHANGED')
    manifest = json.loads((info / 'correction.json').read_bytes())
    if set(git(source, 'diff', '--name-only', BASE, REV).decode().splitlines()) != set(manifest['files']):
        raise RuntimeError('CANDIDATE_MANIFEST_FILE_SET_CHANGED')
    for name, entry in manifest['files'].items():
        if git(source, 'rev-parse', REV + ':' + name).decode().strip() != entry['corrected_blob']:
            raise RuntimeError('CANDIDATE_SOURCE_BLOB_CHANGED=' + name)
    git(source, 'diff', '--exit-code', '--no-ext-diff', 'HEAD', '--')
    sirit = source / 'externals/sirit'
    if git(sirit, 'rev-parse', 'HEAD').decode().strip() != SIRIT_OLD:
        raise RuntimeError('SIRIT_CHECKOUT_NOT_RESTORED')
    if git(sirit, 'status', '--porcelain', '--untracked-files=all').strip():
        raise RuntimeError('SIRIT_CHECKOUT_HAS_LOCAL_CHANGES')
    reference = root / REFERENCE_BINARY
    if digest(reference) != REFERENCE_SHA:
        raise RuntimeError('PRESERVED_RENDERING_REFERENCE_CHANGED')
    return source, info, candidate, reference


def inventory(profile: Path):
    """Hash copied state; never include save contents in the report."""
    if profile.is_symlink() or not profile.is_dir():
        raise RuntimeError('PROFILE_MISSING_OR_UNSAFE=' + str(profile))
    result = []
    for directory, dirs, files in os.walk(profile, followlinks=False):
        if Path(directory) == profile:
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
            files = [f for f in files if f not in EXCLUDED]
        for name in sorted(dirs + files):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise RuntimeError('PROFILE_SPECIAL_FILE=' + str(path))
            if stat.S_ISREG(mode):
                result.append(dict(path=str(path.relative_to(profile)), bytes=path.stat().st_size,
                                   sha256=digest(path)))
    return sorted(result, key=lambda item: item['path'])


def persistent_copy(root: Path):
    parent = root / 'test-profiles'
    seed_session = parent / SEED_SESSION
    seed = seed_session / 'user'
    if parent.is_symlink() or seed_session.is_symlink():
        raise RuntimeError('UNSAFE_REFERENCE_PROFILE_PARENT')
    marker = seed_session / 'session.json'
    digest(marker)
    identity = json.loads(marker.read_bytes())
    if identity.get('reference_revision') != REFERENCE_REV or identity.get('reference_binary_sha256') != REFERENCE_SHA:
        raise RuntimeError('REFERENCE_SESSION_IDENTITY_CHANGED')
    before = inventory(seed)
    seed_config = json.loads((seed / 'config.json').read_bytes())
    if seed_config.get('General', {}).get('home_dir', '') != '':
        raise RuntimeError('REFERENCE_PROFILE_POINTS_TO_EXTERNAL_SAVES')
    target = parent / SESSION
    expected = dict(schema=1, purpose='automatic_coherence_runtime_acceptance',
                    candidate_revision=REV, seed_session=SEED_SESSION,
                    seed_reference_revision=REFERENCE_REV, normal_saves_modified=False)
    created = False
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise RuntimeError('TEST_SESSION_PATH_OCCUPIED')
        digest(target / 'session.json')
        if json.loads((target / 'session.json').read_bytes()) != expected:
            raise RuntimeError('TEST_SESSION_IDENTITY_CHANGED')
        inventory(target / 'user')
    else:
        required = sum(item['bytes'] for item in before) + 2 * 1024**3
        if shutil.disk_usage(parent).free < required:
            raise RuntimeError('INSUFFICIENT_DISK_FOR_INDEPENDENT_SAVE_COPY')
        temp = Path(tempfile.mkdtemp(prefix='.coherence-session-', dir=parent))
        try:
            shutil.copytree(seed, temp / 'user', symlinks=True,
                ignore=lambda directory, names: EXCLUDED.intersection(names)
                    if Path(directory) == seed else set())
            if inventory(temp / 'user') != before or inventory(seed) != before:
                raise RuntimeError('REFERENCE_CHANGED_DURING_SAVE_COPY')
            (temp / 'session.json').write_text(json.dumps(expected, indent=2) + '\n')
            if target.exists() or target.is_symlink():
                raise RuntimeError('TEST_SESSION_APPEARED_DURING_COPY')
            os.rename(temp, target)
            created = True
        finally:
            if temp.exists():
                shutil.rmtree(temp)
    profile = target / 'user'
    digest(profile / 'config.json')
    active = json.loads((profile / 'config.json').read_bytes())
    if not isinstance(active, dict):
        raise RuntimeError('INVALID_TEST_CONFIGURATION')
    for section in ('GPU', 'General', 'Log'):
        if not isinstance(active.setdefault(section, {}), dict):
            raise RuntimeError('INVALID_CONFIGURATION_SECTION=' + section)
    if active['General'].get('home_dir', '') != '':
        raise RuntimeError('TEST_PROFILE_POINTS_TO_EXTERNAL_SAVES')
    overrides = profile / 'custom_configs'
    if overrides.is_symlink() or (overrides.exists() and any(overrides.iterdir())):
        raise RuntimeError('TITLE_OVERRIDES_NOT_ALLOWED_IN_THIS_TEST')
    # A deliberately stale option proves that automatic policy comes from the binary.
    # This affects only this independent test configuration, never the reference.
    active['GPU'].update(readbacks_mode=0, dump_shaders=False, patch_shaders=False)
    active['Log'].update(filter='*:warning Config:info', sync=False, skip_duplicate=True)
    config_path = profile / 'config.json'
    fd, name = tempfile.mkstemp(prefix='.coherence-config-', dir=profile)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(active, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, config_path)
    finally:
        Path(name).unlink(missing_ok=True)
    if inventory(seed) != before:
        raise RuntimeError('REFERENCE_PROFILE_CHANGED_DURING_PREPARATION')
    return profile, created, seed, before


def once(text: str, before: str, after: str) -> str:
    if text.count(before) != 1:
        raise RuntimeError('PINNED_RUNTIME_ANCHOR_CHANGED=' + before[:90])
    return text.replace(before, after, 1)


def adapt_runtime(text: str) -> str:
    if hashlib.sha256(text.encode()).hexdigest() != RUNTIME_SHA:
        raise RuntimeError('PREVIOUS_PROTECTED_RUNTIME_CHANGED')
    text = once(text, 'cwd=self.run / "profile", env=env',
                'cwd=Path(self.meta["PROFILE_PATH"]).parent, env=env')
    text = once(text, 'if elapsed >= self.args.seconds:',
                'if self.args.seconds and elapsed >= self.args.seconds:')
    text = once(text, 'if not 40 <= args.seconds <= 900:',
                'if args.seconds != 0 and not 40 <= args.seconds <= 900:')
    text = once(text, 'parser.error("--seconds must be between 40 and 900")',
                'parser.error("--seconds must be 0 or between 40 and 900")')
    text = once(text, 'f"shadps4-masked-wave-runtime-{stamp}-{uuid.uuid4().hex[:6]}"',
                'f"shadps4-automatic-coherence-runtime-{stamp}-{uuid.uuid4().hex[:6]}"')
    text = once(text, 'self.say(f"RUN_DURATION={self.args.seconds}s SCREENSHOTS=every_20s_max15")',
                'self.say("RUN_DURATION=" + (str(self.args.seconds) + "s" if self.args.seconds else "UNTIL_USER_STOP") + " SCREENSHOTS=every_20s_max15")')
    text = once(text, 'self.meta["READBACK_POLICY"] = "COPIED_GLOBAL_SETTING_NOT_FORCED"',
                'self.meta["READBACK_POLICY"] = "AUTOMATIC_IN_EMULATOR_LEGACY_OPTION_DISABLED_IN_TEST_COPY"')
    ast.parse(text)
    return text


def configure_runtime(module, root: Path, info: Path):
    Original = module.Runtime

    class CoherenceRuntime(Original):
        def clone_profile(self):
            profile, created, self.seed, self.seed_inventory = persistent_copy(root)
            self.session_profile = profile
            (self.run / 'info/requested-config.json').write_bytes((profile / 'config.json').read_bytes())
            (self.run / 'info/reference-files-before.json').write_text(json.dumps(self.seed_inventory, indent=2) + '\n')
            (self.run / 'info/session-files-before.json').write_text(json.dumps(inventory(profile), indent=2) + '\n')
            self.meta.update(PROFILE_PATH=str(profile), PROFILE_POLICY='PERSISTENT_INDEPENDENT_COPY_OF_REFERENCE',
                SESSION_SEEDED_NOW='YES' if created else 'NO_REUSED', REBUILD='NO',
                READBACK_POLICY='AUTOMATIC_IN_EMULATOR_LEGACY_OPTION_DISABLED_IN_TEST_COPY',
                LEGACY_CONFIG_READBACK_MODE='0', EXPERIMENTAL_RENDER_FLAGS='NONE',
                COMPILER_DIAGNOSTICS='OFF_NO_SHADER_DUMPS', EXPENSIVE_DIAGNOSTICS='OFF',
                ORIGINAL_SAVES='NORMAL_AND_REFERENCE_PROFILES_UNTOUCHED',
                FULLSCREEN='REQUESTED_TRUE', FPS='REQUESTED_TRUE',
                RUN_LIMIT='MANUAL_STOP' if self.args.seconds == 0 else str(self.args.seconds))
            self.say('TEST_PROFILE=' + str(profile))
            self.say('FULLSCREEN=ON; FPS=ON; REFERENCE_SAVES=UNTOUCHED; SHADER_DUMPS=OFF')
            self.say('Press Ctrl+C ONCE here to stop; keep SSH open for cleanup and UPLOAD_THIS.')
            self.check_stop()

        def environment(self):
            env = super().environment()
            # Never re-enable the experimental flags that belonged only to the old build.
            for key in list(env):
                if key.startswith(('SHADPS4_', 'RADV_', 'VK_')):
                    env.pop(key)
            (self.run / 'info/render-environment.json').write_text('{}\n')
            return env

        def monitor_vram(self, elapsed, free):
            super().monitor_vram(elapsed, free)
            if elapsed >= 20:
                head = module.ANSI.sub('', bytes(self.log.head).decode(errors='replace'))
                if AUTOMATIC_LINE not in head or not self.log.precise.is_set():
                    raise RuntimeError('AUTOMATIC_COHERENCE_NOT_CONFIRMED_BY_EMULATOR')
                self.meta['AUTOMATIC_COHERENCE_VERIFIED'] = 'PASS_LOGGED_WITH_LEGACY_CONFIG_ZERO'
            if elapsed >= getattr(self, 'disk_check_due', 0):
                self.disk_check_due = elapsed + 10
                if shutil.disk_usage(root).free < 2 * 1024**3:
                    raise RuntimeError('LOW_DISK_SPACE_STOP_OWNED_TEST')

        def collect_indirect_snapshot(self):
            # Preserve bounded evidence, but no new dumps or misleading 0/0 validation PASS.
            self.meta.update(INDIRECT_ARGUMENT_CAPTURE='DISABLED',
                COMPILER_ARTIFACTS='NOT_COLLECTED_DUMPING_DISABLED',
                CAPTURED_SPIRV_VALIDATION='NOT_RUN_NO_NEW_SHADER_DUMPS')
            for name in ('build-result.json', 'correction.json', 'automatic-coherence.patch',
                         'native-tests.log', 'coherence-native-tests.log', 'coherence-policy-tests.log'):
                path = info / name
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024**2:
                    raise RuntimeError('BUILD_EVIDENCE_UNSAFE=' + name)
                shutil.copyfile(path, self.run / 'info' / name)
            intact = not hasattr(self, 'seed_inventory') or inventory(self.seed) == self.seed_inventory
            self.meta['REFERENCE_PROFILE_UNCHANGED'] = 'PASS' if intact else 'FAIL'
            same_binary = digest(self.args.candidate) == BINARY_SHA
            self.meta['TEST_BINARY_UNCHANGED'] = 'PASS' if same_binary else 'FAIL'
            if hasattr(self, 'session_profile'):
                (self.run / 'info/session-files-after.json').write_text(json.dumps(inventory(self.session_profile), indent=2) + '\n')
                self.meta['SESSION_RETAINED_AT'] = str(self.session_profile)
            text = module.ANSI.sub('', (self.run / 'game.log').read_text(errors='replace'))
            modes = re.findall(r'GPU readbacksMode:\s*(\d+)\b', text)
            observed = AUTOMATIC_LINE in text and bool(modes) and set(modes) == {'2'}
            self.meta['AUTOMATIC_COHERENCE_VERIFIED'] = 'PASS_LOGGED_WITH_LEGACY_CONFIG_ZERO' if observed else 'NOT_CONFIRMED'
            self.meta['READBACK_MODE_OBSERVED'] = ','.join(modes) or 'NOT_OBSERVED'
            self.meta['MEMORY_TRACKING_SIGNALS'] = 'LOGGED' if 'Memory tracking method: signals' in text else 'NOT_OBSERVED'
            (self.run / 'info/coherence-check.json').write_text(json.dumps(dict(
                candidate=REV, binary_sha256=BINARY_SHA, legacy_config_readbacks_mode=0,
                effective_readback_modes=modes, automatic_policy_logged=observed,
                reference_profile_unchanged=intact, candidate_unchanged=same_binary,
                visual_result='REQUIRES_SCREENSHOTS_AND_USER_REVIEW',
                indexed_texture_port_included=False, all_games_validated=False), indent=2) + '\n')
            if not intact or not same_binary:
                raise RuntimeError('POST_RUN_INTEGRITY_FAILURE')
            if self.meta['GAME_LAUNCHED'] == 'YES' and not observed:
                raise RuntimeError('AUTOMATIC_COHERENCE_NOT_VERIFIED')

    module.Runtime = CoherenceRuntime


def load_protected_runtime(root: Path, source: Path, folder: Path):
    raw = git(source, 'show', LAUNCH_REV + ':' + LAUNCH_FILE)
    if hashlib.sha256(raw).hexdigest() != LAUNCH_SHA:
        raise RuntimeError('PINNED_LAUNCHER_CHANGED')
    launcher = types.ModuleType('coherence_pinned_runtime_loader')
    launcher.__file__ = str(folder / 'pinned_loader.py')
    exec(compile(raw, launcher.__file__, 'exec'), launcher.__dict__)
    text, _ = launcher.prepare_runtime(root, source)
    changed = adapt_runtime(text)
    runtime_path = folder / 'protected_runtime.py'
    runtime_path.write_text(changed)
    module = launcher.load_module(runtime_path, 'coherence_protected_runtime')
    return module, runtime_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=0)
    args = parser.parse_args()
    if args.seconds != 0 and not 40 <= args.seconds <= 900:
        parser.error('seconds must be 0 (manual stop) or 40..900')
    root = Path.home() / 'projects/shadps4'
    (root / 'logs').mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='coherence-launch-', dir=root / 'logs'))
    try:
        with (root / 'clean-rebase-verified/.build.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            source, info, candidate, reference = verify_build(root)
            module, path = load_protected_runtime(root, source, folder)
            configure_runtime(module, root, info)
            # Use the unmodified full-tree verifier from the protected runtime too.
            module.verify_host(info / 'build-result.json', candidate, REV)
            print('VERIFIED_CANDIDATE=' + REV + '; REBUILD=NO', flush=True)
            sys.argv = [str(path), '--candidate', str(candidate), '--source-revision', REV,
                '--host-report', str(info / 'build-result.json'), '--baseline-binary', str(reference),
                '--baseline-profile', str(Path.home() / '.local/share/shadPS4'),
                '--game', '/mnt/roms-all/ps4-games/CUSA11456/eboot.bin', '--seconds', str(args.seconds)]
            return module.main()
    except (Exception, KeyboardInterrupt) as error:
        (folder / 'failure.txt').write_text(type(error).__name__ + ': ' + str(error) + '\n')
        archive = folder.with_suffix('.tar.gz')
        with tarfile.open(archive, 'w:gz') as packed:
            packed.add(folder / 'failure.txt', arcname='info/failure.txt')
        print('COHERENCE_LAUNCH_FAILED=' + str(error), file=sys.stderr, flush=True)
        print('UPLOAD_THIS=' + str(archive), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
