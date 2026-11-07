#!/usr/bin/env bash
set -Eeuo pipefail
RUNNER_PIN=${1:?Pass the immutable runner commit}
[[ "$RUNNER_PIN" =~ ^[0-9a-f]{40}$ ]] || exit 2
SOURCE=4caaea20e0bcfd68aaf0a1d7e6bb21f9bdcf98b1
ELF=2e71fab6d42f8e35573dbe54d25f2540697b8e48785da4cd3bc2f84d9b0469be
PROJECT="$HOME/projects/shadps4"
mkdir -p "$PROJECT/scripts" "$PROJECT/logs"
WORK=$(mktemp -d "$PROJECT/scripts/regression-baseline.XXXXXX")
trap 'rm -rf -- "$WORK"' EXIT
python3 - "$PROJECT" "$SOURCE" "$ELF" <<'PY'
import hashlib,json,pathlib,subprocess,sys
project=pathlib.Path(sys.argv[1]);pin=sys.argv[2];expected=sys.argv[3]
base=project/'tests'/('universal-upstream-'+pin[:12])
binary=base/'build/shadps4';source=base/'source'
if binary.is_symlink() or not binary.is_file():
    raise SystemExit('BASELINE_BINARY_NOT_AVAILABLE: no build, install or launch performed')
h=hashlib.sha256()
with binary.open('rb') as stream:
    for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
if h.hexdigest()!=expected:
    raise SystemExit('BASELINE_BINARY_HASH_MISMATCH: no build, install or launch performed')
if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!=pin:
    raise SystemExit('BASELINE_SOURCE_MISMATCH: no build, install or launch performed')
verified=False
for path in (project/'logs').glob('universal-upstream-*/result.json'):
    try:record=json.loads(path.read_text())
    except (OSError,ValueError):continue
    if (record.get('source_commit')==pin and record.get('binary_sha256')==expected and
            record.get('candidate_binary')==str(binary) and
            record.get('result')=='PASS_NATIVE_BUILD_CPU_AND_STARTUP_TESTS_GPU_NOT_RUN'):
        verified=True
if not verified:
    raise SystemExit('BASELINE_BUILD_RECORD_NOT_AVAILABLE: no launch performed')
print('BASELINE_VERIFIED='+pin,flush=True)
print('REPLAY_LAST_VISIBLE_SCENE_BUILD; GRAPHICS_AND_CUTSCENE_ACCEPTANCE_STILL_PENDING',flush=True)
PY
curl --ipv4 --fail --location --retry 3 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$RUNNER_PIN/scripts/shadps4-universal-two-game-check.sh" \
    -o "$WORK/run.sh"
printf '%s  %s\n' 5cc38008a511705fab8b9feed4f4abd0713a1e8e7b431ffc78c41491f1072761 "$WORK/run.sh" | sha256sum --check -
sudo -v
unset SHADPS4_PR117_CAPTURE_ONCE SHADPS4_TEST_PERFORMANCE
export SHADPS4_TEST_GPU_TRACE=1
export SHADPS4_TEST_FIRST_GAME=CUSA11456
export SHADPS4_KERNEL_JOURNAL_SUDO=1
bash "$WORK/run.sh" "$SOURCE" existing "$RUNNER_PIN"
