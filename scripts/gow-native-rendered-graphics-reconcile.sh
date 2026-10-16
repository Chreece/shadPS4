#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Graphics provenance reconciliation ON TOP OF today's emulator-owned native CPU.
# The old rendered build is reference data ONLY: it is NEVER executed or installed.
# No DynamoRIO, game ID/shader hash exceptions, driver/display modifications,
# old emulator promotion, cross-process kills, or SSH-session termination.
set -Eeuo pipefail

ROOT="$HOME/projects/shadps4/GodOfWarRagnarok"
RENDER="$ROOT/tests/main-proof-20261015-151529-3107006"
RENDER_SRC="$RENDER/source"
RENDER_BIN="$RENDER/build/shadps4"
RENDER_SHA="8c013fa6bbeb5650affca3fdd02a75bbf6139e54b2231322e1f4f1170ca0d144"
NATIVE_COMMIT="1fe3e8b626f34f092bb2be37de8d14fe26d45bc2"
REPO="https://github.com/Chreece/shadPS4.git"
LOGROOT="$HOME/projects/shadps4/logs"
RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
RUN="$LOGROOT/gow-native-rendered-gfx-$RUN_ID"
ARCHIVE="$LOGROOT/gow-native-rendered-gfx-$RUN_ID.tar.gz"
STAGE="$ROOT/tests/native-rendered-gfx-$RUN_ID"
SOURCE="$STAGE/source"
BUILD="$STAGE/build"
CANDIDATE="$BUILD/shadps4"
STATUS="PRECHECK"
PLAY_EXIT="NOT_STARTED"
SHOTS=0
mkdir -p "$RUN/screenshots" "$STAGE" "$LOGROOT"

finish() {
  local rc=$?
  trap - EXIT
  {
    printf 'STATUS=%s\nPLAY_EXIT=%s\nSCRIPT_EXIT=%s\n' "$STATUS" "$PLAY_EXIT" "$rc"
    printf 'RENDERED_GRAPHICS_REFERENCE_SHA256=%s\nNATIVE_CPU_SOURCE_COMMIT=%s\n' \
      "$RENDER_SHA" "$NATIVE_COMMIT"
    printf 'STAGED_SOURCE=%s\nSTAGED_BUILD=%s\n' "$SOURCE" "$CANDIDATE"
    printf 'SCREENSHOTS=%s\nOLD_RENDERED_EXECUTABLE=NOT_EXECUTED\n' "$SHOTS"
    printf 'EXTERNAL_CPU_TRANSLATION=DISABLED\nINSTALLED_EMULATOR=UNCHANGED\n'
    printf 'OTHER_EMULATORS=UNCHANGED\nX11=UNCHANGED\nSUNSHINE=UNCHANGED\n'
    printf 'DRIVERS=UNCHANGED\nSSH_SESSION=UNCHANGED\n'
  } > "$RUN/summary.txt"
  if [[ -f "$RUN/game-console.log" ]]; then
    for pattern in 'Phi node type F64' \
                   'Unexpected instruction for offset computation, Phi' \
                   'Unexpected instruction for offset computation, ReadFirstLane' \
                   'Failed to compute offset for SRT walker' \
                   'Unhandled access violation' 'X connection to'; do
      printf '%s\t%s\n' "$pattern" \
        "$(grep -Fc "$pattern" "$RUN/game-console.log" || true)" \
        >> "$RUN/shader-error-counts.tsv"
    done
    grep -Ei 'critical|unreachable|SRT walker|Unexpected instruction for offset|X connection to' \
      "$RUN/game-console.log" | tail -150 > "$RUN/error-tail.txt" || true
  fi
  tar -C "$RUN" -czf "$ARCHIVE" . || true
  printf '\nRESULT=%s\nCANDIDATE=%s\nARCHIVE=%s\n' "$STATUS" "$CANDIDATE" "$ARCHIVE"
  return "$rc"
}
trap finish EXIT
exec > >(tee "$RUN/progress.log") 2>&1

echo "APPROACH=preserve today native CPU; replace renderer code from verified graphics source"
echo "REFERENCE_BINARY=checksum-only, never run"
if [[ ! -f "$RENDER_BIN" || ! -d "$RENDER_SRC" ]]; then
  echo "Verified renderer baseline files are missing. No substitute baseline used."
  exit 2
fi
old_sha="$(sha256sum "$RENDER_BIN" | awk '{print $1}')"
if [[ "$old_sha" != "$RENDER_SHA" ]]; then
  printf 'RENDER_BASELINE_SHA_MISMATCH expected=%s actual=%s\n' "$RENDER_SHA" "$old_sha"
  echo "Refusing an unverified historical renderer."
  exit 2
