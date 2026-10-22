#!/usr/bin/env python3
"""Single-variable readback diagnosis on the already-built copy-range candidate.

No compilation, emulator source edits, permanent setting writes, new driver flags,
extra GPU snapshots, automated input, or executable promotion. This is a diagnostic,
not a permanent workaround or a claimed emulator fix.
"""
from __future__ import annotations
import ast
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import types
import tarfile
import tempfile

LAUNCH_REV = 'e5db2b9967e4a69e76ff3fb7e8dcf1139ae058b3'
LAUNCH_FILE = 'scripts/run_verified_copy_candidate_20261022.py'
LAUNCH_SHA = '6dffa58152ee047a52ed63e648b5b90a094de9a8d08101c094393d382b0ac63c'
RUNTIME_SHA = 'b688234b4dfbd690399f0fec2f4c55351379cfd71d6cebb4484318e83175510f'
REFERENCE = 'shadps4-masked-wave-runtime-20261022-165356-fd8a5e'
FINAL_SHA = 'd40ee62d79992dcf6b30fa6060b79709cac769bfa759e8f086b1af56a165720b'
ENV_SHA = 'ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356'
REVISION = '9d70b53b323f54a38bb2f77eed4435154679adfe'
BINARY_SHA = '0355d23f97c85c65f27d1a50f7a01e5587b39248829d440df7d535a1a235c10f'


def readback_reference(root):
    reference = root / 'logs' / REFERENCE
    paths = {name: reference / rel for name, rel in {
        'final': 'info/final.txt', 'environment': 'info/render-environment.json',
        'config': 'profile/user/config.json'}.items()}
    for name, path in paths.items():
        if (path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024
                or not path.resolve().is_relative_to(reference.resolve())):
            raise RuntimeError('REFERENCE_FILE_MISSING_OR_UNSAFE=' + str(path))
    data = {name: path.read_bytes() for name, path in paths.items()}
    for name, expected in (('final', FINAL_SHA), ('environment', ENV_SHA)):
        if hashlib.sha256(data[name]).hexdigest() != expected:
            raise RuntimeError('REFERENCE_EVIDENCE_CHANGED=' + name)
    config = json.loads(data['config'])
    if not isinstance(config, dict) or not isinstance(config.get('GPU'), dict):
        raise RuntimeError('REFERENCE_CONFIG_INVALID')
    if type(config['GPU'].get('readbacks_mode')) is not int or config['GPU']['readbacks_mode'] != 0:
        raise RuntimeError('REFERENCE_MUST_HAVE_DISABLED_READBACKS')
    if config['GPU'].get('dump_shaders') is not True or config['GPU'].get('patch_shaders') is not False:
        raise RuntimeError('REFERENCE_DIAGNOSTICS_CHANGED')
    if json.loads(data['environment']) != {}:
        raise RuntimeError('REFERENCE_DRIVER_OVERRIDES_NOT_EMPTY')
    return reference, paths, data, config


