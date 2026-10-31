#!/usr/bin/env bash
# Correct the *custom host Sunshine supervisor*, not Vulkan or AMDGPU recovery.
# Read-only root evidence, exact source SHA, backup, AST-checked patch, rollback.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
TAG="gds-pr117-live-xorg-guard-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
OUT="$LOGS/$TAG"
ARCHIVE="$LOGS/$TAG.tar.gz"
SERVICE="sunshine-session-supervisor.service"
LIVE="/usr/local/sbin/sunshine-session-supervisor"
EXPECTED="7c1c0a2e1015664ab71bb914786fc18becaecf88dbc917bb8093986661e5c815"
STATE=PRECHECK
MODIFIED=NO
mkdir -p "$OUT"
finish() {
    rc=$?
    trap - EXIT
    set +e
    printf 'STATE=%s\nEXIT_CODE=%s\nMODIFIED=%s\n' "$STATE" "$rc" "$MODIFIED" >"$OUT/result.txt"
    printf 'XORG_SUNSHINE_NETWORK_SSH_RESTARTS=NONE_BY_SCRIPT\nGPU_RESET_WATCH=UNMODIFIED\nGAME_LAUNCHED=NO\n' >>"$OUT/result.txt"
    tar --exclude='__pycache__' --exclude='*.patched.py' -C "$LOGS" -czf "$ARCHIVE" "$TAG" 2>"$OUT/tar-errors.txt"
    echo "STATUS=$STATE"
    echo "UPLOAD_THIS=$ARCHIVE"
    [[ ! -f "$OUT/rollback.txt" ]] || cat "$OUT/rollback.txt"
    echo 'SSH_SESSION=OPEN'
}
trap finish EXIT
for cmd in sudo python3 sha256sum systemctl journalctl grep pgrep tar; do
    command -v "$cmd" >/dev/null || { echo "MISSING=$cmd"; exit 2; }
done
sudo -v || { echo 'SUDO_AUTH_NEEDED; NO_CHANGES'; exit 3; }

STATE=VERIFY_EXACT_HOST_SUPERVISOR
sudo test -f "$LIVE" || { echo 'SUPERVISOR_SCRIPT_MISSING'; exit 3; }
[[ "$(sudo sha256sum "$LIVE" | awk '{print $1}')" == "$EXPECTED" ]] || {
    echo 'SUPERVISOR_POLICY_CHANGED; DO_NOT_PATCH_UNKNOWN_VERSION'
    sudo sha256sum "$LIVE"
    exit 3
}
sudo systemctl show "$SERVICE" -p Id -p ActiveState -p MainPID -p ExecStart \
    -p ExecStop -p ExecStopPost -p PartOf -p BindsTo >"$OUT/supervisor-service.txt"
grep -Fq "$LIVE" "$OUT/supervisor-service.txt" || {
    echo 'RUNNING_SERVICE_POINTS_ELSEWHERE'; exit 3;
}
python3 - "$OUT/supervisor-service.txt" <<'PYUNIT'
from pathlib import Path
import sys
p=Path(sys.argv[1]); values=dict(x.split('=',1) for x in p.read_text().splitlines() if '=' in x)
if values.get('ActiveState')!='active': raise SystemExit('SUPERVISOR_NOT_ACTIVE')
for name in ('ExecStop','ExecStopPost','PartOf','BindsTo'):
    v=values.get(name,'').strip()
    if v and (name in ('ExecStop','ExecStopPost') or 'sunshine.service' in v or 'headless-x.service' in v):
        raise SystemExit('SUPERVISOR_HAS_UNVERIFIED_STOP_DEPENDENCY='+name)
print('ISOLATED_SUPERVISOR_CONTROLLER=PASS')
PYUNIT
sudo systemctl show sunshine.service -p ActiveState -p MainPID >"$OUT/sunshine-before.txt"
sudo systemctl show sunshine-amdgpu-reset-watch.service -p ActiveState -p MainPID >"$OUT/gpu-watch-before.txt"
pgrep -ax Xorg >"$OUT/xorg-before.txt" || true
pgrep -ax sunshine >"$OUT/sunshine-pids-before.txt" || true

