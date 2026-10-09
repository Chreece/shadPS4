#!/usr/bin/env python3
"""God of War buffer/image alias readback diagnostic with verified F64 Phi and SRT/GDS.

Carry the exact previously verified SRT/GDS diagnostic patch in this
self-contained script. Its bytes and the reviewed original-source checksums
are verified locally; NO previous report is required on the user server.
Apply the patch in the dedicated build tree, retain the proven F64 Phi correction,
and instrument just the unsupported image-to-texel-buffer alias readback.
THIS IS DIAGNOSTIC, NOT A COMPLETE IMAGE-ALIAS IMPLEMENTATION.

Restore the original sources BEFORE launching. Never touch production ES-DE,
saves, Sunshine, display services, or the caller's SSH session.
"""
from __future__ import annotations
import base64
import collections
import datetime as dt
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import zlib

HOME = Path.home()
SRC = HOME / 'shadps4-esde-verified-builds/20261009-173836/source'
BUILD = HOME / 'shadps4-esde-verified-builds/20261009-174801/build'
LIVE = HOME / 'Applications/shadps4/shadps4'
EXPECTED_HEAD = '4eb9fc5f92188abbb30eb2cc55980e922b2a0ec0'
EXPECTED_LIVE_SHA256 = 'faca7d7ec1b375afdfac3d35b396a33092d3d56fd917ced8a67c9dc6e8e99834'
EXPECTED_PATCH_SHA256 = '43ff03c09c642e07b411c592c241e80f9446607cae8ee54339537a3606a7b9c8'
BASE_FILES = {
    'process.cpp.original': 'src/core/libraries/kernel/process.cpp',
    'data-share.cpp.original': 'src/shader_recompiler/frontend/translate/data_share.cpp',
    'shader-info.h.original': 'src/shader_recompiler/info.h',
    'flatten_extended_userdata_pass.cpp.original': 'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp',
    'vk_rasterizer.cpp.original': 'src/video_core/renderer_vulkan/vk_rasterizer.cpp',
}
SPIRV = 'src/shader_recompiler/backend/spirv/emit_spirv.cpp'
INFO = 'src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp'
BUFFER_CACHE = 'src/video_core/buffer_cache/buffer_cache.cpp'
TEXTURE_CACHE = 'src/video_core/texture_cache/texture_cache.cpp'
DRIVER = 'src/core/libraries/videoout/driver.cpp'
EXPECTED_TEXTURE_SOURCE_SHA256 = '13ec634c922df5710b9e674e96fb2fcf7759d384f2ab0bc7b01175bb7f71c700'
ORIGINAL_SHA256 = {
    'process.cpp.original': '19f0f273b1cb3d36bca923fd1cb78277b0676a8faa74f91bf145555afc09e5ea',
    'data-share.cpp.original': 'b77d42862c8c14e7e9cc7cec2101918002f9a9649ab284db46ffa24e7bcd4478',
    'shader-info.h.original': '7721931dc376a8763c3a06ecbd4d108f57473769972605e2eee5fd9e1eab5165',
    'flatten_extended_userdata_pass.cpp.original': 'bfcfb28400afddc0e8ac4aad85d41419c302d9807e53f4b736bc497dfcc252db',
    'vk_rasterizer.cpp.original': '8d73198df4f9114aa1a2e79892ed489a89a73fd01c1a982f616116fc08e7588d',
}
# Exact 11,151-byte tested SRT/GDS diagnostic source patch.
# zlib-compressed, base64 to remain valid UTF-8 text in the GitHub script.
PROVEN_PATCH_ZLIB_BASE64 = (
    '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'
)

STAMP = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
REPORT = HOME / f'shadps4-gow-dma-codegen-{STAMP}.tar.gz'
TRIAL = HOME / f'Applications/shadps4-gow-dma-codegen-trial-{STAMP}'
WORK = Path(tempfile.mkdtemp(prefix='.gow-dma-codegen-', dir=HOME))
SHOTS = (0, 3, 6, 9, 11, 12, 14, 16, 19, 22, 26, 32, 39, 49, 61, 73)
RESULT = 'NOT_RUN'
PHASE = 'initialization'
BACKUPS: dict[Path, bytes] = {}
DIRTY = False
OWN_GAME = None
OWN_BUILD = None
SOURCE_RESTORED = False
try:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
except (OSError, ValueError):
    pass

def note(message):
    print(message, flush=True)