# This mixin is inserted into the exact previously tested runtime. Its inherited
# host verification, process ownership, locks, cleanup and Ollama handling stay intact.
class ReadbackControlMixin:
    def clone_profile(self):
        reference, paths, evidence, before = readback_reference(self.root)
        if self.args.source_revision != REVISION or self.meta.get('CANDIDATE_SHA256') != BINARY_SHA:
            raise RuntimeError('READBACK_COMPARISON_REQUIRES_EXACT_BINARY')
        self.readback_paths = paths
        self.readback_hashes = {k: hashlib.sha256(v).hexdigest() for k, v in evidence.items()}
        self.readback_reference_ready = False
        self.readback_comparison = {
            'diagnostic_only': True, 'reference_run': str(reference),
            'reference_status': 'GPU_CONTEXT_FAILURE', 'candidate_revision': REVISION,
            'candidate_sha256': BINARY_SHA, 'rebuilt': False,
            'profile_source': 'retained_disposable_profile_from_failed_run',
            'reference_config_provenance': 'retained_post_run_file; no historical config hash in capture',
            'reference_hashes_at_test_start': self.readback_hashes,
            'intended_config_changes': [{'path': 'GPU.readbacks_mode', 'before': 0, 'after': 2}],
            'same_binary_comparison': True, 'claim': 'NO_EMULATOR_FIX_OR_VISUAL_SUCCESS_ASSUMED'}
        (self.run / 'info/readback-reference-final.txt').write_bytes(evidence['final'])
        (self.run / 'info/readback-config-before.json').write_bytes(evidence['config'])
        original_profile = self.original_profile
        try:
            self.original_profile = reference / 'profile/user'
            super().clone_profile()
        finally:
            # The inherited preflight/finish still checks the actual permanent config.
            self.original_profile = original_profile
        config = self.run / 'profile/user/config.json'
        if config.is_symlink() or not config.is_file():
            raise RuntimeError('DISPOSABLE_CONFIG_NOT_REGULAR')
        current = json.loads(config.read_bytes())
        if current != before:
            raise RuntimeError('REFERENCE_PROFILE_WOULD_CHANGE_OTHER_SETTINGS')
        current['GPU']['readbacks_mode'] = 2
        config.write_text(json.dumps(current, indent=2) + '\n')
        check = json.loads(config.read_bytes())
        if check['GPU'].pop('readbacks_mode') != 2:
            raise RuntimeError('PRECISE_READBACK_CONFIGURATION_FAILED')
        check['GPU']['readbacks_mode'] = 0
        if check != before:
            raise RuntimeError('NOT_A_SINGLE_VARIABLE_CONFIG_CHANGE')
        (self.run / 'info/readback-config-requested.json').write_bytes(config.read_bytes())
        self.readback_comparison['requested_config_sha256'] = digest(config)
        self.readback_comparison['config_delta_verified'] = 'GPU.readbacks_mode:0->2 ONLY'
        self.readback_reference_ready = True
        self.meta.update(READBACK_POLICY='TEMPORARY_PRECISE_SINGLE_VARIABLE_DIAGNOSTIC',
                         READBACK_REFERENCE=REFERENCE, READBACK_MODE_REQUESTED='2',
                         CONFIG_DELTA='GPU.readbacks_mode:0->2_ONLY',
                         EXPENSIVE_DIAGNOSTICS='FUNCTIONAL_READBACKS;NO_EXTRA_GPU_SNAPSHOTS_OR_DRIVER_TRACING',
                         PROFILE_SOURCE='RETAINED_FAILED_RUN_PROFILE_ISOLATED_COPY')
        self._save_readback_comparison()
        self.say('READBACK_CONTROL=READY same_binary=YES setting_change=0->2 permanent_config=UNTOUCHED')

    def _save_readback_comparison(self):
        (self.run / 'info/readback-comparison.json').write_text(
            json.dumps(self.readback_comparison, indent=2) + '\n')

    def environment(self):
        env = super().environment()
        recorded = json.loads((self.run / 'info/render-environment.json').read_bytes())
        if recorded != {}:
            raise RuntimeError('DRIVER_ENVIRONMENT_DIFFERS_FROM_REFERENCE')
        self.readback_comparison['driver_override_environment_matches_reference'] = True
        self._save_readback_comparison()
        return env

    def monitor_vram(self, elapsed, free):
        super().monitor_vram(elapsed, free)
        if elapsed >= 20 and not self.log.precise.is_set():
            raise RuntimeError('PRECISE_READBACK_MODE_NOT_CONFIRMED_BY_EMULATOR')

    def _finish_readback_control(self, code):
        self.meta['READBACK_POLICY'] = 'TEMPORARY_PRECISE_SINGLE_VARIABLE_DIAGNOSTIC'
        if not getattr(self, 'readback_reference_ready', False):
            self.meta['READBACK_COMPARISON'] = 'NOT_STARTED'
            return code
        try:
            saved = ANSI.sub('', (self.run / 'game.log').read_text(errors='replace'))
            modes = re.findall(r'GPU readbacksMode:\s*(\d+)\b', saved)
            active = bool(modes) and set(modes) == {'2'} and self.log.precise.is_set()
            self.meta['READBACK_MODE_OBSERVED'] = ','.join(modes) or 'NOT_OBSERVED'
            self.meta['READBACK_MODE_VERIFIED'] = 'PASS' if active else 'FAIL'
            if self.meta.get('GAME_LAUNCHED') == 'YES' and not active:
                if code == 0:
                    self.meta['STATUS'] = 'READBACK_MODE_NOT_VERIFIED'
                code = max(code, 2)
            intact = all(digest(p) == self.readback_hashes[k] for k, p in self.readback_paths.items())
            same_binary = digest(self.args.candidate) == BINARY_SHA
            self.meta['REFERENCE_CONFIG_AND_EVIDENCE_UNCHANGED'] = 'PASS' if intact else 'FAIL'
            self.meta['TEST_BINARY_UNCHANGED'] = 'PASS' if same_binary else 'FAIL'
            if not intact or not same_binary:
                if code == 0:
                    self.meta['STATUS'] = 'READBACK_CONTROL_INTEGRITY_FAILED'
                code = max(code, 3)
            self.readback_comparison.update(
                observed_modes=modes, mode_verified=active, reference_unchanged=intact,
                test_binary_unchanged=same_binary,
                interpretation='REQUIRES_VISUAL_REVIEW; READBACKS_COMBINE_TRANSFER_AND_WAITING')
            self._save_readback_comparison()
        except Exception as error:
            self.meta['READBACK_COMPARISON'] = 'EVIDENCE_ERROR'
            self.say('READBACK_COMPARISON_ERROR=' + str(error))
            code = max(code, 2)
        return code


