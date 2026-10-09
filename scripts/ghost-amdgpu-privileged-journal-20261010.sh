#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Read-only recovery of root-owned AMDGPU kernel and recovery service logs.
# Never changes sudoers, GPU, services, shadPS4, or current SSH session.
(
set -Eeuo pipefail
FROM='2026-10-09 23:52:00'
TO='2026-10-10 00:12:00'
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${HOME:?}/ghost-amdgpu-root-journal-${STAMP}.tar.gz"
DIR="$(mktemp -d /tmp/ghost-amdgpu-root-journal-XXXXXXXX)"
trap 'rm -rf -- "$DIR"' EXIT

printf 'Read-only kernel journal retrieval for Ghost (local time: %s to %s)\n' "$FROM" "$TO"
if ! sudo -n true >/dev/null 2>&1; then
  printf 'Kernel messages are root-owned. One sudo authentication is required; no settings will be changed.\n'
  sudo -v
fi

capture() {
  local label="$1"; shift
  local rc=0
  timeout --signal=TERM --kill-after=3s 35s "$@" > "$DIR/$label.txt" 2> "$DIR/$label.stderr" || rc=$?
  printf '%s rc=%s bytes=%s\n' "$label" "$rc" "$(wc -c < "$DIR/$label.txt")" >> "$DIR/capture-status.txt"
}

{
  printf 'Captured='; date --iso-8601=seconds
  printf 'Timezone='; date '+%Z %z'
  printf 'Kernel='; uname -r
  printf 'BootID='; cat /proc/sys/kernel/random/boot_id
  printf 'TargetWindow=%s to %s local\n' "$FROM" "$TO"
} > "$DIR/metadata.txt"

capture boot-list sudo -n journalctl --list-boots --no-pager
capture kernel-ghost-window sudo -n journalctl -k -b 0 --no-pager -o short-iso --since "$FROM" --until "$TO"
capture kernel-boot-tail sudo -n journalctl -k -b 0 --no-pager -o short-iso -n 3500
capture amd-reset-watch sudo -n journalctl --no-pager -o short-iso --since "$FROM" --until "$TO" -u sunshine-amdgpu-reset-watch.service
capture sunshine-system sudo -n journalctl --no-pager -o short-iso --since "$FROM" --until "$TO" -u sunshine.service
capture dmesg-root sudo -n dmesg --time-format iso --color=never

MATCH='amdgpu|radv|drm|gpu.*(hang|fault|timeout|reset|recover|ring|queue)|ring.*(timeout|hang|reset)|vm.*fault|device.?lost|job.timed.out|soft.recover|fence.*timeout|gfx_off|sdma|mes|vcn'
for label in kernel-ghost-window kernel-boot-tail amd-reset-watch sunshine-system dmesg-root; do
  if [ -s "$DIR/$label.txt" ]; then
    grep -Ei "$MATCH" "$DIR/$label.txt" > "$DIR/$label.gpu-matches.txt" || true
  fi
done

{
  printf 'Privileged journal collection finished; system settings unchanged.\n'
  cat "$DIR/capture-status.txt"
  for label in kernel-ghost-window kernel-boot-tail amd-reset-watch sunshine-system dmesg-root; do
    file="$DIR/$label.gpu-matches.txt"
    if [ -f "$file" ]; then
      printf '\n%s GPU-related lines: %s\n' "$label" "$(wc -l < "$file")"
      tail -n 25 -- "$file"
    fi
  done
} > "$DIR/summary.txt"

tar -czf "$OUT" -C "$DIR" .
printf '\nARCHIVE_READY=%s\nUPLOAD_THIS_FILE=%s\n' "$OUT" "$OUT"
printf 'NO_GPU_RESETS_OR_SERVICE_CHANGES=YES\nSSH_SESSION=REMAINS_OPEN\n'
)