STATE=COLLECT_ROOT_JOURNAL_AND_INCIDENT_REASON
sudo env TZ=UTC journalctl -b -u "$SERVICE" --since '2026-10-31 07:06:30' \
    --until '2026-10-31 07:11:00' --no-pager --output=short-iso \
    >"$OUT/supervisor-journal.txt" 2>"$OUT/supervisor-journal-err.txt" || true
sudo env TZ=UTC journalctl -b -u sunshine-amdgpu-recover.service \
    -u sunshine-amdgpu-reset-watch.service -u sunshine.service \
    --since '2026-10-31 07:08:00' --until '2026-10-31 07:11:00' \
    --no-pager --output=short-iso >"$OUT/gpu-recovery-units.txt" 2>"$OUT/gpu-units-err.txt" || true
sudo python3 - "$OUT/recovery-incidents.txt" <<'PYINC'
from pathlib import Path
from datetime import datetime,timezone
import sys
base=Path('/home/chreece/.local/state/sunshine-session-supervisor')
a=datetime(2026,10,31,7,6,tzinfo=timezone.utc).timestamp()
b=datetime(2026,10,31,7,12,tzinfo=timezone.utc).timestamp()
lines=[]
for p in base.glob('incident-*.log'):
    try:
        if not p.is_file() or p.is_symlink() or not a<=p.stat().st_mtime<=b: continue
        selected=[x[:400] for x in p.read_text(errors='replace').splitlines()[:8]
                  if x.startswith(('time=','reason='))]
        lines.append('incident='+p.name+'\n'+'\n'.join(selected))
    except OSError: continue
Path(sys.argv[1]).write_text('\n\n'.join(lines) or 'NO_MATCHING_INCIDENT_LOGS\n')
PYINC
sudo chown "$(id -u):$(id -g)" "$OUT/recovery-incidents.txt"
grep -Ei 'RECOVERY BEGIN|display health failure|RECOVERY END' \
    "$OUT/supervisor-journal.txt" | tail -n 70 >"$OUT/recovery-decisions.txt" || true

STATE=GENERATE_AND_VALIDATE_PRECISE_GUARD
PATCHED="$OUT/sunshine-session-supervisor.patched.py"
BACKUP="/var/backups/sunshine-session-supervisor-$TAG.orig.py"
sudo python3 - "$LIVE" "$EXPECTED" "$PATCHED" <<'PYPATCH'
import ast,hashlib,sys
from pathlib import Path
src,expected,dest=sys.argv[1:]
raw=Path(src).read_bytes()
if hashlib.sha256(raw).hexdigest()!=expected: raise SystemExit('SOURCE_CHANGED_DURING_INSPECTION')
s=raw.decode('utf-8')
if 'PR117_LIVE_XORG_GUARD_V1' in s: raise SystemExit('GUARD_ALREADY_PRESENT')
helper='''# PR117_LIVE_XORG_GUARD_V1: xrandr timeout is not Xorg death.
# Retain the separate kernel AMDGPU-reset recovery path unchanged.
def pr117_live_xorg_pid():
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdecimal():
                continue
            try:
                if (proc / "comm").read_text().strip() != "Xorg":
                    continue
                if b":0" in (proc / "cmdline").read_bytes().split(b"\\0"):
                    return int(proc.name)
            except (OSError, ValueError):
                continue
    except OSError:
        return -1  # Unknown state: fail closed and preserve session.
    # Stale X11 socket files must not suppress legitimate recovery.
    # A connectable X11 socket is evidence of a currently running server.
    try:
        import socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.4)
            client.connect("/tmp/.X11-unix/X0")
            return -1
    except (FileNotFoundError, ConnectionRefusedError):
        return 0
    except OSError:
        return -1  # Timed-out/ambiguous socket: preserve possibly live Xorg.


'''
a='def recover(reason):\n'
b='    RECOVERY_LOCK.parent.mkdir(parents=True, exist_ok=True)\n'
c='        incident(reason)\n        terminate_sunshine_children(f"display recovery: {reason}")\n'
replacement='''    live = pr117_live_xorg_pid()
    if live:
        log(f"PR117_LIVE_XORG_GUARD: defer display recovery, pid={live}; "
            f"reason={reason}; leave Sunshine/Xorg running")
        return
    RECOVERY_LOCK.parent.mkdir(parents=True, exist_ok=True)
'''
second='''        incident(reason)
        live = pr117_live_xorg_pid()
        if live:
            log(f"PR117_LIVE_XORG_GUARD: deferred after incident, pid={live}")
            return
        terminate_sunshine_children(f"display recovery: {reason}")
'''
for old,new in ((a,helper+a),(b,replacement),(c,second)):
    if s.count(old)!=1: raise SystemExit('UNKNOWN_SUPERVISOR_PREIMAGE='+old[:50])
    s=s.replace(old,new,1)
