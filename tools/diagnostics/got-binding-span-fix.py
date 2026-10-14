#!/usr/bin/env python3
"""Fix cached-shader binding spans; retain the verified private RADV/capture setup.

The captured vertex shader uses bindings 20..22, while the live layout places
its buffers at 384..386. AddBindings must count each image's NumBindings(), as
the SPIR-V emitter and descriptor writer already do. No shader is bypassed.
Only direct callers and the shader-cache version TU are rebuilt, in a new tree.
"""
from __future__ import annotations
import datetime as dt
import difflib
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
import types

TREE = 'got-binding-span-20261014'
LABEL = 'got-binding-span-visual'
ORIGINAL_LABEL = 'got-binding-span-source'
PIN = '3b7197f57c2a44048f6365e2568050df7324aa33'
V31_SHA = '1e7264ec822d00060cddf9649fc704217524163e81673d87e5059e64e1375f6f'
V37_SHA = '1a4d7fb9b69a9d2b632298d82472a44b0af81bc912b1058a5fbb3c044c998543'
INFO_SHA = 'ef16d204d0815eb4208b4332dd5277d81f74ff19c5db78edd7783f989168a32a'
RESOURCE_SHA = '8a1c4d7d81e6f88cd700fdd5f9e4d8a57ee62b1b46bd9952d2a968af81f9d347'
VK_SHA = '02be53c70ac824ca42b1df41af93163f0e4bdfe5b1666f270193f51fd58f155d'
SRT_SHA = '5bbdf054553361bda16921a813fc81499994ea62e28340fc90cb5f06034ad1ca'
DRIVER_SHA = '87908e0c6833c0f6dc86cb079cb0f34de0dee7e970da89b5303f0e6b29e326f7'
LAUNCHER_SHA = 'fbb1c8748595bec7abcad46a52ca41799f768c3788d05235699344d55ea0be6f'
HELPERS = {
    'got-radv-retained-capture.py': 'a1b08afea8ecb2f30341e82153b139ebc6d4959aa18405bfa8369b9537a8d885',
    'got-umr-live-capture.py': '2ffdc34ffc17f15331c0a26f4c3117831adcca11977462c95a3ab81554fd0fe0',
    'got-umr-dual-capture.py': '05c881edffa4cfa2fca349b557eb8f5b7b6b94cf09c2d0886ed3700fc5434d8f',
}
INFO = 'src/shader_recompiler/info.h'
CACHE = 'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp'
SERIAL = 'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp'
OLD = '''    void AddBindings(Backend::Bindings& bnd) const {
        bnd.buffer += buffers.size();
        bnd.unified += buffers.size() + images.size() + samplers.size();
    }'''
NEW = '''    void AddBindings(Backend::Bindings& bnd) const {
        bnd.buffer += buffers.size();
        bnd.unified += buffers.size() + samplers.size();
        // Cached stages must advance exactly like SPIR-V emission and the
        // descriptor layout, including image arrays and dynamic mip arrays.
        for (const auto& image : images) {
            bnd.unified += image.NumBindings(*this);
        }
    }'''


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular(p: Path) -> bool:
    return p.is_file() and not p.is_symlink()


def read(p: Path, expected: str | None = None) -> bytes:
    if not regular(p):
        raise RuntimeError('Missing/unsafe input: ' + str(p))
    data = p.read_bytes()
    if expected is not None and digest(data) != expected:
        raise RuntimeError('Pinned input changed: ' + str(p))
    return data


def once(s: str, old: str, new: str) -> str:
    if s.count(old) != 1:
        raise RuntimeError('Source differs at ' + repr(old[:90]))
    return s.replace(old, new, 1)


def save(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() or p.is_symlink():
        if read(p) != data:
            raise RuntimeError('Refusing to overwrite different existing output: ' + str(p))
        return
    with p.open('xb') as f:
        f.write(data)


def run(args: list[str], cwd: Path | None = None, limit: int = 30) -> str:
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=limit)
    if p.returncode:
        raise RuntimeError(shlex.join(args[:5]) + ': ' + (p.stderr or p.stdout)[-1800:])
    return p.stdout


