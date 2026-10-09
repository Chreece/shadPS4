#!/usr/bin/env python3
"""Dump only guarded GoW shader 0x57b077ac SPIR-V and complete DMA markers."""
from __future__ import annotations
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile

URL=('https://raw.githubusercontent.com/Chreece/shadPS4/'
     'b73a4ba79a175e55d899a46a127298c51d7a5e8a/scripts/'
     'diagnose-gow-dma-codegen-20261010.py')
BLOB='4dc8b22ff3454f9ee019f87b728c09013bc65a79'


def one(s: str, old: str, new: str, label: str) -> str:
    count=s.count(old)
    if count!=1:
        raise RuntimeError(f'SAFE_STOP: {label}: expected one source anchor, found {count}')
    return s.replace(old,new,1)


PATCH_FUNCTIONS=r'''
PIPELINE_CACHE = 'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp'

def patch_target_spv(s):
    s=patch_once(s,'#include "common/hash.h"',
        '#include <cstdlib>\n#include <filesystem>\n#include "common/hash.h"',
        'SPIR-V dump source includes')
    old = ''' + repr('''    if (!EmulatorSettings.IsDumpShaders()) {
        return;
    }

    using namespace Common::FS;
    const auto dump_dir = GetUserPath(PathType::ShaderDir) / "dumps";''') + r'''
    new = ''' + repr('''    const char* gow_dir = std::getenv("SHADPS4_GOW_DMA_SPV_DIR");
    const bool gow_only = gow_dir != nullptr && hash == 0x57b077acULL &&
                          stage == Shader::HwStage::Compute && ext == "spv";
    if (!EmulatorSettings.IsDumpShaders() && !gow_only) {
        return;
    }

    using namespace Common::FS;
    const auto dump_dir = gow_only ? std::filesystem::path(gow_dir) :
                          GetUserPath(PathType::ShaderDir) / "dumps";''') + r'''
    return patch_once(s,old,new,'SPIR-V target-only dump gate')

def collect_dma_spv():
    import hashlib
    path=WORK/'full-emulator.log'
    markers=[]
    if path.is_file():
        with path.open(errors='replace') as f:
            for line in f:
                if 'GOW_TARGET_DMA_' in line or ('GOW_GPU_COMPUTE_SUPPRESSED' in line and
                                                  'pgm_hash=0x57b077ac' in line):
                    markers.append(line.rstrip()[-700:])
    (WORK/'dma-complete-proof.txt').write_text('\n'.join(markers))
    found=[]
    for item in sorted((WORK/'dma-spv').glob('*.spv')):
        blob=item.read_bytes()
        found.append({'file':item.name,'bytes':len(blob),
                      'sha256':hashlib.sha256(blob).hexdigest(),
                      'spirv_magic_ok':blob[:4]==b'\x03\x02\x23\x07'})
    (WORK/'dma-spv-index.json').write_text(json.dumps(found,indent=2))
    note('TARGET_SPV_CAPTURED='+str(len(found)))
    note('COMPLETE_DMA_MARKERS='+str(len(markers)))

'''


