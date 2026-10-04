#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build/run real-GDB tests in an isolated Docker container, never against PES."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

import trace_video_progress as trace
import pes_frame_profile as frames


FIXTURE_CPP = r'''
#include <array>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <thread>

using Bytes = unsigned char;
template <typename T> void put(Bytes* out, unsigned offset, T value) {
    std::memcpy(out + offset, &value, sizeof(value));
}
namespace Libraries::Videodec2 {
__attribute__((noinline, used)) int sceVideodec2CreateDecoder(Bytes* config, void*, void*, void*) {
    return config[0] == 0x48 ? 0 : 1;
}
__attribute__((noinline, used)) int sceVideodec2Decode(void*, Bytes*, Bytes* frame, Bytes* output) {
    static unsigned count = 0;
    if (++count % 5 == 0) return static_cast<int>(0x80690001u);
    frame[24] = 1;
    output[8] = 1;
    output[9] = 0;
    output[10] = 1;
    put<std::uint32_t>(output, 16, 1280);
    put<std::uint32_t>(output, 20, 1280);
    put<std::uint32_t>(output, 24, 720);
    return 0;
}
__attribute__((noinline, used)) int sceVideodec2Flush(void*, Bytes* frame, Bytes* output) {
    frame[24] = 1;
    output[8] = 0;
    output[10] = 0;
    return 0;
}
}
// EXTRA_SYMBOLS
// FRAME_SYMBOLS
int main(int argc, char** argv) {
    if (argc != 3) return 2;
    const std::filesystem::path dir(argv[1]);
    const std::string mode(argv[2]);
    std::array<Bytes, 0x48> config{};
    std::array<Bytes, 0x30> input{}, output{};
    std::array<Bytes, 0x20> frame{};
    put<std::uint64_t>(config.data(), 0, 0x48);
    put<std::uint32_t>(config.data(), 12, 1);
    put<std::uint32_t>(config.data(), 24, 1280);
    put<std::uint32_t>(config.data(), 28, 720);
    put<std::uint64_t>(input.data(), 0, 0x30);
    put<std::uint64_t>(input.data(), 16, 4096);
    put<std::uint64_t>(input.data(), 24, 90000);
    put<std::uint64_t>(input.data(), 32, 89999);
    const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(120);
    std::uint64_t ticks = 0;
    while (std::chrono::steady_clock::now() < end) {
        { std::ofstream out(dir / "heartbeat.next"); out << ++ticks << '\n'; }
        std::filesystem::rename(dir / "heartbeat.next", dir / "heartbeat");
        if (std::filesystem::exists(dir / "go")) {
            if (mode == "exit") return 0;
            if (mode == "active") {
                Libraries::Videodec2::sceVideodec2CreateDecoder(config.data(), nullptr, nullptr, nullptr);
                Libraries::Videodec2::sceVideodec2Decode(nullptr, input.data(), frame.data(), output.data());
                Libraries::Videodec2::sceVideodec2Flush(nullptr, frame.data(), output.data());
            }
            if (mode == "frames-active") frame_activity();
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    return 0;
}
'''.replace('// EXTRA_SYMBOLS', 'namespace Libraries::Videodec {\n' + '\n'.join(
    f'__attribute__((noinline, used)) int sceVideodecFixture{i:02d}() {{ return {i}; }}'
    for i in range(33)) + '\n}').replace('// FRAME_SYMBOLS', frames.FIXTURE_CPP)

