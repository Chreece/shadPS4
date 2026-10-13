#!/usr/bin/env python3
"""Restore retained GoT private RADV (v10+v14), verify its actual load, then capture.

No emulator build or source changes. The two exact historical binary workarounds
are confined to a private copy. Never patch /usr/lib, permissions, or other games.
The existing pinned dual-capture/UMR broker remains the hardware diagnostic path.
"""
from __future__ import annotations
import fcntl
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import stat
import struct
import subprocess
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


def capture_recorded(project: Path) -> bool:
    """A created shell script is NOT evidence that its game was scheduled."""
    pointer = project.parent / 'logs' / ('.' + LABEL + '-latest')
    if not pointer.exists() and not pointer.is_symlink():
        return False
    data = read_regular(pointer, 8192).decode('utf-8')
    values = dict(line.split('=', 1) for line in data.splitlines() if '=' in line)
    unit, report = values.get('unit', ''), Path(values.get('report', ''))
    if (not re.fullmatch(re.escape(LABEL) + r'-[0-9]{8}-[0-9]{6}', unit)
            or report.parent != project.parent / 'logs'
            or not report.name.startswith(unit + '-')):
        raise RuntimeError('Capture pointer is incomplete or changed; preserving all state')
    return True


def running_emulators(proc: Path = Path('/proc'), uid: int | None = None) -> list[dict]:
    """Find only positively identified shadPS4 processes, without disturbing others.

    A restricted /proc/<pid>/stat or exe is common on headless Linux systems.
    In that case try comm/cmdline; unrelated inaccessible processes must not
    prevent launching the capture. Unknown candidate shadPS4 processes fail
    closed rather than assuming the GPU is idle.
    """
    owner = os.getuid() if uid is None else uid
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != owner:
                continue
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        comm = ''
        try:
            comm = (entry / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            pass
        try:
            raw = (entry / 'cmdline').read_bytes()
            argv = raw.split(b'\\0')
            cmd0 = os.fsdecode(argv[0]) if argv and argv[0] else ''
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            cmd0 = ''
        exe = ''
        try:
            exe = os.readlink(entry / 'exe').removesuffix(' (deleted)')
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass
        candidate = (comm.lower().startswith('shadps4') or
                     Path(cmd0).name.lower().startswith('shadps4') or
                     Path(exe).name.lower().startswith('shadps4'))
        if not candidate:
            continue
        try:
            stat_text = (entry / 'stat').read_text()
            fields = stat_text.rsplit(') ', 1)
            state = fields[1].split()[0] if len(fields) == 2 else '?'
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (PermissionError, IndexError):
            state = '?'
        if state in ('Z', 'X'):
            continue
        found.append({'pid': int(entry.name), 'name': comm or Path(cmd0).name,
                      'executable': exe or cmd0, 'state': state})
    return sorted(found, key=lambda item: item['pid'])


def show_busy(games: list[dict]) -> None:
    print('CAPTURE=BLOCKED_OTHER_SHADPS4; NO_GAME_STARTED; NO_GPU_PROBE', flush=True)
    for game in games:
        print('EXISTING_GAME_PID=%s NAME=%s EXE=%s' %
              (game['pid'], game['name'], game['executable']), flush=True)
    print('EXISTING_GAME_UNTOUCHED; CLOSE_IT_NORMALLY_THEN_RERUN_THIS_HELPER', flush=True)


def active_capture_units() -> list[str]:
    """Do not rotate a launcher used by a timer, including its startup delay."""
    command = ['systemctl', '--user', 'list-units', '--all', '--plain',
               '--no-legend', '--no-pager', LABEL + '-*.timer', LABEL + '-*.service']
    result = subprocess.run(command, capture_output=True, text=True, timeout=10,
                            env=dict(os.environ, LC_ALL='C', SYSTEMD_COLORS='0'))
    if result.returncode:
        raise RuntimeError('Cannot check capture timers; preserving launcher: ' + result.stderr[-400:])
    active = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) < 4 or not fields[0].startswith(LABEL + '-'):
            raise RuntimeError('Unexpected systemd unit listing; preserving launcher')
        if fields[2] not in ('inactive', 'failed'):
            active.append(fields[0])
    return active


def broker_is_inactive(unit: str) -> bool:
    result = subprocess.run(['systemctl', 'show', unit + '.service', '--no-pager',
                             '--property=LoadState', '--property=ActiveState'],
                            capture_output=True, text=True, timeout=10,
                            env=dict(os.environ, LC_ALL='C', SYSTEMD_COLORS='0'))
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode not in (0, 4) or 'LoadState' not in values or 'ActiveState' not in values:
        raise RuntimeError('Cannot establish previous broker state; nothing replaced')
    return values['ActiveState'] in ('inactive', 'failed')


