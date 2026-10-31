#!/usr/bin/env bash
# Diagnose the Sunshine SIGTERM and subsequent Xorg restart. READ ONLY.
# No emulator run, no service stop/restart, no network/display mutation.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
TAG="gds-pr117-supervisor-cause-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
OUT="$LOGS/$TAG"
ARCHIVE="$LOGS/$TAG.tar.gz"
FROM="2026-10-31 07:06:30"
UNTIL="2026-10-31 07:11:00"
STATUS=INCOMPLETE
mkdir -p "$OUT"
finish() {
    local rc=$?
    trap - EXIT
    set +e
    printf 'STATUS=%s\nEXIT_CODE=%s\nGAME_LAUNCHED=NO\n' "$STATUS" "$rc" > "$OUT/result.txt"
    printf 'SSH_SESSION=OPEN\nX11_SUNSHINE_NETWORK_RESTARTED=NO\n' >> "$OUT/result.txt"
    printf 'WINDOW_UTC=%s to %s\n' "$FROM" "$UNTIL" >> "$OUT/result.txt"
    tar -C "$LOGS" -czf "$ARCHIVE" "$TAG" 2>"$OUT/archive-errors.txt"
    echo "STATUS=$STATUS"
    echo "UPLOAD_THIS=$ARCHIVE"
}
trap finish EXIT
for command in python3 journalctl grep tail ps stat tar timeout; do
    command -v "$command" >/dev/null || { echo "MISSING_TOOL=$command"; exit 2; }
done

# Sources shown in the prior evidence: two long-lived monitors survived
# and a new Xorg/Sunshine session was started after a Sunshine SIGTERM.
cat > "$OUT/observed-timeline.txt" <<'TIMELINE'
2026-10-31T08:09:06+01:00 Sunshine PID 4179757: Terminate handler called
2026-10-31T08:09:16+01:00 Sunshine: forced shutdown after 10 seconds
2026-10-31T08:09:29+01:00 new Xorg PID 2237705 started
2026-10-31T08:09:36+01:00 new Sunshine PID 2240305 started
Previous Xorg: AMDGPU Page flip failed: Invalid argument
Supervisors: /usr/local/sbin/sunshine-session-supervisor, sunshine-amdgpu-reset-watch
CAUSE OF SIGNAL/RESTART: NOT YET ESTABLISHED
TIMELINE

ps -eo pid,ppid,user,lstart,stat,args |
  grep -Ei 'sunshine-session-supervisor|sunshine-amdgpu-reset-watch|sunshine-x11-prepare|start-headless-x|/Xorg |/sunshine' |
  grep -v 'gds-pr117-supervisor-cause' > "$OUT/current-controller-processes.txt" || true

# Capture actual restart-policy logic, not any secret-bearing full config.
python3 - "$OUT" <<'PY'
from pathlib import Path
import re,hashlib,sys
root=Path(sys.argv[1])
home=Path.home()
paths=[
 Path('/usr/local/sbin/sunshine-session-supervisor'),
 Path('/usr/local/sbin/sunshine-amdgpu-reset-watch'),
 Path('/usr/local/sbin/sunshine-x11-prepare'),
 Path('/usr/local/sbin/start-headless-x.sh'),
 home/'.local/bin/sunshine-session-preflight',
 home/'.local/bin/sunshine-kms-guard.sh',
 home/'.local/bin/start-headless-x.sh',
]
match=re.compile(r'(?i)(systemctl|restart|stop|start|signal|kill|TERM|watch|monitor|sleep|timeout|reset|amdgpu|gpu|drm|Xorg|sunshine|session|fail|dead|hang|health|PID|flock|journalctl|dmesg)')
secret=re.compile(r'(?i)(password|passwd|secret|bearer|authorization|api[_-]?key|access[_-]?token|private[_-]?key)')
meta=[]
for p in paths:
    if not p.is_file() or p.is_symlink():
        meta.append(f'{p} NOT_REGULAR_OR_MISSING')
        continue
    try:raw=p.read_bytes();lines=raw.decode('utf-8','replace').splitlines()
    except (OSError,ValueError) as e:
        meta.append(f'{p} UNREADABLE={type(e).__name__}')
        continue
    meta.append(f'{p} SHA256={hashlib.sha256(raw).hexdigest()} LINES={len(lines)}')
    shown=set()
    for i,line in enumerate(lines):
        if match.search(line) and not secret.search(line):
            shown.update(range(max(0,i-3),min(len(lines),i+5)))
    out=[]
    for i in sorted(shown)[:650]:
        line=lines[i]
        if secret.search(line):line='[REDACTED POSSIBLE SECRET]'
        out.append(f'{i+1:04d} {line[:350]}')
    (root/f'policy-{p.name}.txt').write_text('\n'.join(out)+'\n')
