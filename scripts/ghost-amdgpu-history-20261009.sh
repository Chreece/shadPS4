(
set -Eeuo pipefail
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${HOME:?}/ghost-amdgpu-history-${STAMP}.tar.gz"
TMP="$(mktemp -d /tmp/ghost-amdgpu-history-XXXXXXXX)"
trap 'rm -rf -- "$TMP"' EXIT
FROM='2026-10-09 23:52:00'
TO='2026-10-10 00:12:00'

printf 'GHOST_GPU_KERNEL_AUDIT\nWINDOW=%s -> %s (host local time)\n' "$FROM" "$TO"
{
  printf 'Captured: '; date --iso-8601=seconds
  printf 'Timezone: '; date '+%Z %z'
  printf 'Boot ID: '; cat /proc/sys/kernel/random/boot_id
  uname -a
  printf '\nLogged-in identity:\n'; id
} > "$TMP/host.txt" 2>&1

capture() {
  local label="$1"; shift
  local code=0
  timeout --signal=TERM --kill-after=3s 25s "$@" > "$TMP/$label.txt" 2> "$TMP/$label.stderr" || code=$?
  printf '%s exit_code=%s bytes=%s\n' "$label" "$code" "$(wc -c < "$TMP/$label.txt")" >> "$TMP/capture-status.txt"
}

capture journal-boots journalctl --list-boots --no-pager
capture kernel-window journalctl -k --no-pager -o short-iso --since "$FROM" --until "$TO"
capture kernel-this-boot-tail journalctl -k -b -n 3000 --no-pager -o short-iso
capture gpu-reset-watch journalctl --no-pager -o short-iso --since "$FROM" --until "$TO" -u sunshine-amdgpu-reset-watch.service
capture gpu-reset-watch-user journalctl --user --no-pager -o short-iso --since "$FROM" --until "$TO" -u sunshine-amdgpu-reset-watch.service
capture sunshine-service journalctl --no-pager -o short-iso --since "$FROM" --until "$TO" -u sunshine.service
capture dmesg dmesg --time-format iso --color=never
capture reset-watch-service-status systemctl status sunshine-amdgpu-reset-watch.service --no-pager -l
capture reset-watch-user-status systemctl --user status sunshine-amdgpu-reset-watch.service --no-pager -l
capture pci-gpus lspci -nnk
capture uname-drm ls -l /sys/class/drm
capture relevant-processes ps -eo pid,ppid,stat,comm

# Read-only, non-interactive fallback if the logged-in account cannot read the kernel journal.
if ! grep -qE '^kernel-window exit_code=0 ' "$TMP/capture-status.txt"; then
  if command -v sudo >/dev/null 2>&1; then
    capture kernel-window-sudo-noninteractive sudo -n journalctl -k --no-pager -o short-iso --since "$FROM" --until "$TO"
  fi
fi

# Preserve the full raw outputs; additionally extract just GPU-related messages for inspection.
pattern='amdgpu|radv|drm|gpu.*(hang|fault|timeout|reset|recover|ring|queue)|ring.*(timeout|hang|reset)|vm.*fault|device.lost|job.timed.out|soft.recover|fence.*timeout|vulkan'
for label in kernel-window kernel-window-sudo-noninteractive kernel-this-boot-tail dmesg gpu-reset-watch; do
  if [ -f "$TMP/$label.txt" ]; then
    grep -Ei "$pattern" "$TMP/$label.txt" > "$TMP/$label.gpu-matches.txt" || true
  fi
done

{
  for d in /sys/class/drm/card*/device; do
    [ -d "$d" ] || continue
    printf '\nDEVICE=%s\n' "$d"
    for f in vendor device subsystem_vendor subsystem_device uevent gpu_busy_percent mem_busy_percent power_dpm_force_performance_level; do
      if [ -r "$d/$f" ]; then
        printf '\n%s:\n' "$f"
        head -c 8192 -- "$d/$f" || true
        printf '\n'
      fi
    done
  done
} > "$TMP/drm-device-readonly.txt" 2>&1

{
  echo 'GPU-related matches in the session and kernel tail:'
  for f in "$TMP"/*.gpu-matches.txt; do
    [ -f "$f" ] || continue
    printf '%s: ' "${f##*/}"
    wc -l < "$f"
    tail -n 12 -- "$f" || true
    printf '\n'
  done
  echo 'Capture command statuses:'
  cat "$TMP/capture-status.txt"
} > "$TMP/summary.txt"

tar -czf "$OUT" -C "$TMP" .
printf '\nARCHIVE_READY=%s\n' "$OUT"
printf 'UPLOAD_THIS_FILE=%s\n' "$OUT"
printf 'NO_EMULATOR_STARTED=YES\nNO_SOURCE_OR_GPU_SETTINGS_CHANGED=YES\nSSH_SESSION=REMAINS_OPEN\n'
)
