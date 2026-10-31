#!/usr/bin/env bash
# Universal GPU-backed descriptor-array experiment with original-GCN capture.
# Isolated branch; original PR117 and native ELF preserved until test passes.
# No title-specific hacks, guessed GPU waterfall values, guest wave ranks,
# packer counters, unverified replay or display/session modifications.
# No game automation, SSH logout, pkill, display/network/Sunshine changes.
set -Eeuo pipefail
umask 077
PROJECT="$HOME/projects/shadps4"
ROOT="$PROJECT/tests/gds-pr117-native-20261029T164137Z-3996104"
SRC="$ROOT/source"
BIN="$ROOT/build/shadps4"
OLD_PIN="7490a113ce79e4da84071fc808443162a5b8ec3b"
INTERMEDIATE="c61b3b29d05990f3938190f9f1bd955a68d6be4e"
PREVIOUS_PIN="763a37e8cbf604e52e85fb3bd62b6e769104ff4c"
PREVIOUS_BRANCH_HEAD="d92ff10faaaa2441ab43df8c4f66821bc53b8afe"
TESTED_PIN="f90b3d008aaeb3322cebc057a0cc1ab91321d229"
PREVIOUS_SOURCE_PIN="23125557a5369d195f2c776784f097aef3745a44"
PROVEN_NATIVE_SOURCE_PIN="e7614336c408e3ea7f95ad51e6f32392b0b4b5f9"
LAST_FAILED_SOURCE_PIN="8497626cba5a5850b87c17abdb5106625788d03f"
LAST_TESTED_SOURCE_PIN="11ca38bc8afcb0709d1f85f55919dfcb26dc2d03"
PIN="d9a5c4ca3b1a4e4cfff3e02f91cde00baf8c19c0"
BRANCH="feature/pr117-gpu-descriptor-tables-20261031"
SHA="9b4300c2666580fe9963eef8f74585e28cb5ad232d8a3c8aa05a732c32871674"
OLD_SHA="$SHA"
GUARD_SHA="ffd231446ad43be68f6ceed675dfc478a0a11b1df59b9d98995035c08c94adbe"
GUARD="/usr/local/sbin/sunshine-session-supervisor"
SUPERVISOR="sunshine-session-supervisor.service"
SUNSHINE="sunshine.service"
LOGS="$PROJECT/logs"
TAG="gds-pr117-gpu-descriptor-$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
OUT="$LOGS/$TAG"
REPORT="$OUT/evidence"
WORK="$ROOT/playtests/$TAG"
ARCHIVE="$LOGS/$TAG.tar.gz"
STATE=PRECHECK
OBS_EXIT=NOT_RUN
mkdir -p "$REPORT" "$WORK"
finish() {
  rc=$?
  trap - EXIT
  set +e
  CONSOLE="$REPORT/guest/full-console.log"
  if [[ -f "$CONSOLE" ]]; then
    python3 - "$CONSOLE" "$REPORT" <<'PY'
import json,sys
from pathlib import Path
src,dst=Path(sys.argv[1]),Path(sys.argv[2])
marks=('PR117_', 'Ordered GDS', 'DS_ORDERED_COUNT',
       'VK_ERROR_DEVICE_LOST', 'SIGSEGV', 'SIGABRT')
selected=[];slots=0;done=0;errors=0
with src.open(encoding='utf-8',errors='replace') as f:
    for line in f:
        slots += 'PR117_REAL_GCN_GPU_CAPTURE_SLOT=' in line
        done += 'PR117_REAL_GCN_CAPTURE_DONE' in line
        errors += ' <Error>' in line or 'VK_ERROR_DEVICE_LOST' in line
        if any(x in line for x in marks) and len(selected)<500:
            selected.append(line[:1200].rstrip())
(dst/'gcn-evidence.log').write_text('\n'.join(selected)+'\n')
(dst/'capture.json').write_text(json.dumps({
    'source':'real emulator console','captured_wave_log_lines':slots,
    'guest_capture_complete':bool(done),'errors':errors,
    'guest_shader_reentry':'NOT_IMPLEMENTED'},indent=2)+'\n')
print('REAL_GUEST_CAPTURE_DONE='+str(bool(done)))
PY
  fi
  if [[ -f "$CONSOLE" ]]; then
    python3 - "$CONSOLE" "$REPORT" <<'PYSUMMARY'
import collections,json,re,sys
from pathlib import Path
src,dst=Path(sys.argv[1]),Path(sys.argv[2])
checks={
    "image_descriptor_tables_bound": "Bound dynamic image descriptor table:",
    "image_descriptor_tables_rejected": "Unsupported image descriptor table:",
    "descriptor_table_capacity_rejected": "Image descriptor table exceeds host or flat-buffer capacity:",
    "incompatible_descriptor_elements": "Incompatible image descriptor array element",
    "gpu_dynamic_srt_pending": "GPU_DYNAMIC_SRT_REQUIRES_SHADER_RESOURCE_LOOKUP",
    "failed_srt_walker": "Failed to compute offset for SRT walker",
    "captured_gds_slots": "PR117_REAL_GCN_GPU_CAPTURE_SLOT=",
    "captured_gds_done": "PR117_REAL_GCN_CAPTURE_DONE",
    "shader_buffer_size_clamps": "Clamped size from 4294967295"
}
counts=collections.Counter()
hashes={k:set() for k in checks}
pattern=re.compile(r'shader=0x([0-9a-fA-F]+)')
with src.open(encoding="utf-8",errors="replace") as stream:
    for line in stream:
        for key,term in checks.items():
            if term in line:
                counts[key]+=1
                match=pattern.search(line)
                if match and len(hashes[key])<128:
                    hashes[key].add(match.group(1).lower())
(dst/"descriptor-array-gpu-summary.json").write_text(json.dumps({
    "source":"unabridged native game console on user's host",
    "counts":{k:counts[k] for k in checks},
    "shader_hashes":{k:sorted(v) for k,v in hashes.items()},
    "guest_post_ds_shader_reentry":"NOT_IMPLEMENTED",
    "gameplay_acceptance":"NOT_ESTABLISHED"
},indent=2)+"\n")
PYSUMMARY
  fi
  {
    echo "STATE=$STATE"
    echo "EXIT_CODE=$rc"
    echo "OBSERVER_EXIT_CODE=$OBS_EXIT"
    echo "SOURCE_SHA=$PIN"
    echo "BINARY_SHA256=$SHA"
    echo "GAME=CUSA34384"
    echo "PREVIOUS_NATIVE_ELF_SHA256=$OLD_SHA"
    echo "GUEST_SHADER_REENTRY=NOT_IMPLEMENTED"
    echo "GAMEPLAY=NOT_CLAIMED"
    echo "SUNSHINE_XORG_SSH_ESDE_NETWORK=UNCHANGED"
  } > "$REPORT/result.txt"
  # Append outcomes to the shared shadPS4 trial diary, including captures
  # stopped before actual GPU re-entry. Never overwrite prior experiments.
  python3 - "$PROJECT/diary/trials.jsonl" "$TAG" "$STATE" "$rc" "$OBS_EXIT" "$PIN" "$SHA" "$ARCHIVE" "$REPORT/capture.json" <<'PYDIARY' || true
import datetime,fcntl,json,pathlib,sys
target,tag,state,rc,observe,source,binary,archive,capture=sys.argv[1:]
info={}
try: info=json.loads(pathlib.Path(capture).read_text(encoding='utf-8'))
except (OSError,ValueError): pass
row=dict(schema_version=2,record_id=tag,
 recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
 scope='universal_shadPS4',record_type='PR117_REAL_GPU_CAPTURE_AFTER_LIVE_XORG_GUARD',
 game_id='CUSA34384',source_commit=source,elf_sha256=binary,
 archive=pathlib.Path(archive).name,result=state,exit_code=int(rc),
 observer_code=str(observe),live_xorg_guard='INSTALLED_HASH_VERIFIED',
 gpu_capture=info.get('guest_capture_complete',False),
 captured_wave_log_lines=info.get('captured_wave_log_lines',0),
 shader_post_ds_reentry='NOT_IMPLEMENTED',
 decision='PENDING_REAL_GCN_GPU_EVIDENCE')
p=pathlib.Path(target);p.parent.mkdir(parents=True,exist_ok=True)
with p.open('a',encoding='utf8') as stream:
 fcntl.flock(stream,fcntl.LOCK_EX)
 stream.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
 stream.flush();fcntl.flock(stream,fcntl.LOCK_UN)
PYDIARY
  tar -C "$OUT" --exclude='full-console.log' \
      -czf "$ARCHIVE" evidence 2>"$REPORT/tar-errors.log"
  echo "STATUS=$STATE"
  echo "UPLOAD_THIS=$ARCHIVE"
  echo "FULL_CONSOLE_RETAINED=$CONSOLE"
  echo "SSH_SESSION=OPEN"
}
trap finish EXIT
for tool in git sha256sum python3 timeout xdpyinfo curl flock tar df awk ldd systemctl pgrep cmp cmake ninja cp ps nice; do
  command -v "$tool" >/dev/null || { echo "MISSING=$tool"; exit 2; }
