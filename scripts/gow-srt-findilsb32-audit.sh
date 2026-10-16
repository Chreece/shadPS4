#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Read-only AMDGPU/X11 incident audit plus isolated shader/CPU tests and build.
set -Eeuo pipefail
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-srt-findilsb32-$ID"
ARCHIVE="$LOGROOT/gow-srt-findilsb32-$ID.tar.gz"
SOURCE="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-cpu-identity-merged-20261016/source"
BUILD="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-cpu-identity-merged-20261016/build"
PIN="6e425838c2493b0c072ec24b11ed512a0863af79"
BRANCH="fix/general-srt-findilsb32-20261016"
mkdir -p "$OUT"
STATUS="STARTED"
finish() {
  local code=$?
  trap - EXIT
  printf 'STATUS=%s\nPIN=%s\nGAME=NOT_LAUNCHED\nINSTALLED_EMULATOR=UNCHANGED\nX11=UNCHANGED\nDRIVERS=UNCHANGED\nSUNSHINE=UNCHANGED\nSSH=UNCHANGED\n' "$STATUS" "$PIN" >"$OUT/summary.txt"
  tar -C "$OUT" -czf "$ARCHIVE" . || true
  printf '\nSTATUS=%s\nARCHIVE=%s\n' "$STATUS" "$ARCHIVE"
  return "$code"
}
trap finish EXIT
exec > >(tee "$OUT/progress.log") 2>&1

latest="$(find "$LOGROOT" -maxdepth 1 -type f -name 'gow-native-stack-align-*.tar.gz' -printf '%f\n' | sort | tail -1 || true)"
stamp="$(printf '%s\n' "$latest" | sed -nE 's/^gow-native-stack-align-([0-9]{8})-([0-9]{6})-.*/\1-\2/p')"
if [[ -z "$stamp" ]]; then stamp="20261016-123405"; fi
local_stamp="$(printf '%s\n' "$stamp" | sed -E 's/^([0-9]{4})([0-9]{2})([0-9]{2})-([0-9]{2})([0-9]{2})([0-9]{2})$/\1-\2-\3 \4:\5:\6/')"
epoch="$(date -d "$local_stamp" +%s)"
since="$(date -d "@$((epoch-180))" '+%F %T')"
until="$(date -d "@$((epoch+1200))" '+%F %T')"
printf 'ANCHOR=%s\nWINDOW=%s to %s\n' "$stamp" "$since" "$until" | tee "$OUT/window.txt"

journalctl -k --no-pager -o short-iso --since "$since" --until "$until" >"$OUT/kernel-journal.txt" 2>&1 || true
journalctl --no-pager -o short-iso --since "$since" --until "$until" \
  -u sunshine.service -u display-manager.service -u lightdm.service -u gdm.service -u sddm.service \
  >"$OUT/system-display.txt" 2>&1 || true
journalctl --user --no-pager -o short-iso --since "$since" --until "$until" \
  -u sunshine.service -u x11vnc.service >"$OUT/user-display.txt" 2>&1 || true
journalctl --no-pager -o short-iso --since "$since" --until "$until" \
  _COMM=Xorg _COMM=Xwayland _COMM=sunshine >"$OUT/display-processes.txt" 2>&1 || true
if command -v dmesg >/dev/null 2>&1; then
  dmesg -T 2>&1 | grep -Ei 'amdgpu|drm|gpu reset|gpu fault|ring .*timeout|xorg|page fault' \
    | tail -350 >"$OUT/kernel-ring-relevant.txt" || true
fi
for file in /var/log/Xorg.0.log "$HOME/.local/share/xorg/Xorg.0.log" "$HOME/.local/share/xorg/Xorg.1.log"; do
  if [[ -f "$file" && -r "$file" ]]; then
    { printf '\n=== %s ===\n' "$file"; tail -500 "$file"; } >>"$OUT/xorg-files.txt" || true
  fi
