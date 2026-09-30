#!/usr/bin/env python3
"""Test preflight failure propagation with fake tools, not an emulator build."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
TOOLS = ('clang-19', 'clang++-19', 'llvm-ar-19', 'llvm-ranlib-19',
         'mold', 'cmake', 'ninja', 'g++-14')
FAKE = '''#!/bin/bash
name="${0##*/}"
if [[ "${1:-}" == "--version" ]]; then printf 'fake test tool\\n'; exit 0; fi
printf '%s\\n' "$name" >> "$CALL_LOG"
[[ "$name" != "${FAIL_TOOL:-}" ]] || exit 29
if [[ "$name" == "clang++-19" || "$name" == "clang-19" ]]; then
    output=""
    while [[ "$#" -gt 0 ]]; do
        if [[ "$1" == "-o" ]]; then output="$2"; break; fi
        shift
    done
    if [[ -n "$output" ]]; then
        printf '#!/bin/bash\\nexit 0\\n' > "$output"
        /bin/chmod +x "$output"
    fi
fi
'''

class ToolchainPreflightTest(unittest.TestCase):
    def run_case(self, missing=None, failing=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = root / 'bin'
            tools.mkdir()
            scratch = root / 'tmp'
            scratch.mkdir()
            for name in TOOLS:
                if name == missing:
                    continue
                path = tools / name
                path.write_text(FAKE)
                path.chmod(0o755)
            for name in ('cat', 'mktemp', 'rm'):
                (tools / name).symlink_to(shutil.which(name))
            call_log = root / 'calls.txt'
            env = {**os.environ, 'PATH': str(tools), 'TMPDIR': str(scratch),
                   'CALL_LOG': str(call_log), 'FAIL_TOOL': failing or ''}
            result = subprocess.run(['/bin/bash', str(ROOT / 'toolchain_preflight.sh')],
                                    env=env, text=True, capture_output=True, timeout=10)
            calls = call_log.read_text().splitlines() if call_log.exists() else []
            self.assertEqual(list(scratch.iterdir()), [])
            return result, calls

    def test_missing_archiver_stops_before_compilation(self):
        result, calls = self.run_case(missing='llvm-ar-19')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('TOOLCHAIN_MISSING=llvm-ar-19', result.stderr)
        self.assertEqual(calls, [])

    def test_missing_ranlib_stops_before_compilation(self):
        result, calls = self.run_case(missing='llvm-ranlib-19')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('TOOLCHAIN_MISSING=llvm-ranlib-19', result.stderr)
        self.assertEqual(calls, [])

    def test_archiver_failure_is_not_reported_as_success(self):
        result, calls = self.run_case(failing='llvm-ar-19')
        self.assertEqual(result.returncode, 29)
        self.assertEqual(calls[-1], 'llvm-ar-19')
        self.assertNotIn('TOOLCHAIN_PREFLIGHT=PASS', result.stdout)

    def test_ranlib_failure_stops_before_link(self):
        result, calls = self.run_case(failing='llvm-ranlib-19')
        self.assertEqual(result.returncode, 29)
        self.assertEqual(calls[-1], 'llvm-ranlib-19')
        self.assertEqual(calls.count('clang++-19'), 1)

    def test_compilation_failure_stops_before_archive(self):
        result, calls = self.run_case(failing='clang++-19')
        self.assertEqual(result.returncode, 29)
        self.assertNotIn('llvm-ar-19', calls)

    def test_complete_command_sequence(self):
        result, calls = self.run_case()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ['clang-19', 'clang++-19', 'llvm-ar-19',
                                 'llvm-ranlib-19', 'clang++-19'])
        self.assertIn('TOOLCHAIN_PREFLIGHT=PASS', result.stdout)

if __name__ == '__main__':
    unittest.main()
