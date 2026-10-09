#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reversible native PM4 RELEASE_MEM data/interrupt selector trial.

Observed 2026-10-09 Ghost with the 64-repeat pixel-shader experiment:
RADV no longer reported device loss, but shadPS4 trapped in
PM4CmdReleaseMem::SignalFence at pm4_cmds.h:975 (data_sel default).
The enum *already* defines DataSelect::None = 0, and the adjacent
PM4CmdEventWriteEop handler correctly performs no write for this value.
RELEASE_MEM lacks that case and also omits documented InterruptSelect::IrqOnly.

A no-data-write RELEASE_MEM must not dereference a guest address.
The interrupt selection is processed afterward as before. IrqOnly only
signals the interrupt if data_sel is None, matching EventWriteEop.
Unknown modes still hit the original UNREACHABLE: do not mask malformed
commands. Log the actual raw packet (first 96 and any unknown) from
Liverpool, allowing the next run to prove which selector caused the trap.

Changes ONLY pm4_cmds.h and liverpool.cpp, kept on an experimental
test branch. Automatic enclosing shell backs up/restores both sources
and cached/installed binaries; no guest save, driver or global config edits.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PM4_REL = "src/video_core/amdgpu/pm4_cmds.h"
LIVERPOOL_REL = "src/video_core/amdgpu/liverpool.cpp"
BASELINE_BLOBS = {
    PM4_REL: "f4a07025a7395262c8868f49c4aeb7d1f970ac60",
    LIVERPOOL_REL: "302c94cba0a90bd5ba8fccb260b8d59c9372773d",
}
DATA_OLD = """    void SignalFence(auto&& signal_irq, auto&& gds_to_mem) const {
        switch (data_sel.Value()) {
        case DataSelect::Data32Low: {"""
DATA_NEW = """    void SignalFence(auto&& signal_irq, auto&& gds_to_mem) const {
        switch (data_sel.Value()) {
        // GHOST_RELEASE_MEM_NONE: no data destination and no memory write.
        // The subsequent int_sel switch still decides whether to signal IRQ.
        case DataSelect::None: {
            break;
        }
        case DataSelect::Data32Low: {"""
IRQ_OLD = """        switch (int_sel.Value()) {
        case InterruptSelect::None: {
            // No interrupt
            break;
        }
        case InterruptSelect::IrqUndocumented:
            [[fallthrough]];"""
IRQ_NEW = """        switch (int_sel.Value()) {
        case InterruptSelect::None: {
            // No interrupt
            break;
        }
        // GHOST_RELEASE_MEM_IRQ_ONLY: same rule as PM4CmdEventWriteEop.
        case InterruptSelect::IrqOnly: {
            ASSERT(data_sel == DataSelect::None);
            signal_irq();
            break;
        }
        case InterruptSelect::IrqUndocumented:
            [[fallthrough]];"""
INCLUDE_OLD = """#include <boost/preprocessor/stringize.hpp>"""
INCLUDE_NEW = """#include <atomic>
#include <boost/preprocessor/stringize.hpp>"""
PROCESS_OLD = """        case PM4ItOpcode::ReleaseMem: {
            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);
            if (rasterizer) {"""
PROCESS_NEW = """        case PM4ItOpcode::ReleaseMem: {
            const auto* release_mem = reinterpret_cast<const PM4CmdReleaseMem*>(header);
            // GHOST_RELEASE_MEM_PACKET: bounded full packet-selector diagnostics
            // on the real guest command, before SignalFence dispatch.
            static std::atomic<u32> ghost_release_mem_logged{0};
            const u32 release_seq =
                ghost_release_mem_logged.fetch_add(1, std::memory_order_relaxed);
            const u32 data_mode = static_cast<u32>(release_mem->data_sel.Value());
            const u32 irq_mode = static_cast<u32>(release_mem->int_sel.Value());
            if (release_seq < 96 || data_mode > 5 || irq_mode > 3) {
                LOG_WARNING(Lib_GnmDriver,
                            "GHOST_RELEASE_MEM_PACKET seq={} data_sel={} int_sel={} "
                            "event={} index={} dst_sel={} dw1={:#x} dw2={:#x} "
                            "addr_lo={:#x} addr_hi={:#x} data_lo={:#x} data_hi={:#x}",
                            release_seq, data_mode, irq_mode,
                            static_cast<u32>(release_mem->event_type.Value()),
                            static_cast<u32>(release_mem->event_index.Value()),
                            static_cast<u32>(release_mem->dst_sel.Value()),
                            release_mem->dw1, release_mem->dw2,
                            release_mem->address_lo, release_mem->address_hi,
                            release_mem->data_lo, release_mem->data_hi);
            }
            if (rasterizer) {"""


