import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import install_local_default as installer
import pes_refresh_baseline as refresh


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.upstream = self.root / 'upstream'
        patched = patch.object(installer, 'UPSTREAM', self.upstream.as_uri())
        patched.start()
        self.addCleanup(patched.stop)
        self.git('init', '-b', 'main', self.upstream)
        self.git('config', 'user.name', 'Fixture', cwd=self.upstream)
        self.git('config', 'user.email', 'fixture@localhost', cwd=self.upstream)
        (self.upstream / 'emulator.txt').write_text('old main\n')
        self.commit(self.upstream, 'old main')
        self.old = self.git('rev-parse', 'HEAD', cwd=self.upstream)
        self.git('update-ref', 'refs/pull/5228/head', self.old, cwd=self.upstream)
        self.origin = self.root / 'origin'
        self.git('clone', self.upstream, self.origin)
        self.git('config', 'user.name', 'Fixture', cwd=self.origin)
        self.git('config', 'user.email', 'fixture@localhost', cwd=self.origin)
        self.manifest = {'schema': 1, 'upstream': {'repository': 'shadps4-emu/shadPS4',
                        'branch': 'main', 'revision': self.old},
                        'pending_fixes': [{'number': 5228, 'head': self.old}]}
        (self.origin / 'documents').mkdir()
        (self.origin / 'documents/LOCAL_TEST_BASELINE.json').write_text(json.dumps(self.manifest, indent=2) + '\n')
        (self.origin / 'retained-audio.txt').write_text('existing 7.1 fix\n')
        (self.origin / 'emulator.txt').write_text('retained diagnostic\n')
        self.commit(self.origin, 'retained changes')
        self.seed = self.git('rev-parse', 'HEAD', cwd=self.origin)

    def git(self, *args, cwd=None):
        return subprocess.check_output(['git', *map(str, args)], cwd=cwd,
                                       stderr=subprocess.PIPE, text=True).strip()

    def commit(self, repo, message):
        self.git('add', '.', cwd=repo)
        self.git('commit', '-m', message, cwd=repo)

    def advance(self, conflict=False):
        name = 'emulator.txt' if conflict else 'new-upstream.txt'
        (self.upstream / name).write_text('new main content\n')
        self.commit(self.upstream, 'advance main')
        return self.git('rev-parse', 'HEAD', cwd=self.upstream)

    def prepare(self, name='refresh'):
        with contextlib.redirect_stdout(io.StringIO()):
            return refresh.prepare(self.root / name, self.seed, self.manifest,
                                   origin=self.origin.as_uri(), upstream=self.upstream.as_uri())

    def test_advanced_main_rebases_fixes_and_exact_source_imports(self):
        current = self.advance()
        result = self.prepare()
        source = Path(result['source_directory'])
        self.assertEqual(result['upstream'], current)
        self.assertEqual((source / 'new-upstream.txt').read_text(), 'new main content\n')
        self.assertEqual((source / 'retained-audio.txt').read_text(), 'existing 7.1 fix\n')
        self.assertEqual((source / 'emulator.txt').read_text(), 'retained diagnostic\n')
        self.assertEqual(result['manifest']['upstream']['revision'], current)
        self.assertEqual(self.manifest['upstream']['revision'], self.old)
        self.assertEqual(self.git('status', '--porcelain', cwd=source), '')
        cache = self.root / 'build-cache'
        self.git('init', cache)
        self.git('remote', 'add', 'origin', self.origin.as_uri(), cwd=cache)
        installer.fetch_revision(cache, result['revision'], result['source_branch'],
                                 lambda args: subprocess.check_call(list(map(str, args)),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), source)
        self.assertEqual(self.git('rev-parse', 'FETCH_HEAD', cwd=cache), result['revision'])
        self.assertEqual(self.git('remote', 'get-url', 'origin', cwd=cache), self.origin.as_uri())
        self.assertEqual(self.git('show', 'FETCH_HEAD:retained-audio.txt', cwd=cache), 'existing 7.1 fix')
        self.assertEqual(self.prepare('repeat')['revision'], result['revision'])

    def test_current_seed_remains_exact_same_commit(self):
        result = self.prepare()
        self.assertEqual(result['revision'], self.seed)

    def test_conflict_fails_without_changing_original_repositories(self):
        current = self.advance(conflict=True)
        with self.assertRaisesRegex(RuntimeError, 'Source refresh failed'):
            self.prepare()
        record = json.loads((self.root / 'refresh/source.json').read_text())
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=self.origin), self.seed)
        self.assertEqual(self.git('rev-parse', 'HEAD', cwd=self.upstream), current)
        self.assertEqual(self.git('status', '--porcelain', cwd=self.origin), '')
        self.assertTrue((self.root / 'refresh/refresh.log').is_file())

    def test_changed_pr_head_stops_before_source_checkout(self):
        current = self.advance()
        self.git('update-ref', 'refs/pull/5228/head', current, cwd=self.upstream)
        with self.assertRaisesRegex(RuntimeError, 'PR head changed'):
            self.prepare()
        self.assertFalse((self.root / 'refresh/source-checkout').exists())
        self.assertEqual(json.loads((self.root / 'refresh/source.json').read_text())['status'], 'failed')

    def test_dirty_local_source_cannot_enter_builder(self):
        result = self.prepare()
        source = Path(result['source_directory'])
        (source / 'emulator.txt').write_text('external edit\n')
        with self.assertRaisesRegex(RuntimeError, 'has edits'):
            installer.fetch_revision(self.root / 'unused', result['revision'],
                                     result['source_branch'], lambda args: self.fail('fetch ran'), source)

    def test_wrong_commit_is_rejected_after_local_import(self):
        result = self.prepare()
        cache = self.root / 'build-cache'
        self.git('init', cache)
        with self.assertRaisesRegex(RuntimeError, 'pins'):
            installer.fetch_revision(cache, 'a' * 40, result['source_branch'],
                                     lambda args: subprocess.check_call(list(map(str, args)),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
                                     result['source_directory'])


if __name__ == '__main__':
    unittest.main()
