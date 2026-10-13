#!/usr/bin/env python3
"""GoW87: isolated shader-permutation metadata correction on the working Phase84 build.
No CMake regeneration, copied build directory, original-source edit, or shader skip.
The existing Ninja dependency records select which original compilation commands to replay
into a private overlay. Everything else is relinked from the existing object files.
"""
from __future__ import annotations
import argparse
import datetime
import difflib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import types

HOME = Path.home()
ROOT = HOME / 'projects/shadps4/GodOfWarRagnarok'
LOGS = HOME / 'projects/shadps4/logs'
BASE = ROOT / 'builds/gow84-ds-ordered-20261013-194151-79151'
SRC = BASE / 'source'
BUILD = BASE / 'build'
BINARY_SHA = '7f6b320cdc2d60e0fc9f4e72b3bde58a2f2f18e3b7f1783825732a0a6ccf76ae'
BASE_RUNNER_SHA = '04537776a5fe9c931fa697e0babd316de5d006ada3ed875a178e73fda0462052'
BASE_RUNNER_URL = ('https://raw.githubusercontent.com/Chreece/shadPS4/'
    '6bfd0acb1f2561760b4c2c13c416117e985bd600/tools/gow86-original-profile-dma.py')
PATCH_SHA = '35e1b1299070ab3f7dea09d087bc81b4b3c9e8672fbcd2259317f52508d554f8'
PATCH_URL = 'https://raw.githubusercontent.com/Chreece/shadPS4/6f4d48b3dd4cf2fb9d487ba661e2478856b55408/tools/gow87-permutation-metadata.patch'
FILES = (
    'src/video_core/renderer_vulkan/vk_pipeline_cache.h',
    'src/video_core/renderer_vulkan/vk_pipeline_cache.cpp',
    'src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp',
    'src/shader_recompiler/specialization.h',
)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return h.hexdigest()


def note(text):
    print('GOW87 ' + text, flush=True)


def stop(proc):
    if proc.poll() is not None:
        return
    group = os.getpgid(proc.pid)
    if group != proc.pid or group == os.getpgrp():
        raise RuntimeError('Refusing to signal an SSH/shared process group')
    os.killpg(group, signal.SIGTERM)
    try:
        proc.wait(timeout=7)
    except subprocess.TimeoutExpired:
        os.killpg(group, signal.SIGKILL)
        proc.wait(timeout=7)


def run(command, log, cwd=None, timeout=900):
    command = list(map(str, command))
    with Path(log).open('wb') as output:
        p = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = p.wait(timeout=timeout)
        except BaseException:
            stop(p)
            raise
    if code:
        tail = '\n'.join(Path(log).read_text(errors='replace').splitlines()[-18:])
        raise RuntimeError(f'Command failed ({code}); {log}\n{tail}')


def read_command(command, cwd):
    return subprocess.check_output(list(map(str, command)), cwd=cwd, text=True,
                                   stderr=subprocess.PIPE, timeout=75)


def fetch(url, path, expected):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError('Refusing a symlinked downloaded dependency')
    if path.is_file() and digest(path) == expected:
        return
    fd, tmp = tempfile.mkstemp(prefix='.gow87-fetch-', dir=path.parent)
    os.close(fd)
    try:
        subprocess.run(['curl', '-4fsSL', '--retry', '2', '--connect-timeout', '20',
                        '--max-time', '120', url, '-o', tmp], check=True, timeout=380)
        if digest(tmp) != expected:
            raise RuntimeError('Downloaded file integrity mismatch: ' + path.name)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def load_transaction():
    scripts = ROOT / 'scripts'
    scripts.mkdir(parents=True, exist_ok=True)
    candidates = [scripts/'gow86.py', scripts/'gow86-transaction-base.py']
    path = next((p for p in candidates if p.is_file() and not p.is_symlink()
                 and digest(p) == BASE_RUNNER_SHA), None)
    if path is None:
        path = candidates[1]
        fetch(BASE_RUNNER_URL, path, BASE_RUNNER_SHA)
    raw = path.read_text()
    original = types.ModuleType('gow86_recovery')
    original.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), original.__dict__)
    # Recover any old interrupted Phase86 transaction first. Neither recovery
    # path needs the old log directories.
    original.recover_pending()
    source = raw.replace('gow86', 'gow87').replace('GOW86', 'GOW87')
    module = types.ModuleType('gow87_transaction')
    module.__file__ = str(path)
    exec(compile(source, 'gow87_transaction', 'exec'), module.__dict__)
    module.recover_pending()
    return module


