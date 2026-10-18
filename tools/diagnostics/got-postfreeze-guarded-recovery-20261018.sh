#!/usr/bin/env bash
# Recover only verifiably interrupted GoT test changes after a host freeze.
# No emulator, compilation, Docker changes, network changes, or Git checkout.
set -Eeuo pipefail
ROOT="$HOME/projects/shadps4"
GOT="$ROOT/GhostOfTsushima"
WORK="$GOT/tests/got-upstream-menu-main-20261017-cached"
SRC="$WORK/source"
BIN="$WORK/build/shadps4"
LOGS="$ROOT/logs"
ID="got-postfreeze-recovery-$(date +%Y%m%d-%H%M%S)"
RECOVERY="$GOT/tests/recovery/$ID"
OUT="$LOGS/$ID"
EXPECTED_BASE="948742f5b1ffebf0dfc4d1b452806c2e649bf35d76cffbb576e2b81bb36b5826"
EXPECTED_HEAD="0b648a826f86fd4ec981872ee482c3fc2ae57699"
BASE_BACKUP="$WORK/bin-backups/shadps4-got-menu-$EXPECTED_BASE"

mkdir -p "$OUT" "$RECOVERY"
exec > >(tee "$OUT/console.log") 2>&1
printf 'RECOVERY_STARTED=%s\n' "$(date -Is)"
if [ ! -d "$SRC/.git" ] || [ ! -d "$WORK/bin-backups" ]; then
    echo 'ERROR=GOT_WORKSPACE_OR_BACKUPS_MISSING_NO_CHANGES'
    exit 2
fi
exec 9>"$WORK/.build.lock"
if ! flock -n 9; then
    echo 'ERROR=GOT_BUILD_LOCK_HELD_NO_CHANGES'
    exit 2
fi
if pgrep -u "$(id -u)" -x shadps4 >/dev/null; then
    echo 'ERROR=EMULATOR_RUNNING_NO_CHANGES'
    exit 2
fi

export SRC BIN GOT RECOVERY EXPECTED_BASE EXPECTED_HEAD BASE_BACKUP OUT
set +e
python3 - <<'PY'
from __future__ import annotations
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile

src=pathlib.Path(os.environ['SRC'])
binary=pathlib.Path(os.environ['BIN'])
got=pathlib.Path(os.environ['GOT'])
restore=pathlib.Path(os.environ['RECOVERY'])
out=pathlib.Path(os.environ['OUT'])
expected=os.environ['EXPECTED_BASE']
expected_head=os.environ['EXPECTED_HEAD']
backup=pathlib.Path(os.environ['BASE_BACKUP'])

results={'binary':'NOT_CHECKED','source':'NOT_CHECKED','restored':[],
         'already_original':[],'conflicts':[],'previous_test_directory':None}

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b''): h.update(chunk)
    return h.hexdigest()

def save_copy(path,root):
    dest=root / path.relative_to(src) if path.is_relative_to(src) else root/path.name
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists(): raise RuntimeError('RECOVERY_BACKUP_ALREADY_EXISTS '+str(dest))
    shutil.copy2(path,dest,follow_symlinks=False)
    return dest

def replace_with_copy(original,target):
    # Target is always an existing regular file within this explicit GoT tree.
    with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.recovery-',delete=False) as fh:
        temp=pathlib.Path(fh.name)
    try:
        shutil.copy2(original,temp)
        os.replace(temp,target)
    finally:
        temp.unlink(missing_ok=True)

