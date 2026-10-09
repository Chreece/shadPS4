#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only compute6 IRQ signal/trigger/dequeue origin instrumentation.

Target ONLY the private Ghost source checkout. Preserve the exact original
PM4, IRQ dispatch, event flags, triggered-bit and guest API behavior.
"""
from __future__ import annotations
import sys

GNM="src/core/libraries/gnmdriver/gnmdriver.cpp"
EQUEUE="src/core/libraries/kernel/equeue.cpp"
LIVERPOOL="src/video_core/amdgpu/liverpool.cpp"

def single(source, needle, replacement, label):
    count=source.count(needle)
    if count!=1:
        raise ValueError(f"{label}: expected exactly one source anchor, found {count}")
    return source.replace(needle,replacement,1)

def transform(files:dict[str,bytes]) -> dict[str,bytes]:
    if set(files)!={GNM,EQUEUE,LIVERPOOL}:
        raise ValueError("Exactly the three intended original files are required")
    g,eq,liv=(files[name].decode("utf-8") for name in (GNM,EQUEUE,LIVERPOOL))
    if any("GHOST_C6_" in text for text in (g,eq,liv)):
        raise ValueError("Refusing second instrumentation application")
    g=single(g, '#include "gnm_error.h"\n', '#include <atomic>\n#include "gnm_error.h"\n',
             "gnmdriver standard header")
    g_anchor=(
       '            equeue->TriggerEvent(static_cast<GnmEventType>(id),\n'
       '                                 OrbisKernelEvent::Filter::GraphicsCore,\n'
       '                                 reinterpret_cast<void*>(id));')
    g_hook=(
       '            if (id == 6) {\n'
       '                static std::atomic<u64> seq{0};\n'
       '                LOG_WARNING(Lib_GnmDriver,\n'
       '                            "GHOST_C6_IRQ_FORWARD seq={} eq={} id={}",\n'
       '                            seq.fetch_add(1) + 1, eq, id);\n'
       '            }\n' + g_anchor
    )
    g=single(g,g_anchor,g_hook,"Compute6 Gnm IRQ bridge")

    eq=single(eq,'#include <thread>\n','#include <atomic>\n#include <thread>\n',
              "equeue standard header")
    trigger='    m_cond.notify_one();\n    return has_found;\n'
    trigger_hook=(
       '    if (ident == 6 && filter == OrbisKernelEvent::Filter::GraphicsCore) {\n'
       '        static std::atomic<u64> seq{0};\n'
       '        LOG_WARNING(Kernel_Event,\n'
       '                    "GHOST_C6_TRIGGER seq={} eq={} found={} data={:#x}",\n'
       '                    seq.fetch_add(1) + 1, m_handle, has_found,\n'
       '                    reinterpret_cast<u64>(trigger_data));\n'
       '    }\n' + trigger
    )
    eq=single(eq,trigger,trigger_hook,"EqueueInternal::TriggerEvent")
    deque='            ev[count++] = it->event;\n'
    deque_hook=(
       '            if (it->event.filter == OrbisKernelEvent::Filter::GraphicsCore &&\n'
       '                it->event.ident == 6) {\n'
       '                static std::atomic<u64> seq{0};\n'
       '                LOG_WARNING(Kernel_Event,\n'
       '                            "GHOST_C6_DEQUEUE seq={} eq={} ident={} "\n'
       '                            "filter={} flags={:#x} data={} udata={:#x}",\n'
       '                            seq.fetch_add(1) + 1, m_handle, it->event.ident,\n'
       '                            static_cast<s32>(it->event.filter),\n'
       '                            it->event.flags, it->event.data,\n'
       '                            reinterpret_cast<u64>(it->event.udata));\n'
       '            }\n' + deque
    )
    eq=single(eq,deque,deque_hook,"EqueueInternal::GetTriggeredEvents")

    release=(
       '            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);\n'
       '            if (rasterizer) {\n'
    )
    release_hook=(
       '            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);\n'
       '            if (static_cast<u32>(queue.pipe_id) == 6 &&\n'
       '                release_mem->int_sel.Value() != InterruptSelect::None) {\n'
       '                static u64 seq = 0;\n'
       '                LOG_WARNING(Render,\n'
       '                            "GHOST_C6_RELEASE seq={} pipe={} int_sel={} data_sel={} "\n'
       '                            "dw1={:#x} dw2={:#x} addr_lo={:#x} addr_hi={:#x} "\n'
       '                            "data_lo={:#x} data_hi={:#x}",\n'
       '                            ++seq, static_cast<u32>(queue.pipe_id),\n'
       '                            static_cast<u32>(release_mem->int_sel.Value()),\n'
       '                            static_cast<u32>(release_mem->data_sel.Value()),\n'
       '                            release_mem->dw1, release_mem->dw2,\n'
       '                            release_mem->address_lo, release_mem->address_hi,\n'
       '                            release_mem->data_lo, release_mem->data_hi);\n'
       '            }\n'
       '            if (rasterizer) {\n'
    )
    liv=single(liv,release,release_hook,"compute PM4 release-memory path")
    output={GNM:g.encode(),EQUEUE:eq.encode(),LIVERPOOL:liv.encode()}
    for name,blob in output.items():
        if blob==files[name] or blob.count(b"GHOST_C6_")==0:
            raise ValueError(f"Instrumentation missing: {name}")
    if g.count("GHOST_C6_IRQ_FORWARD")!=1 or eq.count("GHOST_C6_TRIGGER")!=1 or eq.count("GHOST_C6_DEQUEUE")!=1 or liv.count("GHOST_C6_RELEASE")!=1:
        raise ValueError("Unexpected instrumentation count")
    return output

def selftest():
    g=(
      '#include "gnm_error.h"\n'
      'void x(){ equeue->TriggerEvent(static_cast<GnmEventType>(id),\n'
      '                                 OrbisKernelEvent::Filter::GraphicsCore,\n'
      '                                 reinterpret_cast<void*>(id)); }\n')
    eq=(
      '#include <thread>\n'
      'void EqueueInternal::TriggerEvent() { m_cond.notify_one();\n    return has_found;\n }\n'
      'void EqueueInternal::GetTriggeredEvents(){\n            ev[count++] = it->event;\n }\n')
    liv=(
      'void f(){\n'
      '            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);\n'
      '            if (rasterizer) {\n }\n }\n')
    originals={GNM:g.encode(),EQUEUE:eq.encode(),LIVERPOOL:liv.encode()}
    out=transform(originals)
    assert len(out)==3
    assert all(out[k]!=originals[k] for k in originals)
    assert 'if (id == 6)' in out[GNM].decode()
    assert 'flags={:#x}' in out[EQUEUE].decode()
    assert 'reinterpret_cast<u64>(it->event.udata)' in out[EQUEUE].decode()
    assert 'release_mem->data_sel.Value()' in out[LIVERPOOL].decode()
    assert all(b"TriggerEvent" in out[x] for x in (GNM,EQUEUE))
    assert b"ev[count++] = it->event;" in out[EQUEUE]
    assert b"if (rasterizer) {" in out[LIVERPOOL]
    for k in originals:
        mutated=dict(originals)
        mutated[k]=b'unexpected source layout\n'
        try:transform(mutated)
        except ValueError:pass
        else:raise AssertionError("Changed source anchor accepted "+k)
    try:transform(out)
    except ValueError:pass
    else:raise AssertionError("Double-patch unexpectedly allowed")
    print("SELFTEST PASS: 3 source anchors, 4 read-only C6 log sites, semantic statements unchanged, duplicate/mismatch guarded")

if __name__=="__main__":
    if sys.argv[1:]!=["--self-test"]:
        raise SystemExit("Usage: ghost-c6-irq-instrument-20261009.py --self-test")
    selftest()