fi
if ! git -C "$RENDER_SRC" rev-parse --git-dir >/dev/null 2>&1; then
  echo "Historical rendered source checkout unavailable."; exit 2
fi
if pgrep -x -u "$(id -u)" shadps4 >/dev/null 2>&1; then
  STATUS="GAME_ALREADY_RUNNING"
  echo "Another shadps4 is active; no build/game process will be disturbed."
  exit 0
fi
echo "RENDERED_BASELINE_SHA256=PASS"
# Full isolated builds consume SSD space. Do not trigger an out-of-space
# incident on the homeserver or clean/overwrite user projects automatically.
free_kb="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
printf 'FREE_SPACE_KB=%s\n' "$free_kb" > "$RUN/space-check.txt"
if [[ ! "$free_kb" =~ ^[0-9]+$ ]] || (( free_kb < 6000000 )); then
  STATUS="LOW_DISK_SPACE"
  echo "Less than 6 GB free. Refusing isolated rebuild rather than filling SSD."
  echo "No automatic deletions; existing working and CPU builds are preserved."
  exit 0
fi
git -C "$RENDER_SRC" rev-parse HEAD > "$RUN/rendered-source-head.txt"
git -C "$RENDER_SRC" status --short > "$RUN/rendered-source-status.txt"

# Fail closed on anything tied to a single title, known shader address, or
# private setup. The user wants general emulator improvements only.
if grep -REn 'SHADPS4_GOW_|GOW_SRT_|CUSA34384|God of War|Ragnar|0xe83356c8|0xad0b8f1f|0x04691bd5' \
   "$RENDER_SRC/src/shader_recompiler" "$RENDER_SRC/src/video_core" \
   --include='*.cpp' --include='*.h' > "$RUN/forbidden-renderer-markers.txt"; then
  STATUS="RENDERER_CONTRACT_AUDIT_FAILED"
  echo "Old renderer contains a forbidden game/setup-specific marker."
  echo "Refusing to import it into native CPU emulator."
  exit 3
fi

# Worktree shares Git objects but NOT modified files or build outputs.
# The base commit is today's tested builtin CPU/HLE/CPUID architecture.
STATUS="STAGING_NATIVE_CPU"
git -C "$RENDER_SRC" -c http.version=HTTP/1.1 fetch --no-tags "$REPO" "$NATIVE_COMMIT"
if [[ "$(git -C "$RENDER_SRC" rev-parse FETCH_HEAD)" != "$NATIVE_COMMIT" ]]; then
  echo "Native CPU source pin mismatch"; exit 3
fi
git -C "$RENDER_SRC" worktree add --detach "$SOURCE" "$NATIVE_COMMIT"
echo "NATIVE_CPU_SOURCE_PIN=PASS"
printf 'CPU source hashes BEFORE renderer reconciliation:\n' > "$RUN/native-cpu-hashes.before"
for path in src/core/cpu_id.cpp src/core/cpu_id_guest.h \
            src/core/cpu_id_trap_table.h src/core/cpu_patches.cpp \
            src/core/signals.cpp src/core/module.cpp \
            src/core/cpu_id_translation/launcher.cpp \
            src/core/libraries/kernel/process.cpp src/main.cpp CMakeLists.txt; do
  sha256sum "$SOURCE/$path" >> "$RUN/native-cpu-hashes.before"
done

# Only broad GPU/shader source paths are restored. The renderer reference has
# previously produced changing, textured GoW frames with original user config.
# Do not import its old drrun, cpuid, HLE, CMake options, launchers or configs.
STATUS="GRAFTING_GENERAL_GRAPHICS_SOURCE"
for area in src/shader_recompiler src/video_core; do
  cp -a "$RENDER_SRC/$area/." "$SOURCE/$area/"
done
# Both headers are universal diagnostic dependencies of the rendered renderer.
# They were untracked in the reference checkout, so copying only tracked
# shader/video directories previously omitted them and broke the build.
for header in gpu_cpu_probe.h frame_metrics.h; do
  origin="$RENDER_SRC/src/common/$header"
  target="$SOURCE/src/common/$header"
  if [[ ! -f "$origin" || -L "$origin" ||
        ! -r "$origin" ]]; then
    STATUS="MISSING_RENDERED_COMMON_DEPENDENCY"
    echo "Cannot verify generic renderer dependency: $origin"
    exit 4
  fi
  if grep -Eq 'SHADPS4_GOW_|GOW_SRT_|CUSA34384|God of War|Ragnar|0xe83356c8|0xad0b8f1f|0x04691bd5' "$origin"; then
    STATUS="NONUNIVERSAL_RENDERER_DEPENDENCY"
    echo "Unapproved title-specific content in $origin"
    exit 4
  fi
  if [[ -e "$target" ]]; then
    cmp -s "$origin" "$target" || {
      STATUS="EXISTING_COMMON_DEPENDENCY_CONFLICT"
      echo "Refusing to overwrite an existing common header: $target"
      exit 4
    }
  else
    install -m 0644 "$origin" "$target"
  fi
  printf '%s  %s\n' "$(sha256sum "$target" | awk '{print $1}')" "$target" \
    >> "$RUN/renderer-common-dependencies.sha256"