done
exec 9>"$LOGS/gds-pr117-real-guest-gpu-exclusive.lock"
exec 8>"$LOGS/gow-upstream-got-build.lock"
exec 7>"$LOGS/shadps4-gow-srt-phi.lock"
exec 6>"$LOGS/shadps4-official-indirect-game.lock"
exec 5>"$ROOT/.indirect-native-build.lock"
# Flock each inherited open descriptor in this shell, retaining ownership
# through the observation and Ollama restoration; no unrelated process stopped.
for fd in 9 8 7 6 5; do
  flock -n "$fd" || {
    echo "OTHER_GPU_OR_BUILD_OWNER_ACTIVE_NO_LAUNCH"; exit 2;
  }
done
[[ -d "$SRC/.git" && -f "$BIN" && -x "$BIN" && ! -L "$BIN" ]] || {
  echo "ISOLATED_NATIVE_ELF_OR_SOURCE_MISSING"; exit 3;
}
CURRENT="$(git -C "$SRC" rev-parse HEAD)"
[[ "$CURRENT" == "$OLD_PIN" || "$CURRENT" == "$INTERMEDIATE" ||
   "$CURRENT" == "$PREVIOUS_PIN" || "$CURRENT" == "$PREVIOUS_BRANCH_HEAD" ||
   "$CURRENT" == "$TESTED_PIN" ||
   "$CURRENT" == "$PREVIOUS_SOURCE_PIN" ||
   "$CURRENT" == "$PROVEN_NATIVE_SOURCE_PIN" ||
   "$CURRENT" == "$LAST_FAILED_SOURCE_PIN" ||
   "$CURRENT" == "$LAST_TESTED_SOURCE_PIN" || "$CURRENT" == "$PIN" ]] || {
  echo "UNKNOWN_ISOLATED_SOURCE_REVISION=$CURRENT; NO_CHANGES"; exit 3;
}
[[ -z "$(git -C "$SRC" status --porcelain=v1 --untracked-files=no)" ]] || {
  echo "DIRTY_SOURCE_REFUSED"; exit 3;
}
if [[ "$CURRENT" == "$OLD_PIN" ]]; then
  echo "$OLD_SHA  $BIN" | sha256sum -c --status || {
    echo "ORIGINAL_NATIVE_ELF_NOT_MATCHING_PREVIOUS_PASS"; exit 3;
  }
