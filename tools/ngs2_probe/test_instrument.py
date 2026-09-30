#!/usr/bin/env python3
import unittest
from instrument import HOOKS, SPAM, instrument

class InstrumentTest(unittest.TestCase):
    def source(self):
        out = []
        for n in HOOKS:
            log = 'LOG_ERROR(Lib_Ngs2, "called");'
            if n == 'sceNgs2CalcWaveformBlock': log = SPAM[0]
            if n == 'sceNgs2ParseWaveformData': log = SPAM[1]
            if n == 'sceNgs2VoiceGetState': log = SPAM[2]
            out.append(f's32 PS4_SYSV_ABI {n}(void* arg) {{\n    {log}\n    return ORBIS_OK;\n}}\n')
        return ''.join(out)
    def test_insert_once_and_preserve_returns(self):
        src = self.source()
        got = instrument(src)
        self.assertEqual(got.count('return ORBIS_OK;'), len(HOOKS))
        self.assertEqual(got.count('if (!Ngs2Probe::Enabled())'), 5)
        for hook in HOOKS.values(): self.assertEqual(got.count(hook), 1)
        self.assertNotIn('probe.h', src)
    def test_reject_duplicate_instrumentation(self):
        with self.assertRaises(ValueError): instrument(instrument(self.source()))
    def test_reject_missing_symbol(self):
        with self.assertRaises(ValueError): instrument(self.source().replace('sceNgs2SystemRender','missing'))
    def test_reject_duplicate_symbol(self):
        with self.assertRaises(ValueError): instrument(self.source()+self.source())
    def test_reject_changed_stub(self):
        with self.assertRaises(ValueError): instrument(self.source().replace(SPAM[0],'changed();'))

if __name__ == '__main__': unittest.main()
