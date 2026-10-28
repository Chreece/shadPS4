#!/usr/bin/env python3
"""Capture capability-gated Vulkan driver fault data on the retained native source.
This is diagnostic evidence, not a gameplay fix. No driver overrides or game inputs.
"""
from __future__ import annotations
import ast
import hashlib
from pathlib import Path
import shlex
import subprocess
import sys
import types

PARENT_REF = 'f3444f3d68b0f6e127e24a226ff90f7db5d1b80f'
PARENT_PATH = 'scripts/shadps4_auto_cpu_write_20261028.py'
PARENT_SHA = '4b77d51b315d917fc07aa75711a4e1477a26c6e74fc0df1417750027a1b226e6'
PACKAGE = '4672f24388817eccfb8413642e7e57e362dbad8b'
PATCH_PATH = 'scripts/patchsets/device-fault-report-20261028.patch'
TEST_PATH = 'tools/development/device_fault_test.cpp'
PATCH_BLOB = 'b0b77afad67496acaad19a2d532116d049975343'
TEST_BLOB = 'f23f0ddbf83f0f262e76ebe291d3bb6bcad026e0'
LAST = 'bc033c22c380734e9fbbc4e2fb9b9cad12e7f73c'
LAST_ELF = 'f42ad1be9a88ec0fa3e0194fd0e8c0c47eec8a29ee99285317cc951dc506d6d2'
OBSERVER_REF = 'd9c87b7224bb8c20ac72282f6cf16a49565223c5'
OBSERVER_PATH = 'tools/development/shadps4_gpu_loss_early_observer_20261026.py'
OBSERVER_BLOB = '188deeb0a25e671e2308ac0acfe8ad81bc0adfc1'


def parent_bytes():
    scripts = Path.home() / 'projects/shadps4/scripts'
    for p in sorted(scripts.glob('gow-auto-write-*.py')):
        if p.is_file() and not p.is_symlink() and p.stat().st_size < 100000:
            data = p.read_bytes()
            if hashlib.sha256(data).hexdigest() == PARENT_SHA:
                return data
    data = subprocess.run(['curl', '-4fsSL', '--connect-timeout', '15', '--max-time', '60',
        f'https://raw.githubusercontent.com/Chreece/shadPS4/{PARENT_REF}/{PARENT_PATH}'],
        capture_output=True, check=True, timeout=65).stdout
    if hashlib.sha256(data).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_RUNNER_HASH_MISMATCH')
    return data


def find_candidate(m):
    root = m.ROOT / 'GodOfWarRagnarok/tests'
    found = sorted(root.glob('gow-host-readback-*/candidate/shadps4'), reverse=True)
    for p in found[:32]:
        if p.is_file() and not p.is_symlink() and p.resolve() == p and m.sha(p) == LAST_ELF:
            return p
    # The inherited worker records this preflight failure before any mutation.
    return root / 'UNVERIFIED_HOST_READBACK_CANDIDATE/shadps4'


CAPTURE_FUNCTION = "def take_picture(report, second, display):\n    # No injected input or display changes. Record why a capture failed.\n    output = report / f'frame-{second:03d}s.png'\n    attempts = []\n    commands = []\n    if shutil.which('import'):\n        commands.append(('imagemagick', ['import', '-display', display, '-window', 'root', str(output)]))\n    if shutil.which('ffmpeg') and shutil.which('xdpyinfo'):\n        try:\n            probe = subprocess.run(['xdpyinfo', '-display', display], stdin=subprocess.DEVNULL,\n                                   capture_output=True, timeout=2, check=False)\n            match = re.search(r'dimensions:\\s+(\\d+x\\d+)', probe.stdout.decode(errors='replace'))\n            if probe.returncode == 0 and match:\n                commands.append(('ffmpeg-x11', ['ffmpeg', '-nostdin', '-loglevel', 'error', '-y',\n                    '-f', 'x11grab', '-draw_mouse', '0', '-video_size', match.group(1),\n                    '-framerate', '1', '-i', display, '-frames:v', '1', str(output)]))\n        except (OSError, subprocess.TimeoutExpired) as ex:\n            attempts.append({'backend': 'geometry', 'error': str(ex)[:400]})\n    success = False\n    for backend, command in commands:\n        try:\n            proc = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n                                  stderr=subprocess.PIPE, timeout=3, check=False)\n            valid = False\n            if output.is_file() and output.stat().st_size > 8:\n                with output.open('rb') as f:\n                    valid = f.read(8) == b'\\x89PNG\\r\\n\\x1a\\n'\n            attempts.append({'backend': backend, 'returncode': proc.returncode, 'valid_png': valid,\n                             'stderr': proc.stderr.decode(errors='replace')[:600]})\n            if proc.returncode == 0 and valid:\n                success = True\n                break\n        except (OSError, subprocess.TimeoutExpired) as ex:\n            attempts.append({'backend': backend, 'error': str(ex)[:400]})\n    if not commands:\n        attempts.append({'backend': 'none', 'error': 'No available screenshot backend'})\n    if not success and output.exists():\n        output.unlink()\n    with (report / 'capture-attempts.jsonl').open('a', encoding='utf-8') as f:\n        f.write(json.dumps({'second': second, 'success': success, 'attempts': attempts}) + '\\n')\n    return success\n\n\n"


