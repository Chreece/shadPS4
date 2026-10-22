#!/usr/bin/env python3
"""Test the real inherited Sirit-check method; compiler/validator calls are stubs.

Run with --native-package pointing to the immutable native-minimum package.
This tests workflow preconditions and error propagation, NOT native code or GPU.
"""
import argparse
import ast
import hashlib
import shutil
import tempfile
from pathlib import Path
import types
import unittest
from unittest.mock import patch
import run_srt_dependencies as shared

NATIVE_SHA = '0471869c8911f455a1ff99e880b433527b22b5239ae3529e207f33bd10eb2b34'
OLD_RUNNER_SHA = 'dbd4c65646f24e73f339a727af1869b3e6c9721b3b30328e2c5797a658fead8f'
ADDITION = '''            # prepare() intentionally skips the old patch stack, which used to
            # create this directory for the inherited Sirit emitter checks.
            (self.run/'native-tests').mkdir(exist_ok=True)
'''


def load_parent(path):
    raw = (path / 'run_native.py').read_bytes()
    if hashlib.sha256(raw).hexdigest() != NATIVE_SHA:
        raise RuntimeError('NATIVE_WORKFLOW_VERSION_MISMATCH')
    tree = ast.parse(raw)
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == 'NativeJob')
    method = next(x for x in cls.body if isinstance(x, ast.FunctionDef) and x.name == 'verify_native_library')
    fixture = ast.Module(body=[ast.ClassDef(name='Parent', bases=[], keywords=[], body=[method], decorator_list=[])], type_ignores=[])
    scope = {'shutil': shutil}
    exec(compile(ast.fix_missing_locations(fixture), str(path / 'run_native.py'), 'exec'), scope)
    return scope['Parent']


class HandoffTests(unittest.TestCase):
    parent = None

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.commands = []
        self.failure = None

    def job(self, module=shared):
        job = module.job_class(types.SimpleNamespace(), self.parent)()
        job.run = self.root / 'run'
        job.info = job.run / 'info'
        job.source = self.root / 'source'
        job.build = self.root / 'build'
        job.package = self.root / 'package'
        job.sirit = job.source / 'externals/sirit'
        job.compiler = 'fixture-c++'
        job.report = {}
        job.info.mkdir(parents=True, exist_ok=True)
        library = job.build / 'externals/sirit/libsirit.a'
        header = job.source / 'externals/spirv-headers/include/spirv/unified1/spirv.hpp11'
        for path in (library, header):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        def command(args, **kwargs):
            words = list(map(str, args))
            self.commands.append(words)
            if '--source' in words and self.failure == 'srt':
                raise RuntimeError('INJECTED_SRT_NATIVE_FAILURE')
            if words[0] == 'fixture-c++':
                output = Path(words[words.index('-o') + 1])
                if not output.parent.is_dir():
                    raise RuntimeError('NATIVE_TEST_OUTPUT_DIRECTORY_MISSING')
                output.write_bytes(b'fixture executable')
            if Path(words[0]).name == 'real-sirit-emitter':
                Path(words[1]).write_bytes(b'fixture SPIR-V')
                return b'FIXTURE_SIRIT_CALL_COMPLETED\n'
            if words[0] == '/fixture/spirv-val' and self.failure == 'validator':
                raise RuntimeError('INJECTED_VALIDATOR_FAILURE')
            return b'FIXTURE_COMMAND_COMPLETED\n'
        job.command = command
        return job

    def test_old_runner_reproduces_missing_directory(self):
        text = Path(shared.__file__).read_text()
        self.assertEqual(text.count(ADDITION), 1)
        old = text.replace(ADDITION, '', 1)
        self.assertEqual(hashlib.sha256(old.encode()).hexdigest(), OLD_RUNNER_SHA)
        module = types.ModuleType('original_shared_srt_fixture')
        module.__file__ = shared.__file__
        exec(compile(old, 'old_shared_runner.py', 'exec'), module.__dict__)
        with patch('shutil.which', return_value='/fixture/spirv-val'):
            with self.assertRaisesRegex(RuntimeError, 'OUTPUT_DIRECTORY_MISSING'):
                self.job(module).verify_native_library()

    def test_new_runner_creates_directory_and_runs_inherited_check(self):
        job = self.job()
        self.assertFalse((job.run / 'native-tests').exists())
        with patch('shutil.which', return_value='/fixture/spirv-val'):
            job.verify_native_library()
        self.assertTrue((job.info / 'native-emitter.spv').is_file())
        self.assertEqual(job.report['spirv_validation'], 'PASS')
        self.assertIn('CMakeFiles/shadps4.dir/' + shared.patch.FLAT + '.o', self.commands[-1])
        self.assertIn('CMakeFiles/shadps4.dir/' + shared.patch.CACHE + '.o', self.commands[-1])

    def test_existing_native_directory_is_accepted(self):
        job = self.job()
        (job.run / 'native-tests').mkdir()
        with patch('shutil.which', return_value='/fixture/spirv-val'):
            job.verify_native_library()

    def test_failed_srt_gate_does_not_run_inherited_compiler(self):
        job = self.job()
        self.failure = 'srt'
        with self.assertRaisesRegex(RuntimeError, 'SRT_NATIVE_FAILURE'):
            job.verify_native_library()
        self.assertEqual(len(self.commands), 1)
        self.assertNotIn('srt_native_dependency_regressions', job.report)

    def test_failed_validator_does_not_compile_production_objects(self):
        job = self.job()
        self.failure = 'validator'
        with patch('shutil.which', return_value='/fixture/spirv-val'):
            with self.assertRaisesRegex(RuntimeError, 'VALIDATOR_FAILURE'):
                job.verify_native_library()
        self.assertFalse(any('ninja' in c for c in self.commands))
        self.assertNotIn('spirv_validation', job.report)

    def test_native_output_path_collision_refused(self):
        job = self.job()
        (job.run / 'native-tests').write_bytes(b'not a directory')
        with self.assertRaises(FileExistsError):
            job.verify_native_library()
        self.assertEqual(self.commands, [])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-package', type=Path, required=True)
    args, rest = parser.parse_known_args()
    HandoffTests.parent = load_parent(args.native_package)
    unittest.main(argv=['test_native_handoff.py', *rest], verbosity=2)
