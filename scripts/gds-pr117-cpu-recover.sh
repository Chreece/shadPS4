#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$HOME/projects/shadps4"
BRANCH='feature/ordered-gds-wave-executor-20261029'
TAG="gds-pr117-dual-cpu-$(date -u +%Y%m%dT%H%M%SZ)-$$"
WORK="$ROOT/tests/$TAG"
EVIDENCE="$ROOT/logs/$TAG"
mkdir -p "$WORK" "$EVIDENCE"
HEAD_SHA='NOT_FETCHED'
GCC='NOT_FOUND'
CLANG='NOT_FOUND'

finish() {
  local rc=$?
  trap - EXIT
  set +e
  python3 - "$EVIDENCE" "$WORK/source" "$TAG" "$HEAD_SHA" "$GCC" "$CLANG" "$rc" <<'PY'
import datetime, json, pathlib, sys
log, src, tag, head, gcc, clang, rc = sys.argv[1:]
log, src = pathlib.Path(log), pathlib.Path(src)
record = {
    'schema_version': 2,
    'record_id': tag,
    'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'scope': 'universal shadPS4 ordered GDS PR 117 CPU toolchain regression',
    'source_commit': head,
    'compilers': {'gcc': gcc, 'clang': clang},
    'cpu_test': 'PASS_GCC_CLANG_SANITIZERS' if int(rc) == 0 else 'FAIL_OR_INCOMPLETE',
    'exit_code': int(rc),
    'gpu_used': False,
    'game_launched': False,
    'visual_result': 'NOT_TESTED',
    'decision': 'KEEP_CPU_REFERENCE; GPU_ORDERED_GDS_BACKEND_NOT_IMPLEMENTED',
}
log.mkdir(parents=True, exist_ok=True)
(log / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
diary = src / 'docs/emulator'
if diary.is_dir():
    ledger = diary / 'TRIALS.jsonl'
    summary = diary / 'EXPERIMENT_DIARY.md'
    if ledger.is_file() and tag not in ledger.read_text():
        with ledger.open('a') as f:
            f.write(json.dumps(record, sort_keys=True) + '\n')
    if summary.is_file() and tag not in summary.read_text():
        with summary.open('a') as f:
            f.write('\n## ' + tag + '\n' +
                'CPU-only GCC+Clang and sanitizer rerun: ' + record['cpu_test'] +
                '. Source: ' + head + '. Runtime, native GPU, visual recovery: NOT TESTED.\n')
PY
  tar -C "$ROOT/logs" -czf "$ROOT/logs/$TAG.tar.gz" "$TAG" 2>"$ROOT/logs/$TAG-tar-errors.log"
  printf '\nPR117_CPU_TEST_EXIT=%s\n' "$rc"
  printf 'EVIDENCE_ARCHIVE=%s\n' "$ROOT/logs/$TAG.tar.gz"
  printf 'SOURCE_CHECKOUT=%s\n' "$WORK/source"
  exit "$rc"
}
trap finish EXIT

printf 'PR117_STAGE=CLONE_ISOLATED_SOURCE\n' | tee "$EVIDENCE/progress.log"
git clone -q --depth 1 --filter=blob:none --single-branch \
  --branch "$BRANCH" 'https://github.com/Chreece/shadPS4.git' \
  "$WORK/source" 2>&1 | tee "$EVIDENCE/clone.log"
SRC="$WORK/source"
HEAD_SHA="$(git -C "$SRC" rev-parse HEAD)"
printf 'PR117_SOURCE_HEAD=%s\n' "$HEAD_SHA" | tee -a "$EVIDENCE/progress.log"
git -C "$SRC" show -s --format=fuller HEAD > "$EVIDENCE/source.txt"

probe_cpp23() {
  printf 'int main(){return 0;}\n' | "$1" -std=c++23 -x c++ -fsyntax-only - >/dev/null 2>&1
}
find_clang() {
  local c
  for c in clang++ clang++-21 clang++-20 clang++-19 clang++-18 clang++-17; do
    command -v "$c" >/dev/null 2>&1 || continue
    if probe_cpp23 "$c"; then command -v "$c"; return 0; fi
  done
  return 1
}
GCC="$(command -v g++)"
probe_cpp23 "$GCC" || { printf 'GCC_HAS_NO_CXX23_SUPPORT\n' >&2; exit 1; }

if ! CLANG="$(find_clang)"; then
  printf 'PR117_STAGE=INSTALL_MISSING_CLANG_NO_SERVICE_RESTART\n' | tee -a "$EVIDENCE/progress.log"
  command -v apt-get >/dev/null || { echo 'APT_REQUIRED_FOR_CLANG' >&2; exit 1; }
  sudo -v
  sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get update -qq \
    2>&1 | tee "$EVIDENCE/apt-update.log"
  installed=0
  for pkg in clang-19 clang-18 clang-17 clang; do
    if apt-cache show "$pkg" 2>/dev/null | grep -q '^Package:'; then
      if sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l \
        apt-get install -y --no-install-recommends "$pkg" \
        2>&1 | tee "$EVIDENCE/apt-install.log"; then
        installed=1
        break
      fi
    fi
  done
  [[ "$installed" == 1 ]] || { echo 'CLANG_PACKAGE_INSTALL_FAILED' >&2; exit 1; }
  CLANG="$(find_clang)" || { echo 'NO_CXX23_CLANG_AFTER_INSTALL' >&2; exit 1; }
fi

printf 'PR117_GCC=%s\nPR117_CLANG=%s\n' "$GCC" "$CLANG" \
  | tee -a "$EVIDENCE/progress.log"
"$GCC" --version | head -1 > "$EVIDENCE/gcc-version.txt"
"$CLANG" --version | head -1 > "$EVIDENCE/clang-version.txt"

for entry in "gcc:$GCC" "clang:$CLANG"; do
  label="${entry%%:*}"
  compiler="${entry#*:}"
  printf 'PR117_STAGE=RUN_%s_ASAN_UBSAN\n' "$label" | tee -a "$EVIDENCE/progress.log"
  (cd "$SRC" && env CXX="$compiler" RUN_SANITIZERS=1 \
    bash tests/run_ordered_gds_cpu.sh) 2>&1 | tee "$EVIDENCE/test-$label.log"
done

printf 'PR117_STAGE=VERIFY_GPU_GATE\n' | tee -a "$EVIDENCE/progress.log"
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
  "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp"
printf 'PR117_CPU=PASS_BOTH_COMPILERS_SANITIZERS\nPR117_GPU=GATED_NOT_TESTED\n' \
  | tee -a "$EVIDENCE/progress.log"
