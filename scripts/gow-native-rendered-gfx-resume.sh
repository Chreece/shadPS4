#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# RESUME only the Oct 16 isolated renderer/native-CPU build after the original
# source-only graft omitted untracked common/gpu_cpu_probe.h and frame_metrics.h.
# No clean rebuild, CPU modification, old emulator runtime, DynamoRIO, SSH exit,
# package installation, service control or global process termination.
set -Eeuo pipefail

ROOT="$HOME/projects/shadps4/GodOfWarRagnarok"
STAGE="$ROOT/tests/native-rendered-gfx-20261016-152535-3014679"
SOURCE="$STAGE/source"
BUILD="$STAGE/build"
CANDIDATE="$BUILD/shadps4"
REFERENCE="$ROOT/tests/main-proof-20261015-151529-3107006"
RENDER_SRC="$REFERENCE/source"
RENDER_BIN="$REFERENCE/build/shadps4"
RENDER_SHA="8c013fa6bbeb5650affca3fdd02a75bbf6139e54b2231322e1f4f1170ca0d144"
NATIVE_SHA="1fe3e8b626f34f092bb2be37de8d14fe26d45bc2"
LOGROOT="$HOME/projects/shadps4/logs"
ID="$(date +%Y%m%d-%H%M%S)-$$"
OUT="$LOGROOT/gow-native-rendered-resume-$ID"
ARCHIVE="$LOGROOT/gow-native-rendered-resume-$ID.tar.gz"
mkdir -p "$OUT/screenshots"
STATUS="PRECHECK"
GAME_EXIT="NOT_STARTED"
SHOTS=0
finish() {
  local code=$?
  trap - EXIT
  {
    printf 'STATUS=%s\nGAME_EXIT=%s\nSCRIPT_EXIT=%s\n' "$STATUS" "$GAME_EXIT" "$code"
    printf 'NATIVE_CPU_COMMIT=%s\nRENDER_REFERENCE_SHA256=%s\n' "$NATIVE_SHA" "$RENDER_SHA"
    printf 'CANDIDATE=%s\nSCREENSHOTS=%s\n' "$CANDIDATE" "$SHOTS"
    printf 'ORIGINAL_RENDER_BINARY=NOT_EXECUTED\nDYNAMORIO=DISABLED\n'
    printf 'INSTALLED_EMULATOR=UNCHANGED\nX11=UNCHANGED\nSUNSHINE=UNCHANGED\nSSH=UNCHANGED\n'
  } >"$OUT/summary.txt"
  if [[ -f "$OUT/game-console.log" ]]; then
    for needle in 'Unexpected instruction for offset computation, Phi' \
                  'Unexpected instruction for offset computation, ReadFirstLane' \
                  'Failed to compute offset for SRT walker' \
                  'Phi node type F64' 'Unhandled access violation' \
                  'ErrorSurfaceLostKHR' 'X connection to'; do
      printf '%s\t%s\n' "$needle" \
        "$(grep -Fc "$needle" "$OUT/game-console.log" || true)" \
        >> "$OUT/error-counts.tsv"
    done
    grep -Ei 'unreachable|critical|Unexpected instruction for offset|SRT walker|X connection' \
      "$OUT/game-console.log" | tail -140 >"$OUT/game-error-tail.txt" || true
  fi
  tar -C "$OUT" -czf "$ARCHIVE" . || true
  printf '\nRESULT=%s\nCANDIDATE=%s\nARCHIVE=%s\n' "$STATUS" "$CANDIDATE" "$ARCHIVE"
  return "$code"
}
trap finish EXIT
exec > >(tee "$OUT/progress.log") 2>&1

if ! git -C "$SOURCE" rev-parse --git-dir >/dev/null 2>&1 ||
   [[ "$(git -C "$SOURCE" rev-parse HEAD)" != "$NATIVE_SHA" ]] ||
   [[ ! -f "$BUILD/build.ninja" ]]; then
  echo "Existing isolated native build/source does not match the previous checkpoint."
  exit 2
fi
if [[ ! -f "$RENDER_BIN" || ! -f "$RENDER_SRC/src/common/gpu_cpu_probe.h" ||
      ! -f "$RENDER_SRC/src/common/frame_metrics.h" ]]; then
  echo "Required verified rendering reference or common headers are absent."
  exit 2
fi
if [[ "$(sha256sum "$RENDER_BIN" | awk '{print $1}')" != "$RENDER_SHA" ]]; then
  echo "Original rendered baseline hash differs. Refusing unverified source."
  exit 2
