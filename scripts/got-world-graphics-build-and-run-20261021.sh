#!/usr/bin/env bash
set -Eeuo pipefail

REV="${1:?Pass the pinned source revision}"
[[ "$REV" =~ ^[0-9a-f]{40}$ ]] || { echo "INVALID_SOURCE_REVISION"; exit 2; }
ROOT="$HOME/projects/shadps4"
WORK="$ROOT/GhostOfTsushima/tests/got-upstream-menu-main-20261017-cached"
SOURCE="$WORK/source"
LOGS="$ROOT/logs"
SCRIPTS="$ROOT/scripts"
BRANCH="fix/world-graphics-invariance-20261021"
CANDIDATE="$WORK/bin-backups/shadps4-got-indexed-sample-feedback-$REV"
HOST_SCRIPT="$SCRIPTS/got-world-graphics-host-20261021-$REV.sh"
RUNTIME_SCRIPT="$SCRIPTS/got-world-graphics-runtime-20261021-$REV.py"
mkdir -p "$SCRIPTS" "$LOGS"

git -C "$SOURCE" cat-file -e "$REV^{commit}"
for rel in scripts/got-indexed-sample-feedback-host-20261019.sh \
           scripts/got-world-graphics-runtime-20261021.py; do
    git -C "$SOURCE" cat-file -e "$REV:$rel"
done
HOST_TMP="$(mktemp "$HOST_SCRIPT.XXXXXX")"
RUNTIME_TMP="$(mktemp "$RUNTIME_SCRIPT.XXXXXX")"
trap 'rm -f -- "$HOST_TMP" "$RUNTIME_TMP"' EXIT
git -C "$SOURCE" show "$REV:scripts/got-indexed-sample-feedback-host-20261019.sh" > "$HOST_TMP"
git -C "$SOURCE" show "$REV:scripts/got-world-graphics-runtime-20261021.py" > "$RUNTIME_TMP"
bash -n "$HOST_TMP"
python3 - "$RUNTIME_TMP" <<'PYSYNTAX'
import ast
from pathlib import Path
import sys
ast.parse(Path(sys.argv[1]).read_text())
PYSYNTAX
mv -f -- "$HOST_TMP" "$HOST_SCRIPT"
mv -f -- "$RUNTIME_TMP" "$RUNTIME_SCRIPT"

verified_host_report() {
    python3 - "$LOGS" "$CANDIDATE" "$REV" <<'PYREPORT'
import hashlib
from pathlib import Path
import sys
import tarfile

logs, candidate, revision = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
if not candidate.is_file():
    raise SystemExit(1)
with candidate.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
for path in sorted(logs.glob('got-indexed-sample-feedback-host-*.tar.gz'),
                   key=lambda p: p.stat().st_mtime, reverse=True):
    try:
        with tarfile.open(path) as archive:
            def read(name):
                member = archive.getmember(name)
                if not member.isfile() or member.size > 65536:
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
            print(path)
            raise SystemExit(0)
    except (OSError, ValueError, KeyError, IndexError, tarfile.TarError):
        continue
raise SystemExit(1)
PYREPORT
}

if HOST_REPORT="$(verified_host_report)"; then
    echo "VERIFIED_CANDIDATE_REUSED=$REV"
else
    echo "BUILDING_CANDIDATE=$REV"
    SHADPS4_HOST_CANDIDATE="$REV" SHADPS4_HOST_BRANCH="$BRANCH" bash "$HOST_SCRIPT"
    HOST_REPORT="$(verified_host_report)" || {
        echo "HOST_BUILD_NOT_VERIFIED; see $LOGS/got-indexed-sample-feedback-host-*.tar.gz"
        exit 2
    }
fi

echo "HOST_BUILD_REPORT=$HOST_REPORT"
echo "GAME_CONTROL=MANUAL; select Continue and play when the game opens."
python3 "$RUNTIME_SCRIPT" --candidate "$CANDIDATE" --source-revision "$REV" \
    --host-report "$HOST_REPORT"
