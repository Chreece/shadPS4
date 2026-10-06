#!/usr/bin/env python3
"""Local Docker build + reversible, same-mode shadPS4 readback A/B trial.

Builds only shadps4 and the isolated tracker tests. Does not launch or kill games,
edit ES-DE/Sunshine, change saves, install host packages, or wait for GitHub CI.
The baseline and candidate both use Relaxed while armed; restore returns the
original binary and original readback setting. Other configuration is preserved.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

BASE = '4f7b012e1dcfe4c7bb44f54504bd735a0c69ec5a'
PATCH = '5c11752b4059599aa36c94328a12be6222a28b04'
REPO = 'https://github.com/Chreece/shadPS4.git'
OLD_IMAGE = 'shadps4-render-playtest-builder:trixie-clang19-v1'
PREVIOUS_IMAGE_TAG = 'shadps4-readback-trial:trixie-gcc14-v2-drm'
IMAGE_TAG = 'shadps4-readback-trial:trixie-gcc14-v3-sdl'
BUILD_CONTRACT = 'docker-gcc14-sdl-complete-v3'
FILES = sorted([
    'src/video_core/buffer_cache/buffer_cache.cpp',
    'src/video_core/buffer_cache/buffer_cache.h',
    'src/video_core/buffer_cache/memory_tracker.h',
    'src/video_core/buffer_cache/region_manager.h',
    'src/video_core/page_manager.cpp',
    'src/video_core/renderer_vulkan/vk_rasterizer.cpp',
])
HOME = Path.home()
WORK = HOME / '.cache/shadps4-clean-verified-main'
SRC = WORK / 'source'
# Never reuse the host-configured CMake cache in Docker. Preserve that tree.
BUILD = SRC / 'build-docker-gcc14-v3'
TRIAL = WORK / 'readback-docker-trial'
REPORT = TRIAL / 'reports'
STATE = TRIAL / 'state.json'
DST = HOME / 'Applications/shadps4/shadps4'
PLAYLOG = HOME / '.local/state/shadps4-playtest-logs'
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path: Path, data: dict) -> None:
    atomic_bytes(path, (json.dumps(data, indent=2) + '\n').encode())


def command(args: list, *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run([str(a) for a in args], cwd=cwd, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if check and p.returncode:
        raise RuntimeError(shlex.join([str(a) for a in args]) + '\n' + p.stderr[-4000:])
    return p


def git(*args: str, check: bool = True) -> str:
    return command(['git', '-C', SRC, *args], check=check).stdout.strip()


def logged(args: list, name: str, *, cwd: Path | None = None, ok: tuple = (0,)) -> int:
    path = REPORT / name
    print(f'{name}: {path}', flush=True)
    offset = path.stat().st_size if path.exists() else 0
    with path.open('ab') as log:
        log.write(('\nCOMMAND=' + shlex.join([str(a) for a in args]) + '\n').encode())
        log.flush()
        proc = subprocess.Popen([str(a) for a in args], cwd=cwd, stdout=log,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        started = time.monotonic()
        try:
            while True:
                try:
                    rc = proc.wait(timeout=20)
                    break
                except subprocess.TimeoutExpired:
                    with path.open('rb') as f:
                        f.seek(max(0, path.stat().st_size - 4096))
                        tail = ANSI.sub('', f.read().decode(errors='replace')).splitlines()
                    last = next((x for x in reversed(tail) if x.strip()), '')
                    print(f'  still running {int(time.monotonic()-started)}s | {last[-150:]}', flush=True)
        except BaseException:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise
    if rc not in ok:
        with path.open('rb') as f:
            f.seek(offset)
            lines = ANSI.sub('', f.read().decode(errors='replace')).splitlines()
        errors = [line for line in lines if re.search(r'fatal error:|error:|CMake Error|undefined reference', line)][:12]
        raise RuntimeError(f'{name}: exit {rc}\nFIRST_ERRORS:\n' + '\n'.join(errors) +
                           '\nLAST_LINES:\n' + '\n'.join(lines[-25:]))
    return rc


def no_game() -> None:
    running = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            exe = Path(os.readlink(p / 'exe').removesuffix(' (deleted)')).name.lower()
            if exe == 'shadps4':
                running.append(p.name)
        except (OSError, PermissionError):
            pass
    if running:
        raise RuntimeError('Close shadPS4 normally first; no process was killed. PIDs=' + ','.join(running))


def install_binary(src: Path, expected: str) -> None:
    if sha(src) != expected:
        raise RuntimeError(f'Binary checksum changed: {src}')
    data = src.read_bytes()
    if not data.startswith(b'\x7fELF') or hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError('Refusing to deploy a non-ELF or changing binary')
    atomic_bytes(DST, data, 0o755)
    if sha(DST) != expected:
        raise RuntimeError('Installed checksum did not match')


def source_clean() -> None:
    git('diff', '--exit-code')
    git('diff', '--cached', '--exit-code')
    sub = git('submodule', 'status', '--recursive')
    if any(line.startswith(('-', '+', 'U')) for line in sub.splitlines()):
        raise RuntimeError('Submodules are missing or at different revisions; see source-state.txt')


def choose_config(explicit: str | None) -> Path:
    candidates = [Path(explicit).expanduser()] if explicit else [
        DST.parent / 'user/config.json',
        HOME / '.local/share/shadPS4/config.json',
        Path(os.environ.get('XDG_DATA_HOME') or HOME / '.local/share') / 'shadPS4/config.json',
    ]
    found = sorted({p.resolve() for p in candidates if p.is_file()})
    if len(found) != 1:
        raise RuntimeError('Cannot uniquely identify the config; nothing was edited. Candidates=' +
                           ', '.join(map(str, found)) + '. Use --config with the active config.json.')
    p = found[0]
    data = json.loads(p.read_text())
    value = data.get('GPU', {}).get('readbacks_mode')
    if type(value) is not int or value not in (0, 1, 2):
        raise RuntimeError(f'Unexpected GPU.readbacks_mode schema in {p}; no guessing')
    custom = p.parent / 'custom_configs/CUSA03745.json'
    if custom.exists():
        game = json.loads(custom.read_text())
        if 'readbacks_mode' in game.get('GPU', {}):
            raise RuntimeError(f'An existing game config overrides the mode: {custom}; left unchanged')
    return p


def change_mode(path: Path, mode: int, allowed: set[int]) -> None:
    before = path.read_bytes()
    data = json.loads(before)
    old = data.get('GPU', {}).get('readbacks_mode')
    if type(old) is not int or old not in allowed:
        raise RuntimeError('Readback setting changed externally; refusing to overwrite it')
    if old == mode:
        return
    data['GPU']['readbacks_mode'] = mode
    if path.read_bytes() != before:
        raise RuntimeError('Configuration changed concurrently')
    atomic_bytes(path, (json.dumps(data, indent=4, ensure_ascii=False) + '\n').encode(),
                 path.stat().st_mode & 0o777)


# Explicit development packages from SDL's Linux build requirements, plus the
# existing emulator toolchain. Installation and all probes run inside Docker.
# https://wiki.libsdl.org/SDL3/README-linux#build-dependencies
SDL_PACKAGES = (
    'libasound2-dev libpulse-dev libaudio-dev libfribidi-dev libjack-dev libsndio-dev '
    'libx11-dev libxext-dev libxrandr-dev libxcursor-dev libxfixes-dev libxi-dev '
    'libxss-dev libxtst-dev libxkbcommon-dev libdrm-dev libgbm-dev libgl1-mesa-dev '
    'libgles2-mesa-dev libegl1-mesa-dev libdbus-1-dev libibus-1.0-dev libudev-dev '
    'libthai-dev libusb-1.0-0-dev libpipewire-0.3-dev libwayland-dev libdecor-0-dev '
    'liburing-dev wayland-protocols'
).split()
BUILD_PACKAGES = (
    'gcc-14 g++-14 mold build-essential cmake ninja-build pkg-config python3 git '
    'ca-certificates libglfw3-dev libopenal-dev libboost-dev libssl-dev uuid-dev '
    'libvulkan-dev fcitx-libs-dev libxinerama-dev libxrender-dev libxt-dev libxv-dev '
    'libxxf86vm-dev'
).split()

DOCKER_PROBE = r"""set -eu
. /etc/os-release
test "$VERSION_CODENAME" = trixie
for x in gcc-14 g++-14 mold cmake python3 git pkg-config make; do
    command -v "$x" >/dev/null
