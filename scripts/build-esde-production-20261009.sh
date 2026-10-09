#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

stamp="$(date +%Y%m%d-%H%M%S)"
root="$HOME/shadps4-esde-verified-builds"
src="$root/20261009-173836/source"
bld="$root/20261009-174801/build"
old_candidate="9e81877f61837d1423e5dc1fbd6c60fd005e8524"
old_upstream="30d82c9003480b991092279f7a10c2a3d2fe03b8"
trial="$HOME/Applications/shadps4-trial-upstream-$stamp"
ev="$root/esde-release-$stamp"
log="$ev/build-and-deploy.log"
report="$HOME/shadps4-esde-release-$stamp.tar.gz"
mkdir -p "$ev"
step=preflight
outcome=FAILED
deploying=0
install_target=""
backup_binary=""
runtime_path=""
runtime_backup=""
old_runtime_moved=0

finish() {
  rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && deploying == 1 )); then
    echo "DEPLOY_FAILED: attempting rollback to original shadPS4"
    if [[ -f "$backup_binary" && -n "$install_target" ]]; then
      cp -a -- "$backup_binary" "$install_target.restore-$stamp" &&
        mv -f -- "$install_target.restore-$stamp" "$install_target" ||
        echo "ERROR: binary rollback failed; original backup remains: $backup_binary"
    fi
    if [[ -n "$runtime_path" && ( -e "$runtime_path" || -L "$runtime_path" ) ]]; then
      mv -- "$runtime_path" "$runtime_path.failed-$stamp" ||
        echo "ERROR: couldn't quarantine newly installed CPU runtime"
    fi
    if ((old_runtime_moved == 1)) && [[ -e "$runtime_backup" || -L "$runtime_backup" ]]; then
      mv -- "$runtime_backup" "$runtime_path" ||
        echo "ERROR: CPU runtime rollback failed; backup remains: $runtime_backup"
    fi
  fi
  {
    echo "RESULT=$outcome"
    echo "EXIT_CODE=$rc"
    echo "LAST_STEP=$step"
    echo "SOURCE=$src"
    echo "SOURCE_HEAD=$(git -C "$src" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "UPSTREAM_MAIN=$(git -C "$src" rev-parse refs/remotes/origin/main 2>/dev/null || echo unknown)"
    echo "TRIAL=$trial"
    echo "INSTALLED=$install_target"
    echo "BACKUP_BINARY=$backup_binary"
    echo "BACKUP_RUNTIME=$runtime_backup"
    echo "SSH_SESSION=UNCHANGED"
  } > "$ev/summary.txt"
  tail -n 1500 "$log" > "$ev/build-log-tail.txt" 2>/dev/null || true
  tar -czf "$report" -C "$root" "$(basename "$ev")" 2>/dev/null || true
  echo
  echo "RESULT=$outcome"
  echo "STEP=$step"
  echo "REPORT=$report"
  echo "INSTALLED_BINARY=$install_target"
  echo "SSH_SESSION=UNCHANGED"
}
trap finish EXIT
exec > >(tee "$log") 2>&1

echo "=== shadPS4 latest-main ES-DE production build ==="
echo "DATE=$(date -Is)"
for cmd in git cmake ninja timeout cmp sha256sum readlink pgrep awk od; do
  command -v "$cmd" >/dev/null || { echo "MISSING_DEPENDENCY=$cmd"; exit 1; }
done
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || {
  echo 'Requires Linux x86-64'; exit 1;
}
[[ -d "$src/.git" && -d "$bld" && -x "$bld/shadps4" ]] || {
  echo 'Previously successful source/build not found; cannot safely reuse'; exit 1;
}
[[ -z "$(git -C "$src" status --porcelain --untracked-files=no)" ]] || {
  echo 'Integrated checkout has local modifications. Preserving without changes.'
  git -C "$src" status --short; exit 1;
}
git -C "$src" merge-base --is-ancestor "$old_candidate" HEAD || {
  echo 'Missing the previously integrated and confirmed CPU/GPU candidate'; exit 1;
}
git -C "$src" merge-base --is-ancestor "$old_upstream" HEAD || {
  echo 'Verified original upstream base is missing'; exit 1;
}

