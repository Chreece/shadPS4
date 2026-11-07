#!/usr/bin/env bash
set -Eeuo pipefail
PIN=${1:?Pass the reviewed immutable source commit}
[[ "$PIN" =~ ^[0-9a-f]{40}$ ]] || exit 2
MODE=${2:-build}
RUNNER_PIN=${3:-$PIN}
[[ "$MODE" == build || "$MODE" == existing ]] || exit 2
[[ "$RUNNER_PIN" =~ ^[0-9a-f]{40}$ ]] || exit 2
export SHADPS4_GRAPHICS_DIAGNOSTICS=1
if [[ "${SHADPS4_TEST_GPU_TRACE:-0}" == 1 ]]; then
    unset SHADPS4_PERFORMANCE_DIAGNOSTICS
    export SHADPS4_VULKAN_DIAGNOSTICS=0
elif [[ "${SHADPS4_TEST_PERFORMANCE:-0}" == 1 ]]; then
    export SHADPS4_PERFORMANCE_DIAGNOSTICS=1
    export SHADPS4_VULKAN_DIAGNOSTICS=0
else
    unset SHADPS4_PERFORMANCE_DIAGNOSTICS
    export SHADPS4_VULKAN_DIAGNOSTICS=1
fi
FIRST_GAME=${SHADPS4_TEST_FIRST_GAME:-CUSA34384}
case "$FIRST_GAME" in
    CUSA34384) GAMES=(CUSA34384 CUSA11456) ;;
    CUSA11456) GAMES=(CUSA11456 CUSA34384) ;;
    *) echo INVALID_FIRST_GAME; exit 2 ;;
esac
PROJECT="$HOME/projects/shadps4"
TAG="universal-two-games-$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="$PROJECT/logs/$TAG"
mkdir -p "$OUT"
finish() {
    local rc=$?
    trap - EXIT
    set +e
    python3 - "$OUT" "$rc" "$PIN" "$RUNNER_PIN" "$MODE" "${GAMES[@]}" <<'PY'
import json,pathlib,shutil,sys
out=pathlib.Path(sys.argv[1]);records=[]
for log in out.glob('*-runner.log'):
    for line in log.read_text(errors='replace').splitlines():
        if not line.startswith('EVIDENCE='):continue
        archive=pathlib.Path(line.removeprefix('EVIDENCE='))
        if archive.is_file() and archive.parent.resolve()==out.parent.resolve():
            shutil.copyfile(archive,out/archive.name)
            result=archive.with_suffix('').with_suffix('')/'result.json'
            if result.is_file():records.append(json.loads(result.read_text()))
(out/'batch-result.json').write_text(json.dumps({'exit_code':int(sys.argv[2]),'runs':records,'graphics_verified':False,'source_commit':sys.argv[3],'runner_commit':sys.argv[4],'build_mode':sys.argv[5],'requested_game_order':sys.argv[6:]},indent=2)+'\n')
PY
    tar -czf "$OUT.tar.gz" -C "$PROJECT/logs" "$TAG"
    printf '\nUPLOAD_THIS=%s.tar.gz\n' "$OUT"
    exit "$rc"
}
trap finish EXIT
curl --ipv4 --fail --location --retry 3 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$RUNNER_PIN/tools/development/run_universal_verified_graphics.py" \
    -o "$OUT/graphics.py"
printf '%s  %s\n' abf4bc07d17bdf05db5d34454db3893fee8ee008a74e6cfa774b9657fba8a3c9 "$OUT/graphics.py" | sha256sum --check -
curl --ipv4 --fail --location --retry 3 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$RUNNER_PIN/tools/development/shadps4_native_120_observer_20261026.py" \
    -o "$OUT/observer.py"
python3 - "$OUT" "$PIN" "$RUNNER_PIN" <<'PY'
import datetime,fcntl,hashlib,importlib.util,json,os,pathlib,sys
out=pathlib.Path(sys.argv[1])
record={'timestamp_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'kind':'universal-prebuild-process-preflight','source_commit':sys.argv[2],
        'runner_commit':sys.argv[3],'game_launched':False,'result':'PREFLIGHT_FAILED'}
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
try:
    runner=load('graphics_preflight',out/'graphics.py')
    if hashlib.sha256((out/'observer.py').read_bytes()).hexdigest()!=runner.OBSERVER_SHA256:
        raise RuntimeError('RUNNER_OBSERVER_HASH_MISMATCH')
    observer=load('observer_preflight',out/'observer.py')
    record['existing_emulator_pids']=observer.existing_games()
    for pid in record['existing_emulator_pids']:
        observer.capture_owned_stop_state(pid,out,filename=f'existing-process-{pid}.json')
    if record['existing_emulator_pids'] or os.environ.get('SHADPS4_KERNEL_JOURNAL_SUDO') == '1':
        try:
            runner.collect_display_health(out, os.environ.get('DISPLAY') or ':0', query_display=False)
        except Exception as error:
            record['display_diagnostic_error']=f'{type(error).__name__}: {error}'
    if record['existing_emulator_pids']:
        raise RuntimeError('AN_EMULATOR_STILL_HAS_LIVE_OR_UNREADABLE_THREADS')
    record['result']='PASS'
except Exception as error:
    record['error']=f'{type(error).__name__}: {error}'
finally:
    (out/'process-preflight.json').write_text(json.dumps(record,indent=2)+'\n')
    journal=out.parent.parent/'docs/emulator/TRIALS.jsonl'
    journal.parent.mkdir(parents=True,exist_ok=True)
    with journal.open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        stream.write(json.dumps(record)+'\n')
print('PREBUILD_PROCESS_PREFLIGHT='+record['result'],flush=True)
if record.get('error'):print(record['error'],flush=True)
sys.exit(0 if record['result']=='PASS' else 2)
PY
if [[ "$MODE" == build ]]; then
    curl --ipv4 --fail --location --retry 3 \
        "https://raw.githubusercontent.com/Chreece/shadPS4/$RUNNER_PIN/scripts/shadps4-universal-upstream-build.sh" \
        -o "$OUT/build.sh"
    printf '%s  %s\n' 8dceded3ad6bba3df7379e792fcf3bb7339b729b6bf22aea432390eda1ed2ce8 "$OUT/build.sh" | sha256sum --check -
    SHADPS4_PERFORMANCE_DIAGNOSTICS=0 bash "$OUT/build.sh" "$PIN" 2>&1 | tee "$OUT/build-runner.log"
else
    printf 'REUSING_VERIFIED_NATIVE_BUILD=%s\n' "$PIN"
fi
if [[ "${SHADPS4_KERNEL_JOURNAL_SUDO:-0}" == 1 ]]; then
    sudo -v
fi
curl --ipv4 --fail --location --retry 3 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$RUNNER_PIN/tools/development/run_universal_container_batch.py" \
    -o "$OUT/container-batch.py"
printf '%s  %s\n' b9c11eb8d0a89959361daaf3de93db781c8f8408431f1465cf126844e6e10535 "$OUT/container-batch.py" | sha256sum --check -
python3 "$OUT/container-batch.py" "$OUT" "$PIN" "$RUNNER_PIN" "${GAMES[@]}"
