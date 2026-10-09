#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

stamp="$(date +%Y%m%d-%H%M%S)"
root="$HOME/shadps4-esde-verified-builds/$stamp"
src="$root/source"
bld="$root/build"
tst="$root/tests"
ev="$root/evidence"
log="$HOME/shadps4-esde-verified-$stamp.log"
report="$HOME/shadps4-esde-verified-$stamp.tar.gz"
mkdir -p "$ev"
exec > >(tee -a "$log") 2>&1
step="preflight"
result="FAILED"
deploying=0
install_target=""
backup_binary=""
runtime_path=""
backup_runtime=""
runtime_changed=0
on_exit() {
  rc=$1
  trap - EXIT
  set +e
  if (( rc != 0 && deploying == 1 )); then
    echo 'DEPLOY_FAILED: restoring original emulator/runtime'
    if [[ -n "$backup_binary" && -e "$backup_binary" ]]; then
      cp -a -- "$backup_binary" "$install_target" || echo 'BINARY_ROLLBACK_ERROR'
    fi
    if (( runtime_changed == 1 )) && [[ -n "$runtime_path" ]]; then
      if [[ -e "$runtime_path" || -L "$runtime_path" ]]; then
        mv -- "$runtime_path" "$runtime_path.failed-$stamp" || echo 'RUNTIME_MOVE_ERROR'
      fi
      if [[ -n "$backup_runtime" && ( -e "$backup_runtime" || -L "$backup_runtime" ) ]]; then
        mv -- "$backup_runtime" "$runtime_path" || echo 'RUNTIME_ROLLBACK_ERROR'
      fi
    fi
  fi
  {
    echo "RESULT=$result"
    echo "EXIT_CODE=$rc"
    echo "LAST_STEP=$step"
    echo "DATE=$(date -Is)"
    echo "SOURCE=$src"
    echo "UPSTREAM_MAIN=${base_sha:-unknown}"
    echo "CANDIDATE=${candidate_sha:-unknown}"
    echo "INSTALL_TARGET=${install_target:-unknown}"
    echo "BACKUP_BINARY=${backup_binary:-none}"
  } > "$ev/summary.txt"
  if [[ -d "$src/.git" ]]; then
    git -C "$src" status --short > "$ev/git-status.txt" 2>&1
    git -C "$src" log --graph --oneline --decorate -35 > "$ev/integration-history.txt" 2>&1
    git -C "$src" diff --cc > "$ev/conflicts.diff" 2>&1
    git -C "$src" diff --name-only --diff-filter=U > "$ev/conflicted-files.txt" 2>&1
    git -C "$src" submodule status --recursive > "$ev/submodules.txt" 2>&1
  fi
  tail -n 5000 -- "$log" > "$ev/build.log" 2>/dev/null || true
  tar -czf "$report" -C "$root" evidence 2>/dev/null || true
  echo
  echo "RESULT=$result"
  echo "STEP=$step"
  echo "REPORT=$report"
  echo "LOG=$log"
  echo "SSH_SESSION=UNCHANGED"
}
trap 'on_exit "$?"' EXIT

echo '=== VERIFIED shadPS4 / ES-DE BUILD ==='
echo "Started $(date -Is)"
echo "Host: $(hostname), OS: $(. /etc/os-release; echo "${PRETTY_NAME}")"
command -v git >/dev/null
command -v cmake >/dev/null
command -v ninja >/dev/null
command -v python3 >/dev/null
command -v tar >/dev/null
command -v timeout >/dev/null
command -v readlink >/dev/null
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'Requires Linux x86-64'; exit 1; }

if command -v clang-19 >/dev/null && command -v clang++-19 >/dev/null; then
  cc=clang-19; cxx=clang++-19
elif command -v gcc-14 >/dev/null && command -v g++-14 >/dev/null; then
  cc=gcc-14; cxx=g++-14
else
  echo 'MISSING_TOOLCHAIN: Clang 19 or GCC 14 (do not silently use Clang 17).'
  exit 1
fi
echo "Compiler: $($cxx --version | head -1)"
step="verify existing ES-DE binary before resource-intensive build"
launcher="$HOME/Applications/shadps4/shadps4"
if [[ ! -e "$launcher" ]]; then
  echo "SAFE_STOP: existing ES-DE shadPS4 core missing: $launcher"
  exit 1
