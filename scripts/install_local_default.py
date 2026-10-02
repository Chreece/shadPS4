#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build pinned main in Docker, select it in ES-DE, and retire old test cores.

Run as the desktop user with games and ES-DE closed. No sudo or service changes.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


REPO = 'https://github.com/Chreece/shadPS4.git'
IMAGE = 'shadps4-ngs2-builder:trixie-clang19-v1'
DOCKERFILE = '''FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git cmake ninja-build build-essential clang-19 clang-tools-19 llvm-19-dev \
    llvm-19-tools lld-19 ccache pkg-config python3 nasm \
    libasound2-dev libpulse-dev libopenal-dev libssl-dev zlib1g-dev libedit-dev \
    libudev-dev libevdev-dev libjack-jackd2-dev libsndio-dev libvulkan-dev \
    libpng-dev libx11-dev libxext-dev libwayland-dev libdecor-0-dev \
    libxkbcommon-dev libxcursor-dev libxi-dev libxss-dev libxtst-dev \
    libxrandr-dev libxfixes-dev libxinerama-dev libegl1-mesa-dev \
    libgl1-mesa-dev libgles2-mesa-dev uuid-dev libdbus-1-dev \
    && test -x /usr/bin/clang-scan-deps-19 \
    && rm -rf /var/lib/apt/lists/*
'''
GUARD_START = '# SHADPS4_SESSION_GUARD_V1\n'
GUARD_END = '# END SHADPS4_SESSION_GUARD_V1\n'
GAME_ID = re.compile(r'CUSA[0-9]{5}')
TEST_LABEL = re.compile(r'ngs2|sparse|wait.?stage|\bbda\b|\btrace\b|\bbaseline\b', re.I)
VERSION_LABEL = re.compile(r'shadPS4\s+v?[0-9]+(?:\.[0-9]+){1,3}(?:[-+][\w.-]+)?', re.I)
BUILD_SELECTOR = re.compile(r'(CUSA[0-9]{5})\|[A-Za-z0-9][A-Za-z0-9_.-]*')


def ordinary_title(stem):
    """Remove build/test annotations, retaining region and other title metadata."""
    def annotation(match):
        label = match.group(1).strip()
        return '' if TEST_LABEL.search(label) or VERSION_LABEL.fullmatch(label) else match.group()
    return re.sub(r'\s*\[([^]]*)\]', annotation, stem).strip()


def say(value):
    print(value, flush=True)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def regular(path):
    return path.is_file() and not path.is_symlink()


