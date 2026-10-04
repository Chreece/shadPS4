#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build and validate a private GPU frame collector, then capture only the owned PES PID."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

BINARY_URL = 'https://renderdoc.org/stable/1.37/renderdoc_1.37.tar.gz'
BINARY_SHA = '4b1b98912072de657d79bdb196fa57b52da4c69d2d3ec099b0c9cf938bdf395d'
SOURCE_URL = 'https://codeload.github.com/baldurk/renderdoc/tar.gz/cd94206b0fd995bfb3e5ed95c1e68d4f5d38ea7e'
SOURCE_SHA = 'aecbff4d72bb3df5f02fa4211dabebde9b12dcd42bb8221417c9e4a0b334bce8'
DOCKERFILE = r'''FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends g++ glslang-tools libvulkan-dev nlohmann-json3-dev libx11-6 libxcb1 libx11-xcb1 && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY binary.tar.gz source.tar.gz ./
RUN mkdir -p /opt/pes-gpu-frame /headers && tar -xf binary.tar.gz --strip-components=1 -C /opt/pes-gpu-frame && tar -xf source.tar.gz --strip-components=1 -C /headers
COPY pes_gpu_frame.cpp fixture.vert fixture.frag ./
RUN g++ -std=c++17 -O1 -DRENDERDOC_PLATFORM_LINUX -I /headers/renderdoc/api/replay -I /headers/renderdoc/api/app pes_gpu_frame.cpp -L /opt/pes-gpu-frame/lib -Wl,-rpath,'$ORIGIN/lib' -lrenderdoc -lvulkan -ldl -pthread -o /opt/pes-gpu-frame/pes-gpu-fixture
RUN g++ -std=c++17 -O1 -DRENDERDOC_PLATFORM_LINUX -DPES_GPU_REPLAY_HELPER -rdynamic -I /headers/renderdoc/api/replay -I /headers/renderdoc/api/app pes_gpu_frame.cpp -L /opt/pes-gpu-frame/lib -Wl,-rpath,'$ORIGIN/lib' -lrenderdoc -lvulkan -ldl -pthread -o /opt/pes-gpu-frame/pes-gpu-frame
RUN glslangValidator -V fixture.vert -o /opt/pes-gpu-frame/fixture.vert.spv && glslangValidator -V fixture.frag -o /opt/pes-gpu-frame/fixture.frag.spv
CMD ["tar", "-C", "/opt/pes-gpu-frame", "-cf", "-", "pes-gpu-frame", "pes-gpu-fixture", "fixture.vert.spv", "fixture.frag.spv", "lib/librenderdoc.so", "etc/vulkan/implicit_layer.d/renderdoc_capture.json", "LICENSE.md"]
'''


VERTEX = '''#version 450
vec2 positions[3] = vec2[](vec2(-0.8,-0.8), vec2(0.8,-0.8), vec2(0,0.8));
void main() { gl_Position=vec4(positions[gl_VertexIndex],0,1); }
'''
FRAGMENT = '''#version 450
layout(location=0) out vec4 color;
void main() { color=vec4(1,0.25,0,1); }
'''

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def cached_download(cache, url, expected):
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (expected + '.tar.gz')
    if path.is_file() and not path.is_symlink() and digest(path) == expected:
        return path
    if path.exists() or path.is_symlink():
        raise RuntimeError('GPU tool cache checksum changed; preserved')
    fd, name = tempfile.mkstemp(dir=cache, prefix='download-')
    temp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out, urllib.request.urlopen(url, timeout=60) as response:
            size = 0
            while data := response.read(1024 * 1024):
                size += len(data)
                if size > 128 * 1024 * 1024:
                    raise RuntimeError('GPU tool download exceeded its size limit')
                out.write(data)
        if digest(temp) != expected:
            raise RuntimeError('GPU tool download checksum mismatch')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return path


