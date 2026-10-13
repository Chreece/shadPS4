#!/usr/bin/env python3
"""One build-free GoT v37 run with a private, non-halting UMR hang callback.

The temporary root broker accepts only Mesa's two documented UMR command shapes,
for this GPU. It never executes client-supplied commands. The game remains in the
existing user-systemd runner. No sudoers, debugfs permission or emulator changes.
The broker removes halt_waves and takes a bounded status-only scan. A balanced
GFXOFF inhibit covers each hardware read; no forced reset or SQ halt is issued.
"""
from __future__ import annotations
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
import time

PCI = '0000:03:00.0'
UMR_PIN = '4ec6294f3204fee024adfb4aa00555c7c8973cc5'
UMR_SHA = '6c6a33ec03091783138b31d229c368f16851f4c5cf0407befe78e8a9be3951cc'
GAME_SHA = '1a4d7fb9b69a9d2b632298d82472a44b0af81bc912b1058a5fbb3c044c998543'
LABEL = 'got-umr-live-visual'
OLD_LABEL = 'got-v37-descriptor-visual'
LAUNCHER_SHA = 'fbb1c8748595bec7abcad46a52ca41799f768c3788d05235699344d55ea0be6f'
MAX_REQUEST = 2048
MAX_REPLY = 16 * 1024 * 1024
BROKER_SECONDS = 180
DEBUG = Path('/sys/kernel/debug/dri') / PCI
# Linux x86-64 _IOWR(0x20, 0, struct containing nine u32 fields).
GPRWAVE_IOCTL = (3 << 30) | (36 << 16) | (0x20 << 8)


