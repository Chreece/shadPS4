# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify passive log capture cannot consume streams or miss redirected stdout."""

import os
from pathlib import Path
import tempfile
import unittest

import collect_pes_runtime_context as context


class OutputTests(unittest.TestCase):
    def test_console_destination_is_collected_without_consuming_pipe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc, work = root / 'process', root / 'capture'
            (proc / 'fd').mkdir(parents=True)
            work.mkdir()
            actual = root / 'arbitrary-session-name.txt'
            actual.write_bytes(b'[Loader] startup failure detail\n')
            (proc / 'fd' / '1').symlink_to(actual)
            pipe = root / 'pipe'
            os.mkfifo(pipe)
            (proc / 'fd' / '2').symlink_to(pipe)
            empty_log = root / 'shadps4.log'
            empty_log.write_bytes(b'launch line only\n')
            (proc / 'fd' / '9').symlink_to(empty_log)
            game_log = root / 'CUSA18676.log'
            game_log.write_bytes(b'[Game] loading failure detail\n')
            (proc / 'fd' / '4').symlink_to(game_log)
            records = {r['fd']: r for r in context.collect_outputs(proc, work)}
            self.assertEqual((work / 'fd-1.0.log').read_bytes(), actual.read_bytes())
            self.assertEqual(records[2]['status'], 'not_regular_not_read')
            self.assertEqual(records[9]['status'], 'captured')
            self.assertEqual(records[4]['status'], 'captured')
            self.assertEqual((work / 'fd-4.0.log').read_bytes(), game_log.read_bytes())

    def test_large_file_retains_exact_head_tail_offsets_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'output'
            data = bytes(range(100))
            source.write_bytes(data)
            with source.open('rb') as stream:
                stream.seek(20)
                result = context.copy_regular_log(
                    Path(f'/proc/self/fd/{stream.fileno()}'), root / 'capture', limit=16)
                self.assertEqual(stream.tell(), 20)
            self.assertEqual(source.read_bytes(), data)
            self.assertEqual([p['offset'] for p in result['parts']], [0, 92])
            self.assertEqual((root / 'capture.0.log').read_bytes(), data[:8])
            self.assertEqual((root / 'capture.1.log').read_bytes(), data[-8:])
            self.assertTrue(result['truncated'])


if __name__ == '__main__':
    unittest.main()