class Collector:
    def __init__(self, work, runtime):
        self.work, self.runtime = work, runtime
        self.program = runtime / 'pes-gpu-frame'
        self.environment = {
            'XDG_DATA_DIRS': str(runtime / 'share') + ':' + os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share'),
            'ENABLE_VULKAN_RENDERDOC_CAPTURE': '1',
        }
        # Apply preloading to the exact emulator exec, after the launcher's reset.
        # No extra launcher process, ptrace attachment, or global layer install.
        self.command = ['env', '-u', 'DISABLE_VULKAN_RENDERDOC_CAPTURE_1_37',
                        'LD_PRELOAD=' + str(runtime / 'lib/librenderdoc.so'),
                        'RENDERDOC_CAPFILE=' + str(work / 'capture/game')]
        (work / 'capture').mkdir(exist_ok=True)
        self.captured = None

    def execute(self, args, log, timeout, capture=False):
        env = dict(os.environ)
        env.update(self.environment)
        for key in ('VK_INSTANCE_LAYERS', 'VK_LAYER_PATH', 'LD_PRELOAD'):
            env.pop(key, None)
        env['ENABLE_VULKAN_RENDERDOC_CAPTURE'] = '1' if capture else '0'
        if not capture:
            env['DISABLE_VULKAN_RENDERDOC_CAPTURE_1_37'] = '1'
        with log.open('w') as output:
            program = self.runtime / 'pes-gpu-fixture' if capture else self.program
            subprocess.run([str(program), *map(str, args)], env=env,
                           stdout=output, stderr=subprocess.STDOUT,
                           check=True, timeout=timeout)

    def preflight(self):
        path = self.work / 'preflight'
        path.mkdir()
        self.execute(['options', path / 'options.json'], path / 'options.log', 15)
        options = json.loads((path / 'options.json').read_text())['encoded']
        if not re.fullmatch(r'[a-p]{16,512}', options):
            raise RuntimeError('Invalid capture options returned by private helper')
        self.command.append('RENDERDOC_CAPOPTS=' + options)
        self.execute(['fixture', path / 'fixture'], path / 'capture.log', 30, capture=True)
        files = list(path.glob('fixture*.rdc'))
        if len(files) != 1:
            raise RuntimeError('Disposable GPU capture missing')
        self.execute(['verify', files[0], path / 'replay'], path / 'replay.log', 45)
        result = json.loads((path / 'replay/frame.json').read_text())
        if result.get('fixture_buffer_verified') is not True:
            raise RuntimeError('Disposable GPU replay did not verify its known buffer contents')
        self.execute(['replay', files[0], path / 'draw'], path / 'draw.log', 60)
        draw = json.loads((path / 'draw/frame.json').read_text())
        outputs = [output for item in draw.get('draws', []) for output in item.get('outputs', [])]
        if (not draw.get('complete') or len(draw.get('shaders', {})) < 2 or
                not any(output.get('max', [0])[0] > 0.99 and output.get('image') for output in outputs)):
            raise RuntimeError('Disposable GPU draw export did not recover the colored triangle')
        print('PES_GPU_PREFLIGHT=PASS: GPU buffer, triangle, shaders and image replayed correctly', flush=True)

    def capture(self, identity):
        import trace_video_progress as trace
        state = trace.inspect_target(identity)
        if state.get('status') != 'observed' or state.get('tracer_pid') != 0:
            raise RuntimeError('Owned PES identity unavailable for GPU capture')
        if str(self.runtime / 'lib/librenderdoc.so') not in (Path('/proc') / str(identity['pid']) / 'maps').read_text():
            raise RuntimeError('Private RenderDoc library did not reach the emulator')
        print('PES_GPU_CAPTURE=capturing one complete GPU frame', flush=True)
        result = self.work / 'capture.json'
        self.execute(['capture', identity['pid'], result], self.work / 'capture.log', 55)
        info = json.loads(result.read_text())
        path = Path(info['path'])
        expected = (self.work / 'capture').resolve()
        if (info.get('pid') != identity['pid'] or not info.get('local') or path.is_symlink()
                or path.resolve().parent != expected or not path.name.startswith('game')
                or path.suffix != '.rdc' or not path.is_file() or path.stat().st_size < 128):
            raise RuntimeError('Capture ownership or file identity mismatch')
        if trace.inspect_target(identity).get('status') != 'observed':
            raise RuntimeError('Owned emulator changed during GPU capture')
        self.captured = path
        print('PES_GPU_CAPTURE=PASS; continuing to final screenshots', flush=True)

    def replay(self):
        if self.captured is None:
            raise RuntimeError('No GPU frame available to inspect')
        print('PES_GPU_REPLAY=extracting draw state, shaders and textures after PES closed', flush=True)
        self.execute(['replay', self.captured, self.work / 'replay'], self.work / 'replay.log', 180)
        result = json.loads((self.work / 'replay/frame.json').read_text())
        if not result.get('complete') or not result.get('draws'):
            raise RuntimeError('GPU frame inspection incomplete')
        print('PES_GPU_REPLAY=PASS', flush=True)


