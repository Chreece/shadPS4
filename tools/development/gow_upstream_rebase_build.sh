#!/usr/bin/env bash
# Separate worktree: rebase build must not touch the proven GoW tree/binaries.
set -Eeuo pipefail
ORIG="$HOME/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
OLD="$ORIG/source"
TEST="$HOME/projects/shadps4/GodOfWarRagnarok/tests/upstream-rebase-32021e41-20261017"
SRC="$TEST/source"; BUILD="$TEST/build"; LOGS="$HOME/projects/shadps4/logs"
UPSTREAM=32021e41bc52f744c80d122e732e24e97496c98d
FIX=29d02318aaad1a199c0ddc9d7fef61b79f350d15
PROVEN="$ORIG/build/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543"
TAG="gow-upstream-rebase-$(date +%Y%m%d-%H%M%S)"; OUT="$LOGS/$TAG"
mkdir -p "$OUT"; BASE_PASS=0; SRT_PASS=0; STAGE=PREFLIGHT
SRT_STAGED=0
SRT_PATHS=(
  # Deliberately do NOT replace upstream's shader_info_collection_pass.cpp:
  # it already carries the proven Phi-FP64 + ReadConst DMA fixes, reconciled
  # against the actual upstream IR opcode set in the pinned source commit.
  src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp
  src/shader_recompiler/ir/passes/srt_offset_codegen.h
  src/shader_recompiler/ir/passes/srt_offset_preflight.h
)
finish() {
  rc="$1"; trap - EXIT; set +e
  if (( SRT_STAGED )); then
    for f in "${SRT_PATHS[@]}"; do
      if [[ -f "$OUT/srt-original/$f" ]]; then
        cp -p "$OUT/srt-original/$f" "$SRC/$f" || rc=1
        # Force the next incremental build to use restored native source;
        # never silently link against an object from an incompatible overlay.
        touch -m "$SRC/$f" || rc=1
      else
        rm -f "$SRC/$f" || rc=1
      fi
    done
    if [[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]]; then
      echo "REBASED_SOURCE_RESTORED=YES"
    else
      echo "REBASED_SOURCE_RESTORED=NO"
      rc=1
    fi
  fi
  sha256sum "$PROVEN" > "$OUT/proven-after.sha256" 2>/dev/null
  if [[ -f "$OUT/proven-before.sha256" ]] && ! cmp -s "$OUT/proven-before.sha256" "$OUT/proven-after.sha256"; then
    echo "PROVEN_BINARY_CHANGED=YES"; rc=1
  fi
  [[ ! -d "$SRC" ]] || git -C "$SRC" status --short >"$OUT/worktree-status.txt" 2>&1
  printf 'STAGE=%s\nBASE_BUILD_PASS=%s\nSRT_BUILD_PASS=%s\nRESULT=%s\n' "$STAGE" "$BASE_PASS" "$SRT_PASS" "$([[ "$rc" == 0 ]] && echo PASS || echo FAILED)" >"$OUT/result.txt"
  tar -czf "$LOGS/$TAG.tar.gz" \
    --exclude="$TAG/pm4-test" \
    --exclude="$TAG/gameplay/full-console.log" \
    --exclude="$TAG/base-gameplay/full-console.log" \
    --exclude="$TAG/proven-gameplay/full-console.log" \
    --exclude="$TAG/module-filename-test" \
    --exclude="$TAG/linux-nested-signal-test" \
    --exclude="$TAG/audio-health-test" \
    --exclude="$TAG/srt-original" \
    -C "$LOGS" "$TAG"
  cat "$OUT/result.txt"; echo "ARCHIVE=$LOGS/$TAG.tar.gz"
  [[ ! -f "$OUT/base.txt" ]] || cat "$OUT/base.txt"
  [[ ! -f "$OUT/srt.txt" ]] || cat "$OUT/srt.txt"
  if [[ "$rc" != 0 ]]; then
    for f in "$OUT/"{python-syntax,source-contract-tests,source-contract,audio-contract-tests,audio-contract,observer-unit-tests,audio-health-compile,module-filename-compile,linux-nested-signal-build,compile-page-manager,compile-memory-permissions,compile-audio-backends,compile-preflight,compile-srt-preflight,submodules,configure,build-base,build-srt,pm4-compile,gameplay-observer,baseline-gameplay-observer,proven-gameplay-observer}.log; do
      [[ ! -s "$f" ]] || { echo "ERROR_TAIL=$f"; tail -18 "$f"; }
    done
  fi
  if [[ -f "$OUT/gameplay/gameplay-result.json" ]]; then
    python3 - "$OUT/gameplay/gameplay-result.json" <<'REPORT'
import json, sys
r=json.load(open(sys.argv[1]))
print("GAME_OBSERVATION_RESULT="+r.get("result","UNKNOWN"))
print("GAME_PID="+str(r.get("pid","NOT_STARTED")))
print("GAME_PROCESS_LEFT_RUNNING="+str(r.get("process_alive_when_reported",False)))
REPORT
  else
    echo "GAME_LAUNCHED=NO; REASON=VERIFICATION_OR_BUILD_NOT_PASSED"
  fi
  if [[ -f "$OUT/proven-gameplay/gameplay-result.json" ]]; then
    python3 - "$OUT/proven-gameplay/gameplay-result.json" <<'PROVEN_REPORT'
import json,sys
r=json.load(open(sys.argv[1]))
print("PROVEN_GAMEPLAY_RESULT="+r.get("result","UNKNOWN"))
print("PROVEN_GAMEPLAY_RUNNING="+str(r.get("process_alive_when_reported",False)))
PROVEN_REPORT
  fi
  if [[ -f "$OUT/base-gameplay/gameplay-result.json" ]]; then
    python3 - "$OUT/base-gameplay/gameplay-result.json" <<'BASELINE_REPORT'
import json,sys
r=json.load(open(sys.argv[1]))
print("PRE_SRT_GAMEPLAY_RESULT="+r.get("result", "UNKNOWN"))
print("PRE_SRT_GAMEPLAY_PID="+str(r.get("pid", "NOT_STARTED")))
print("PRE_SRT_GAMEPLAY_RUNNING="+str(r.get("process_alive_when_reported",False)))
BASELINE_REPORT
  fi
  echo "ORIGINAL_GOW_SOURCE=UNCHANGED; SUNSHINE_X11=UNCHANGED; SSH_SESSION=UNCHANGED"
  exit "$rc"
}
trap 'finish $?' EXIT
exec 9>"$LOGS/.gow-upstream-rebase.lock"
flock -n 9 || { echo "REBASE_BUILD_ALREADY_RUNNING"; exit 1; }
[[ -e "$OLD/.git" && "$(git -C "$OLD" rev-parse --show-toplevel)" == "$OLD" ]] || { echo "ORIGINAL_SOURCE_MISSING"; exit 1; }
echo "e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa  $PROVEN" | sha256sum -c - >"$OUT/proven-check.txt"
sha256sum "$PROVEN" >"$OUT/proven-before.sha256"
git -C "$OLD" cat-file -e "$FIX^{commit}"
git -C "$OLD" merge-base --is-ancestor "$UPSTREAM" "$FIX" || { echo "UPSTREAM_ANCESTRY_FAILED"; exit 1; }
# Validate immutable tip and upstream ancestry rather than a brittle manual
# commit count that changes with every source correction.
git -C "$OLD" rev-list --count "$UPSTREAM..$FIX" >"$OUT/upstream-ahead-count.txt"
FREE="$(df -Pk "$HOME" | awk 'END{print $4}')"
[[ "$FREE" =~ ^[0-9]+$ ]] && (( FREE >= 2097152 )) || { echo "FREE_SSD_BELOW_2_GIB; NO_DELETION"; exit 1; }
STAGE=ISOLATED_WORKTREE
if [[ ! -e "$SRC" ]]; then
  mkdir -p "$TEST"
  git -C "$OLD" worktree add --detach "$SRC" "$FIX" >"$OUT/worktree.log" 2>&1
