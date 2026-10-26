#!/usr/bin/env bash
# Native GoT RADV hang/fault capture; exact known baseline SHA and no emulator rebuild.
# Neither modifies kernel/driver settings nor any installed game binary or SSH session.
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
LOGS="$HOME/projects/shadps4/logs"
SCRIPTS="$HOME/projects/shadps4/scripts"
OUT="$LOGS/got-radv-fault-$(date +%Y%m%d-%H%M%S)"
EXPECTED=260ed43abbe538127ab82b3580a48c5f67be19ac4bbdd32ebefd78c73cdaf50d
mkdir -p "$OUT" "$SCRIPTS"
STATUS=PREFLIGHT
CHILD_EXIT=null
finish() {
  local rc=$?
  trap - EXIT
  python3 - "$OUT" "$LOGS" "$EXPECTED" "$STATUS" "$CHILD_EXIT" "$rc" <<'PY' || true
import json,sys
from pathlib import Path
out,logs,sha,status,child,rc=sys.argv[1:]
record={"schema_version":2,"record_type":"LOCAL_GPU_FAULT_CAPTURE",
 "tested_game":"CUSA11456","source_binary_sha256":sha,
 "status":status,"child_runner_exit":int(child) if child.isdigit() else None,
 "wrapper_exit":int(rc),"mode":"RADV_DEBUG=hang on exact known baseline; no emulation or kernel changes",
 "graphics_and_fault_result":"REVIEW_CAPTURE"}
line=json.dumps(record,separators=(',',':'))
Path(out,'trial.jsonl').write_text(line+'\n')
with Path(logs,'emulator-trials-local.jsonl').open('a') as f:f.write(line+'\n')
PY
  tar -C "$OUT" -czf "$OUT.tar.gz" . || true
  printf 'UPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$OUT.tar.gz"
  exit "$rc"
}
trap finish EXIT
for command_name in curl python3 tar date; do
  command -v "$command_name" >/dev/null 2>&1 || { echo "Missing $command_name" >&2; exit 2; }
done
STAMP="$OUT/started.stamp"
touch "$STAMP"
RUNNER="$OUT/proven-runner.sh"
curl -4 --http1.1 --fail --silent --show-error --location --retry 3 \
 'https://raw.githubusercontent.com/Chreece/shadPS4/7ec5ad5036d2d5f3836a8bc5ce58514b428f2812/scripts/got-proven-readbacks-replay-118s-20261026.sh' \
 -o "$RUNNER"
python3 - "$RUNNER" "$EXPECTED" <<'PY'
from pathlib import Path
import sys
s=Path(sys.argv[1]).read_text()
assert s.startswith('#!/usr/bin/env bash\n')
assert ('EXPECTED='+sys.argv[2]) in s
assert 'timeout -s TERM -k 2s 118s "$BIN"' in s
assert 'LOCAL_DIARY_APPENDED' in s
assert 'UPLOAD_THIS' in s
PY
bash -n "$RUNNER"
if command -v umr >/dev/null 2>&1; then
  printf 'UMR=%s\n' "$(command -v umr)" >> "$OUT/host-tools.txt"
else
  printf '%s\n' 'UMR_NOT_INSTALLED=YES (report may have limited pipeline/wave information)' >> "$OUT/host-tools.txt"
fi
printf 'RADV_HANG_MODE=PER_PROCESS_ONLY\nDRIVER_ENV_RADV_DEBUG=hang\nBINARY_SHA256_EXPECTED=%s\n' "$EXPECTED" > "$OUT/experiment.txt"
STATUS=RUNNING
child=0
RADV_DEBUG=hang bash "$RUNNER" 2>&1 | tee "$OUT/child-runner.log" || child=$?
CHILD_EXIT="$child"
ARCHIVE="$(sed -n 's/^UPLOAD_THIS=//p' "$OUT/child-runner.log" | tail -n 1)"
if [[ -n "$ARCHIVE" && -f "$ARCHIVE" ]]; then
  cp -p -- "$ARCHIVE" "$OUT/native-run.tar.gz"
else
  echo 'CHILD_ARCHIVE_NOT_FOUND' >> "$OUT/experiment.txt"
fi
STATUS=CAPTURED_NATIVE_RESULT
# Report collection is read-only: never remove/alter driver reports in $HOME.
python3 - "$OUT" "$STAMP" <<'PY'
from pathlib import Path
import sys, shutil, json
out=Path(sys.argv[1]); stamp=Path(sys.argv[2]).stat().st_mtime
candidates=[]
for p in Path.home().glob('radv_dumps_*'):
    if p.is_dir() and not p.is_symlink() and p.stat().st_mtime >= stamp:
        candidates.append(p)
report_root=out/'driver-reports';report_root.mkdir(exist_ok=True)
preferred={'pipeline.log','trace.log','vm_fault.log','addr_binding_report.log','bo_history.log','bo_ranges.log','dmesg.log','gpu_info.log','app_info.log','umr_waves.log','registers.log','umr_ring.log'}
limit=64*1024*1024;used=0;seen=[];skipped=[]
for folder in sorted(candidates):
    files=sorted((x for x in folder.rglob('*') if x.is_file() and not x.is_symlink()),
        key=lambda p:(0 if p.name in preferred else 1 if p.suffix=='.spv' else 2,p.as_posix()))
    for src in files:
        size=src.stat().st_size
        rel=Path(folder.name)/src.relative_to(folder)
        if size>32*1024*1024 or used+size>limit:
            skipped.append({'path':str(rel),'bytes':size,'reason':'bounded archive size'})
            continue
        target=report_root/rel;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,target);used+=size
        seen.append({'path':str(rel),'bytes':size})
(out/'driver-manifest.json').write_text(json.dumps({'driver_reports_found':len(candidates),'report_paths':[str(x) for x in candidates], 'collected':seen,'skipped':skipped,'collected_bytes':used},indent=2)+'\n')
print('RADV_REPORT_DIRECTORIES='+str(len(candidates)))
print('RADV_COLLECTED_BYTES='+str(used))
PY
if [[ "$child" == 0 || "$child" == 10 || "$child" == 4 ]]; then
  STATUS=FINISHED_EVIDENCE_NEEDS_REVIEW
else
  STATUS=CHILD_RUNNER_UNEXPECTED_FAILURE
fi
echo "CHILD_RUNNER_EXIT=$child"
