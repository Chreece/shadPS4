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
    STAGING="$(mktemp -d "$TEST/.source-stage-XXXXXXXX")"
    mkdir "$STAGING"
    ( cd "$OLD" && tar --exclude='.git' --exclude='./build' -cf - . ) |
        ( cd "$STAGING" && tar -xf - )
    printf '%s\n' "$SOURCE_SHA" > "$STAGING/.source-sha"
    mv "$STAGING" "$SRC"
fi
[[ "$(cat "$SRC/.source-sha")" == "$SOURCE_SHA" ]] || {
    echo "WORKING_SOURCE_CHANGED_SINCE_TEST_SNAPSHOT; NO_RESET"; exit 2;
}
STAGE=RESTORE_ISOLATED_GIT_METADATA
# The previous snapshot excluded .git from the root AND every submodule.
# CMake needs the exact ffmpeg-core submodule Git SHA for matching prebuilts.
# Create relative-path Git pointers in the isolated snapshot; the OpenAL
# CMake version target directly concatenates the .git pointer with /index.
# No checkout, reset, fetch, or metadata writes to the proven source.
python3 - "$OLD" "$SRC" "$OUT" <<'RESTORE_GIT_METADATA'
from pathlib import Path
import os
import subprocess
import sys

def git(path, *args):
    return subprocess.check_output(
        ["git", "-C", str(path), *args],
        stderr=subprocess.PIPE, text=True
    ).strip()

old, new, report = (Path(x).resolve(strict=True) for x in sys.argv[1:4])
assert old != new, "ORIGINAL_AND_ISOLATED_SOURCE_SAME_PATH"
assert (old / ".git").exists() or (old / ".git").is_symlink(), "ORIGINAL_GIT_MISSING"
markers = []
for root, dirs, files in os.walk(old, followlinks=False):
    if ".git" in dirs:
        dirs.remove(".git")
        markers.append(Path(root))
    elif ".git" in files:
        markers.append(Path(root))
assert old in markers, "ORIGINAL_GIT_MARKER_MISSING"
evidence = []
for idx, original_dir in enumerate(markers):
    relative = original_dir.relative_to(old)
    copied_dir = new / relative
    assert copied_dir.exists(), f"ISOLATED_DIRECTORY_MISSING:{relative}"
    original_head = git(original_dir, "rev-parse", "HEAD")
    # Some historical submodules are links to an existing external source.
    # They are read-only here; never write through a symlink outside new.
    if not copied_dir.resolve().is_relative_to(new):
        assert git(copied_dir, "rev-parse", "HEAD") == original_head, (
            f"EXTERNAL_SUBMODULE_GIT_MISMATCH:{relative}"
        )
        evidence.append(f"EXTERNAL_GIT_ALREADY_VALID {relative} {original_head}")
        continue
    gitdir = Path(git(original_dir, "rev-parse", "--absolute-git-dir")).resolve(strict=True)
    pointer = copied_dir / ".git"
    if pointer.is_dir() or pointer.is_symlink():
        assert git(copied_dir, "rev-parse", "HEAD") == original_head, (
            f"EXISTING_ISOLATED_GIT_MISMATCH:{relative}"
        )
    else:
        expected = f"gitdir: {os.path.relpath(gitdir, copied_dir)}\n"
        if pointer.is_file() and pointer.read_text() != expected:
            (report / f"replaced-test-git-pointer-{idx}.txt").write_bytes(
                pointer.read_bytes()
            )
        if not pointer.is_file() or pointer.read_text() != expected:
            temp = pointer.with_name(f".git.repair-{os.getpid()}")
            temp.write_text(expected)
            os.replace(temp, pointer)
    assert git(copied_dir, "rev-parse", "HEAD") == original_head, (
        f"ISOLATED_GIT_HEAD_MISMATCH:{relative}"
    )
    # Real Git submodules often set core.worktree to their original path.
    # This is safe for the read-only SHA queries CMake performs.
    actual_top = Path(git(copied_dir, "rev-parse", "--show-toplevel")).resolve()
    assert actual_top in (copied_dir.resolve(), original_dir.resolve()), (
        f"ISOLATED_GIT_TOPLEVEL_UNEXPECTED:{relative}"
    )
    evidence.append(f"GIT_SHA_MATCH {relative} {original_head}")
# Explicitly validate the dependency that caused the last failure.
ff_orig = old / "externals/ffmpeg-core"
ff_copy = new / "externals/ffmpeg-core"
assert ff_orig.is_dir() and ff_copy.is_dir(), "FFMPEG_SUBMODULE_MISSING"
assert git(ff_orig, "rev-parse", "--show-toplevel") == str(ff_orig.resolve()), (
    "PROVEN_FFMPEG_METADATA_INVALID"
)
ffmpeg_top = Path(git(ff_copy, "rev-parse", "--show-toplevel")).resolve()
assert ffmpeg_top in (ff_copy.resolve(), ff_orig.resolve()), (
    "ISOLATED_FFMPEG_METADATA_INVALID"
)
assert git(ff_orig, "rev-parse", "HEAD") == git(ff_copy, "rev-parse", "HEAD"), (
    "FFMPEG_PREBUILT_SHA_MISMATCH"
)
# OpenAL Soft uses CMake to interpret .git itself, not just git rev-parse.
# Its CMakeLists.txt resolves: OpenAL_SOURCE_DIR + parsed relative gitdir + /index.
openal = new / "externals/openal-soft"
openal_pointer = openal / ".git"
assert openal_pointer.is_file(), "OPENAL_GITDIR_POINTER_MISSING"
pointer_text = openal_pointer.read_text().strip()
assert pointer_text.startswith("gitdir: "), "OPENAL_GITDIR_POINTER_BAD_FORMAT"
openal_relative = pointer_text[len("gitdir: "):]
assert not os.path.isabs(openal_relative), "OPENAL_GITDIR_MUST_BE_RELATIVE"
openal_index = (openal / openal_relative / "index").resolve()
expected_openal_dir = Path(git(old / "externals/openal-soft", "rev-parse", "--absolute-git-dir")).resolve()
assert openal_index == expected_openal_dir / "index", "OPENAL_CMAKE_DEPENDENCY_WRONG_DIRECTORY"
assert openal_index.is_file(), f"OPENAL_CMAKE_INDEX_MISSING:{openal_index}"
evidence.append(f"OPENAL_CMAKE_VERSION_DEPENDENCY=PASS {openal_index}")
evidence.append(f"FFMPEG_PREBUILT_GIT_SHA=VERIFIED {git(ff_copy, 'rev-parse', 'HEAD')}")
(report / "git-metadata-check.txt").write_text("\n".join(evidence) + "\n")
for line in evidence:
    print(line)
