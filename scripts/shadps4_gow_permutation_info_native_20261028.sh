#!/usr/bin/env bash
# PR94 shader-permutation metadata ownership correction, source-extracted CPU tests,
# native build, one bounded GoW test, and restoration of the exact previous baseline.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
LOGS="$ROOT/logs"
TREE="$ROOT/Upstream/tests/gow-hle-abi-20261027-194629-907086"
SRC="$TREE/source"
BUILD="$TREE/build"
BIN="$BUILD/shadps4"
BASE="bbe1a72840733afccba5f48fb4b5eb29a1c96128"
BASE_ELF="65fafc436831d76380db349b89c89fe610d16325981870ac073d8b9ed0f010b9"
ORIGINAL="$ROOT/GodOfWarRagnarok/build/shadps4"
ORIGINAL_SHA="8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e"
PR91="$TREE/old-binaries/pr91-46195c52-native-shadps4"
PR91_SHA="46195c52e9dd3fb3921b0b5c99a233edf4b484ecceb7794c12ae864626d6b3af"
mkdir -p "$ROOT/scripts" "$ROOT/diary" "$LOGS"
if (( $# == 0 )); then
 TAG="gow-permutation-info-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
 LOG="$LOGS/$TAG.log"
 (set -C; : > "$LOG") || exit 3
 nohup setsid -w bash "$0" --worker "$TAG" </dev/null >"$LOG" 2>&1 &
 PID=$!
 disown "$PID" 2>/dev/null || true
 printf 'WORKER_PID=%s\nMONITOR=tail -F %q\nUPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$PID" "$LOG" "$LOGS/$TAG.tar.gz"
 exit 0
fi
[[ $# == 2 && "$1" == --worker && "$2" =~ ^gow-permutation-info-[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]] || exit 3
TAG="$2"
E="$LOGS/$TAG-evidence"
ARCHIVE="$LOGS/$TAG.tar.gz"
CANDIDATE="$ROOT/GodOfWarRagnarok/tests/$TAG/candidate"
GAME_TAG="gow-permutation-game-$(date -u +%Y%m%d-%H%M%S)-$BASHPID"
GAME_ARCHIVE="$LOGS/$GAME_TAG.tar.gz"
GAME_REPORT="$ROOT/GodOfWarRagnarok/tests/playtest-$GAME_TAG/evidence/game"
STATE=PREFLIGHT
PATCHED=NO
NEW_PIN=''
NEW_SHA=''
GAME_RC=NOT_RUN
SNAP="$TREE/old-binaries/pr94-65fafc43-native-shadps4"
[[ ! -e "$E" ]] || exit 3
mkdir -p "$E/tools"
finish() {
 local rc=$? restore=NOT_NEEDED
 trap - EXIT
 set +e
 if [[ "$PATCHED" == YES ]]; then
  python3 - "$SRC" "$BASE" "$NEW_PIN" "$E" <<'PYRESTORE'
import hashlib,json,pathlib,subprocess,sys
src,base,pin,e=sys.argv[1:];s=pathlib.Path(src);e=pathlib.Path(e)
def git(*args): return subprocess.check_output(['git','-C',src,*args],text=True).strip()
def blob(b): return hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
head=git('rev-parse','HEAD')
if pin and head==pin:
 if git('status','--porcelain','--untracked-files=no'):raise SystemExit('SOURCE_RESTORE_REFUSED_EXTERNAL_CHANGES')
 subprocess.run(['git','-C',src,'switch','-q','--detach',base],check=True)
elif head==base and (e/'source-manifest.json').exists():
 m=json.loads((e/'source-manifest.json').read_text())['files']
 changed=set(git('diff','HEAD','--name-only').splitlines())
 if changed-set(m):raise SystemExit('SOURCE_RESTORE_REFUSED_OTHER_CHANGED_FILES')
 for p,h in m.items():
  if blob((s/p).read_bytes()) not in (h['new_blob'],h['old_blob']):raise SystemExit('SOURCE_RESTORE_REFUSED_'+p)
 for p in m:(s/p).write_bytes((e/'original'/p).read_bytes())
 subprocess.run(['git','-C',src,'reset','-q','HEAD','--',*m],check=True)
else:raise SystemExit('SOURCE_RESTORE_REFUSED_HEAD_CHANGED')
print('PR94_SOURCE_RESTORED=YES')
PYRESTORE
  if [[ $? == 0 ]]; then
   restore=SOURCE_RESTORED
   if [[ -f "$SNAP" && ! -L "$SNAP" && "$(sha256sum "$SNAP" | awk '{print $1}')" == "$BASE_ELF" ]] && cp --reflink=auto -- "$SNAP" "$BIN" && [[ "$(sha256sum "$BIN" | awk '{print $1}')" == "$BASE_ELF" ]]; then
    restore="$restore,PR94_ELF_RESTORED"
   else
    restore="$restore,ELF_RESTORE_FAILED";rc=10
   fi
  else
   restore=REFUSED_EXTERNAL_SOURCE_CHANGE;rc=10
  fi
 fi
 [[ ! -f "$GAME_ARCHIVE" ]] || cp -- "$GAME_ARCHIVE" "$E/game-run.tar.gz"
 {
  printf 'STATE=%s\nEXIT_CODE=%s\nBASE_SOURCE=%s\nCANDIDATE_SOURCE=%s\nCANDIDATE_SHA256=%s\nCANDIDATE_RETAINED=%s\nGAME_RC=%s\nRESTORE=%s\n' "$STATE" "$rc" "$BASE" "$NEW_PIN" "$NEW_SHA" "$CANDIDATE/shadps4" "$GAME_RC" "$restore"
  printf 'CURRENT_HEAD=%s\nCURRENT_ELF_SHA256=%s\nPROTECTED_GOW_SHA256=%s\n' "$(git -C "$SRC" rev-parse HEAD 2>/dev/null)" "$(sha256sum "$BIN" 2>/dev/null | awk '{print $1}')" "$(sha256sum "$ORIGINAL" 2>/dev/null | awk '{print $1}')"
 } >"$E/result.txt"
 python3 - "$ROOT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$NEW_PIN" "$NEW_SHA" "$GAME_REPORT/native-result.json" "$ARCHIVE" "$restore" <<'PYDIARY'
import datetime,fcntl,json,os,pathlib,sys
p,tag,state,rc,pin,sha,result,archive,restore=sys.argv[1:]
r=pathlib.Path(result);game=json.loads(r.read_text()) if r.is_file() else None
row={'schema_version':2,'record_id':tag,'recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
'record_type':'GENERIC_SHADER_PERMUTATION_INFO_RESTORED_ON_PR94','game_id':'CUSA34384',
'source_commit':pin,'base_commit':'bbe1a72840733afccba5f48fb4b5eb29a1c96128','binary_sha256':sha,
'result':state,'exit_code':int(rc),'tested_games':['CUSA34384'] if game else [],'native_result':game,
'restore':restore,'archive':os.path.basename(archive),'decision':'CANDIDATE_RETAINED_NOT_PROMOTED_PENDING_GPU_VISUAL_REVIEW',
'next_test':'Verify source-extracted regression, native compilation, variant telemetry, actual GPU outcome and real guest pixels. Do not infer game success from CPU tests or startup overlay.'}
with open(p,'a') as f:
 fcntl.flock(f.fileno(),fcntl.LOCK_EX);f.write(json.dumps(row,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
PYDIARY
 tar -czf "$ARCHIVE" --exclude='model/baseline' --exclude='model/corrected' -C "$LOGS" "$TAG-evidence" "$TAG.log"
 printf 'RESULT=%s\nRESTORE=%s\nUPLOAD_THIS=%s\nSSH_SESSION=OPEN\n' "$STATE" "$restore" "$ARCHIVE"
 exit "$rc"
}
trap finish EXIT
for command in git flock sha256sum python3 curl tar cmake ninja g++ ldd nice df pgrep; do
 command -v "$command" >/dev/null || { echo "MISSING_COMMAND=$command";exit 3; }
done
exec 6>"$LOGS/gow-permutation-info-exclusive.lock"
exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/gow-hle-stack-build.lock"
flock -n 6 && flock -n 9 && flock -n 8 && flock -n 7 || { echo 'ANOTHER_SHARED_BUILD_OR_TEST_ACTIVE_NO_CHANGES';exit 3; }
STATE=VERIFY_EXACT_PR94_AND_PROTECTED_GOW
if git -C "$SRC" symbolic-ref -q HEAD >/dev/null; then echo SOURCE_NOT_DETACHED_REFUSE_BRANCH_MUTATION;exit 4;fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$BASE" && -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || exit 4
[[ -f "$BUILD/build.ninja" && -x "$BIN" && ! -L "$BIN" && "$(sha256sum "$BIN" | awk '{print $1}')" == "$BASE_ELF" ]] || exit 4
[[ -f "$ORIGINAL" && ! -L "$ORIGINAL" && "$(sha256sum "$ORIGINAL" | awk '{print $1}')" == "$ORIGINAL_SHA" ]] || exit 4
[[ -f "$PR91" && ! -L "$PR91" && "$(sha256sum "$PR91" | awk '{print $1}')" == "$PR91_SHA" ]] || exit 4
grep -Fxq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$BUILD/CMakeCache.txt" || { echo WRONG_CMAKE_SOURCE;exit 4; }
grep -Fxq "CMAKE_BUILD_TYPE:STRING=Release" "$BUILD/CMakeCache.txt" || { echo NOT_RELEASE_CMAKE;exit 4; }
if pgrep -x shadps4 >"$E/active-emulators.txt";then echo 'ANOTHER_EMULATOR_RUNNING_NO_CHANGES';exit 4;fi
FREE="$(df -Pk "$TREE" | awk 'END {print $4}')"
[[ "$FREE" =~ ^[0-9]+$ && "$FREE" -ge 3145728 ]] || { echo 'FREE_DISK_BELOW_3GIB';exit 4; }
mkdir -p "$TREE/old-binaries"
[[ -e "$SNAP" || -L "$SNAP" ]] || cp --reflink=auto -- "$BIN" "$SNAP"
[[ -f "$SNAP" && ! -L "$SNAP" && "$(sha256sum "$SNAP" | awk '{print $1}')" == "$BASE_ELF" ]] || exit 4
# Fetch immutable, locally regression-tested source tools; no prebuilt executable.
HELPER_REF="f895ffcd438ff14313246a89d6f8cd94e16c0157"
while read -r NAME HASH; do
 curl -4fsSL --retry 3 --retry-delay 2 "https://raw.githubusercontent.com/Chreece/shadPS4/$HELPER_REF/scripts/pr94_permutation/$NAME" -o "$E/tools/$NAME"
 [[ "$(git hash-object "$E/tools/$NAME")" == "$HASH" ]] || { echo "HELPER_HASH_FAILED=$NAME";exit 5; }
done <<'HELPERS'
fix_permutation_metadata.py f30d7924afec08c64e3f23de394d7500b66f12e0
test_permutation_metadata.py c19d6530ba1da7a7bc164224acb4f57fadfab33e
HELPERS
python3 - "$E/tools" <<'PYPAYLOAD'
import ast,pathlib,sys
for p in pathlib.Path(sys.argv[1]).glob('*.py'):ast.parse(p.read_text())
print('PINNED_SOURCE_TOOLS_SYNTAX=PASS')
PYPAYLOAD
cat > "$E/tools/reviewed-trial.json" <<'REVIEWED_TRIAL'
{"schema_version":2,"record_id":"gow-gpu-submit-trace-20261028T111830Z-1380900-reviewed","recorded_at":"2026-10-28","record_type":"GOW_QUEUE_SEPARATED_TRACE_NATIVE_FAILURE_AND_PERMUTATION_OWNERSHIP_REVIEW","game_id":"CUSA34384","tested_games":["CUSA34384"],"archive":"gow-gpu-submit-trace-20261028T111830Z-1380900.tar.gz","archive_sha256":"c4e099a8600cfb3cc08dcea1d61124f0f39960ef075debaf892318b9abd65064","game_archive":"gow-gpu-trace-20261028-111830-1380904.tar.gz","game_archive_sha256":"7f9962419a1aa3a996ebe4d25b9e56df15524ae171989d24aa4b58d1c5b5e6cd","source_commit":"0fcd2676e34001be59f450ec3570786d28165f1b","binary_sha256":"0a0c0625c0e8d6e5fec5f53089e5f878e79a1705b0093716ea0d6938836ba874","native_build":"PASS","compiler_warnings":0,"cpu_queue_fixture":"PASS","runtime_seconds":22.385,"game_exit_code":-11,"device_loss":true,"trace":{"failed_submit_tick":13,"cached_completion":1,"events":1614,"compute_events":1477,"draw_events":124,"submits":13,"overflow":0,"first_indirect_shader":"0xcfe2c248","first_indirect_guest_address":"0x21d4157e0","first_indirect_tick":2},"trace_limit":"CPU-recorded commands and cached completion do not establish the guilty GPU operation or live first-incomplete tick.","original_elf_unchanged":true,"pr94_source_and_elf_restored":true,"visual":"NOT_OBSERVED","audio":"NOT_OBSERVED","gameplay":"NOT_OBSERVED","ollama_unloaded":false,"ollama_original_loaded_models":[],"source_defect":"PR94 GetProgram compiles new_info but serializes/returns first Program::info; Module owns no independent Info. Disk preload also substitutes first metadata. A prior generic ownership correction exists in source 803bb428731be794bdc44e38b56313d030a7403a.","actual_runtime_relevance":"Archived console explicitly compiles vs 0x2b3f2254 (permutation); no shader-hash condition is used in the correction.","causality":"Metadata defect is source-proven; whether it causes this RADV loss is not proven.","local_validation":{"method":"Extracted production Program/GetProgram/LoadPipelineStage and specialization equality against deterministic host stubs, not full emulator build. GCC C++23 -Werror ASan UBSan.","old_defect_reproduced":true,"new_variants":79,"cache_reuse":"PASS","pointer_stability":"PASS","array_binding_count":"PASS","reload_independent_metadata":"PASS","occupied_cache_slots":"REJECTED","indirect_and_lds_program_keys":"PRESERVED"},"decision":"RETAIN_TRACE_EVIDENCE; RESTORE_GENERIC_PERMUTATION_METADATA_OWNERSHIP_AS_UNPROMOTED_CANDIDATE; NEVER_CLAIM_GPU_FIX_BEFORE_NATIVE_TEST","next_test":"Pinned four-file ownership correction with CPU regression then native object/link gate and one bounded GoW test; preserve new candidate separately and restore PR94."}
REVIEWED_TRIAL
# Record the just-reviewed queue trace in the same universal ledger before changing source.
python3 - "$ROOT/diary/trials.jsonl" "$E/tools/reviewed-trial.json" <<'PYREVIEW'
import fcntl,json,os,sys
p,record=sys.argv[1:];row=json.load(open(record))
with open(p,'a+') as f:
 fcntl.flock(f.fileno(),fcntl.LOCK_EX);f.seek(0)
 found=False
 for line in f:
  try:found |= json.loads(line).get('record_id')==row['record_id']
  except (ValueError,AttributeError):continue
 if not found:
  f.seek(0,2);f.write(json.dumps(row,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
PYREVIEW
STATE=RESTORE_OWNED_PERMUTATION_METADATA
python3 -B "$E/tools/fix_permutation_metadata.py" --source "$SRC" --evidence "$E"
PATCHED=YES
STATE=SOURCE_EXTRACTED_ASAN_UBSAN_REGRESSION
python3 -B "$E/tools/test_permutation_metadata.py" --source "$SRC" --evidence "$E/model" >"$E/cpu-regressions.log" 2>&1 || { tail -100 "$E/cpu-regressions.log";exit 5; }
cat "$E/cpu-regressions.log"
STATE=COMMIT_EXACT_LOCAL_CANDIDATE
PATHS=(src/video_core/renderer_vulkan/vk_pipeline_cache.cpp src/video_core/renderer_vulkan/vk_pipeline_cache.h src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp src/shader_recompiler/specialization.h)
git -C "$SRC" diff --check
EXPECTED="$(printf '%s\n' "${PATHS[@]}" | sort)"
[[ "$(git -C "$SRC" diff --name-only | sort)" == "$EXPECTED" ]] || exit 5
git -C "$SRC" add -- "${PATHS[@]}"
git -C "$SRC" -c user.name='Local emulator experiment' -c user.email='local-experiment@invalid' -c core.hooksPath=/dev/null -c commit.gpgSign=false commit -q -m 'shader cache: retain exact metadata per permutation on pinned PR94'
NEW_PIN="$(git -C "$SRC" rev-parse HEAD)"
[[ "$(git -C "$SRC" rev-parse HEAD^)" == "$BASE" && -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || exit 5
git -C "$SRC" show --format=fuller --stat HEAD >"$E/committed-source.txt"
STATE=COMPILE_REAL_CHANGED_PRODUCTION_OBJECTS
for OBJ in CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_pipeline_cache.cpp.o CMakeFiles/shadps4.dir/src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp.o; do
 nice -n 10 ninja -C "$BUILD" -j 2 "$OBJ" >>"$E/native-objects.log" 2>&1 || { tail -120 "$E/native-objects.log";exit 6; }
done
STATE=NATIVE_FULL_LINK
nice -n 10 cmake --build "$BUILD" --target shadps4 --parallel 2 >"$E/native-link.log" 2>&1 || { tail -120 "$E/native-link.log";exit 7; }
NEW_SHA="$(sha256sum "$BIN" | awk '{print $1}')"
[[ -x "$BIN" && "$NEW_SHA" != "$BASE_ELF" ]] || exit 7
ldd "$BIN" >"$E/ldd.txt"
! grep -q 'not found' "$E/ldd.txt" || exit 7
grep -Ein '(^|[ :])warning:' "$E/native-objects.log" "$E/native-link.log" >"$E/compiler-warnings.txt" || true
mkdir -p "$CANDIDATE"
cp --reflink=auto -- "$BIN" "$CANDIDATE/shadps4"
[[ "$(sha256sum "$CANDIDATE/shadps4" | awk '{print $1}')" == "$NEW_SHA" ]] || exit 7
cp -- "$E/source.patch" "$E/source-manifest.json" "$E/committed-source.txt" "$CANDIDATE/"
printf 'SOURCE=%s\nBINARY_SHA256=%s\n' "$NEW_PIN" "$NEW_SHA" >"$CANDIDATE/provenance.txt"
echo "PERMUTATION_FIX_NATIVE_BUILD=PASS SOURCE=$NEW_PIN ELF=$NEW_SHA"
STATE=DERIVE_PINNED_GAME_RUNNER
TEMPLATE="$E/template.sh"
DERIVED="$E/game-runner.sh"
curl -4fsSL --retry 3 --retry-delay 2 'https://raw.githubusercontent.com/Chreece/shadPS4/bb0321cddb6254ac3d237e2c6e53f7a2645a83f5/scripts/shadps4_official_pr5479_pr88_gow_native_20261027.sh' -o "$TEMPLATE"
[[ "$(git hash-object "$TEMPLATE")" == 'b773b5936e5bc72ae631c3c0f9ac20578fa9f7f8' ]] || exit 8
python3 - "$TEMPLATE" "$DERIVED" "$TREE" "$NEW_PIN" "$NEW_SHA" <<'PYDERIVE'
from pathlib import Path
import sys
old,new,tree,pin,sha=sys.argv[1:];s=Path(old).read_text()
for a,b,n in [
('gow-official-indirect-','gow-permutation-game-',2),
('CANDIDATE="$ROOT/Upstream/tests/shadps4-indirect-hazards-20261027-170922-3777629"',f'CANDIDATE="{tree}"',1),
('43d7d22360dee7a545ef9e81557fb8ae9109f7a4',pin,2),
('3a4ced9bb56440edbf4b273d2bc71564b0555c42191c14083ba7ee47d0782c75',sha,2),
('OFFICIAL_MAIN_PR5479_PR88_NATIVE_GOW','PR94_PERMUTATION_METADATA_NATIVE_GOW',1),
('"recorded_at":"2026-10-27"','"recorded_at":"2026-10-28"',1)]:
 if s.count(a)!=n:raise SystemExit('GAME_TEMPLATE_DRIFT '+a[:60])
 s=s.replace(a,b)
a='''exec 9>"$LOGS/gow-upstream-got-build.lock"
exec 8>"$LOGS/shadps4-gow-srt-phi.lock"
exec 7>"$LOGS/shadps4-official-indirect-game.lock"
flock -n 9 && flock -n 8 && flock -n 7 || {
    echo "CONCURRENT_GAME_BUILD_LOCK_UNAVAILABLE_NO_ACTION"; exit 3;
}'''
b='''[[ "$GOW_PERMUTATION_PARENT_LOCKS" == YES ]] || { echo PARENT_LOCKS_NOT_HELD;exit 3; }'''
if s.count(a)!=1:raise SystemExit('GAME_TEMPLATE_LOCK_DRIFT')
s=s.replace(a,b)
if '--seconds 118' not in s or 'GAME="CUSA34384"' not in s or 'shutil.copy2(old,dest)' not in s:raise SystemExit('GAME_GUARD_CHANGED')
Path(new).write_text(s)
PYDERIVE
bash -n "$DERIVED"
STATE=ONE_NATIVE_GOW_RUNTIME
export GOW_PERMUTATION_PARENT_LOCKS=YES
GAME_RC=0
bash "$DERIVED" --worker "$GAME_TAG" >"$LOGS/$GAME_TAG.log" 2>&1 || GAME_RC=$?
unset GOW_PERMUTATION_PARENT_LOCKS
python3 - "$GAME_REPORT" "$E" <<'PYCOLLECT'
from pathlib import Path
import shutil,sys
r,e=map(Path,sys.argv[1:]);p=r/'full-console.log'
if p.is_file():
 if p.stat().st_size<=32*1024*1024:shutil.copy2(p,e/'runtime-full-console.log')
 with p.open(errors='replace') as f,(e/'permutation-and-failure.txt').open('w') as out:
  for line in f:
   if any(x in line for x in ('ShaderPermutationInfo:','(permutation)','Device lost','context is lost','Assertion Failed','PipelineCacheEnabled')):out.write(line)
PYCOLLECT
if [[ "$GAME_RC" != 0 ]]; then STATE=BUILD_PASSED_GAME_RUNTIME_FAILED;exit "$GAME_RC";fi
STATE=BUILD_PASSED_GAME_OBSERVED_VISUAL_REVIEW_PENDING
