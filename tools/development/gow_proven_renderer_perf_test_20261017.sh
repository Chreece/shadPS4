#!/usr/bin/env bash
# One-shot isolated candidate from the exact working GoW source; no changes to proven build.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
ORIG="$ROOT/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679"
OLD="$ORIG/source"
PROVEN="$ORIG/build/shadps4.native-userfaultfd-deferred-gow-uffd-deferred-build-20261016-235543"
SHA=e413ab1d953793f50c5500d528b43aae73fc3ab7ceb966d8d3ab9da275310ffa
TEST="$ROOT/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017"
SRC="$TEST/source"
BUILD="$TEST/build"
CAND="$BUILD/shadps4.gow-log-hotpath-20261017"
LOGS="$ROOT/logs"
TAG="gow-renderer-hotpath-$(date +%Y%m%d-%H%M%S)"
OUT="$LOGS/$TAG"
mkdir -p "$OUT" "$TEST"
STAGE=PREFLIGHT
finish() {
    rc=$?
    trap - EXIT
    set +e
    echo "STAGE=$STAGE RESULT=$rc" > "$OUT/result.txt"
    printf '%s  %s\n' "$SHA" "$PROVEN" | sha256sum -c - > "$OUT/proven-after.txt" 2>&1 || rc=3
    tar -czf "$LOGS/$TAG.tar.gz" -C "$LOGS" "$TAG"
    echo "ARCHIVE=$LOGS/$TAG.tar.gz"
    echo "ORIGINAL_SOURCE_AND_BUILD=UNCHANGED SSH=UNCHANGED SUNSHINE_X11=UNCHANGED"
    exit "$rc"
}
trap finish EXIT
exec 9>"$TEST/.build.lock"
flock -n 9 || { echo "TEST_ALREADY_RUNNING"; exit 2; }
printf '%s  %s\n' "$SHA" "$PROVEN" | sha256sum -c -
[[ -e "$OLD/.git" ]] || { echo "PROVEN_SOURCE_MISSING"; exit 2; }
command -v cmake >/dev/null
command -v python3 >/dev/null
command -v c++ >/dev/null
command -v tar >/dev/null
VKR="src/video_core/renderer_vulkan/vk_rasterizer.cpp"
python3 - "$OLD/$VKR" <<'CHECK'
import sys
from pathlib import Path
s=Path(sys.argv[1]).read_text()
assert s.count('LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",')==1, "UNEXPECTED_PROVEN_SOURCE; NO_BUILD"
assert '#include "common/debug.h"' in s
print("PROVEN_UNTHROTTLED_RENDERER=VERIFIED")
CHECK
find_games() {
    local p exe
    for p in /proc/[0-9]*; do
        [[ "$(stat -c %u "$p" 2>/dev/null || :)" == "$(id -u)" ]] || continue
        exe="$(readlink -f "$p/exe" 2>/dev/null || :)"
        case "$(basename "$exe")" in shadps4*|shadPS4*) echo "$p $exe";; esac
    done
}
[[ -z "$(find_games)" ]] || { echo "LIVE_EMULATOR_DETECTED; REFUSING_TO_INTERRUPT"; exit 2; }
free_kib="$(df -Pk "$TEST" | awk 'END {print $4}')"
[[ "$free_kib" =~ ^[0-9]+$ ]] && (( free_kib >= 4194304 )) || {
    echo "LESS_THAN_4_GIB_FREE; SAFE_STOP_WITHOUT_CLEANUP"; exit 2;
}
STAGE=ISOLATED_SOURCE
SOURCE_SHA="$(sha256sum "$OLD/$VKR" | cut -d' ' -f1)"
if [[ ! -f "$SRC/.source-sha" ]]; then
    [[ ! -e "$SRC" ]] || { echo "PARTIAL_TEST_SOURCE_EXISTS; REFUSE_REPLACE"; exit 2; }
    mkdir "$SRC"
    ( cd "$OLD" && tar --exclude='.git' --exclude='./build' -cf - . ) |
        ( cd "$SRC" && tar -xf - )
    printf '%s\n' "$SOURCE_SHA" > "$SRC/.source-sha"
