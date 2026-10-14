#!/usr/bin/env python3
"""GoT v43: retain v42 execution and capture Mesa's rejected SPIR-V and error.

No emulator build, driver patch, job-counter edit, or extra GPU-wave scan.
Uses the already cached, exact-build-ID Mesa symbols to intercept the driver's
SPIR-V error callback, plus Mesa's native failure-only binary dump facility.
The captured 0x14b258 fault is symbolized offline before any new game is started.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile

LABEL = 'got-v43-spirv-rejection-visual'
PREVIOUS_BLOB = '4266e2e906694ba4797e8244275793cd0ddf954a'
BUILD_ID = '8a5c889901316f0caf5e5d8d10be9351dac2eb9c'
DEBUG_SHA = '2adff34225eaaf278be352821daefb0046a5a1b3cd96039ac10462997e1ecb48'
DRIVER_SHA = '87908e0c6833c0f6dc86cb079cb0f34de0dee7e970da89b5303f0e6b29e326f7'
FAULT_OFFSET = 0x14B258
FAULT_BYTES = bytes.fromhex('44085040')
FRAMES = (FAULT_OFFSET, 0x12F143, 0x12F84B, 0x12FAD9, 0x12FCEB)
CALLBACK = 'radv_spirv_nir_debug'
MAX_SHADER = 4 * 1024 * 1024
MAX_DUMPS = 8


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise RuntimeError('Missing/unsafe input: ' + str(path))
    return path.read_bytes()


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError('Verified capture template differs: ' + repr(old[:80]))
    return text.replace(old, new, 1)


def command(argv: list[str], timeout: int = 60) -> str:
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, errors='replace', timeout=timeout,
                            env=dict(os.environ, LC_ALL='C', DEBUGINFOD_URLS=''))
    if result.returncode:
        raise RuntimeError(Path(argv[0]).name + ' failed: ' + result.stderr[-1200:])
    return result.stdout


def executable_segments(raw: bytes) -> list[tuple[int, int, int]]:
    if len(raw) < 64 or raw[:6] != b'\x7fELF\x02\x01':
        raise RuntimeError('Expected ELF64 little-endian driver')
    if struct.unpack_from('<HH', raw, 16) != (3, 62):
        raise RuntimeError('Expected an x86-64 shared library')
    start = struct.unpack_from('<Q', raw, 32)[0]
    entsize, count = struct.unpack_from('<HH', raw, 54)
    if entsize != 56 or not 1 <= count <= 256 or start + count * entsize > len(raw):
        raise RuntimeError('Invalid ELF program headers')
    result = []
    for i in range(count):
        typ, flags, off, va, _, size, _, _ = struct.unpack_from('<IIQQQQQQ', raw, start+i*entsize)
        if typ == 1 and flags & 1:
            if off + size > len(raw):
                raise RuntimeError('Truncated executable ELF segment')
            result.append((off, va, size))
    return result


def to_vma(raw: bytes, offset: int) -> int:
    found = [va + offset - off for off, va, size in executable_segments(raw)
             if off <= offset < off + size]
    if len(found) != 1:
        raise RuntimeError('Captured file offset has no unique executable mapping')
    return found[0]


def function_symbol(text: str, name: str) -> int:
    found = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 4 and fields[2] in ('t', 'T') and fields[3] == name:
            found.add(int(fields[0], 16))
    if len(found) != 1:
        raise RuntimeError('No unique unmodified-ABI symbol for ' + name)
    return found.pop()


def plan_path(project: Path) -> Path:
    return project / 'tools/got43-spirv-rejection/plan.json'


def prepare_plan(project: Path) -> dict:
    """Offline only: use existing exact symbols; no downloads or GPU access."""
    if os.geteuid() == 0:
        raise RuntimeError('Start as the normal user; do not sudo python')
    tools = {k: shutil.which(k) for k in ('readelf', 'nm', 'addr2line')}
    if not all(tools.values()):
        raise RuntimeError('Missing offline symbol tool; no packages installed')
    symbols = project / 'tools/radv-symbols' / (BUILD_ID + '.debug')
    driver = project / 'tools/radv-nuw-complete-25.0.7/libvulkan_radeon.so'
    raw = read_regular(driver, 160_000_000)
    if digest(raw) != DRIVER_SHA or raw[FAULT_OFFSET:FAULT_OFFSET+4] != FAULT_BYTES:
        raise RuntimeError('Private driver/fault bytes differ from the uploaded v42 capture')
    debug = read_regular(symbols, 256*1024*1024)
    if digest(debug) != DEBUG_SHA:
        raise RuntimeError('Cached Mesa symbols differ from the earlier verified symbol report')
    notes = command([tools['readelf'], '-n', str(symbols)])
    if set(re.findall(r'Build ID:\s*([0-9a-f]+)', notes)) != {BUILD_ID}:
        raise RuntimeError('Cached debug build ID mismatch')
    frames = [to_vma(raw, x) for x in FRAMES]
    resolved = command([tools['addr2line'], '-a', '-f', '-C', '-i', '-e', str(symbols)] +
                       [hex(v if i == 0 else v-1) for i, v in enumerate(frames)], 90)
    first = re.split(r'(?m)^0x[0-9a-f]+\s*$', resolved)
    if len(first) < 2 or 'radv_shader_spirv_to_nir' not in first[1]:
        raise RuntimeError('Fault resolved outside the expected translator; no game started.\n' + resolved)
    offset = function_symbol(command([tools['nm'], '-S', '-n', '--defined-only', str(symbols)], 90), CALLBACK)
    if not any(va <= offset < va+size for _, va, size in executable_segments(raw)):
        raise RuntimeError('SPIR-V callback symbol is outside executable driver segments')
    if b'MESA_SPIRV_FAIL_DUMP_PATH\0' not in raw:
        raise RuntimeError('This driver does not contain the native failure-dump option')
    plan = {'kind': 'V43_PARSER_REJECTION_CAPTURE', 'build_id': BUILD_ID,
            'debug_sha256': DEBUG_SHA, 'driver_sha256': DRIVER_SHA,
            'callback': CALLBACK, 'callback_vma': offset,
            'callback_file_bytes_16': next(raw[off+offset-va:off+offset-va+16].hex()
                for off, va, size in executable_segments(raw) if va <= offset < va+size),
            'fault_file_offset': hex(FAULT_OFFSET), 'fault_elf_vma': hex(frames[0]),
            'resolved_v42_stack': resolved, 'game_started': False,
            'emulator_rebuilt': False, 'driver_modified': False}
    folder = plan_path(project).parent
    if folder.is_symlink() or not (project/'tools').is_dir():
        raise RuntimeError('Unsafe/missing project tools directory')
    folder.mkdir(mode=0o700, exist_ok=True)
    dst = plan_path(project)
    data = (json.dumps(plan, indent=2)+'\n').encode()
    if dst.exists() or dst.is_symlink():
        if read_regular(dst, 100_000) != data:
            raise RuntimeError('A different v43 symbol plan already exists; preserving it')
    else:
        with dst.open('xb') as stream:
            stream.write(data)
    print('V42_FAULT=SYMBOLIZED_OFFLINE; PRIVATE_RADV=UNCHANGED', flush=True)
    print(resolved, flush=True)
    return plan


def load_plan(project: Path) -> dict:
    plan = json.loads(read_regular(plan_path(project), 100_000))
    if plan.get('driver_sha256') != DRIVER_SHA or plan.get('debug_sha256') != DEBUG_SHA or \
       plan.get('callback') != CALLBACK or plan.get('build_id') != BUILD_ID:
        raise RuntimeError('Unrecognized v43 symbol plan')
    offset = plan.get('callback_vma')
    if not isinstance(offset, int) or not 0 < offset < 160_000_000:
        raise RuntimeError('Invalid callback address in symbol plan')
    if not re.fullmatch('[0-9a-f]{32}', plan.get('callback_file_bytes_16', '')):
        raise RuntimeError('Invalid callback instruction fingerprint')
    return plan


# Installed after the existing private-driver gate has checked the actual mapped
# file and retained code bytes. The C callback's arguments follow x86-64 SysV.
GDB_ERROR = r'''
class SpirvRejectionCallback(gdb.Breakpoint):
    errors = 0
    def stop(self):
        level = int(gdb.parse_and_eval('$rsi')) & 0xffffffff
        if level != 2 or self.errors >= 16:
            return False
        self.errors += 1
        row = {'level': level, 'callback': 'radv_spirv_nir_debug',
               'inferior_data_modified': False, 'gameplay_verified': False}
        try:
            inf = gdb.selected_inferior()
            row['thread'] = gdb.selected_thread().name
            row['offset_bytes'] = int(gdb.parse_and_eval('$rdx'))
            ptr = int(gdb.parse_and_eval('$rcx'))
            if not 0x10000 <= ptr < (1<<47):
                raise RuntimeError('Invalid compiler-message pointer')
            data = bytearray()
            while len(data) < 8192:
                at = ptr+len(data)
                size = min(64, 8192-len(data), 4096-(at & 4095))
                part = bytes(inf.read_memory(at, size))
                end = part.find(b'\0')
                data.extend(part if end < 0 else part[:end])
                if end >= 0:
                    break
            row['message'] = data.decode('utf-8', errors='replace')
            row['message_limit_reached'] = len(data) == 8192
            row['backtrace'] = gdb.execute('bt 12', to_string=True)[:16000]
        except Exception as exc:
            row['read_error'] = str(exc)
        write('spirv-rejection-%02d.json' % self.errors, row)
        print('GOT43_SPIRV_REJECTION=CAPTURED offset='+str(row.get('offset_bytes')), flush=True)
        # Continue: Mesa itself writes the rejected binary, then the existing
        # fatal breakpoint captures the original null-result dereference.
        return False
'''


def extend_gdb(source: str, plan: dict) -> str:
    anchor = 'class RetainedRadvGate(gdb.Breakpoint):\n'
    source = once(source, anchor, GDB_ERROR + '\n' + anchor)
    marker = "            print('GOT_NUW_COMPLETE_DRIVER_LOADED=VERIFIED', flush=True)\n"
    extra = ("            got43_callback = bias + " + str(plan['callback_vma']) + "\n"
             "            if bytes(inf.read_memory(got43_callback,16)).hex() != " +
             repr(plan['callback_file_bytes_16']) + ":\n"
             "                raise RuntimeError('SPIR-V callback runtime bytes differ from symbols')\n"
             "            self.spirv_error_bp = SpirvRejectionCallback('*'+hex(got43_callback), internal=True)\n"
             "            write('spirv-callback-ready.json', {'ok':True, 'callback_vma':" +
             str(plan['callback_vma']) + ", 'driver_sha256':" + repr(DRIVER_SHA) + "})\n"
             "            print('GOT43_SPIRV_ERROR_CALLBACK=ARMED', flush=True)\n")
    source = once(source, marker, marker+extra)
    compile(source, '<got43-gdb>', 'exec')
    return source


def transform_runner(text: str) -> str:
    marker = '    export RADV_DEBUG=hang\n'
    text = once(text, marker, marker +
        '    mkdir -p -- "$report/info/rejected-spirv"\n'
        '    export MESA_SPIRV_FAIL_DUMP_PATH="$report/info/rejected-spirv"\n')
    return text


def inspect_spirv(data: bytes) -> dict:
    if len(data) < 20 or len(data) % 4 or len(data) > MAX_SHADER:
        raise ValueError('Invalid/bounded SPIR-V size')
    words = struct.unpack('<%dI' % (len(data)//4), data)
    if words[0] != 0x07230203:
        raise ValueError('Not little-endian SPIR-V')
    caps = []; entries = []; pos = 5; count = 0
    while pos < len(words):
        length, op = words[pos] >> 16, words[pos] & 0xffff
        if length == 0 or pos+length > len(words):
            raise ValueError('Invalid SPIR-V instruction framing at byte '+str(pos*4))
        if op == 17 and length == 2: caps.append(words[pos+1])
        if op == 15 and length >= 4:
            entries.append({'execution_model':words[pos+1], 'function_id':words[pos+2]})
        pos += length; count += 1
    return {'sha256':digest(data), 'bytes':len(data), 'version':hex(words[1]),
            'id_bound':words[3], 'capabilities':caps, 'entrypoints':entries,
            'instruction_count':count, 'framing_valid':True,
            'framing_does_not_establish_semantic_validity':True}


def analyze_dumps(report: Path) -> dict:
    folder = report/'info/rejected-spirv'
    result = {'rejections': [], 'gameplay_verified':False,
              'shader_skipped_or_replaced':False}
    if not folder.is_dir() or folder.is_symlink():
        result['result'] = 'NO_NATIVE_SPIRV_FAILURE_DUMP_INCONCLUSIVE'; return result
    files = sorted(folder.glob('fail-*.spirv'))
    if len(files)>MAX_DUMPS:
        result['dump_limit_exceeded']=len(files)
    for path in files[:MAX_DUMPS]:
        row={'path':path.name}
        try:
            data=read_regular(path,MAX_SHADER)
            row.update(inspect_spirv(data))
            validator=shutil.which('spirv-val')
            if validator:
                with path.with_suffix('.validation.txt').open('wb') as log:
                    proc=subprocess.run([validator,'--target-env','vulkan1.3',str(path)],
                        stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,timeout=20,
                        env=dict(os.environ,LC_ALL='C'))
                row['validator_exit']=proc.returncode
                row['validator_target_env']='vulkan1.3'
            else:
                row['validator']='NOT_INSTALLED; no package changes'
        except Exception as exc:
            row['analysis_error']=str(exc)
        result['rejections'].append(row)
    result['result']='REJECTED_SPIRV_PRESERVED' if result['rejections'] else 'NO_REJECTION_DUMP_INCONCLUSIVE'
    return result


def collect_source(project: Path, report: Path, tree: str) -> list[dict]:
    root=project/'tests'/tree
    src=root/'src/shader_recompiler'
    manifest=[]; total=0
    if not src.is_dir() or src.is_symlink(): return manifest
    for path in sorted(src.rglob('*')):
        if path.suffix not in ('.h','.cpp','.inc','.hpp','.inl') or path.is_symlink() or not path.is_file():
            continue
        if not path.resolve().is_relative_to(src.resolve()) or path.stat().st_size>800_000:
            continue
        data=path.read_bytes()
        if total+len(data)>12*1024*1024 or len(manifest)>=240: break
        total+=len(data)
        rel=path.relative_to(root)
        dest=report/'info/compiler-source'/rel
        dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(data)
        manifest.append({'path':str(rel),'sha256':digest(data)})
    return manifest


def load_previous(script: Path):
    path=script.with_name('got-v42-job-writer-capture.py')
    data=read_regular(path,100_000)
    blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
    if blob!=PREVIOUS_BLOB:
        raise RuntimeError('v42 helper changed; refusing unknown dependency')
    spec=importlib.util.spec_from_file_location('got43_verified_v42',path)
    if spec is None or spec.loader is None: raise RuntimeError('Cannot load verified v42 helper')
    prior=importlib.util.module_from_spec(spec); spec.loader.exec_module(prior)
    module=prior.load_previous(script)
    module.LABEL=LABEL
    previous_install=module.install
    def install(retained,project):
        retained=previous_install(retained,project)
        old_stack=retained.load_stack
        def load_stack(p):
            dual,base=old_stack(p)
            old_gdb,old_transform,old_gather=dual.gdb_source,base.transform,base.gather
            def gdb_source(session):
                return extend_gdb(old_gdb(session),load_plan(p))
            def transform(source,helper,session):
                return transform_runner(old_transform(source,helper,session))
            def gather(session,report):
                rc=old_gather(session,report)
                try:
                    plan=load_plan(p)
                    base.store(report/'info/spirv-rejection-plan.json',plan)
                    analysis=analyze_dumps(report)
                    errors=[]
                    for path in sorted((session/'native').glob('spirv-rejection-*.json'))[:16]:
                        if base.regular(path): errors.append(json.loads(read_regular(path,40_000)))
                    analysis['driver_error_messages']=errors
                    base.store(report/'info/spirv-rejection.json',analysis)
                    base.store(report/'info/compiler-source-manifest.json',collect_source(p,report,module.TREE))
                except Exception as exc:
                    base.store(report/'info/spirv-rejection-collection-error.json',{'error':str(exc)})
                return rc
            dual.gdb_source,base.transform,base.gather=gdb_source,transform,gather
            return dual,base
        retained.load_stack=load_stack
        return retained
    module.install=install
    return module


def main() -> int:
    os.umask(0o077)
    script=Path(__file__).resolve(); project=script.parent.parent
    if script.parent.name!='scripts' or project.name!='GhostOfTsushima':
        raise RuntimeError('Place helper in ~/projects/shadps4/GhostOfTsushima/scripts')
    args=sys.argv[1:]
    if not args or args==['prepare']:
        logs=project.parent/'logs'
        if logs.is_symlink() or not logs.is_dir(): raise RuntimeError('Project log directory missing/unsafe')
        try:
            prepare_plan(project)
        except Exception as exc:
            with tempfile.TemporaryDirectory(prefix='got43-preflight-',dir=logs) as name:
                work=Path(name)
                (work/'failure.json').write_text(json.dumps({'error':str(exc),'game_launched':False},indent=2)+'\n')
                archive=logs/(work.name+'.tar.gz')
                with tarfile.open(archive,'w:gz') as tar:
                    tar.add(work/'failure.json',arcname='failure.json')
                print('UPLOAD_OFFLINE_FAILURE='+str(archive),flush=True)
            raise
        if args==['prepare']: return 0
    return load_previous(script).main()


if __name__=='__main__':
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
    except Exception as exc:
        print('STOP: '+str(exc),file=sys.stderr); sys.exit(1)