fi
if [[ "$(git -C "$RENDER_SRC" rev-parse HEAD)" != "2e5ff4eb89a5c1ccf9519cd2da1e544e569a831b" ]]; then
  echo "Original rendering source revision differs. Nothing overwritten."
  exit 2
fi
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  STATUS="OTHER_EMULATOR_RUNNING"
  echo "An emulator is running; not rebuilding over or stopping it."
  exit 0
fi
free_kb="$(df -Pk "$BUILD" | awk 'NR==2 {print $4}')"
if [[ ! "$free_kb" =~ ^[0-9]+$ ]] || (( free_kb < 500000 )); then
  STATUS="LOW_DISK_SPACE"
  echo "Less than 500 MB free. Refusing incremental build; no automatic deletion."
  exit 0
fi
echo "INCIDENT=previous Ninja compilation stopped at missing common/gpu_cpu_probe.h"
echo "RESUME_BUILD=$BUILD"
echo "REFERENCE_RENDER_BINARY=checksum only, not run"

# Pin every protected CPU, HLE, ABI and emulator build-system source to the
# SHA256 values recorded in the previous successful source-integrity audit.
verify_native() {
  local label="$1" rel expected actual
  # Exact native-CPU Git object IDs, computed from the pinned commit itself.
  # No manually transcribed SHA256 manifest: that caused the prior false alarm.
  for rel in \
      src/core/cpu_id.cpp \
      src/core/cpu_id_guest.h \
      src/core/cpu_id_trap_table.h \
      src/core/cpu_patches.cpp \
      src/core/signals.cpp \
      src/core/module.cpp \
      src/core/cpu_id_translation/launcher.cpp \
      src/core/libraries/kernel/process.cpp \
      src/main.cpp \
      CMakeLists.txt; do
    if [[ ! -f "$SOURCE/$rel" || -L "$SOURCE/$rel" ]]; then
      echo "MISSING_OR_SYMLINKED_PROTECTED_NATIVE_SOURCE=$rel"
      return 1
    fi
    expected="$(git -C "$SOURCE" rev-parse --verify "$NATIVE_SHA:$rel")" || return 1
    actual="$(git -C "$SOURCE" hash-object -- "$SOURCE/$rel")" || return 1
    printf '%s\t%s\t%s\n' "$rel" "$expected" "$actual" \
      >> "$OUT/native-source-objects-$label.tsv"
    if [[ "$actual" != "$expected" ]]; then
      echo "PROTECTED_NATIVE_SOURCE_CHANGED=$rel"
      return 1
    fi
  done
  echo "NATIVE_CPU_HLE_CMAKE_INTEGRITY=$label PASS"
}

verify_native BEFORE

STATUS="RESTORING_COMMON_DEPENDENCIES"
for name in gpu_cpu_probe.h frame_metrics.h; do
  from="$RENDER_SRC/src/common/$name"
  to="$SOURCE/src/common/$name"
  if [[ ! -f "$from" || -L "$from" || ! -r "$from" ]]; then
    STATUS="UNSAFE_REFERENCE_HEADER"
    echo "Missing/unreadable/symlinked original renderer header: $from"
    exit 3
  fi
  if grep -Eq 'SHADPS4_GOW_|GOW_SRT_|CUSA34384|God of War|Ragnar|0xe83356c8|0xad0b8f1f|0x04691bd5' "$from"; then
    STATUS="CONTRACT_VIOLATION"
    echo "Header contains a title-specific marker; refusing import: $name"
    exit 3
  fi
  if [[ -e "$to" || -L "$to" ]]; then
    if ! cmp -s "$from" "$to"; then
      STATUS="EXISTING_HEADER_CONFLICT"
      echo "A different file already exists at $to. Refusing overwrite."
      exit 3
    fi
  else
    install -m 0644 "$from" "$to"
  fi
  hash="$(sha256sum "$to" | awk '{print $1}')"
  blob="$(git -C "$RENDER_SRC" hash-object "$from")"
  printf '%s\tsha256=%s\tgit_blob=%s\n' "$name" "$hash" "$blob" \
    | tee -a "$OUT/restored-common-headers.tsv"
done
verify_native AFTER
echo "CPU_AND_HLE_UNCHANGED=PASS"

