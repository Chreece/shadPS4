#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only CLI regressions for the combined all-game journal."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

QUERY = Path(__file__).with_name('query_emulator_diary.py')


class JournalReviewsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root / 'TRIALS.jsonl'
        self.private = self.root / 'private.jsonl'
        self.registry = self.root / 'FIXES.json'
        self.reviews = self.root / 'reviews'
        self.reviews.mkdir()
        self.gow = {'record_id': 'gow-old', 'date': '2026-10-27',
                    'game_id': 'CUSA34384', 'tested_games': ['CUSA34384'],
                    'decision': 'REJECT_RUNTIME', 'archive': 'gow.tar.gz'}
        self.review = {'record_id': 'got-new', 'recorded_on': '2026-10-28',
                       'context_games': ['CUSA11456', 'CUSA34384'],
                       'tested_games': ['CUSA11456'], 'related_prs': [68, 69],
                       'decision': 'KEEP_GENERIC_FIX_NOT_FULL_GRAPHICS',
                       'emulator_effect': 'Eight exact stencil mask events',
                       'game_effect': 'Sword silhouette, missing scenery',
                       'main_merge_commits': {'69': 'a' * 40},
                       'next': 'Keep corrected stencil; inspect real shader consumers'}
        self.ledger.write_text(json.dumps(self.gow) + '\n')
        self.private.write_text(json.dumps({'record_id': 'private-new',
            'date': '2026-10-28', 'game_id': 'CUSA99999'}) + '\n')
        self.put('current.json', self.review)
        self.registry.write_text(json.dumps({'fixes': [{'id': 'gpu-linear-readback',
            'status': 'MERGED_MAIN_NATIVE_MAIN_PENDING', 'subsystem': 'gpu-coherence',
            'related_prs': [57, 68]}]}))

    def put(self, name, item):
        (self.reviews / name).write_text(json.dumps(item))

    def run_query(self, *options, ok=True):
        args = [sys.executable, str(QUERY), '--ledger', str(self.ledger),
                '--private-ledger', str(self.private), '--registry', str(self.registry)]
        result = subprocess.run(args + list(options), text=True, capture_output=True, timeout=8)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def query(self, *options):
        return json.loads(self.run_query('--json', '--all', *options).stdout)

    def test_shared_reviews_and_private_are_one_search(self):
        result = self.query()
        self.assertEqual({x['record_id'] for x in result['trials']},
                         {'gow-old', 'got-new', 'private-new'})

    def test_recent_negative_review_is_searchable(self):
        self.put('failed.json', {'record_id': 'failed', 'recorded_on': '2026-10-28',
            'decision': 'REJECT_LINEAR8_NO_MATCHES', 'emulator_effect': 'IndexedLinear8Source=0'})
        result = self.query('--find', 'IndexedLinear8Source', '--rejected')
        self.assertEqual([x['record_id'] for x in result['trials']], ['failed'])

    def test_recorded_on_sorts_after_old_ledger_date(self):
        trials = self.query()['trials']
        self.assertLess([x['record_id'] for x in trials].index('got-new'),
                        [x['record_id'] for x in trials].index('gow-old'))

    def test_context_does_not_change_tested_games(self):
        trials = self.query('--game', 'CUSA34384')['trials']
        got = next(x for x in trials if x['record_id'] == 'got-new')
        self.assertEqual(got['tested_games'], ['CUSA11456'])
        self.assertEqual(len(trials), 2)

    def test_distinct_review_ids_sharing_hash_survive(self):
        for ident in ('first-review', 'correction-review'):
            self.put(ident + '.json', {'record_id': ident, 'artifact_sha256': 'b' * 64,
                                     'decision': 'REJECT_TEST', 'reason': 'shared artifact'})
        result = self.query('--find', 'shared artifact')
        self.assertEqual(result['matching_trial_count'], 2)

    def test_duplicate_id_does_not_replace_shared_history(self):
        self.put('duplicate.json', dict(self.gow, decision='PASS_NOT_AUTHORIZED'))
        result = self.query('--find', 'gow-old')
        self.assertEqual(result['matching_trial_count'], 1)
        self.assertEqual(result['trials'][0]['decision'], 'REJECT_RUNTIME')

    def test_fix_links_addenda_by_related_pr(self):
        result = self.query('--fix', 'gpu-linear-readback')
        self.assertEqual([x['record_id'] for x in result['trials']], ['got-new'])
        self.assertEqual(len(result['fixes']), 1)

    def test_malformed_review_is_not_silently_skipped(self):
        (self.reviews / 'bad.json').write_text('{')
        result = self.run_query(ok=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid shared review', result.stderr)

    def test_missing_record_id_is_not_silently_skipped(self):
        self.put('no-id.json', {'decision': 'REJECT_UNKNOWN'})
        result = self.run_query(ok=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn('record_id', result.stderr)

    def test_missing_review_directory_keeps_legacy_usage(self):
        (self.reviews / 'current.json').unlink()
        self.reviews.rmdir()
        self.assertEqual(self.query()['matching_trial_count'], 2)

    def test_explicit_review_directory(self):
        other = self.root / 'chosen'; other.mkdir()
        (other / 'alt.json').write_text(json.dumps({'record_id': 'alt'}))
        ids = {x['record_id'] for x in self.query('--reviews', str(other))['trials']}
        self.assertEqual(ids, {'gow-old', 'private-new', 'alt'})

    def test_render_includes_real_date_decision_merge_and_next(self):
        text = self.run_query('--find', 'Eight exact', '--all').stdout
        for expected in ('2026-10-28', 'KEEP_GENERIC_FIX_NOT_FULL_GRAPHICS',
                         'Sword silhouette', 'a' * 40, 'inspect real shader consumers',
                         'REVIEW: current.json'):
            self.assertIn(expected, text)

    def test_search_does_not_modify_inputs(self):
        def state():
            return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.root.rglob('*') if p.is_file()}
        before = state()
        self.query('--find', 'stencil')
        self.assertEqual(before, state())

    def test_build_only_review_remains_untested(self):
        self.put('build.json', {'record_id': 'build-only', 'recorded_on': '2026-10-28',
            'tested_games': [], 'context_games': ['CUSA11456'], 'build': 'PASS',
            'game_result': 'NOT_RUN', 'decision': 'KEEP_BUILD_ONLY'})
        result = self.query('--find', 'build-only')['trials'][0]
        self.assertEqual(result['tested_games'], [])
        self.assertEqual(result['game_result'], 'NOT_RUN')


if __name__ == '__main__':
    unittest.main()