step="verify existing ES-DE launcher"
launcher="$HOME/Applications/shadps4/shadps4"
[[ -e "$launcher" ]] || { echo "Existing ES-DE executable absent: $launcher"; exit 1; }
install_target="$(readlink -f "$launcher")"
[[ "$install_target" == "$HOME"/* && -f "$install_target" ]] || {
  echo "Unexpected ES-DE target: $install_target"; exit 1;
}
[[ "$(od -An -tx1 -N4 "$install_target" | tr -d ' \n')" == 7f454c46 ]] || {
  echo 'Existing ES-DE target is not an ELF executable; preserving it'; exit 1;
}
[[ -w "$install_target" && -w "$(dirname "$install_target")" ]] || {
  echo 'No permission to update the existing target; preserving it'; exit 1;
}
installed_before="$(sha256sum "$install_target" | awk '{print $1}')"
echo "EXISTING_ESDE=$install_target"
echo "EXISTING_SHA256=$installed_before"

step="fetch latest upstream main"
git -C "$src" fetch --no-tags origin main
latest="$(git -C "$src" rev-parse refs/remotes/origin/main)"
git -C "$src" merge-base --is-ancestor "$old_upstream" "$latest" || {
  echo 'Upstream main no longer contains our verified original base; safe stop'; exit 1;
}
echo "LATEST_UPSTREAM_MAIN=$latest"
if ! git -C "$src" merge-base --is-ancestor "$latest" HEAD; then
  step="integrate latest upstream into our confirmed CPU/GPU fixes"
  if ! git -C "$src" -c commit.gpgsign=false merge --no-edit --no-ff \
      -m "Local ES-DE integration: upstream main $latest" "$latest"; then
    git -C "$src" status --short > "$ev/merge-conflicts.txt" 2>&1
    git -C "$src" diff --cc > "$ev/merge-conflicts.diff" 2>&1
    git -C "$src" merge --abort || true
    echo 'SAFE_STOP: upstream merge conflicts; existing ES-DE unchanged'
    exit 1
  fi
fi
head="$(git -C "$src" rev-parse HEAD)"
git -C "$src" merge-base --is-ancestor "$latest" "$head"
git -C "$src" merge-base --is-ancestor "$old_candidate" "$head"
git -C "$src" status --porcelain --untracked-files=no > "$ev/source-status.txt"
[[ ! -s "$ev/source-status.txt" ]] || { echo 'Source not clean'; exit 1; }
git -C "$src" diff --check "$old_candidate" HEAD
git -C "$src" log --oneline --graph -n 23 > "$ev/source-history.txt"
echo "INTEGRATED_HEAD=$head"

step="incremental release build (optional tests disabled)"
cmake -S "$src" -B "$bld" \
  -DCMAKE_BUILD_TYPE=Release -DENABLE_TESTS=OFF \
  -DENABLE_CPU_ID_TRANSLATION=ON -DENABLE_UPDATER=OFF
cmake --build "$bld" --target shadps4 --parallel 5
[[ -x "$bld/shadps4" ]] || { echo 'Emulator executable missing'; exit 1; }
[[ -x "$bld/cpu-id-runtime/bin64/drrun" &&
   -f "$bld/cpu-id-runtime/libshadps4_cpu_id.so" &&
   -f "$bld/cpu-id-runtime/lib64/release/libdynamorio.so" ]] || {
  echo 'CPU translation runtime incomplete'; exit 1;
}
echo 'RELEASE_BUILD=PASS'

step="smoke test emulator binary and CPU-ID runtime"
timeout 20s "$bld/shadps4" --help > "$ev/binary-help.txt" 2>&1
grep -q -- '--cpu-id-mode' "$ev/binary-help.txt" || {
  echo 'CPU-ID execution modes missing'; exit 1;
}
ldd "$bld/shadps4" > "$ev/linked-libraries.txt" 2>&1
if grep -q 'not found' "$ev/linked-libraries.txt"; then
  grep 'not found' "$ev/linked-libraries.txt"
  echo 'Missing shared library'; exit 1
fi
sha256sum "$bld/shadps4" > "$ev/build-sha256.txt"
echo 'EXECUTABLE_AND_RUNTIME_SMOKE=PASS'

step="stage complete candidate outside ES-DE"
mkdir -p "$trial"
cp -a "$bld/shadps4" "$trial/shadps4"
cp -a "$bld/cpu-id-runtime" "$trial/cpu-id-runtime"
cmp "$bld/shadps4" "$trial/shadps4"
timeout 20s "$trial/shadps4" --help > "$ev/trial-help.txt" 2>&1
echo "TRIAL_BINARY=$trial/shadps4"

step="protect active games and detect existing binary changes"
if pgrep -x shadps4 >/dev/null; then
  outcome=BUILT_NOT_INSTALLED
  echo 'shadps4 is running. Build saved in trial folder, active game untouched.'
  exit 0
fi
if [[ "$(sha256sum "$install_target" | awk '{print $1}')" != "$installed_before" ]]; then
  outcome=BUILT_NOT_INSTALLED
  echo 'Installed emulator changed during build. Build staged; no deployment.'
  exit 0
fi

step="back up original and stage replacement beside it"
install_dir="$(dirname "$install_target")"
backup_binary="$install_target.before-latest-main-$stamp"
runtime_path="$install_dir/cpu-id-runtime"
runtime_backup="$install_dir/cpu-id-runtime.before-latest-main-$stamp"
binary_new="$install_dir/.shadps4-new-$stamp"
runtime_new="$install_dir/.cpu-id-runtime-new-$stamp"
[[ ! -e "$binary_new" && ! -e "$runtime_new" ]] || {
  echo 'Unexpected staging name collision; no deployment'; exit 1;
}
install -m 755 "$trial/shadps4" "$binary_new"
cp -a "$trial/cpu-id-runtime" "$runtime_new"
cmp "$binary_new" "$trial/shadps4"
cp -a "$install_target" "$backup_binary"
sha256sum "$backup_binary" > "$ev/backup-sha256.txt"
echo "ORIGINAL_BACKUP=$backup_binary"

step="deploy with automatic rollback if verification fails"
deploying=1
if [[ -e "$runtime_path" || -L "$runtime_path" ]]; then
  mv -- "$runtime_path" "$runtime_backup"
  old_runtime_moved=1
fi
mv -- "$runtime_new" "$runtime_path"
mv -- "$binary_new" "$install_target"
timeout 20s "$launcher" --help > "$ev/installed-help.txt" 2>&1
cmp "$trial/shadps4" "$install_target"
[[ -x "$runtime_path/bin64/drrun" && -f "$runtime_path/libshadps4_cpu_id.so" ]]
deploying=0
outcome=INSTALLED_PENDING_GAMEPLAY
step=complete
echo "INSTALLED=$install_target"
echo "UPSTREAM_MAIN=$latest"
echo "INTEGRATED_CPU_GPU=$head"
echo "BACKUP_BINARY=$backup_binary"
echo "BACKUP_CPU_RUNTIME=$runtime_backup"
echo 'RELEASE_BUILD=PASS; CLI_SMOKE=PASS; GAMEPLAY=NOT_YET_TESTED'
echo 'Next: launch a game through ES-DE to validate runtime gameplay.'
echo 'No optional GCN tests, sudo, graphics driver changes, or SSH logout.'
