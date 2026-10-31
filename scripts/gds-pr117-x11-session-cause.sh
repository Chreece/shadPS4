#!/usr/bin/env bash
# Read-only PR117 Xorg/Sunshine failure investigation; NEVER launch the game.
# The observed failure was 2026-10-31 07:08:36--07:09:37 UTC.
set -Eeuo pipefail
umask 077
PROJECT="$HOME/projects/shadps4"
LOGS="$PROJECT/logs"
TAG="gds-pr117-x11-cause-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
OUT="$LOGS/$TAG"
ARCHIVE="$LOGS/$TAG.tar.gz"
PREV="$LOGS/gds-pr117-abi-real-gpu-20261031T070828Z-2228561/evidence"
FROM="2026-10-31 06:58:00 UTC"
TO="2026-10-31 07:20:00 UTC"
STATUS=INCOMPLETE
mkdir -p "$OUT"
finish() {
  local rc=$?
  trap - EXIT
  set +e
  printf 'STATUS=%s\nEXIT_CODE=%s\n' "$STATUS" "$rc" > "$OUT/result.txt"
  printf 'GAME_LAUNCHED=NO\nNETWORK_RESTARTED=NO\n' >> "$OUT/result.txt"
  printf 'XORG_SUNSHINE_CONFIG_MODIFIED=NO\nSSH_SESSION=OPEN\n' >> "$OUT/result.txt"
  printf 'WINDOW_START_UTC=%s\nWINDOW_END_UTC=%s\n' "$FROM" "$TO" >> "$OUT/result.txt"
  tar -C "$LOGS" -czf "$ARCHIVE" "$TAG" 2>"$OUT/archive-errors.txt"
  echo "STATUS=$STATUS"
  echo "UPLOAD_THIS=$ARCHIVE"
}
trap finish EXIT
for cmd in journalctl grep tail ps tar timeout python3 wc cp; do
  command -v "$cmd" >/dev/null || { echo "MISSING=$cmd"; exit 2; }
done
for file in display-before.txt display-after.txt result.txt capture.json; do
  [[ -f "$PREV/$file" && ! -L "$PREV/$file" ]] &&
    cp -- "$PREV/$file" "$OUT/previous-$file" || true
done
python3 - "$OUT" <<'PY'
from pathlib import Path
import re,sys
p=Path(sys.argv[1])
def read(kind):
    f=p/f'previous-display-{kind}.txt'
    out={}
    if f.exists():
        for row in f.read_text(errors='replace').splitlines():
            m=re.match(r'^\s*(\d+)\s+(.+)$',row)
            if not m:continue
            if '/Xorg ' in m[2]:out['XORG']=m[1]
            if '/sunshine' in m[2]:out['SUNSHINE']=m[1]
    return out
before,after=read('before'),read('after')
rows=[]
for name in ('XORG','SUNSHINE'):
    a,b=before.get(name),after.get(name)
    rows.extend((f'{name}_PID_BEFORE={a or "UNKNOWN"}',
                 f'{name}_PID_AFTER={b or "UNKNOWN"}',
                 f'{name}_REPLACED={"YES" if a and b and a!=b else "UNVERIFIED"}'))
rows.append('CAUSE=NOT_DETERMINED_BY_PID_CHANGE_ALONE')
(p/'display-session-classification.txt').write_text('\n'.join(rows)+'\n')
print('\n'.join(rows))
PY

MATCH='Xorg|sunshine|display-manager|sddm|lightdm|gdm|X11|xserver|session.*(stop|start|fail|restart|terminate)|segfault|coredump|core dump|SIGSEGV|SIGABRT|SIGBUS|out of memory|oom|killed process|amdgpu|gpu reset|gpu fault|drm|SurfaceLost|seat0|logind|gpu hang|server terminated'
read_journal() {
  local label="$1";shift
  # Scoped journal query: do not retain unrelated system-user events.
  set +e
  timeout 24 journalctl "$@" --since "$FROM" --until "$TO" \
    --no-pager --output=short-iso 2>"$OUT/$label.err" |
      grep -Eia "$MATCH" | tail -n 900 > "$OUT/$label.log"
  set -e
  printf '%s_LINES=%s\n' "$label" "$(wc -l < "$OUT/$label.log")" \
    >> "$OUT/collection-status.txt"
}
read_journal system -b
read_journal kernel -b -k
read_journal user --user -b

# Read actual Xorg (old and new) and Sunshine error logs, if accessible.
for path in /var/log/Xorg.0.log.old /var/log/Xorg.0.log \
            "$HOME/.local/share/xorg/Xorg.0.log.old" \
            "$HOME/.local/share/xorg/Xorg.0.log"; do
  [[ -f "$path" && ! -L "$path" ]] || continue
  label="$(printf '%s' "$path" | tr '/ ' '__')"
  stat -c '%n SIZE=%s MTIME=%y' "$path" >> "$OUT/xorg-log-files.txt" || true
  grep -Eia '\(EE\)|\(WW\)|fatal|signal|backtrace|GPU|amdgpu|drm|terminated' \
    "$path" | tail -n 300 > "$OUT/xorg-$label.txt" || true
done
for path in "$HOME/.config/sunshine/logs/sunshine.log" \
            "$HOME/.config/sunshine/sunshine.log"; do
  [[ -f "$path" && ! -L "$path" ]] || continue
  label="$(printf '%s' "$path" | tr '/ ' '__')"
  stat -c '%n SIZE=%s MTIME=%y' "$path" >> "$OUT/sunshine-log-files.txt" || true
  grep -Eia 'error|fatal|session|display|x11|capture|reset|crash|encoder|gpu' \
    "$path" | tail -n 260 > "$OUT/sunshine-$label.txt" || true
done

# Collect read-only coredump metadata; never export raw core memory.
if command -v coredumpctl >/dev/null; then
  timeout 12 coredumpctl --no-pager list --since "$FROM" --until "$TO" \
    > "$OUT/coredump-list.txt" 2>"$OUT/coredump.err" || true
  for pid in 4179610 4179757 2230760; do
    timeout 8 coredumpctl --no-pager info "$pid" \
      > "$OUT/coredump-$pid.txt" 2>"$OUT/coredump-$pid.err" || true
  done
fi
if command -v loginctl >/dev/null; then
  timeout 8 loginctl list-sessions --no-legend --no-pager \
    > "$OUT/current-sessions.txt" 2>"$OUT/loginctl.err" || true
fi
ps -eo pid,ppid,lstart,etime,stat,args |
  grep -Ei '(^ *PID|/Xorg|/sunshine|sddm|lightdm|gdm|display-manager)' \
  > "$OUT/current-display-processes.txt" || true
STATUS=DISPLAY_SERVER_FAILURE_EVIDENCE_COLLECTED