(root/'policy-metadata.txt').write_text('\n'.join(meta)+'\n')
PY

PATTERN='sunshine|xorg|session|supervisor|watch|signal|sigterm|sigkill|kill|restart|stop|start|terminate|hang|dead|amdgpu|gpu|drm|display|systemd|logind|segfault|coredump|oom|reset|power|suspend|sleep'
collect() {
    label="$1";shift
    # All timestamps parsed in UTC, including with noninteractive sudo.
    TZ=UTC timeout 18 "$@" --since "$FROM" --until "$UNTIL" --no-pager \
      --output=short-iso 2>"$OUT/$label.stderr" |
      grep -Eia "$PATTERN" | tail -n 950 > "$OUT/$label.log" || true
    printf '%s_LINES=%s\n' "$label" "$(wc -l < "$OUT/$label.log")" \
      >> "$OUT/journal-collection.txt"
}
collect user journalctl --user -b
collect system-restricted journalctl -b
collect kernel-restricted journalctl -b -k

# Previous restricted journal omitted other users' and kernel messages.
# Try root read-only journal access ONLY if sudo is already noninteractive.
if command -v sudo >/dev/null && sudo -n -v >/dev/null 2>&1; then
    echo 'ELEVATED_JOURNAL=AUTHORIZED_NONINTERACTIVE' >> "$OUT/journal-collection.txt"
    collect system-privileged sudo -n env TZ=UTC journalctl -b
    collect kernel-privileged sudo -n env TZ=UTC journalctl -b -k
    for unit in sunshine sunshine-session-supervisor sunshine-amdgpu-reset-watch start-headless-x; do
        timeout 12 sudo -n env TZ=UTC journalctl -b -u "$unit" \
          --since "$FROM" --until "$UNTIL" --no-pager --output=short-iso \
          > "$OUT/unit-$unit.log" 2>"$OUT/unit-$unit.stderr" || true
    done
else
    echo 'ELEVATED_JOURNAL=NOT_AUTHORIZED_NO_PASSWORD_PROMPT' >> "$OUT/journal-collection.txt"
fi

for pid in 1253 1288 3075 4179610 4179757 2237705 2240305; do
    TZ=UTC timeout 8 journalctl -b _PID="$pid" \
      --since "$FROM" --until "$UNTIL" --no-pager --output=short-iso \
      > "$OUT/pid-$pid.log" 2>"$OUT/pid-$pid.stderr" || true
done
if command -v systemctl >/dev/null; then
    for unit in sunshine sunshine-session-supervisor sunshine-amdgpu-reset-watch start-headless-x; do
        systemctl show "$unit" --no-pager -p Id -p ActiveState -p MainPID \
          -p ExecMainStartTimestamp -p ExecMainExitTimestamp -p ExecMainCode \
          -p ExecMainStatus -p Restart -p NRestarts \
          > "$OUT/unit-status-$unit.txt" 2>&1 || true
        systemctl --user show "$unit" --no-pager -p Id -p ActiveState -p MainPID \
          -p ExecMainStartTimestamp -p ExecMainExitTimestamp -p ExecMainCode \
          -p ExecMainStatus -p Restart -p NRestarts \
          > "$OUT/user-unit-status-$unit.txt" 2>&1 || true
    done
fi
if command -v coredumpctl >/dev/null; then
    timeout 10 coredumpctl --no-pager list --since "$FROM" --until "$UNTIL" \
      > "$OUT/coredump-metadata.txt" 2>"$OUT/coredump-errors.txt" || true
fi
if [[ -f /var/log/Xorg.0.log.old && ! -L /var/log/Xorg.0.log.old ]]; then
    tail -n 100 /var/log/Xorg.0.log.old |
      grep -Eia 'WW|EE|fatal|signal|terminated|flip|drm|gpu|error' \
      > "$OUT/xorg-old-tail.txt" || true
fi
STATUS=SUPERVISOR_AND_SIGNAL_PROVENANCE_COLLECTED
