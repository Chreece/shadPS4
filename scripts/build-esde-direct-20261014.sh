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
phase="CLI and linker smoke"
if ldd "$build/shadps4" | grep -F 'not found'; then
  echo "MISSING_SHARED_LIBRARY"; exit 1;
fi
timeout 20s "$build/shadps4" --help > "$root/logs/esde-help-$stamp.log" 2>&1 || {
  echo "CLI_SMOKE_FAILED; see $root/logs/esde-help-$stamp.log"; exit 1;
}
echo "SMOKE=PASS (no gameplay tests)"
phase="ES-DE deployment"
launcher="$HOME/Applications/shadps4/shadps4"
if [[ ! -e "$launcher" ]]; then
  status="BUILT_NOT_INSTALLED"; echo "MISSING_ESDE_LAUNCHER=$launcher"; exit 0
fi
target="$(readlink -f "$launcher")"
if [[ "$target" != "$HOME/"* || ! -f "$target" || ! -w "$target" ]]; then
  status="BUILT_NOT_INSTALLED"; echo "UNSAFE_ESDE_TARGET=$target"; exit 0
fi
if pgrep -x shadps4 >/dev/null; then
  status="BUILT_NOT_INSTALLED"; echo "GAME_RUNNING: old ES-DE preserved"; exit 0
fi
runtime="$(dirname "$target")/cpu-id-runtime"
runtime_backup="$runtime.before-direct-$stamp"
backup="$target.before-direct-$stamp"
new_bin="$target.new-$stamp"
new_runtime="$runtime.new-$stamp"
[[ ! -e "$backup" && ! -e "$runtime_backup" &&
   ! -e "$new_bin" && ! -e "$new_runtime" ]] || {
  echo "BACKUP_NAME_EXISTS"; exit 1;
}
install -m755 "$build/shadps4" "$new_bin"
cp -a "$build/cpu-id-runtime" "$new_runtime"
cmp "$build/shadps4" "$new_bin"
cp -a "$target" "$backup"
if pgrep -x shadps4 >/dev/null; then
  status="BUILT_NOT_INSTALLED"; echo "GAME_STARTED_DURING_STAGE"; exit 0
fi
deploy=1
if [[ -e "$runtime" || -L "$runtime" ]]; then
  mv "$runtime" "$runtime_backup"
  runtime_moved=1
fi
mv "$new_runtime" "$runtime"
mv -f "$new_bin" "$target"
cmp "$build/shadps4" "$target"
[[ -x "$runtime/bin64/drrun" && -f "$runtime/libshadps4_cpu_id.so" ]] || exit 1
timeout 20s "$launcher" --help > "$root/logs/esde-installed-help-$stamp.log" 2>&1
deploy=0
status="INSTALLED_PENDING_MANUAL_GAMEPLAY"
echo "INSTALL=PASS; previous executable retained at $backup"
echo "TEST_SUITES=NOT_RUN; AUTOMATIC_X=DISABLED"
