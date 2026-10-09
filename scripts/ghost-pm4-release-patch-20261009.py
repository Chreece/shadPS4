#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Narrow PM4 RELEASE_MEM selector correction for isolated Ghost testing.

Mirror the legal None and IrqOnly semantics of EVENT_WRITE_EOP.
Retain all other selectors, unknown traps and IRQ behavior unchanged.
Capture the actual command fields whenever the new cases are reached.
"""
from __future__ import annotations
import sys

START = 'struct PM4CmdReleaseMem {'
STOP = 'struct PM4CmdSetBase {'
FUNC = '    void SignalFence(auto&& signal_irq, auto&& gds_to_mem) const {\n'
DATA_ANCHOR = '        case DataSelect::Data32Low: {\n'
INT_ANCHOR = '        case InterruptSelect::IrqUndocumented:\n'
MARK = 'GHOST_PM4_RELEASE_MEM'

def transform(src: bytes) -> bytes:
    original = src.decode('utf-8')
    if original.count(START) != 1 or original.count(STOP) != 1:
        raise ValueError('Expected exactly one PM4 RELEASE_MEM command structure')
    start = original.index(START)
    stop = original.index(STOP, start)
    section = original[start:stop]
    for anchor in (FUNC, DATA_ANCHOR, INT_ANCHOR, '        switch (data_sel.Value()) {\n',
                   '        switch (int_sel.Value()) {\n'):
        if section.count(anchor) != 1:
            raise ValueError('Unexpected RELEASE_MEM anchor count: ' + repr(anchor))
    if MARK in section or 'case DataSelect::None:' in section or 'case InterruptSelect::IrqOnly:' in section:
        raise ValueError('RELEASE_MEM is already patched or has different selector handling')
    if section.count('            UNREACHABLE();') != 2:
        raise ValueError('Expected BOTH original unknown-selector traps to remain')
    instrumentation = (
        '        // GHOST_PM4_RELEASE_MEM: evidence + guarded correction for legal selectors.\n'
        '        const u32 ghost_data_select = static_cast<u32>(data_sel.Value());\n'
        '        const u32 ghost_interrupt_select = static_cast<u32>(int_sel.Value());\n'
        '        if (ghost_data_select == 0u || ghost_data_select > 5u ||\n'
        '            ghost_interrupt_select == 1u || ghost_interrupt_select > 3u) {\n'
        '            LOG_WARNING(Render_Vulkan,\n'
        '                        "GHOST_PM4_RELEASE_MEM data_sel={} int_sel={} "\n'
        '                        "dw1={:#x} dw2={:#x} addr_lo={:#x} addr_hi={:#x} "\n'
        '                        "data_lo={:#x} data_hi={:#x}",\n'
        '                        ghost_data_select, ghost_interrupt_select, dw1, dw2,\n'
        '                        address_lo, address_hi, data_lo, data_hi);\n'
        '        }\n'
    )
    section = section.replace(FUNC, FUNC + instrumentation, 1)
    section = section.replace(DATA_ANCHOR,
        '        case DataSelect::None: {\n'
        '            // Legal RELEASE_MEM without a memory write.\n'
        '            break;\n'
        '        }\n' + DATA_ANCHOR, 1)
    section = section.replace(INT_ANCHOR,
        '        case InterruptSelect::IrqOnly: {\n'
        '            // Same semantics as EVENT_WRITE_EOP: IRQ without fence data.\n'
        '            ASSERT(data_sel == DataSelect::None);\n'
        '            signal_irq();\n'
        '            break;\n'
        '        }\n' + INT_ANCHOR, 1)
    assert section.count(MARK) == 2
    assert section.count('            UNREACHABLE();') == 2
    assert section.count('case DataSelect::None:') == 1
    assert section.count('case InterruptSelect::IrqOnly:') == 1
    assert section.count('case DataSelect::GdsMemStore:') == 1
    return (original[:start] + section + original[stop:]).encode('utf-8')

def selftest():
    fixture = (
        '#pragma once\n' + START + '\n' + FUNC
        + '        switch (data_sel.Value()) {\n'
        + DATA_ANCHOR + '            do_data32();\n            break;\n        }\n'
        + '        case DataSelect::GdsMemStore: {\n'
        + '            write_gds();\n            break;\n        }\n'
        + '        default: {\n            UNREACHABLE();\n        }\n        }\n'
        + '        switch (int_sel.Value()) {\n'
        + '        case InterruptSelect::None: {\n            break;\n        }\n'
        + INT_ANCHOR + '            [[fallthrough]];\n'
        + '        case InterruptSelect::IrqWhenWriteConfirm: {\n'
        + '            signal_irq();\n            break;\n        }\n'
        + '        default: {\n            UNREACHABLE();\n        }\n        }\n'
        + '    }\n};\n' + STOP + '\n};\n'
    ).encode()
    changed = transform(fixture)
    text = changed.decode()
    assert text.count('"GHOST_PM4_RELEASE_MEM data_sel={} int_sel={} "') == 1
    assert text.count('UNREACHABLE();') == 2
    assert 'ASSERT(data_sel == DataSelect::None);' in text
    assert '        case DataSelect::GdsMemStore: {' in text
    assert '        case InterruptSelect::IrqWhenWriteConfirm: {' in text
    assert 'do_data32()' in text
    try: transform(changed)
    except ValueError: pass
    else: raise AssertionError('A second patch was silently accepted')
    for damaged in (fixture.replace(DATA_ANCHOR.encode(), b''),
                    fixture.replace(INT_ANCHOR.encode(), b''),
                    fixture.replace(b'            UNREACHABLE();', b'', 1)):
        try: transform(damaged)
        except ValueError: pass
        else: raise AssertionError('Malformed source was accepted')
    print('SELFTEST PASS: no-data/no-write, IRQ-only/irq, previous cases unchanged, defaults fatal, double patch rejected')

if __name__ == '__main__':
    if sys.argv[1:] != ['--self-test']:
        raise SystemExit('Usage: ghost-pm4-release-patch-20261009.py --self-test')
    selftest()
