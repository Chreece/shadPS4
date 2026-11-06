#!/usr/bin/env bash
set -Eeuo pipefail
PIN=${1:?Pass the reviewed immutable source commit}
[[ "$PIN" =~ ^[0-9a-f]{40}$ ]] || exit 2
MODE=${2:-build}
RUNNER_PIN=${3:-$PIN}
[[ "$MODE" == build || "$MODE" == existing ]] || exit 2
[[ "$RUNNER_PIN" =~ ^[0-9a-f]{40}$ ]] || exit 2
export SHADPS4_GRAPHICS_DIAGNOSTICS=1
if [[ "${SHADPS4_TEST_PERFORMANCE:-0}" == 1 ]]; then
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
curl --fail --location --retry 3 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$RUNNER_PIN/tools/development/run_universal_verified_graphics.py" \
    -o "$OUT/graphics.py"
printf '%s  %s\n' b71be72c11c5f7f7cbe5da7c21b619334d1c523ad97f71cd90303c7d383305d6 "$OUT/graphics.py" | sha256sum --check -
curl --fail --location --retry 3 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$RUNNER_PIN/tools/development/shadps4_native_120_observer_20261026.py" \
    -o "$OUT/observer.py"
python3 - "$OUT" "$PIN" "$RUNNER_PIN" <<'PY'
import datetime,fcntl,hashlib,importlib.util,json,pathlib,sys
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
    curl --fail --location --retry 3 \
        "https://raw.githubusercontent.com/Chreece/shadPS4/$PIN/scripts/shadps4-universal-upstream-build.sh" \
        -o "$OUT/build.sh"
    printf '%s  %s\n' 6f827be5ef09ff9e296a970c7c02d3ebda7b4c05d5d4a1dd79189716968e6416 "$OUT/build.sh" | sha256sum --check -
    SHADPS4_PERFORMANCE_DIAGNOSTICS=0 bash "$OUT/build.sh" "$PIN" 2>&1 | tee "$OUT/build-runner.log"
else
    printf 'REUSING_VERIFIED_NATIVE_BUILD=%s\n' "$PIN"
fi
RESULT=0
for GAME in "${GAMES[@]}"; do
    printf '\nTESTING=%s MAX_SECONDS=120 AUTOMATIC_INPUT=NO\n' "$GAME"
    set +e
    SHADPS4_TEST_SOURCE_COMMIT="$PIN" \
    SHADPS4_TEST_RUNNER_COMMIT="$RUNNER_PIN" \
    SHADPS4_TEST_GAME="/mnt/roms-all/ps4-games/$GAME/eboot.bin" \
        python3 "$OUT/graphics.py" \
        2>&1 | tee "$OUT/$GAME-runner.log"
    rc=${PIPESTATUS[0]}
    set -e
    if (( rc != 0 )); then RESULT=$rc; fi
    if python3 - "$OUT/$GAME-runner.log" <<'PY'
import json,pathlib,sys
for line in pathlib.Path(sys.argv[1]).read_text(errors='replace').splitlines():
    if line.startswith('EVIDENCE='):
        p=pathlib.Path(line.removeprefix('EVIDENCE=').removesuffix('.tar.gz'))/'result.json'
        if p.is_file():
            result=json.loads(p.read_text())
            native=result.get('native',{})
            unsafe=result.get('stop_remaining_tests') or native.get('gpu_failure') or (
                result.get('game_launched') and not native.get('owned_child_terminated',False))
            sys.exit(0 if unsafe else 1)
sys.exit(0)
PY
    then
        echo SHARED_PREFLIGHT_GPU_FAILURE_OR_UNCONFIRMED_STOP_ENDING_REMAINING_TESTS
        RESULT=1
        break
    fi
done
exit "$RESULT"