def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def command(args, timeout=12, env=None, outfile=None):
    try:
        if outfile is None:
            return subprocess.run(args, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, check=False,
                                  timeout=timeout, env=env)
        with Path(outfile).open('wb') as sink:
            return subprocess.run(args, stdout=sink, stderr=subprocess.STDOUT,
                                  check=False, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return type('CommandError', (), {'returncode': -1, 'stdout': str(exc).encode()})()

def git(*args):
    return command(['git', '-C', str(SRC), *args], timeout=15)

def fail(reason):
    raise RuntimeError('SAFE_STOP: ' + reason)

def patch_once(value, before, after, label):
    if value.count(before) != 1:
        fail(f'{label}: anchor count {value.count(before)}, expected 1')
    return value.replace(before, after, 1)

def make_phi_patch(spv: str, info: str):
    original = '''    case IR::Type::U64:
        return ctx.U64;
    default:
        UNREACHABLE_MSG("Phi node type {}", type);'''
    replacement = '''    case IR::Type::U64:
        return ctx.U64;
    case IR::Type::F64:
        // F64 is already defined by EmitContext and requires Float64 capability.
        if (ctx.info.pgm_hash == 0xd0ba3e4dULL) {
            LOG_WARNING(Render_Recompiler, "GOW_F64_PHI_TYPE shader={:#x} uses_fp64={}",
                        ctx.info.pgm_hash, ctx.info.uses_fp64);
        }
        return ctx.F64[1];
    default:
        UNREACHABLE_MSG("Phi node type {}", type);'''
    spv = patch_once(spv, original, replacement, 'SPIR-V TypeId F64')
    spv = patch_once(spv, '#include "common/assert.h"',
                     '#include "common/assert.h"\n#include "common/logging/log.h"',
                     'SPIR-V logger include')
    source = '''void Visit(Info& info, const IR::Inst& inst) {
    switch (inst.GetOpcode()) {'''
    modified = '''void Visit(Info& info, const IR::Inst& inst) {
    // A Phi can be the only remaining operation requiring the SPIR-V F64 type.
    if (inst.GetOpcode() == IR::Opcode::Phi && inst.Type() == IR::Type::F64) {
        info.uses_fp64 = true;
        if (info.pgm_hash == 0xd0ba3e4dULL) {
            LOG_WARNING(Render_Recompiler, "GOW_F64_PHI_INFO shader={:#x}", info.pgm_hash);
        }
    }
    switch (inst.GetOpcode()) {'''
    info = patch_once(info, source, modified, 'shader-info F64 Phi')
    info = patch_once(info, '#include "core/emulator_settings.h"',
                      '#include "core/emulator_settings.h"\n#include "common/logging/log.h"',
                      'shader-info logger include')
    return spv, info


def make_alias_patch(buffer_cache: str):
    # Refuse to alter an unknown assertion or guess another memory-copy method.
    anchor = ('    ASSERT_MSG(device_addr == image.info.guest_address,\n               "Texel buffer aliases image subresources {:x} : {:x}", device_addr,\n               image.info.guest_address);')
    patch = ('    if (device_addr != image.info.guest_address &&\n        std::getenv("SHADPS4_GOW_DIAGNOSTIC_ALIAS_GUARD") != nullptr) {\n        // DIAGNOSTIC ONLY. We cannot safely copy a tiled image starting from\n        // a subresource offset with the full-image TileImage parameters.\n        // Do not fabricate metadata, redirect the transfer, or touch CPU memory.\n        static std::atomic<u64> alias_count{0};\n        const u64 hit = alias_count.fetch_add(1, std::memory_order_relaxed) + 1;\n        if (hit <= 32 || (hit & (hit - 1)) == 0) {\n            LOG_WARNING(Render_Vulkan,\n                        "GOW_IMAGE_ALIAS_UNSUPPORTED hit={} request={:#x} size={:#x} "\n                        "image_base={:#x} image_bytes={:#x} offset={:#x} "\n                        "mips={} layers={} tiled={} safe_to_download={}",\n                        hit, device_addr, size, image.info.guest_address,\n                        image.info.guest_size, device_addr - image.info.guest_address,\n                        image.info.resources.levels, image.info.resources.layers,\n                        static_cast<bool>(image.info.props.is_tiled), image.SafeToDownload());\n        }\n        return false;\n    }\n    ASSERT_MSG(device_addr == image.info.guest_address,\n               "Texel buffer aliases image subresources {:x} : {:x}", device_addr,\n               image.info.guest_address);')
    if 'GOW_IMAGE_ALIAS_UNSUPPORTED' in buffer_cache:
        fail('image alias diagnostic guard already present; refusing repeat patch')
    if buffer_cache.count(anchor) != 1:
        fail('image/texel alias synchronization source differs; not patching')
    if buffer_cache.count('#include <algorithm>') != 1:
        fail('BufferCache include layout differs; not patching')
    buffer_cache = patch_once(buffer_cache, '#include <algorithm>',
                              '#include <algorithm>\n#include <atomic>\n#include <cstdlib>',
                              'buffer cache diagnostic includes')
    return patch_once(buffer_cache, anchor, patch, 'image alias diagnostic guard')


def video_patch(tex, driver):
    old = """    ForEachImageWithAddress(address, [&](ImageId image_id, Image& image) {
        if (ensure_valid && !image.SafeToDownload()) {"""
    new = """    ForEachImageWithAddress(address, [&](ImageId image_id, Image& image) {
        if (image.info.guest_address != address) {
            return;
        }
        if (ensure_valid && !image.SafeToDownload()) {"""
    tex = patch_once(tex, old, new, 'exact-address image lookup')
    driver = patch_once(driver, '#include "common/debug.h"',
        '#include "common/debug.h"\n#include <atomic>\n#include <cstdlib>\n'
        '#include "common/logging/log.h"', 'video flip trace includes')
    old_submit = """bool VideoOutDriver::SubmitFlip(VideoOutPort* port, s32 index, s64 flip_arg,
                                bool is_eop /*= false*/) {
    {"""
    new_submit = """bool VideoOutDriver::SubmitFlip(VideoOutPort* port, s32 index, s64 flip_arg,
                                bool is_eop /*= false*/) {
    if (std::getenv("SHADPS4_GOW_VIDEO_TRACE") != nullptr) {
        static std::atomic<u64> n{0};
        const u64 x = n.fetch_add(1, std::memory_order_relaxed) + 1;
        if (x <= 16 || (x & (x - 1)) == 0) {
            LOG_WARNING(Lib_VideoOut, "GOW_VO_SUBMIT n={} index={} eop={}", x, index, is_eop);
        }
    }
    {"""
    driver = patch_once(driver, old_submit, new_submit, 'video flip submission')
    old_present = """    // Present the frame.
    presenter->Present(req.frame);"""
    new_present = """    u64 gow_trace_flip_id = 0;
    if (std::getenv("SHADPS4_GOW_VIDEO_TRACE") != nullptr) {
        static std::atomic<u64> n{0};
        gow_trace_flip_id = n.fetch_add(1, std::memory_order_relaxed) + 1;
        if (gow_trace_flip_id <= 16 || (gow_trace_flip_id & (gow_trace_flip_id - 1)) == 0) {
            LOG_WARNING(Lib_VideoOut, "GOW_VO_PRESENT_BEGIN n={} index={}",
                        gow_trace_flip_id, req.index);
        }
    }
    // Present the frame.
    presenter->Present(req.frame);
    if (gow_trace_flip_id != 0 &&
        (gow_trace_flip_id <= 16 || (gow_trace_flip_id & (gow_trace_flip_id - 1)) == 0)) {
        LOG_WARNING(Lib_VideoOut, "GOW_VO_PRESENT_END n={} index={}",
                    gow_trace_flip_id, req.index);
    }"""
    driver = patch_once(driver, old_present, new_present, 'video frame presentation')
    return tex, driver


RASTERIZER = 'src/video_core/renderer_vulkan/vk_rasterizer.cpp'
PRESENTER = 'src/video_core/renderer_vulkan/vk_presenter.cpp'

def make_draw_trace(code):
    code=patch_once(code, '#include "common/debug.h"',
                    '#include "common/debug.h"\n#include <atomic>\n#include <cstdlib>',
                    'draw tracer headers')
    code=patch_once(code, 'namespace Vulkan {\n',
        '''namespace Vulkan {

static bool GowRenderTraceOn() { return std::getenv("SHADPS4_GOW_DRAW_TRACE") != nullptr; }
static bool GowRenderSample(u64 n) { return n <= 16 || (n & (n - 1)) == 0; }
static std::atomic<u64> gow_draw_attempt{0};
static std::atomic<u64> gow_draw_emitted{0};
static std::atomic<u64> gow_draw_no_pipeline{0};
static std::atomic<u64> gow_draw_bind_fail{0};
static std::atomic<u64> gow_draw_filtered{0};
static std::atomic<u64> gow_indirect_attempt{0};
static std::atomic<u64> gow_indirect_emitted{0};
static std::atomic<u64> gow_rt_trace{0};

''', 'draw tracer counters')
    start='void Rasterizer::Draw(bool is_indexed, u32 index_offset) {'
    end='void Rasterizer::DrawIndirect('
    if code.count(start) != 1 or code.count(end) != 1:
        fail('Rasterizer Draw structure differs')
    prefix, suffix=code.split(start,1)
    section, suffix=suffix.split(end,1)
    section=patch_once(section, '    scheduler.PopPendingOperations();',
        '''    scheduler.PopPendingOperations();
    if (GowRenderTraceOn()) {
        const u64 n=gow_draw_attempt.fetch_add(1,std::memory_order_relaxed)+1;
        if (GowRenderSample(n)) {
            const auto& cb=liverpool->regs.color_buffers[0];
            LOG_WARNING(Render_Vulkan, "GOW_DRAW_ATTEMPT n={} cb0={:#x} indexed={} vertices={}",
                        n, cb ? cb.Address() : u64{0}, is_indexed, liverpool->regs.num_indices);
        }
    }''', 'draw attempts')
    section=patch_once(section, '    if (!FilterDraw()) {\n        return;\n    }',
        '''    if (!FilterDraw()) {
        if (GowRenderTraceOn()) {
            const u64 n=gow_draw_filtered.fetch_add(1,std::memory_order_relaxed)+1;
            if (GowRenderSample(n)) LOG_WARNING(Render_Vulkan,"GOW_DRAW_FILTERED n={}",n);
        }
        return;
    }''', 'draw filtering')
    section=patch_once(section, '    if (!pipeline) {\n        return;\n    }',
        '''    if (!pipeline) {
        if (GowRenderTraceOn()) {
            const u64 n=gow_draw_no_pipeline.fetch_add(1,std::memory_order_relaxed)+1;
            if (GowRenderSample(n)) LOG_WARNING(Render_Vulkan,"GOW_DRAW_NO_PIPELINE n={}",n);
        }
        return;
    }''', 'missing draw pipeline')
    section=patch_once(section, '    if (!BindResources(pipeline)) {\n        return;\n    }',
        '''    if (!BindResources(pipeline)) {
        if (GowRenderTraceOn()) {
            const u64 n=gow_draw_bind_fail.fetch_add(1,std::memory_order_relaxed)+1;
            if (GowRenderSample(n)) LOG_WARNING(Render_Vulkan,"GOW_DRAW_BIND_FAILED n={}",n);
        }
        return;
    }''', 'failed resource binding')
    section=patch_once(section, '    DebugState.IncDrawCall();',
        '''    DebugState.IncDrawCall();
    if (GowRenderTraceOn()) {
        const u64 n=gow_draw_emitted.fetch_add(1,std::memory_order_relaxed)+1;
        if (GowRenderSample(n)) {
            const auto& cb=regs.color_buffers[0];
            LOG_WARNING(Render_Vulkan,"GOW_DRAW_EMITTED n={} cb0={:#x} vertices={}",
                        n,cb ? cb.Address() : u64{0},regs.num_indices);
        }
    }''', 'successful draws')
    code=prefix+start+section+end+suffix

    start='void Rasterizer::DrawIndirect('
    end='void Rasterizer::DispatchDirect('
    prefix, suffix=code.split(start,1)
    section, suffix=suffix.split(end,1)
    section=patch_once(section,'    scheduler.PopPendingOperations();',
        '''    scheduler.PopPendingOperations();
    if (GowRenderTraceOn()) {
        const u64 n=gow_indirect_attempt.fetch_add(1,std::memory_order_relaxed)+1;
        if (GowRenderSample(n)) LOG_WARNING(Render_Vulkan,"GOW_INDIRECT_ATTEMPT n={}",n);
    }''', 'indirect draw attempts')
    section=patch_once(section,'    ResetBindings(false);',
        '''    if (GowRenderTraceOn()) {
        const u64 n=gow_indirect_emitted.fetch_add(1,std::memory_order_relaxed)+1;
        if (GowRenderSample(n)) LOG_WARNING(Render_Vulkan,"GOW_INDIRECT_EMITTED n={}",n);
    }
    ResetBindings(false);''','indirect draw emissions')
    code=prefix+start+section+end+suffix

    start='RenderState Rasterizer::BeginRendering(const GraphicsPipeline* pipeline) {'
    end='void Rasterizer::Resolve() {'
    prefix,suffix=code.split(start,1)
    section,suffix=suffix.split(end,1)
    section=patch_once(section,
        '        const auto& image_view = texture_cache.FindRenderTarget(image_id, desc);',
        '''        const auto& image_view = texture_cache.FindRenderTarget(image_id, desc);
        if (GowRenderTraceOn()) {
            const u64 n=gow_rt_trace.fetch_add(1,std::memory_order_relaxed)+1;
            if (GowRenderSample(n)) {
                LOG_WARNING(Render_Vulkan,
                    "GOW_RT_TARGET n={} mrt={} base={:#x} bytes={:#x} w={} h={} video_surface={}",
                    n,cb,image->info.guest_address,image->info.guest_size,
                    image->info.size.width,image->info.size.height,
                    static_cast<bool>(image->usage.vo_surface));
            }
        }''','color target addresses')
    return prefix+start+section+end+suffix

def make_present_trace(code):
    code=patch_once(code,'#include <atomic>', '#include <atomic>\n#include <cstdlib>',
                    'presenter trace headers')
    code=patch_once(code,
        '''    const auto image_id = texture_cache.FindImage(desc);
    texture_cache.UpdateImage(image_id);''',
        '''    const auto image_id = texture_cache.FindImage(desc);
    if (std::getenv("SHADPS4_GOW_DRAW_TRACE") != nullptr) {
        static std::atomic<u64> n{0};
        const u64 count=n.fetch_add(1,std::memory_order_relaxed)+1;
        if (count <= 16 || (count & (count-1)) == 0) {
            const auto& src=texture_cache.GetImage(image_id);
            LOG_WARNING(Render_Vulkan,
                "GOW_VO_SOURCE n={} request={:#x} base={:#x} bytes={:#x} w={} h={} rt={} storage={} video={}",
                count,cpu_address,src.info.guest_address,src.info.guest_size,
                src.info.size.width,src.info.size.height,
                static_cast<bool>(src.usage.render_target),
                static_cast<bool>(src.usage.storage),
                static_cast<bool>(src.usage.vo_surface));
        }
    }
    texture_cache.UpdateImage(image_id);''','source image for presented frame')
    return code


DMA_BACKEND = 'src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp'

def make_single_dma_info(code):
    code = patch_once(code, '#include "core/emulator_settings.h"',
                      '#include <cstdlib>\n#include "core/emulator_settings.h"',
                      'per shader DMA Info include')
    old = '    if (!EmulatorSettings.IsDirectMemoryAccessEnabled()) {\n        info.uses_dma = false;\n        info.readconst_types = Info::ReadConstType::None;\n    }'
    new = '    const bool gow_dma_target =\n        info.pgm_hash == 0x57b077acULL && info.hw_stage == HwStage::Compute &&\n        std::getenv("SHADPS4_GOW_ONE_SHADER_DMA_COMPILE") != nullptr;\n    if (!EmulatorSettings.IsDirectMemoryAccessEnabled() && !gow_dma_target) {\n        info.uses_dma = false;\n        info.readconst_types = Info::ReadConstType::None;\n    }\n    if (gow_dma_target) {\n        LOG_WARNING(Render_Recompiler,\n                    "GOW_TARGET_DMA_INFO shader={:#x} uses_dma={} readconst_types={}",\n                    info.pgm_hash, info.uses_dma, static_cast<u32>(info.readconst_types));\n    }'
    return patch_once(code, old, new, 'per shader DMA info collection')


def make_single_dma_backend(code):
    code = patch_once(code, '#include "common/assert.h"',
                      '#include <atomic>\n#include <cstdlib>\n'
                      '#include "common/assert.h"\n'
                      '#include "common/logging/classes.h"\n'
                      '#include "common/logging/log.h"',
                      'DMA backend includes')
    old = '    const u32 flatbuf_off_dw = inst->Flags<u32>();\n    if (!EmulatorSettings.IsDirectMemoryAccessEnabled()) {\n        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));\n    }\n    if (flatbuf_off_dw == 0) {'
    new = '    const u32 flatbuf_off_dw = inst->Flags<u32>();\n    const bool gow_dma_target =\n        ctx.info.pgm_hash == 0x57b077acULL && ctx.info.hw_stage == HwStage::Compute &&\n        ctx.info.uses_dma &&\n        std::getenv("SHADPS4_GOW_ONE_SHADER_DMA_COMPILE") != nullptr;\n    if (!EmulatorSettings.IsDirectMemoryAccessEnabled() && !gow_dma_target) {\n        return ctx.EmitFlatbufferLoad(ctx.ConstU32(flatbuf_off_dw));\n    }\n    if (gow_dma_target && flatbuf_off_dw == 0) {\n        static std::atomic<u32> compiled{0};\n        const u32 n = compiled.fetch_add(1, std::memory_order_relaxed) + 1;\n        if (n <= 8 || (n & (n - 1)) == 0) {\n            LOG_WARNING(Render_Recompiler,\n                        "GOW_TARGET_DMA_DYNAMIC_CODEGEN count={} shader={:#x} flags={}",\n                        n, ctx.info.pgm_hash, flatbuf_off_dw);\n        }\n    }\n    if (flatbuf_off_dw == 0) {'
    return patch_once(code, old, new, 'per shader DMA backend selection')


def read_proven_patch():
    # Everything needed is pinned in this file. Never require a report that
    # the user already uploaded to ChatGPT and may no longer have locally.
    try:
        packed = base64.b64decode(PROVEN_PATCH_ZLIB_BASE64, validate=True)
        patch = zlib.decompress(packed)
    except (ValueError, zlib.error) as error:
        fail('Embedded verified SRT/GDS patch is corrupt: ' + str(error))
    if digest(patch) != EXPECTED_PATCH_SHA256 or len(patch) != 11151:
        fail('Embedded SRT/GDS patch checksum/size mismatch')
    originals = {}
    for archive_name, relative_path in BASE_FILES.items():
        source_path = SRC / relative_path
        if not source_path.is_file():
            fail('Required original source missing: ' + relative_path)
        contents = source_path.read_bytes()
        if digest(contents) != ORIGINAL_SHA256[archive_name]:
            fail('Source no longer matches verified baseline: ' + relative_path)
        originals[relative_path] = contents
    return patch, originals

def assert_ready():
    if not SRC.is_dir() or not BUILD.is_dir() or not LIVE.is_file():
        fail('Dedicated shadPS4 source/build or installed executable missing')
    if file_sha(LIVE) != EXPECTED_LIVE_SHA256:
        fail('Protected installed ES-DE executable SHA-256 changed')
    if git('rev-parse', 'HEAD').stdout.decode(errors='replace').strip() != EXPECTED_HEAD:
        fail('Dedicated source revision changed')
    if git('status', '--porcelain', '--untracked-files=no').stdout.strip():
        fail('Dedicated source has uncommitted changes; preserving them')
    for name in ('cmake', 'git', 'ffmpeg', 'ps'):
        if not shutil.which(name):
            fail('Missing command ' + name)
    ps = command(['ps', '-eo', 'pid,args'], timeout=8)
    if ps.returncode != 0:
        fail('Cannot verify there is no concurrent build')
    for line in ps.stdout.decode(errors='replace').splitlines():
        if str(BUILD) in line and re.search(r'(cmake --build|\bninja\b|\bmake -j)', line):
            fail('Concurrent build detected; source cannot be changed safely')
    for fp in glob.glob('/proc/[0-9]*/comm'):
        try:
            if os.stat(fp).st_uid == os.getuid() and Path(fp).read_text().strip().lower() in ('shadps4','drrun'):
                fail('Emulator is already running; no other game will be interrupted')
        except (OSError, ValueError):
            pass
    for path in ('bin64/drrun', 'libshadps4_cpu_id.so',
                 'lib64/release/libdynamorio.so'):
        if not (BUILD / 'cpu-id-runtime' / path).is_file():
            fail('Missing CPU-ID runtime component: ' + path)

def graphics_env():
    env = os.environ.copy()
    matches = []
    for proc in glob.glob('/proc/[0-9]*/comm'):
        try:
            pid = int(proc.split('/')[2])
            if os.stat(proc).st_uid != os.getuid():
                continue
            name = Path(proc).read_text().strip().lower()
            if 'es-de' not in name and 'sunshine' not in name:
                continue
            vals = {}
            for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
                if b'=' in item:
                    k, v = item.split(b'=', 1)
                    vals[k.decode(errors='replace')] = v.decode(errors='surrogateescape')
            if vals.get('DISPLAY'):
                matches.append((0 if 'es-de' in name else 1, pid, name, vals))
        except (OSError, ValueError, UnicodeError):
            continue
    matches.sort()
    if matches:
        _, pid, name, vals = matches[0]
        allowed = re.compile(r'^(DISPLAY|WAYLAND_DISPLAY|XAUTHORITY|XDG_RUNTIME_DIR|'
                             r'XDG_DATA_HOME|DBUS_SESSION_BUS_ADDRESS|PATH|LD_LIBRARY_PATH|'
                             r'PULSE_SERVER|VK_.*|RADV_.*|MESA_.*|AMD_.*|SDL_.*)$')
        for k, v in vals.items():
            if allowed.fullmatch(k):
                env[k] = v
        source = f'{name}:{pid}'
    else:
        source = 'SSH_FALLBACK'
        if not env.get('DISPLAY') and Path('/tmp/.X11-unix/X0').exists():
            env['DISPLAY'] = ':0'
        env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    if not env.get('DISPLAY'):
        fail('No X11 display; Moonlight client not required, Sunshine session must exist')
    env['SHADPS4_CPU_ID_MODE'] = 'auto'
    env['SHADPS4_GOW_SUPPRESS_GPU_COMPUTE'] = '1'
    env['SHADPS4_GOW_DIAGNOSTIC_GDS_NONEXECUTING'] = '1'
    env['SHADPS4_GOW_DIAGNOSTIC_ALIAS_GUARD'] = '1'
    env['SHADPS4_GOW_VIDEO_TRACE'] = '1'
    env['SHADPS4_GOW_DRAW_TRACE'] = '1'
    env['SHADPS4_GOW_ONE_SHADER_DMA_COMPILE'] = '1'
    env.pop('SHADPS4_CPU_ID_RESTART', None)
    env.pop('RADV_DEBUG', None)
    (WORK/'display-context.txt').write_text(f'SOURCE={source}\nDISPLAY={env["DISPLAY"]}\n')
    return env

def screenshot(path: Path, env):
    binary = shutil.which('ffmpeg')
    if not binary:
        return False
    args = [binary, '-hide_banner','-nostdin','-loglevel','error',
            '-f','x11grab','-framerate','1','-i',env['DISPLAY'],
            '-frames:v','1','-vf','scale=1280:-2','-q:v','6','-y',str(path)]
    output = command(args, timeout=3, env=env)
    if output.returncode == 0 and path.exists() and path.stat().st_size > 1200:
        return True
    with (WORK/'screenshot-errors.txt').open('a') as f:
        f.write(f'{dt.datetime.now().isoformat()} rc={output.returncode} '
                f'{output.stdout[-500:]!r}\n')
    path.unlink(missing_ok=True)
    return False

def gpu_stats():
    for device in sorted(Path('/sys/class/drm').glob('card*/device')):
        try:
            if (device/'vendor').read_text().strip().lower() != '0x1002':
                continue
            out={}
            for key in ('gpu_busy_percent','mem_info_vram_used','mem_info_gtt_used'):
                try: out[key] = int((device/key).read_text())
                except (OSError,ValueError): pass
            return out
        except OSError:
            pass
    return {}

def watched_pids():
    result = {'xorg': [], 'sunshine': [], 'shadps4': [], 'drrun': []}
    for fp in glob.glob('/proc/[0-9]*/comm'):
        try:
            pid=int(fp.split('/')[2]);name=Path(fp).read_text().strip().lower()
            if name in result: result[name].append(pid)
        except (OSError,ValueError): pass
    return result

def stop_owned(proc):
    if proc is None or proc.poll() is not None:
        return
    for sig, wait in ((signal.SIGINT, 5), (signal.SIGTERM, 5), (signal.SIGKILL, 3)):
        if proc.poll() is not None: return
        try:
            if os.getpgid(proc.pid) != proc.pid:
                fail('Own child process group identity changed; refusing unrelated kill')
            os.killpg(proc.pid, sig)
            proc.wait(timeout=wait)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            pass

def build_trial(patch: bytes, orig: dict[str,bytes]):
    global DIRTY, SOURCE_RESTORED, OWN_BUILD, PHASE
    PHASE = 'check source patch against exact previously working originals'
    for path, prior_contents in orig.items():
        if (SRC / path).read_bytes() != prior_contents:
            fail('Original source differs from the previously tested baseline: '+path)
    for rel in (SPIRV, INFO, BUFFER_CACHE, TEXTURE_CACHE, DRIVER, PRESENTER, DMA_BACKEND):
        if not (SRC / rel).is_file():
            fail('Missing source '+rel)
    # Validate both new edits before changing the tree.
    new_spv,new_info=make_phi_patch((SRC/SPIRV).read_text(), (SRC/INFO).read_text())
    new_info=make_single_dma_info(new_info)
    new_backend=make_single_dma_backend((SRC/DMA_BACKEND).read_text())
    new_buffer_cache=make_alias_patch((SRC/BUFFER_CACHE).read_text())
    if file_sha(SRC/TEXTURE_CACHE) != EXPECTED_TEXTURE_SOURCE_SHA256:
        fail('Texture cache baseline differs from archived trial, no changes applied')
    new_texture,new_driver=video_patch((SRC/TEXTURE_CACHE).read_text(),
                                       (SRC/DRIVER).read_text())
    # Check the additional files before any source modification.
    new_presenter=make_present_trace((SRC/PRESENTER).read_text())
    make_draw_trace((SRC/RASTERIZER).read_text())
    (WORK/'video-source-checksums.json').write_text(json.dumps({
        'texture':file_sha(SRC/TEXTURE_CACHE),'driver':file_sha(SRC/DRIVER)},indent=2))
    # Pin the source version/lookup semantics in every archive; this is not a fix.
    tc=(SRC/TEXTURE_CACHE)
    if tc.is_file():
        contents=tc.read_text()
        (WORK/'texture-cache-lookup-source.txt').write_text(
            'sha256='+digest(contents.encode())+'\n' +
            contents[contents.find('TextureCache::FindImageFromRange('):
                     contents.find('TextureCache::FindImageFromRange(')+2400])
    (WORK/'buffer-cache-source-sha.txt').write_text(file_sha(SRC/BUFFER_CACHE)+'\n')
    # Git does not need a network connection and --check does not modify source.
    patchfile=WORK/'prior-verified-experiment.patch'
    patchfile.write_bytes(patch)
    check=git('apply','--check',str(patchfile))
    if check.returncode:
        fail('Prior known-good source patch does not apply: '
             +check.stdout.decode(errors='replace')[-1000:])
    for rel in (*orig.keys(), SPIRV, INFO, BUFFER_CACHE, TEXTURE_CACHE, DRIVER, PRESENTER, DMA_BACKEND):
        BACKUPS[SRC/rel]=(SRC/rel).read_bytes()
    (WORK/'backup-source-paths.json').write_text(json.dumps(
        {str(k): digest(v) for k,v in BACKUPS.items()},indent=2))
    # Save recovery source bundle in report, independent of in-memory backups.
    with tarfile.open(WORK/'original-sources.tar.gz','w:gz') as tf:
        for rel in (*orig.keys(),SPIRV,INFO,BUFFER_CACHE,TEXTURE_CACHE,DRIVER,PRESENTER,DMA_BACKEND):
            tf.add(SRC/rel,arcname=rel)
    PHASE='apply proven SRT/GDS, verified F64, and isolated image alias diagnostic'
    DIRTY=True
    applied=git('apply', str(patchfile))
    if applied.returncode:
        fail('Prior GDS/SRT patch failed: '+applied.stdout.decode(errors='replace')[-1000:])
    (SRC/SPIRV).write_text(new_spv)
    (SRC/INFO).write_text(new_info)
    (SRC/DMA_BACKEND).write_text(new_backend)
    (SRC/BUFFER_CACHE).write_text(new_buffer_cache)
    (SRC/TEXTURE_CACHE).write_text(new_texture)
    (SRC/DRIVER).write_text(new_driver)
    (SRC/PRESENTER).write_text(new_presenter)
    (SRC/RASTERIZER).write_text(make_draw_trace((SRC/RASTERIZER).read_text()))
    check=git('diff','--check')
    if check.returncode:
        fail('F64/SRT/image-alias patch has diff whitespace errors: '+check.stdout.decode(errors='replace')[-700:])
    (WORK/'full-experiment.patch').write_bytes(git('diff','--',
        *[str(p.relative_to(SRC)) for p in BACKUPS]).stdout)
    PHASE='incremental isolated shadPS4 compilation'
    note('SOURCE_PATCHED_FOR_BUILD_ONLY=YES; prior diagnostic safeguards retained')
    buildlog=WORK/'build.log'
    with buildlog.open('wb') as output:
        OWN_BUILD=subprocess.Popen(['cmake','--build',str(BUILD),'--target','shadps4',
                                    '--parallel','5'],stdout=output,
                                   stderr=subprocess.STDOUT,start_new_session=True)
        try:
            rc=OWN_BUILD.wait(timeout=900)
        except subprocess.TimeoutExpired:
            stop_owned(OWN_BUILD)
            fail('Build exceeded 15 minutes; see build.log')
        finally:
            OWN_BUILD=None
    if rc:
        fail('Compilation failed; see build.log')
    exe=BUILD/'shadps4'
    if not exe.is_file():
        fail('Compilation reported success but shadps4 target is missing')
    TRIAL.mkdir(parents=True,exist_ok=False)
    shutil.copy2(exe, TRIAL/'shadps4')
    shutil.copytree(BUILD/'cpu-id-runtime', TRIAL/'cpu-id-runtime', symlinks=True)
    note('ISOLATED_TRIAL_SHA256='+file_sha(TRIAL/'shadps4'))

def restore_source():
    global DIRTY,SOURCE_RESTORED
    if not DIRTY:
        return
    status={}
    for path, contents in BACKUPS.items():
        try:
            if path.read_bytes()!=contents:
                path.write_bytes(contents)
            status[str(path)] = (path.read_bytes()==contents)
        except OSError as exc:
            status[str(path)] = f'RESTORATION_ERROR: {exc}'
    clean = git('status','--porcelain','--untracked-files=no').stdout.strip()==b''
    status['git_source_clean']=clean
    (WORK/'restoration.json').write_text(json.dumps(status,indent=2))
    DIRTY=False if clean and all(v is True for k,v in status.items() if k!='git_source_clean') else True
    SOURCE_RESTORED=not DIRTY
    if DIRTY:
        fail('Source restoration verification FAILED; do not launch a game')
    note('SOURCE_RESTORATION=PASS')

def contact_sheet(photos):
    try:
        from PIL import Image,ImageDraw
        entries=sorted(photos.glob('*.jpg'))
        if not entries: return
        w,h,cols=410,258,3
        sheet=Image.new('RGB',(cols*w,h*((len(entries)+cols-1)//cols)),(15,15,17))
        draw=ImageDraw.Draw(sheet)
        for n,path in enumerate(entries):
            with Image.open(path) as img:
                small=img.convert('RGB');small.thumbnail((w-10,h-27))
            x,y=(n%cols)*w,(n//cols)*h
            sheet.paste(small,(x+3,y+24))
            draw.text((x+6,y+4),path.stem,fill='white')
        sheet.save(WORK/'contact-sheet.jpg',quality=83)
    except (ImportError,OSError,ValueError) as e:
        (WORK/'contact-sheet-error.txt').write_text(str(e))

def run_game(env):
    global OWN_GAME,PHASE
    PHASE='one bounded isolated image/texel alias diagnostic trial'
    photos=WORK/'screenshots';photos.mkdir()
    capture=[];telemetry=[]
    stop=threading.Event()
    start=time.monotonic()
    def images():
        for at in SHOTS:
            while not stop.is_set() and time.monotonic()-start < at:
                time.sleep(.06)
            if stop.is_set():return
            f=photos/f'{len(capture):02d}_t{at:03d}.jpg'
            ok=screenshot(f,env)
            capture.append({'planned_s':at,'actual_s':round(time.monotonic()-start,2),
                            'ok':ok,'filename':f.name if ok else None})
            note(f'SCREENSHOT t={at}s: {"OK" if ok else "UNAVAILABLE"}')
    stdout=WORK/'full-emulator.log'
    with stdout.open('wb') as sink:
        OWN_GAME=subprocess.Popen([str(TRIAL/'shadps4'),'--cpu-id-mode','auto',
                                    '--game','CUSA34384','--fullscreen','true'],
                                   env=env,stdout=sink,stderr=subprocess.STDOUT,
                                   start_new_session=True)
        worker=threading.Thread(target=images,name='GoWF64PhiPhotos',daemon=True)
        worker.start()
        next_sample=0.0;busy_since=None;reason=None
        try:
            while True:
                elapsed=time.monotonic()-start
                if elapsed>=next_sample:
                    usage=gpu_stats();pids=watched_pids()
                    telemetry.append({'elapsed_s':round(elapsed,2),'gpu':usage,
                                      'pids':pids,'trial_rc':OWN_GAME.poll()})
                    if usage.get('gpu_busy_percent',0)>=99:
                        if busy_since is None:busy_since=elapsed
                    else:busy_since=None
                    next_sample=elapsed+1
                # End early if display capture hangs during a confirmed GPU saturation.
                if busy_since is not None and elapsed-busy_since>=10 and sum(not x['ok'] for x in capture)>=2:
                    reason='GPU_100_PERCENT_AND_DISPLAY_TIMEOUT';break
                if OWN_GAME.poll() is not None:
                    reason='PROCESS_EXIT';break
                if elapsed>=85:
                    reason='TIME_LIMIT';break
                time.sleep(.15)
        finally:
            if OWN_GAME.poll() is None:stop_owned(OWN_GAME)
            OWN_GAME.wait();rc=OWN_GAME.returncode
            OWN_GAME=None;stop.set();worker.join(timeout=4)
    (WORK/'screenshot-timeline.json').write_text(json.dumps(capture,indent=2))
    (WORK/'gpu-process-timeline.json').write_text(json.dumps(telemetry,indent=2))
    contact_sheet(photos)
    counters=collections.Counter()
    evidence=[]
    video_events=[]
    graphics_events=[]
    filter_terms=('GOW_F64_PHI_','Phi node type F64','GOW_GDS_DIAG_','GOW_GPU_COMPUTE_SUPPRESSED',
                  'TypeId: Unreachable','Assertion Failed','Unknown opcode',
                  'GOW_IMAGE_ALIAS_UNSUPPORTED','Unhandled metadata type CMask',
                  'GOW_VO_SUBMIT','GOW_VO_PRESENT_',
                  'GOW_DRAW_','GOW_INDIRECT_','GOW_RT_TARGET','GOW_VO_SOURCE',
                  'GOW_TARGET_DMA_',
                  'GOW_SRT_FLAGGED_COMPUTE','Device lost','Fatal','Compiling vs shader')
    with stdout.open('rb') as f:
        for line in f:
            decoded=line.decode(errors='replace')
            for label,regex in [('f64_phi_type','GOW_F64_PHI_TYPE'),
                                ('f64_info','GOW_F64_PHI_INFO'),
                                ('gds_nonexecution','GOW_GDS_DIAG_DISPATCH_SUPPRESSED'),
                                ('incomplete_srt_compute','GOW_SRT_FLAGGED_COMPUTE'),
                                ('skip_marker','GOW_GPU_COMPUTE_SUPPRESSED'),
                                ('unsupported_phi','Unexpected instruction for offset computation, Phi'),
                                ('unsupported_findilsb','Unexpected instruction for offset computation, FindILsb32'),
                                ('f64_phi_assert','Phi node type F64'),
                                ('image_alias_guard_markers','GOW_IMAGE_ALIAS_UNSUPPORTED'),
                                ('cmask_unhandled','Unhandled metadata type CMask'),
                                ('gpu_device_lost','Device lost during submit'),
                                ('vo_submits','GOW_VO_SUBMIT'),
                                ('vo_present_begin','GOW_VO_PRESENT_BEGIN'),
                                ('vo_present_end','GOW_VO_PRESENT_END'),
                                ('draw_attempt','GOW_DRAW_ATTEMPT'),
                                ('draw_filtered','GOW_DRAW_FILTERED'),
                                ('draw_no_pipeline','GOW_DRAW_NO_PIPELINE'),
                                ('draw_bind_failed','GOW_DRAW_BIND_FAILED'),
                                ('draw_emitted','GOW_DRAW_EMITTED'),
                                ('indirect_attempt','GOW_INDIRECT_ATTEMPT'),
                                ('indirect_emitted','GOW_INDIRECT_EMITTED'),
                                ('render_targets','GOW_RT_TARGET'),
                                ('video_sources','GOW_VO_SOURCE'),
                                ('dma_info','GOW_TARGET_DMA_INFO'),
                                ('dma_dynamic_codegen','GOW_TARGET_DMA_DYNAMIC_CODEGEN')]:
                if regex in decoded:counters[label]+=1
            if any(t in decoded for t in ('GOW_DRAW_','GOW_INDIRECT_','GOW_RT_TARGET','GOW_VO_SOURCE')) and len(graphics_events)<1500:
                graphics_events.append(decoded.strip()[:700])
            if 'GOW_VO_' in decoded and len(video_events)<400:
                video_events.append(decoded.strip()[:450])
            if any(term in decoded for term in filter_terms) and len(evidence)<350:
                evidence.append(decoded.strip()[:700])
    (WORK/'critical-events.txt').write_text('\n'.join(evidence))
    (WORK/'video-flip-events.txt').write_text('\n'.join(video_events))
    (WORK/'graphics-target-events.txt').write_text('\n'.join(graphics_events))
    (WORK/'dma-codegen-proof.txt').write_text('\n'.join(
        x for x in evidence if 'GOW_TARGET_DMA_' in x))
    (WORK/'error-counts.json').write_text(json.dumps(counters,indent=2))
    with stdout.open('rb') as f:
        header=f.read(350_000)
        f.seek(max(0,stdout.stat().st_size-2_200_000));tail=f.read(2_200_000)
    (WORK/'emulator-start.log').write_bytes(header)
    (WORK/'emulator-end.log').write_bytes(tail)
    stdout.unlink()
    result={'game':'CUSA34384','experiment':'F64 Phi + nonexecuting SRT/GDS + image subresource alias guard (diagnostic only)',
            'return_code':rc,'elapsed_s':round(time.monotonic()-start,2),
            'end_reason':reason,'counters':dict(counters),
            'screenshots':sum(x['ok'] for x in capture),
            'menu_confirmed':False,'moonlight_required':False,
            'original_sources_restored':SOURCE_RESTORED}
    (WORK/'trial.json').write_text(json.dumps(result,indent=2))
    return result

def collect_system():
    for label,argv in [('kernel-gpu',['journalctl','-k','--no-pager','--since','-8 minutes']),
                       ('system',['journalctl','--no-pager','--since','-8 minutes'])]:
        r=command(argv,timeout=13)
        rows=r.stdout.decode(errors='replace').splitlines()
        selected=[x for x in rows if re.search(r'amdgpu|radv|drm|shadps4|sunshine|xorg|reset|GPU|hang|segfault|oom',x,re.I)]
        (WORK/(label+'.txt')).write_text('\n'.join(selected[-900:])[-1_400_000:])
    for root in (HOME/'.local/share/shadPS4/log',TRIAL/'user/log'):
        for name in ('CUSA34384.log','shadps4.log'):
            path=root/name
            if path.is_file():
                with path.open('rb') as f:
                    if path.stat().st_size>1_700_000:f.seek(-1_700_000,2)
                    (WORK/('game-'+name)).write_bytes(f.read(1_700_000))

def check_selftest():
    # These test the same source-transform functions the real run calls.
    a='''#include "common/assert.h"
Id TypeId(const EmitContext& ctx, IR::Type type) {
    switch (type) {
    case IR::Type::U64:
        return ctx.U64;
    default:
        UNREACHABLE_MSG("Phi node type {}", type);
    }
}'''
    b='''#include "core/emulator_settings.h"
void Visit(Info& info, const IR::Inst& inst) {
    switch (inst.GetOpcode()) {
        break;
    }
}'''
    c,d=make_phi_patch(a,b)
    assert c.count('case IR::Type::F64:')==1
    assert c.count('GOW_F64_PHI_TYPE')==1
    assert d.count('inst.Type() == IR::Type::F64')==1
    try:
        make_phi_patch(c,d)
    except RuntimeError:
        pass
    else:
        fail('Idempotence safety check did not reject a second patch')
    # Embedded source patch is proven; verify payload and local original files.
    patch, originals=read_proven_patch()
    assert len(originals)==len(BASE_FILES)
    if len(patch)<8000:fail('Previous known-good patch unexpectedly short')
    if SRC.is_dir() and (SRC/SPIRV).is_file() and (SRC/INFO).is_file():
        make_phi_patch((SRC/SPIRV).read_text(),(SRC/INFO).read_text())
        # Check that the bundled GDS/SRT patch actually applies to the
        # currently installed original sources, without changing any files.
        test_patch = WORK / 'selftest-proven-patch.diff'
        test_patch.write_bytes(patch)
        result = git('apply', '--check', str(test_patch))
        if result.returncode:
            fail('Self-test: bundled SRT/GDS patch does not apply: ' +
                 result.stdout.decode(errors='replace')[-900:])
    # Exercise exact shader alias guard anchor before any modification.
    sample = '#include <algorithm>\n' + '    ASSERT_MSG(device_addr == image.info.guest_address,\n               "Texel buffer aliases image subresources {:x} : {:x}", device_addr,\n               image.info.guest_address);'
    alias = make_alias_patch(sample)
    assert alias.count('GOW_IMAGE_ALIAS_UNSUPPORTED') == 1
    assert alias.count('ASSERT_MSG(device_addr == image.info.guest_address') == 1
    try:
        make_alias_patch(alias)
    except RuntimeError:
        pass
    else:
        fail('Repeated alias patch failed to refuse duplicate instrumentation')
    if SRC.is_dir() and (SRC/BUFFER_CACHE).is_file():
        make_alias_patch((SRC/BUFFER_CACHE).read_text())
    if SRC.is_dir() and (SRC/TEXTURE_CACHE).is_file() and (SRC/DRIVER).is_file():
        if file_sha(SRC/TEXTURE_CACHE) != EXPECTED_TEXTURE_SOURCE_SHA256:
            fail('Self-test: texture cache checksum differs from reviewed baseline')
        test_texture,test_driver=video_patch((SRC/TEXTURE_CACHE).read_text(),
                                             (SRC/DRIVER).read_text())
        assert test_texture.count('image.info.guest_address != address') >= 1
        assert test_driver.count('GOW_VO_PRESENT_BEGIN') == 1
        assert test_driver.count('GOW_VO_PRESENT_END') == 1
        assert test_driver.count('GOW_VO_SUBMIT') == 1
    if SRC.is_dir() and (SRC/RASTERIZER).is_file():
        assert make_draw_trace((SRC/RASTERIZER).read_text()).count('GOW_DRAW_EMITTED') == 1
    if SRC.is_dir() and (SRC/PRESENTER).is_file():
        assert make_present_trace((SRC/PRESENTER).read_text()).count('GOW_VO_SOURCE') == 1
    if SRC.is_dir() and (SRC/DMA_BACKEND).is_file():
        assert make_single_dma_backend((SRC/DMA_BACKEND).read_text()).count(
            'GOW_TARGET_DMA_DYNAMIC_CODEGEN') == 1
    if SRC.is_dir() and (SRC/INFO).is_file():
        src_f64=make_phi_patch((SRC/SPIRV).read_text(), (SRC/INFO).read_text())[1]
        assert make_single_dma_info(src_f64).count('GOW_TARGET_DMA_INFO') == 1
    note('SELF_TEST=PASS: prior diagnostics + per-shader BDA codegen without GPU execution')

def main():
    global RESULT,PHASE
    if '--self-test' in sys.argv:
        check_selftest();RESULT='SELF_TEST_PASS';return
    PHASE='check protected installation, source and running emulators'
    assert_ready()
    env=graphics_env()
    if not screenshot(WORK/'preflight-display.jpg',env):
        fail('X11 screenshot capture failed; no game launched')
    if gpu_stats().get('gpu_busy_percent',0)>=97:
        fail('GPU already overloaded (>97%) before trial; no game launched')
    patch,originals=read_proven_patch()
    build_trial(patch,originals)
    restore_source()
    if git('status','--porcelain','--untracked-files=no').stdout.strip():
        fail('Source changed after restoration; no game launched')
    if file_sha(LIVE)!=EXPECTED_LIVE_SHA256:
        fail('Installed ES-DE executable modified unexpectedly; trial not started')
    smoke=command([str(TRIAL/'shadps4'),'--help'],timeout=20,outfile=WORK/'smoke.log')
    if smoke.returncode:
        fail('New isolated executable failed --help smoke test')
    result=run_game(env)
    PHASE='collect GPU and game logs'
    collect_system()
    if file_sha(LIVE)!=EXPECTED_LIVE_SHA256:
        fail('Installed ES-DE executable changed unexpectedly')
    RESULT='TRIAL_COMPLETE'
    note('F64_PHI_EMITTED_MARKERS='+str(result['counters'].get('f64_phi_type',0)))
    note('F64_PHI_UNREACHABLE='+str(result['counters'].get('f64_phi_assert',0)))
    note('VO_SUBMITS='+str(result['counters'].get('vo_submits',0)))
    note('VO_PRESENT_BEGIN='+str(result['counters'].get('vo_present_begin',0)))
    note('VO_PRESENT_END='+str(result['counters'].get('vo_present_end',0)))
    note('GRAPHICS_DRAW_EMITTED_MARKERS='+str(result['counters'].get('draw_emitted',0)))
    note('PRESENTED_IMAGE_MARKERS='+str(result['counters'].get('video_sources',0)))
    note('TARGET_DMA_DYNAMIC_CODEGEN='+str(result['counters'].get('dma_dynamic_codegen',0)))
    note('TARGET_DMA_DISPATCHES_STILL_GUARDED=YES')

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        RESULT='INTERRUPTED_WITH_REPORT'
        (WORK/'error.txt').write_text('Operator interrupted. Only our own trial/build process groups may be stopped.\n')
    except BaseException:
        RESULT='SAFE_STOP_WITH_REPORT'
        (WORK/'error.txt').write_text(traceback.format_exc())
    finally:
        try:
            if OWN_BUILD is not None:stop_owned(OWN_BUILD)
            if OWN_GAME is not None:stop_owned(OWN_GAME)
            if DIRTY:restore_source()
            details={'result':RESULT,'last_phase':PHASE,'previous_patch_sha256':EXPECTED_PATCH_SHA256,
                     'restored_before_game_launch':SOURCE_RESTORED,
                     'installed_esde_sha256':file_sha(LIVE) if LIVE.is_file() else None,
                     'installed_binary_unchanged':LIVE.is_file() and file_sha(LIVE)==EXPECTED_LIVE_SHA256,
                     'source_clean':git('status','--porcelain','--untracked-files=no').stdout.strip()==b'' if SRC.is_dir() else None,
                     'trial_binary':str(TRIAL/'shadps4'),'ssh_session_preserved':True,
                     'game_menu_confirmed':False}
            (WORK/'manifest.json').write_text(json.dumps(details,indent=2))
        except BaseException:
            (WORK/'finalization-error.txt').write_text(traceback.format_exc())
        try:
            with tarfile.open(REPORT,'w:gz',compresslevel=6) as f:
                for item in sorted(WORK.iterdir()):f.add(item,arcname=item.name)
            note('\nRESULT='+RESULT)
            note('REPORT='+str(REPORT))
            note('UPLOAD_THIS_ARCHIVE=YES')
            note('MOONLIGHT=NOT_NEEDED_UNTIL_MENU_OR_INTRO_VISIBLE')
            note('PRODUCTION_ESDE_AND_SSH_UNCHANGED=YES')
        finally:
            shutil.rmtree(WORK,ignore_errors=True)
    sys.exit(0 if RESULT in ('TRIAL_COMPLETE','SELF_TEST_PASS') else 1)