def patch_observer(m, work):
    cached = m.ROOT / ('GodOfWarRagnarok/tests/playtest-'
        'gow-host-readback-game-20261028-152806-2685768/native-observer.py')
    if cached.is_file() and m.blob(cached.read_bytes()) == OBSERVER_BLOB:
        raw = cached.read_bytes()
    else:
        raw = m.cmd(['curl', '-4fsSL', '--connect-timeout', '15', '--max-time', '60',
            f'https://raw.githubusercontent.com/Chreece/shadPS4/{OBSERVER_REF}/{OBSERVER_PATH}'], timeout=65)
    m.require(m.blob(raw) == OBSERVER_BLOB, 'OBSERVER_PREIMAGE_CHANGED')
    text = raw.decode()
    a = text.index('def take_picture(')
    b = text.index('def term_owned(', a)
    text = text[:a] + CAPTURE_FUNCTION + text[b:]
    ast.parse(text)
    dest = work / 'observer-with-capture-errors.py'
    dest.write_text(text)
    return dest


def load_worker(raw):
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise RuntimeError('PARENT_BYTES_CHANGED')
    text = raw.decode()
    changes = {
        'gow-auto-cpu-write-': 'gow-device-fault-',
        'gow-auto-coherence-game-': 'gow-device-fault-game-',
        'AUTOMATIC_GPU_CPU_WRITE_COHERENCE': 'VULKAN_DEVICE_FAULT_EVIDENCE',
        'auto-cpu-write-coherence.lock': 'gpu-device-fault-evidence.lock',
        'Mandatory GPU-dirty buffer preservation on CPU write; read/backend/image options unchanged':
            'Capability-gated driver fault report only; shader and memory behavior retained',
        'automatic-cpu-write.patch': 'device-fault-report.patch',
        'baseline-memory-tracker.h': 'baseline-vk-instance.cpp',
        'metadata_candidate': 'host_readback_candidate',
        'NATIVE_BUFFER_CACHE_OBJECT': 'NATIVE_DEVICE_FAULT_OBJECTS',
        "'CMakeFiles/shadps4.dir/src/video_core/buffer_cache/buffer_cache.cpp.o'":
            "'CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_instance.cpp.o', "
            "'CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_scheduler.cpp.o'",
        'memory: state-driven preservation of GPU-owned bytes before CPU writes':
            'diagnostics: capture driver device-fault evidence after GPU loss',
        'coherence-events.txt': 'device-fault-events.txt',
        "if 'CpuWriteCoherence:' in line or 'Device lost during submit' in line or 'GPU_QUEUE_FAILURE' in line":
            "if 'DEVICE_FAULT_' in line or 'CpuWriteCoherence:' in line or 'Device lost during submit' in line or 'GPU_QUEUE_FAILURE' in line",
    }
    for before, after in changes.items():
        if before not in text:
            raise RuntimeError('PARENT_ANCHOR_MISSING=' + before)
        text = text.replace(before, after)
    ast.parse(text)
    m = types.ModuleType('device_fault_worker')
    m.__file__ = str(Path(__file__).resolve())
    exec(compile(text, m.__file__, 'exec'), m.__dict__)
    m.LAST, m.LAST_ELF = LAST, LAST_ELF
    m.LAST_BIN = find_candidate(m)
    m.UPSTREAM_OLD = m.UPSTREAM = 'ce74386283f559f2279a7c31416bcdc9359be0b3'
    m.UPSTREAM_PATHS = set()
    m.PACKAGE = PACKAGE
    m.PATCH_PATH, m.TEST_PATH = PATCH_PATH, TEST_PATH
    m.PACKAGE_BLOBS = {PATCH_PATH: PATCH_BLOB, TEST_PATH: TEST_BLOB}
    m.M = 'src/video_core/renderer_vulkan/vk_instance.cpp'
    m.B = 'src/video_core/renderer_vulkan/vk_scheduler.cpp'
    m.H = 'src/video_core/renderer_vulkan/vk_device_fault_report.h'
    m.PREIMAGES = {m.M: 'bdc0e95a4225d4d86d03c3fb990f29197813f497',
                  m.B: 'd5a0abc9ac2616e5ce40edef177eb701e578154f'}
    m.CHANGED = {m.M, m.B, m.H}

    def cpu_tests(_old, template, work, evidence):
        headers = m.SRC / 'externals/vulkan-headers/include'
        m.require((headers / 'vulkan/vulkan.hpp').is_file(), 'REAL_VULKAN_HEADERS_MISSING')
        test = evidence / 'device-fault-cpu.cpp'
        test.write_text(template)
        exe = work / 'device-fault-cpu-test'
        m.cmd(['g++', '-std=c++23', '-O2', '-DNDEBUG', '-Wall', '-Wextra', '-Werror',
               '-pedantic', '-pthread', '-I', headers, '-I', m.SRC / 'src', test, '-o', exe],
              evidence / 'device-fault-cpu-build.log', timeout=180)
        result = m.cmd([exe], evidence / 'device-fault-cpu-result.txt', timeout=30)
        m.require(b'DEVICE_FAULT_CPU_PROTOCOL=PASS' in result, 'CPU_PROTOCOL_FAILED')
        api = evidence / 'vulkan-api-types.cpp'
        api.write_text('''#define VK_NO_PROTOTYPES
#define VULKAN_HPP_NO_CONSTRUCTORS
#define VULKAN_HPP_DISPATCH_LOADER_DYNAMIC 1
#include <type_traits>
#include <vulkan/vulkan.hpp>
#include "video_core/renderer_vulkan/vk_device_fault_report.h"
static_assert(std::is_same_v<decltype(VULKAN_HPP_DEFAULT_DISPATCHER.vkGetDeviceFaultInfoEXT),
                             PFN_vkGetDeviceFaultInfoEXT>);
int main() {
    vk::StructureChain chain{vk::DeviceCreateInfo{}, vk::PhysicalDeviceVulkan11Features{}};
    auto info = chain.get();
    VkPhysicalDeviceFaultFeaturesEXT fault{};
    fault.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FAULT_FEATURES_EXT;
    fault.deviceFault = VK_TRUE;
    fault.deviceFaultVendorBinary = VK_FALSE;
    fault.pNext = const_cast<void*>(info.pNext);
    info.pNext = &fault;
    return info.pNext == &fault && fault.pNext != nullptr ? 0 : 1;
}
''')
        api_exe = work / 'vulkan-api-types'
        m.cmd(['g++', '-std=c++23', '-O2', '-Wall', '-Wextra', '-Werror', '-pedantic',
               '-pthread', '-I', headers, '-I', m.SRC / 'src', api, '-o', api_exe],
              evidence / 'vulkan-api-build.log', timeout=180)
        m.cmd([api_exe], evidence / 'vulkan-api-result.txt', timeout=30)
        print('REAL_VULKAN_HEADER_AND_FAULT_PROTOCOL=PASS NO_GPU_TEST_YET', flush=True)
    m.cpu_tests = cpu_tests

    original_derive = m.derive_runtime
    def derive_runtime(target, pin, binary_sha):
        original_derive(target, pin, binary_sha)
        observer = patch_observer(m, target.parent)
        text = target.read_text()
        old_upstream = '3eecea94ddaca360b19f4b800523b609a8b2ffcc'
        m.require(text.count(old_upstream) == 2, 'RUNTIME_UPSTREAM_ANCHOR_CHANGED')
        text = text.replace(old_upstream, m.UPSTREAM)
        start = text.index('curl -4fsSL --retry 3 --retry-delay 2')
        end = text.index('    -o "$OBSERVER"', start) + len('    -o "$OBSERVER"')
        m.require(OBSERVER_PATH in text[start:end], 'OBSERVER_DOWNLOAD_ANCHOR_CHANGED')
        text = text[:start] + 'cp -- ' + shlex.quote(str(observer)) + ' "$OBSERVER"' + text[end:]
        m.require(text.count(OBSERVER_BLOB) == 1, 'OBSERVER_HASH_ANCHOR_CHANGED')
        text = text.replace(OBSERVER_BLOB, m.blob(observer.read_bytes()))
        target.write_text(text)
        m.cmd(['bash', '-n', target])
    m.derive_runtime = derive_runtime
    return m


if __name__ == '__main__':
    raise SystemExit(load_worker(parent_bytes()).main())