def sha(path: Path) -> str:
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def store(path: Path, obj: object) -> None:
    with path.open('w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2)
        f.write('\n')


def now() -> str:
    return dt.datetime.now().astimezone().isoformat()


def command_kind(args: list[str]) -> tuple[str, str]:
    """Accept exact native Mesa templates, never arbitrary flags, paths or PCI IDs."""
    if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
        raise ValueError('Invalid argument types')
    if args[:2] != ['--by-pci', PCI]:
        raise ValueError('GPU selection rejected')
    if len(args) == 4 and args[2] == '-RS':
        kind, ring = 'ring', args[3]
    elif (len(args) == 10 and args[2:6] == ['-O', 'bits,halt_waves', '-go', '0']
          and args[6] == '-wa' and args[8:] == ['-go', '1']):
        kind, ring = 'waves', args[7]
    else:
        raise ValueError('Not a permitted Mesa UMR template')
    if ring not in ('gfx', 'gfx_0.0.0'):
        raise ValueError('Not the expected graphics ring')
    return kind, ring


def umr_args(binary: Path, database: Path, kind: str, ring: str) -> list[str]:
    base = [str(binary), '--database-path', str(database), '--by-pci', PCI]
    if kind == 'waves':
        # No ring traversal, GPR reads or LLVM disassembly during the status scan.
        # PCs can be correlated with the separately retained RADV shader dump.
        return base + ['-O', 'bits,skip_gprs,no_disasm', '--waves', 'none']
    if kind == 'ring' and ring in ('gfx', 'gfx_0.0.0'):
        return base + ['-RS', ring]
    raise ValueError('Unknown capture kind')


def receive(sock: socket.socket, limit: int) -> bytes:
    data = bytearray()
    while True:
        part = sock.recv(min(65536, limit + 1 - len(data)))
        if not part:
            return bytes(data)
        data.extend(part)
        if len(data) > limit:
            raise ValueError('Socket message exceeds bound')


def rpc(session: Path, request: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(32)
        s.connect(str(session / 'broker.sock'))
        s.sendall(json.dumps(request).encode())
        s.shutdown(socket.SHUT_WR)
        return json.loads(receive(s, MAX_REPLY))


def kernel_preflight() -> dict:
    """Check the per-open selection ioctl, not an idle hardware wave scan."""
    for name in ('amdgpu_gprwave', 'amdgpu_regs2', 'amdgpu_gfxoff'):
        if not (DEBUG / name).exists():
            raise RuntimeError('Required debugfs interface missing: ' + name)
    fd = os.open(DEBUG / 'amdgpu_gprwave', os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fcntl.ioctl(fd, GPRWAVE_IOCTL, bytearray(struct.pack('<9I', *([0] * 9))), True)
    finally:
        os.close(fd)
    return {'kernel_selection_ioctl': 'ACCEPTED', 'wave_data_read': False}


def capture(binary: Path, database: Path, kind: str, ring: str, spool_dir: Path | None = None) -> dict:
    """Inhibit/release GFXOFF once; no SQ halt, even on timeout or cancellation."""
    result = {'ok': False, 'kind': kind, 'started': now(), 'halt_requested': False,
              'gfxoff_inhibit_acquired': False, 'gfxoff_inhibit_released': False}
    process = None
    fd = os.open(DEBUG / 'amdgpu_gfxoff', os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        # A zero acquires an inhibit; a paired one releases our inhibit. Do not
        # restore gfx_off_state as if it were a policy setting: it is live state.
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
        try:
            if os.pwrite(fd, struct.pack('<I', 0), 0) != 4:
                raise RuntimeError('Could not acquire GFXOFF inhibit')
            result['gfxoff_inhibit_acquired'] = True
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        command = umr_args(binary, database, kind, ring)
        result['command'] = command
        with tempfile.TemporaryFile(dir=spool_dir) as output:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                stderr=subprocess.STDOUT, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                start_new_session=True)
            try:
                code = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                code = 124
            output.seek(0)
            data = output.read(4 * 1024 * 1024 + 1)
        result.update(exit_code=code, output=data[:4 * 1024 * 1024].decode(errors='replace'),
                      output_truncated=len(data) > 4 * 1024 * 1024, ok=code == 0)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                result['child_cleanup_error'] = 'UMR did not exit after SIGKILL'
        try:
            if result['gfxoff_inhibit_acquired']:
                result['gfxoff_inhibit_released'] = os.pwrite(fd, struct.pack('<I', 1), 0) == 4
                if not result['gfxoff_inhibit_released']:
                    result['ok'] = False
        finally:
            os.close(fd)
    result['finished'] = now()
    return result


def broker(uid: int, session: Path) -> int:
    if os.geteuid() != 0 or uid <= 0:
        raise RuntimeError('Broker needs root and a non-root owner')
    home = Path(pwd.getpwuid(uid).pw_dir)
    project = home / 'projects/shadps4/GhostOfTsushima'
    if session.parent != project / 'tools' or not session.name.startswith('umr-session-'):
        raise RuntimeError('Invalid session location')
    if session.is_symlink() or session.stat().st_uid != uid or session.stat().st_mode & 0o077:
        raise RuntimeError('Session directory must be private and owned by the caller')
    binary = project / 'tools/umr/build/src/app/umr'
    database = project / 'tools/umr/database'
    if not regular(binary) or sha(binary) != UMR_SHA:
        raise RuntimeError('UMR executable changed')
    readiness = {'ok': True, **kernel_preflight(), 'halt_requested': False,
                 'umr_sha256': UMR_SHA, 'broker_started': now()}
    def cancelled(_signum, _frame):
        raise KeyboardInterrupt('Broker cancelled')
    signal.signal(signal.SIGTERM, cancelled)
    seen: set[str] = set()
    captures = []
    endpoint = session / 'broker.sock'
    start = time.monotonic()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(endpoint))
        os.chown(endpoint, uid, pwd.getpwuid(uid).pw_gid)
        os.chmod(endpoint, 0o600)
        server.listen(3)
        server.settimeout(1)
        try:
            while time.monotonic() - start < BROKER_SECONDS:
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                stop = False
                with conn:
                    conn.settimeout(25)
                    try:
                        _pid, peer_uid, _gid = struct.unpack('3i', conn.getsockopt(
                            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                        if peer_uid != uid:
                            raise PermissionError('Socket peer rejected')
                        request = json.loads(receive(conn, MAX_REQUEST))
                        if request == {'op': 'ping'}:
                            answer = readiness
                        elif request == {'op': 'finish'}:
                            answer = {'ok': True, 'captures': captures, 'finished': now()}
                            stop = True
                        elif isinstance(request, dict) and set(request) == {'op', 'args'} and request['op'] == 'umr':
                            kind, ring = command_kind(request['args'])
                            if kind in seen:
                                raise ValueError('Capture already attempted; no repeat')
                            if sha(binary) != UMR_SHA:
                                raise ValueError('UMR executable changed')
                            seen.add(kind)
                            answer = capture(binary, database, kind, ring, session)
                            captures.append({k: v for k, v in answer.items() if k != 'output'})
                        else:
                            raise ValueError('Operation rejected')
                    except Exception as exc:
                        answer = {'ok': False, 'error': str(exc)}
                    try:
                        conn.sendall(json.dumps(answer).encode())
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                if stop:
                    break
        finally:
            endpoint.unlink(missing_ok=True)
    return 0


def client(session: Path, args: list[str]) -> int:
    kind, _ = command_kind(args)
    answer = rpc(session, {'op': 'umr', 'args': args})
    store(session / (kind + '.json'), answer)
    print('GOT_UMR_NATIVE_CALLBACK=' + kind.upper(), flush=True)
    print('HALT_REQUESTED=NO; GPRS=SKIPPED; CAPTURE_IS_NOT_A_FIXED_GAME', flush=True)
    print(answer.get('output', answer.get('error', 'No output')), flush=True)
    return 0 if answer.get('ok') else 1


def gather(session: Path, report: Path) -> int:
    try:
        store(session / 'broker-result.json', rpc(session, {'op': 'finish'}))
    except Exception as exc:
        store(session / 'broker-result.json', {'ok': False, 'error': str(exc)})
    project = session.parent.parent
    checkpoints = {}
    for key, path, expected in (
        ('umr', project / 'tools/umr/build/src/app/umr', UMR_SHA),
        ('v37', project / 'tests/got-v37-guest-image-safety-20261013/build/shadps4', GAME_SHA),
        ('v37_launcher', project / 'scripts' / (OLD_LABEL + '.sh'), LAUNCHER_SHA)):
        checkpoints[key] = regular(path) and sha(path) == expected
    store(session / 'checkpoint-after.json', checkpoints)
    (report / 'logs').mkdir(parents=True, exist_ok=True)
    for name in ('preflight.json', 'broker-result.json', 'waves.json', 'ring.json', 'checkpoint-after.json'):
        p = session / name
        if regular(p) and p.stat().st_size <= MAX_REPLY:
            shutil.copyfile(p, report / 'logs' / ('umr-' + name))
    return 0


def transform(source: str, script: Path, session: Path) -> str:
    """Unique replacements of the real uploaded runner, not guessed shell code."""
    def replace_once(old: str, new: str) -> None:
        nonlocal source
        if source.count(old) != 1:
            raise ValueError('Runner mismatch at ' + repr(old[:95]))
        source = source.replace(old, new)
    if OLD_LABEL not in source or GAME_SHA not in source:
        raise ValueError('Wrong source launcher')
    replace_once('    export RADV_DEBUG=hang,noumr\n',
                 '    export RADV_DEBUG=hang\n'
                 '    export PATH=' + shlex.quote(str(session / 'bin')) + ':"$PATH"\n')
    replace_once('    # RADV defaults to $HOME/radv_dumps_...;',
                 '    /usr/bin/python3 ' + shlex.quote(str(script)) + ' gather ' +
                 shlex.quote(str(session)) + ' "$report" || :\n'
                 '    # RADV defaults to $HOME/radv_dumps_...;')
    replace_once('  check_ready "$ready"\n  [[ -s "$GAME" ]]',
                 '  check_ready "$ready"\n'
                 '  /usr/bin/python3 ' + shlex.quote(str(script)) + ' ping ' +
                 shlex.quote(str(session)) + ' > "$report/info/umr-readiness.json" || exit 2\n'
                 '  [[ -s "$GAME" ]]')
    source = source.replace(OLD_LABEL, LABEL)
    source = source.replace('got-v31-portable-ready-', 'got-umr-live-portable-ready-')
    source = source.replace("echo \"GAME_STARTS_IN=20s\"", "echo 'UMR_CALLBACK=ARMED'")
    source = source.replace("echo \"GAMEPLAY_LIMIT=90s\"", "echo 'GAMEPLAY_LIMIT=75s'")
    source = source.replace("printf 'AFTER_3_MINUTES=bash %q status\\n'", "printf 'GET_REPORT=bash %q status\\n'")
    return source


def status(home: Path) -> None:
    p = home / 'projects/shadps4/GhostOfTsushima/scripts' / (LABEL + '.sh')
    if regular(p):
        subprocess.run(['/usr/bin/bash', str(p), 'status'], check=False)
    else:
        print('No live UMR capture has been prepared.')


def main() -> int:
    os.umask(0o077)
    home = Path.home()
    if os.geteuid() == 0:
        raise RuntimeError('Start as your normal user, not sudo python')
    project = home / 'projects/shadps4/GhostOfTsushima'
    scripts = project / 'scripts'
    script = Path(__file__).resolve()
    old = scripts / (OLD_LABEL + '.sh')
    new = scripts / (LABEL + '.sh')
    if regular(new):
        print('EXISTING_CAPTURE=STATUS_ONLY; NO_NEW_GAME_RUN')
        status(home)
        return 0
    if new.exists() or new.is_symlink() or script.parent != scripts:
        raise RuntimeError('Install this helper in the project scripts directory')
    for cmd in ('sudo', 'systemd-run', 'systemctl', 'bash', 'git', 'xdotool', 'xwininfo', 'import'):
        if shutil.which(cmd) is None:
            raise RuntimeError('Missing tool: ' + cmd)
    binary = project / 'tools/umr/build/src/app/umr'
    game = project / 'tests/got-v37-guest-image-safety-20261013/build/shadps4'
    for p, digest in ((binary, UMR_SHA), (game, GAME_SHA)):
        if not regular(p) or sha(p) != digest:
            raise RuntimeError('Preserved executable mismatch: ' + str(p))
    if not regular(old) or sha(old) != LAUNCHER_SHA:
        raise RuntimeError('Preserved v37 screenshot runner differs from the uploaded source')
    source_root = project / 'tools/umr'
    commit = subprocess.check_output(['git', '-C', str(source_root), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != UMR_PIN:
        raise RuntimeError('UMR source revision changed')
    subprocess.run(['git', '-C', str(source_root), 'diff', '--quiet', 'HEAD', '--', 'src', 'database'], check=True)
    subprocess.run(['systemctl', '--user', 'show-environment'], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['sudo', '-n', 'true'], check=True)
    session = Path(tempfile.mkdtemp(prefix='umr-session-', dir=project / 'tools'))
    unit = 'got-umr-broker-' + session.name.removeprefix('umr-session-')
    (session / 'bin').mkdir()
    new_source = transform(old.read_text(), script, session)
    check = subprocess.run(['/usr/bin/bash', '-n'], input=new_source, text=True, capture_output=True)
    if check.returncode:
        raise RuntimeError('Generated runner syntax failed: ' + check.stderr)
    shim = ('#!/usr/bin/python3\nimport os,sys\nos.execv("/usr/bin/python3", '
            '["/usr/bin/python3", ' + repr(str(script)) + ', "client", ' +
            repr(str(session)) + '] + sys.argv[1:])\n')
    compile(shim, '<umr shim>', 'exec')
    (session / 'bin/umr').write_text(shim)
    (session / 'bin/umr').chmod(0o700)
    store(session / 'setup.json', {'created': now(), 'root_unit': unit, 'launcher': str(new),
          'old_launcher_sha256': sha(old), 'umr_sha256': UMR_SHA, 'game_sha256': GAME_SHA})
    # Only this finite broker runs as root. No service files or sudoers entries.
    try:
        subprocess.run(['sudo', '-n', 'systemd-run', '--quiet', '--collect', '--service-type=exec',
            '--unit=' + unit, '--property=RuntimeMaxSec=190s', '--property=TimeoutStopSec=10s',
            '--property=KillMode=mixed', '--property=UMask=0077', '/usr/bin/python3', str(script),
            'broker', str(os.getuid()), str(session)], check=True)
        answer = None
        for _ in range(100):
            if (session / 'broker.sock').exists():
                answer = rpc(session, {'op': 'ping'})
                break
            time.sleep(0.1)
        if not answer or not answer.get('ok'):
            raise RuntimeError('Privileged broker did not pass preflight; no game started')
        store(session / 'preflight.json', answer)
        with new.open('x') as f:
            f.write(new_source)
        new.chmod(0o700)
        print('UMR_KERNEL_IOCTL=ACCEPTED; GAME=v37_UNCHANGED; HALT_REQUESTED=NO', flush=True)
        launched = subprocess.run(['/usr/bin/bash', str(new), 'start'], check=False)
        if launched.returncode:
            raise RuntimeError('Game launcher declined; see its output')
        print('GET_REPORT=bash ' + str(new) + ' status', flush=True)
        print('SESSION=' + str(session), flush=True)
        return 0
    except Exception as exc:
        try:
            rpc(session, {'op': 'finish'})
        except Exception:
            pass
        store(session / 'setup-error.json', {'error': str(exc), 'time': now(), 'root_unit': unit})
        try:
            journal = subprocess.run(['sudo', '-n', 'journalctl', '-u', unit, '--no-pager', '-n', '60'],
                                     capture_output=True, text=True, timeout=8)
            (session / 'broker-journal.txt').write_text(journal.stdout + journal.stderr)
        except Exception:
            pass
        archive = home / 'projects/shadps4/logs' / ('got-umr-setup-' + session.name + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar:
            for name in ('setup.json', 'setup-error.json', 'preflight.json', 'broker-journal.txt'):
                item = session / name
                if regular(item):
                    tar.add(item, arcname=name, recursive=False)
        print('UPLOAD_SETUP_FAILURE=' + str(archive), flush=True)
        raise


if __name__ == '__main__':
    try:
        args = sys.argv[1:]
        if args and args[0] == 'broker' and len(args) == 3:
            code = broker(int(args[1]), Path(args[2]))
        elif args and args[0] == 'client' and len(args) >= 3:
            code = client(Path(args[1]), args[2:])
        elif args and args[0] == 'gather' and len(args) == 3:
            code = gather(Path(args[1]), Path(args[2]))
        elif args and args[0] == 'ping' and len(args) == 2:
            answer = rpc(Path(args[1]), {'op': 'ping'})
            print(json.dumps(answer))
            code = 0 if answer.get('ok') else 1
        elif args == ['status']:
            status(Path.home()); code = 0
        elif not args:
            code = main()
        else:
            raise RuntimeError('Invalid helper arguments')
        sys.exit(code)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
        sys.exit(1)