done
for package in __REQUIRED_PACKAGES__; do
    test "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null)" = 'install ok installed'
done
pkg-config --print-errors --exists xtst x11 xext xrandr xfixes xi xcursor xscrnsaver \
    wayland-client libdecor-0 xkbcommon alsa libpulse libdrm gbm ibus-1.0 dbus-1 \
    libpipewire-0.3 jack sndio fribidi liburing libudev libusb-1.0 egl glesv2
# Compile the formerly missing headers, with their real transitive include flags.
d=$(mktemp -d)
trap 'rm -rf "$d"' EXIT
cat > "$d/check.c" <<'C'
#include <ibus.h>
#include <dbus/dbus.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <gbm.h>
#include <pipewire/pipewire.h>
#include <jack/jack.h>
#include <sndio.h>
#include <liburing.h>
#include <libudev.h>
#include <libusb.h>
int main(void) { return 0; }
C
modules='ibus-1.0 dbus-1 libdrm gbm libpipewire-0.3 jack sndio liburing libudev libusb-1.0'
gcc-14 $(pkg-config --cflags $modules) "$d/check.c" \
    -o "$d/check" $(pkg-config --libs $modules)
printf 'SDL_DEPENDENCIES_AND_HEADERS=PASS\n'
""".replace('__REQUIRED_PACKAGES__', ' '.join(SDL_PACKAGES + BUILD_PACKAGES))


def image_id(image: str) -> str | None:
    p = command(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], check=False)
    value = p.stdout.strip()
    if p.returncode != 0:
        return None
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
        raise RuntimeError('Docker returned an unexpected immutable image ID')
    return value


def probe_image(image: str) -> bool:
    p = command(['docker', 'run', '--rm', '--entrypoint', '/bin/bash', image,
                 '-lc', DOCKER_PROBE], check=False)
    REPORT.mkdir(parents=True, exist_ok=True)
    with (REPORT / 'docker-image-preflight.log').open('a') as f:
        f.write(f'IMAGE={image}\nRC={p.returncode}\n{p.stdout}\n{p.stderr}\n')
    return p.returncode == 0


def docker_image(cached: str | None = None, *, allow_repair: bool = True) -> str:
    command(['docker', 'info', '--format', '{{.ServerVersion}}'])
    # Revalidate the saved immutable ID on EVERY incomplete-build resume. Merely
    # rebuilding a tag cannot repair a state.json that still pins the old image.
    cached_id = image_id(cached) if cached else None
    if cached_id and probe_image(cached_id):
        print('DOCKER_IMAGE=VERIFIED_SAVED_IMAGE')
        return cached_id
    if not allow_repair:
        raise RuntimeError('Saved Docker image is missing or lacks required headers. A baseline '
                           'has already been built; refusing to mix A/B toolchains.')
    if cached:
        print('DOCKER_IMAGE=REPAIRING_INCOMPLETE_SAVED_IMAGE')
    available: list[str] = []
    checked = {cached_id} if cached_id else set()
    for image in [IMAGE_TAG, PREVIOUS_IMAGE_TAG, OLD_IMAGE]:
        iid = image_id(image)
        if not iid:
            continue
        available.append(iid)
        if iid in checked:
            continue
        checked.add(iid)
        if probe_image(iid):
            return iid
    # Layer onto the exact existing image when possible. Installs occur only
    # inside Docker; no host package or GPU-driver installation is performed.
    context = TRIAL / 'docker-context-v3-sdl'
    context.mkdir(parents=True, exist_ok=True)
    base_id = cached_id or (available[0] if available else None)
    base = 'debian:trixie-slim'
    if base_id:
        # BuildKit does not accept a local image config digest in FROM. Give
        # the verified ID a dedicated tag; never retag the user's original image.
        base = 'shadps4-readback-base:sdl-' + base_id.split(':', 1)[1]
        command(['docker', 'image', 'tag', base_id, base])
        if image_id(base) != base_id:
            raise RuntimeError('Docker repair base tag did not match the saved image')
    packages = ' '.join(dict.fromkeys(BUILD_PACKAGES + SDL_PACKAGES))
    (context / 'Dockerfile').write_text(f'FROM {base}\nUSER root\nRUN apt-get update && '
        f'DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends {packages} && rm -rf /var/lib/apt/lists/*\n')
    logged(['docker', 'build', '--pull=false', '--progress=plain', '-t', IMAGE_TAG, context],
           'docker-image-sdl-complete.log')
    repaired = image_id(IMAGE_TAG)
    if not repaired or not probe_image(repaired):
        raise RuntimeError('Docker SDL dependency/header verification failed after repair; '
                           'the saved image pin and installed emulator remain unchanged')
    print('SDL_DEPENDENCIES_AND_HEADERS=PASS')
    return repaired


def ensure_trial_image(state: dict) -> str:
    old = state.get('image')
    image = docker_image(old, allow_repair=not bool(state.get('baseline_sha')))
    if old != image:
        state.setdefault('image_history', []).append(dict(previous=old, image=image, time=time.time()))
    state['image'] = image
    write_json(STATE, state)
    return image


def docker_args(image: str, args: list) -> list:
    return ['docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
            '--mount', f'type=bind,source={WORK},target={WORK}',
            '--workdir', SRC, '-e', 'HOME=/tmp', '-e', 'LC_ALL=C.UTF-8',
            '--entrypoint', '/usr/bin/env', image, *args]


def bind_build_environment(image: str) -> None:
    """Reject an unrelated cache instead of silently sharing its feature results."""
    expected = dict(contract=BUILD_CONTRACT, image=image, source=str(SRC),
                    generator='Unix Makefiles', cc='/usr/bin/gcc-14', cxx='/usr/bin/g++-14')
    marker = BUILD / '.docker-build-environment.json'
    if BUILD.is_symlink():
        raise RuntimeError('Docker build directory must not be a symlink')
    if marker.exists():
        if json.loads(marker.read_text()) != expected:
            raise RuntimeError('Build directory belongs to a different environment; left untouched')
    else:
        if BUILD.exists() and any(BUILD.iterdir()):
            raise RuntimeError('Unlabelled build directory is not empty; refusing to reuse its cache')
        BUILD.mkdir(parents=True, exist_ok=True)
        write_json(marker, expected)
    cache = BUILD / 'CMakeCache.txt'
    if cache.exists():
        values = {}
        for line in cache.read_text().splitlines():
            if line and not line.startswith(('#', '//')) and '=' in line and ':' in line:
                key, value = line.split('=', 1)
                values[key.split(':', 1)[0]] = value
        for key, value in {
            'CMAKE_HOME_DIRECTORY': str(SRC), 'CMAKE_GENERATOR': 'Unix Makefiles',
            'CMAKE_C_COMPILER': '/usr/bin/gcc-14', 'CMAKE_CXX_COMPILER': '/usr/bin/g++-14'
        }.items():
            if key in values and values[key] != value:
                raise RuntimeError(f'Docker CMake cache mismatch: {key}; left untouched')
    write_json(REPORT / 'build-environment.json', expected)


def compile_binary(image: str, phase: str, jobs: int) -> tuple[str, str]:
    # One clean Docker-only configure, then incremental baseline/candidate builds.
    # The old host build-verified directory is never deleted or modified.
    bind_build_environment(image)
    print(f'BUILD_DIRECTORY={BUILD}', flush=True)
    args = ['cmake', '-G', 'Unix Makefiles', '-S', SRC, '-B', BUILD,
            '-DCMAKE_BUILD_TYPE=Release', '-DENABLE_TESTS=OFF',
            '-DCMAKE_C_COMPILER=/usr/bin/gcc-14', '-DCMAKE_CXX_COMPILER=/usr/bin/g++-14',
            '-DCMAKE_ASM_COMPILER=/usr/bin/gcc-14', '-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=mold',
            '-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=mold',
            '-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF',
            '-DCMAKE_C_COMPILER_LAUNCHER=', '-DCMAKE_CXX_COMPILER_LAUNCHER=',
            '-DCMAKE_MAKE_PROGRAM=/usr/bin/make', '-DPKG_CONFIG_EXECUTABLE=/usr/bin/pkg-config',
            '-DPython3_EXECUTABLE=/usr/bin/python3', '-DPYTHON_EXECUTABLE=/usr/bin/python3']
    logged(docker_args(image, args), phase + '-configure-v3.log')
    # Compile the entire SDL target FIRST. Its objects are reused by the main build.
    # This tests every selected SDL backend, not just a hand-picked header list.
    logged(docker_args(image, ['cmake', '--build', BUILD, '--target', 'SDL3-static',
                              '--parallel', str(jobs)]), phase + '-sdl-v3.log')
    print('SDL3_STATIC_BUILD=PASS', flush=True)
    logged(docker_args(image, ['cmake', '--build', BUILD, '--target', 'shadps4', '--parallel', str(jobs)]),
           phase + '-build-v3.log')
    binary = BUILD / 'shadps4'
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError('Build did not produce an executable shadps4')
    logged(['timeout', '20', binary, '--help'], phase + '-host-loader-v3.log')
    dest = TRIAL / ('shadps4-' + phase)
    shutil.copy2(binary, dest)
    return str(dest), sha(dest)


def migrate_build_contract(state: dict) -> None:
    if state.get('build_contract') == BUILD_CONTRACT:
        return
    if state.get('ready') or state.get('baseline_sha') or state.get('candidate_head'):
        raise RuntimeError('An older trial already built a variant; refusing to mix environments')
    backup = TRIAL / ('state-before-v3-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    write_json(backup, state)
    state['build_contract'] = BUILD_CONTRACT
    state['build_directory'] = str(BUILD)
    write_json(STATE, state)
    print('BUILD_CACHE=NEW_DOCKER_ONLY_DIRECTORY; old build-verified preserved', flush=True)


def prepare(explicit_config: str | None, jobs: int) -> dict:
    if STATE.exists():
        state = json.loads(STATE.read_text())
        if state.get('base') != BASE or state.get('patch') != PATCH:
            raise RuntimeError('A different trial occupies this directory; left unchanged')
        migrate_build_contract(state)
        if state.get('ready'):
            print('BUILD_CACHE=READY; no rebuild requested')
            return state
    else:
        no_game()
        if git('rev-parse', 'HEAD') != BASE:
            raise RuntimeError('Source is not the captured 4f7b012e baseline; no reset performed')
        source_clean()
        config = choose_config(explicit_config)
        manifest = PLAYLOG / 'current-verified-deployment.txt'
        meta = dict(line.split('=', 1) for line in manifest.read_text().splitlines() if '=' in line)
        if meta.get('stack_head') != BASE or meta.get('sha256') != sha(DST):
            raise RuntimeError('Installed binary no longer matches the captured baseline manifest')
        original = TRIAL / 'shadps4-original'
        shutil.copy2(DST, original)
        shutil.copy2(config, TRIAL / 'original-config.json')
        state = dict(base=BASE, patch=PATCH, started=time.time(), ready=False, history=[],
                     original=str(original), original_sha=sha(original),
                     original_link=os.readlink(DST) if DST.is_symlink() else None,
                     config=str(config), original_mode=json.loads(config.read_text())['GPU']['readbacks_mode'],
                     original_branch=git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False))
        write_json(STATE, state)
        migrate_build_contract(state)
    source_clean()
    head = git('rev-parse', 'HEAD')
    if head not in {BASE, state.get('candidate_head')}:
        raise RuntimeError('Source changed since trial initialization; not modifying it')
    image = ensure_trial_image(state)
    logged(['git', '-C', SRC, 'fetch', '--no-tags', '--no-recurse-submodules', REPO, PATCH], 'fetch.log')
    if sorted(git('diff-tree', '--no-commit-id', '--name-only', '-r', PATCH).splitlines()) != FILES:
        raise RuntimeError('Candidate is not the expected six-file source-only commit')
    (REPORT / 'candidate.patch').write_text(git('show', '--format=', '--binary', PATCH) + '\n')
    helper = TRIAL / 'readback_fence_test.py'
    helper.write_text(git('show', PATCH + ':tests/standalone/readback_fence_test.py') + '\n')
    (REPORT / 'source-state.txt').write_text(git('log', '--oneline', '-12') + '\n' + git('submodule', 'status', '--recursive'))
    if not state.get('baseline_sha'):
        if head != BASE:
            raise RuntimeError('Baseline must be compiled before the source candidate')
        state['baseline'], state['baseline_sha'] = compile_binary(image, 'baseline', jobs)
        write_json(STATE, state)
    if head == BASE:
        branch = 'playtest/local-readback-docker-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        git('switch', '-c', branch)
        try:
            git('-c', 'user.name=Chris Chreece', '-c', 'user.email=68458228+Chreece@users.noreply.github.com',
                '-c', 'commit.gpgsign=false', 'cherry-pick', PATCH)
        except BaseException:
            git('cherry-pick', '--abort', check=False)
            git('switch', state['original_branch'] if state['original_branch'] else '--detach',
                *([] if state['original_branch'] else [BASE]))
            raise
        state['candidate_head'] = git('rev-parse', 'HEAD')
        state['candidate_branch'] = branch
        write_json(STATE, state)
    if git('rev-parse', 'HEAD^') != BASE:
        raise RuntimeError('Candidate is not exactly one commit above baseline')
    if sorted(git('diff', '--name-only', BASE, 'HEAD').splitlines()) != FILES:
        raise RuntimeError('Unexpected source changes above baseline')
    # Ensure loading screen survived; the candidate must not modify emulator.cpp.
    if not (SRC / 'src/imgui/startup_loading.cpp').is_file():
        raise RuntimeError('Loading-screen source missing')
    source_clean()
    for suffix, flags in [('tracker.log', []), ('tracker-sanitizers.log', ['--sanitize'])]:
        logged(docker_args(image, ['python3', helper, '--root', SRC, '--compiler', '/usr/bin/g++-14', *flags]), suffix)
        if 'TRACKER_TESTS=PASS cases=11 randomized_transitions=30000' not in (REPORT / suffix).read_text():
            raise RuntimeError('Tracker test success marker missing')
    state['candidate'], state['candidate_sha'] = compile_binary(image, 'candidate', jobs)
    state['ready'] = True
    write_json(STATE, state)
    return state


def select(state: dict, phase: str) -> None:
    no_game()
    if not state.get('ready'):
        raise RuntimeError('Build has not completed')
    allowed = {state['original_sha'], state['baseline_sha'], state['candidate_sha']}
    if sha(DST) not in allowed:
        raise RuntimeError('Another deployment replaced the emulator; refusing to overwrite it')
    cfg = Path(state['config'])
    # Backup exists before any live file changes; both test variants use the SAME mode.
    current_mode = json.loads(cfg.read_text())['GPU']['readbacks_mode']
    change_mode(cfg, 1, {state['original_mode'], 1})
    try:
        install_binary(Path(state[phase]), state[phase + '_sha'])
    except BaseException:
        change_mode(cfg, current_mode, {1})
        raise
    state['phase'] = phase
    state['history'].append(dict(phase=phase, time=time.time(), mode=1,
        sha256=state[phase + '_sha'], revision=BASE if phase == 'baseline' else state['candidate_head']))
    write_json(STATE, state)
    write_json(REPORT / 'deployment.json', {k: v for k, v in state.items() if k != 'original_link'})
    print(f'\nREADY={phase.upper()}_RELAXED\nREADBACKS_MODE=1\nBINARY_SHA256={sha(DST)}')
    print('Launch The Last Guardian normally in Moonlight -> ES-DE; use the same checkpoint.')
    print('No game launched. ES-DE/Sunshine, saves and audio settings were not edited.')


def restore(state: dict) -> None:
    no_game()
    allowed = {state.get(k) for k in ('original_sha', 'baseline_sha', 'candidate_sha')}
    if sha(DST) not in allowed:
        raise RuntimeError('Installed binary changed externally; refusing to overwrite it')
    cfg = Path(state['config'])
    current_mode = json.loads(cfg.read_text())['GPU']['readbacks_mode']
    if current_mode not in (1, state['original_mode']):
        raise RuntimeError('Readback mode changed externally; restore refused')
    install_binary(Path(state['original']), state['original_sha'])
    if state.get('original_link'):
        link = DST.with_name(DST.name + '.restore-link')
        if link.exists() or link.is_symlink():
            raise RuntimeError('Restore link temporary path already exists')
        link.symlink_to(state['original_link'])
        if sha(link) != state['original_sha']:
            link.unlink()
            raise RuntimeError('Original symlink target changed; restored bytes retained instead')
        os.replace(link, DST)
    change_mode(cfg, state['original_mode'], {1, state['original_mode']})
    state['phase'] = 'restored'
    write_json(STATE, state)
    print(f'RESTORED_ORIGINAL_BINARY={state["original_sha"]}\nRESTORED_READBACKS_MODE={state["original_mode"]}')


def collect(state: dict) -> Path:
    snapshot = TRIAL / 'latest-capture'
    snapshot.mkdir(exist_ok=True)
    audit = []
    copied = []
    for directory in sorted(PLAYLOG.iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            continue
        meta = directory / 'session.meta'
        if not meta.is_file():
            continue
        text = meta.read_text(errors='replace')
        m = re.search(r'^started=(.+)$', text, re.M)
        if not m:
            continue
        started = dt.datetime.fromisoformat(m[1]).timestamp()
        if started < state['started']:
            continue
        dest = snapshot / directory.name
        dest.mkdir(exist_ok=True)
        for filename in ['session.meta', 'runtime.log']:
            f = directory / filename
            if not f.is_file() or f.is_symlink():
                continue
            # Snapshot a bounded byte count so a running producer cannot grow forever.
            limit = min(f.stat().st_size, 128 * 1024 * 1024)
            with f.open('rb') as src, (dest / filename).open('wb') as out:
                remaining = limit
                while remaining:
                    chunk = src.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
            copied.append(dest / filename)
        log = dest / 'runtime.log'
        if log.exists():
            with log.open('rb') as f:
                txt = ANSI.sub('', f.read().decode(errors='replace'))
            revision = re.search(r'\bRevision ([0-9a-f]{40})', txt)
            mode = re.search(r'GPU readbacksMode: (\d+)', txt)
            active = 'Relaxed readback fence protection active:' in txt
            audit.append(dict(session=directory.name, revision=revision[1] if revision else None,
                              mode=int(mode[1]) if mode else None, fence_marker=active))
    write_json(REPORT / 'runtime-audit.json', {'sessions': audit, 'phase_history': state.get('history', [])})
    write_json(REPORT / 'trial-state.json', state)
    archive = HOME / ('shadps4-readback-ab-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as t:
        t.add(REPORT, arcname='readback-ab/reports', recursive=True)
        for p in copied:
            t.add(p, arcname='readback-ab/sessions/' + str(p.relative_to(snapshot)), recursive=False)
    print('ARCHIVE=' + str(archive))
    print(json.dumps(audit, indent=2))
    return archive


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'baseline', 'candidate', 'collect', 'restore'], nargs='?', default='prepare')
    p.add_argument('--config', help='Active config.json only when discovery is ambiguous')
    p.add_argument('--jobs', type=int, default=min(6, os.cpu_count() or 2))
    a = p.parse_args()
    REPORT.mkdir(parents=True, exist_ok=True)
    with (TRIAL / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('ERROR=Another local trial command is running', file=sys.stderr)
            return 1
        try:
            if a.action == 'prepare':
                state = prepare(a.config, max(1, min(a.jobs, 8)))
                select(state, 'baseline')
            else:
                state = json.loads(STATE.read_text())
                if a.action in ('baseline', 'candidate'):
                    select(state, a.action)
                elif a.action == 'restore':
                    restore(state)
                else:
                    collect(state)
            return 0
        except (Exception, KeyboardInterrupt) as e:
            print(f'\nFAILED={e}\nREPORT_DIRECTORY={REPORT}', file=sys.stderr)
            (REPORT / 'failure.txt').write_text(str(e) + '\n')
            print('No game was killed. No SSH session was closed. Restore is available after arming.')
            try:
                fallback = json.loads(STATE.read_text()) if STATE.exists() else {'started': time.time(), 'history': []}
                collect(fallback)
            except Exception as pack_error:
                print(f'ARCHIVE_FAILED={pack_error}; reports remain in {REPORT}', file=sys.stderr)
            return 1
        finally:
            print('SSH_SESSION=REMAINS_OPEN')


if __name__ == '__main__':
    raise SystemExit(main())