def resolve_output(value, directory):
    p = Path(value)
    return (p if p.is_absolute() else Path(directory)/p).resolve()


def expand_response(args, directory, depth=0):
    if depth > 3:
        raise RuntimeError('Nested compiler response file limit')
    result = []
    for token in args:
        if token.startswith('@'):
            path = resolve_output(token[1:], directory)
            result.extend(expand_response(shlex.split(path.read_text()), directory, depth+1))
        else:
            result.append(token)
    return result


def compilation_records(build):
    path = build/'compile_commands.json'
    records = json.loads(path.read_text()) if path.is_file() else json.loads(
        read_command(['ninja', '-t', 'compdb'], build))
    result = {}
    for entry in records:
        directory = Path(entry.get('directory', build)).resolve()
        args = entry.get('arguments') or shlex.split(entry['command'])
        if '-c' not in args or '-o' not in args:
            continue
        args = expand_response(args, directory)
        output = resolve_output(args[args.index('-o')+1], directory)
        source = resolve_output(args[args.index('-c')+1], directory)
        result[output] = (args, source, directory)
    if not result:
        raise RuntimeError('No usable compilation records in the existing build')
    return result


def dependencies(build):
    text = read_command(['ninja', '-t', 'deps'], build)
    result, current = {}, None
    for line in text.splitlines():
        if ': #deps ' in line and not line.startswith(' '):
            current = resolve_output(line.split(': #deps ', 1)[0], build)
            result[current] = set()
        elif current is not None and line.startswith('    '):
            result[current].add(resolve_output(line.strip(), build))
    if not result:
        raise RuntimeError('Existing Ninja dependency records are unavailable')
    return result


def link_arguments(build, target='shadps4'):
    text = read_command(['ninja', '-t', 'commands', target], build)
    matches = []
    for line in text.splitlines():
        if '-o ' not in line:
            continue
        args = shlex.split(line)
        sections, section = [], []
        for token in args:
            if token == '&&':
                sections.append(section); section = []
            else:
                section.append(token)
        sections.append(section)
        for section in sections:
            if '-o' not in section:
                continue
            index = section.index('-o')
            if index+1 < len(section) and resolve_output(section[index+1], build) == (build/target).resolve():
                matches.append(section)
    if len(matches) != 1:
        raise RuntimeError(f'Expected one existing linker command, found {len(matches)}')
    args = expand_response(matches[0], build)
    if any(x in args for x in (';', '|', '||', '>', '<')):
        raise RuntimeError('Unsupported shell operator in link command')
    return args