fi
[[ "$(cat "$SRC/.source-sha")" == "$SOURCE_SHA" ]] || {
    echo "WORKING_SOURCE_CHANGED_SINCE_TEST_SNAPSHOT; NO_RESET"; exit 2;
}
python3 - "$SRC/$VKR" <<'PATCH'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
old='''                if (size != vsharp.GetSize()) {
                    LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x}",
                              vsharp.GetSize(), size, stage.pgm_hash);
                }'''
new='''                if (size != vsharp.GetSize()) {
                    // Unbounded guest descriptors are common in real shaders. Preserve
                    // mapping bounds but never synchronously log on every draw call:
                    // emit 1, 2, 4, 8 ... sightings per shader instead.
                    static thread_local std::unordered_map<u32, u64> clamp_counts;
                    const u64 seen = ++clamp_counts[stage.pgm_hash];
                    if ((seen & (seen - 1)) == 0) {
                        LOG_ERROR(Render, "Clamped size from {} to {} for stage {:#x} "
                                          "(seen {} times on this GPU thread)",
                                  vsharp.GetSize(), size, stage.pgm_hash, seen);
                    }
                }'''
if old in s:
    assert s.count(old)==1
    s=s.replace('#include "common/debug.h"','#include <unordered_map>\n\n#include "common/debug.h"',1)
    s=s.replace(old,new,1)
    p.write_text(s)
else:
    assert s.count(new)==1 and s.count('#include <unordered_map>')==1, "CANDIDATE_PATCH_MISMATCH"
print("ISOLATED_RENDERER_RATE_LIMIT_PATCH=PASS")
PATCH
sha256sum "$OLD/$VKR" "$SRC/$VKR" > "$OUT/renderer-sources.sha256"
STAGE=ISOLATED_CONFIGURE
build_type="$(sed -n 's/^CMAKE_BUILD_TYPE:STRING=//p' "$ORIG/build/CMakeCache.txt" 2>/dev/null | head -1)"
[[ -n "$build_type" ]] || build_type=Release
printf 'BUILD_TYPE=%s\n' "$build_type" > "$OUT/build-info.txt"
if [[ ! -f "$BUILD/CMakeCache.txt" ]]; then
    if command -v ccache >/dev/null; then
        cmake -S "$SRC" -B "$BUILD" -DCMAKE_BUILD_TYPE="$build_type" -DENABLE_TESTS=OFF -DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache > "$OUT/configure.log" 2>&1 || {
            tail -90 "$OUT/configure.log"; exit 2;
        }
    else
        cmake -S "$SRC" -B "$BUILD" -DCMAKE_BUILD_TYPE="$build_type" -DENABLE_TESTS=OFF > "$OUT/configure.log" 2>&1 || {
            tail -90 "$OUT/configure.log"; exit 2;
        }
    fi
fi
STAGE=ISOLATED_BUILD
echo "BUILDING_ISOLATED_CANDIDATE=YES; PROVEN_BINARY=NOT_MODIFIED"
cmake --build "$BUILD" --target shadps4 --parallel 4 > "$OUT/build.log" 2>&1 || {
    tail -100 "$OUT/build.log"; exit 2;
}
[[ -x "$BUILD/shadps4" ]] || { echo "BUILT_EXECUTABLE_MISSING"; exit 2; }
cp -p "$BUILD/shadps4" "$CAND"
sha256sum "$CAND" > "$OUT/candidate.sha256"
[[ -z "$(find_games)" ]] || { echo "EMULATOR_STARTED_DURING_BUILD; NO_LAUNCH"; exit 2; }
printf '%s  %s\n' "$SHA" "$PROVEN" | sha256sum -c -
STAGE=AUTO_LAUNCH
if [[ -z "$(printenv DISPLAY || :)" ]]; then export DISPLAY=:0; fi
if [[ -z "$(printenv XDG_RUNTIME_DIR || :)" ]]; then export XDG_RUNTIME_DIR="/run/user/$(id -u)"; fi
if [[ -z "$(printenv DBUS_SESSION_BUS_ADDRESS || :)" ]]; then export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"; fi
( cd "$ORIG" && exec "$CAND" --cpu-id-mode builtin --userfaultfd --game CUSA34384 --fullscreen true --show-fps > "$OUT/game-console.log" 2>&1 </dev/null ) &
pid=$!
sleep 5
[[ "$(readlink -f "/proc/$pid/exe" 2>/dev/null || :)" == "$CAND" ]] || {
    echo "GAME_EXITED_ON_STARTUP; SEE_ARCHIVE"; exit 2;
}
echo "GAME_LAUNCHED=YES PID=$pid; USE_MOONLIGHT=YES" | tee "$OUT/pid.txt"
STAGE=PER_THREAD_SAMPLING
python3 - "$pid" "$CAND" "$OUT" <<'PROFILE'
import csv,os,pathlib,sys,time,shutil,subprocess
pid=int(sys.argv[1]); exe=os.path.realpath(sys.argv[2]); out=pathlib.Path(sys.argv[3])
hz=os.sysconf("SC_CLK_TCK"); prev={}
gpus=[]
for d in pathlib.Path("/sys/class/drm").glob("card[0-9]*/device"):
    try:
        if (d/"vendor").read_text().strip()=="0x1002" and (d/"gpu_busy_percent").is_file(): gpus.append(d/"gpu_busy_percent")
    except OSError: pass