DOCKERFILE = '''FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends g++ gdb python3 \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /test
COPY trace_video_progress.py validate_video_diagnostic.py pes_frame_profile.py fixture.cpp ./
RUN g++ -std=c++20 -O0 -fno-omit-frame-pointer -fno-inline -pthread fixture.cpp -o fixture \\
    && touch /run/pes-fixture-container
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python3", "-u", "/test/validate_video_diagnostic.py", "--inside-container"]
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def stop_fixture(child):
    """Only Popen objects created by this disposable-container test are accepted."""
    if child is not None:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def heartbeat(work):
    return int((work / 'heartbeat').read_text().strip())


def validate_observations(mode, result, profile='video'):
    if profile == 'frames':
        frames.validate_observations(mode, result, require)
        return
    apis = result['apis']
    require(len(apis) == 36, f'Expected 36 resolved APIs, got {len(apis)}')
    if mode == 'idle':
        require(all(v['calls'] == 0 for v in apis.values()), 'Idle target recorded API activity')
    elif mode == 'active':
        for api in ('sceVideodec2CreateDecoder', 'sceVideodec2Decode', 'sceVideodec2Flush'):
            require(apis[api]['calls'] == 32 and apis[api]['returns'] == 32 and apis[api]['capped'],
                    f'Incomplete entry/return/cap accounting for {api}: {apis[api]}')
        decode = apis['sceVideodec2Decode']
        require(decode['errors'] == {'0x80690001': 6}, f'Unexpected decode errors: {decode}')
        require(decode['valid_frames'] == 26, f'Unexpected valid frames: {decode}')
        inputs = [r for r in result['records'] if r['event'] == 'enter' and
                  r.get('api') == 'sceVideodec2Decode']
        decoded_frames = [r for r in result['records'] if r.get('valid')]
        require(len(inputs) == 32 and all(r.get('au_bytes') == 4096 and
                r.get('pts') == 90000 and r.get('dts') == 89999 for r in inputs),
                'Input fields did not match fixture values')
        require(len(decoded_frames) == 26 and all(r.get('width') == 1280 and
                r.get('height') == 720 and r.get('pitch') == 1280 and
                r.get('frame_accepted') is True for r in decoded_frames),
                'Output fields did not match fixture values')


def run_case(root, mode, profile='video'):
    work = root / mode
    work.mkdir()
    target = debugger = decoy = None
    controller = None
    finished = threading.Event()
    controller_errors = []
    try:
        fixture_mode = 'frames-active' if profile == 'frames' and mode == 'active' else mode
        target = subprocess.Popen(['/test/fixture', str(work), fixture_mode], start_new_session=True)
        until = time.monotonic() + 5
        while not (work / 'heartbeat').exists():
            require(target.poll() is None and time.monotonic() < until, 'Fixture failed to start')
            time.sleep(0.01)
        proc = Path('/proc') / str(target.pid)
        identity = {'pid': target.pid, 'executable': '/test/fixture',
                    'start_ticks': (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19],
                    'sha256': hashlib.sha256(Path('/test/fixture').read_bytes()).hexdigest()}
        (work / 'identity.json').write_text(json.dumps(identity))
        probe = work / 'probe.py'
        # Exercise repeated canonical addresses from overlapping GDB queries too.
        trace.write_probe(identity, probe, profile=profile,
                          extra_symbol_queries=("Vulkan::Rasterizer::DrawIndirect",) if profile == 'frames' else ())
        if mode == 'interrupt':
            other = work / 'unrelated.py'
            other.write_text('import time\ntime.sleep(100)\n')
            with (work / 'decoy.txt').open('w') as output:
                decoy = subprocess.Popen(trace.debugger_command(identity, other),
                                         stdout=output, stderr=subprocess.STDOUT,
                                         start_new_session=True)

        def control():
            try:
                until = time.monotonic() + trace.SETUP_SECONDS
                while not finished.wait(0.02):
                    if 'PES_VIDEO_ARMED=' in (work / 'gdb.txt').read_text(errors='replace'):
                        (work / 'go').touch()
                        if mode == 'interrupt' and not finished.wait(1):
                            trace.interrupt_debugger(work, [])
                        return
                    if time.monotonic() > until:
                        raise RuntimeError('GDB never armed the fixture')
            except Exception as error:
                controller_errors.append(str(error))

        started = time.monotonic()
        with (work / 'gdb.txt').open('w') as output:
            debugger = subprocess.Popen(trace.debugger_command(identity, probe),
                                        stdout=output, stderr=subprocess.STDOUT,
                                        start_new_session=True)
            controller = threading.Thread(target=control, daemon=True)
            controller.start()
            outcome = trace.wait_for_debugger(debugger, work, [])
        finished.set()
        controller.join(timeout=12)
        require(not controller.is_alive(), 'Fixture controller did not exit')
        result = trace.build_report((work / 'gdb.txt').read_text(errors='replace'),
                                    outcome, trace.inspect_target(identity), profile=profile)
        result['controller_errors'] = controller_errors
        (work / 'result.json').write_text(json.dumps(result, indent=2))
        require(not controller_errors, str(controller_errors))
        require(outcome['debugger_exited'], 'Debugger did not exit; test fixture will be removed')
        if mode in ('idle', 'active'):
            require(trace.report_passed(result), json.dumps(result.get('errors')) +
                    '; status=' + result['status'])
            validate_observations(mode, result, profile)
        elif mode == 'interrupt':
            require(result['status'] == 'interrupted' and result['cleanup_verified'] and
                    result.get('detached') is True and not trace.report_passed(result),
                    'Interrupted capture did not fail and detach correctly: ' + json.dumps(result))
            require(decoy.poll() is None, 'Interrupt helper affected unrelated GDB')
        else:
            require(result['status'] == 'target_exited' and not trace.report_passed(result),
                    'Target exit was misclassified: ' + json.dumps(result))
        if mode != 'exit':
            before = heartbeat(work)
            time.sleep(0.3)
            require(target.poll() is None and heartbeat(work) > before,
                    'Fixture did not resume after detach')
        print(f'CASE={mode} PASS elapsed={time.monotonic()-started:.3f}s', flush=True)
    except BaseException:
        log = work / 'gdb.txt'
        if log.exists():
            print('GDB_FAILURE_TAIL_BEGIN', flush=True)
            print(log.read_text(errors='replace')[-6000:], flush=True)
            print('GDB_FAILURE_TAIL_END', flush=True)
        raise
    finally:
        finished.set()
        if controller is not None:
            controller.join(timeout=12)
        stop_fixture(debugger)
        stop_fixture(decoy)
        stop_fixture(target)


def inside_container():
    require(Path('/run/pes-fixture-container').exists(), 'Run the Docker launcher, not this flag on the host')
    root = Path('/results')
    profile = os.environ.get('PES_CAPTURE_PROFILE', 'video')
    trace.capture_profile(profile)
    try:
        print(subprocess.check_output(['gdb', '--version'], text=True).splitlines()[0], flush=True)
        for mode in ('idle', 'active', 'interrupt', 'exit'):
            run_case(root, mode, profile)
        print('PES_DIAGNOSTIC_SELFTEST=PASS', flush=True)
    finally:
        owner = os.environ.get('SELFTEST_OWNER', '').split(':')
        if len(owner) == 2 and all(x.isdigit() for x in owner):
            for path in root.rglob('*'):
                os.chown(path, int(owner[0]), int(owner[1]), follow_symlinks=False)


def docker_run_command(prefix, image, name, results, profile='video'):
    return prefix + ['docker', 'run', '--rm', '--name', name,
                     '--network', 'none', '--cap-add', 'SYS_PTRACE',
                     '--read-only', '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m',
                     '--pids-limit', '128', '--memory', '512m', '--cpus', '2',
                     '--env', f'SELFTEST_OWNER={os.getuid()}:{os.getgid()}',
                     '--env', 'PES_CAPTURE_PROFILE=' + profile,
                     '--mount', f'type=bind,src={results},dst=/results', image]


def launch(profile='video'):
    trace.capture_profile(profile)
    require(shutil.which('docker'), 'Docker is not installed; no emulator changes were made')
    prefix = []
    check = subprocess.run(['docker', 'info'], capture_output=True, text=True, timeout=20)
    if check.returncode:
        require(shutil.which('sudo'), 'Docker is unavailable: ' + check.stderr[-500:])
        print('Docker needs sudo; enter your password if prompted.', flush=True)
        subprocess.run(['sudo', '-v'], check=True)
        prefix = ['sudo', '-n']
        subprocess.run(prefix + ['docker', 'info'], check=True, stdout=subprocess.DEVNULL, timeout=20)
    work = Path(tempfile.mkdtemp(prefix='shadps4-pes-selftest-', dir=Path.home()))
    context = work / 'build'
    results = work / 'results'
    context.mkdir()
    results.mkdir()
    here = Path(__file__).resolve().parent
    for name in ('trace_video_progress.py', 'validate_video_diagnostic.py', 'pes_frame_profile.py'):
        shutil.copy2(here / name, context / name)
    (context / 'fixture.cpp').write_text(FIXTURE_CPP)
    (context / 'Dockerfile').write_text(DOCKERFILE)
    fingerprint = hashlib.sha256(b''.join(p.read_bytes() for p in sorted(context.iterdir()))).hexdigest()[:12]
    image = 'shadps4-pes-selftest:' + fingerprint
    name = 'pes-selftest-' + work.name.rsplit('-', 1)[-1]
    print('Building isolated GDB test image. First build needs network access.', flush=True)
    subprocess.run(prefix + ['docker', 'build', '-t', image, str(context)],
                   check=True, timeout=900, start_new_session=True)
    print('Testing the diagnostic on four disposable targets; about one minute.', flush=True)
    command = docker_run_command(prefix, image, name, results, profile)
    log = work / 'selftest.log'
    child = None
    try:
        with log.open('w') as output:
            child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                     start_new_session=True)
            position = 0
            deadline = time.monotonic() + 180
            while child.poll() is None:
                time.sleep(0.2)
                with log.open() as source:
                    source.seek(position)
                    chunk = source.read()
                    position = source.tell()
                if chunk:
                    print(chunk, end='', flush=True)
                require(time.monotonic() < deadline, 'Self-test exceeded 180 seconds; removing its container')
            with log.open() as source:
                source.seek(position)
                print(source.read(), end='', flush=True)
        require(child.returncode == 0 and 'PES_DIAGNOSTIC_SELFTEST=PASS' in log.read_text(),
                'GDB validation failed; do not run the PES capture yet')
    finally:
        # Remove only this randomly named disposable container, even after Ctrl+C.
        subprocess.run(prefix + ['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15, check=False)
        if child is not None:
            stop_fixture(child)
        archive = work / 'selftest-evidence.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            output.add(results, arcname='results')
            if log.exists():
                output.add(log, arcname='selftest.log')
        print('SELFTEST_EVIDENCE=' + str(archive), flush=True)
        print('SSH session preserved; returning to your existing shell.', flush=True)


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--inside-container']:
            inside_container()
        else:
            require(not sys.argv[1:], 'Unknown argument')
            launch()
    except (Exception, KeyboardInterrupt) as error:
        print('PES_DIAGNOSTIC_SELFTEST=FAIL: ' + (str(error) or 'Interrupted'), flush=True)
        sys.exit(1)