def replace_once(source: str, original: str, replacement: str, label: str) -> str:
    found = source.count(original)
    if found != 1:
        raise ValueError(f"{label}: expected one exact legacy anchor, got {found}")
    return source.replace(original, replacement, 1)


def transform(pm4: str, liverpool: str) -> tuple[str, str]:
    if "GHOST_RELEASE_MEM_NONE" in pm4 or "GHOST_RELEASE_MEM_PACKET" in liverpool:
        raise ValueError("Already-patched PM4 release-mem source")
    if pm4.count("enum class DataSelect : u32 {") != 1 or \
       pm4.count("    None = 0,") < 2:
        raise ValueError("Unexpected PM4 selector enumeration layout")
    if "struct PM4CmdReleaseMem {" not in pm4 or \
       "struct PM4CmdEventWriteEop {" not in pm4:
        raise ValueError("Legacy release/eop command definitions missing")
    if "    IrqOnly = 1," not in pm4:
        raise ValueError("Explicit InterruptSelect::IrqOnly enum missing")
    # The adjacent EOP implementation must already support no-write and IRQ-only.
    eop = pm4[pm4.index("struct PM4CmdEventWriteEop {"):
              pm4.index("struct PM4CmdAcquireMem {")]
    if "case DataSelect::None:" not in eop or \
       "case InterruptSelect::IrqOnly:" not in eop:
        raise ValueError("Reference EOP fence semantics no longer match")
    output_pm4 = replace_once(pm4, DATA_OLD, DATA_NEW, "ReleaseMem data selector")
    output_pm4 = replace_once(output_pm4, IRQ_OLD, IRQ_NEW, "ReleaseMem IRQ selector")
    output_liverpool = replace_once(liverpool, INCLUDE_OLD, INCLUDE_NEW, "atomic include")
    output_liverpool = replace_once(output_liverpool, PROCESS_OLD, PROCESS_NEW,
                                    "ReleaseMem dispatch diagnostic")
    release = output_pm4[output_pm4.index("struct PM4CmdReleaseMem {"):]
    assert release.count("case DataSelect::None:") == 1
    assert release.count("case InterruptSelect::IrqOnly:") == 1
    assert "ASSERT(data_sel == DataSelect::None);" in release
    assert "case DataSelect::GdsMemStore:" in release
    assert release.count("UNREACHABLE();") >= 2
    assert output_liverpool.count("GHOST_RELEASE_MEM_PACKET seq=") == 1
    assert output_liverpool.count("static std::atomic<u32> ghost_release_mem_logged") == 1
    assert "if (rasterizer) {" in output_liverpool
    assert pm4[:pm4.index("struct PM4CmdReleaseMem {")] == \
        output_pm4[:output_pm4.index("struct PM4CmdReleaseMem {")]
    return output_pm4, output_liverpool


