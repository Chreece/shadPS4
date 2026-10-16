#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests that the native DMA patcher targets outer C++ methods, not lambdas."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: test_dma_patch_scope.py <patcher.py>")
    spec = importlib.util.spec_from_file_location("dma_source_patcher", Path(sys.argv[1]))
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot import native DMA patcher")
    patcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(patcher)
    cases = [
        (
            "nested buffer invalidation",
            "void BufferCache::InvalidateMemory(VAddr device_addr, u64 size, bool assume_locks) {",
            """void BufferCache::InvalidateMemory(VAddr device_addr, u64 size, bool assume_locks) {
    memory_tracker->InvalidateRegion(device_addr, size, [this, device_addr, size, assume_locks] {
        ReadMemory(device_addr, size, true, false, assume_locks);
    });
}
int AnotherMethod() { return 0; }
""",
            "    memory_tracker->NotifyPendingDmaUpload();\n",
            "    });\n",
        ),
        (
            "indented inline CPU dirty method with comments and strings",
            "void MarkRegionAsCpuModified(VAddr cpu_addr, u64 size) noexcept {",
            """    void MarkRegionAsCpuModified(VAddr cpu_addr, u64 size) noexcept {
        // } ignored comment
        auto label = "{ curly } text";
        /* ignored { } block comment */
        IteratePages(cpu_addr, size, [](auto* manager) {
            manager->MarkChanged();
        });
    }
    void Other() {}
""",
            "        pending_dma_upload.Notify();\n",
            "        });\n",
        ),
        (
            "nested GPU readback conditional",
            "void UnmarkRegionAsGpuModified(VAddr cpu_addr, u64 size, bool is_write) noexcept {",
            """    void UnmarkRegionAsGpuModified(VAddr cpu_addr, u64 size, bool is_write) noexcept {
        IteratePages(cpu_addr, size, [is_write](auto* r) {
            if (is_write) { r->MarkChanged(); }
        });
    }
""",
            "        if (is_write) {\n            pending_dma_upload.Notify();\n        }\n",
            "        });\n",
        ),
    ]
    checks = 0
    for name, signature, source, insertion, inner in cases:
        modified = patcher.insert_before_method_close(source, signature, insertion)
        assert modified.count(insertion) == 1, f"{name}: insertion not unique"
        assert modified.index(insertion) > modified.index(inner), (
            f"{name}: inserted inside nested lambda/conditional"
        )
        assert modified.replace(insertion, "", 1) == source, (
            f"{name}: unrelated bytes modified"
        )
        checks += 3
        try:
            patcher.insert_before_method_close(modified, signature, insertion)
            raise AssertionError(f"{name}: accepted duplicate insertion")
        except ValueError:
            checks += 1
    try:
        signature, source, insertion = cases[0][1:4]
        patcher.insert_before_method_close(source + source, signature, insertion)
        raise AssertionError("Duplicated method signature accepted")
    except ValueError:
        checks += 1
    try:
        patcher.insert_before_method_close("void Example() {\n  if (true) {\n",
                                          "void Example() {", "    changed();\n")
        raise AssertionError("Unbalanced method braces accepted")
    except ValueError:
        checks += 1
    print(f"NATIVE_DMA_PATCH_SCOPE=PASS checks={checks} "
          "nested_lambdas=PASS indented_method=PASS ambiguous_rejected=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
