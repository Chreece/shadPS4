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
#include <cstring>
#include <stdexcept>
#include <sys/mman.h>
#include <thread>
#include <unistd.h>
std::atomic<unsigned> gate{17};
std::atomic<bool> ready{false};
__attribute__((noinline)) void checkpoint() { asm volatile("" ::: "memory"); }
__attribute__((noinline)) void waiting_caller() { checkpoint(); gate.fetch_add(1); }
void prepare_startup_stage() {
    for (auto address : {0x6c8000ul, 0x5e7f000ul, 0x4c11000ul, 0x6ce000ul, 0x6cf000ul,
                         0x3639000ul}) {
        if (mmap(reinterpret_cast<void*>(address), 4096, PROT_READ | PROT_WRITE,
                 MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0) == MAP_FAILED)
            throw std::runtime_error("fixture mapping unavailable");
    }
    const unsigned char first[]{0x8b,0x05,0xfb,0x68,0x7b,0x05,0x83,0xf8,0x0f,
                                0x0f,0x84,0x8b,0x00,0x00,0x00};
    const unsigned char second[]{0x48,0x98,0x48,0xc1,0xe0,0x05,0x42,0xff,0x54,0x38,0x10};
    std::memcpy(reinterpret_cast<void*>(0x6c8d3b), first, sizeof(first));
    std::memcpy(reinterpret_cast<void*>(0x6c8d73), second, sizeof(second));
    *reinterpret_cast<unsigned*>(0x5e7f63c) = 2;
    *reinterpret_cast<void(**)()>(0x4c113d0 + 2*32 + 16) = waiting_caller;
    const unsigned char listener[]{0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,
        0x41,0x55,0x41,0x54,0x53,0x48,0x83,0xec,0x48,
        0x83,0x47,0x30,0x01,0x48,0x83,0xc4,0x48,0x5b,0x41,0x5c,0x41,0x5d,
        0x41,0x5e,0x41,0x5f,0x5d,0xc3};
    const unsigned char poll[]{0x55,0x48,0x89,0xe5,0x41,0x57,0x41,0x56,0x53,0x48,0x83,0xec,0x18};
    std::memcpy(reinterpret_cast<void*>(0x6ce910), listener, sizeof(listener));
    std::memcpy(reinterpret_cast<void*>(0x6ce3a0), poll, sizeof(poll));
    *reinterpret_cast<unsigned long*>(0x5e7f988) = 0x5e7fa00;
    *reinterpret_cast<unsigned long*>(0x5e7fa00) = 0x4c11a68;
    *reinterpret_cast<unsigned*>(0x5e7fa30) = 1;
    for (unsigned i = 0; i < 19; ++i)
        *reinterpret_cast<int*>(0x3639dd0 + 4*i) = 0x6ce921 - 0x3639dd0;
    if (mprotect(reinterpret_cast<void*>(0x6ce000), 8192, PROT_READ | PROT_EXEC))
        throw std::runtime_error("fixture listener mapping failed");
    if (mprotect(reinterpret_cast<void*>(0x6c8000), 4096, PROT_READ | PROT_EXEC))
        throw std::runtime_error("fixture executable mapping failed");
}
int main() {
    pthread_setname_np(pthread_self(), "Game:Main");
    prepare_startup_stage();
    std::thread worker([] {
        pthread_setname_np(pthread_self(), "JobExecutor");
        ready.store(true);
        while(gate.load()<18) usleep(1000);
    });
    while(!ready.load()) usleep(1000);
    waiting_caller();
    unsigned event = 2;
    reinterpret_cast<void(*)(void*,void*,void*)>(0x6ce910)(
        reinterpret_cast<void*>(0x5e7fa00), nullptr, &event);
    worker.join();
    return 0;
}
'''

PROBE = r'''
import ast, gdb, sys, time, json
from pathlib import Path
sys.path.insert(0, "/test")
import pes_frame_profile
import trace_video_progress
result = {}
proc = Path("/proc") / str(gdb.selected_inferior().pid)
def memory(address, size): return bytes(gdb.selected_inferior().read_memory(address, size))
def register(name): return int(gdb.parse_and_eval("$" + name))
def number(data, offset, size=8): return int.from_bytes(data[offset:offset+size], "little")
exec(compile(pes_frame_profile.SUPPORT, "<profile>", "exec"))
guest_wait_snapshot("before")
startup_stage_snapshot("before")
guest_wait_snapshot("after")
startup_stage_snapshot("after")
assert all(s["stage_index"] == 2 for s in result["startup_stage_samples"])
expected = hex(int(gdb.parse_and_eval("(void*)&waiting_caller")))
assert all(s["callbacks"][2]["poll_callback"] == expected
           for s in result["startup_stage_samples"])