def test() -> None:
    pm4 = (
        "enum class DataSelect : u32 {\\n    None = 0,\\n    Data32Low = 1,\\n};\\n"
        "enum class InterruptSelect : u32 {\\n    None = 0,\\n    IrqOnly = 1,\\n};\\n"
        "struct PM4CmdEventWriteEop {\\n"
        "  case DataSelect::None:\\n  case InterruptSelect::IrqOnly:\\n"
        "};\\nstruct PM4CmdAcquireMem {};\\n"
        "struct PM4CmdReleaseMem {\\n"
        + DATA_OLD + "\\n" + IRQ_OLD + "\\n"
        "case DataSelect::GdsMemStore:\\n"
        "default: UNREACHABLE(); default: UNREACHABLE();\\n};\\n"
    )
    liv = INCLUDE_OLD + "\\n" + PROCESS_OLD + "\\n"
    edited_pm4, edited_liv = transform(pm4, liv)
    assert "GHOST_RELEASE_MEM_NONE" in edited_pm4
    assert "GHOST_RELEASE_MEM_IRQ_ONLY" in edited_pm4
    assert "GHOST_RELEASE_MEM_PACKET" in edited_liv
    assert "data_mode > 5" in edited_liv
    assert edited_pm4.count("case DataSelect::None:") == 2
    for bad_pm4, bad_liv in (
        (edited_pm4, liv),
        (pm4.replace(DATA_OLD, ""), liv),
        (pm4, edited_liv),
        (pm4.replace("case InterruptSelect::IrqOnly:", "INVALID"), liv),
    ):
        try:
            transform(bad_pm4, bad_liv)
        except ValueError:
            pass
        else:
            raise AssertionError("Bad or duplicate source accepted")
    # Executable selector semantics, independent of the hardware:
    calls = []
    def fence(data_selector: int, interrupt_selector: int):
        if data_selector == 0:
            pass  # no memory write
        elif data_selector in (1, 2, 3, 4, 5):
            calls.append("write")
        else:
            raise ValueError("unknown selector")
        if interrupt_selector == 1:
            assert data_selector == 0
            calls.append("irq")
        elif interrupt_selector in (2, 3):
            calls.append("irq")
        elif interrupt_selector != 0:
            raise ValueError("unknown irq")
    fence(0, 0)
    assert calls == []
    fence(0, 1)
    assert calls == ["irq"]
    fence(1, 2)
    assert calls == ["irq", "write", "irq"]
    try:
        fence(6, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown data mode silently accepted")
    print("SELFTEST PASS: original EOP selector correspondence, RELEASE_MEM "
          "no-write and IRQ-only, existing writes/IRQ unchanged, unknown "
          "mode rejection, fail-closed dual-source anchors")


def git_blob(path: Path) -> str:
    return subprocess.check_output(
        ["git", "hash-object", str(path)], text=True).strip()


def main() -> None:
    if sys.argv[1:] == ["--self-test"]:
        test()
        return
    if len(sys.argv) not in (3, 4):
        raise SystemExit("Usage: trial.py ROOT BACKUP_DIR [--check-only]")
    root = Path(sys.argv[1]).resolve()
    backup = Path(sys.argv[2]).resolve()
    check = len(sys.argv) == 4 and sys.argv[3] == "--check-only"
    if len(sys.argv) == 4 and not check:
        raise SystemExit("Unknown option")
    paths = [root / PM4_REL, root / LIVERPOOL_REL]
    for name, path in zip((PM4_REL, LIVERPOOL_REL), paths):
        if path.is_symlink() or not path.is_file() or \
           git_blob(path) != BASELINE_BLOBS[name]:
            raise ValueError("Unexpected or modified source (fail-closed): " + name)
    originals = [p.read_bytes() for p in paths]
    updated = transform(*(b.decode("utf-8") for b in originals))
    test()
    if check:
        print("GHOST_RELEASE_MEM_NONE_CHECK_PASS: both pinned source blobs, "
              "legal selectors and diagnostics verified; no writes")
        return
    # Save BOTH unmodified files and verify before modifying EITHER.
    for name, raw in zip((PM4_REL, LIVERPOOL_REL), originals):
        saved = backup / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        if saved.exists():
            raise RuntimeError("Refusing preexisting source backup: " + str(saved))
        saved.write_bytes(raw)
        if saved.read_bytes() != raw:
            raise IOError("Source backup failed: " + str(saved))
    try:
        for path, new in zip(paths, updated):
            temp = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=path.parent,
                    prefix=".ghost-release-mem-", suffix=".tmp", delete=False
                ) as out:
                    temp = Path(out.name)
                    out.write(new)
                os.chmod(temp, path.stat().st_mode & 0o777)
                os.replace(temp, path)
                temp = None
            finally:
                if temp is not None and temp.exists():
                    temp.unlink()
            if path.read_text(encoding="utf-8") != new:
                raise IOError("Modified source verification failed: " + str(path))
            print("GHOST_RELEASE_MEM_FILE_PATCHED=" + str(path))
    except BaseException:
        # The shell EXIT trap still restores both if anything fails.
        raise
    print("GHOST_RELEASE_MEM_NONE_PATCH=APPLIED: diagnostic no-write "
          "RELEASE_MEM handling, IRQ-only + exact raw packet logging")


if __name__ == "__main__":
    main()
