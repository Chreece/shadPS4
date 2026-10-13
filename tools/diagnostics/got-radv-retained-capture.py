#!/usr/bin/env python3
"""Restore retained GoT private RADV (v10+v14), verify its actual load, then capture.

No emulator build or source changes. The two exact historical binary workarounds
are confined to a private copy. Never patch /usr/lib, permissions, or other games.
The existing pinned dual-capture/UMR broker remains the hardware diagnostic path.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import sys
import tarfile
import tempfile

LABEL = 'got-radv-retained-visual'
DUAL_SHA = '05c881edffa4cfa2fca349b557eb8f5b7b6b94cf09c2d0886ed3700fc5434d8f'
SYSTEM_SHA = '7ad998b763b899649facdb4e53ac37d666dff372e84184accc0d19508e8c55e1'
RETAINED_SHA = '5b69473448ecb10d0fd1d4451cd344b9e3d677a7ab14c47045a8f26c0e4426cf'
BUILD_ID = '8a5c889901316f0caf5e5d8d10be9351dac2eb9c'
SYSTEM_DRIVER = Path('/usr/lib/x86_64-linux-gnu/libvulkan_radeon.so')
SITES = ((0x9ca84, bytes.fromhex('80 48 24 04')),
         (0x12a72c, bytes.fromhex('e8 8f c4 26 00')))


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_regular(path: Path, limit: int = 160_000_000) -> bytes:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > limit:
        raise RuntimeError('Missing/unsafe file: ' + str(path))
    return path.read_bytes()


def paths(project: Path) -> tuple[Path, Path]:
    folder = project / 'tools/radv-retained-25.0.7'
    return folder / 'libvulkan_radeon.so', folder / 'radeon_icd.json'


def validate_original(raw: bytes) -> None:
    if digest(raw) != SYSTEM_SHA:
        raise RuntimeError('Installed RADV differs from the captured exact build; nothing patched')
    if raw[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<HH', raw, 16) != (3, 62):
        raise RuntimeError('Not the captured x86-64 ELF library')
    phoff = struct.unpack_from('<Q', raw, 32)[0]
    entsize, count = struct.unpack_from('<HH', raw, 54)
    if entsize != 56 or not 1 <= count <= 100 or phoff + entsize * count > len(raw):
        raise RuntimeError('Unexpected ELF program headers')
    for address, old in SITES:
        matches = []
        for i in range(count):
            typ, flags, off, va, _, size, _, _ = struct.unpack_from('<IIQQQQQQ', raw, phoff+i*entsize)
            if typ == 1 and flags & 1 and va <= address and address + len(old) <= va + size:
                matches.append(off + address - va)
        if matches != [address] or raw[address:address+len(old)] != old:
            raise RuntimeError('Historical instruction/ELF mapping differs: ' + hex(address))
    if raw[0x9ca7c:0x9ca84] != bytes.fromhex('49 8b 01 48 8b 7c 24 18'):
        raise RuntimeError('v10 instruction context mismatch')
    if raw[0x12a731:0x12a73a] != bytes.fromhex('49 8b 7d 48 be 15 00 00 00'):
        raise RuntimeError('v14 instruction context mismatch')
    if 0x12a72c + 5 + struct.unpack_from('<i', raw, 0x12a72d)[0] != 0x396bc0:
        raise RuntimeError('v14 call target mismatch')


def reconstruct(raw: bytes) -> bytes:
    validate_original(raw)
    changed = bytearray(raw)
    for off, old in SITES:
        changed[off:off+len(old)] = b'\x90' * len(old)
    answer = bytes(changed)
    if digest(answer) != RETAINED_SHA:
        raise RuntimeError('Restored driver does not match the retained v17 SHA; refusing new variant')
    return answer


def validate_retained(raw: bytes) -> None:
    if digest(raw) != RETAINED_SHA:
        raise RuntimeError('Private driver does not match the previously retained v17 SHA')
    original = bytearray(raw)
    for off, old in SITES:
        if raw[off:off+len(old)] != b'\x90' * len(old):
            raise RuntimeError('Private driver lost the historical workaround at ' + hex(off))
        original[off:off+len(old)] = old
    validate_original(bytes(original))


def write_or_verify(path: Path, data: bytes, mode: int) -> None:
    if path.exists() or path.is_symlink():
        if read_regular(path) != data:
            raise RuntimeError('Refusing to replace different existing file: ' + str(path))
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        # Only this newly created file; no checkpoint or system-file removal.
        path.unlink(missing_ok=True)
        raise


def load_icd_template() -> dict:
    candidates = (Path('/usr/share/vulkan/icd.d/radeon_icd.x86_64.json'),
                  Path('/usr/share/vulkan/icd.d/radeon_icd.json'))
    template = next((p for p in candidates if p.is_file() and not p.is_symlink()), None)
    if template is None:
        raise RuntimeError('Installed Radeon ICD template missing')
    return json.loads(read_regular(template, 32_000))


def prepare_driver(project: Path) -> dict:
    if os.geteuid() == 0:
        raise RuntimeError('Prepare as the normal user, not sudo python')
    lib, icd = paths(project)
    for p in (project, project/'tools'):
        if not p.is_dir() or p.is_symlink():
            raise RuntimeError('Missing or unsafe project directory: ' + str(p))
    if lib.parent.is_symlink():
        raise RuntimeError('Private driver directory is a symlink')
    system = read_regular(SYSTEM_DRIVER)
    validate_original(system)
    old = project/'recovered-v17-gpu/radv-private/libvulkan_radeon.so'
    source = 'reconstructed_exact_historical_9_byte_variant'
    if old.is_file() and not old.is_symlink() and digest(read_regular(old)) == RETAINED_SHA:
        restored = read_regular(old)
        validate_retained(restored)
        source = 'reused_preserved_v17_copy'
    else:
        restored = reconstruct(system)
    lib.parent.mkdir(mode=0o700, exist_ok=True)
    write_or_verify(lib, restored, 0o700)
    validate_retained(read_regular(lib))
    if not os.access(lib, os.R_OK):
        raise RuntimeError('Private library not readable')
    config = load_icd_template()
    if not isinstance(config.get('ICD'), dict) or not isinstance(config['ICD'].get('api_version'), str):
        raise RuntimeError('Installed Radeon ICD structure unexpected')
    config['ICD']['library_path'] = str(lib)
    write_or_verify(icd, (json.dumps(config, indent=2)+'\n').encode(), 0o600)
    if digest(read_regular(SYSTEM_DRIVER)) != SYSTEM_SHA:
        raise RuntimeError('System driver changed during preparation')
    info = {'result': 'RETAINED_DRIVER_READY_NOT_YET_LOADED', 'source': source,
            'private_library': str(lib), 'private_sha256': RETAINED_SHA,
            'icd': str(icd), 'system_driver_sha256': SYSTEM_SHA, 'build_id': BUILD_ID,
            'system_driver_modified': False, 'emulator_rebuilt': False,
            'restored_historical_workarounds': [
                {'stage': 'v10', 'offset': '0x9ca84', 'bytes': 4,
                 'purpose': 'omit unsafe no-unsigned-wrap metadata write'},
                {'stage': 'v14', 'offset': '0x12a72c', 'bytes': 5,
                 'purpose': 'retain earlier first nir_opt_sink optimization bypass'}],
            'gameplay_fixed': False}
    # This is setup metadata, not a preserved user checkpoint.
    (lib.parent/'preparation.json').write_text(json.dumps(info, indent=2)+'\n')
    return info


def verify_files(project: Path) -> tuple[Path, Path]:
    lib, icd = paths(project)
    validate_retained(read_regular(lib))
    config = json.loads(read_regular(icd, 32_000))
    if config.get('ICD', {}).get('library_path') != str(lib):
        raise RuntimeError('Private ICD no longer points to the retained library')
    if digest(read_regular(SYSTEM_DRIVER)) != SYSTEM_SHA:
        raise RuntimeError('System driver changed since the captured baseline')
    return lib, icd


# GDB stops at the exported driver entry once, checks its actual mapped path and
# code bytes, then continues. A wrong driver stops here instead of being silently
# accepted as another "same baseline" run. No inferior-memory writes are made.
GDB_GATE = r'''
_driver_gate_failed = False
class RetainedRadvGate(gdb.Breakpoint):
    def stop(self):
        global _driver_gate_failed
        proof = {'result': 'DRIVER_GATE_REFUSED', 'expected_sha256': RETAINED_DRIVER_SHA,
                 'gameplay_verified': False}
        try:
            inf = gdb.selected_inferior()
            pc = int(gdb.newest_frame().pc())
            maps = Path('/proc/%d/maps' % inf.pid).read_text()
            row = None
            for line in maps.splitlines():
                fields = line.split(None, 5)
                if len(fields) != 6:
                    continue
                lo, hi = (int(x, 16) for x in fields[0].split('-'))
                if lo <= pc < hi:
                    row = (lo, int(fields[2], 16), fields[5])
                    break
            if row is None or Path(row[2]) != Path(RETAINED_DRIVER):
                raise RuntimeError('Driver entry is not mapped from the pinned private path: '+str(row))
            data = Path(row[2]).read_bytes()
            if hashlib.sha256(data).hexdigest() != RETAINED_DRIVER_SHA:
                raise RuntimeError('Loaded driver file hash changed')
            bias = row[0] - row[1]
            codes = {}
            for off, length in ((0x9ca84, 4), (0x12a72c, 5)):
                code = bytes(inf.read_memory(bias+off, length))
                if code != b'\x90' * length:
                    raise RuntimeError('Loaded code lost retained patch at '+hex(off))
                codes[hex(off)] = code.hex()
            proof.update(result='PRIVATE_RADV_LOADED_AND_CODE_VERIFIED', path=row[2],
                         sha256=RETAINED_DRIVER_SHA, runtime_bytes=codes,
                         verification_point='driver_entry_before_pipeline_creation')
            write('driver-provenance.json', proof)
            print('GOT_RETAINED_RADV_LOADED=VERIFIED', flush=True)
            self.enabled = False
            return False
        except Exception as exc:
            proof['error'] = str(exc)
            write('driver-provenance.json', proof)
            _driver_gate_failed = True
            print('GOT_RETAINED_RADV_LOADED=REFUSED '+str(exc), flush=True)
            return True
'''


def extend_gdb(dual, project: Path) -> None:
    lib, _ = paths(project)
    original = dual.gdb_source
    def gdb_source(session: Path) -> str:
        text = original(session)
        anchor = "    gdb.execute('run')\n"
        if text.count(anchor) != 1 or text.count('def dump_native():\n') != 1:
            raise RuntimeError('Pinned debugger template changed')
        text = text.replace('def dump_native():\n',
            'def dump_native():\n'
            '    if _driver_gate_failed:\n'
            "        write('stop.json', {'reason': 'DRIVER_GATE_REFUSED', 'gameplay_verified': False})\n"
            '        return False\n')
        text = text.replace(anchor,
            "    gdb.execute('set breakpoint pending on')\n"
            "    driver_gate = RetainedRadvGate('vk_icdGetInstanceProcAddr', internal=True)\n" + anchor)
        # Gate class must be defined before the try/run block executes.
        marker = "for command in ('set pagination off'"
        if text.count(marker) != 1:
            raise RuntimeError('Pinned GDB command section changed')
        text = text.replace(marker, GDB_GATE + '\n' + marker)
        return ('RETAINED_DRIVER = '+repr(str(lib))+'\nRETAINED_DRIVER_SHA = '+repr(RETAINED_SHA)+'\n'+text)
    dual.gdb_source = gdb_source


def load_stack(project: Path):
    p = Path(__file__).resolve().with_name('got-umr-dual-capture.py')
    raw = read_regular(p, 100_000)
    if digest(raw) != DUAL_SHA:
        raise RuntimeError('Pinned dual-capture helper missing/changed; no unknown backend executed')
    spec = importlib.util.spec_from_file_location('got_retained_dual', p)
    dual = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dual)
    base = dual.load_base()
    dual.install_hooks(base)
    old_transform, old_gather = base.transform, base.gather
    lib, icd = paths(project)
    def transform(source, script, session):
        text = old_transform(source, script, session)
        anchor = '    export RADV_DEBUG=hang\n'
        if text.count(anchor) != 1:
            raise RuntimeError('Pinned driver environment anchor changed')
        text = text.replace(anchor, anchor +
            '    export VK_DRIVER_FILES='+shlex.quote(str(icd))+'\n' +
            '    export VK_ICD_FILENAMES='+shlex.quote(str(icd))+'\n' +
            '    export MESA_SHADER_CACHE_DISABLE=true\n')
        text = text.replace('got-umr-dual-portable-ready-', 'got-radv-retained-portable-ready-')
        text = text.replace("echo 'UMR_TRIGGERS=PIPELINE+FATAL+RADV'",
                            "echo 'DRIVER=RETAINED_PRIVATE_RADV; LOAD_VERIFICATION=GDB_ENTRY' ")
        return text
    def gather(session, report):
        rc = old_gather(session, report)
        try:
            verify_files(project)
            after = {'private_unchanged': True, 'system_unchanged': True}
        except Exception as exc:
            after = {'error': str(exc), 'private_unchanged': False}
        proof_path = session/'native/driver-provenance.json'
        proof = json.loads(proof_path.read_text()) if base.regular(proof_path) else {'result': 'NOT_OBSERVED'}
        info = {'driver': proof, 'after': after, 'gameplay_verified': False,
                'restored_old_workarounds_not_new_gameplay_fix': True,
                'zero_captures_is_not_success': True}
        base.store(report/'info/retained-driver.json', info)
        prep = lib.parent/'preparation.json'
        if base.regular(prep):
            shutil.copyfile(prep, report/'info/driver-preparation.json')
        return rc
    base.transform, base.gather = transform, gather
    base.LABEL = LABEL
    base.__file__ = str(Path(__file__).resolve())
    extend_gdb(dual, project)
    return dual, base


def main() -> int:
    os.umask(0o077)
    script = Path(__file__).resolve()
    project = script.parent.parent
    if script.parent.name != 'scripts' or project.name != 'GhostOfTsushima':
        raise RuntimeError('Place helper in ~/projects/shadps4/GhostOfTsushima/scripts')
    args = sys.argv[1:]
    dual, base = load_stack(project)
    if args == ['status']:
        base.status(project.parent.parent.parent); return 0
    if not args or args == ['prepare']:
        home = Path.home()
        if project != home/'projects/shadps4/GhostOfTsushima':
            raise RuntimeError('Project is not under the current normal user home')
        launcher = project/'scripts'/(LABEL+'.sh')
        if base.regular(launcher) and not args:
            print('EXISTING_CAPTURE=STATUS_ONLY; NO_REPEAT_RUN')
            base.status(home); return 0
        logs = home/'projects/shadps4/logs'
        logs.mkdir(parents=True, exist_ok=True)
        try:
            info = prepare_driver(project)
            print('PRIVATE_RADV=EXACT_RETAINED_V17_HASH; SYSTEM_RADV=UNTOUCHED', flush=True)
            print('PREPARATION='+str(paths(project)[0].parent/'preparation.json'), flush=True)
            if args == ['prepare']:
                return 0
            dual.preflight_gdb(base)
            return base.main()
        except Exception as exc:
            with tempfile.TemporaryDirectory(prefix='got-radv-restore-', dir=logs) as temp:
                p = Path(temp)/'failure.json'
                p.write_text(json.dumps({'error': str(exc), 'time': base.now(),
                                        'gameplay_verified': False}, indent=2)+'\n')
                archive = logs/(Path(temp).name+'.tar.gz')
                with tarfile.open(archive, 'w:gz') as t:
                    t.add(p, arcname='failure.json')
                print('UPLOAD_PREPARATION_FAILURE='+str(archive), flush=True)
            raise
    if args[0] == 'broker' and len(args) == 3:
        return base.broker(int(args[1]), Path(args[2]))
    if args[0] == 'client' and len(args) >= 3:
        return base.client(Path(args[1]), args[2:])
    if args[0] == 'gather' and len(args) == 3:
        return base.gather(Path(args[1]), Path(args[2]))
    if args[0] == 'ping' and len(args) == 2:
        answer = base.rpc(Path(args[1]), {'op': 'ping'})
        print(json.dumps(answer)); return 0 if answer.get('ok') else 1
    if args[0] == 'supervise' and len(args) >= 4:
        lib, icd = verify_files(project)
        # Enforce the private ICD in the debugger/inferior environment too.
        os.environ.update(VK_DRIVER_FILES=str(icd), VK_ICD_FILENAMES=str(icd),
                          MESA_SHADER_CACHE_DISABLE='true')
        return dual.supervise(base, Path(args[1]), Path(args[2]), args[3:])
    raise RuntimeError('Invalid arguments')


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print('STOP: '+str(exc), file=sys.stderr)
        sys.exit(1)