def atomic_write(path, data, mode=0o700):
    fd, name = tempfile.mkstemp(prefix=path.name + '.new-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
            os.fchmod(out.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def atomic_link(path, target):
    fd, name = tempfile.mkstemp(prefix=path.name + '.new-', dir=path.parent)
    os.close(fd)
    os.unlink(name)
    try:
        Path(name).symlink_to(target)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def no_running_apps():
    busy = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            name = (proc / 'comm').read_text().strip().lower()
            exe = (proc / 'exe').readlink().name.removesuffix(' (deleted)').lower()
            if exe in ('shadps4', 'shadps4.exe', 'es-de') or name == 'es-de':
                busy.append(proc.name + ':' + exe)
        except (OSError, ValueError):
            continue
    if busy:
        raise RuntimeError('Close the game and ES-DE normally, then rerun: ' + ', '.join(busy))


def guard_prefix(home, text):
    helper = home / '.local/lib/shadps4-session-guard/guard.py'
    prefix = (GUARD_START + 'if [[ "${SHADPS4_GUARD_PARENT_PID:-}" != "$PPID" ]]; then\n'
              '    exec python3 ' + shlex.quote(str(helper)) + ' run "$0" "$@"\n'
              'fi\nunset SHADPS4_GUARD_PARENT_PID\n' + GUARD_END)
    if not text.partition('\n')[2].startswith(prefix) or text.count(GUARD_START) != 1:
        raise RuntimeError('Existing shadPS4 single-instance guard is not recognized; preserved.')
    if not regular(helper):
        raise RuntimeError('Existing single-instance helper is missing; preserved.')
    return prefix


def launcher(home, prefix):
    core = home / 'Applications/shadps4/shadps4'
    manager = home / 'Applications/shadps4QtLauncher-latest.AppImage'
    # Run from the same directory as the successfully observed NGS2 sessions.
    # Neither the user data directory nor any audio/GPU configuration is changed.
    text = ('#!/usr/bin/env bash\n' + prefix + '# SHADPS4_DEFAULT_MAIN_V1\n'
            'set -euo pipefail\n'
            'entry="${1:-}"\n'
            'if [[ -f "$entry" && "${entry,,}" == *.ps4 ]]; then\n'
            '    IFS= read -r game < "$entry" || [[ -n "${game:-}" ]]\n'
            '    game="${game%$\'\\r\'}"\n'
            'else\n    game="$entry"\nfi\n'
            'if [[ "$game" == GUI ]]; then\n'
            '    cd ' + shlex.quote(str(home)) + '\n'
            '    exec ' + shlex.quote(str(manager)) + '\n'
            'fi\n'
            'if [[ ! "$game" =~ ^CUSA[0-9]{5}$ ]]; then\n'
            '    printf "Invalid or retired PS4 entry: %s\\n" "$entry" >&2\n'
            '    exit 2\nfi\n'
            'unset SHADPS4_NGS2_DIAGNOSTICS SHADPS4_GRAPHICS_DIAGNOSTICS '
            'SHADPS4_NGS2_DIAGNOSTICS_TRIGGER\n'
            'cd ' + shlex.quote(str(home)) + '\n'
            'exec ' + shlex.quote(str(core)) + ' --game "$game" --fullscreen true\n')
    return text.encode()


def read_xml(path):
    data = path.read_bytes()
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise RuntimeError('Unexpected XML declarations in ' + str(path))
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return data, ET.fromstring(data, parser=parser)


def inspect_esde(home):
    config = home / 'ES-DE/custom_systems/es_systems.xml'
    if not regular(config):
        raise RuntimeError('Expected existing ES-DE custom systems file: ' + str(config))
    _, tree = read_xml(config)
    systems = [node for node in tree.findall('system') if node.findtext('name') == 'ps4']
    if len(systems) != 1:
        raise RuntimeError('Expected exactly one PS4 custom system.')
    node = systems[0]
    commands = node.findall('command')
    expected = str(home / '.local/bin/shadps4-esde') + ' %ROM%'
    if len(commands) != 1 or (commands[0].text or '').strip() != expected:
        raise RuntimeError('PS4 launch command differs from the supplied report; preserved.')
    roms = Path(node.findtext('path', '')).expanduser()
    if not roms.is_absolute() or not roms.is_dir() or roms.is_symlink():
        raise RuntimeError('Expected an accessible PS4 ROM-entry directory.')
    entries, tests, unknown = {}, [], []
    for path in sorted(roms.iterdir()):
        if path.suffix.lower() != '.ps4':
            continue
        if not regular(path) or path.stat().st_size > 4096:
            raise RuntimeError('Unexpected PS4 entry; preserved: ' + str(path))
        lines = path.read_text().splitlines()
        token = lines[0] if lines else ''
        if token == 'GUI':
            # Manager shortcuts are not games or disposable test entries.
            continue
        build_label = ordinary_title(path.stem) != path.stem
        selector = BUILD_SELECTOR.fullmatch(token)
        if GAME_ID.fullmatch(token) and not build_label:
            entries.setdefault(token, []).append(path)
        elif selector or (GAME_ID.fullmatch(token) and build_label):
            # The old version/experiment selector is retired, not interpreted as
            # a command or path. Only the validated game ID reaches the new entry.
            tests.append((path, selector.group(1) if selector else token))
        else:
            unknown.append(str(path) + ' (first line: ' + repr(token[:160]) + ')')
    if unknown:
        raise RuntimeError('Unrecognized PS4 entries; nothing switched:\n' + '\n'.join(unknown))
    return roms, entries, tests


def smoke(binary):
    with binary.open('rb') as stream:
        header = stream.read(5)
    if header != b'\x7fELF\x02':
        raise RuntimeError('Built core is not a 64-bit ELF executable.')
    result = subprocess.run(['ldd', str(binary)], capture_output=True, text=True, timeout=30)
    if result.returncode or 'not found' in result.stdout:
        raise RuntimeError('Missing runtime libraries: ' + result.stdout + result.stderr)
    with tempfile.TemporaryDirectory(prefix='shadps4-smoke-') as tmp:
        (Path(tmp) / 'user').mkdir()
        result = subprocess.run([str(binary), '--help'], cwd=tmp,
                                capture_output=True, text=True, timeout=30)
        if result.returncode or 'shadPS4 Emulator CLI' not in result.stdout:
            raise RuntimeError('Core startup check failed: ' + (result.stdout + result.stderr)[-2000:])


def build(home, revision):
    work = home / '.cache/shadps4-ngs2-local/ca67919d-docker'
    work.mkdir(parents=True, exist_ok=True)
    source = work / 'source'
    logfile = work / 'default-main-build.log'
    say('BUILD_LOG=' + str(logfile))
    jobs = str(min(8, os.cpu_count() or 2))

    def container(args):
        return ['docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
                '--mount', f'type=bind,src={work},dst={work}', '--workdir', str(work),
                '--env', 'HOME=' + str(work / 'container-home'),
                '--env', 'CCACHE_DIR=' + str(work / 'ccache'), IMAGE, *map(str, args)]

    with logfile.open('a') as log:
        def run(args, docker=False):
            args = list(map(str, args))
            say('STEP=' + shlex.join(args))
            result = subprocess.run(container(args) if docker else args,
                                    stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                log.flush()
                with logfile.open('rb') as stream:
                    stream.seek(max(0, logfile.stat().st_size - 8000))
                    say(stream.read().decode(errors='replace'))
                raise RuntimeError('Build failed; default unchanged. Log: ' + str(logfile))

        run(['docker', 'info', '--format', '{{.ServerVersion}}'])
        if not source.exists():
            run(['git', 'clone', '--no-checkout', REPO, source])
        if source.is_symlink():
            raise RuntimeError('Unexpected build-source symlink.')
        remote = subprocess.check_output(['git', '-C', source, 'remote', 'get-url', 'origin'],
                                         text=True).strip()
        if remote != REPO:
            raise RuntimeError('Build cache belongs to another repository; preserved.')
        run(['git', '-C', source, 'fetch', '--no-tags', '--no-recurse-submodules', 'origin', 'main'])
        fetched = subprocess.check_output(['git', '-C', source, 'rev-parse', 'FETCH_HEAD'],
                                          text=True).strip()
        if fetched != revision:
            raise RuntimeError('Main moved to ' + fetched + '; this command pins ' + revision)
        dirty = subprocess.check_output(['git', '-C', source, 'status', '--porcelain',
                                         '--untracked-files=normal'], text=True)
        if dirty:
            raise RuntimeError('Build source has edits/untracked files; preserved: ' + dirty[:1000])
        run(['git', '-C', source, 'checkout', '--detach', revision])
        run(['git', '-C', source, 'submodule', 'update', '--init', '--recursive', '--jobs', jobs])
        context = work / 'docker-context'
        context.mkdir(exist_ok=True)
        (context / 'Dockerfile').write_text(DOCKERFILE)
        for name in ('container-home', 'ccache'):
            (work / name).mkdir(exist_ok=True)
        run(['docker', 'build', '--tag', IMAGE, context])
        run(['/usr/bin/clang-scan-deps-19', '--version'], docker=True)
        compiler = ['-DCMAKE_C_COMPILER=clang-19', '-DCMAKE_CXX_COMPILER=clang++-19',
                    '-DCMAKE_CXX_COMPILER_CLANG_SCAN_DEPS=/usr/bin/clang-scan-deps-19']
        for suite in ('ngs2_hle', 'userservice', 'occlusion_query', 'image_transfer',
                      'graphics_diagnostics'):
            folder = work / ('default-check-' + suite)
            run(['cmake', '-S', source / 'tests' / suite, '-B', folder, '-G', 'Ninja',
                 '-DCMAKE_BUILD_TYPE=Release', *compiler], docker=True)
            run(['cmake', '--build', folder, '--parallel', jobs], docker=True)
            run(['ctest', '--test-dir', folder, '--no-tests=error', '--output-on-failure'], docker=True)
        run(['python3', '-m', 'unittest', 'discover', '-s', source / 'scripts',
             '-p', 'test_collect_graphics_evidence.py'], docker=True)
        folder = work / 'build'
        # Older CMake cached the missing dependency scanner outside CMakeCache.txt too.
        for path in [folder / 'CMakeCache.txt', *folder.glob('CMakeFiles/*/CMakeCXXCompiler.cmake')]:
            if regular(path):
                before = path.read_text()
                after = before.replace('CMAKE_CXX_COMPILER_CLANG_SCAN_DEPS-NOTFOUND',
                                       '/usr/bin/clang-scan-deps-19')
                if before != after:
                    path.write_text(after)
        run(['cmake', '-S', source, '-B', folder, '-G', 'Ninja', *compiler,
             '-DCMAKE_BUILD_TYPE=Release', '-DENABLE_TESTS=OFF',
             '-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF',
             '-DCMAKE_C_COMPILER_LAUNCHER=ccache', '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache'], docker=True)
        run(['cmake', '--build', folder, '--target', 'shadps4', '--parallel', jobs], docker=True)
    binary = work / 'build/shadps4'
    smoke(binary)
    return binary


def current_core(home, text):
    selected = re.findall(r'# NGS2 isolated core selection: ([0-9a-f]{40})', text)
    if len(selected) == 1:
        core = home / 'Applications/shadps4/releases' / ('ngs2-' + selected[0][:8]) / 'shadps4'
    elif not selected:
        core = home / 'Applications/shadps4/shadps4'
    else:
        raise RuntimeError('Multiple old test selections; preserved.')
    resolved = core.resolve(strict=True)
    if not resolved.is_relative_to(home / 'Applications/shadps4') or not regular(resolved):
        raise RuntimeError('Previous core is outside the known installation; preserved.')
    return resolved


def plan_entries(home, roms, entries, tests):
    removed = {p for p, _ in tests}
    additions = []
    known = set(entries)
    reserved = set(roms.iterdir())
    # Keep an ordinary launch entry for every game that only had a test entry.
    for old, game in tests:
        if game not in known:
            title = ordinary_title(old.stem) or game
            dest = roms / (title + '.ps4')
            if dest in reserved:
                dest = roms / (game + '.ps4')
            if dest in reserved:
                raise RuntimeError('Cannot safely create ordinary entry: ' + str(dest))
            additions.append((dest, (game + '\n').encode()))
            reserved.add(dest)
            known.add(game)
    gamelists = []
    for i, path in enumerate((home / 'ES-DE/gamelists/ps4/gamelist.xml', roms / 'gamelist.xml')):
        if not regular(path):
            continue
        original, tree = read_xml(path)
        changed = False
        for game in list(tree.findall('game')):
            value = game.findtext('path', '')
            if not value:
                continue
            entry = Path(value) if Path(value).is_absolute() else roms / value
            if entry in removed or entry.resolve() in {p.resolve() for p in removed}:
                tree.remove(game)
                changed = True
        if changed:
            gamelists.append((path, original,
                              ET.tostring(tree, encoding='utf-8', xml_declaration=True)))
    return additions, gamelists


def cleanup_entries(home, roms, entries, tests, backup):
    additions, gamelists = plan_entries(home, roms, entries, tests)
    for path, content in additions:
        atomic_write(path, content, 0o644)
    for i, (path, original, replacement) in enumerate(gamelists):
        atomic_write(backup / ('gamelist-' + str(i) + '.xml'), original, 0o600)
        atomic_write(path, replacement, stat.S_IMODE(path.stat().st_mode))
    removed = []
    for i, (path, _) in enumerate(tests):
        shutil.copy2(path, backup / ('entry-' + str(i) + '.ps4'))
        path.unlink()
        removed.append(path)
    return [str(p) for p in removed]


def cleanup_cores(root, keep):
    removed = []
    releases = root / 'releases'
    if releases.is_symlink() or not releases.is_dir():
        raise RuntimeError('Unexpected release directory; preserved.')
    # Only executable payloads and known disposable helpers are removed. A user/
    # directory, saves, configuration, libraries or unknown files are never deleted.
    for directory in sorted(releases.iterdir()):
        if directory == keep or directory.is_symlink() or not directory.is_dir():
            continue
        binary = directory / 'shadps4'
        if regular(binary):
            with binary.open('rb') as stream:
                header = stream.read(5)
            if header == b'\x7fELF\x02':
                binary.unlink()
                removed.append(str(binary))
        elif binary.is_symlink():
            binary.unlink()
            removed.append(str(binary))
        retired = str(binary) in removed or directory.name == 'ngs2-probe'
        for name in ('run_diagnostic.py', 'collect_graphics.py', 'run_probe.py') if retired else ():
            path = directory / name
            if regular(path):
                path.unlink()
        if not any(directory.iterdir()):
            directory.rmdir()
    return removed


def restore(state_file):
    state = json.loads(state_file.read_text())
    home = Path.home()
    core = home / 'Applications/shadps4/shadps4'
    wrapper = home / '.local/bin/shadps4-esde'
    previous = state_file.parent / 'previous/shadps4'
    if (state.get('home') != str(home) or not core.is_symlink() or
            str(core.readlink()) != state['binary'] or
            digest(core) != state['binary_sha256'] or
            digest(wrapper) != state['launcher_sha256'] or
            digest(previous) != state['previous_sha256']):
        raise RuntimeError('Installation changed since this backup; automatic restore stopped.')
    no_running_apps()
    atomic_link(core, previous)
    say('DEFAULT_MAIN_RESTORE=PASS; previous core restored with ordinary ES-DE entries.')


def install(home, revision):
    no_running_apps()
    for tool in ('git', 'docker', 'ldd', 'bash'):
        if not shutil.which(tool):
            raise RuntimeError('Missing tool: ' + tool)
    root = home / 'Applications/shadps4'
    wrapper = home / '.local/bin/shadps4-esde'
    if not regular(wrapper) or root.is_symlink() or not root.is_dir():
        raise RuntimeError('Expected the existing regular shadPS4 installation and launcher.')
    if (root / 'releases').is_symlink() or not (root / 'releases').is_dir():
        raise RuntimeError('Expected a regular release directory; preserved.')
    original = wrapper.read_bytes()
    prefix = guard_prefix(home, original.decode())
    candidate = launcher(home, prefix)
    subprocess.run(['bash', '-n'], input=candidate, check=True)
    roms, entries, tests = inspect_esde(home)
    plan_entries(home, roms, entries, tests)
    previous = current_core(home, original.decode())
    previous_sha = digest(previous)
    core = root / 'shadps4'
    old_link = core.readlink() if core.is_symlink() else None
    if not core.is_symlink() and not regular(core):
        raise RuntimeError('Existing default core is missing or not a file; preserved.')
    built = build(home, revision)
    # A failed build never gets this far. Recheck launchers and active processes
    # after the long compile before touching the usable installation.
    no_running_apps()
    if wrapper.read_bytes() != original or digest(previous) != previous_sha:
        raise RuntimeError('Installation changed during build; default preserved.')
    roms, entries, tests = inspect_esde(home)
    plan_entries(home, roms, entries, tests)
    release = root / 'releases' / ('main-' + revision[:12])
    if release.is_symlink() or (release.exists() and not release.is_dir()):
        raise RuntimeError('Unexpected target release; preserved.')
    release.mkdir(parents=True, exist_ok=True)
    binary = release / 'shadps4'
    if binary.is_symlink():
        raise RuntimeError('Unexpected target binary symlink; preserved.')
    built_sha = digest(built)
    if binary.exists() and digest(binary) != built_sha:
        raise RuntimeError('Target release already contains a different binary; preserved.')
    if not binary.exists():
        shutil.copy2(built, binary)
        binary.chmod(0o755)
    state_root = home / '.local/state/shadps4-default-main'
    state_root.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='install-', dir=state_root))
    backup.chmod(0o700)
    (backup / 'previous').mkdir()
    shutil.copy2(previous, backup / 'previous/shadps4')
    shutil.copy2(wrapper, backup / 'launcher.before')
    if old_link is None:
        shutil.copy2(core, backup / 'default.before')
    shutil.copy2(Path(__file__), backup / 'install_local_default.py')
    state = {'revision': revision, 'home': str(home), 'binary': str(binary),
             'binary_sha256': built_sha, 'previous_sha256': previous_sha,
             'launcher_sha256': hashlib.sha256(candidate).hexdigest(), 'cleanup': 'pending'}
    state_file = backup / 'state.json'
    atomic_write(state_file, (json.dumps(state, indent=2) + '\n').encode(), 0o600)
    no_running_apps()
    if wrapper.read_bytes() != original or digest(previous) != previous_sha:
        raise RuntimeError('Installation changed before the final switch; default preserved.')
    try:
        atomic_link(core, binary)
        atomic_write(wrapper, candidate, stat.S_IMODE(wrapper.stat().st_mode))
        if digest(core) != built_sha or wrapper.read_bytes() != candidate:
            raise RuntimeError('Default selection verification failed.')
    except BaseException:
        if old_link is not None:
            atomic_link(core, old_link)
        else:
            atomic_write(core, (backup / 'default.before').read_bytes(),
                         stat.S_IMODE((backup / 'default.before').stat().st_mode))
        atomic_write(wrapper, original, stat.S_IMODE((backup / 'launcher.before').stat().st_mode))
        raise
    say('DEFAULT_SELECTED=' + str(binary))
    say('RESTORE=python3 ' + shlex.quote(str(backup / 'install_local_default.py')) +
        ' --restore ' + shlex.quote(str(state_file)))
    state['removed_entries'] = cleanup_entries(home, roms, entries, tests, backup)
    state['removed_cores'] = cleanup_cores(root, release)
    state['cleanup'] = 'complete'
    atomic_write(state_file, (json.dumps(state, indent=2) + '\n').encode(), 0o600)
    # This extra copy is only needed for rollback of a failed two-file switch.
    (backup / 'default.before').unlink(missing_ok=True)
    # Retain one previous core across repeated successful invocations. Never
    # discard an edited backup or one still selected by an active default.
    for old_state in state_root.glob('install-*/state.json'):
        if old_state == state_file or not regular(old_state):
            continue
        old_core = old_state.parent / 'previous/shadps4'
        try:
            record = json.loads(old_state.read_text())
            if (record.get('home') == str(home) and regular(old_core) and
                    old_core.resolve() != core.resolve() and
                    digest(old_core) == record.get('previous_sha256')):
                old_core.unlink()
        except (OSError, ValueError):
            continue
    say('REMOVED_TEST_ENTRIES=' + str(len(state['removed_entries'])))
    say('REMOVED_OLD_CORES=' + str(len(state['removed_cores'])))
    say('COMMIT=' + revision)
    say('BINARY_SHA256=' + built_sha)
    say('DEFAULT_MAIN_RESULT=PASS')
    say('Launch the ordinary game entry in ES-DE. One rollback core remains in ' + str(backup))
    say('Saves, settings, 7.1 audio and Sunshine were not changed. Audio remains experimental.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--revision')
    group.add_argument('--restore', type=Path)
    args = parser.parse_args()
    if os.geteuid() == 0 or sys.platform != 'linux' or os.uname().machine != 'x86_64':
        raise RuntimeError('Run as your normal desktop user on Linux x86-64, without sudo.')
    lock_dir = Path.home() / '.local/state/shadps4-ngs2'
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.restore:
            restore(args.restore)
        else:
            if not re.fullmatch(r'[0-9a-f]{40}', args.revision):
                raise RuntimeError('An exact 40-character commit is required.')
            install(Path.home(), args.revision)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, ET.ParseError,
            subprocess.SubprocessError) as error:
        print('DEFAULT_MAIN_RESULT=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
