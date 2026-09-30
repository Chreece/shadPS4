#!/usr/bin/env python3
"""Exercise source preparation with real local Git repos and a fake Docker CLI.

The fixture intentionally stops at test_instrument.py, before image construction,
compilation or installation. No user's source tree or Docker daemon is used.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


class SourceSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ngs2-setup-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = os.environ.copy()
        self.env.update({
            'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_AUTHOR_NAME': 'Test',
            'GIT_AUTHOR_EMAIL': 'test@example.invalid',
            'GIT_COMMITTER_NAME': 'Test',
            'GIT_COMMITTER_EMAIL': 'test@example.invalid',
            'GIT_ALLOW_PROTOCOL': 'file',
        })
        for name in ('nested', 'module', 'remote'):
            repo = self.root / name
            repo.mkdir()
            self.git(repo, 'init', '-q')
            (repo / 'tracked').write_text(name + '\n')
            self.git(repo, 'add', '.')
            self.git(repo, 'commit', '-qm', 'base')
        self.git(self.root / 'module', 'submodule', 'add',
                 str(self.root / 'nested'), 'nested')
        self.git(self.root / 'module', 'commit', '-qam', 'add nested')
        self.remote = self.root / 'remote'
        self.git(self.remote, 'submodule', 'add', str(self.root / 'module'), 'external')
        (self.remote / 'same').write_text('unchanged\n')
        self.git(self.remote, 'add', '.')
        self.git(self.remote, 'commit', '-qm', 'add module')
        self.base = self.git(self.remote, 'rev-parse', 'HEAD').strip()
        self.seed = self.root / 'original'
        self.git(self.root, 'clone', '--recurse-submodules', str(self.remote), str(self.seed))
        self.git(self.remote, 'switch', '-c', 'diagnostics/ngs2-bounded-trace')
        tools = self.remote / 'tools/ngs2_probe'
        tools.mkdir(parents=True)
        (tools / 'test_instrument.py').write_text(
            "print('SETUP_SENTINEL: stop before building')\nraise SystemExit(19)\n")
        self.git(self.remote, 'add', '.')
        self.git(self.remote, 'commit', '-qm', 'diagnostic fixture')
        self.expected = self.git(self.remote, 'rev-parse', 'HEAD').strip()
        (self.seed / 'tracked').write_text('local staged edit\n')
        self.git(self.seed, 'add', 'tracked')
        (self.seed / 'untracked').write_text('local untracked data\n')
        (self.seed / 'external/tracked').write_text('local module edit\n')
        (self.seed / 'external/nested/tracked').write_text('local nested edit\n')
        os.utime(self.seed / 'same', ns=(1_000_000_000, 1_000_000_000))
        self.src = self.root / 'isolated'
        self.state = self.root / 'state'
        self.dest = self.root / 'dest'
        fake_bin = self.root / 'bin'
        fake_bin.mkdir()
        docker = fake_bin / 'docker'
        docker.write_text('#!/usr/bin/env bash\ncase "$1" in\n'
                          '  info|ps) true ;;\n'
                          '  *) echo UNEXPECTED_DOCKER_COMMAND >&2; false ;;\nesac\n')
        docker.chmod(0o755)
        self.env['PATH'] = str(fake_bin) + os.pathsep + self.env['PATH']
        script = Path(__file__).with_name('build.sh').read_text()
        replacements = {
            '/home/chreece/src/shadps4-waitstage-fix': str(self.seed),
            '/home/chreece/src/shadps4-ngs2-probe-source': str(self.src),
            '/home/chreece/.local/state/shadps4-ngs2-probe': str(self.state),
            '/home/chreece/Applications/shadps4/releases/ngs2-probe': str(self.dest),
            'https://github.com/Chreece/shadPS4.git': self.remote.as_uri(),
            '0a7790aaa11c5ec0009cc66976bb90a8ce7078e5': self.base,
        }
        for old, new in replacements.items():
            script = script.replace(old, new)
        self.script = self.root / 'build.sh'
        self.script.write_text(script)
        self.original = self.snapshot()

    def git(self, cwd, *args):
        result = subprocess.run(['git', '-c', 'protocol.file.allow=always', *args],
                                cwd=cwd, env=self.env, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout

    def snapshot(self):
        files = ('tracked', 'untracked', 'same', 'external/tracked',
                 'external/nested/tracked')
        return (
            {p: (self.seed / p).read_bytes() for p in files},
            self.git(self.seed, 'rev-parse', 'HEAD'),
            self.git(self.seed, 'symbolic-ref', 'HEAD'),
            self.git(self.seed, 'status', '--porcelain'),
            self.git(self.seed, 'diff', '--cached'),
        )

    def run_setup(self, expected=None):
        return subprocess.run(['bash', str(self.script), expected or self.expected],
                              env=self.env, text=True, capture_output=True, timeout=25)

    def test_dirty_seed_and_nested_modules_are_preserved(self):
        result = self.run_setup()
        self.assertIn('SETUP_SENTINEL', result.stdout, result.stdout + result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), self.original)
        self.assertEqual((self.src / 'tracked').read_text(), 'remote\n')
        self.assertEqual((self.src / 'external/tracked').read_text(), 'module\n')
        self.assertEqual((self.src / 'external/nested/tracked').read_text(), 'nested\n')
        self.assertFalse((self.src / 'untracked').exists())
        self.assertFalse(list((self.src / '.git').rglob('alternates')))

    def test_identical_file_mtime_reused_but_dirty_file_not_copied(self):
        result = self.run_setup()
        self.assertIn('UNCHANGED_SOURCE_TIMESTAMPS_REUSED=', result.stdout)
        self.assertEqual((self.src / 'same').stat().st_mtime_ns, 1_000_000_000)
        self.assertEqual((self.src / 'tracked').read_text(), 'remote\n')

    def test_dirty_isolated_checkout_is_not_reset(self):
        self.run_setup()
        p = self.src / 'tracked'
        p.write_text('keep isolated edits\n')
        result = self.run_setup()
        self.assertIn('ISOLATED_SOURCE_HAS_LOCAL_CHANGES=', result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(p.read_text(), 'keep isolated edits\n')
        self.assertEqual(self.snapshot(), self.original)

    def test_symlink_checkout_is_rejected(self):
        self.src.symlink_to(self.seed, target_is_directory=True)
        result = self.run_setup()
        self.assertIn('ISOLATED_SOURCE_INVALID', result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), self.original)

    def test_success_messages_are_not_printed_on_setup_failure(self):
        result = self.run_setup()
        self.assertIn('SETUP_SENTINEL', result.stdout)
        self.assertIn('NGS2_PROBE_SETUP=FAIL', result.stdout)
        self.assertNotIn('NGS2_PROBE_BUILD=PASS', result.stdout)
        self.assertNotIn('READY: no game launched', result.stdout)
        self.assertEqual(result.returncode, 1)


if __name__ == '__main__':
    unittest.main()
