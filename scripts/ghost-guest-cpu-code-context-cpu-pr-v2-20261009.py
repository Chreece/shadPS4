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

The 15:52 run proved RIP=0xb09942 (14/16 samples), neighbors 0xb09940/44,
inside a readable/executable 0x400000..0x173c000 guest memfd. Capture a
bounded 128-byte code window 0xb09900..0xb0997f and the signal ucontext
registers ONLY if the sampled RIP itself lies in that window.
This is a reversible diagnostic. It never writes guest registers or data.

When the Ghost build stages verified CPU PRs, the patcher also accepts a
modified signals.cpp ONLY when its Git blob matches the exact per-file
CPU overlay manifest from the previously validated six-PR stack. This is
never a general permission to patch dirty or unrelated guest signal code.

The controller verifies the exact test-owned PID, opt-in environment,
created log file and hot Game:Main Linux TID before sending bounded tgkill
samples. The shell restores original signals.cpp and every other source and
both executable copies on EVERY exit path. Not an upstream emulator change.
"""
from __future__ import annotations
import json
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
#include <ucontext.h>
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
static void GhostCpuRipCapture(const void* code_address, const void* raw_context) noexcept {
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
    GhostCpuLoopDetail(code_address, raw_context);
}
#endif

#if defined(_WIN32)"""
DETAIL_CXX = r"""// GHOST_CPU_LOOP_CODE_V1: exact 15:52 guest executable page, never
// dereference a non-code pointer. RIP is required within the same 128 bytes.
static void GhostCpuLoopDetail(const void* code_address, const void* raw_context) noexcept {
    const uintptr_t rip = reinterpret_cast<uintptr_t>(code_address);
    constexpr uintptr_t kGuestCode = 0x0000000000b09900ULL;
    if (ghost_cpu_rip_fd < 0 || raw_context == nullptr ||
        rip < kGuestCode || rip >= kGuestCode + 128) {
        return;
    }
    const auto* ctx = reinterpret_cast<const ucontext_t*>(raw_context);
    const auto& registers = ctx->uc_mcontext.gregs;
    char line[1024];
    size_t pos = 0;
    const auto append_text = [&](const char* string) noexcept {
        while (*string && pos < sizeof(line) - 1) {
            line[pos++] = *string++;
        }
    };
    const auto append_hex = [&](uintptr_t value) noexcept {
        constexpr char alphabet[] = "0123456789abcdef";
        for (int shift = int(sizeof(uintptr_t) * 8) - 4; shift >= 0; shift -= 4) {
            if (pos < sizeof(line) - 1) {
                line[pos++] = alphabet[(value >> shift) & 0xf];
            }
        }
    };
    const auto append_tid = [&](unsigned long value) noexcept {
        char reversed[24];
        size_t digits = 0;
        do {
            reversed[digits++] = char('0' + (value % 10));
            value /= 10;
        } while (value && digits < sizeof(reversed));
        while (digits && pos < sizeof(line) - 1) {
            line[pos++] = reversed[--digits];
        }
    };
    append_text("GHOST_CPU_LOOP_BYTES tid=");
    append_tid(static_cast<unsigned long>(::syscall(SYS_gettid)));
    append_text(" rip=0x");
    append_hex(rip);
    append_text(" base=0x");
    append_hex(kGuestCode);
    append_text(" code=");
    const auto* code = reinterpret_cast<const volatile unsigned char*>(kGuestCode);
    constexpr char nibble[] = "0123456789abcdef";
    for (size_t i = 0; i < 128; ++i) {
        const unsigned char value = code[i];
        if (pos + 2 >= sizeof(line)) {
            return;
        }
        line[pos++] = nibble[value >> 4];
        line[pos++] = nibble[value & 15];
    }
    const auto append_register = [&](const char* label, int reg) noexcept {
        append_text(label);
        append_hex(static_cast<uintptr_t>(registers[reg]));
    };
    append_register(" rax=0x", REG_RAX);
    append_register(" rbx=0x", REG_RBX);
    append_register(" rcx=0x", REG_RCX);
    append_register(" rdx=0x", REG_RDX);
    append_register(" rsi=0x", REG_RSI);
    append_register(" rdi=0x", REG_RDI);
    append_register(" r8=0x", REG_R8);
    append_register(" r9=0x", REG_R9);
    append_register(" r10=0x", REG_R10);
    append_register(" r11=0x", REG_R11);
    append_register(" r12=0x", REG_R12);
    append_register(" r13=0x", REG_R13);
    append_register(" r14=0x", REG_R14);
    append_register(" r15=0x", REG_R15);
    append_register(" rsp=0x", REG_RSP);
    append_register(" rbp=0x", REG_RBP);
    append_register(" rflags=0x", REG_EFL);
    if (pos < sizeof(line)) {
        line[pos++] = '\n';
        (void)::write(ghost_cpu_rip_fd, line, pos);
    }
}

"""

