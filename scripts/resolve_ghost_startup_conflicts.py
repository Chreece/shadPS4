#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Resolve ONLY the two startup PR #5275 conflicts recorded at 2026-10-08 13:16.

Before any write, compare the Git index stages and conflicted source with
the uploaded archive. Preserve both the newer networking code/tests and the
startup progress/loading registrations.
"""
import hashlib
from pathlib import Path
import re
import subprocess
import sys

EXPECTED = {
    'CMakeLists.txt': {
        'stages': ('98ee1dd7c77a4d3231f5d2708cd60a68bdd3dbb4',
                   '62333b1cc44e16f63aec16e22dec6bd035bd0acb',
                   'f2a9c980ba6004264fc3b1dcb92dfeb06b8f6830'),
        'work': '6e513675214efd087a97276db92250a7997015fa',
    },
    'tests/CMakeLists.txt': {
        'stages': ('9f1fa58392c6727ab7a9ddeba42208879296cc80',
                   '5aa2901c2dbecba7a8af65e8ab0cdf3df86e3bb9',
                   '7a5a2b62f6a0e193f3fbfa8151b6c9c77f6e217c'),
        'work': '850c9e33ae6a996aa8a33a026abea8690b64322d',
    },
}
LOAD = 'f8550f0ea3b3dd12eef824c07d1ed02a26533a6f'


def hash_object(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def resolved(file: str, data: bytes) -> bytes:
    spec = EXPECTED[file]
    assert hash_object(data) == spec['work'], f'Unexpected conflict file: {file}'
    text = data.decode()
    pat = re.compile(r'(?m)^<<<<<<< HEAD\n(.*?)^=======\n(.*?)^>>>>>>> ' + LOAD + r'\n', re.S)
    matches = list(pat.finditer(text))
    assert len(matches) == 1, f'Unexpected merge hunk count for {file}'
    left, right = matches[0].group(1, 2)
    if file == 'CMakeLists.txt':
        assert left.startswith('set(NET_CORE  src/core/net/guest_net.cpp\n')
        assert 'set(NET_LIB   src/core/libraries/net/net.cpp\n' in left
        old = 'set(CORE src/core/aerolib/stubs.cpp\n'
        assert left.count(old) == 1 and left.endswith(old)
        assert right == ('set(CORE src/core/startup_progress.h\n'
                         '         src/core/aerolib/stubs.cpp\n')
        replacement = left.replace(old, right)
    else:
        assert left.startswith('# ===========================================================================\n# Core::Net tests')
        assert 'add_net_lib_test(shadps4_net_kernel_test' in left
        assert 'target_link_libraries(shadps4_net_kernel_test PRIVATE' in left
        assert left.endswith(')\n')
        assert right == '\n# Startup loading feedback\nadd_subdirectory(startup_loading)\n'
        replacement = left + right
    merged = text[:matches[0].start()] + replacement + text[matches[0].end():]
    assert not re.search(r'(?m)^(?:<<<<<<<|=======\s*$|>>>>>>>)', merged), 'Unresolved markers'
    if file == 'CMakeLists.txt':
        for token in ('src/core/startup_progress.h', 'src/imgui/startup_loading.cpp',
                      'src/imgui/startup_loading.h', 'set(NET_CORE ', 'set(NET_LIB ',
                      'src/core/cpu_id.cpp'):
            assert token in merged, f'Missing {token}'
        for token in ('src/core/startup_progress.h', 'src/imgui/startup_loading.cpp',
                      'src/imgui/startup_loading.h'):
            assert merged.count(token) == 1, f'Duplicated {token}'
    else:
        assert merged.count('add_subdirectory(startup_loading)') == 1
        assert merged.count('add_net_lib_test(shadps4_net_kernel_test') == 1
    return merged.encode()


def git(cwd: Path, *args):
    return subprocess.check_output(['git', '-C', str(cwd), *args], text=True).strip()


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python3 resolve_ghost_startup_conflicts.py <worktree>')
    folder = Path(sys.argv[1]).resolve()
    assert (folder / '.git').exists(), 'The target must be a Git worktree'
    unresolved = git(folder, 'diff', '--name-only', '--diff-filter=U').splitlines()
    assert sorted(unresolved) == sorted(EXPECTED), f'Unexpected conflicts: {unresolved}'
    fixes = {}
    for path, spec in EXPECTED.items():
        for i, expected in enumerate(spec['stages'], 1):
            actual = git(folder, 'rev-parse', f':{i}:{path}')
            assert actual == expected, f'Index stage {i} mismatch in {path}: {actual}'
        fixes[path] = resolved(path, (folder / path).read_bytes())
        print(f'VERIFIED: {path} - all archived Git stages, conflict blob and preserved content')
    # All validations pass BEFORE any file is changed.
    for path, data in fixes.items():
        (folder / path).write_bytes(data)
    print('RESOLVED: 2/2 startup conflicts, preserving network + loading features')


if __name__ == '__main__':
    main()