fi
# The previous guest test could not reap its owned child after SIGKILL.
# Refuse another GPU run or source/build mutation if that process remains.
STATE=CHECK_UNREAPED_GAME_PROCESS
if pgrep -x shadps4 > "$REPORT/prior-game-pids.txt"; then
  # pgrep includes exited zombies (Z), which cannot execute GPU commands.
  LIVE_GAME_PIDS=()
  while IFS= read -r pid; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    state="$(ps -o stat= -p "$pid" 2>/dev/null || true)"
    [[ -z "$state" || "$state" == Z* || "$state" == X* ]] ||
      LIVE_GAME_PIDS+=("$pid")
  done < "$REPORT/prior-game-pids.txt"
  if (( ${#LIVE_GAME_PIDS[@]} )); then
    ps -eo pid,ppid,stat,wchan:32,args > "$REPORT/processes-at-refusal.txt"
    printf 'LIVE_EMULATOR_PID=%s\n' "${LIVE_GAME_PIDS[@]}" > "$REPORT/live-game-pids.txt"
    echo "PREVIOUS_OR_CONCURRENT_EMULATOR_STILL_ALIVE; NO_ACTION"
    exit 4
  fi
  echo "PREVIOUS_EMULATOR_PIDS_ALREADY_EXITED=YES"
fi
STATE=PRESERVE_PREVIOUS_NATIVE_ELF
BASE_ELF_SHA="$(sha256sum "$BIN" | awk '{print $1}')"
printf '%s  %s\n' "$BASE_ELF_SHA" "$BIN" > "$REPORT/old-native-elf.sha256"
PRESERVED="$ROOT/preserved/pr117-pre-srt-ds-$BASE_ELF_SHA"
mkdir -p "$(dirname "$PRESERVED")"
[[ -e "$PRESERVED" ]] || cp --reflink=auto "$BIN" "$PRESERVED"
echo "$BASE_ELF_SHA  $PRESERVED" | sha256sum -c --status || {
  echo "PRESERVED_NATIVE_ELF_MISMATCH"; exit 4;
}
[[ -f "$ROOT/build/build.ninja" && -f "$ROOT/build/CMakeCache.txt" ]] || exit 4
grep -Fq "CMAKE_HOME_DIRECTORY:INTERNAL=$SRC" "$ROOT/build/CMakeCache.txt" || exit 4
STATE=FAST_FORWARD_ONLY_ISOLATED_GDS_SOURCE
if [[ "$CURRENT" != "$PIN" ]]; then
  GIT_TERMINAL_PROMPT=0 git -C "$SRC" fetch --no-tags \
    https://github.com/Chreece/shadPS4.git "refs/heads/$BRANCH" \
    > "$REPORT/fetch.log" 2>&1 || {
       tail -70 "$REPORT/fetch.log"; exit 5
  }
  git -C "$SRC" cat-file -e "$PIN^{commit}" || exit 5
  git -C "$SRC" merge-base --is-ancestor HEAD "$PIN" || {
    echo "UNIVERSAL_SOURCE_DIVERGED"; exit 5;
  }
  git -C "$SRC" switch --quiet --detach "$PIN"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$PIN" ]] || exit 5
[[ -z "$(git -C "$SRC" status --porcelain=v1 --untracked-files=no)" ]] || exit 5
STATE=UNIVERSAL_GCN_VULKAN_SOURCE_AND_OBSERVER_REGRESSIONS
python3 "$SRC/tests/check_ordered_gds_ir.py" > "$REPORT/source-contracts.log" 2>&1 || {
  tail -100 "$REPORT/source-contracts.log"; exit 6
}
grep -Fq 'UNIVERSAL_INDIRECT_COMMAND_WAR_HAZARDS_NORMAL_AND_GCN_FIXED' \
  "$REPORT/source-contracts.log" || exit 6
PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/tests/test_native_gpu_observer.py" \
  > "$REPORT/gpu-observer-tests.log" 2>&1 || {
  tail -100 "$REPORT/gpu-observer-tests.log"; exit 6
}
grep -Fq 'GENERIC_NATIVE_GPU_OBSERVER_TIMEOUT_RECOVERY=PASS' \
  "$REPORT/gpu-observer-tests.log" || exit 6
STATE=STRICT_UNIVERSAL_SRT_HOST_WALKER_SOURCE
python3 "$SRC/tests/check_srt_host_walker.py" \
  >"$REPORT/srt-source-tests.log" 2>&1 || {
    tail -110 "$REPORT/srt-source-tests.log"; exit 6
}
grep -Fq 'DYNAMIC_GPU_PHI_STAYS_ON_GPU=PASS' \
  "$REPORT/srt-source-tests.log" || exit 6
grep -Fq 'ORDERED_GDS_GUEST_COUNTER_NOT_FABRICATED=PASS' \
  "$REPORT/srt-source-tests.log" || exit 6
grep -Fq 'HOST_LANE_UNIFORM_ONLY_GPU_DEPENDENT_SRT_STAYS_GPU=PASS' \
  "$REPORT/srt-source-tests.log" || exit 6
grep -Fq 'HOST_SCALAR_EQUIVALENT_PHI_NO_GPU_BRANCH_GUESS=PASS' \
  "$REPORT/srt-source-tests.log" || exit 6
grep -Fq 'ZOMBIE_NOT_MISCLASSIFIED_AS_LIVE=PASS' \
  "$REPORT/gpu-observer-tests.log" || exit 6
grep -Fq 'GENERIC_X11_SCREENSHOT_FALLBACK=PASS' \
  "$REPORT/gpu-observer-tests.log" || exit 6
STATE=STRICT_GPU_BACKED_DESCRIPTOR_ARRAY_SOURCE
PYTHONDONTWRITEBYTECODE=1 python3 "$SRC/tests/check_pr117_gpu_descriptor_tables.py" \
  >"$REPORT/descriptor-array-source-tests.log" 2>&1 || {
    tail -100 "$REPORT/descriptor-array-source-tests.log"
    exit 6
}
grep -Fq 'GPU_BACKED_DYNAMIC_IMAGE_TABLE_PROVENANCE_AND_BOUNDS=PASS' \
  "$REPORT/descriptor-array-source-tests.log" || exit 6
grep -Fq 'PR117_BUFFER_CACHE_GPU_READBACK_ABI_AND_SYNCHRONIZATION=PASS' \
  "$REPORT/descriptor-array-source-tests.log" || exit 6
grep -Fq 'GPU_DESCRIPTOR_BATCH_POOL_CAPACITY_AND_ADAPTIVE_RETRY=PASS' \
  "$REPORT/descriptor-array-source-tests.log" || exit 6
grep -Fq 'VULKAN_NONUNIFORM_IMAGE_DESCRIPTOR_AND_PERSISTENT_POINTERS=PASS' \
  "$REPORT/descriptor-array-source-tests.log" || exit 6
grep -Fq 'PR117_ORDERED_GDS_NORMAL_EXECUTION_STILL_FAIL_CLOSED=PASS' \
  "$REPORT/descriptor-array-source-tests.log" || exit 6
echo "GPU_DESCRIPTOR_ARRAY_SOURCE_CONTRACTS=PASS"
STATE=EXECUTABLE_GENERIC_DESCRIPTOR_BATCH_REGRESSION
for compiler in g++ clang++-17; do
  command -v "$compiler" >/dev/null || {
    echo "MISSING_DESCRIPTOR_BATCH_COMPILER=$compiler"; exit 6;
  }
  label=gcc
  [[ "$compiler" != clang++-17 ]] || label=clang
  "$compiler" -std=c++20 -O2 -UNDEBUG -Wall -Wextra -Werror -pedantic \
    -I"$SRC/src" "$SRC/tests/descriptor_heap_adaptive_batch.cpp" \
    -o "$REPORT/descriptor-batch-$label" \
    >"$REPORT/descriptor-batch-$label-build.log" 2>&1 || {
      tail -100 "$REPORT/descriptor-batch-$label-build.log"; exit 6;
    }
  "$REPORT/descriptor-batch-$label" \
    >"$REPORT/descriptor-batch-$label-run.log" 2>&1 || {
      tail -100 "$REPORT/descriptor-batch-$label-run.log"; exit 6;
    }
  for expected in \
    'DESCRIPTOR_BATCH_ADAPTIVE_GPU_ARRAY=PASS' \
    'DESCRIPTOR_POOL_ROLLOVER_AND_FRAGMENTATION=PASS' \
    'UNEXPECTED_VULKAN_ERROR_FAIL_CLOSED=PASS'; do
    grep -Fq "$expected" "$REPORT/descriptor-batch-$label-run.log" || exit 6
  done
  rm -f -- "$REPORT/descriptor-batch-$label"
done
echo "GCC_AND_CLANG_DESCRIPTOR_BATCH_REGRESSIONS=PASS"
SRT_XBYAK_INCLUDE=""
for dir in "$SRC/externals/xbyak" "$SRC/externals/xbyak/include" \
           /usr/local/include /usr/include; do
    if [[ -f "$dir/xbyak/xbyak.h" ]]; then
        SRT_XBYAK_INCLUDE="$dir"
        break
    fi
done
[[ -n "$SRT_XBYAK_INCLUDE" ]] || {
    echo "XBYAK_HEADERS_NOT_IN_PINNED_SOURCE"; exit 6
}
STATE=GCC_CLANG_EXECUTABLE_REAL_XBYAK_SRT_JIT
for compiler in g++ clang++-17; do
    command -v "$compiler" >/dev/null || {
        echo "MISSING_JIT_REGRESSION_COMPILER=$compiler"; exit 6
    }
    label=gcc
    [[ "$compiler" != clang++-17 ]] || label=clang
    # Clang 17 cannot instantiate GCC 14 libstdc++'s C++23 tuple-like
    # constructor for Xbyak's unordered_map use (LLVM issue #97842).
    # This standalone executable JIT fixture uses C++20 facilities only;
    # keep GCC's C++23 coverage and run Clang's identical JIT checks in C++20.
    jit_std=c++23
    [[ "$compiler" != clang++-17 ]] || jit_std=c++20
    echo "SRT_JIT_COMPILER=$compiler STANDARD=$jit_std"
    "$compiler" -std="$jit_std" -O2 -UNDEBUG -Wall -Wextra -Werror -pedantic \
       -I"$SRT_XBYAK_INCLUDE" "$SRC/tests/srt_host_offset_jit.cpp" \
       -o "$REPORT/srt-jit-$label" >"$REPORT/jit-$label-build.log" 2>&1 || {
         tail -100 "$REPORT/jit-$label-build.log"; exit 6
       }
    "$REPORT/srt-jit-$label" >"$REPORT/jit-$label-run.log" 2>&1 || {
       tail -100 "$REPORT/jit-$label-run.log"; exit 6
    }
    grep -Fq 'SRT_HOST_FIRST_SET_BIT_JIT=PASS' \
      "$REPORT/jit-$label-run.log" || exit 6
    rm -f "$REPORT/srt-jit-$label"
done
grep -Fq 'ASSERT_MSG(!program.info.uses_ordered_gds' \
  "$SRC/src/shader_recompiler/backend/spirv/emit_spirv.cpp" || {
    echo "NORMAL_GUEST_GDS_GUARD_MISSING"; exit 6
}
STATE=BUILD_ACTUAL_SHADPS4_GCN_AND_UNIVERSAL_INDIRECT_FIX
nice -n 10 cmake --build "$ROOT/build" --target shadps4 --parallel 2 \
  > "$REPORT/build-native.log" 2>&1 || {
    tail -150 "$REPORT/build-native.log"; exit 7
}
SHA="$(sha256sum "$BIN" | awk '{print $1}')"
sha256sum "$BIN" > "$REPORT/updated-elf.sha256"
grep -E 'warning:|error:|fatal error:' "$REPORT/build-native.log" \
  > "$REPORT/compiler-diagnostics.log" || true
[[ ! -s "$REPORT/compiler-diagnostics.log" ]] || {
    tail -90 "$REPORT/compiler-diagnostics.log"; exit 7
}
echo "UNIVERSAL_HOST_SRT_PLUS_GDS_NATIVE_LINK=PASS ELF=$SHA"
echo "TRUE_POST_DS_GUEST_SHADER_REENTRY=NOT_IMPLEMENTED"

STATE=VERIFY_INSTALLED_SUPERVISOR_LIVE_XORG_GUARD
[[ -f "$GUARD" && ! -L "$GUARD" && -r "$GUARD" ]] || {
  echo "GUARDED_SUPERVISOR_NOT_READABLE; NO_GAME"; exit 4;
}
echo "$GUARD_SHA  $GUARD" | sha256sum -c --status || {
  echo "PR117_LIVE_XORG_GUARD_NOT_INSTALLED_OR_HAS_CHANGED; NO_GAME"; exit 4;
}
systemctl is-active --quiet "$SUPERVISOR" || {
  echo "PR117_SUPERVISOR_NOT_ACTIVE; NO_GAME"; exit 4;
}
systemctl is-active --quiet "$SUNSHINE" || {
  echo "SUNSHINE_NOT_ACTIVE; NO_GAME"; exit 4;
}
systemctl is-active --quiet sunshine-amdgpu-reset-watch.service || {
  echo "AMDGPU_RESET_WATCHER_NOT_ACTIVE; NO_GAME"; exit 4;
}
systemctl show "$SUPERVISOR" -p ActiveState -p MainPID -p ExecStart \
    > "$REPORT/supervisor-before.txt"
systemctl show "$SUNSHINE" -p ActiveState -p MainPID \
    > "$REPORT/sunshine-unit-before.txt"
grep -Fq "$GUARD" "$REPORT/supervisor-before.txt" || {
  echo "SUPERVISOR_UNIT_POINTS_TO_UNVERIFIED_BINARY"; exit 4;
}
pgrep -ax Xorg > "$REPORT/xorg-pids-before.txt" || {
  echo "XORG_IS_NOT_RUNNING; NO_GAME"; exit 4;
}
grep -Fq 'Xorg :0 ' "$REPORT/xorg-pids-before.txt" || {
  echo "HEADLESS_XORG_DISPLAY_ZERO_NOT_RUNNING; NO_GAME"; exit 4;
}
pgrep -ax sunshine > "$REPORT/sunshine-pids-before.txt" || {
  echo "SUNSHINE_IS_NOT_RUNNING; NO_GAME"; exit 4;
}
echo "LIVE_XORG_GUARD=VERIFIED_AND_RUNNING"
ldd "$BIN" > "$REPORT/ldd.txt"
! grep -q 'not found' "$REPORT/ldd.txt" || {
  echo "RUNTIME_LIBRARY_MISSING"; exit 3;
}
if command -v pgrep >/dev/null && pgrep -x shadps4 > "$REPORT/existing-pids.txt"; then
  echo "EXISTING_GAME_PROCESS_REFUSE_PARALLEL"; exit 3;
fi
STATE=REUSE_X11_NO_CHANGES
DISPLAY_OK=":0"
if ! DISPLAY=:0 timeout 4 xdpyinfo -display :0 \
       >"$REPORT/display-check.log" 2>&1; then
  echo "VERIFIED_XORG_ZERO_IS_NOT_ACCESSIBLE; NO_GAME"
  exit 4
fi
export DISPLAY="$DISPLAY_OK"
{
  echo "DISPLAY=$DISPLAY"
  pgrep -ax sunshine || true
  pgrep -ax Xorg || true
} > "$REPORT/display-before.txt"
STATE=PIN_PROVEN_OBSERVER_AND_OLLAMA_POLICY
OBSERVER="$WORK/native-observer.py"
VRAM="$WORK/ollama-vram-session.py"
cp -- "$SRC/tools/development/shadps4_native_gpu_observer.py" "$OBSERVER"
cp -- "$SRC/tools/development/shadps4_ollama_vram_session.py" "$VRAM"
[[ "$(git hash-object "$OBSERVER")" == "$(git -C "$SRC" rev-parse HEAD:tools/development/shadps4_native_gpu_observer.py)" ]] || exit 8
[[ "$(git hash-object "$VRAM")" == "$(git -C "$SRC" rev-parse HEAD:tools/development/shadps4_ollama_vram_session.py)" ]] || exit 8
STATE=ISOLATED_PORTABLE_PROFILE_NO_LIVE_SAVES
PROFILE=""
for d in "$HOME/user" "$HOME/.local/share/shadPS4" \
  "$PROJECT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/user"; do
  [[ -d "$d" && ! -L "$d" && -f "$d/config.json" ]] || continue
  PROFILE="$d"
  break
done
[[ -n "$PROFILE" ]] || { echo "PORTABLE_PROFILE_NOT_FOUND"; exit 5; }
KIB="$(df -Pk "$ROOT" | awk 'END{print $4}')"
[[ "$KIB" =~ ^[0-9]+$ ]] && (( KIB >= 3145728 )) || {
  echo "DISK_HEADROOM_BELOW_3_GIB"; exit 5;
}
mkdir -p "$WORK/user"
python3 - "$PROFILE" "$WORK/user" <<'PY'
import json,os,pathlib,shutil,sys
src,dst=map(pathlib.Path,sys.argv[1:])
src=src.resolve(strict=True)
skip={"log","logs","shader","screenshots","captures","cache","tmp","temp","dumps","download"}
files=[];total=0
for root,dirs,names in os.walk(src,followlinks=False):
    base=pathlib.Path(root)
    dirs[:]=[d for d in dirs if not (base/d).is_symlink()
             and not (base==src and d.lower() in skip)]
    for name in names:
        p=base/name
        if p.is_symlink() or not p.is_file():continue
        total+=p.stat().st_size
        if total>2*1024**3 or len(files)>100000:
            raise SystemExit("PROFILE_COPY_LIMIT_EXCEEDED")
        files.append((p,p.relative_to(src)))
for p,rel in files:
    q=dst/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
cfg=dst/"config.json"
data=json.loads(cfg.read_text(encoding="utf-8"))
if not isinstance(data,dict):raise SystemExit("INVALID_CONFIG")
g=data.get("General")
if g is not None:
    if not isinstance(g,dict):raise SystemExit("INVALID_GENERAL")
    for k in ("home_dir","sys_modules_dir","font_dir","addon_install_dir"):
        if k in g:
            if not isinstance(g[k],str):raise SystemExit("INVALID_CONFIG_PATH")
            g[k]=""
    cfg.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(f"ISOLATED_PROFILE_COPIED_FILES={len(files)}",flush=True)
PY
STATE=ACTUAL_GUEST_GCN_GPU_CAPTURE_ONCE
export SHADPS4_PR117_CAPTURE_ONCE=1
if [[ -z "$(printenv XDG_RUNTIME_DIR || true)" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
echo "CAPTURE=REAL_GCN_FIRST_PASS_ONLY; NO_FAKE_REENTRY"
echo "GAME=CUSA34384; VRAM_UNLOAD_OLLAMA_ONLY_IF_BELOW_8_GIB"
echo "OBSERVE=118s + 2s terminate ONLY owned child process; no automated input"
set +e
python3 -B "$VRAM" --observer "$OBSERVER" --binary "$BIN" \
  --sha256 "$SHA" --game CUSA34384 --cwd "$WORK" \
  --report "$REPORT/guest" --seconds 118 \
  --state "$REPORT/ollama-session.json" --minimum-gib 8 \
  >"$REPORT/observer-and-vram.log" 2>&1
OBS_EXIT=$?
set -e
tail -n 65 "$REPORT/observer-and-vram.log"
{
  echo "DISPLAY=$DISPLAY"
  pgrep -ax sunshine || true
  pgrep -ax Xorg || true
} > "$REPORT/display-after.txt"
pgrep -ax Xorg > "$REPORT/xorg-pids-after.txt" || true
pgrep -ax sunshine > "$REPORT/sunshine-pids-after.txt" || true
systemctl show "$SUPERVISOR" -p ActiveState -p MainPID \
  > "$REPORT/supervisor-after.txt"
systemctl show "$SUNSHINE" -p ActiveState -p MainPID \
  > "$REPORT/sunshine-unit-after.txt"
if ! cmp -s "$REPORT/xorg-pids-before.txt" "$REPORT/xorg-pids-after.txt" ||
   ! cmp -s "$REPORT/sunshine-pids-before.txt" "$REPORT/sunshine-pids-after.txt"; then
  STATE=DISPLAY_SERVER_OR_SUNSHINE_REPLACED_DURING_GPU_TEST
  echo "DISPLAY_PROCESS_INTEGRITY=FAILED"
  exit 8
fi
echo "DISPLAY_PROCESS_INTEGRITY=PRESERVED"
if [[ -f "$REPORT/guest/full-console.log" ]] &&
   grep -Fq 'PR117_REAL_GCN_CAPTURE_DONE' "$REPORT/guest/full-console.log"; then
  STATE=AUTHENTIC_FIRST_PASS_GCN_GPU_CAPTURE_REACHED
  echo "REAL_GCN_GPU_CAPTURE=CONFIRMED"
  echo "GUEST_SHADER_REENTRY=NOT_IMPLEMENTED"
else
  STATE=REAL_GAME_RUN_WITHOUT_AUTHENTIC_GCN_CAPTURE
  echo "REAL_GCN_GPU_CAPTURE=NOT_CONFIRMED"
  echo "GUEST_SHADER_REENTRY=NOT_IMPLEMENTED"
  exit 7
fi