fi
install_target="$(readlink -f "$launcher")"
if [[ "$install_target" != "$HOME"/* || ! -f "$install_target" ]]; then
  echo "SAFE_STOP: unexpected target for ES-DE symlink: $install_target"
  exit 1
fi
if [[ "$(od -An -tx1 -N4 "$install_target" | tr -d ' \n')" != 7f454c46 ]]; then
  echo "SAFE_STOP: existing core is not a Linux ELF binary: $install_target"
  exit 1
fi
if [[ ! -w "$install_target" || ! -w "$(dirname "$install_target")" ]]; then
  echo "SAFE_STOP: no write permission for the existing ES-DE binary or its directory."
  exit 1
fi
echo "EXISTING_ESDE_BINARY=$install_target"
step="preflight complete"
df -h "$HOME"
free -h

step="check previous verified integration"
# Preserve the exact clean integration produced by the previous attempt.
# Reuse it ONLY while the upstream base still equals the current main.
resume_src="$HOME/shadps4-esde-verified-builds/20261009-173836/source"
resume_base="30d82c9003480b991092279f7a10c2a3d2fe03b8"
resume_head="9e81877f61837d1423e5dc1fbd6c60fd005e8524"
resume=0
if [[ -d "$resume_src/.git" ]] &&
   [[ "$(git -C "$resume_src" rev-parse HEAD 2>/dev/null)" == "$resume_head" ]] &&
   [[ "$(git -C "$resume_src" rev-parse refs/remotes/origin/main 2>/dev/null)" == "$resume_base" ]] &&
   [[ -z "$(git -C "$resume_src" status --porcelain --untracked-files=no 2>/dev/null)" ]]; then
  echo 'Checking the current upstream main before reusing the completed integration.'
  if remote_main="$(git -C "$resume_src" ls-remote --heads origin main | awk '{print $1}')" &&
     [[ "$remote_main" == "$resume_base" ]]; then
    resume=1
    src="$resume_src"
    base_sha="$resume_base"
    candidate_sha="$resume_head"
    echo "RESUME_EXISTING_INTEGRATION=$src"
    echo "UPSTREAM_MAIN=$base_sha"
    echo "CANDIDATE=$candidate_sha"
  else
    echo 'The upstream base changed or could not be verified; performing a fresh pinned integration.'
  fi
fi

if (( resume == 0 )); then
step="fetch fresh upstream main"
git clone --filter=blob:none --no-checkout https://github.com/shadps4-emu/shadPS4.git "$src"
git -C "$src" checkout -B "esde-verified-$stamp" origin/main
git -C "$src" config user.name 'Local shadPS4 integration'
git -C "$src" config user.email 'local-shadps4-integration@localhost'
base_sha="$(git -C "$src" rev-parse HEAD)"
echo "UPSTREAM_MAIN=$base_sha"

step="fetch pinned proven PRs"
# Pinned heads were reviewed on 2026-10-09. Do not silently accept later edits.
declare -A pinned=(
  [5321]=b02f24559ad86249aef549d11531977a19b8196f
  [5314]=c48f374d6a7649f7debdd58fc1aa0f81911131f3
  [5325]=9bb22d370e5847a37ecda13c5628d5d280a03f2f
  [5234]=b0adb63c43f50e6eff7d30e96414c1a62bc56b90
  [5230]=17198a07daa4a50e4c0388a5002d2c4fee3fe636
  [5232]=89d2333f161abf0ad86afb7f8b7eb62efe5c09d4
  [5228]=53c1def065426d33026b373f8a2344a3b22ec1d1
  [5235]=4651f38dc15757be7b9428b402f9a223cfc05743
  [5275]=8a796a331a0c56a2f067fe0c883394f80f9769d6
)
prs=(5321 5314 5325 5234 5230 5232 5228 5235 5275)
for pr in "${prs[@]}"; do
  git -C "$src" fetch --no-tags origin "refs/pull/$pr/head:refs/remotes/origin/proven-$pr"
  actual="$(git -C "$src" rev-parse "refs/remotes/origin/proven-$pr")"
  echo "PR #$pr $actual"
  if [[ "$actual" != "${pinned[$pr]}" ]]; then
    echo "STOP: PR #$pr changed since verification. Expected ${pinned[$pr]}"
    exit 1
  fi
done

step="integrate verified CPU and general fixes"
# The #5321 head already includes #5287, #5304, #5315 and an earlier #5314.
# First merge this pinned integration branch. It was verified mergeable against
# upstream main 661d6fd0463a8ea5b441d3fcefd7d110a819c914.
if ! git -C "$src" merge-base --is-ancestor refs/remotes/origin/proven-5321 HEAD; then
  echo '===== MERGING CPU integration PR #5321 ====='
  if ! git -C "$src" -c commit.gpgsign=false merge --no-ff --no-edit \
       -m 'Local ES-DE integration: pinned CPU PR #5321' \
       refs/remotes/origin/proven-5321; then
    echo 'SAFE_STOP: CPU integration diverges from fresh upstream main.'
    exit 1
  fi
fi

# The newest hardware-profile changes in #5314 were made after the integrated
# #5321 branch. A normal branch merge conflicts in cpu_id.cpp. Evidence shows
# the two pinned versions differ *only* in GuestCpuid(), the emulation-profile
# include/global, and guest_neo initialization. Verify exactly that before
# selecting the later hardware-validated CPU profile file.
step="apply hardware-verified PS4 / PS4 Pro CPU profile"
current_cpu_blob="$(git -C "$src" rev-parse 'HEAD:src/core/cpu_id.cpp')"
profile_cpu_blob="$(git -C "$src" rev-parse 'refs/remotes/origin/proven-5314:src/core/cpu_id.cpp')"
if [[ "$current_cpu_blob" != fcdbac396cb109ba457122a2f5fa7b648af4392d ||
      "$profile_cpu_blob" != 31f691e1e96892866dd0275951fc8ad8e62033a5 ]]; then
  echo 'SAFE_STOP: CPU profiles changed; cannot use the reviewed resolution.'
  echo "current=$current_cpu_blob profile=$profile_cpu_blob"
  exit 1
fi
python3 - "$src" <<'PROFILE_CHECK'
import subprocess
import sys
from pathlib import Path
src = Path(sys.argv[1])
original = (src / 'src/core/cpu_id.cpp').read_text()
profile = subprocess.check_output(
    ['git', '-C', str(src), 'show',
     'refs/remotes/origin/proven-5314:src/core/cpu_id.cpp'], text=True)

def non_profile(text):
    for line in (
        '#include "core/emulator_settings.h"\n',
        'bool guest_neo{};\n',
        '    guest_neo = EmulatorSettings.IsNeo();\n',
    ):
        text = text.replace(line, '')
    start = text.index('std::array<u32, 4> GuestCpuid(')
    end = text.index('\nu32 CurrentGuestCpu()', start)
    return text[:start] + '/* verified guest CPUID profile */' + text[end:]

if non_profile(original) != non_profile(profile):
    raise SystemExit('SAFE_STOP: PR #5314 changes code outside the verified CPU profile')
print('CPU profile isolation confirmed: no unrelated CPU-ID code changed.')
PROFILE_CHECK
git -C "$src" show 'refs/remotes/origin/proven-5314:src/core/cpu_id.cpp' \
  > "$src/src/core/cpu_id.cpp"
git -C "$src" add src/core/cpu_id.cpp
git -C "$src" -c commit.gpgsign=false commit \
  -m 'Local integration: PS4/Pro hardware CPU metadata from PR #5314'

# Independent PRs should be replayed as their own commits, not merged as
# entire older branches. A branch merge could reintroduce unrelated old code.
# Verify each change is entirely contained in the expected number of commits.
step="cherry-pick independently tested CPU and general fixes"
declare -A expected_commits=(
  [5325]=1
  [5234]=1
  [5230]=1
  [5232]=2
  [5228]=1
  [5235]=2
  [5275]=6
)
for pr in 5325 5234 5230 5232 5228 5235 5275; do
  ref="refs/remotes/origin/proven-$pr"
  if git -C "$src" merge-base --is-ancestor "$ref" HEAD; then
    echo "PR #$pr already integrated; skipping"
    continue
  fi
  ancestor="$(git -C "$src" merge-base refs/remotes/origin/main "$ref")"
  mapfile -t commits < <(git -C "$src" rev-list --reverse "$ancestor..$ref")
  if [[ "${#commits[@]}" -ne "${expected_commits[$pr]}" ]]; then
    echo "SAFE_STOP: PR #$pr has ${#commits[@]} commits; expected ${expected_commits[$pr]}."
    exit 1
  fi
  echo "===== APPLYING VERIFIED PR #$pr (${#commits[@]} commits) ====="
  for sha in "${commits[@]}"; do
    if ! git -C "$src" -c commit.gpgsign=false cherry-pick --no-edit "$sha"; then
      echo "SAFE_STOP: PR #$pr requires review against newest source. Not deploying."
      git -C "$src" status --short
      exit 1
    fi
  done
done
candidate_sha="$(git -C "$src" rev-parse HEAD)"
echo "CANDIDATE=$candidate_sha"
fi

step="verify unchanged bundled runtime patch and source whitespace"
# This .patch is consumed by git apply; its five whitespace lines are part of
# a pinned upstream runtime patch, not C++ source formatting. Do not rewrite it.
runtime_patch="src/core/cpu_id_translation/dynamorio.patch"
expected_runtime_patch="ab9c52d60788adaf41b37cbff0a7a146ca69f6ea"
runtime_patch_blob="$(git -C "$src" rev-parse "HEAD:$runtime_patch")"
if [[ "$runtime_patch_blob" != "$expected_runtime_patch" ]]; then
  echo "SAFE_STOP: bundled DynamoRIO patch changed: $runtime_patch_blob"
  exit 1
fi
git -C "$src" diff --check "$base_sha" HEAD -- . ":(exclude)$runtime_patch"
echo 'WHITESPACE_CHECK=PASS (source checked; pinned external .patch exempt)'

step="submodules"
git -C "$src" submodule update --init --recursive --jobs 4

step="configure release"
cmake -S "$src" -B "$bld" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="$cc" -DCMAKE_CXX_COMPILER="$cxx" \
  -DENABLE_CPU_ID_TRANSLATION=ON -DENABLE_TESTS=OFF \
  -DENABLE_UPDATER=OFF

step="build emulator and bundled CPU runtime"
cmake --build "$bld" --parallel 5
[[ -x "$bld/shadps4" ]] || { echo 'No shadps4 executable'; exit 1; }
[[ -x "$bld/cpu-id-runtime/bin64/drrun" ]] || { echo 'Bundled DynamoRIO runner missing'; exit 1; }
[[ -f "$bld/cpu-id-runtime/libshadps4_cpu_id.so" ]] || { echo 'Bundled CPU client missing'; exit 1; }

step="smoke test"
timeout 20s "$bld/shadps4" --help > "$ev/binary-help.txt" 2>&1
if ! grep -q -- '--cpu-id-mode' "$ev/binary-help.txt"; then
  echo 'CPU translation CLI is missing!'; exit 1
fi
ldd "$bld/shadps4" > "$ev/ldd.txt"
if grep -q 'not found' "$ev/ldd.txt"; then
  echo 'Missing shared library:'; grep 'not found' "$ev/ldd.txt"; exit 1
fi
sha256sum "$bld/shadps4" > "$ev/binary-sha256.txt"

step="configure and run repository tests"
cmake -S "$src" -B "$tst" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="$cc" -DCMAKE_CXX_COMPILER="$cxx" \
  -DENABLE_TESTS=ON -DENABLE_CPU_ID_TRANSLATION=OFF -DENABLE_UPDATER=OFF
cmake --build "$tst" --parallel 5
ctest --test-dir "$tst" --output-on-failure --timeout 120 | tee "$ev/ctest.txt"
if grep -Eq '(No tests were found|Total Tests: 0)' "$ev/ctest.txt"; then
  echo 'Test collection unexpectedly empty'; exit 1
fi

step="verify ES-DE install path"
launcher="$HOME/Applications/shadps4/shadps4"
[[ -e "$launcher" ]] || { echo "Missing existing ES-DE target: $launcher"; exit 1; }
install_target="$(readlink -f "$launcher")"
[[ "$install_target" == "$HOME"/* && -f "$install_target" ]] || {
  echo "Unexpected executable path: $install_target"; exit 1;
}
if [[ "$(od -An -tx1 -N4 "$install_target" | tr -d ' \n')" != 7f454c46 ]]; then
  echo "The current ES-DE executable is not ELF: $install_target; preserving it"; exit 1
fi
if pgrep -x shadps4 >/dev/null; then
  echo 'shadps4 is running. No installation attempted, leaving active game untouched.'
  exit 1
fi
install_dir="$(dirname "$install_target")"
runtime_path="$install_dir/cpu-id-runtime"
backup_binary="$install_target.before-verified-$stamp"
backup_runtime="$install_dir/cpu-id-runtime.before-verified-$stamp"
binary_candidate="$install_dir/.shadps4.new-$stamp"
runtime_candidate="$install_dir/.cpu-id-runtime.new-$stamp"
[[ ! -e "$binary_candidate" && ! -e "$runtime_candidate" ]] || exit 1

step="stage ES-DE release without changing live installation"
cp -a "$bld/cpu-id-runtime" "$runtime_candidate"
install -m 755 "$bld/shadps4" "$binary_candidate"
timeout 20s "$binary_candidate" --help > "$ev/staged-help.txt" 2>&1
sha256sum "$binary_candidate" > "$ev/staged-sha256.txt"
cp -a "$install_target" "$backup_binary"

echo "BACKUP_BINARY=$backup_binary"
step="atomic-ish deployment and installed binary verification"
deploying=1
if [[ -e "$runtime_path" || -L "$runtime_path" ]]; then
  mv -- "$runtime_path" "$backup_runtime"
fi
runtime_changed=1
mv -- "$runtime_candidate" "$runtime_path"
mv -- "$binary_candidate" "$install_target"
timeout 20s "$launcher" --help > "$ev/installed-help.txt" 2>&1
cmp -s "$bld/shadps4" "$install_target"
[[ -x "$runtime_path/bin64/drrun" && -f "$runtime_path/libshadps4_cpu_id.so" ]]

result="SUCCESS"
deploying=0
step="complete: launch a game from ES-DE to verify actual gameplay"
echo "INSTALLED=$install_target"
echo "BACKUP_BINARY=$backup_binary"
echo "BACKUP_CPU_RUNTIME=$backup_runtime"
echo 'Existing game files, saves, configurations, Sunshine and ES-DE were not modified.'
