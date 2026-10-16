#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Read-only postmortem for a previous X11/Sunshine/shadPS4 presentation loss.
# Never restarts, stops, kills, reconfigures or launches a program.
set -Eeuo pipefail

LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-display-loss-root-audit-$ID"
ARCHIVE="$LOGROOT/gow-display-loss-root-audit-$ID.tar.gz"
START="2026-10-16 12:31:00"
END="2026-10-16 12:36:30"
mkdir -p "$OUT"
STATE="STARTED"
KERNEL_VISIBILITY="UNKNOWN"

finish() {
    local code=$?
    trap - EXIT
    printf 'STATUS=%s\nKERNEL_VISIBILITY=%s\nAUDIT_WINDOW=%s to %s\n' \
        "$STATE" "$KERNEL_VISIBILITY" "$START" "$END" > "$OUT/summary.txt"
    printf 'X11=UNMODIFIED\nAMDGPU=UNMODIFIED\nSUNSHINE=UNMODIFIED\nSHADPS4=NOT_STARTED\nSSH=UNCHANGED\n' \
        >> "$OUT/summary.txt"
    tar -C "$OUT" -czf "$ARCHIVE" . || true
    printf '\nSTATUS=%s\nKERNEL_VISIBILITY=%s\nARCHIVE=%s\n' \
        "$STATE" "$KERNEL_VISIBILITY" "$ARCHIVE"
    return "$code"
}
trap finish EXIT
exec > >(tee "$OUT/progress.log") 2>&1

printf 'DIAGNOSTICS=READ_ONLY\nWINDOW=%s to %s\n' "$START" "$END"
printf '%s\n' 'Original incident: loss of X connection during GoW native shader compilation.'
printf '%s\n' 'Investigating which service/condition terminated the old display.'

# Kernel journal is commonly restricted on Debian. Preserve the access error.
if journalctl -k -b --no-pager -o short-iso --since "$START" --until "$END" \
    > "$OUT/kernel-unprivileged.txt" 2> "$OUT/kernel-unprivileged-error.txt" \
    && grep -q '^2026' "$OUT/kernel-unprivileged.txt"; then
    KERNEL_VISIBILITY="UNPRIVILEGED"
else
    KERNEL_VISIBILITY="RESTRICTED"
fi

# Optional non-interactive, READ-ONLY sudo access. Never request a password,
# change permissions, or modify system configuration.
if [[ "$KERNEL_VISIBILITY" == RESTRICTED ]] && command -v sudo >/dev/null 2>&1; then
    if sudo -n journalctl -k -b --no-pager -o short-iso \
        --since "$START" --until "$END" > "$OUT/kernel-privileged.txt" \
        2> "$OUT/kernel-privileged-error.txt"; then
        KERNEL_VISIBILITY="PRIVILEGED"
    else
        KERNEL_VISIBILITY="RESTRICTED_NO_PASSWORDLESS_SUDO"
    fi
fi

if [[ "$KERNEL_VISIBILITY" == UNPRIVILEGED ]]; then
    cp "$OUT/kernel-unprivileged.txt" "$OUT/kernel-incident.txt"
elif [[ "$KERNEL_VISIBILITY" == PRIVILEGED ]]; then
    cp "$OUT/kernel-privileged.txt" "$OUT/kernel-incident.txt"
else
    printf 'Kernel logs not readable in this SSH session; no reset conclusion is possible.\n' \
      > "$OUT/kernel-incident.txt"
fi

# Collect journal entries for both active and pre-existing recovery services.
journalctl --no-pager -o short-iso --since "$START" --until "$END" \
    -u sunshine.service \
    -u sunshine-amdgpu-reset-watch.service \
    -u sunshine-amdgpu-recover.service \
    -u start-headless-x.service \
    -u sunshine-session-supervisor.service \
    -u display-manager.service \
    > "$OUT/recovery-units.txt" 2>&1 || true

journalctl --user --no-pager -o short-iso --since "$START" --until "$END" \
    -u sunshine.service \
    -u sunshine-amdgpu-reset-watch.service \
    -u sunshine-session-supervisor.service \
    > "$OUT/user-units.txt" 2>&1 || true

# The window is deliberately short. Keep the raw journal separately from
# relevant process/service messages so there is a precise causal timeline.
journalctl --no-pager -o short-iso --since "$START" --until "$END" \
    > "$OUT/window-journal.txt" 2>&1 || true

grep -Ei 'amdgpu|gpu reset|reset watch|gpu recover|recover|sunshine|Xorg|Xwayland|start-headless|xinit|x11|drm|session-supervisor|presentation|vk_error|terminated|shutdown|restarted|timeout|watchdog' \
    "$OUT/window-journal.txt" > "$OUT/incident-timeline.txt" || true

if [[ "$KERNEL_VISIBILITY" == PRIVILEGED ]] && command -v sudo >/dev/null 2>&1; then
    if sudo -n journalctl --no-pager -o short-iso --since "$START" --until "$END" \
        > "$OUT/system-window-privileged.txt" 2> "$OUT/system-window-privileged-error.txt"; then
        grep -Ei 'amdgpu|GPU|drm|sunshine|Xorg|start-headless|xinit|recover|reset|systemd|session|OOM|killed|segfault' \
            "$OUT/system-window-privileged.txt" > "$OUT/system-timeline-privileged.txt" || true
        rm "$OUT/system-window-privileged.txt"
    fi
fi

{
    printf 'AUDIT_TIME=%s\n' "$(date -Is)"
    printf '%s\n' '--- read-only service state ---'
    for unit in sunshine.service sunshine-amdgpu-reset-watch.service \
                sunshine-amdgpu-recover.service start-headless-x.service; do
        systemctl show "$unit" --no-pager \
            -p Id -p LoadState -p ActiveState -p SubState \
            -p ExecMainStartTimestamp -p ExecMainExitTimestamp \
            -p NRestarts 2>&1 || true
    done
    printf '%s\n' '--- existing display processes ---'
    ps -eo pid,ppid,lstart,comm,args | grep -E \
      '(Xorg|Xwayland|sunshine|headless-x|amdgpu-reset|session-supervisor)' \
      | grep -v grep || true
} > "$OUT/service-state.txt" 2>&1

if grep -Eiq '(amdgpu.*(reset|timeout|fault)|ring .*timeout|GPU fault|VM_L2_PROTECTION_FAULT|GPU recovery)' \
    "$OUT/kernel-incident.txt"; then
    echo "KERNEL_GPU_EVENT=MATCH_FOUND_REVIEW_CONTEXT"
else
    if [[ "$KERNEL_VISIBILITY" == RESTRICTED* ]]; then
        echo "KERNEL_GPU_EVENT=UNKNOWN_NO_PRIVILEGED_JOURNAL"
    else
        echo "KERNEL_GPU_EVENT=NO_MATCH_IN_ACCESSIBLE_JOURNAL"
    fi
fi

if grep -q '12:34:59' "$OUT/incident-timeline.txt" "$OUT/recovery-units.txt"; then
    echo "DISPLAY_RESTART=OBSERVED_AT_12_34_59"
fi
STATE="AUDIT_CAPTURED"
echo "No services or drivers were altered; the isolated emulator build was not launched."