done
{
  date -Is
  ps -eo pid,lstart,comm,args | grep -E '(Xorg|Xwayland|sunshine|shadps4|gamescope|xinit|startx|openbox|kwin)' | grep -v grep || true
  if command -v xdpyinfo >/dev/null 2>&1; then
    timeout 5s xdpyinfo -display :0 2>&1 | grep -E '(display|dimensions|vendor string|unable to open)' || true
  fi
} > "$OUT/display-now.txt" 2>&1
if grep -Eiq 'gpu reset|GPU recovery|amdgpu.*(timeout|fault|reset)|ring [a-z0-9_]+ timeout|VM_L2_PROTECTION_FAULT|GPU fault detected' \
  "$OUT/kernel-journal.txt" "$OUT/kernel-ring-relevant.txt"; then
  echo "GPU_RESET_INDICATOR=PRESENT"
else
  echo "GPU_RESET_INDICATOR=NOT_FOUND_OR_LOG_ACCESS_LIMITED"
fi
echo "GPU_X11_POSTMORTEM=CAPTURED"

if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  STATUS="AUDIT_ONLY_GAME_RUNNING"
  echo "An emulator is running; build skipped to avoid disturbance."
  exit 0
fi
if ! git -C "$SOURCE" rev-parse --git-dir >/dev/null 2>&1 || [[ ! -f "$BUILD/build.ninja" ]]; then
  STATUS="AUDIT_ONLY_NO_BUILD"
  echo "Isolated build not found; no other installation changed."
  exit 0
fi
if [[ -n "$(git -C "$SOURCE" status --porcelain)" ]]; then
  STATUS="AUDIT_ONLY_DIRTY_WORKTREE"
  echo "Existing source has edits, refusing checkout."
  exit 0
fi
STATUS="FETCHING_FIX"
git -C "$SOURCE" -c http.version=HTTP/1.1 fetch --no-tags \
  "https://github.com/Chreece/shadPS4.git" "refs/heads/$BRANCH"
if ! git -C "$SOURCE" merge-base --is-ancestor "$PIN" FETCH_HEAD; then
  STATUS="UNEXPECTED_BRANCH"
  echo "Pinned fix not found, refusing unknown code."
  exit 3
fi
git -C "$SOURCE" checkout --detach "$PIN"
echo "SOURCE=$(git -C "$SOURCE" rev-parse HEAD)"
STATUS="TESTING"
g++ -std=c++23 -O2 -Wall -Wextra -I"$SOURCE/src" -I"$SOURCE/externals/xbyak" \
  "$SOURCE/tests/shader_recompiler/test_srt_findilsb32.cpp" -o "$OUT/srt-findilsb32-test"
"$OUT/srt-findilsb32-test"
for spec in guest_cpuid rdpid_trap_table; do
  if [[ "$spec" == guest_cpuid ]]; then file="$SOURCE/tests/cpu_id/test_guest_cpuid.cpp";
  else file="$SOURCE/tests/cpu_id/test_rdpid_trap_table.cpp"; fi
  g++ -std=c++23 -O2 -pthread -I"$SOURCE/src" "$file" -o "$OUT/$spec"
  "$OUT/$spec"
done
g++ -std=c++23 -O2 -I"$SOURCE/src" \
  "$SOURCE/tests/kernel/test_module_filename.cpp" -o "$OUT/kernel_filename_test"
"$OUT/kernel_filename_test"
STATUS="BUILDING"
cmake -S "$SOURCE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_TESTS=OFF -DENABLE_CPU_ID_TRANSLATION=OFF
jobs="$(nproc)"
if (( jobs > 6 )); then jobs=6; fi
cmake --build "$BUILD" --target shadps4 --parallel "$jobs"
test -x "$BUILD/shadps4"
sha256sum "$BUILD/shadps4" >"$OUT/candidate-sha256.txt"
STATUS="BUILD_OK_NO_GAME_LAUNCH"
echo "SRT_FINDILSB32_TEST_BUILD=PASS"