def archive_unscheduled_launcher(project: Path, base, script: Path) -> None:
    """Recover ONLY an exact launcher from a recorded failed, unscheduled setup.

    Preserve it and its failure evidence. A scheduled capture, unknown launcher,
    active broker or active timer is never overwritten or stopped.
    """
    launcher = project / 'scripts' / (LABEL + '.sh')
    if not launcher.exists() and not launcher.is_symlink():
        return
    contents = read_regular(launcher, 100_000)
    old = project / 'scripts' / (base.OLD_LABEL + '.sh')
    source = read_regular(old, 100_000)
    if digest(source) != base.LAUNCHER_SHA:
        raise RuntimeError('Original v37 launcher changed; no retry reconstruction')
    matches = []
    for session in sorted((project / 'tools').glob('umr-session-*')):
        if session.is_symlink() or not session.is_dir():
            continue
        if not re.fullmatch(r'umr-session-[a-z0-9_]+', session.name):
            continue
        setup_path, error_path = session / 'setup.json', session / 'setup-error.json'
        if not setup_path.exists() or not error_path.exists():
            continue
        setup = json.loads(read_regular(setup_path, 32_000))
        error = json.loads(read_regular(error_path, 32_000))
        unit = 'got-umr-broker-' + session.name.removeprefix('umr-session-')
        if (setup.get('launcher') != str(launcher) or setup.get('root_unit') != unit
                or error.get('root_unit') != unit
                or error.get('error') != 'Game launcher declined; see its output'):
            continue
        if (setup.get('old_launcher_sha256') != base.LAUNCHER_SHA
                or setup.get('umr_sha256') != base.UMR_SHA
                or setup.get('game_sha256') != base.GAME_SHA):
            continue
        expected = base.transform(source.decode('utf-8'), script, session).encode('utf-8')
        if contents == expected:
            matches.append((session, unit))
    if len(matches) != 1:
        raise RuntimeError('Launcher is not a uniquely verified failed setup; nothing overwritten')
    session, unit = matches[0]
    if not broker_is_inactive(unit):
        raise RuntimeError('Previous finite broker is still exiting; retry later; no process stopped')
    if capture_recorded(project) or active_capture_units():
        raise RuntimeError('Capture became scheduled/active; retry recovery cancelled')
    # Only our setup processes share this lock; a manually started game is left
    # alone by the later launcher and worker checks as well.
    if running_emulators():
        raise RuntimeError('A game started during retry preparation; nothing replaced')
    if read_regular(launcher, 100_000) != contents:
        raise RuntimeError('Launcher changed during verification; preserving it')
    folder = Path(tempfile.mkdtemp(prefix='got-radv-unscheduled-', dir=project.parent / 'logs'))
    proof = {'reason': 'launcher_created_but_game_not_scheduled', 'session': str(session),
             'launcher_sha256': digest(contents), 'broker_unit': unit,
             'prior_capture_restarted': False, 'existing_games_signalled': False}
    (folder / 'retry-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
    for name in ('setup.json', 'setup-error.json'):
        write_or_verify(folder / name, read_regular(session / name, 32_000), 0o600)
    # Keep the old launcher in the report directory. Do not delete a checkpoint.
    launcher.rename(folder / launcher.name)
    print('UNSCHEDULED_LAUNCHER=ARCHIVED; FRESH_SESSION_ALLOWED', flush=True)
    print('PREVIOUS_SETUP_PRESERVED=' + str(folder), flush=True)


def run_setup(project: Path, script: Path, dual, base, prepare_only: bool = False) -> int:
    home = Path.home()
    if os.geteuid() == 0 or project != home / 'projects/shadps4/GhostOfTsushima':
        raise RuntimeError('Run from the normal user project, not sudo python')
    lockpath = project / 'scripts' / ('.' + LABEL + '-setup.lock')
    fd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise RuntimeError('Unsafe setup lock file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('CAPTURE=SETUP_ALREADY_IN_PROGRESS; NO_SECOND_START', flush=True)
            return 0
        if not prepare_only:
            if capture_recorded(project):
                print('EXISTING_CAPTURE=RECORDED_STATUS_ONLY; NO_REPEAT_RUN', flush=True)
                base.status(home)
                return 0
            games = running_emulators()
            if games:
                show_busy(games)
                return 0
            units = active_capture_units()
            if units:
                print('CAPTURE=EXISTING_UNIT_ACTIVE; NO_RESTART; UNITS=' + ','.join(units), flush=True)
                return 0
            archive_unscheduled_launcher(project, base, script)
        logs = project.parent / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        try:
            prepare_driver(project)
            print('PRIVATE_RADV=EXACT_RETAINED_V17_HASH; SYSTEM_RADV=UNTOUCHED', flush=True)
            print('PREPARATION=' + str(paths(project)[0].parent / 'preparation.json'), flush=True)
            if prepare_only:
                return 0
            dual.preflight_gdb(base)
            # The base still checks for another emulator at launch and again in
            # its worker. A late refusal remains retryable using setup-error.json.
            return base.main()
        except Exception as exc:
            with tempfile.TemporaryDirectory(prefix='got-radv-restore-', dir=logs) as temp:
                p = Path(temp) / 'failure.json'
                p.write_text(json.dumps({'error': str(exc), 'time': base.now(),
                                        'gameplay_verified': False}, indent=2) + '\n')
                archive = logs / (Path(temp).name + '.tar.gz')
                with tarfile.open(archive, 'w:gz') as t:
                    t.add(p, arcname='failure.json')
                print('UPLOAD_PREPARATION_FAILURE=' + str(archive), flush=True)
            raise
    finally:
        os.close(fd)


def main() -> int:
    os.umask(0o077)
    script = Path(__file__).resolve()
    project = script.parent.parent
    if script.parent.name != 'scripts' or project.name != 'GhostOfTsushima':
        raise RuntimeError('Place helper in ~/projects/shadps4/GhostOfTsushima/scripts')
    args = sys.argv[1:]
    dual, base = load_stack(project)
    if args == ['status']:
        if capture_recorded(project):
            base.status(project.parent.parent.parent)
        else:
            print('CAPTURE=NOT_SCHEDULED; RERUN_WITHOUT_STATUS_TO_RETRY')
        return 0
    if not args or args == ['prepare']:
        return run_setup(project, script, dual, base, args == ['prepare'])
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