SIGNAL_OLD = """    auto* code_address = Common::GetRip(raw_context);

    Ucontext context{info, reinterpret_cast<ucontext_t*>(raw_context)};"""
SIGNAL_NEW = """    auto* code_address = Common::GetRip(raw_context);
#ifdef __linux__
    if (sig == SIGUSR2) {
        GhostCpuRipCapture(code_address, raw_context);
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
    gate = "static int ghost_cpu_rip_fd = -1;\n"
    if src.count(gate) != 1:
        raise ValueError("Expected exact opt-in CPU RIP file descriptor location")
    src = src.replace(gate, gate + DETAIL_CXX, 1)
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
    assert src.count("GhostCpuRipCapture(code_address, raw_context);") == 1
    assert "O_EXCL |" in src and "O_NOFOLLOW" in src
    assert src.count("GHOST_CPU_LOOP_BYTES tid=") == 1
    assert src.count("GHOST_CPU_LOOP_CODE_V1") == 1
    assert src.count("0x0000000000b09900ULL") == 1
    assert src.count("GhostCpuLoopDetail(code_address, raw_context);") == 1
    assert "line[pos++] = '\\n';" in src
    assert "line[pos++] = '" + chr(10) + "';" not in src
    assert "REG_RAX" in src and "REG_EFL" in src
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
    assert revised.count("line[pos++] =") >= 5
    assert "GHOST_CPU_LOOP_BYTES tid=" in revised
    assert "0x0000000000b09900ULL" in revised
    assert "ghost_cpu_rip_fd < 0 || raw_context == nullptr" in revised
    assert "append_register(\" rflags=0x\", REG_EFL);" in revised
    assert "GhostCpuRipCapture(code_address, raw_context);" in revised
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
    with tempfile.TemporaryDirectory(prefix="ghost-cpu-overlay-probe-test-") as test:
        path = Path(test) / "manifest.json"
        path.write_text(json.dumps({
            "phase": "applied", "host_head": HOST_HEAD,
            "stack_head": "b02f24559ad86249aef549d11531977a19b8196f",
            "reciprocal_head": "9bb22d370e5847a37ecda13c5628d5d280a03f2f",
            "changed": [{"path": FILE, "original_blob": "a" * 40,
                         "stage_blob": "b" * 40}],
        }))
        assert verified_cpu_overlay_signal_blob(Path(test), "a" * 40, "b" * 40, str(path))
        assert not verified_cpu_overlay_signal_blob(Path(test), "a" * 40, "c" * 40, str(path))
        assert not verified_cpu_overlay_signal_blob(Path(test), "a" * 40, "b" * 40, "")
    print("SELFTEST PASS: pinned CPU overlay signals.cpp provenance, host-variant sigaction constructor, Linux-only opt-in 
          "handler, original guest signals preserved, exact source boundaries, "
          "cleanup insertion, duplicate and modified-source rejection")



def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def verified_cpu_overlay_signal_blob(root: Path, tracked: str,
                                     actual: str, manifest_path: str) -> bool:
    """Verify prior CPU source transformation and provenance, fail closed."""
    if not manifest_path or not Path(manifest_path).is_absolute():
        return False
    manifest = Path(manifest_path)
    if manifest.is_symlink() or not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text())
        if data.get("phase") != "applied" or data.get("host_head") != HOST_HEAD:
            return False
        if data.get("stack_head") != \
                "b02f24559ad86249aef549d11531977a19b8196f":
            return False
        if data.get("reciprocal_head") != \
                "9bb22d370e5847a37ecda13c5628d5d280a03f2f":
            return False
        entries = [e for e in data.get("changed", []) if e.get("path") == FILE]
        return (len(entries) == 1 and
                entries[0].get("original_blob") == tracked and
                entries[0].get("stage_blob") == actual)
    except (OSError, ValueError, TypeError, KeyError):
        return False


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
        if not verified_cpu_overlay_signal_blob(
                root, tracked, actual, os.environ.get("GHOST_CPU_OVERLAY_MANIFEST", "")):
            raise ValueError("signals.cpp is not tracked HEAD or exact approved CPU overlay: "
                             f"actual={actual}, tracked={tracked}")
        print("GHOST_CPU_RIP_SOURCE_ACCEPTED_VERIFIED_CPU_PR_OVERLAY=1")
    source = target.read_bytes()
    revised = transform(source.decode("utf-8")).encode("utf-8")
    selftest()
    print(f"GHOST_CPU_RIP_SOURCE_VERIFIED original_blob={actual} expected_head={HOST_HEAD}")
    if check_only:
        print("GHOST_CPU_RIP_CODE_CONTEXT_CPU_PR_V2_CHECK_PASS: pinned HEAD or exact six-PR overlay, 128-byte code/register capture")
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
    print("GHOST_CPU_RIP_PATCH=APPLIED: CPU PR compatible bounded guest code/register capture, no guest state edits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