def adapt_runtime(text):
    if hashlib.sha256(text.encode()).hexdigest() != RUNTIME_SHA:
        raise RuntimeError('PREVIOUS_RUNTIME_VERSION_MISMATCH')
    old = '        self.meta["READBACK_POLICY"] = "COPIED_GLOBAL_SETTING_NOT_FORCED"'
    if text.count(old) != 1 or text.count('\ndef main():\n') != 1:
        raise RuntimeError('READBACK_RUNTIME_ANCHOR_NOT_UNIQUE')
    text = text.replace(old, '        code = self._finish_readback_control(code)', 1)
    text = text.replace('f"shadps4-masked-wave-runtime-{stamp}-{uuid.uuid4().hex[:6]}"',
                        'f"shadps4-readback-control-runtime-{stamp}-{uuid.uuid4().hex[:6]}"', 1)
    text = text.replace('"NORMAL_RENDERER_NO_DRIVER_OVERRIDES"',
                        '"READBACK_ONLY_DIAGNOSTIC_NO_DRIVER_OVERRIDES"', 1)
    constants = '\n'.join(name + '=' + repr(globals()[name]) for name in
                          ('REFERENCE', 'FINAL_SHA', 'ENV_SHA', 'REVISION', 'BINARY_SHA'))
    extra = ('\n' + constants + '\n\n' + inspect.getsource(readback_reference) + '\n' +
             inspect.getsource(ReadbackControlMixin) +
             '\nRuntime = type("ReadbackControlRuntime", (ReadbackControlMixin, Runtime), {})\n')
    text = text.replace('\ndef main():\n', extra + '\ndef main():\n', 1)
    ast.parse(text)
    return text


def main():
    root = Path.home() / 'projects/shadps4'
    source = root / 'clean-rebase-verified/source'
    # Verify the retained baseline before importing any local runtime package.
    readback_reference(root)
    raw = subprocess.check_output(['git', '-C', str(source), 'show', LAUNCH_REV + ':' + LAUNCH_FILE],
                                  stderr=subprocess.PIPE, timeout=30)
    if hashlib.sha256(raw).hexdigest() != LAUNCH_SHA:
        raise RuntimeError('PINNED_LAUNCHER_CHECKSUM_MISMATCH')
    ast.parse(raw)
    launcher = types.ModuleType('pinned_readback_reference_launcher')
    launcher.__file__ = str(source / LAUNCH_FILE)
    sys.modules[launcher.__name__] = launcher
    exec(compile(raw, launcher.__file__, 'exec'), launcher.__dict__)
    previous = launcher.prepare_runtime
    def prepare(root, source):
        text, base = previous(root, source)
        return adapt_runtime(text), base
    launcher.prepare_runtime = prepare
    return launcher.main()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print('READBACK_CONTROL_PREFLIGHT_FAILED=' + str(error), file=sys.stderr)
        try:
            logs = Path.home() / 'projects/shadps4/logs'
            logs.mkdir(parents=True, exist_ok=True)
            folder = Path(tempfile.mkdtemp(prefix='readback-control-preflight-', dir=logs))
            (folder / 'failure.txt').write_text(type(error).__name__ + ': ' + str(error) + '\nGAME_LAUNCHED=NO\n')
            archive = folder.with_suffix('.tar.gz')
            with tarfile.open(archive, 'w:gz') as packed:
                packed.add(folder / 'failure.txt', arcname='info/failure.txt')
            print('UPLOAD_THIS=' + str(archive), flush=True)
        except OSError as report_error:
            print('REPORT_ERROR=' + str(report_error), file=sys.stderr)
        raise SystemExit(2)
