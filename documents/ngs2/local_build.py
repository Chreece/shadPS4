#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build and install a local Linux diagnostic core without a remote CI gate."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import zipfile
import deploy_test as deploy

REVISION = "66a2ef4d25e2029628dad50f5ec9a308ef072c47"
PREVIOUS = deploy.COMMIT


def selection(original, binary):
    text = original.decode()
    home = binary.parents[4]
    candidates = []
    for previous in (PREVIOUS, "ca67919dacf2917140fb957142dcd993737d9dd6",
                     "ac36a0edd40409c3c9ed67dc68c630b7d2dcba7e",
                     "59566b916c3ff680616081c9bcde642e70f874a7"):
        release = home / "Applications/shadps4/releases" / ("ngs2-" + previous[:8])
        command = (shlex.quote(str(release / "shadps4")) + " --game CUSA36843 --fullscreen true"
                   if previous == PREVIOUS else "python3 " + shlex.quote(str(release / "run_diagnostic.py")))
        candidates.append("# NGS2 isolated core selection: " + previous + "\n        " + command)
    found = [block for block in candidates if text.count(block) == 1]
    if len(found) != 1:
        raise RuntimeError("Expected a known earlier NGS2 test selection; launcher left unchanged.")
    old_block = found[0]
    probe_command = f'python3 {home}/Applications/shadps4/releases/ngs2-probe/run_probe.py "$@"'
    restored = text.replace(old_block, probe_command, 1)
    # Validate the complete known dispatcher before replacing its test invocation.
    deploy.selected_probe_wrapper(restored, binary)
    invocation = "python3 " + shlex.quote(str(binary.parent / "run_diagnostic.py"))
    return text.replace(old_block, deploy.MARKER + "\n        " + invocation, 1).encode()


def runner(binary, trace):
    return ('''#!/usr/bin/env python3
from collections import deque
import os
from pathlib import Path
import signal
import subprocess
import sys
binary = BINARY
trace = TRACE
trace.parent.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, SHADPS4_NGS2_DIAGNOSTICS="1")
with trace.open("wb") as report:
    report.write(b"NGS2 diagnostic revision REVISION\\n")
    report.flush()
    process = subprocess.Popen([binary, "--game", "CUSA36843", "--fullscreen", "true"],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    saved = 0
    console_tail = deque(maxlen=512)
    errors = deque(maxlen=128)
    # Drain both streams after the caps, retaining the final crash output too.
    while True:
        line = process.stdout.readline(4096)
        if not line:
            break
        if line.startswith(b"NGS2_DIAG "):
            if saved < 2048:
                report.write(line[:1024])
                report.flush()
                saved += 1
        else:
            console_tail.append(line)
            if any(marker in line.lower() for marker in
                   (b"<error>", b"<critical>", b"unhandled", b"assertion", b"fatal")):
                errors.append(line)
    process.stdout.close()
    code = process.wait()
    report.write(b"\\nEMULATOR_ERROR_TAIL (at most 512 KiB)\\n")
    report.writelines(errors)
    report.write(b"\\nEMULATOR_CONSOLE_TAIL (stdout and stderr, at most 2 MiB)\\n")
    report.writelines(console_tail)
    report.write(("exit_code=%s\\n" % code).encode())
    if code < 0:
        report.write(("signal=%s\\n" % signal.Signals(-code).name).encode())
sys.exit(128 - code if code < 0 else code)
'''.replace('BINARY', repr(str(binary))).replace('TRACE', 'Path(' + repr(str(trace)) + ')')
        .replace('REVISION', REVISION)).encode()


DOCKERFILE = """FROM debian:trixie-slim
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
"""


def container_command(work, image, args):
    # Only the build workspace is mounted, under the host UID/GID. No GPU,
    # game directories, desktop sockets or Docker socket enter the builder.
    return ['docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
            '--mount', f'type=bind,src={work},dst={work}',
            '--workdir', str(work), '--env', 'HOME=' + str(work / 'container-home'),
            '--env', 'CCACHE_DIR=' + str(work / 'ccache'), image, *args]



def repair_scan_deps_cache(directory):
    # CMake persists a missing scanner in its compiler-description file, not
    # only CMakeCache.txt. Repair that exact sentinel without deleting objects.
    files = [directory / 'CMakeCache.txt']
    files.extend(directory.glob('CMakeFiles/*/CMakeCXXCompiler.cmake'))
    for path in files:
        if path.is_file() and not path.is_symlink():
            text = path.read_text()
            fixed = text.replace('CMAKE_CXX_COMPILER_CLANG_SCAN_DEPS-NOTFOUND',
                                 '/usr/bin/clang-scan-deps-19')
            if fixed != text:
                path.write_text(fixed)