def prepare(home, work, token):
    if not re.fullmatch(r'[0-9a-f]{32}', token):
        raise RuntimeError('Invalid GPU build ownership token')
    work.mkdir()
    context = work / 'gpu-build'
    context.mkdir()
    downloads = home / '.cache/shadps4-pes-gpu-capture/downloads'
    print('PES_GPU_TOOLS=preparing checksum-verified private RenderDoc tools', flush=True)
    for url, checksum, name in [(BINARY_URL, BINARY_SHA, 'binary.tar.gz'),
                                (SOURCE_URL, SOURCE_SHA, 'source.tar.gz')]:
        shutil.copyfile(cached_download(downloads, url, checksum), context / name)
    shutil.copy2(Path(__file__).with_suffix('.cpp'), context)
    (context / 'fixture.vert').write_text(VERTEX)
    (context / 'fixture.frag').write_text(FRAGMENT)
    (context / 'Dockerfile').write_text(DOCKERFILE)
    key = hashlib.sha256((DOCKERFILE + VERTEX + FRAGMENT + (context / 'pes_gpu_frame.cpp').read_text()).encode()).hexdigest()[:16]
    image = 'shadps4-pes-gpu-frame:' + key
    with (work / 'build.log').open('w') as log:
        subprocess.run(['docker', 'build', '-t', image, str(context)], stdout=log,
                       stderr=subprocess.STDOUT, timeout=900, check=True)
    bundle = work / 'gpu-runtime.tar'
    with bundle.open('wb') as out, (work / 'export.log').open('w') as log:
        subprocess.run(['docker', 'run', '--rm', '--read-only', '--network', 'none',
                        '--label', 'org.shadps4.pes-test=' + token, image], stdout=out,
                       stderr=log, timeout=45, check=True)
    runtime = work / 'gpu-runtime'
    runtime.mkdir()
    with tarfile.open(bundle) as archive:
        archive.extractall(runtime, filter='data')
    layers = runtime / 'share/vulkan/implicit_layer.d'
    layers.mkdir(parents=True)
    manifest = json.loads((runtime / 'etc/vulkan/implicit_layer.d/renderdoc_capture.json').read_text())
    manifest['layer']['library_path'] = str(runtime / 'lib/librenderdoc.so')
    (layers / 'renderdoc_capture.json').write_text(json.dumps(manifest))
    save = {'renderdoc_commit': 'cd94206b0fd995bfb3e5ed95c1e68d4f5d38ea7e',
            'binary_archive_sha256': BINARY_SHA, 'source_archive_sha256': SOURCE_SHA,
            'helper_sha256': digest(runtime / 'pes-gpu-frame')}
    (work / 'tools.json').write_text(json.dumps(save, indent=2))
    collector = Collector(work, runtime)
    collector.preflight()
    return collector
