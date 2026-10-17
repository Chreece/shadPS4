#!/usr/bin/env python3
"""Upstream GoW native source contract; run before compilation.

This checks cross-component invariants and opcode compatibility. Passing it is
not a claim that C++ compilation, linking, or gameplay succeeded.
"""
import pathlib
import sys


def required(ok: bool, message: str) -> None:
    if not ok:
        raise RuntimeError(f"SOURCE_CONTRACT_FAIL: {message}")


def opcode_definitions(source: str) -> set[str]:
    result = set()
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("OPCODE("):
            name = stripped[len("OPCODE("):].split(",", 1)[0].strip()
            required(name.isidentifier(), f"invalid opcode declaration: {stripped[:100]}")
            result.add(name)
    return result


def opcode_uses(source: str) -> set[str]:
    result = set()
    for line in source.splitlines():
        marker = "case IR::Opcode::"
        if marker in line:
            name = line.partition(marker)[2].split(":", 1)[0].strip()
            required(name.isidentifier(), f"invalid opcode case: {line[:100]}")
            result.add(name)
    return result


def validate(root: pathlib.Path) -> tuple[int, int]:
    def read(relative: str) -> str:
        path = root / relative
        required(path.is_file(), f"missing source: {relative}")
        return path.read_text(encoding="utf-8")

    pm = read("src/video_core/page_manager.cpp")
    signal_impl = pm.partition("struct SignalImpl : public PageManager::Impl {")[2].partition(
        "PageManager::PageManager(")[0]
    required(bool(signal_impl), "SignalImpl class not found")
    required("inline static SignalImpl* instance" in signal_impl,
             "SignalImpl static callback owner pointer missing")
    required("instance->cached_pages" in signal_impl,
             "SignalImpl callback lost guarded page-state lookup")
    assigned = signal_impl.find("instance = this;")
    registered = signal_impl.find("RegisterAccessViolationHandler(")
    required(assigned != -1 and registered != -1 and assigned < registered,
             "SignalImpl owner must be initialized before callback registration")

    for header in ("src/video_core/page_manager.h",
                   "src/video_core/renderer_vulkan/vk_rasterizer.h"):
        required("enum class MemoryPermission : u32;" in read(header),
                 f"Core::MemoryPermission forward declaration missing from {header}")

    # Native CPUID/RDTSCP interception can raise a nested SIGSEGV when
    # emulator-host timing or logging executes inside another signal handler.
    # This exact Linux signal policy existed in the gameplay-capable source.
    signal_source = read("src/core/signals.cpp")
    signal_setup = signal_source.partition("SignalDispatch::SignalDispatch()")[2].partition(
        "void SignalDispatch::RemoveHandlers()")[0]
    required(bool(signal_setup), "native CPU signal handler registration missing")
    required("sigaddset(&action.sa_mask, SIGUSR1);" in signal_setup,
             "guest SIGUSR1 callbacks must be masked during CPU instruction faults")
    sigflags = signal_setup.find("action.sa_flags |= SA_NODEFER;")
    required(sigflags != -1 and
             signal_setup.find("sigaction(SIGSEGV, &action, nullptr)", sigflags) > sigflags and
             signal_setup.find("sigaction(SIGILL, &action, nullptr)", sigflags) > sigflags,
             "native CPU nested SIGSEGV/SIGILL policy lost in upstream rebase")
    required("#if defined(__linux__) && defined(ARCH_X86_64)" in
             signal_setup[:sigflags] if sigflags >= 0 else False,
             "nested CPU faults must be Linux x86-64-only")

    pthread = read("src/core/libraries/kernel/threads/pthread.cpp")
    required('#include "core/libraries/kernel/process.h"' in pthread,
             "pthread.cpp lacks GetGuestCpuMask declaration")
    required("native_thr->Create(RunThread, new_thread, mask, std::move(affinity))" in pthread,
             "native thread creation does not supply mask and inherited affinity")
    required("new_thread->affinity_mask.store(mask" in pthread,
             "virtual CPU identity mask must be set before guest thread starts")

    kernel = read("src/core/libraries/kernel/process.cpp")
    guest_module_header = read("src/core/libraries/kernel/module_filename_validation.h")
    hle_entry = kernel.partition("sceKernelLoadStartModule(")[2]
    required(bool(hle_entry), "missing sceKernelLoadStartModule HLE entry")
    validation = hle_entry.find("ValidateModuleFilename(moduleFileName)")
    unguarded_log = hle_entry.find('LOG_INFO(Lib_Kernel, "called filename = {}')
    required(validation != -1 and unguarded_log != -1 and validation < unguarded_log,
             "guest module filename must be validated before host fmt/string operations")
    required("force_align_arg_pointer" in kernel.partition("sceKernelLoadStartModule(")[0],
             "proven x86-64 guest HLE stack realignment is missing")
    required("ORBIS_KERNEL_ERROR_EFAULT" in guest_module_header and
             "ORBIS_KERNEL_ERROR_ENOENT" in guest_module_header,
             "invalid guest module filenames must return the correct API errors")

    rasterizer = read("src/video_core/renderer_vulkan/vk_rasterizer.cpp")
    required("InvalidateMemoryOnGpuThread(" in rasterizer,
             "deferred UFFD GPU callback implementation missing")
    required("pending_gpu_invalidations.fetch_add(1)" in pm and
             "pending_gpu_invalidations.fetch_sub(1)" in pm,
             "deferred UFFD fault queue missing enqueue or completion accounting")

    collector = read("src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp")
    known = opcode_definitions(read("src/shader_recompiler/ir/opcodes.inc"))
    used = opcode_uses(collector)
    required(bool(known), "current upstream opcode catalog is empty")
    required(bool(used), "shader info collector contains no opcode cases")
    unknown = sorted(used - known)
    required(not unknown, "shader collector references undefined upstream IR opcodes: " +
             ", ".join(unknown))
    required("case IR::Opcode::Phi:" in collector and "info.uses_fp64 = true;" in collector,
             "ported SRT Phi-FP64 capability tracking is missing")
    required("case IR::Opcode::ReadConst:" in collector and
             "info.readconst_types |= Info::ReadConstType::Dynamic" in collector and
             "info.uses_dma = true" in collector,
             "unflattened ReadConst must preserve DMA backing")
    required("info.uses_dma = false" not in collector,
             "unflattened ReadConst DMA is incorrectly suppressed")

    return len(used), len(known)


def main() -> int:
    required(len(sys.argv) == 2, "usage: gow_upstream_source_contract.py <source-tree>")
    used, known = validate(pathlib.Path(sys.argv[1]))
    print(f"SHADER_UPSTREAM_IR_COMPATIBILITY=PASS referenced={used} known={known}")
    print("UPSTREAM_NATIVE_SOURCE_CONTRACT=PASS")
    print("SOURCE_CONTRACT_NOTE=Static checks only; C++ compilation and gameplay still required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