try:
    head=subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip()
    results['source_head']=head
    if head!=expected_head:
        results['conflicts'].append('UNEXPECTED_SOURCE_HEAD: '+head)
        raise RuntimeError('Source HEAD changed; no source or binary restoration attempted')

    runs=got/'tests'/'runs'
    test_runs=sorted((p for p in runs.glob('got-material-source-*')
                      if p.is_dir() and not p.is_symlink()),key=lambda p:p.name,reverse=True) if runs.exists() else []
    chosen=next((p for p in test_runs if (p/'info'/'restore-files.txt').is_file()),None)
    if chosen is None:
        results['source']='NO_SAVED_RESTORE_MANIFEST_INSPECT_MANUALLY'
        results['conflicts'].append('No material-source restore-files.txt found; source not modified')
    else:
        results['previous_test_directory']=str(chosen)
        entries={}
        for raw in (chosen/'info'/'restore-files.txt').read_text().splitlines():
            action,sep,rel=raw.partition('|')
            relpath=pathlib.PurePosixPath(rel)
            if sep!='|' or action not in ('RESTORE','REMOVE') or not relpath.parts or relpath.parts[0]!='src' or '..' in relpath.parts or relpath.is_absolute():
                results['conflicts'].append('INVALID_RESTORE_ENTRY: '+raw)
                continue
            if rel in entries and entries[rel]!=action:
                results['conflicts'].append('CONFLICTING_RESTORE_ENTRY: '+raw)
            else: entries[rel]=action
        for rel,action in entries.items():
            target=src/rel
            prior=chosen/'tmp'/'prior'/rel
            candidate=chosen/'tmp'/'candidate'/rel
            if any(p.is_symlink() for p in (target,prior,candidate) if p.exists()):
                results['conflicts'].append('SYMLINK_REFUSED: '+rel)
                continue
            if action=='RESTORE':
                if not prior.is_file():
                    results['conflicts'].append('MISSING_ORIGINAL_BACKUP: '+rel)
                elif target.is_file() and sha(target)==sha(prior):
                    results['already_original'].append(rel)
                elif target.is_file() and candidate.is_file() and sha(target)==sha(candidate):
                    save_copy(target,restore/'overwritten-candidates')
                    replace_with_copy(prior,target)
                    assert sha(target)==sha(prior)
                    results['restored'].append(rel)
                else:
                    results['conflicts'].append('CURRENT_DIFFERS_FROM_BOTH_SAVED_VERSIONS: '+rel)
            elif action=='REMOVE':
                if not target.exists():
                    results['already_original'].append(rel)
                elif target.is_file() and candidate.is_file() and sha(target)==sha(candidate):
                    save_copy(target,restore/'removed-candidates')
                    target.unlink()
                    results['restored'].append('DELETE '+rel)
                else:
                    results['conflicts'].append('UNRECOGNIZED_CANDIDATE_ONLY_PATH: '+rel)
        results['source']='RESTORED_VERIFIED_OVERLAYS' if not results['conflicts'] else 'PARTIAL_OR_UNVERIFIED'

    if not binary.is_file() or binary.is_symlink():
        results['binary']='MISSING_OR_SYMLINKED_NO_CHANGES'
        results['conflicts'].append('Current game executable missing/not regular')
    else:
        current_sha=sha(binary)
        results['binary_before_sha256']=current_sha
        if current_sha==expected:
            results['binary']='ALREADY_VERIFIED_BASELINE'
        elif backup.is_file() and not backup.is_symlink() and sha(backup)==expected:
            save_copy(binary,restore/'prior-binaries')
            replace_with_copy(backup,binary)
            if sha(binary)!=expected: raise RuntimeError('Baseline executable failed verification after restore')
            results['binary']='VERIFIED_BASELINE_RESTORED'
        else:
            results['binary']='UNVERIFIED_BACKUP_NO_CHANGES'
            results['conflicts'].append('Missing SHA256-verified baseline binary backup')
    results['binary_after_sha256']=sha(binary) if binary.is_file() else None
except Exception as exc:
    results['conflicts'].append(type(exc).__name__+': '+str(exc))
finally:
    (out/'recovery-result.json').write_text(json.dumps(results,indent=2)+'\n')
    print('SOURCE_STATUS='+results.get('source','NOT_CHECKED'))
    print('BINARY_STATUS='+results.get('binary','NOT_CHECKED'))
    print('FILES_RESTORED='+str(len(results['restored'])))
    print('CONFLICTS='+str(len(results['conflicts'])))
    for err in results['conflicts'][:15]:print('CAUTION='+err)
    print('RECOVERY_BACKUPS='+str(restore))
