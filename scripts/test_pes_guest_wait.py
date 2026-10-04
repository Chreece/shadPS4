import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import pes_frame_profile as profile
import validate_pes_guest_wait as preflight


class GuestWaitTests(unittest.TestCase):
    def run_sample(self, unreadable=False, tick=None):
        class Frame:
            def __init__(self, pc, caller=None):
                self.address, self.caller = pc, caller
            def pc(self): return self.address
            def name(self): return 'fixture_' + hex(self.address)
            def older(self): return self.caller
            def architecture(self):
                return SimpleNamespace(disassemble=lambda start, end:
                                       [{'addr': start, 'asm': 'nop', 'length': 1}])

        selected = [None]
        class Thread:
            def __init__(self, name, number):
                self.name, self.global_num, self.ptid = name, number, (11, number, 0)
            def switch(self): selected[0] = self
            def is_valid(self): return True

        main, host, worker = Thread('Game:Main', 3), Thread('shadPS4:Main', 1), Thread('JobExecutor', 4)
        selected[0] = host
        gdb = SimpleNamespace(Breakpoint=object, selected_thread=lambda: selected[0],
                              selected_frame=lambda: Frame(0x400400),
                              newest_frame=lambda: Frame(0x400400, Frame(0x400500)),
                              selected_inferior=lambda: SimpleNamespace(threads=lambda: [host, worker, main]))
        reads = []
        def memory(address, size):
            self.assertTrue(0x400000 <= address < 0x402000 or 0x700000 <= address < 0x720000)
            self.assertLessEqual(size, 768)
            reads.append((address, size))
            if unreadable:
                raise RuntimeError('fixture mapping became unavailable')
            data = (0x701000).to_bytes(8, 'little') * ((size + 7) // 8)
            return data[:size]
        registers = {'rip': 0x400400, 'rsp': 0x700800, 'rbx': 0x701000}
        scope = {'gdb': gdb, 'time': time if tick is None else SimpleNamespace(monotonic=tick),
                 'result': {}, 'memory': memory, 'register': lambda name: registers.get(name, 0),
                 'number': lambda data, offset, size=8: int.from_bytes(data[offset:offset+size], 'little')}
        exec(compile(profile.SUPPORT, '<profile>', 'exec'), scope)
        with tempfile.TemporaryDirectory() as directory:
            scope['proc'] = Path(directory)
            (Path(directory) / 'maps').write_text(
                '00400000-00402000 r-xp 00000000 00:00 0\n'
                '00700000-00720000 rw-p 00000000 00:00 0\n'
                '00800000-00820000 ---p 00000000 00:00 0\n'
                '700000000000-700000010000 rw-p 00000000 00:00 0\n')
            scope['guest_wait_snapshot']('before')
        self.assertIs(selected[0], host)
        result = scope['result']['guest_wait_samples'][0]
        json.dumps(result)
        return result, reads

    def test_records_main_first_and_restores_original_thread(self):
        result, reads = self.run_sample()
        self.assertEqual([t['name'] for t in result['threads']], ['Game:Main', 'JobExecutor'])
        self.assertTrue(result['memory'])
        self.assertTrue(result['code'])
        self.assertLessEqual(result['read_bytes'], 32768)
        self.assertEqual(len(reads), len(set(reads)))
        self.assertEqual(result['threads'][0]['registers']['rbx'], '0x701000')

    def test_unreadable_memory_is_recorded_without_losing_thread_restoration(self):
        result, reads = self.run_sample(unreadable=True)
        self.assertTrue(result['errors'])
        self.assertFalse(result['memory'])
        self.assertTrue(reads)

    def test_time_budget_stops_optional_collection(self):
        result, reads = self.run_sample(tick=Mock(side_effect=[0, 2, 3]))
        self.assertTrue(result['limited'])
        self.assertFalse(reads)
        self.assertEqual(result['seconds'], 3)

    def test_preflight_accepts_gdb_cpp_names_and_rejects_missing_caller(self):
        sample, _ = self.run_sample()
        sample['threads'][0]['frames'][0]['symbol'] = 'waiting_caller()'
        after = dict(sample, phase='after')
        result = {'guest_wait_samples': [sample, after]}
        preflight.check_samples(result, fixture=True)
        sample['threads'][0]['frames'][0]['symbol'] = 'unrelated'
        with self.assertRaisesRegex(RuntimeError, 'caller'):
            preflight.check_samples(result, fixture=True)


if __name__ == '__main__':
    unittest.main()