def compile_arguments(args, source, directory, object_path, overlay, changed_sources):
    if any(x in args for x in ('&&', ';', '|', '||', '>', '<')):
        raise RuntimeError('Unsupported shell-wrapped compilation command')
    if any('cmake_pch' in x or x.startswith(('-save-temps', '-fdump-')) for x in args):
        raise RuntimeError('PCH or side-output flags need explicit isolation; refusing')
    compilers = [i for i, x in enumerate(args) if re.fullmatch(
        r'(?:[^/]+-)?(?:c\+\+|g\+\+|clang\+\+)(?:-[0-9.]+)?', Path(x).name)]
    if len(compilers) != 1:
        raise RuntimeError('C++ compiler not recognized in compilation command')
    compiler = compilers[0]
    new = []
    i = 0
    while i < len(args):
        token = args[i]
        if token in ('-o', '-MF', '-MT', '-MQ'):
            i += 2; continue
        if token in ('-MD', '-MMD', '-MP'):
            i += 1; continue
        if token == '-c':
            new.extend(['-c', str(changed_sources.get(source, source))]); i += 2; continue
        new.append(token)
        if i == compiler:
            new.extend(['-I', str(overlay/'src'), '-I', str(overlay)])
        i += 1
    depfile = object_path.with_suffix('.d')
    new.extend(['-MMD', '-MF', str(depfile), '-o', str(object_path)])
    return new, depfile


def isolated_link(args, build, replacements, binary):
    rewritten = []
    replaced = set()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '-o':
            rewritten.extend(['-o', str(binary)]); i += 2; continue
        if arg.startswith('-Wl,--dependency-file='):
            rewritten.append('-Wl,--dependency-file='+str(binary.parent/'link.d'))
        elif arg.endswith(('.o', '.obj')):
            source = resolve_output(arg, build)
            if source in replacements:
                rewritten.append(str(replacements[source])); replaced.add(source)
            else:
                rewritten.append(arg)
        else:
            rewritten.append(arg)
        i += 1
    if replaced != set(replacements):
        raise RuntimeError('Not all changed objects are present in the linker command')
    return rewritten


def overlay_build(build, source_root, overlay, output, logs, changed_files, target='shadps4'):
    """Replay only affected compilation commands, writing nothing to the input build."""
    records = compilation_records(build)
    depmap = dependencies(build)
    linking = link_arguments(build, target)
    linked_objects = {resolve_output(x, build) for x in linking if x.endswith(('.o', '.obj'))}
    changed_headers = {(source_root/p).resolve() for p in changed_files if p.endswith('.h')}
    changed_sources = {(source_root/p).resolve(): overlay/p for p in changed_files if p.endswith('.cpp')}
    selected = {}
    for obj in linked_objects:
        record = records.get(obj)
        if record is None:
            continue
        _, source, _ = record
        includes = depmap.get(obj, set())
        if source in changed_sources or includes.intersection(changed_headers):
            selected[obj] = record
    if set(changed_sources) - {r[1] for r in selected.values()}:
        raise RuntimeError('A changed source has no original linked object')
    if any(not any(h in depmap.get(o, set()) for o in selected) for h in changed_headers):
        raise RuntimeError('Header dependency coverage incomplete; not reusing incompatible objects')
    if not selected:
        raise RuntimeError('No affected objects were found')
    objects = output/'objects'; objects.mkdir(parents=True, exist_ok=True)
    replacements = {}; audit = []
    original_hashes = {str(o): digest(o) for o in selected}
    for index, (obj, (args, source, directory)) in enumerate(sorted(selected.items()), 1):
        new_object = objects/f'{index:03d}-{obj.name}'
        command, depfile = compile_arguments(args, source, directory, new_object, overlay, changed_sources)
        note(f'COMPILE {index}/{len(selected)} {source.name}')
        run(command, logs/f'compile-{index:03d}.log', cwd=directory, timeout=1200)
        if not new_object.is_file() or not depfile.is_file():
            raise RuntimeError('Compiler did not produce the isolated object/dependency file')
        deps_text = depfile.read_text().replace('\\\n', ' ')
        for header in changed_headers.intersection(depmap.get(obj, set())):
            patched_header = overlay/header.relative_to(source_root)
            if str(patched_header) not in deps_text:
                raise RuntimeError('Recompiled object did not consume the patched header: '+str(header))
        replacements[obj] = new_object
        audit.append({'source': str(source), 'original_object': str(obj),
                      'overlay_object': str(new_object), 'command': command})
    binary = output/target
    command = isolated_link(linking, build, replacements, binary)
    (logs/'compile-link-audit.json').write_text(json.dumps(
        {'compilations':audit, 'link_command':command, 'reused_objects':len(linked_objects)-len(selected)},indent=2)+'\n')
    note('LINK isolated executable; original objects/checkpoints remain in place')
    run(command, logs/'link.log', cwd=build, timeout=1200)
    if not binary.is_file():
        raise RuntimeError('Isolated link produced no executable')
    if any(digest(p) != value for p, value in original_hashes.items()):
        raise RuntimeError('Original compilation output changed unexpectedly')
    return binary, {'recompiled_units':len(selected), 'reused_objects':len(linked_objects)-len(selected),
                    'original_objects_unchanged':True, 'binary_sha256':digest(binary)}