def patched_sources(info: str, cache: str, serial: str) -> dict[str, str]:
    header = once(info, OLD, NEW)
    marker = '        info.AddBindings(binding);'
    log = '''        const auto binding_span_start = binding.unified;
        info.AddBindings(binding);
        const auto old_next = binding_span_start + info.buffers.size() +
                              info.images.size() + info.samplers.size();
        if (binding.unified != old_next) {
            static std::atomic_flag binding_span_logged = ATOMIC_FLAG_INIT;
            if (!binding_span_logged.test_and_set(std::memory_order_relaxed)) {
                LOG_WARNING(Render_Vulkan,
                            "GOT_BINDING_SPAN_FIXED shader={:#x} start={} old_next={} new_next={}",
                            info.pgm_hash, binding_span_start, old_next, binding.unified);
            }
        }'''
    cache = once(cache, marker, log)
    if '#include <atomic>' not in cache:
        i = cache.find('#include ')
        if i < 0:
            raise RuntimeError('Pipeline-cache include section missing')
        cache = cache[:i] + '#include <atomic>\n' + cache[i:]
    pat = r'(?m)^(static constexpr u32 ShaderBinaryVersion = )(\d+)(u;)$'
    m = re.search(pat, serial)
    if not m or len(re.findall(pat, serial)) != 1 or int(m[2]) >= 65534:
        raise RuntimeError('Shader cache version is not uniquely recognized')
    serial = serial[:m.start()] + m[1] + str(int(m[2])+1) + m[3] + serial[m.end():]
    return {INFO: header, CACHE: cache, SERIAL: serial}


def parse_command(text: str) -> list[str]:
    text = text.strip()
    if text.startswith(': && ') and text.endswith(' && :'):
        text = text[5:-5]
    if any(x in text for x in ('\n', '&&', ';', '|', '>', '<', '`', '$(')):
        raise RuntimeError('Complex shell command rejected')
    return shlex.split(text)


def compile_command(command: str, a: Path, c: Path, rel: str) -> list[str]:
    args = parse_command(command)
    obj = 'CMakeFiles/shadps4.dir/' + rel + '.o'
    out = str(c/'build'/obj)
    if args.count(str(a/rel)) != 1 or args.count('-o') != 1:
        raise RuntimeError('Ambiguous compile command for ' + rel)
    if args[args.index('-o')+1] != obj:
        raise RuntimeError('Unexpected object output for ' + rel)
    args = [str(c/rel) if x == str(a/rel) else
            out if x == obj else out+'.d' if x == obj+'.d' else x for x in args]
    # Put the candidate source include root BEFORE the original one. Do not
    # replace external/generated dependency include directories with empty ones.
    ix = next((i for i,x in enumerate(args) if x.startswith('-I') or x == '-isystem'), None)
    if ix is None:
        raise RuntimeError('Cannot place candidate include root')
    args[ix:ix] = ['-I'+str(c/'src')]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    return args


def link_command(script: str, a: Path, b: Path, c: Path, rels: list[str]) -> list[str]:
    lines = [s for s in script.splitlines() if s.strip() and not s.startswith('#')
             and not s.startswith('set ')]
    if len(lines) != 1:
        raise RuntimeError('Expected one retained linker command')
    args = parse_command(lines[0])
    old = str(b/'build/shadps4')
    if args.count('-o') != 1 or args[args.index('-o')+1] != old:
        raise RuntimeError('Retained linker output not recognized')
    for rel in rels:
        obj = 'CMakeFiles/shadps4.dir/' + rel + '.o'
        indices = [i for i,x in enumerate(args) if x in (obj, str(a/'build'/obj))]
        if len(indices) != 1:
            raise RuntimeError('Retained link input is not unique: ' + rel)
        args[indices[0]] = str(c/'build'/obj)
    args = [str(c/'build/shadps4') if x == old else
            x.replace(str(b/'build/link.d'), str(c/'build/link.d')) for x in args]
    if any('dependency-file=' in x and str(c/'build/link.d') not in x for x in args):
        raise RuntimeError('Unknown linker dependency output')
    return args


