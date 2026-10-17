#!/usr/bin/env bash
# GoT: re-run corrected finite-index image shader lowering, preserving baseline.
(
set -Eeuo pipefail
LOGS="$HOME/projects/shadps4/logs"
PREV="got-bounded-gpu-corrected-20261017-224119"
NAME="got-bounded-gpu-testfixed-$(date +%Y%m%d-%H%M%S)"
WORKER="$LOGS/$NAME.worker.sh"
mkdir -p "$LOGS"
python3 - "$LOGS/$PREV.tar.gz" "$PREV.worker.sh" "$WORKER" <<'PY'
from pathlib import Path
import hashlib, sys, tarfile
archive, member, output = sys.argv[1:]
with tarfile.open(archive, 'r:gz') as archive_file:
    member_data = archive_file.extractfile(member)
    assert member_data is not None, 'VERIFIED_PREVIOUS_WORKER_NOT_FOUND'
    original = member_data.read()
old_sha = '665a7b3e6166ee04cff2fca20846ec321fcb41c3126129ed30cbc8161cb073fe'
assert hashlib.sha256(original).hexdigest() == old_sha, 'PREVIOUS_WORKER_SHA_CHANGED'
old_commit = b'f2509c62cb005ad42ec25247698dc4c3fa8e3e56'
new_commit = b'268db562d0f58df66a392b1f504c01ae84583c17'
assert original.count(old_commit) == 1, 'UNEXPECTED_CANDIDATE_PIN'
modified = original.replace(old_commit, new_commit)
new_sha = '58dea8254fe9daff3e6025576008d7f827e57a239ee9ac3f5ac2dd574fda7ae2'
assert hashlib.sha256(modified).hexdigest() == new_sha, 'RETRY_WORKER_SHA_CHANGED'
p = Path(output)
p.write_bytes(modified)
p.chmod(0o700)
print('CANDIDATE_SOURCE=268db562d0f58df66a392b1f504c01ae84583c17')
print('CORRECTED_TEST_SHA256=' + new_sha)
PY
bash -n "$WORKER"
echo "RUN=$NAME"
set +e
bash "$WORKER" "$NAME" 2>&1 | tee "$LOGS/$NAME.log"
RC=${PIPESTATUS[0]}
set -e
echo "RESULT=$RC"
echo "REPORT=$LOGS/$NAME.tar.gz"
echo "SSH_SESSION=UNCHANGED"
exit "$RC"
)
