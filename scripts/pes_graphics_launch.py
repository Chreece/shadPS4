# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Enable existing native GPU diagnostics after the guarded launcher's env reset."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile


def atomic_write(path, data, mode):
    fd, name = tempfile.mkstemp(prefix='.pes-graphics-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def instrument(original, home, validation=False, gpu_collector=None):
    command = b'exec ' + os.fsencode(home / 'Applications/shadps4/shadps4')
    command += b' --game "$game" --fullscreen true\n'
    lines = original.splitlines(keepends=True)
    if (lines.count(command) != 1 or b'# SHADPS4_SESSION_GUARD_V1\n' not in lines or
            b'# SHADPS4_DEFAULT_MAIN_V1\n' not in lines):
        raise RuntimeError('Unrecognized guarded launcher; preserved')
    index = lines.index(command)
    lines.insert(index, b'export SHADPS4_GRAPHICS_DIAGNOSTICS=1\n')
    if validation:
        from pes_vulkan_validation import ENVIRONMENT
        lines.insert(index, ''.join('export ' + key + '=' + shlex.quote(value) + '\n'
                                   for key, value in ENVIRONMENT.items()).encode())
    if gpu_collector is not None:
        extra = ''.join('export ' + key + '=' + shlex.quote(value) + '\n'
                        for key, value in gpu_collector.environment.items()).encode()
        index = lines.index(command)
        lines.insert(index, extra)
        lines[index + 1] = (b'exec ' + shlex.join(gpu_collector.command).encode() + b' ' +
                            command.removeprefix(b'exec '))
    return b''.join(lines)


@contextmanager
def enabled_launch(home, wrapper, expected_sha, work, validation=False, gpu_collector=None):
    info = wrapper.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Launcher owner or file type changed; preserved')
    original = wrapper.read_bytes()
    if hashlib.sha256(original).hexdigest() != expected_sha:
        raise RuntimeError('Launcher changed before graphics capture; preserved')
    modified = instrument(original, home, validation, gpu_collector)
    (work / 'launcher.original').write_bytes(original)
    (work / 'launcher.instrumented').write_bytes(modified)
    try:
        atomic_write(wrapper, modified, stat.S_IMODE(info.st_mode))
        yield
    finally:
        if wrapper.is_symlink() or not wrapper.is_file():
            raise RuntimeError('Launcher replaced externally; original saved in ' + str(work))
        current = wrapper.read_bytes()
        if current == modified:
            atomic_write(wrapper, original, stat.S_IMODE(info.st_mode))
        elif current != original:
            raise RuntimeError('Launcher edited externally; original saved in ' + str(work))
        if wrapper.read_bytes() != original:
            raise RuntimeError('Launcher restoration failed; original saved in ' + str(work))


def verify_environment(identity, proc_root=Path('/proc'), validation=False):
    proc = proc_root / str(identity['pid'])
    def start_ticks():
        return (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    if start_ticks() != identity['start_ticks']:
        raise RuntimeError('Emulator identity changed before graphics verification')
    enabled = b'SHADPS4_GRAPHICS_DIAGNOSTICS=1' in (proc / 'environ').read_bytes().split(b'\0')
    if start_ticks() != identity['start_ticks'] or not enabled:
        raise RuntimeError('Native graphics diagnostics did not reach the emulator')
    if validation:
        from pes_vulkan_validation import ENVIRONMENT
        environment = (proc / 'environ').read_bytes().split(b'\0')
        if any((key + '=' + value).encode() not in environment
               for key, value in ENVIRONMENT.items()):
            raise RuntimeError('Vulkan validation environment did not reach the emulator')
        if 'libVkLayer_khronos_validation.so' not in (proc / 'maps').read_text():
            raise RuntimeError('Vulkan validation layer is not loaded in the emulator')
        if start_ticks() != identity['start_ticks']:
            raise RuntimeError('Emulator identity changed during validation verification')


def analyze(paths):
    counts = {}
    examples = []
    for path in paths:
        with path.open(errors='replace') as source:
            for line in source:
                match = re.search(r'GRAPHICS_DIAG ms=(\d+) event=([\w-]+) count=(\d+)', line)
                if not match:
                    continue
                event, count = match[2], int(match[3])
                counts[event] = max(counts.get(event, 0), count)
                if len(examples) < 48:
                    examples.append(line.strip())
    return {'started': counts.get('enabled', 0) > 0,
            'event_count_lower_bounds': counts, 'examples': examples}