def self_test():
    for tool in ('ninja', 'c++'):
        if not shutil.which(tool):
            raise RuntimeError('Required build tool missing: '+tool)
    with tempfile.TemporaryDirectory(prefix='gow87-overlay-test-') as tmp:
        root=Path(tmp); src=root/'source'; build=root/'build'; overlay=root/'overlay'; out=root/'out'; logs=root/'logs'
        for p in (src,build,overlay,out,logs): p.mkdir()
        (src/'mode.h').write_text('#pragma once\n#define VALUE 1\n')
        (src/'unit').mkdir()
        (src/'main.cpp').write_text('#include <iostream>\nint value();int main(){std::cout<<value()<<"\\n";}\n')
        # Full include path style matches shadPS4 and permits a header-only overlay.
        (src/'unit/value.cpp').write_text('#include "test/mode.h"\nint value(){return VALUE;}\n')
        (src/'test').mkdir();(src/'mode.h').rename(src/'test/mode.h')
        (build/'build.ninja').write_text(f'''rule CXX
  command = c++ -I{src} -MMD -MF $out.d -c $in -o $out
  depfile = $out.d
  deps = gcc
rule LINK
  command = : && c++ $in -o app && :
build value.o: CXX {src}/unit/value.cpp
build main.o: CXX {src}/main.cpp
build app: LINK value.o main.o
''')
        run(['ninja','app'],logs/'initial.log',cwd=build,timeout=30)
        baseline={p:digest(p) for p in (build/'app',build/'value.o',build/'main.o',build/'build.ninja')}
        (overlay/'test').mkdir();(overlay/'test/mode.h').write_text('#pragma once\n#define VALUE 2\n')
        binary,stats=overlay_build(build,src,overlay,out,logs,('test/mode.h',),target='app')
        assert read_command([binary],build).strip()=='2'
        assert read_command([build/'app'],build).strip()=='1'
        assert stats['recompiled_units']==1 and stats['reused_objects']==1
        assert all(digest(p)==h for p,h in baseline.items())
    note('SELF_TEST=PASS dependency-selected recompilation, header override, relink, unchanged original build')