done
sha256sum "$SOURCE/src/core/cpu_id.cpp" > "$RUN/native-cpu-identity-sha256.txt"
printf 'CPU source hashes AFTER renderer reconciliation:\n' > "$RUN/native-cpu-hashes.after"
for path in src/core/cpu_id.cpp src/core/cpu_id_guest.h \
            src/core/cpu_id_trap_table.h src/core/cpu_patches.cpp \
            src/core/signals.cpp src/core/module.cpp \
            src/core/cpu_id_translation/launcher.cpp \
            src/core/libraries/kernel/process.cpp src/main.cpp CMakeLists.txt; do
  sha256sum "$SOURCE/$path" >> "$RUN/native-cpu-hashes.after"
done
# The only difference between the hash reports may be their first heading.
if ! cmp -s <(tail -n +2 "$RUN/native-cpu-hashes.before") \
             <(tail -n +2 "$RUN/native-cpu-hashes.after"); then
  STATUS="CPU_SOURCE_CHANGED_UNEXPECTEDLY"
  echo "GPU restoration changed protected CPU/HLE source; refusing build."
  exit 4
fi
echo "NATIVE_CPU_AND_HLE_FILES_UNCHANGED=PASS"
git -C "$SOURCE" diff --stat -- src/shader_recompiler src/video_core \
  > "$RUN/renderer-diff-stat.txt"
git -C "$SOURCE" diff --binary -- src/shader_recompiler src/video_core \
  > "$RUN/renderer-diff.patch"
git -C "$SOURCE" status --short > "$RUN/staged-source-status.txt"
git -C "$SOURCE" diff --check
if [[ ! -s "$RUN/renderer-diff.patch" ]]; then
  STATUS="GRAPHICS_SOURCE_IDENTICAL"
  echo "Native GPU source already matches rendered baseline in tracked files."
  echo "Do not guess a different graphics fix; review CPU execution/config differential."
  exit 0
fi
echo "GRAPHICS_SOURCE_RECONCILIATION=TRACKED_CHANGES_PRESENT"
wc -l "$RUN/renderer-diff.patch"

# New worktrees do not automatically initialize submodules.
STATUS="INITIALIZING_DEPENDENCIES"
git -C "$SOURCE" submodule update --init --recursive --jobs 4

STATUS="CPU_REGRESSIONS"
mkdir -p "$RUN/tests"
for spec in guest_cpuid rdpid_trap_table; do
  g++ -std=c++23 -O2 -pthread -I"$SOURCE/src" \
    "$SOURCE/tests/cpu_id/test_$spec.cpp" -o "$RUN/tests/$spec"
  "$RUN/tests/$spec"
done
g++ -std=c++23 -O2 -I"$SOURCE/src" \
  "$SOURCE/tests/kernel/test_module_filename.cpp" -o "$RUN/tests/kernel_filename"
"$RUN/tests/kernel_filename"

