#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-run x86_64/Linux guest RIP sampling at confirmed Ghost frame stalls.

The 2026-10-09 RELEASE_MEM + shader-cap trial ran 156 s without a GPU hang,
but guest flips froze at 620 around 36 s while Game:Main consumed ~95% CPU.
This patch adds an ASYNC-SIGNAL-SAFE, test-only SIGUSR2 sampling handler to
signals.cpp. It is installed only if the game process receives
GHOST_CPU_RIP_LOG=/absolute/path; no other SIGUSR2 behavior changes unless
this explicit opt-in is present. The handler only writes the native RIP/TID,
never touches emulated guest state, signals or counters.

The 15:19 host reported different constructor formatting; opt-in registration
is inserted by verified Linux function boundaries and existing sigaction sites.

The controller verifies the exact test-owned PID, opt-in environment,
created log file and hot Game:Main Linux TID before sending bounded tgkill
samples. The shell restores original signals.cpp and every other source and
both executable copies on EVERY exit path. Not an upstream emulator change.
"""
from __future__ import annotations
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

FILE = "src/core/signals.cpp"
HOST_HEAD = "89af13f6d306ebc24396b4e8e207688537cdc28b"
INC_OLD = "#include <csignal>\n#include <pthread.h>\n"
INC_NEW = """#include <csignal>
#include <pthread.h>
#ifdef __linux__
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif
"""
NAMESPACE_OLD = "namespace Core {\n\n#if defined(_WIN32)"
NAMESPACE_NEW = """namespace Core {

// GHOST_CPU_RIP_PROBE: test-only SIGUSR2 snapshot, opt-in from the test
// launcher. Only async-signal-safe syscall/write and integer formatting
// execute in the handler. No logging locks or guest-memory accesses.
#ifdef __linux__
static int ghost_cpu_rip_fd = -1;
static void GhostCpuRipCapture(const void* code_address) noexcept {
    if (ghost_cpu_rip_fd < 0) {
        return;
    }
    char line[112];
    size_t pos = 0;
    constexpr char prefix[] = "GHOST_CPU_RIP_SAMPLE tid=";
    for (size_t i = 0; i < sizeof(prefix) - 1; ++i) {
        line[pos++] = prefix[i];
    }
    unsigned long tid = static_cast<unsigned long>(::syscall(SYS_gettid));
    char decimal[24];
    size_t digits = 0;
    do {
        decimal[digits++] = char('0' + tid % 10);
        tid /= 10;
    } while (tid && digits < sizeof(decimal));
    while (digits) {
        line[pos++] = decimal[--digits];
    }
    constexpr char separator[] = " rip=0x";
    for (size_t i = 0; i < sizeof(separator) - 1; ++i) {
        line[pos++] = separator[i];
    }
    constexpr char hex[] = "0123456789abcdef";
    const uintptr_t rip = reinterpret_cast<uintptr_t>(code_address);
    for (int shift = int(sizeof(uintptr_t) * 8) - 4; shift >= 0; shift -= 4) {
        line[pos++] = hex[(rip >> shift) & 0xf];
    }
    line[pos++] = '\\n';
    (void)::write(ghost_cpu_rip_fd, line, pos);
}
#endif

#if defined(_WIN32)"""
SIGNAL_OLD = """    auto* code_address = Common::GetRip(raw_context);

    Ucontext context{info, reinterpret_cast<ucontext_t*>(raw_context)};"""
SIGNAL_NEW = """    auto* code_address = Common::GetRip(raw_context);
#ifdef __linux__
    if (sig == SIGUSR2) {
        GhostCpuRipCapture(code_address);
        return; // No guest signal dispatch or context/register modification
    }
#endif

    Ucontext context{info, reinterpret_cast<ucontext_t*>(raw_context)};"""
REGISTER_OLD = """        "Failed to register signal handlers.");
#endif
}

void SignalDispatch::RemoveHandlers() {"""
REGISTER_NEW = """        "Failed to register signal handlers.");
#ifdef __linux__
    if (const char* path = std::getenv("GHOST_CPU_RIP_LOG"); path && path[0] == '/' &&
        std::strlen(path) < 1024) {
        // Fail closed rather than following links or replacing an existing file.
        ghost_cpu_rip_fd = ::open(path, O_WRONLY | O_CREAT | O_EXCL |
                                       O_APPEND | O_CLOEXEC | O_NOFOLLOW, 0600);
        ASSERT_MSG(ghost_cpu_rip_fd >= 0,
                   "GHOST_CPU_RIP_PROBE cannot create uniquely owned diagnostic file");
        ASSERT_MSG(sigaction(SIGUSR2, &action, nullptr) == 0,
                   "GHOST_CPU_RIP_PROBE cannot register test-only SIGUSR2");
        LOG_WARNING(Core, "GHOST_CPU_RIP_READY probe=1 sigusr2=1");
    }
#endif
#endif
}

void SignalDispatch::RemoveHandlers() {"""
REMOVE_OLD = """    if (!(sigaction(SIGSEGV, &action, nullptr) == 0 && sigaction(SIGBUS, &action, nullptr) == 0 &&
          sigaction(SIGILL, &action, nullptr) == 0 && sigaction(SIGFPE, &action, nullptr) == 0 &&
          sigaction(SIGTRAP, &action, nullptr) == 0 && sigaction(SIGSYS, &action, nullptr) == 0 &&
          sigaction(SIGUSR1, &action, nullptr) == 0 &&
          sigaction(SIGSLEEP, &action, nullptr) == 0)) {
        LOG_CRITICAL(Core, "Failed to remove signal handlers.");
        std::quick_exit(1);
    }
#endif
}"""
REMOVE_NEW = """    if (!(sigaction(SIGSEGV, &action, nullptr) == 0 && sigaction(SIGBUS, &action, nullptr) == 0 &&
          sigaction(SIGILL, &action, nullptr) == 0 && sigaction(SIGFPE, &action, nullptr) == 0 &&
          sigaction(SIGTRAP, &action, nullptr) == 0 && sigaction(SIGSYS, &action, nullptr) == 0 &&
          sigaction(SIGUSR1, &action, nullptr) == 0 &&
          sigaction(SIGSLEEP, &action, nullptr) == 0)) {
        LOG_CRITICAL(Core, "Failed to remove signal handlers.");
        std::quick_exit(1);
    }
#ifdef __linux__
    if (ghost_cpu_rip_fd >= 0) {
        ASSERT_MSG(sigaction(SIGUSR2, &action, nullptr) == 0,
                   "GHOST_CPU_RIP_PROBE failed to restore SIGUSR2");
        ::close(ghost_cpu_rip_fd);
        ghost_cpu_rip_fd = -1;
    }
#endif
#endif
}"""


# The installed host's pinned HEAD differs in the formatting of the
# SignalDispatch constructor (15:19 captured check-only failure). Accept
# only the reviewed Linux sigaction structure, not arbitrary substring
# replacements or changed user source. Keep the exact two other source
# modifications and never replace or skip existing signal registrations.
REGISTER_INSERT = """#ifdef __linux__
    if (const char* path = std::getenv("GHOST_CPU_RIP_LOG"); path && path[0] == '/' &&
        std::strlen(path) < 1024) {
        // No symlinks, no replacement of an existing diagnostic file.
        ghost_cpu_rip_fd = ::open(path, O_WRONLY | O_CREAT | O_EXCL |
                                       O_APPEND | O_CLOEXEC | O_NOFOLLOW, 0600);
        ASSERT_MSG(ghost_cpu_rip_fd >= 0,
                   "GHOST_CPU_RIP_PROBE cannot create uniquely owned diagnostic file");
        ASSERT_MSG(sigaction(SIGUSR2, &action, nullptr) == 0,
                   "GHOST_CPU_RIP_PROBE cannot register test-only SIGUSR2");
        LOG_WARNING(Core, "GHOST_CPU_RIP_READY probe=1 sigusr2=1");
    }
#endif
"""
REMOVE_INSERT = """#ifdef __linux__
    if (ghost_cpu_rip_fd >= 0) {
        ASSERT_MSG(sigaction(SIGUSR2, &action, nullptr) == 0,
                   "GHOST_CPU_RIP_PROBE failed to restore SIGUSR2");
        ::close(ghost_cpu_rip_fd);
        ghost_cpu_rip_fd = -1;
    }
#endif
"""


def append_inside_linux_signal_function(
    source: str, function: str, next_function: str,
    addition: str, *, is_registration: bool,
) -> str:
    """Structural, fail-closed Linux insertion before outer Windows #endif."""
    start_tag = function + " {"
    end_tag = "\n" + next_function + " {"
    if source.count(start_tag) != 1 or source.count(end_tag) != 1:
        raise ValueError("Ambiguous signal-handler function boundaries: " + function)
    start = source.index(start_tag)
    end = source.index(end_tag, start)
    if end <= start:
        raise ValueError("Signal-handler functions out of expected order")
    body = source[start:end]
    for required in (
        "#if defined(_WIN32)", "\n#else\n", "struct sigaction action{};",
        "sigaction(SIGSEGV, &action, nullptr)",
        "sigaction(SIGUSR1, &action, nullptr)",
    ):
        if required not in body:
            raise ValueError(function + ": missing reviewed signal registration " + required)
    if is_registration:
        if "action.sa_sigaction = SignalHandler;" not in body or \
           "sigaction(SIGSLEEP, &action, nullptr)" not in body:
            raise ValueError(function + ": original guest signal registration unexpected")
    else:
        if "action.sa_handler = SIG_DFL;" not in body:
            raise ValueError(function + ": original signal handler reset unexpected")
    if "sigaction(SIGUSR2" in body or "GHOST_CPU_RIP" in body:
        raise ValueError(function + ": SIGUSR2 or previous probe already registered")
    # The reviewed constructor and remover must end with the outer
    # Windows/Linux #endif followed by the closing brace. Never insert into
    # arbitrary inner #ifdef, unrelated functions or other Linux signals.
    closing = re.search(r"(?m)^#endif[ \t]*\n\}[ \t]*\n*\Z", body)
    if closing is None or closing.start() <= body.find("\n#else\n"):
        raise ValueError(function + ": missing unique outer platform closing block")
    insert_at = start + closing.start()
    return source[:insert_at] + addition + source[insert_at:]


def transform(src: str) -> str:
    sites = (
        ("platform includes", INC_OLD, INC_NEW),
        ("namespace helper", NAMESPACE_OLD, NAMESPACE_NEW),
        ("early signal sample", SIGNAL_OLD, SIGNAL_NEW),
    )
    if "GHOST_CPU_RIP_PROBE" in src:
        raise ValueError("CPU probe is already installed")
    for label, old, _ in sites:
        if src.count(old) != 1:
            raise ValueError(f"{label}: expected exactly one reviewed anchor, saw {src.count(old)}")
    for _, before, after in sites:
        src = src.replace(before, after, 1)
    src = append_inside_linux_signal_function(
        src, "SignalDispatch::SignalDispatch()",
        "void SignalDispatch::RemoveHandlers()", REGISTER_INSERT, is_registration=True)
    src = append_inside_linux_signal_function(
        src, "void SignalDispatch::RemoveHandlers()",
        "SignalDispatch::~SignalDispatch()", REMOVE_INSERT, is_registration=False)
    assert src.count("GHOST_CPU_RIP_SAMPLE tid=") == 1
    assert src.count('std::getenv("GHOST_CPU_RIP_LOG")') == 1
    assert src.count("sigaction(SIGUSR2, &action, nullptr)") == 2
    assert src.count("if (sig == SIGUSR2)") == 1
    assert src.count("GhostCpuRipCapture(code_address);") == 1
    assert "O_EXCL |" in src and "O_NOFOLLOW" in src
    return src


def selftest() -> None:
    # Two real sigaction layouts: the original upstream formatting and
    # the differently formatted 2026 host constructor. Do not require
    # one exact ASSERT_MSG line or warning-string formatting.
    current_fixture = (
        '#include "common/test.h"\n' + INC_OLD +
        NAMESPACE_OLD + "\n" + SIGNAL_OLD + "\n" +
        "SignalDispatch::SignalDispatch() {\n"
        "#if defined(_WIN32)\n"
        "    OriginalWindowsHandler();\n"
        "#else\n"
        "    struct sigaction action{};\n"
        "    action.sa_sigaction = SignalHandler;\n"
        "    ASSERT_MSG(sigaction(SIGSEGV, &action, nullptr) == 0 &&\n"
        "               sigaction(SIGUSR1, &action, nullptr) == 0 &&\n"
        "               sigaction(SIGSLEEP, &action, nullptr) == 0,\n"
        "               \"host customized signal handler report\");\n"
        "#endif\n"
        "}\n\n"
        "void SignalDispatch::RemoveHandlers() {\n"
        "#if defined(_WIN32)\n"
        "    OriginalWindowsCleanup();\n"
        "#else\n"
        "    struct sigaction action{};\n"
        "    action.sa_handler = SIG_DFL;\n"
        "    if (!(sigaction(SIGSEGV, &action, nullptr) == 0 &&\n"
        "          sigaction(SIGUSR1, &action, nullptr) == 0 &&\n"
        "          sigaction(SIGSLEEP, &action, nullptr) == 0)) {\n"
        "        std::quick_exit(1);\n"
        "    }\n"
        "#endif\n"
        "}\n\n"
        "SignalDispatch::~SignalDispatch() {}\n"
    )
    revised = transform(current_fixture)
    # Python triple-quoted NAMESPACE_NEW is evaluated before writing C++.
    # A single \n in the patch source becomes an actual LF *inside* a
    # character literal and previously caused a C++ build failure. Require
    # the generated C++ to contain the two character escape sequence.
    assert r"line[pos++] = '\n';" in revised
    assert "line[pos++] = '" + chr(10) + "';" not in revised
    assert revised.count("line[pos++] =") == 5
    assert "GHOST_CPU_RIP_SAMPLE tid=" in revised
    assert 'std::getenv("GHOST_CPU_RIP_LOG")' in revised
    assert "::write(ghost_cpu_rip_fd, line, pos)" in revised
    assert "::open(path, O_WRONLY | O_CREAT | O_EXCL" in revised
    assert revised.count("sigaction(SIGUSR2, &action, nullptr)") == 2
    assert "host customized signal handler report" in revised
    assert revised.index("GHOST_CPU_RIP_READY") < revised.index(
        "void SignalDispatch::RemoveHandlers()")
    assert revised.index("GHOST_CPU_RIP_PROBE failed to restore SIGUSR2") < (
        revised.index("SignalDispatch::~SignalDispatch()"))
    # Changing the exact format of the original registration text is okay.
    differently_formatted = current_fixture.replace(
        '"host customized signal handler report"',
        '"actual host changed this message since upstream"')
    assert "GHOST_CPU_RIP_READY" in transform(differently_formatted)
    for invalid in (
        revised,
        current_fixture.replace(SIGNAL_OLD, ""),
        current_fixture.replace("struct sigaction action{};", "", 1),
        current_fixture.replace("action.sa_handler = SIG_DFL;", ""),
        current_fixture.replace("SignalDispatch::~SignalDispatch()", "OtherDestructor"),
        current_fixture.replace("sigaction(SIGUSR1, &action, nullptr)", "BadSignal()", 1),
        current_fixture.replace("action.sa_sigaction = SignalHandler;", "wrong handler"),
    ):
        try:
            transform(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Modified, unknown or duplicate signal source accepted")
    print("SELFTEST PASS: host-variant sigaction constructor, Linux-only opt-in "
          "handler, original guest signals preserved, exact source boundaries, "
          "cleanup insertion, duplicate and modified-source rejection")



def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        selftest()
        return 0
    if len(sys.argv) not in (3, 4) or (len(sys.argv) == 4 and sys.argv[3] != "--check-only"):
        raise SystemExit("Usage: patch.py ROOT BACKUP_DIR [--check-only]")
    root = Path(sys.argv[1]).resolve()
    save_root = Path(sys.argv[2]).resolve()
    check_only = len(sys.argv) == 4
    target = root / FILE
    if target.is_symlink() or not target.is_file():
        raise ValueError("Unexpected signal-handler source type")
    if git("-C", str(root), "rev-parse", "HEAD") != HOST_HEAD:
        raise ValueError("Source Git HEAD is not the verified host commit")
    tracked = git("-C", str(root), "rev-parse", "HEAD:" + FILE)
    actual = git("hash-object", str(target))
    if actual != tracked:
        raise ValueError(f"signals.cpp differs from tracked HEAD: {actual} != {tracked}")
    source = target.read_bytes()
    revised = transform(source.decode("utf-8")).encode("utf-8")
    selftest()
    print(f"GHOST_CPU_RIP_SOURCE_VERIFIED original_blob={actual} expected_head={HOST_HEAD}")
    if check_only:
        print("GHOST_CPU_RIP_HOST_STRUCTURE_CHECK_PASS: verified tracked source and Linux signal function boundaries; no writes")
        return 0
    backup = save_root / FILE
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        raise ValueError("Refusing to overwrite existing signals.cpp backup")
    backup.write_bytes(source)
    if backup.read_bytes() != source:
        raise IOError("Original signals.cpp backup verification failed")
    temp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=target.parent, prefix=".ghost-cpu-rip-", delete=False
        ) as writer:
            temp = Path(writer.name)
            writer.write(revised)
        os.chmod(temp, stat.S_IMODE(target.stat().st_mode))
        os.replace(temp, target)
        temp = None
    finally:
        if temp is not None and temp.exists():
            temp.unlink()
    if target.read_bytes() != revised:
        raise IOError("Patched source verification failed")
    print("GHOST_CPU_RIP_PATCH=APPLIED: opt-in SIGUSR2, Linux-only, no guest changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