assert all(any(c["pc"] == expected and c["kind"] == "active startup callback"
               for c in s["code"]) for s in result["startup_stage_samples"])
gdb.selected_inferior().write_memory(0x5e7f63c, (99).to_bytes(4, "little"))
startup_stage_snapshot("invalid-index")
assert result["startup_stage_samples"].pop()["status"] == "invalid_stage"
gdb.selected_inferior().write_memory(0x6c8d3b, b"\x00")
startup_stage_snapshot("wrong-image")
assert result["startup_stage_samples"].pop()["status"] == "signature_mismatch"
gdb.selected_inferior().write_memory(0x6c8d3b, b"\x8b")
gdb.selected_inferior().write_memory(0x5e7f63c, (2).to_bytes(4, "little"))
for s in result["startup_stage_samples"]:
    assert s['listener']['substate'] == 1
    assert s['listener']['selected_branch'] == '0x6ce921'
    assert any(m['kind'] == 'complete init listener' and len(m['bytes']) == 2*(0x6cf6c0-0x6ce910)
               for m in s['memory'])
entry_class = next(n for n in ast.parse(trace_video_progress.PROBE).body
                   if isinstance(n, ast.ClassDef) and n.name == 'Entry')
exec(compile(ast.Module(body=[entry_class], type_ignores=[]), '<actual-entry-probe>', 'exec'))
entries, pending = [], []
result.update(apis={}, resolved_symbols={})
arm_startup_listener_probes(result['startup_stage_samples'][0])
gdb.execute('continue', to_string=True)
assert len(pending) == 1 and pending[0][0] == 'entry'
probe = pending[0][1]
assert probe.api == 'PES::InitListener'
probe.enabled = False
args = capture_args(probe.api)
before = fields_before(probe.api, args)
assert before['substate'] == 1 and before['event_type'] == 2, before
context = {'api': probe.api, 'thread': gdb.selected_thread().global_num, 'args': args}
Return(context)
pending = []
gdb.execute('continue', to_string=True)
assert len(pending) == 1 and pending[0][0] == 'return'
consume_return(context)
after = fields_after(probe.api, args, None)
assert after['substate'] == 2 and not after['complete'], after
result['listener_preflight'] = {'before': before, 'after': after, 'passed': True}
for breakpoint in gdb.breakpoints() or ():
    breakpoint.delete()
Path("/results/snapshots.json").write_text(json.dumps(result, indent=2))
'''

DOCKERFILE = '''FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends g++ gdb python3 \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /test
COPY fixture.cpp probe.py pes_frame_profile.py trace_video_progress.py ./
RUN g++ -std=c++20 -g -O1 -no-pie -pthread fixture.cpp -o fixture
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["gdb", "--batch", "-ex", "set debuginfod enabled off", "-ex", "break checkpoint", "-ex", "run", "-ex", "source /test/probe.py", "-ex", "continue", "/test/fixture"]
'''


def check_samples(result, fixture=False):
    if fixture and not result.get('listener_preflight', {}).get('passed'):
        raise RuntimeError('Guest listener entry/return preflight did not pass')
    stages = result.get('startup_stage_samples', [])
    if [s.get('phase') for s in stages] != ['before', 'after']:
        raise RuntimeError('Both startup-stage snapshots are required')
    for stage in stages:
        if (stage.get('status') != 'sampled' or stage.get('errors') or
                not stage.get('code') or not 0 < stage.get('read_bytes', 0) <= 131072):
            raise RuntimeError('Startup-stage probe did not capture verified guest state')
        if stage.get('seconds', 99) > 0.75:
            raise RuntimeError('Startup-stage probe exceeded its timing budget')
        if stage.get('stage_index') == 11:
            listener = stage.get('listener', {})
            if not listener.get('code_verified') or 'substate' not in listener:
                raise RuntimeError('Stage 11 listener was not identified safely')
    if not fixture and any(s.get('listener', {}).get('code_verified') for s in stages):
        required = {'PES::InitStage', 'PES::InitListener'}
        if not required.issubset(result.get('apis', {})):
            raise RuntimeError('Init-listener probes were not armed')
        if any(r.get('api') in required and r.get('read_error')
               for r in result.get('records', [])):
            raise RuntimeError('Init-listener argument or return capture failed')
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
    shutil.copy2(Path(__file__).with_name('trace_video_progress.py'), context)
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
