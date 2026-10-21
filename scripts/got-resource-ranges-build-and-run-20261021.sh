#!/usr/bin/env bash
# A source-correction package on the last tested candidate. No Git checkout/reset.
set -Eeuo pipefail
REV="${1:?Pass the pinned package revision}"
[[ "$REV" =~ ^[0-9a-f]{40}$ ]] || { echo "INVALID_PACKAGE_REVISION"; exit 2; }
ROOT="$HOME/projects/shadps4"
WORK="$ROOT/GhostOfTsushima/tests/got-upstream-menu-main-20261017-cached"
SOURCE="$WORK/source"
LOGS="$ROOT/logs"
SCRIPTS="$ROOT/scripts"
BRANCH="fix/shader-resource-ranges-20261021"
BASE="94ed29b7c5767718ead87164558d6ec7523bcc9a"
CANDIDATE="$WORK/bin-backups/shadps4-got-indexed-sample-feedback-$REV"
HOST_SCRIPT="$SCRIPTS/got-resource-ranges-host-$REV.sh"
RUNTIME_SCRIPT="$SCRIPTS/got-resource-ranges-runtime-$REV.py"
PREPARE_SCRIPT="$SCRIPTS/prepare-resource-host-$REV.py"
mkdir -p "$SCRIPTS" "$LOGS"
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
exec > >(tee -a "$LOGS/got-resource-ranges-launcher-$STAMP.log") 2>&1

# No external dependencies are downloaded or changed by this wrapper.
git -C "$SOURCE" cat-file -e "$REV^{commit}"
TMPDIR_LOCAL="$(mktemp -d "$SCRIPTS/.got-resource-ranges.XXXXXX")"
trap 'rm -rf -- "$TMPDIR_LOCAL"' EXIT
for path in scripts/got-indexed-sample-feedback-host-20261019.sh \
            scripts/got-world-graphics-runtime-20261021.py \
            scripts/prepare-resource-host-20261021.py \
            scripts/apply-resource-access-ranges-20261021.py \
            src/shader_recompiler/buffer_access_range.h \
            src/shader_recompiler/ir/passes/buffer_access_bounds.h \
            tests/shader_recompiler/test_buffer_access_range.cpp; do
    git -C "$SOURCE" cat-file -e "$REV:$path"
done
git -C "$SOURCE" show "$REV:scripts/got-indexed-sample-feedback-host-20261019.sh" > "$TMPDIR_LOCAL/host-original.sh"
git -C "$SOURCE" show "$REV:scripts/prepare-resource-host-20261021.py" > "$TMPDIR_LOCAL/prepare.py"
git -C "$SOURCE" show "$REV:scripts/got-world-graphics-runtime-20261021.py" > "$TMPDIR_LOCAL/runtime.py"
python3 - "$TMPDIR_LOCAL/prepare.py" "$TMPDIR_LOCAL/runtime.py" <<'PYSYNTAX'
import ast
from pathlib import Path
import sys
for name in sys.argv[1:]:
    ast.parse(Path(name).read_text())
PYSYNTAX
python3 "$TMPDIR_LOCAL/prepare.py" "$TMPDIR_LOCAL/host-original.sh" "$TMPDIR_LOCAL/host.sh" --runtime "$TMPDIR_LOCAL/runtime.py"
bash -n "$TMPDIR_LOCAL/host.sh"
mv -- "$TMPDIR_LOCAL/host.sh" "$HOST_SCRIPT"
mv -- "$TMPDIR_LOCAL/runtime.py" "$RUNTIME_SCRIPT"
mv -- "$TMPDIR_LOCAL/prepare.py" "$PREPARE_SCRIPT"

verified_host_report() {
    python3 - "$LOGS" "$CANDIDATE" "$REV" "$SOURCE" "$BASE" <<'PYREPORT'
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
logs, candidate = Path(sys.argv[1]), Path(sys.argv[2])
revision, source, base = sys.argv[3:]
if not candidate.is_file():
    raise SystemExit(1)
with candidate.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
patcher = subprocess.run(['git', '-C', source, 'show',
                          revision + ':scripts/apply-resource-access-ranges-20261021.py'],
                         check=True, stdout=subprocess.PIPE).stdout
patcher_hash = hashlib.sha256(patcher).hexdigest()
for path in sorted(logs.glob('got-indexed-sample-feedback-host-*.tar.gz'),
                   key=lambda p: p.stat().st_mtime, reverse=True):
    try:
        with tarfile.open(path) as archive:
            def read(name):
                member = archive.getmember(name)
                if not member.isfile() or member.size > 1024 * 1024:
                    raise ValueError('Invalid host metadata')
                return archive.extractfile(member).read().decode()
            final = read('info/final.txt').splitlines()
            required = ('STATUS=HOST_ONLY_BUILD_PASS', 'EXIT=0',
                        'CANDIDATE_BUILT=1', 'BASELINE_EXECUTABLE_RESTORED=1')
            if not all(line in final for line in required):
                continue
            if f'PINNED_CANDIDATE={revision}' not in read('info/start.txt').splitlines():
                continue
            if read('info/candidate-preserved.sha256').split()[0] != digest:
                continue
            manifest = json.loads(read('info/resource-correction.json'))
            if (manifest.get('base') != base or
                manifest.get('kind') != 'PINNED_SOURCE_CORRECTION' or
                manifest.get('patcher_sha256') != patcher_hash or
                len(manifest.get('files', {})) != 7):
                continue
            tests = read('host-tests.log')
            if 'CORRECTED_SOURCE_MANIFEST=PASS' not in tests or 'BUFFER_ACCESS_RANGE=PASS' not in tests:
                continue
            print(path)
            raise SystemExit(0)
    except (OSError, ValueError, KeyError, IndexError, tarfile.TarError):
        continue
raise SystemExit(1)
PYREPORT
}

if HOST_REPORT="$(verified_host_report)"; then
    echo "VERIFIED_CORRECTED_CANDIDATE_REUSED=$REV"
else
    echo "BUILDING_SOURCE_CORRECTION_PACKAGE=$REV BASE=$BASE"
    SHADPS4_HOST_CANDIDATE="$REV" SHADPS4_HOST_BRANCH="$BRANCH" bash "$HOST_SCRIPT"
    HOST_REPORT="$(verified_host_report)" || {
        echo "CORRECTED_HOST_BUILD_NOT_VERIFIED; see $LOGS/got-indexed-sample-feedback-host-*.tar.gz"
        exit 2
    }
fi

echo "HOST_BUILD_REPORT=$HOST_REPORT"
echo "GAME_CONTROL=MANUAL; select Continue and play the affected scene."
python3 "$RUNTIME_SCRIPT" --candidate "$CANDIDATE" --source-revision "$REV" \
    --host-report "$HOST_REPORT"