def main():
    if os.geteuid() == 0:
        raise RuntimeError("Run as your normal user, without sudo.")
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise RuntimeError("This local build targets Linux x86-64.")
    os.environ['PATH'] = str(Path.home() / '.local/bin') + os.pathsep + os.environ['PATH']
    use_docker = '--docker' in sys.argv[1:]
    required = ('git', 'docker') if use_docker else ('git', 'cmake', 'ctest', 'ninja', 'clang-19', 'clang++-19', 'pkg-config')
    missing = [tool for tool in required if not shutil.which(tool)]
    if missing:
        raise RuntimeError("Missing build tools: " + ', '.join(missing))
    home = Path.home()
    work = home / '.cache/shadps4-ngs2-local' / ('ca67919d' + ('-docker' if use_docker else ''))
    work.mkdir(parents=True, exist_ok=True)
    logfile = work / 'build.log'
    print('BUILD_LOG=' + str(logfile), flush=True)
    deploy.COMMIT = REVISION
    deploy.MARKER = '# NGS2 isolated core selection: ' + REVISION
    deploy.BUILD = 'local'
    binary = home / 'Applications/shadps4/releases' / ('ngs2-' + REVISION[:8]) / 'shadps4'
    wrapper = home / '.local/bin/shadps4-esde'
    if wrapper.is_symlink():
        raise RuntimeError('Unexpected launcher symlink.')
    selection(wrapper.read_bytes(), binary)
    deploy.no_running_core()
    source = work / 'source'
    jobs = str(min(8, os.cpu_count() or 2))
    image = 'shadps4-ngs2-builder:trixie-clang19-v1'
    with logfile.open('ab') as log:
        def run(args):
            if use_docker and args[0] in ('cmake', 'ctest'):
                args = container_command(work, image, args)
            print('STEP=' + shlex.join([str(x) for x in args]), flush=True)
            result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT)
            if result.returncode:
                log.flush()
                print(logfile.read_text(errors='replace')[-6000:])
                raise RuntimeError('Build step failed; upload ' + str(logfile))
        if use_docker:
            run(['docker', 'info', '--format', '{{.ServerVersion}}'])
            context = work / 'docker-context'
            context.mkdir(exist_ok=True)
            (context / 'Dockerfile').write_text(DOCKERFILE)
            (work / 'container-home').mkdir(exist_ok=True)
            (work / 'ccache').mkdir(exist_ok=True)
            run(['docker', 'build', '--tag', image, str(context)])
            run(container_command(work, image, ['/usr/bin/clang-scan-deps-19', '--version']))
            repair_scan_deps_cache(work / 'build')
            repair_scan_deps_cache(work / 'focused')
        if not source.exists():
            run(['git', 'clone', '--no-checkout', 'https://github.com/Chreece/shadPS4.git', source])
        run(['git', '-C', source, 'fetch', 'origin', REVISION])
        # This checkout belongs solely to this pinned local build; refuse edits.
        dirty = subprocess.check_output(['git', '-C', source, 'status', '--porcelain', '--untracked-files=no'], text=True)
        if dirty and (source / 'CMakeLists.txt').exists():
            raise RuntimeError('Local build source has edits; refusing to overwrite them.')
        run(['git', '-C', source, 'checkout', '--detach', REVISION])
        run(['git', '-C', source, 'submodule', 'update', '--init', '--recursive', '--jobs', jobs])
        compiler = ['-DCMAKE_C_COMPILER=clang-19', '-DCMAKE_CXX_COMPILER=clang++-19']
        if use_docker:
            compiler += ['-DCMAKE_CXX_COMPILER_CLANG_SCAN_DEPS=/usr/bin/clang-scan-deps-19']
        focused = work / 'focused'
        run(['cmake', '-S', source / 'tests/ngs2_hle', '-B', focused, '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release', *compiler])
        run(['cmake', '--build', focused, '--parallel', jobs])
        run(['ctest', '--test-dir', focused, '--output-on-failure'])
        startup = work / 'userservice-test'
        run(['cmake', '-S', source / 'tests/userservice', '-B', startup, '-G', 'Ninja',
             '-DCMAKE_BUILD_TYPE=Release', *compiler])
        run(['cmake', '--build', startup, '--parallel', jobs])
        run(['ctest', '--test-dir', startup, '--output-on-failure'])
        build = work / 'build'
        options = ['-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE=OFF']
        if use_docker or shutil.which('ccache'):
            options += ['-DCMAKE_C_COMPILER_LAUNCHER=ccache', '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache']
        run(['cmake', '-S', source, '-B', build, '-G', 'Ninja', *compiler, *options])
        run(['cmake', '--build', build, '--target', 'shadps4', '--parallel', jobs])
    archive = work / 'local-build.zip'
    with zipfile.ZipFile(archive, 'w') as zipped:
        zipped.write(build / 'shadps4', 'shadps4')
    payload = archive.read_bytes()
    metadata = {'id': 'local-' + REVISION, 'size_in_bytes': len(payload),
                'digest': 'sha256:' + deploy.digest(payload)}
    deploy.artifact_metadata = lambda wait: metadata
    deploy.download = lambda artifact, destination: shutil.copyfile(archive, destination)
    deploy.selected_wrapper = selection
    binary.parent.mkdir(parents=True, exist_ok=True)
    trace = home / ('ngs2-diagnostic-' + REVISION[:8] + '.log')
    helper = binary.parent / 'run_diagnostic.py'
    content = runner(binary, trace)
    if helper.is_symlink() or (helper.exists() and helper.read_bytes() != content):
        raise RuntimeError('Diagnostic helper already exists with different contents.')
    deploy.atomic_write(helper, content, 0o700)
    # Reuses checked ELF/startup, atomic switch, backup, and verified rollback.
    deploy.install(home, 0)
    print('TRACE_FILE=' + str(trace))
    print('Test the NGS2 probe entry, then close the game and upload TRACE_FILE.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print('NGS2_LOCAL_RESULT=FAIL: ' + str(error), file=sys.stderr)
        sys.exit(1)