sys.exit(2 if results['conflicts'] or results.get('binary') not in ('ALREADY_VERIFIED_BASELINE','VERIFIED_BASELINE_RESTORED') else 0)
PY
REPAIR_RC=$?
set -e

# Limit status to source-only paths; do NOT recurse into the CLI11 symlinked submodule.
DIFF_STATUS=VERIFIED
UNTRACKED_STATUS=VERIFIED
if ! git -C "$SRC" diff --no-ext-diff --name-only HEAD -- src >"$OUT/changed-source-files.txt" 2>"$OUT/source-diff-errors.txt"; then
    DIFF_STATUS=FAILED
    REPAIR_RC=2
fi
if ! git -C "$SRC" ls-files --others --exclude-standard -- src >"$OUT/untracked-source-files.txt" 2>"$OUT/untracked-errors.txt"; then
    UNTRACKED_STATUS=FAILED
    REPAIR_RC=2
fi
if [ -s "$OUT/changed-source-files.txt" ] || [ -s "$OUT/untracked-source-files.txt" ]; then
    echo 'WARNING=SOURCE_FILES_STILL_DIFFER_FROM_BASELINE_CHECK_REPORT'
    REPAIR_RC=2
fi
printf 'SOURCE_DIFF_AUDIT=%s\nUNTRACKED_SOURCE_AUDIT=%s\n' \
    "$DIFF_STATUS" "$UNTRACKED_STATUS" > "$OUT/source-audit-status.txt"

# Previous-boot kernel messages need root access; sudo -n cannot hang waiting
# for a password and this command never changes kernel/journald settings.
if timeout 45s sudo -n journalctl -b -1 -k -n 3500 -o short-iso --no-pager \
     >"$OUT/previous-boot-kernel.txt" 2>"$OUT/previous-boot-errors.txt"; then
    echo 'PRIVILEGED_PREVIOUS_KERNEL_LOG=CAPTURED'
else
    echo 'PRIVILEGED_PREVIOUS_KERNEL_LOG=UNAVAILABLE_NEEDS_SUDO_ACCESS'
fi
sudo -n journalctl -b -1 --since '2026-10-18 13:20:00' \
    --until '2026-10-18 14:38:00' -n 3500 -o short-iso --no-pager \
    >"$OUT/previous-boot-selected.txt" 2>>"$OUT/previous-boot-errors.txt" || true

{
    echo '=== KERNEL FAULT/RESOURCE SIGNALS ==='
    grep -Eai 'amdgpu|gpuvm|ring gfx|timeout|reset|oom|out of memory|killed process|hung task|rcu stall|soft lockup|watchdog|nvme|segfault|compaction|swap|usb' \
       "$OUT/previous-boot-kernel.txt" "$OUT/previous-boot-selected.txt" | tail -300 || true
    echo '=== FINAL SRC CHANGES ==='
    sed -n '1,90p' "$OUT/changed-source-files.txt"
    echo '=== CANDIDATE ONLY SOURCE ==='
    sed -n '1,90p' "$OUT/untracked-source-files.txt"
} >"$OUT/summary.txt"

tar -czf "$OUT.tar.gz" -C "$OUT" .
cat "$OUT/recovery-result.json"
echo "RECOVERY_REPORT=$OUT.tar.gz"
echo "SOURCE_DIFF_AUDIT=$DIFF_STATUS SOURCE_DIFF_LINES=$(wc -l < "$OUT/changed-source-files.txt")"
echo "UNTRACKED_SOURCE_AUDIT=$UNTRACKED_STATUS UNTRACKED_SOURCE_LINES=$(wc -l < "$OUT/untracked-source-files.txt")"
echo 'NO_GAME_BUILD_OR_LAUNCH=YES'
echo 'SSH_SESSION=UNCHANGED'
exit "$REPAIR_RC"