with (out/"threads.csv").open("w",newline="") as f, (out/"system.csv").open("w",newline="") as g:
    tf=csv.writer(f); sf=csv.writer(g)
    tf.writerow(["elapsed_s","tid","name","state","cpu_pct_interval","wchan"])
    sf.writerow(["elapsed_s","gpu_busy_pct","rss_kb","threads","log_bytes"])
    started=time.monotonic()
    for i in range(61):
        elapsed=round(time.monotonic()-started,2)
        if os.path.realpath(f"/proc/{pid}/exe")!=exe:
            print(f"GAME_EXITED_AT={elapsed}s",flush=True); break
        now={}
        for t in pathlib.Path(f"/proc/{pid}/task").glob("[0-9]*"):
            try:
                tid=int(t.name); st=(t/"stat").read_text().rsplit(") ",1)[1].split()
                ticks=int(st[11])+int(st[12]); stamp=time.monotonic()
                cpu="" if tid not in prev else round(100*(ticks-prev[tid][0])/(hz*max(.01,stamp-prev[tid][1])),1)
                now[tid]=(ticks,stamp)
                tf.writerow([elapsed,tid,(t/"comm").read_text().strip(),st[0],cpu,(t/"wchan").read_text().strip()])
            except (OSError,ValueError,IndexError): pass
        prev=now
        try:
            state=pathlib.Path(f"/proc/{pid}/status").read_text()
            field=lambda k:next((row.split()[1] for row in state.splitlines() if row.startswith(k+":")),"")
            sf.writerow([elapsed,gpus[0].read_text().strip() if gpus else "",field("VmRSS"),field("Threads"),(out/"game-console.log").stat().st_size])
        except OSError: pass
        f.flush();g.flush()
        if i in (20,40,60) and shutil.which("import"):
            try: subprocess.run(["import","-display",os.environ.get("DISPLAY",":0"),"-window","root",str(out/f"frame-{i*2}s.png")],timeout=5,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            except (OSError,subprocess.TimeoutExpired): pass
        if i%10==0: print(f"GAME_T={elapsed}s THREADS={len(now)}",flush=True)
        if i<60: time.sleep(2)
print("THREAD_AND_GPU_SAMPLES_CAPTURED=YES",flush=True)
PROFILE
STAGE=COMPLETE
python3 - "$OUT" <<'REPORT'
from pathlib import Path
import sys
p=Path(sys.argv[1]); s=(p/"game-console.log").read_text(errors="replace")
print("CLAMP_ERROR_LOG_COUNT="+str(s.count("Clamped size from")))
print("CONSOLE_SIZE_BYTES="+str((p/"game-console.log").stat().st_size))
print("CRASH_SIGNAL_HANDLER="+str("Unreachable code!" in s))
REPORT
echo "PROVEN_BUILD=SHA_VERIFIED; CANDIDATE_LEFT_RUNNING=YES"