print("ISOLATED_GIT_METADATA=VERIFIED")
RESTORE_GIT_METADATA
python3 - "$OLD/$VKR" "$SRC/$VKR" "$OUT" <<'PATCH'
from pathlib import Path
import re
import sys

source, target, report = map(Path, sys.argv[1:4])
assert source.is_file() and not source.is_symlink(), "ORIGINAL_RENDERER_SOURCE_INVALID"
assert target.is_file() and not target.is_symlink(), "ISOLATED_RENDERER_SOURCE_INVALID"
original_bytes = source.read_bytes()
original = original_bytes.decode("utf-8")
assert original.count('#include "common/debug.h"') == 1, "ORIGINAL_INCLUDE_ANCHOR_MISMATCH"
assert "#include <unordered_map>" not in original, "ORIGINAL_RENDERER_ALREADY_RATE_LIMITED"

# Identify the exact diagnostic call, allowing differences in whitespace and
# surrounding layout. Do NOT change buffer sizes, access flags or cache logic.
pattern = re.compile(
    r'(?m)^(?P<indent>[ \t]*)LOG_ERROR\(Render,\s*'
    r'"Clamped size from \{\} to \{\} for stage \{:#x\}",\s*'
    r'vsharp\.GetSize\(\),\s*size,\s*stage\.pgm_hash\);'
)
matches = list(pattern.finditer(original))
assert len(matches) == 1, (
    f"ORIGINAL_CLAMP_DIAGNOSTIC_MATCHES={len(matches)}; NO_BUILD"
)
indent = matches[0].group("indent")
modified = pattern.sub(
    lambda match: (
        f"{indent}// Preserve guest mapping bounds; avoid logging on every draw.\n"
        f"{indent}static thread_local std::unordered_map<u64, u64> clamp_counts;\n"
        f"{indent}const u64 seen = ++clamp_counts[stage.pgm_hash];\n"
        f"{indent}if ((seen & (seen - 1)) == 0) {{\n"
        f'{indent}    LOG_ERROR(Render, "Clamped size from {{}} to {{}} for stage {{:#x}} "\n'
        f'{indent}                      "(seen {{}} times on this GPU thread)",\n'
        f"{indent}              vsharp.GetSize(), size, stage.pgm_hash, seen);\n"
        f"{indent}}}"
    ),
    original,
    count=1,
)
modified = modified.replace(
    '#include "common/debug.h"',
    '#include <unordered_map>\n\n#include "common/debug.h"',
    1,
)
assert modified != original
assert modified.count("std::unordered_map<u64, u64> clamp_counts;") == 1
assert modified.count('const u64 size = memory->ClampRangeSize(') == original.count(
    'const u64 size = memory->ClampRangeSize('
)
assert modified.count('buffer_cache.ObtainBuffer(') == original.count(
    'buffer_cache.ObtainBuffer('
)
assert modified.count('texture_cache.InvalidateMemoryFromGPU(') == original.count(
    'texture_cache.InvalidateMemoryFromGPU('
)

# An interrupted previous test may have left an unrelated renderer version
# in the test-owned snapshot. Preserve it as evidence; regenerate only this
# test-owned source file from the verified original.
existing = target.read_bytes()
candidate = modified.encode("utf-8")
if existing not in (original_bytes, candidate):
    backup = report / "previous-isolated-renderer.cpp"
    backup.write_bytes(existing)
    print("STALE_TEST_RENDERER=BACKED_UP_AND_REGENERATED_FROM_PROVEN_SOURCE")
if existing != candidate:
    target.write_bytes(candidate)
assert target.read_bytes() == candidate
assert source.read_bytes() == original_bytes, "PROVEN_RENDERER_CHANGED"
print("ISOLATED_RENDERER_RATE_LIMIT_PATCH=PASS; IDP=ORIGINAL_SOURCE_ONLY")
PATCH
sha256sum "$OLD/$VKR" "$SRC/$VKR" > "$OUT/renderer-sources.sha256"
STAGE=ISOLATED_CONFIGURE
build_type=Release
if [[ -f "$ORIG/build/CMakeCache.txt" ]]; then
    cached_type="$(sed -n 's/^CMAKE_BUILD_TYPE:STRING=//p' "$ORIG/build/CMakeCache.txt" | head -1)"
    [[ -z "$cached_type" ]] || build_type="$cached_type"
fi
printf 'BUILD_TYPE=%s\n' "$build_type" > "$OUT/build-info.txt"
# Always refresh CMake after repairing Git metadata from the prior failed configure.
if :; then
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
            print(f"GAME_EXITED_AT={elapsed}s",flush=True); raise SystemExit(2)
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
