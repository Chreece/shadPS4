#!/usr/bin/env bash
# Build the ALREADY MERGED shadPS4 source for ES-DE. No diagnostics/test suites.
set -Eeuo pipefail
umask 022
root="$HOME/projects/shadps4"
work="$root/ESDE/release-20261014-160446"
src="$work/source"
build="$work/build"
mkdir -p "$root/logs"
stamp="$(date +%Y%m%d-%H%M%S)"
log="$root/logs/esde-direct-$stamp.log"
status="FAILED_NOT_INSTALLED"
phase="preflight"
deploy=0
runtime_moved=0
target=""
backup=""
runtime=""
runtime_backup=""
finish() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && deploy )); then
    echo "DEPLOY_FAILED_ROLLBACK=STARTED"
    if [[ -f "$backup" && -n "$target" ]]; then
      cp -a "$backup" "$target.restore-$stamp" &&
        mv -f "$target.restore-$stamp" "$target" || echo "BINARY_ROLLBACK_FAILED"
    fi
    if [[ -n "$runtime" && -e "$runtime" ]]; then
      mv "$runtime" "$runtime.failed-$stamp" || true
    fi
    if (( runtime_moved )) && [[ -d "$runtime_backup" ]]; then
      mv "$runtime_backup" "$runtime" || echo "RUNTIME_ROLLBACK_FAILED"
    fi
  fi
  echo "RESULT=$status"
  echo "EXIT_CODE=$rc"
  echo "LAST_PHASE=$phase"
  echo "LOG=$log"
  echo "SOURCE=$src"
  echo "BUILD=$build"
  echo "BACKUP=$backup"
  echo "SSH_SESSION=UNCHANGED"
}
trap finish EXIT
exec > >(tee -a "$log") 2>&1
echo "=== SHADPS4 ES-DE DIRECT BUILD / NO TESTS ==="
echo "START=$(date -Is)"
for exe in git cmake ninja timeout sha256sum readlink ldd cmp; do
  command -v "$exe" >/dev/null || { echo "MISSING_COMMAND=$exe"; exit 1; }
done
[[ -d "$src/.git" ]] || { echo "EXISTING_SOURCE_NOT_FOUND"; exit 1; }
[[ "$(git -C "$src" rev-parse HEAD)" == "62925125e31b513c243ab042a7cf97c34872f06f" ]] || {
  echo "UNEXPECTED_SOURCE_COMMIT; refusing to overwrite"; exit 1;
}
[[ -z "$(git -C "$src" status --porcelain --untracked-files=no)" ]] || {
  echo "SOURCE_MODIFIED; refusing to overwrite"; exit 1;
}
git -C "$src" merge-base --is-ancestor f47bbd5d6e9f49d3fd11cb4fd50533a835976a08 HEAD
git -C "$src" merge-base --is-ancestor bde2b3c1fdc8add6b73b24894d4d47837b1f2db3 HEAD
for file in src/core/devtools/quit_dialog.h src/imgui/startup_loading.cpp \
            src/core/cpu_id_translation/build.py; do
  [[ -f "$src/$file" ]] || { echo "MISSING_REQUIRED_SOURCE=$file"; exit 1; }
done
if command -v clang-19 >/dev/null && command -v clang++-19 >/dev/null; then
  cc=clang-19; cxx=clang++-19
elif command -v gcc-14 >/dev/null && command -v g++-14 >/dev/null; then
  cc=gcc-14; cxx=g++-14
else
  echo "TOOLCHAIN_MISSING: need Clang19 or GCC14"; exit 1
fi
echo "COMPILER=$($cxx --version | head -n1)"
echo "FEATURES=upstream+GoW-main, loading-GUI, controller-quit"
echo "GOT_V46_V51_OVERLAYS=NOT_YET_INCLUDED"
echo "AUTOMATIC_INPUT=OFF; GAMEPLAY_SCREENSHOTS=OFF; UNIT_TESTS=OFF"
phase="submodules"
git -C "$src" -c http.version=HTTP/1.1 submodule update --init --recursive --jobs 4
phase="CMake Release configure"
cmake -S "$src" -B "$build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="$cc" -DCMAKE_CXX_COMPILER="$cxx" \
  -DENABLE_CPU_ID_TRANSLATION=ON -DENABLE_TESTS=OFF -DENABLE_UPDATER=OFF
phase="Ninja compile (NO TESTS)"
cmake --build "$build" --target shadps4 --parallel 5
[[ -x "$build/shadps4" ]] || { echo "BUILD_BINARY_MISSING"; exit 1; }
[[ -x "$build/cpu-id-runtime/bin64/drrun" &&
   -f "$build/cpu-id-runtime/libshadps4_cpu_id.so" ]] || {
  echo "CPU_ID_RUNTIME_MISSING"; exit 1;
}
echo "COMPILE=PASS"
sha256sum "$build/shadps4"
phase="build completed"
status="BUILT_NOT_INSTALLED"
echo "BUILD_STATUS=COMPLETE"
echo "NEW_BINARY=$build/shadps4"
echo "CPU_ID_RUNTIME=$build/cpu-id-runtime"
echo "ESDE_DEPLOYMENT=SKIPPED"
echo "WHY=GoW Phase84 runtime SRT planner and GoT v46/v51 overlays have not been reconciled."
echo "PROTECTED=existing ES-DE executable, GoW/GoT workspaces, game saves, SSH."
echo "NO_TEST_SUITES_OR_AUTOMATED_INPUT_WERE_EXECUTED"
