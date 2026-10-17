// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>
#include <asm/prctl.h>
#include <cpuid.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "core/cpu_id_translation/launcher.h"
#include "core/cpu_id.h"

#ifdef ENABLE_CPU_ID_TRANSLATION
extern "C" int ShadCpuIdTranslationActive();
#endif

namespace Core {
namespace {

#ifdef ENABLE_CPU_ID_TRANSLATION
constexpr const char* RestartMarker = "SHADPS4_CPU_ID_RESTART";

bool NeedsTranslation() {
    unsigned int eax, ebx, ecx, edx;
    const bool rdpid = __get_cpuid_count(7, 0, &eax, &ebx, &ecx, &edx) && (ecx & (1u << 22));
    const bool cpuid_faulting = syscall(SYS_arch_prctl, ARCH_SET_CPUID, 1) == 0;
    const bool tsc_faulting = prctl(PR_SET_TSC, PR_TSC_ENABLE) == 0;
    return rdpid || !cpuid_faulting || !tsc_faulting;
}
#endif

} // namespace

bool StartCpuIdTranslation(int argc, char* argv[], std::string_view mode, bool run_guest) {
    if (setenv("SHADPS4_CPU_ID_MODE", std::string(mode).c_str(), 1) != 0) {
        std::fprintf(stderr, "Cannot preserve CPU identity mode: %s\n", std::strerror(errno));
        return false;
    }
#ifdef ENABLE_CPU_ID_TRANSLATION
    if (ShadCpuIdTranslationActive()) {
        unsetenv(RestartMarker);
        return true;
    }
#endif
    if (!run_guest) {
        return true;
    }
#ifdef ENABLE_CPU_ID_TRANSLATION
    if (std::getenv(RestartMarker)) {
        std::fputs("CPU identity translation did not start; refusing to restart again.\n", stderr);
        return false;
    }
#endif
    if (mode == "builtin"
#ifndef ENABLE_CPU_ID_TRANSLATION
        || mode == "auto"
#endif
    ) {
        // Execute normal host x86-64 code directly, while enforcing PS4
        // CPUID/RDTSCP identity via the existing Linux per-guest-thread fault
        // handlers and short RDPID via the emulator's native CPU patcher.
        // Does not start DynamoRIO and does not silently fall back to host
        // TSC_AUX identity.
        SetBuiltinCpuIdMode(true);
        return true;
    }
    if (mode == "native") {
        return true;
    }
#ifdef ENABLE_CPU_ID_TRANSLATION
    if (mode == "auto" && !NeedsTranslation()) {
        return true;
    }

    std::error_code error;
    const auto executable = std::filesystem::read_symlink("/proc/self/exe", error);
    if (error) {
        std::fprintf(stderr, "Cannot locate the emulator: %s\n", error.message().c_str());
        return false;
    }
    auto runtime = executable.parent_path() / "cpu-id-runtime";
    if (!std::filesystem::is_directory(runtime, error)) {
        runtime = executable.parent_path() / "../libexec/shadps4/cpu-id-runtime";
    }
    const auto runner = runtime / "bin64/drrun";
    const auto client = runtime / "libshadps4_cpu_id.so";
    for (const auto& file : {runner, client, runtime / "lib64/release/libdynamorio.so"}) {
        if (!std::filesystem::is_regular_file(file, error)) {
            std::fprintf(stderr,
                         "CPU identity runtime is missing: %s\n"
                         "Install the complete emulator package, or explicitly select "
                         "--cpu-id-mode=native for limited generated-instruction coverage.\n",
                         file.c_str());
            return false;
        }
    }
    if (setenv(RestartMarker, "1", 1) != 0) {
        std::fprintf(stderr, "Cannot prepare CPU identity translation: %s\n", std::strerror(errno));
        return false;
    }
    std::vector<std::string> command{runner.string(),
                                     "-root",
                                     runtime.string(),
                                     "-use_dll",
                                     (runtime / "lib64/release/libdynamorio.so").string(),
                                     "-disable_rseq",
                                     "-no_follow_children",
                                     "-vm_base",
                                     "0x710020000000",
                                     "-no_vm_base_near_app",
                                     "-c",
                                     client.string(),
                                     "--",
                                     executable.string()};
    for (int index = 1; index < argc; ++index) {
        command.emplace_back(argv[index]);
    }
    std::vector<char*> arguments;
    for (auto& argument : command) {
        arguments.push_back(argument.data());
    }
    arguments.push_back(nullptr);
    std::fputs("Starting CPU identity translation.\n", stderr);
    execv(runner.c_str(), arguments.data());
    const int saved_errno = errno;
    unsetenv(RestartMarker);
    std::fprintf(stderr, "Cannot start CPU identity translation: %s\n", std::strerror(saved_errno));
    return false;
#else
    // In a pure in-process build no external instrumentation is installed,
    // loaded, or needed by the normal auto/builtin modes.
    (void)argc;
    (void)argv;
    std::fputs("External CPU identity translation is not bundled. "
               "Choose --cpu-id-mode=builtin or native.\n", stderr);
    return false;
#endif
}

} // namespace Core