def package_failure(out, result):
    (out/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    path=LOGS/(out.name+'.tar.gz')
    with tarfile.open(path,'w:gz',compresslevel=3) as t:
        for p in sorted(out.rglob('*')):
            if p.is_file() and not p.is_symlink():
                t.add(p,arcname=str(Path(out.name)/p.relative_to(out)))
    note('REPORT='+str(path))


def execute(build_only=False):
    LOGS.mkdir(parents=True,exist_ok=True)
    with (ROOT/'scripts/gow87.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        base=load_transaction()
        for path,expected in base.PINS.items():
            if digest(path)!=expected:
                raise RuntimeError('Protected baseline fingerprint changed: '+str(path))
        stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        name=f'gow87-build-{stamp}-{os.getpid()}'
        work=ROOT/'tests'/name;work.mkdir(parents=True,exist_ok=False)
        logs=LOGS/name;logs.mkdir(mode=0o700)
        result={'phase':'SOURCE','status':'STARTED','baseline_binary_sha256':BINARY_SHA}
        try:
            patch=work/'gow87.patch';fetch(PATCH_URL,patch,PATCH_SHA)
            overlay=work/'overlay';overlay.mkdir()
            before={}
            for rel in FILES:
                source=SRC/rel
                if not source.is_file() or source.is_symlink():
                    raise RuntimeError('Unsafe or missing original source: '+rel)
                target=overlay/rel;target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source,target);before[rel]=digest(source)
            if before['src/video_core/renderer_vulkan/vk_pipeline_cache.cpp']!='8b8564bbec963ac2acf9afc4b5936b65bcae210e2d35029d3c585c2c4f548976':
                raise RuntimeError('Phase84 pipeline-cache source differs from the verified merge fingerprint')
            run(['git','apply','--check',patch],logs/'patch-check.log',cwd=overlay,timeout=30)
            run(['git','apply',patch],logs/'patch-apply.log',cwd=overlay,timeout=30)
            shutil.copyfile(patch,logs/'applied.patch')
            result['source_before']=before
            result['source_after']={rel:digest(overlay/rel) for rel in FILES}
            result['phase']='COMPILE_AND_LINK'
            binary,stats=overlay_build(BUILD,SRC,overlay,work,logs,FILES)
            result['build']=stats
            if any(digest(SRC/p)!=h for p,h in before.items()):
                raise RuntimeError('Protected original source changed')
            if digest(BUILD/'shadps4')!=BINARY_SHA:
                raise RuntimeError('Original Phase84 executable changed')
            runtime=BUILD/'cpu-id-runtime'
            if not runtime.is_dir():
                raise RuntimeError('Existing CPU identity runtime unavailable')
            (work/'cpu-id-runtime').symlink_to(runtime.resolve(),target_is_directory=True)
            run(['ldd',binary],logs/'shared-libraries.log',timeout=20)
            if 'not found' in (logs/'shared-libraries.log').read_text():
                raise RuntimeError('A linked runtime library is not available')
            result['status']='BUILD_PASS'
            (work/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
            if build_only:
                package_failure(logs,result)
                return 0
            base.BINARY=binary
            base.PINS[binary]=digest(binary)
            cwd=ROOT/'runtime/gow87'
            for p in (ROOT/'runtime',cwd):
                if p.is_symlink():raise RuntimeError('Runtime path is a symlink')
                p.mkdir(parents=True,exist_ok=True)
            if (cwd/'user').exists() or (cwd/'user').is_symlink():
                raise RuntimeError('Unexpected portable profile; not changing the working profile')
            base.CWD=cwd
            original_save=base.save_json
            def with_build_proof(path,value):
                if path.name=='result.json' and path.parent.parent==LOGS:
                    value['metadata_fix_build']=stats
                    evidence=path.parent/'build';evidence.mkdir(exist_ok=True)
                    for p in logs.iterdir():
                        if p.is_file():shutil.copyfile(p,evidence/p.name)
                original_save(path,value)
            base.save_json=with_build_proof
            note('RUNNING candidate with original profile, CPU auto and reversible DMA override')
            argv=sys.argv[:]
            try:
                sys.argv=[str(Path(__file__))]
                return base.main()
            finally:
                sys.argv=argv
        except BaseException as error:
            result['status']='FAILED';result['error']=repr(error)
            (logs/'traceback.txt').write_text(traceback.format_exc())
            package_failure(logs,result)
            note('STOPPED '+repr(error))
            return 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--build-only',action='store_true')
    parser.add_argument('--restore',action='store_true')
    opts=parser.parse_args()
    if opts.self_test:
        self_test();return 0
    (ROOT/'scripts').mkdir(parents=True,exist_ok=True)
    if opts.restore:
        load_transaction();note('RESTORE=COMPLETE');return 0
    return execute(opts.build_only)


if __name__=='__main__':
    raise SystemExit(main())