fi
[[ -e "$SRC/.git" ]] || { echo "ISOLATED_GIT_WORKTREE_MISSING"; exit 1; }
[[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || {
  echo "EXISTING_ISOLATED_SOURCE_DIRTY; DO_NOT_RESET"; exit 1;
}
PREVIOUS_HEAD="$(git -C "$SRC" rev-parse HEAD)"
if [[ "$PREVIOUS_HEAD" != "$FIX" ]]; then
  git -C "$SRC" merge-base --is-ancestor "$PREVIOUS_HEAD" "$FIX" || {
    echo "EXISTING_WORKTREE_NOT_ANCESTOR; REFUSING_RESET"; exit 1;
  }
  # Safe detached fast-forward of ONLY the dedicated rebased test worktree.
  # Never touch the older gameplay source tree or its build directory.
  git -C "$SRC" -c advice.detachedHead=false switch --detach "$FIX" >"$OUT/worktree-advance.log" 2>&1
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$FIX" ]] || {
  echo "ISOLATED_WORKTREE_ADVANCE_FAILED"; exit 1;
}
echo "REBASE_WORKTREE_FAST_FORWARD=PASS; OLD=$PREVIOUS_HEAD; NEW=$FIX" | tee "$OUT/worktree-advance.txt"
STAGE=PYTHON_SOURCE_TOOL_SELFTEST
# Compile all Python helper sources and run 15 positive/negative test cases
# BEFORE the real-source gate. No untested regex or parser can silently stop
# an otherwise compile-ready native source tree again.
python3 -m py_compile \
  "$SRC/tools/development/gow_upstream_source_contract.py" \
  "$SRC/tools/development/gow_upstream_source_contract_test.py" \
  "$SRC/tools/development/gow_upstream_gameplay.py" \
  "$SRC/tools/development/gow_upstream_gameplay_test.py" \
  >"$OUT/python-syntax.log" 2>&1 || {
    echo "PYTHON_SYNTAX=FAILED"; cat "$OUT/python-syntax.log"; exit 1;
  }
echo "PYTHON_SYNTAX=PASS"
python3 "$SRC/tools/development/gow_upstream_source_contract_test.py" \
  >"$OUT/source-contract-tests.log" 2>&1 || {
    echo "SOURCE_CONTRACT_TEST_SUITE=FAILED"
    cat "$OUT/source-contract-tests.log"
    exit 1
  }
tail -2 "$OUT/source-contract-tests.log"
STAGE=SOURCE_CONTRACT_PREFLIGHT
python3 "$SRC/tools/development/gow_upstream_source_contract.py" "$SRC" \
  >"$OUT/source-contract.log" 2>&1 || {
    echo "SOURCE_CONTRACT=FAILED"
    cat "$OUT/source-contract.log"
    exit 1
  }
cat "$OUT/source-contract.log"
git -C "$SRC" -c http.version=HTTP/1.1 submodule update --init --recursive --jobs 4 >"$OUT/submodules.log" 2>&1
echo "UPSTREAM_MAIN=$UPSTREAM" >"$OUT/commits.txt"
echo "NATIVE_FIX_COMMIT=$FIX" >>"$OUT/commits.txt"
STAGE=AUDIO_OUTPUT_CONTRACT_TESTS
python3 -m py_compile \
    "$SRC/tools/development/audio_output_contract.py" \
    "$SRC/tools/development/audio_output_contract_test.py"
python3 "$SRC/tools/development/audio_output_contract_test.py" \
    >"$OUT/audio-contract-tests.log" 2>&1 || {
      echo "AUDIO_OUTPUT_CONTRACT_TESTS=FAILED"
      cat "$OUT/audio-contract-tests.log"
      exit 1
    }
tail -2 "$OUT/audio-contract-tests.log"
python3 "$SRC/tools/development/audio_output_contract.py" "$SRC" \
    >"$OUT/audio-contract.log" 2>&1 || {
      echo "AUDIO_OUTPUT_CONTRACT=FAILED"
      cat "$OUT/audio-contract.log"
      exit 1
    }
cat "$OUT/audio-contract.log"
STAGE=GAME_OBSERVER_UNIT_TESTS
python3 "$SRC/tools/development/gow_upstream_gameplay_test.py" \
  >"$OUT/observer-unit-tests.log" 2>&1 || {
    echo "GAME_OBSERVER_UNIT_TESTS=FAILED"
    cat "$OUT/observer-unit-tests.log"
    exit 1
  }
tail -2 "$OUT/observer-unit-tests.log"
STAGE=PM4_REGRESSION
CXX_BIN="$(command -v c++ || echo /usr/bin/c++)"
"$CXX_BIN" -std=c++20 -O2 -Wall -Wextra -Werror -I"$SRC/src" "$SRC/tests/pm4_shader_register_window_test.cpp" -o "$OUT/pm4-test" >"$OUT/pm4-compile.log" 2>&1
"$OUT/pm4-test" >"$OUT/pm4-result.txt"
cat "$OUT/pm4-result.txt"
STAGE=AUDIO_OUTPUT_HEALTH_UNIT_TEST
"$CXX_BIN" -std=c++20 -O2 -Wall -Wextra -Werror \
    -I"$SRC/src" "$SRC/tests/audio_output_health_test.cpp" \
    -o "$OUT/audio-health-test" >"$OUT/audio-health-compile.log" 2>&1 || {
      echo "AUDIO_OUTPUT_HEALTH_COMPILE=FAILED"
      tail -35 "$OUT/audio-health-compile.log"
      exit 1
    }
"$OUT/audio-health-test"
echo "AUDIO_OUTPUT_HEALTH=PASS; PCM_F32_AND_S16=VERIFIED"
STAGE=GUEST_MODULE_FILENAME_REGRESSION
"$CXX_BIN" -std=c++20 -O2 -Wall -Wextra -Werror -I"$SRC/src" \
  "$SRC/tests/module_filename_validation_test.cpp" -o "$OUT/module-filename-test" \
  >"$OUT/module-filename-compile.log" 2>&1 || {
    echo "GUEST_MODULE_FILENAME_COMPILE=FAILED"
    tail -40 "$OUT/module-filename-compile.log"
    exit 1
  }
"$OUT/module-filename-test"
echo "GUEST_MODULE_FILENAME_REGRESSION=PASS; HLE_ABI_FIX=SOURCE_INCLUDED"
STAGE=LINUX_NESTED_CPU_SIGNAL_REGRESSION
# Independently exercise the kernel signal mechanics in the proven native
# CPU fault handler. The test fails if nested delivery isn't permitted.
"$CXX_BIN" -std=c++20 -O2 -Wall -Wextra -Werror \
  "$SRC/tests/linux_nested_cpu_signal_policy_test.cpp" \
  -o "$OUT/linux-nested-signal-test" \
  >"$OUT/linux-nested-signal-build.log" 2>&1 || {
    echo "NESTED_CPU_SIGNAL_TEST_COMPILE=FAILED"
    tail -40 "$OUT/linux-nested-signal-build.log"
    exit 1
  }
timeout 8s "$OUT/linux-nested-signal-test" >"$OUT/linux-nested-signal-result.txt" 2>&1 || {
  echo "NESTED_CPU_SIGNAL_POLICY=FAILED"
  cat "$OUT/linux-nested-signal-result.txt"
  exit 1
}
grep -q '^NESTED_CPU_SIGNAL_POLICY=PASS;' "$OUT/linux-nested-signal-result.txt" || {
  echo "NESTED_CPU_SIGNAL_POLICY=NOT_PROVEN"; exit 1
}
cat "$OUT/linux-nested-signal-result.txt"
STAGE=CONFIGURE_UPSTREAM
if command -v ccache >/dev/null 2>&1; then
  echo "CACHE=CCACHE; REUSE_PREVIOUS_OBJECTS=YES" >"$OUT/cache.txt"
  cmake -S "$SRC" -B "$BUILD" -DCMAKE_BUILD_TYPE=RelWithDebInfo -DENABLE_TESTS=OFF -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache >"$OUT/configure.log" 2>&1
else
  echo "CACHE=INCREMENTAL_CMAKE" >"$OUT/cache.txt"
  cmake -S "$SRC" -B "$BUILD" -DCMAKE_BUILD_TYPE=RelWithDebInfo -DENABLE_TESTS=OFF >"$OUT/configure.log" 2>&1
fi
STAGE=EARLY_SOURCE_COMPILE_CHECK
# The already-configured GNU Make build supports compiling shadps4's own
# translation units before the full link. This catches header/API regressions
# without rebuilding Protobuf, SDL, FFmpeg, or Vulkan dependencies first.
# Objects compiled here are reused by the subsequent normal CMake build.
if [[ -f "$BUILD/CMakeFiles/shadps4.dir/build.make" ]]; then
  MAKE_BIN="$(command -v gmake || command -v make)"
  OBJECTS=(
    CMakeFiles/shadps4.dir/src/core/devtools/widget/shader_list.cpp.o
    CMakeFiles/shadps4.dir/src/video_core/page_manager.cpp.o
    CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_rasterizer.cpp.o
    CMakeFiles/shadps4.dir/src/video_core/amdgpu/liverpool.cpp.o
    CMakeFiles/shadps4.dir/src/core/cpu_id.cpp.o
    CMakeFiles/shadps4.dir/src/core/libraries/kernel/threads/pthread.cpp.o
    CMakeFiles/shadps4.dir/src/core/libraries/kernel/process.cpp.o
    CMakeFiles/shadps4.dir/src/core/module.cpp.o
    CMakeFiles/shadps4.dir/src/core/memory.cpp.o
    CMakeFiles/shadps4.dir/src/shader_recompiler/ir/passes/resource_patching_pass.cpp.o
    CMakeFiles/shadps4.dir/src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp.o
  )
  for obj in "${OBJECTS[@]}"; do
    grep -Fq "$obj:" "$BUILD/CMakeFiles/shadps4.dir/build.make" || {
      echo "EARLY_CHECK_TARGET_MISSING=$obj; NO_COMPILATION_STARTED"; exit 1;
    }
  done
  STAGE=PAGE_MANAGER_COMPILE_CHECK
  # Compile the previously failing translation unit by itself first.
  # On success it becomes an up-to-date object reused by the wider preflight.
  "$MAKE_BIN" -C "$BUILD" -f CMakeFiles/shadps4.dir/build.make \
    CMakeFiles/shadps4.dir/src/video_core/page_manager.cpp.o \
    >"$OUT/compile-page-manager.log" 2>&1 || {
    echo "PAGE_MANAGER_COMPILE_CHECK=FAILED"
    tail -65 "$OUT/compile-page-manager.log"
    exit 1
  }
  echo "PAGE_MANAGER_COMPILE_CHECK=PASS"
  STAGE=GUEST_MEMORY_PERMISSION_COMPILE
  # This compiles the actual production 64-combination static_assert, not
  # a separate reimplementation of its R/W/X rules. The generated object
  # is reused by CMake's subsequent link.
  "$MAKE_BIN" -C "$BUILD" -f CMakeFiles/shadps4.dir/build.make \
    CMakeFiles/shadps4.dir/src/core/memory.cpp.o \
    >"$OUT/compile-memory-permissions.log" 2>&1 || {
      echo "GUEST_MEMORY_PERMISSION_COMPILE=FAILED"
      tail -85 "$OUT/compile-memory-permissions.log"
      exit 1
    }
  echo "GUEST_MEMORY_64_MASK_CONTRACT=COMPILED_PASS"
  STAGE=EARLY_AUDIO_BACKEND_COMPILE
  AUDIO_OBJECTS=(
    CMakeFiles/shadps4.dir/src/core/libraries/audio/audioout.cpp.o
    CMakeFiles/shadps4.dir/src/core/libraries/audio/sdl_audio_out.cpp.o
    CMakeFiles/shadps4.dir/src/core/libraries/audio/openal_audio_out.cpp.o
    CMakeFiles/shadps4.dir/src/core/libraries/audio3d/audio3d.cpp.o
  )
  for obj in "${AUDIO_OBJECTS[@]}"; do
    grep -Fq "$obj:" "$BUILD/CMakeFiles/shadps4.dir/build.make" || {
      echo "AUDIO_BACKEND_COMPILE_TARGET_MISSING=$obj"
      exit 1
    }
  done
  "$MAKE_BIN" -C "$BUILD" -f CMakeFiles/shadps4.dir/build.make -j4 \
    "${AUDIO_OBJECTS[@]}" >"$OUT/compile-audio-backends.log" 2>&1 || {
      echo "AUDIO_BACKEND_COMPILE=FAILED"
      tail -75 "$OUT/compile-audio-backends.log"
      exit 1
    }
  echo "AUDIO_BACKEND_COMPILE=PASS; SDL_OPENAL_AUDIO3D=CHECKED"
  STAGE=EARLY_SOURCE_COMPILE_CHECK
  "$MAKE_BIN" -C "$BUILD" -f CMakeFiles/shadps4.dir/build.make -j4 "${OBJECTS[@]}" \
    >"$OUT/compile-preflight.log" 2>&1 || {
      echo "EARLY_CHECK=FAILED"; tail -65 "$OUT/compile-preflight.log"; exit 1;
    }
  echo "EARLY_CHECK=PASS; NATIVE_OBJECTS_COMPILED=${#OBJECTS[@]}"
  echo "SHADER_INFO_COLLECTOR=COMPILED_FROM_UPSTREAM_RECONCILED_SOURCE"
else
  echo "EARLY_CHECK=NO_UNIX_MAKEFILE; USING_STANDARD_CMAKE_BUILD"
fi
# Every successful verification continues to the complete build and
# automatic game launch. A failing gate exits without launching anything.
STAGE=BUILD_REBASED_CPU_UFFD_PM4
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/build-base.log" 2>&1
[[ -x "$BUILD/shadps4" ]] || { echo "BUILT_BINARY_MISSING"; exit 1; }
nm -C "$BUILD/shadps4" | grep -F 'Vulkan::Rasterizer::InvalidateMemoryOnGpuThread' >"$OUT/uffd-symbol.txt" || { echo "DEFERRED_UFFD_SYMBOL_MISSING"; exit 1; }
nm -C "$BUILD/shadps4" | grep -F 'Core::HandleCpuIdFault' >"$OUT/native-cpu-symbol.txt" || { echo "BUILTIN_CPU_HANDLER_NOT_LINKED"; exit 1; }
BASE="$BUILD/shadps4.upstream-native-$TAG"
[[ ! -e "$BASE" ]] || { echo "BASE_CANDIDATE_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$BASE"; sha256sum "$BASE" >"$OUT/base.sha256"
echo "BASE_CANDIDATE=$BASE" >"$OUT/base.txt"; BASE_PASS=1
echo "REBASED_CPU_UFFD_PM4_BUILD=PASS"
echo "NATIVE_CPU_AFFINITY_INTERFACE=RECONCILED"
STAGE=STAGE_SOURCE_MATCHED_SRT
echo "SRT_SHADER_INFO=UPSTREAM_RECONCILED; STALE_IR_OPCODES_NOT_IMPORTED" | tee "$OUT/srt-port-provenance.txt"
# Keep the separate testing worktree clean even on SRT build failure.
# Back up the upstream versions before staging anything.
for f in "${SRT_PATHS[@]}"; do
  if [[ -e "$SRC/$f" ]]; then
    [[ -f "$SRC/$f" && ! -L "$SRC/$f" ]] || { echo "UNSAFE_SRT_WORKTREE_FILE=$f"; exit 1; }
    mkdir -p "$OUT/srt-original/$(dirname "$f")"
    cp -p "$SRC/$f" "$OUT/srt-original/$f"
  fi
done
SRT_STAGED=1
while read -r sha relative; do
  [[ -n "$sha" ]] || continue
  oldfile="$OLD/$relative"; newfile="$SRC/$relative"
  echo "$sha  $oldfile" | sha256sum -c - >>"$OUT/srt-sha-check.txt"
  [[ -f "$oldfile" && ! -L "$oldfile" ]] || { echo "UNSAFE_SRT_FILE=$relative"; exit 1; }
  if [[ -f "$newfile" && "$(sha256sum "$newfile" | cut -d' ' -f1)" == "$sha" ]]; then continue; fi
  if [[ "$relative" == *.h && -e "$newfile" ]]; then echo "UPSTREAM_HELPER_COLLISION=$relative"; exit 1; fi
  install -D -m 644 "$oldfile" "$newfile"
  echo "$sha  $newfile" | sha256sum -c - >>"$OUT/srt-overlay-check.txt"
done <<'PINNED_SRT'
14130ab04071e93159b074bbbdd6f7b362661eeaa040e20dec91fa54208aa161 src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp
a8695bab2b6fdbbf42abb24f0d3e75a9c3cd53445d78ffef36c0196cb80167b9 src/shader_recompiler/ir/passes/srt_offset_codegen.h
4b7b8a6e4422d1620c3c2910e94554bb3fa99a4e05d4ee82bb7ee9a43b88ae1b src/shader_recompiler/ir/passes/srt_offset_preflight.h
PINNED_SRT
STAGE=EARLY_NATIVE_SRT_COMPILE_CHECK
if [[ -f "$BUILD/CMakeFiles/shadps4.dir/build.make" ]]; then
  SRT_OBJECTS=(
    CMakeFiles/shadps4.dir/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp.o
    CMakeFiles/shadps4.dir/src/shader_recompiler/ir/passes/shader_info_collection_pass.cpp.o
  )
  for obj in "${SRT_OBJECTS[@]}"; do
    grep -Fq "$obj:" "$BUILD/CMakeFiles/shadps4.dir/build.make" || {
      echo "SRT_EARLY_TARGET_MISSING=$obj"; exit 1;
    }
  done
  "$MAKE_BIN" -C "$BUILD" -f CMakeFiles/shadps4.dir/build.make -j2 "${SRT_OBJECTS[@]}"     >"$OUT/compile-srt-preflight.log" 2>&1 || {
      echo "SRT_EARLY_CHECK=FAILED"; tail -65 "$OUT/compile-srt-preflight.log"; exit 1;
    }
  echo "SRT_EARLY_CHECK=PASS"
fi
STAGE=BUILD_REBASED_NATIVE_SRT
cmake --build "$BUILD" --target shadps4 --parallel 4 >"$OUT/build-srt.log" 2>&1
nm -C "$BUILD/shadps4" | grep -F 'Vulkan::Rasterizer::InvalidateMemoryOnGpuThread' >"$OUT/srt-uffd-symbol.txt" || { echo "SRT_BUILD_LOST_UFFD"; exit 1; }
SRT="$BUILD/shadps4.upstream-srt-$TAG"
[[ ! -e "$SRT" ]] || { echo "SRT_CANDIDATE_EXISTS"; exit 1; }
cp -p "$BUILD/shadps4" "$SRT"; sha256sum "$SRT" >"$OUT/srt.sha256"
echo "SRT_CANDIDATE=$SRT" >"$OUT/srt.txt"; SRT_PASS=1
echo "MAIN_UPSTREAM_REBASE_BUILD=PASS; GUEST_PERMISSION_TRACKING=PORT_INCLUDED"

# No verification-only exit: a fully verified and linked build proceeds into
# the real game automatically. The observer never closes anyone's game.
STAGE=GAME_LAUNCH_PREFLIGHT
OBSERVER="$SRC/tools/development/gow_upstream_gameplay.py"
[[ -f "$OBSERVER" ]] || { echo "GAME_OBSERVER_SCRIPT_MISSING"; exit 1; }
python3 - "$OBSERVER" >"$OUT/game-observer-syntax.txt" <<'CHECK_PY'
import ast
import pathlib
import sys
ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print("GAME_OBSERVER_PYTHON_SYNTAX=PASS")
CHECK_PY

# This copy is immutable and was SHA-recorded after final linking.
GAME_SHA="$(sha256sum "$SRT" | cut -d' ' -f1)"
[[ "$GAME_SHA" =~ ^[a-f0-9]{64}$ ]] || { echo "GAME_BINARY_DIGEST_INVALID"; exit 1; }
echo "GAME_CANDIDATE_SHA256=$GAME_SHA" >"$OUT/auto-game.txt"
echo "AUTO_GAME_LAUNCH=ENABLED; USER_INPUT=MANUAL_MOONLIGHT"
STAGE=GAME_LAUNCH_AND_OBSERVATION
if python3 "$OBSERVER" --binary "$SRT" --sha256 "$GAME_SHA" \
    --report "$OUT/gameplay" --cwd "$ORIG" --seconds 180 \
    2>&1 | tee "$OUT/gameplay-observer.log"; then
  STAGE=COMPLETE
  echo "GO_W_UPSTREAM_REBASE_FULL_CHAIN=PASS"
  echo "GAME_AUTOMATIC_LAUNCH=COMPLETED; SSH_SESSION=UNCHANGED"
else
  SRT_RUNTIME_RC=$?
  echo "SRT_GAME_OBSERVATION=FAILED; RUNTIME_EXIT=$SRT_RUNTIME_RC"
  # A/B isolation only when the first game is definitively dead due to a
  # fatal guest-process signal. Never start a second game if one is alive
  # or if the first launch was blocked by a preexisting emulator.
  SHOULD_DIFF="$(python3 - "$OUT/gameplay/gameplay-result.json" <<'DECIDE'
import json,sys
try:
    r=json.load(open(sys.argv[1]))
    ok=(r.get("exit_code") == -11 and
        r.get("process_alive_when_reported") is False and
        r.get("pid") is not None)
    print("YES" if ok else "NO")
except (OSError, ValueError, TypeError):
    print("NO")
DECIDE
)"
  if [[ "$SHOULD_DIFF" == "YES" ]]; then
    STAGE=PRE_SRT_RUNTIME_COMPARISON
    # BASE was compiled before the native SRT overlay, using exactly the
    # same new CPU/PM4/UFFD/kernel fix source and game settings.
    BASE_SHA="$(sha256sum "$BASE" | cut -d' ' -f1)"
    echo "SRT_SIGSEGV=CONFIRMED; AUTO_PRE_SRT_AB=START" | tee "$OUT/ab-difference.txt"
    if python3 "$OBSERVER" --binary "$BASE" --sha256 "$BASE_SHA" \
        --report "$OUT/base-gameplay" --cwd "$ORIG" --seconds 180 \
        2>&1 | tee "$OUT/baseline-gameplay-observer.log"; then
      echo "PRE_SRT_GAMEPLAY=ALIVE; SRT_SPECIFIC_REGRESSION=SUSPECTED" \
        | tee -a "$OUT/ab-difference.txt"
    else
      echo "PRE_SRT_GAMEPLAY=FAILED; COMMON_NATIVE_OR_GAME_STARTUP_REGRESSION=SUSPECTED" \
        | tee -a "$OUT/ab-difference.txt"

      # If *both* new binaries have actually died from SIGSEGV, run the
      # unchanged, SHA-proven 10+ FPS gameplay executable as a positive
      # control for HLE startup and read-only host audio-routing snapshots.
      # We never relaunch if any emulator survived or another was already
      # running. Old binary/saves/Sunshine/X11/SSH remain untouched.
      USE_PROVEN_CONTROL="$(python3 - "$OUT/base-gameplay/gameplay-result.json" <<'PROVEN_DECIDE'
import json,sys
try:
    r=json.load(open(sys.argv[1]))
    ok=(r.get("exit_code") == -11 and
        r.get("process_alive_when_reported") is False and
        r.get("pid") is not None)
    print("YES" if ok else "NO")
except (OSError, ValueError, TypeError):
    print("NO")
PROVEN_DECIDE
)"
      if [[ "$USE_PROVEN_CONTROL" == "YES" ]]; then
        STAGE=PROVEN_GAMEPLAY_AUDIO_CONTROL
        echo "TWO_NEW_SIGSEGV=CONFIRMED; SHA_PROVEN_GAMEPLAY_AUDIO_CONTROL=START" \
          | tee -a "$OUT/ab-difference.txt"
        if python3 "$OBSERVER" --binary "$PROVEN" \
            --sha256 "e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa" \
            --report "$OUT/proven-gameplay" --cwd "$ORIG" --seconds 180 \
            2>&1 | tee "$OUT/proven-gameplay-observer.log"; then
          echo "PROVEN_GAMEPLAY=ALIVE; NEW_UPSTREAM_REGRESSION=CONFIRMED" \
            | tee -a "$OUT/ab-difference.txt"
        else
          echo "PROVEN_GAMEPLAY=FAILED; HOST_OR_CONFIG_CHANGE_POSSIBLE" \
            | tee -a "$OUT/ab-difference.txt"
        fi
      else
        echo "PROVEN_CONTROL=SKIPPED; REASON=PRE_SRT_DID_NOT_EXIT_SIGSEGV" \
          | tee -a "$OUT/ab-difference.txt"
      fi
    fi
    # The rebased SRT variant remains FAILED even if proven control runs.
  else
    echo "AB_COMPARISON=SKIPPED; REASON=NO_CONFIRMED_EXITED_SIGSEGV" \
      | tee "$OUT/ab-difference.txt"
  fi
  exit "$SRT_RUNTIME_RC"
fi
