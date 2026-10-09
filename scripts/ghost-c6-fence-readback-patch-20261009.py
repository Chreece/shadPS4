#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Instrument Compute6's precise 64-bit progress slot at its PM4 write site.

The instrumentation only reads the guest slot and logs its before/after value.
The existing PM4 SignalFence call and all other executable behavior remain intact.
"""
from __future__ import annotations
import sys

START = '''            release_mem->SignalFence(
                [pipe_id = queue.pipe_id] {
                    Platform::IrqC::Instance()->Signal(static_cast<Platform::InterruptId>(pipe_id));
                },
                [this](VAddr dst, u16 gds_index, u16 num_dwords) {
                    rasterizer->CopyBuffer(dst, gds_index, num_dwords * sizeof(u32), false, true);
                });
'''
WRAPPED = '''            // GHOST_C6_SLOT_FENCE_READBACK: observe the actual PM4 fence write.
            const bool ghost_c6_slot_match =
                static_cast<u32>(queue.pipe_id) == 6 &&
                release_mem->Address<VAddr>() == 0x11000000b8ull;
            if (ghost_c6_slot_match) {
                LOG_WARNING(Render,
                            "GHOST_C6_SLOT_BEFORE addr={:#x} request={:#x} observed={:#x} "
                            "data_sel={} int_sel={}",
                            release_mem->Address<VAddr>(), release_mem->DataQWord(),
                            *release_mem->Address<u64*>(),
                            static_cast<u32>(release_mem->data_sel.Value()),
                            static_cast<u32>(release_mem->int_sel.Value()));
            }
''' + START + '''            if (ghost_c6_slot_match) {
                const u64 ghost_slot_after = *release_mem->Address<u64*>();
                LOG_WARNING(Render,
                            "GHOST_C6_SLOT_AFTER addr={:#x} request={:#x} observed={:#x} "
                            "matches_expected={}",
                            release_mem->Address<VAddr>(), release_mem->DataQWord(),
                            ghost_slot_after, ghost_slot_after == release_mem->DataQWord());
            }
'''


def transform(source: bytes) -> bytes:
    text = source.decode('utf-8')
    if 'GHOST_C6_RELEASE seq=' not in text:
        raise ValueError('Missing exact existing Compute6 read-only tracer')
    if 'GHOST_C6_SLOT_BEFORE' in text or 'GHOST_C6_SLOT_AFTER' in text:
        raise ValueError('Double instrumentation or a different source revision')
    if text.count(START) != 1:
        raise ValueError('Unexpected release_mem->SignalFence call-site count ' + str(text.count(START)))
    result = text.replace(START, WRAPPED, 1)
    if result.count(START) != 1:
        raise ValueError('Original fence dispatch was lost')
    if result.count('GHOST_C6_SLOT_BEFORE') != 1 or result.count('GHOST_C6_SLOT_AFTER') != 1:
        raise ValueError('Unexpected readback marker count')
    if result.count('0x11000000b8ull') != 1:
        raise ValueError('Exact fence slot match is missing')
    return result.encode('utf-8')


def selftest():
    fixture = ('#include "core/platform.h"\n' +
               'void run(){\n    // GHOST_C6_RELEASE seq=\n' + START + '}\n').encode()
    patched = transform(fixture)
    assert patched.count(b'GHOST_C6_SLOT_BEFORE') == 1
    assert patched.count(b'GHOST_C6_SLOT_AFTER') == 1
    assert patched.count(START.encode()) == 1
    assert patched.index(b'GHOST_C6_SLOT_BEFORE') < patched.index(START.encode()) < patched.index(b'GHOST_C6_SLOT_AFTER')
    for bad in (patched,fixture.replace(b'GHOST_C6_RELEASE seq=',b'other log'),
                fixture.replace(START.encode(),b'other fence call')):
        try:transform(bad)
        except ValueError: pass
        else: raise AssertionError('Invalid input was accepted')
    print('FENCE_READBACK_PATCH_SELFTEST_PASS=call_site,read_only_markers,ordering,guards')


if __name__ == '__main__':
    if sys.argv[1:] != ['--self-test']:
        raise SystemExit('Usage: ghost-c6-fence-readback-patch-20261009.py --self-test')
    selftest()