def transform(s: str) -> str:
    s=one(s,"f'shadps4-gow-dma-codegen-{STAMP}.tar.gz'",
            "f'shadps4-gow-dma-spv-{STAMP}.tar.gz'",'archive name')
    s=one(s,"f'Applications/shadps4-gow-dma-codegen-trial-{STAMP}'",
            "f'Applications/shadps4-gow-dma-spv-trial-{STAMP}'",'trial name')
    s=one(s,"prefix='.gow-dma-codegen-'","prefix='.gow-dma-spv-'",'working directory')
    s=one(s,'def read_proven_patch():',PATCH_FUNCTIONS+'def read_proven_patch():','helpers')
    s=one(s,'    for rel in (SPIRV, INFO, BUFFER_CACHE, TEXTURE_CACHE, DRIVER, PRESENTER, DMA_BACKEND):',
        '    for rel in (SPIRV, INFO, BUFFER_CACHE, TEXTURE_CACHE, DRIVER, PRESENTER, DMA_BACKEND, PIPELINE_CACHE):',
        'preflight source')
    s=one(s,'    new_backend=make_single_dma_backend((SRC/DMA_BACKEND).read_text())',
        '    new_backend=make_single_dma_backend((SRC/DMA_BACKEND).read_text())\n'
        '    new_pipeline=patch_target_spv((SRC/PIPELINE_CACHE).read_text())','patch preflight')
    s=one(s,'    for rel in (*orig.keys(), SPIRV, INFO, BUFFER_CACHE, TEXTURE_CACHE, DRIVER, PRESENTER, DMA_BACKEND):',
        '    for rel in (*orig.keys(), SPIRV, INFO, BUFFER_CACHE, TEXTURE_CACHE, DRIVER, PRESENTER, DMA_BACKEND, PIPELINE_CACHE):',
        'source backup')
    s=one(s,'        for rel in (*orig.keys(),SPIRV,INFO,BUFFER_CACHE,TEXTURE_CACHE,DRIVER,PRESENTER,DMA_BACKEND):',
        '        for rel in (*orig.keys(),SPIRV,INFO,BUFFER_CACHE,TEXTURE_CACHE,DRIVER,PRESENTER,DMA_BACKEND,PIPELINE_CACHE):',
        'recovery archive')
    s=one(s,'    (SRC/DMA_BACKEND).write_text(new_backend)',
        '    (SRC/DMA_BACKEND).write_text(new_backend)\n'
        '    (SRC/PIPELINE_CACHE).write_text(new_pipeline)','patch write')
    s=one(s,"    env['SHADPS4_GOW_ONE_SHADER_DMA_COMPILE'] = '1'",
        "    env['SHADPS4_GOW_ONE_SHADER_DMA_COMPILE'] = '1'\n"
        "    (WORK/'dma-spv').mkdir(exist_ok=True)\n"
        "    env['SHADPS4_GOW_DMA_SPV_DIR'] = str(WORK/'dma-spv')",'SPV output directory')
    s=one(s,'    stdout.unlink()', '    collect_dma_spv()\n    stdout.unlink()',
          'archive markers and SPV before deleting long log')
    s=one(s,"    note('SELF_TEST=PASS: prior diagnostics + per-shader BDA codegen without GPU execution')",
        "    if SRC.is_dir() and (SRC/PIPELINE_CACHE).is_file():\n"
        "        assert patch_target_spv((SRC/PIPELINE_CACHE).read_text()).count('SHADPS4_GOW_DMA_SPV_DIR')==1\n"
        "    note('SELF_TEST=PASS: targeted SPIR-V dump with all invalid-resource compute still guarded')",
        'self-test')
    if 'GOW_GPU_COMPUTE_SUPPRESSED' not in s or 'SHADPS4_GOW_DMA_SPV_DIR' not in s:
        raise RuntimeError('SAFE_STOP: guarded compute or targeted SPV marker missing')
    return s


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='gow-dma-spv-wrapper-') as d:
        source=Path(d)/'verified-dma.py'
        result=Path(d)/'target-spv.py'
        p=subprocess.run(['curl','-fLsS','--retry','3',URL,'-o',str(source)],check=False)
        if p.returncode:
            raise RuntimeError('SAFE_STOP: pinned base download failed')
        raw=source.read_bytes()
        git_sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if git_sha!=BLOB:
            raise RuntimeError('SAFE_STOP: base script checksum mismatch')
        code=transform(raw.decode())
        compile(code,str(result),'exec')
        result.write_text(code)
        if '--self-test' in sys.argv:
            print('WRAPPER_TRANSFORM_SYNTAX=PASS',flush=True)
            return subprocess.run([sys.executable,str(result),'--self-test'],check=False).returncode
        print('TARGET_SPV_ONLY=0x57b077ac; GPU_DISPATCH_STILL_GUARDED=YES',flush=True)
        return subprocess.run([sys.executable,str(result)],check=False).returncode

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as e:
        print('SAFE_STOP_WRAPPER='+str(e),file=sys.stderr)
        sys.exit(1)