# Before rebuilding, verify every internal quoted header used by renderer
# sources now resolves within this independent native CPU source tree.
missing=0
while IFS= read -r header; do
  [[ -n "$header" ]] || continue
  if [[ ! -f "$SOURCE/src/$header" ]]; then
    echo "MISSING_INTERNAL_HEADER=$header" | tee -a "$OUT/missing-headers.txt"
    missing=$((missing+1))
  fi
done < <(
  grep -RhoE --include='*.h' --include='*.cpp' \
    '^[[:space:]]*#[[:space:]]*include[[:space:]]+"(common|core|shader_recompiler|video_core)/[^"]+"' \
    "$SOURCE/src/shader_recompiler" "$SOURCE/src/video_core" |
  sed -E 's/.*"([^"]+)".*/\1/' | sort -u || true
)
if (( missing > 0 )); then
  STATUS="OTHER_MISSING_INTERNAL_HEADERS"
  echo "Additional generic dependencies required before compiling: $missing"
  exit 3
fi
echo "INTERNAL_RENDERER_HEADERS=PASS"
git -C "$SOURCE" diff --check
git -C "$SOURCE" status --short > "$OUT/source-status.txt"

# Resume the existing 2,618-rule Ninja build; already compiled dependencies
# and objects remain intact. Do NOT delete or reconfigure the build directory.
if ! grep -Eq '^ENABLE_CPU_ID_TRANSLATION(:[^=]*)?=OFF$' "$BUILD/CMakeCache.txt"; then
  STATUS="CPU_TRANSLATION_NOT_DISABLED"
  echo "Cached CMake configuration does not explicitly disable DynamoRIO."
  exit 4
fi
STATUS="INCREMENTAL_BUILD"
echo "INCREMENTAL_COMPILE_BEGIN"
if ! cmake --build "$BUILD" --target shadps4 --parallel 6; then
  STATUS="BUILD_FAILED"
  echo "Incremental build failed. See compiler output in progress.log."
  exit 5
fi
test -x "$CANDIDATE"
sha256sum "$CANDIDATE" | tee "$OUT/candidate-sha256.txt"
verify_native COMPILED
if ! command -v nm >/dev/null 2>&1 || ! command -v objdump >/dev/null 2>&1; then
  STATUS="HLE_PROLOGUE_AUDIT_UNAVAILABLE"
  echo "nm/objdump unavailable; compiled build retained, game not launched."
  exit 4
