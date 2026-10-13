#!/usr/bin/env bash
# Check the local UMR executable, database and device configuration.
# No game launch, register writes, wave scan, install or permission changes.
set -uo pipefail
umask 077
S="$HOME/projects/shadps4/GhostOfTsushima/tools/umr"
U="$S/build/src/app/umr"
PIN=4ec6294f3204fee024adfb4aa00555c7c8973cc5
L="$HOME/projects/shadps4/logs"
mkdir -p "$L" || exit 1
O=$(mktemp -d "$L/got-umr-verify-$(date +%Y%m%d-%H%M%S)-XXXXXX") || exit 1
finish() {
    local rc=$?
    printf 'CHECK_EXIT=%s\n' "$rc" >> "$O/result.txt"
    if tar -czf "$O.tar.gz" -C "$O" .; then
        printf '\nUPLOAD=%s.tar.gz\n' "$O"
    else
        printf '\nARCHIVE_FAILED; REPORT_DIR=%s\n' "$O" >&2
    fi
}
trap finish EXIT
fail() { printf 'STOP: %s\n' "$*" | tee -a "$O/result.txt" >&2; exit 1; }
for x in git timeout sudo sha256sum grep find tar; do
    command -v "$x" >/dev/null || fail "Missing tool: $x"
done
[[ -x "$U" && -f "$U" && ! -L "$U" ]] || fail "UMR executable missing or symlinked: $U"
[[ -d "$S/database/ip" ]] || fail "Register database missing: $S/database/ip"
git -C "$S" rev-parse HEAD > "$O/commit.txt" 2>&1 || fail 'Cannot read UMR source revision'
[[ "$(cat "$O/commit.txt")" == "$PIN" ]] || fail 'UMR source revision differs from the pinned build'
sha256sum "$U" > "$O/binary.sha256" || fail 'Cannot hash the UMR executable'
git -C "$S" status --short > "$O/source-status.txt" 2>&1 || true
uname -r > "$O/kernel.txt"
grep -Ei '^(UMR_NO_(LLVM|DRM)|LLVM[^:]*|CMAKE_BUILD_TYPE):' \
    "$S/build/CMakeCache.txt" > "$O/build-options.txt" 2>&1 || true
# Validate flags using this executable, not an assumed UMR release.
timeout --kill-after=2s 10s "$U" --help > "$O/help.txt" 2>&1
help_rc=$?
printf 'HELP_EXIT=%s\n' "$help_rc" >> "$O/result.txt"
for opt in --database-path --by-pci --config; do
    grep -Fq -- "$opt" "$O/help.txt" || fail "This binary's help does not advertise $opt; see report"
done
printf '=== UMR device configuration ===\n'
(cd "$O" && sudo -n timeout --kill-after=3s 30s "$U" \
    --database-path "$S/database" --by-pci 0000:03:00.0 --config) \
    > "$O/config.txt" 2>&1
rc=$?
cat "$O/config.txt"
printf 'CONFIG_EXIT=%s\n' "$rc" | tee -a "$O/result.txt"
# Names and permissions only: do not read ring buffers, waves or GPU memory.
sudo -n find /sys/kernel/debug/dri/0000:03:00.0 -maxdepth 1 \
    -name 'amdgpu_*' -printf '%M %u:%g %f\n' \
    > "$O/debugfs-interfaces.txt" 2>&1 || true
printf '=== Build options ===\n'
cat "$O/build-options.txt"
printf 'GAME_LAUNCHED=NO\nWAVE_SCAN=NOT_RUN\n' | tee -a "$O/result.txt"
exit "$rc"