def build(project: Path) -> dict:
    a = project/'tests/got-v31-nptoolkit-mspace-20261013'
    b = project/'tests/got-v37-guest-image-safety-20261013'
    c = project/'tests'/TREE
    for d in (a, b, project/'tools', project/'scripts'):
        if not d.is_dir() or d.is_symlink():
            raise RuntimeError('Missing/unsafe project directory: ' + str(d))
    checks = {a/'build/shadps4': V31_SHA, b/'build/shadps4': V37_SHA,
              b/INFO: INFO_SHA, b/'src/shader_recompiler/resource.h': RESOURCE_SHA,
              b/'src/video_core/renderer_vulkan/vk_rasterizer.cpp': VK_SHA,
              b/'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp': SRT_SHA,
              project/'tools/radv-nuw-complete-25.0.7/libvulkan_radeon.so': DRIVER_SHA}
    for p,h in checks.items():
        read(p,h)
    for p in (a,b):
        if run(['git','-C',str(p),'rev-parse','HEAD']).strip() != PIN:
            raise RuntimeError('Retained worktree HEAD changed')
    callers = []
    callpat = re.compile(r'(?:\.|->)\s*AddBindings\s*\(')
    for p in (b/'src').rglob('*'):
        if p.suffix in ('.h','.cpp') and regular(p) and callpat.search(p.read_text()):
            if p.suffix != '.cpp':
                raise RuntimeError('Header caller needs wider dependency rebuild: ' + str(p))
            callers.append(str(p.relative_to(b)))
    if CACHE not in callers or len(callers)>6:
        raise RuntimeError('Unexpected AddBindings caller set: '+repr(callers))
    units = sorted(set(callers + [SERIAL]))
    for rel in units:
        if read(a/rel) != read(b/rel):
            raise RuntimeError('Retained object provenance differs for ' + rel)
    edits = patched_sources(read(b/INFO).decode(), read(b/CACHE).decode(), read(b/SERIAL).decode())
    if not c.exists():
        if c.is_symlink() or shutil.disk_usage(project).free < 700*1024*1024:
            raise RuntimeError('Unsafe target or less than 700 MiB free')
        run(['git','-C',str(b),'worktree','add','--detach',str(c),PIN], limit=60)
        # Replay all tracked source modifications rather than reconstructing a
        # subset of the retained work. Untracked src headers are carried too.
        diff = subprocess.check_output(['git','-C',str(b),'diff','--binary','HEAD','--','src'])
        if diff:
            p = subprocess.run(['git','-C',str(c),'apply','--binary','-'],input=diff,capture_output=True)
            if p.returncode:
                raise RuntimeError('Could not preserve v37 source delta: '+p.stderr.decode()[-1000:])
        untracked = subprocess.check_output(['git','-C',str(b),'ls-files','--others','--exclude-standard','-z','--','src'])
        for raw in untracked.split(b'\0'):
            if not raw: continue
            rel = Path(os.fsdecode(raw))
            if not (b/rel).resolve().is_relative_to(b.resolve()) or not regular(b/rel):
                raise RuntimeError('Unsafe untracked source input')
            save(c/rel,read(b/rel))
    if c.is_symlink() or run(['git','-C',str(c),'rev-parse','HEAD']).strip()!=PIN:
        raise RuntimeError('Candidate worktree not recognized')
    for rel,text in edits.items():
        current = read(c/rel)
        if current not in (read(b/rel),text.encode()):
            raise RuntimeError('Candidate source already changed: '+rel)
        (c/rel).write_text(text)
    for rel in ('src/video_core/renderer_vulkan/vk_rasterizer.cpp',
                'src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp'):
        if read(c/rel)!=read(b/rel):
            raise RuntimeError('Candidate lost a retained fix: '+rel)
    proof = c/'build/binding-span-proof.json'
    if proof.exists():
        meta=json.loads(read(proof)); read(c/'build/shadps4',meta['binary_sha256'])
        for rel,h in meta['source_sha256'].items():read(c/rel,h)
        print('BUILD=REUSE_VERIFIED_BINDING_SPAN_BINARY',flush=True)
        return meta
    if (c/'build/shadps4').exists():
        raise RuntimeError('Unverified existing candidate executable; preserving it')
    logroot=project.parent/'logs';logroot.mkdir(parents=True,exist_ok=True)
    log=logroot/('got-binding-span-build-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'.log')
    cmds=run(['ninja','-C',str(a/'build'),'-t','commands','shadps4'],limit=60).splitlines()
    compiled=[]
    for rel in units:
        matching=[x for x in cmds if str(a/rel) in x and ' -c ' in x]
        if len(matching)!=1:raise RuntimeError('No unique compile command: '+rel)
        compiled.append(compile_command(matching[0],a,c,rel))
    link=link_command(read(b/'build/link-v37.sh').decode(),a,b,c,units)
    (c/'build').mkdir(exist_ok=True)
    save(c/'build/commands.json',(json.dumps(compiled+[link],indent=2)+'\n').encode())
    patch=''.join(''.join(difflib.unified_diff(read(b/rel).decode().splitlines(True),text.splitlines(True),
                 fromfile='a/'+rel,tofile='b/'+rel)) for rel,text in edits.items())
    save(c/'build/binding-span.patch',patch.encode())
    with log.open('ab') as out:
        for ix,command in enumerate(compiled+[link]):
            print(('COMPILE='+units[ix]) if ix<len(units) else 'LINK=RETAINED_V37_PLUS_BINDING_FIX',flush=True)
            out.write((shlex.join(command)+'\n').encode());out.flush()
            result=subprocess.run(command,cwd=a/'build',stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,timeout=900)
            if result.returncode:raise RuntimeError('Compiler/linker failed; upload '+str(log))
    for p,h in checks.items():read(p,h)
    binary_sha=digest(read(c/'build/shadps4'))
    if binary_sha==V37_SHA:raise RuntimeError('Candidate binary unchanged')
    meta={'binary_sha256':binary_sha,'parent_binary_sha256':V37_SHA,'compiled_units':units,
          'source_sha256':{rel:digest(text.encode()) for rel,text in edits.items()},
          'private_driver_sha256':DRIVER_SHA,'gameplay_verified':False,'build_log':str(log)}
    save(proof,(json.dumps(meta,indent=2)+'\n').encode())
    print('BUILD=PASS; PREVIOUS_BINARIES=PRESERVED; DRIVER=UNCHANGED',flush=True)
    return meta


def load_module(name: str, code: str, script: Path):
    module=types.ModuleType(name);module.__file__=str(script)
    exec(compile(code,str(script)+'::'+name,'exec'),module.__dict__)
    return module


def load_runtime(script: Path, project: Path, meta: dict):
    codes={name:read(script.with_name(name),h).decode() for name,h in HELPERS.items()}
    retained=codes['got-radv-retained-capture.py']
    retained=once(retained,r"raw.split(b'\\0')",r"raw.split(b'\0')")
    retained=retained.replace('PRIVATE_RADV=EXACT_RETAINED_V17_HASH; SYSTEM_RADV=UNTOUCHED',
                              'PRIVATE_RADV=UNCHANGED_VERIFIED_NUW_COPY; SYSTEM_RADV=UNTOUCHED')
    mod=load_module('got_span_retained',retained,script)
    mod.LABEL=LABEL;mod.RETAINED_SHA=DRIVER_SHA
    mod.SITES=((0x9ca54,bytes.fromhex('80 48 24 04')),)+mod.SITES
    mod.paths=lambda p:(p/'tools/radv-nuw-complete-25.0.7/libvulkan_radeon.so',
                       p/'tools/radv-nuw-complete-25.0.7/radeon_icd.json')
    def prepare(p):
        mod.verify_files(p)
        print('DRIVER=PREVIOUSLY_VERIFIED_NUW_COPY; NO_NEW_DRIVER_PATCHES',flush=True)
        return {'gameplay_verified':False}
    mod.prepare_driver=prepare
    mod.GDB_GATE=once(mod.GDB_GATE,'((0x9ca84, 4), (0x12a72c, 5))',
                     '((0x9ca54, 4), (0x9ca84, 4), (0x12a72c, 5))')
    mod.GDB_GATE=mod.GDB_GATE.replace('GOT_RETAINED_RADV_LOADED','GOT_BINDING_SPAN_DRIVER_LOADED')
    # Preserve the original launcher and verified helpers. Execute independently
    # named in-memory copies with only candidate path/hash/label substitutions.
    original=read(project/'scripts/got-v37-descriptor-visual.sh',LAUNCHER_SHA).decode()
    oldtree='got-v37-guest-image-safety-20261013'
    newsource=original.replace(oldtree,TREE).replace('got-v37-descriptor-visual',ORIGINAL_LABEL).replace(V37_SHA,meta['binary_sha256'])
    launcher=project/'scripts'/(ORIGINAL_LABEL+'.sh')
    save(launcher,newsource.encode());launcher.chmod(0o700)
    live=load_module('got_span_live',codes['got-umr-live-capture.py'].replace(oldtree,TREE).replace('GAME=v37_UNCHANGED','GAME=BINDING_SPAN_FIXED'),script)
    live.GAME_SHA=meta['binary_sha256'];live.LAUNCHER_SHA=digest(newsource.encode());live.OLD_LABEL=ORIGINAL_LABEL
    dual=load_module('got_span_dual',codes['got-umr-dual-capture.py'].replace(oldtree,TREE),script)
    dual.install_hooks(live)
    live.LABEL=LABEL;live.__file__=str(script)
    mod.extend_gdb(dual,project)
    previous_transform,previous_gather=live.transform,live.gather
    def transform(source,helper,session):
        text=previous_transform(source,helper,session)
        icd=mod.paths(project)[1]
        text=once(text,'    export RADV_DEBUG=hang\n','    export RADV_DEBUG=hang\n'
                  '    export VK_DRIVER_FILES='+shlex.quote(str(icd))+'\n'
                  '    export VK_ICD_FILENAMES='+shlex.quote(str(icd))+'\n'
                  '    export MESA_SHADER_CACHE_DISABLE=true\n')
        return text.replace('got-umr-dual-portable-ready-','got-binding-span-portable-ready-')
    def gather(session,report):
        rc=previous_gather(session,report)
        mod.verify_files(project)
        proof=session/'native/driver-provenance.json'
        driver=json.loads(proof.read_text()) if live.regular(proof) else {'result':'NOT_OBSERVED'}
        live.store(report/'info/binding-span.json',dict(meta,driver=driver,gameplay_verified=False))
        shutil.copyfile(project/'tests'/TREE/'build/binding-span.patch',report/'info/binding-span.patch')
        return rc
    live.transform,live.gather=transform,gather
    mod.load_stack=lambda p:(dual,live)
    return mod,dual,live


def main() -> int:
    os.umask(0o077)
    script=Path(__file__).resolve();project=script.parent.parent
    if script.parent.name!='scripts' or project.name!='GhostOfTsushima':
        raise RuntimeError('Place helper in ~/projects/shadps4/GhostOfTsushima/scripts')
    args=sys.argv[1:]
    proof=project/'tests'/TREE/'build/binding-span-proof.json'
    if not args or args==['build']:
        if os.geteuid()==0:raise RuntimeError('Run as your normal user, not sudo python')
        scanner=load_module('got_span_scan',read(script.with_name('got-radv-retained-capture.py'),
                            HELPERS['got-radv-retained-capture.py']).decode().replace(
                                r"raw.split(b'\\0')",r"raw.split(b'\0')"),script)
        games=scanner.running_emulators()
        if games:scanner.show_busy(games);return 0
        with (project/'scripts/.got-binding-span-build.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            meta=build(project)
        if args==['build']:return 0
    else:
        meta=json.loads(read(proof));read(project/'tests'/TREE/'build/shadps4',meta['binary_sha256'])
    mod,dual,live=load_runtime(script,project,meta)
    if args and args[0]=='supervise':
        _,icd=mod.verify_files(project)
        os.environ.update(VK_DRIVER_FILES=str(icd),VK_ICD_FILENAMES=str(icd),MESA_SHADER_CACHE_DISABLE='true')
        return dual.supervise(live,Path(args[1]),Path(args[2]),args[3:])
    return mod.main()


if __name__=='__main__':
    try:sys.exit(main())
    except KeyboardInterrupt:sys.exit(130)
    except Exception as exc:
        print('STOP: '+str(exc),file=sys.stderr);sys.exit(1)