fi
symbol="$(nm -C "$CANDIDATE" | awk 'index($0," T Libraries::Kernel::sceKernelLoadStartModule(") || index($0," t Libraries::Kernel::sceKernelLoadStartModule(") {if (!first) first=$1} END {print first}')"
if [[ -z "$symbol" ]]; then
  STATUS="HLE_PROLOGUE_SYMBOL_MISSING"
  echo "Cannot locate protected guest-to-host kernel module loader."
  exit 4
fi
end="$(printf '%x' "$((16#$symbol + 160))")"
objdump -dC --start-address="0x$symbol" --stop-address="0x$end" "$CANDIDATE" > "$OUT/hle-prologue.txt"
if ! grep -Eq '[[:space:]]and[[:space:]].*%rsp' "$OUT/hle-prologue.txt"; then
  STATUS="HLE_PROLOGUE_REGRESSED"
  echo "Compiled x86-64 HLE stack realignment not verified; no game launch."
  exit 4
fi
echo "COMPILED_HLE_STACK_REALIGN=PASS"
echo "NATIVE_CPU_UNMODIFIED=PASS"
echo "DYNAMORIO=OFF (original Ninja CMake options)"
STATUS="BUILD_SUCCESS"

# Only launch after confirming that the existing Moonlight/X11 session is
# accessible to this user. No X server, service, driver or account changes.
if ! command -v xdpyinfo >/dev/null 2>&1; then
  STATUS="BUILT_NO_X11_CHECKER"
  echo "Built candidate ready; xdpyinfo unavailable, no game launched."
  exit 0
fi
display_found=""
auth_found=""
sunpid="$(pgrep -u "$(id -u)" -x sunshine | head -1 || true)"
sun_display=""
sun_auth=""
if [[ -n "$sunpid" && -r "/proc/$sunpid/environ" ]]; then
  sun_display="$(tr '\0' '\n' < "/proc/$sunpid/environ" 2>/dev/null |
    sed -n 's/^DISPLAY=//p' | head -1 || true)"
  sun_auth="$(tr '\0' '\n' < "/proc/$sunpid/environ" 2>/dev/null |
    sed -n 's/^XAUTHORITY=//p' | head -1 || true)"
fi
xpid="$(pgrep -x Xorg | head -1 || true)"
xauth=""
if [[ -n "$xpid" && -r "/proc/$xpid/cmdline" ]]; then
  xauth="$(tr '\0' '\n' < "/proc/$xpid/cmdline" 2>/dev/null |
    awk 'p=="-auth" {print;exit} {p=$0}' || true)"
fi
for d in "$sun_display" "${DISPLAY:-}" :0 :1 :2; do
  [[ -n "$d" ]] || continue
  for auth in "$sun_auth" "$xauth" "${XAUTHORITY:-}" \
       "$HOME/.Xauthority" "/run/user/$(id -u)/Xauthority" ""; do
    if [[ -n "$auth" ]]; then
      [[ -r "$auth" && -f "$auth" ]] || continue
      if env DISPLAY="$d" XAUTHORITY="$auth" \
        timeout 3s xdpyinfo -display "$d" >/dev/null 2>&1; then
        display_found="$d"; auth_found="$auth"; break 2
      fi
    elif env -u XAUTHORITY DISPLAY="$d" \
      timeout 3s xdpyinfo -display "$d" >/dev/null 2>&1; then
      display_found="$d"; auth_found=""; break 2
    fi
  done
done
if [[ -z "$display_found" ]]; then
  STATUS="BUILT_DISPLAY_UNAVAILABLE"
  echo "Native+old-renderer build succeeded, but SSH cannot open Moonlight X11."
  echo "No game launched; candidate retained for next manual test."
  exit 0
fi
export DISPLAY="$display_found"
if [[ -n "$auth_found" ]]; then
  export XAUTHORITY="$auth_found"
else
  unset XAUTHORITY
fi
echo "EXISTING_X11_ACCESS=PASS DISPLAY=$DISPLAY"

cd "$OUT"
ulimit -c 0 || true
STATUS="GAME_RUNNING"
echo "GAME=CUSA34384 CPU_BACKEND=builtin GRAPHICS=rendered source"
printf 'elapsed_s\tcpu_percent\tmem_percent\trss_kb\tstate\n' > "$OUT/performance.tsv"
env -u SHADPS4_CPU_ID_RESTART "$CANDIDATE" --cpu-id-mode builtin \
  --game CUSA34384 --fullscreen true --show-fps >"$OUT/game-console.log" 2>&1 &
pid=$!
GAME_EXIT="RUNNING"
early=0
for ((sec=0;sec<180;sec+=5)); do
  state="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -z "$state" || "$state" == Z* ]]; then
    early=1
    echo "GAME_EXITED_AT=$sec"
    break
  fi
  printf '%s\t%s\n' "$sec" \
    "$(LC_ALL=C ps -p "$pid" -o %cpu=,%mem=,rss=,stat= 2>/dev/null || true)" \
    >>"$OUT/performance.tsv"
  if (( sec==20 || sec==60 || sec==110 || sec==160 )); then
    shot="$OUT/screenshots/at-$sec.png"
    if command -v import >/dev/null 2>&1 &&
       timeout 6s import -display "$DISPLAY" -window root "$shot" \
         >/dev/null 2>&1 && [[ -s "$shot" ]]; then
      SHOTS=$((SHOTS+1))
      echo "SCREENSHOT=$shot"
    else
      echo "SCREENSHOT_SKIPPED=$sec (game continues)"
    fi
  fi
  if (( sec%30==0 )); then echo "GAME_PROGRESS=$sec"; fi
  sleep 5
done
if (( early==0 )); then
  STATUS="PLAY_WINDOW_COMPLETED"
  kill -TERM "$pid" 2>/dev/null || true
  for ((n=0;n<10;n++)); do
    state="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -z "$state" || "$state" == Z* ]]; then break; fi
    sleep 1
  done
  state="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -n "$state" && "$state" != Z* ]]; then
    STATUS="WINDOW_DONE_CANDIDATE_STILL_RUNNING"
    GAME_EXIT="RUNNING"
    echo "Candidate still running; no forced kill attempted."
    exit 0
  fi
fi
set +e
wait "$pid"
GAME_EXIT=$?
set -e
if (( early==1 )); then
  if (( GAME_EXIT==0 )); then STATUS="GAME_EXITED"; else STATUS="GAME_CRASHED"; fi
fi
echo "GAME_RESULT=$STATUS EXIT=$GAME_EXIT"