# This is an isolated candidate and is never installed to ES-DE.
STATUS="BUILDING"
cmake -S "$SOURCE" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_TESTS=OFF -DENABLE_CPU_ID_TRANSLATION=OFF
JOBS="$(nproc)"
if (( JOBS > 6 )); then JOBS=6; fi
cmake --build "$BUILD" --target shadps4 --parallel "$JOBS"
test -x "$CANDIDATE"
sha256sum "$CANDIDATE" > "$RUN/candidate-sha256.txt"
if command -v nm >/dev/null 2>&1 && command -v objdump >/dev/null 2>&1; then
  symbol="$(nm -C "$CANDIDATE" | awk 'index($0," T Libraries::Kernel::sceKernelLoadStartModule(") || index($0," t Libraries::Kernel::sceKernelLoadStartModule(") {if (!first) first=$1} END {print first}')"
  if [[ -z "$symbol" ]]; then
    STATUS="HLE_STACK_ALIGNMENT_NOT_VERIFIED"
    echo "Cannot verify HLE guest-to-host stack alignment in built binary."
    exit 4
  fi
  end="$(printf '%x' "$((16#$symbol + 160))")"
  objdump -dC --start-address="0x$symbol" --stop-address="0x$end" "$CANDIDATE" \
     > "$RUN/hle-prologue.txt"
  if ! grep -Eq '[[:space:]]and[[:space:]].*%rsp' "$RUN/hle-prologue.txt"; then
    STATUS="HLE_STACK_ALIGNMENT_NOT_VERIFIED"
    echo "Compiled HLE function lacks required stack alignment."
    exit 4
  fi
else
  STATUS="HLE_TOOLS_MISSING"
  echo "nm/objdump are required to verify the native HLE ABI."
  exit 4
fi
echo "CPU_TESTS=PASS HLE_ABI=PASS DYNAMORIO=OFF"
echo "GRAPHICS_BASELINE=source from independently recorded rendered frames"
STATUS="BUILD_OK"

# A missing SSH X11 authorization is a test-environment blocker, NOT a GPU
# regression. Only use existing Moonlight/Xorg credentials; never restart it.
if command -v xdpyinfo >/dev/null 2>&1; then
  display_ok=0
  for display_try in :0 :1; do
    for cookie in "$HOME/.Xauthority" "/run/user/$(id -u)/Xauthority"; do
      [[ -r "$cookie" ]] || continue
      if env DISPLAY="$display_try" XAUTHORITY="$cookie" \
         timeout 3s xdpyinfo -display "$display_try" >/dev/null 2>&1; then
        export DISPLAY="$display_try"
        export XAUTHORITY="$cookie"
        display_ok=1
        break 2
      fi
    done
  done
  if (( display_ok==0 )); then
    STATUS="BUILT_DISPLAY_NOT_READY"
    echo "Native+graphics candidate ready, but SSH cannot open an existing X11 display."
    echo "No emulator launch attempted; Moonlight/X11 unchanged."
    exit 0
  fi
else
  STATUS="BUILT_DISPLAY_PROBE_MISSING"
  echo "Candidate compiled, but xdpyinfo is missing; skipping gameplay."
  exit 0
fi

# Manual gameplay. Never use the old reference binary, DynamoRIO or scripted
# controller presses. Images alone do not prove performance.
STATUS="PLAYING"
cd "$RUN"
ulimit -c 0 || true
printf 'elapsed_s\tcpu_pct\tmem_pct\trss_kb\tprocess_state\n' > "$RUN/performance.tsv"
env -u SHADPS4_CPU_ID_RESTART "$CANDIDATE" --cpu-id-mode builtin \
  --game CUSA34384 --fullscreen true --show-fps > "$RUN/game-console.log" 2>&1 &
pid=$!
PLAY_EXIT="RUNNING"
EARLY=0
for ((sec=0;sec<180;sec+=5)); do
  psstate="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -z "$psstate" || "$psstate" == Z* ]]; then
    EARLY=1
    echo "GAME_EXITED_AT=$sec"
    break
  fi
  printf '%s\t%s\n' "$sec" \
    "$(LC_ALL=C ps -p "$pid" -o %cpu=,%mem=,rss=,stat= 2>/dev/null || true)" \
    >> "$RUN/performance.tsv"
  if (( sec==20 || sec==55 || sec==100 || sec==150 )); then
    shot="$RUN/screenshots/at-$sec.png"
    if command -v import >/dev/null 2>&1; then
      if timeout 5s import -display "$DISPLAY" -window root "$shot" \
         >/dev/null 2>&1 && [[ -s "$shot" ]]; then
        SHOTS=$((SHOTS+1))
        echo "SCREENSHOT=$shot"
      else
        echo "SCREENSHOT_UNAVAILABLE=$sec (continuing)"
      fi
    fi
  fi
  if (( sec%30==0 )); then echo "PLAY_PROGRESS=$sec"; fi
  sleep 5
done
if (( EARLY==0 )); then
  STATUS="PLAY_WINDOW_COMPLETED"
  kill -TERM "$pid" 2>/dev/null || true
  for ((n=0;n<10;n++)); do
    psstate="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
    if [[ -z "$psstate" || "$psstate" == Z* ]]; then break; fi
    sleep 1
  done
  psstate="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -n "$psstate" && "$psstate" != Z* ]]; then
    STATUS="PLAY_WINDOW_DONE_CANDIDATE_STILL_RUNNING"
    PLAY_EXIT="STILL_RUNNING"
    echo "Test candidate ignored graceful shutdown; no force kill."
    exit 0
  fi
fi
set +e
wait "$pid"
PLAY_EXIT=$?
set -e
if (( EARLY==1 )); then
  if (( PLAY_EXIT==0 )); then STATUS="GAME_EXITED"; else STATUS="GAME_CRASHED"; fi
fi
echo "GAMEPLAY_RESULT=$STATUS EXIT=$PLAY_EXIT"
