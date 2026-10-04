#!/usr/bin/env python3
"""Check the added snapshots on an isolated two-thread target before launching PES."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

FIXTURE = r'''
#include <pthread.h>
#include <atomic>
#include <thread>
#include <unistd.h>
std::atomic<unsigned> gate{17};
std::atomic<bool> ready{false};
__attribute__((noinline)) void checkpoint() { asm volatile("" ::: "memory"); }
__attribute__((noinline)) void waiting_caller() { checkpoint(); gate.fetch_add(1); }
int main() {
    pthread_setname_np(pthread_self(), "Game:Main");
    std::thread worker([] {
        pthread_setname_np(pthread_self(), "JobExecutor");
        ready.store(true);
        while(gate.load()<18) usleep(1000);
    });
    while(!ready.load()) usleep(1000);
    waiting_caller();
    worker.join();
    return 0;
}
'''

PROBE = r'''
import gdb, sys, time, json
from pathlib import Path
sys.path.insert(0, "/test")
import pes_frame_profile
result = {}
proc = Path("/proc") / str(gdb.selected_inferior().pid)
def memory(address, size): return bytes(gdb.selected_inferior().read_memory(address, size))
def register(name): return int(gdb.parse_and_eval("$" + name))
def number(data, offset, size=8): return int.from_bytes(data[offset:offset+size], "little")
exec(compile(pes_frame_profile.SUPPORT, "<profile>", "exec"))
guest_wait_snapshot("before")
guest_wait_snapshot("after")
Path("/results/snapshots.json").write_text(json.dumps(result, indent=2))
'''

DOCKERFILE = '''FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends g++ gdb python3 \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /test
COPY fixture.cpp probe.py pes_frame_profile.py ./
RUN g++ -std=c++20 -g -O1 -no-pie -pthread fixture.cpp -o fixture
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["gdb", "--batch", "-ex", "set debuginfod enabled off", "-ex", "break checkpoint", "-ex", "run", "-ex", "source /test/probe.py", "-ex", "continue", "/test/fixture"]
'''


def check_samples(result, fixture=False):
    samples = result.get('guest_wait_samples', [])
    if [sample.get('phase') for sample in samples] != ['before', 'after']:
        raise RuntimeError('Both guest wait snapshots are required')
    for sample in samples:
        main = next((t for t in sample.get('threads', []) if t.get('name') == 'Game:Main'), None)
        if (main is None or not main.get('registers') or not main.get('frames') or
                not sample.get('code') or not sample.get('memory') or
                not 0 < sample.get('read_bytes', 0) <= 32768):
            raise RuntimeError('Guest wait snapshot lacks registers, caller code or memory')
        if fixture:
            if not any(t.get('name') == 'JobExecutor' for t in sample['threads']):
                raise RuntimeError('Worker thread was not captured')
            if not any(f.get('symbol') in ('waiting_caller', 'waiting_caller()')
                       for f in main['frames']):
                raise RuntimeError('Native caller was not unwound')
            if sample.get('seconds', 99) > 1.25:
                raise RuntimeError('Disposable snapshot exceeded its timing budget')
            if main.get('error') or sample.get('errors'):
                raise RuntimeError('Disposable snapshot had read errors')


def validate(work, token):
    if not re.fullmatch(r'[0-9a-f]{32}', token):
        raise RuntimeError('Invalid preflight ownership token')
    context, results = work / 'build', work / 'results'
    context.mkdir(parents=True)
    results.mkdir()
    (context / 'fixture.cpp').write_text(FIXTURE)
    (context / 'probe.py').write_text(PROBE)
    (context / 'Dockerfile').write_text(DOCKERFILE)
    shutil.copy2(Path(__file__).with_name('pes_frame_profile.py'), context)
    fingerprint = hashlib.sha256(b''.join(p.read_bytes() for p in sorted(context.iterdir()))).hexdigest()[:16]
    image = 'shadps4-pes-guest-wait:' + fingerprint
    print('PES_WAIT_PREFLIGHT=checking the new snapshots on a disposable target', flush=True)
    with (work / 'build.log').open('w') as log:
        subprocess.run(['docker', 'build', '-t', image, str(context)],
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    command = ['docker', 'run', '--rm', '--label', 'org.shadps4.pes-test=' + token,
               '--network', 'none', '--cap-add', 'SYS_PTRACE', '--read-only',
               '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m', '--pids-limit', '64',
               '--memory', '512m', '--cpus', '2', '--user', f'{os.getuid()}:{os.getgid()}',
               '--mount', f'type=bind,src={results},dst=/results', image]
    with (work / 'gdb.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
    check_samples(json.loads((results / 'snapshots.json').read_text()), fixture=True)
    print('PES_WAIT_PREFLIGHT=PASS', flush=True)
