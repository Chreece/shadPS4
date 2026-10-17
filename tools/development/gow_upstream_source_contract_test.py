#!/usr/bin/env python3
"""Both acceptance and fail-closed tests for the source-contract gate."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

CONTRACT = Path(__file__).with_name("gow_upstream_source_contract.py")
SPEC = importlib.util.spec_from_file_location("gow_source_contract", CONTRACT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

FIXTURE = {
    "src/core/libraries/kernel/module_filename_validation.h":
        "ORBIS_KERNEL_ERROR_EFAULT ORBIS_KERNEL_ERROR_ENOENT",
    "src/core/libraries/kernel/process.cpp": """
#if defined(__x86_64__)
__attribute__((force_align_arg_pointer))
#endif
sceKernelLoadStartModule(
const s32 filename_result = ValidateModuleFilename(moduleFileName);
LOG_INFO(Lib_Kernel, "called filename = {}, args = {}", moduleFileName, args);
""",
    "src/video_core/page_manager.cpp": """
struct SignalImpl : public PageManager::Impl {
    inline static SignalImpl* instance{};
    SignalImpl() { instance = this;
      RegisterAccessViolationHandler(GuestFaultSignalHandler);
    }
    static bool GuestFaultSignalHandler() { return instance->cached_pages.find(0); }
};
PageManager::PageManager() {}
pending_gpu_invalidations.fetch_add(1);
pending_gpu_invalidations.fetch_sub(1);
""",
    "src/video_core/page_manager.h": "enum class MemoryPermission : u32;\n",
    "src/video_core/renderer_vulkan/vk_rasterizer.h": "enum class MemoryPermission : u32;\n",
    "src/core/libraries/kernel/threads/pthread.cpp": """
#include "core/libraries/kernel/process.h"
new_thread->affinity_mask.store(mask, std::memory_order_release);
native_thr->Create(RunThread, new_thread, mask, std::move(affinity));
""",
    "src/video_core/renderer_vulkan/vk_rasterizer.cpp":
        "void InvalidateMemoryOnGpuThread() {}\n",
    "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp": """
case IR::Opcode::Phi: info.uses_fp64 = true;
case IR::Opcode::ReadConst: info.uses_dma = true;
info.readconst_types |= Info::ReadConstType::Dynamic;
case IR::Opcode::DataAppend: break;
""",
    "src/shader_recompiler/ir/opcodes.inc": """
OPCODE(Phi, U32)
OPCODE(ReadConst, U32)
OPCODE(DataAppend, U32)
""",
}


def populate(root):
    for name, content in FIXTURE.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


class SourceContractTest(unittest.TestCase):
    def test_current_source_passes(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            self.assertEqual(module.validate(root), (3, 3))
            p = subprocess.run([sys.executable, str(CONTRACT), str(root)],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("UPSTREAM_NATIVE_SOURCE_CONTRACT=PASS", p.stdout)

    def test_missing_guest_hle_module_validation_fails(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            file = root / "src/core/libraries/kernel/process.cpp"
            file.write_text(file.read_text().replace(
                "ValidateModuleFilename(moduleFileName)", "NO_FILENAME_VALIDATION"))
            with self.assertRaisesRegex(RuntimeError, "guest module filename must be validated"):
                module.validate(root)

    def test_missing_guest_hle_stack_alignment_fails(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            file = root / "src/core/libraries/kernel/process.cpp"
            file.write_text(file.read_text().replace("force_align_arg_pointer", "BAD_ABI"))
            with self.assertRaisesRegex(RuntimeError, "stack realignment is missing"):
                module.validate(root)

    def test_missing_signal_owner_fails(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            file = root / "src/video_core/page_manager.cpp"
            file.write_text(file.read_text().replace("inline static SignalImpl* instance{};", ""))
            with self.assertRaisesRegex(RuntimeError, "static callback owner"):
                module.validate(root)

    def test_missing_memory_permission_decl_fails(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            (root / "src/video_core/page_manager.h").write_text("struct Broken {};\n")
            with self.assertRaisesRegex(RuntimeError, "forward declaration missing"):
                module.validate(root)

    def test_old_wrong_native_thread_api_fails(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            file = root / "src/core/libraries/kernel/threads/pthread.cpp"
            file.write_text(file.read_text().replace(
                "native_thr->Create(RunThread, new_thread, mask, std::move(affinity))",
                "native_thr->Create(RunThread, new_thread)"))
            with self.assertRaisesRegex(RuntimeError, "inherited affinity"):
                module.validate(root)

    def test_four_old_gds_opcodes_fail_with_names(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            file = root / "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp"
            with file.open("a") as stream:
                stream.write("\n".join(
                    f"case IR::Opcode::{name}: break;"
                    for name in ("GdsOrderedCount", "GdsOrderedSignal",
                                 "BufferOrderedCount", "BufferOrderedSignal")))
            with self.assertRaisesRegex(RuntimeError, "GdsOrderedCount.*GdsOrderedSignal"):
                module.validate(root)

    def test_empty_catalog_fails(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            (root / "src/shader_recompiler/ir/opcodes.inc").write_text("")
            with self.assertRaisesRegex(RuntimeError, "opcode catalog is empty"):
                module.validate(root)

    def test_disabling_dma_fails(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            populate(root)
            file = root / "src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp"
            file.write_text(file.read_text() + "\ninfo.uses_dma = false;\n")
            with self.assertRaisesRegex(RuntimeError, "DMA is incorrectly suppressed"):
                module.validate(root)

    def test_reference_parser_handles_whitespace_and_complex_opcode_types(self):
        txt = "  OPCODE(ReadConstBuffer, U32, Opaque, U32)\nOPCODE( DataAppend, U32, U1)\n"
        self.assertEqual(module.opcode_definitions(txt), {"ReadConstBuffer", "DataAppend"})
        cases = "  case IR::Opcode::ReadConstBuffer:\ncase IR::Opcode::DataAppend:"
        self.assertEqual(module.opcode_uses(cases), {"ReadConstBuffer", "DataAppend"})


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SourceContractTest))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print(f"UPSTREAM_SOURCE_CONTRACT_TESTS=PASS cases={result.testsRun}")