compile(s,src,'exec')
tree=ast.parse(s)
fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='recover')
if s.count('PR117_LIVE_XORG_GUARD:')!=2: raise SystemExit('TWO_RECOVERY_GUARDS_REQUIRED')
logs=[]
ns={'amdgpu_recovery_active':lambda:False,'pr117_live_xorg_pid':lambda:2237705,'log':logs.append}
exec(compile(ast.Module(body=[fn],type_ignores=[]),'<pure-test>','exec'),ns)
ns['recover']('xrandr timeout')
if not logs or 'defer display recovery' not in logs[0]: raise SystemExit('ACTIVE_XORG_GUARD_TEST_FAILED')
Path(dest).write_text(s)
print('LIVE_XORG_RECOVERY_GUARD_UNIT_TEST=PASS')
print('NEW_SOURCE_SHA256='+hashlib.sha256(s.encode()).hexdigest())
PYPATCH
sudo python3 -m py_compile "$PATCHED"
sudo install -d -m 0700 /var/backups
sudo install -m 0600 "$LIVE" "$BACKUP"
( echo "ROLLBACK_SOURCE=$BACKUP"; echo "ORIGINAL_SHA256=$EXPECTED" ) >"$OUT/rollback.txt"
[[ "$(sudo sha256sum "$LIVE" | awk '{print $1}')" == "$EXPECTED" ]] || {
    echo 'SUPERVISOR_EDITED_CONCURRENTLY; ABORT'; exit 4;
}
sudo install -o root -g root -m 0755 "$PATCHED" "$LIVE.pr117.pending"
sudo mv -f "$LIVE.pr117.pending" "$LIVE"
MODIFIED=YES
sudo sha256sum "$LIVE" >"$OUT/new-source.sha256"
STATE=RESTART_ONLY_SUPERVISOR_CONTROLLER
if ! sudo systemctl restart "$SERVICE" || ! sudo systemctl is-active --quiet "$SERVICE"; then
    echo 'SUPERVISOR_CONTROLLER_RESTART_FAILED; restoring original policy'
    sudo install -o root -g root -m 0755 "$BACKUP" "$LIVE"
    sudo systemctl restart "$SERVICE" || true
    MODIFIED=ROLLED_BACK
    exit 5
fi
sudo systemctl show "$SERVICE" -p ActiveState -p MainPID >"$OUT/supervisor-after.txt"
sudo systemctl show sunshine.service -p ActiveState -p MainPID >"$OUT/sunshine-after.txt"
pgrep -ax Xorg >"$OUT/xorg-after.txt" || true
pgrep -ax sunshine >"$OUT/sunshine-pids-after.txt" || true
if ! cmp -s "$OUT/xorg-before.txt" "$OUT/xorg-after.txt" ||
   ! cmp -s "$OUT/sunshine-pids-before.txt" "$OUT/sunshine-pids-after.txt"; then
    STATE=GUARD_INSTALLED_BUT_EXTERNAL_DISPLAY_PROCESS_CHANGED
    echo 'Xorg or Sunshine PID changed during the installation; no further recovery attempted'
    exit 6
fi
STATE=PASS_LIVE_XORG_GUARD_AND_UNCHANGED_MOONLIGHT_DESKTOP
echo 'LIVE_XORG_NOT_KILLED_ON_XRANDR_TIMEOUT=PASS'
echo 'GENUINE_AMDGPU_RESET_SERVICE=UNCHANGED'
