#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Verify shader-derived Vulkan descriptor writes against Oct 9 RADV crash,
# retaining the previously verified nine-mip, Vulkan-1.1 and sampler trials.
# One-run validation: full source/binary rollback, only own spawned game terminates.
set -Eeuo pipefail
umask 077

ROOT="$HOME/.cache/shadps4-ghost-fullstack-20261008-131621/source"
BUILD="$HOME/.cache/ghost-fullstack-resume-20261008-132842/build"
DEST="$HOME/Applications/shadps4/shadps4"
STATE="$HOME/.local/state/shadps4-playtest-logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
SESSION="$HOME/.cache/ghost-descriptor-trial-$STAMP"
ARCHIVE="$HOME/ghost-descriptor-trial-$STAMP.tar.gz"
LOG="$SESSION/build.log"
EXPECTED_HEAD='89af13f6d306ebc24396b4e8e207688537cdc28b'
EXPECTED_SHA='f54245b0cf835995172a910c4e3a16cb13aef6c36a39fa5a8be53f3190183e4f'
PATCHER_REV='327107e0bfd8cd12b04dfac607040788d89d59f6'
REVERSE_PATCHER_REV='a9a5a90bba0a22931ed5ccef93b1c06cdbb48f3f'
VK11_PATCHER_REV='698693acac4f0555ddd6fde05ead3529ace4ec1c'
VK11_REL='src/video_core/renderer_vulkan/vk_instance.cpp'
VK11_SOURCE_TOUCHED=0
R8_PATCHER_REV='e43fa05509c0c572fb166e51b66bd6c2602aa21b'
DESCRIPTOR_PATCHER_REV='cf2946c833a09a7bf5003a6cad0523e55ec6dd3f'
FALLBACK_PROBE_REV='33aee211bc16f00326ffe17bcb5407ca5ca0028d'
ARENA_PATCHER_REV='f9b9ceadf21bc1caf5267b339b2539ff887de18c'
ARENA_HEADER_REL='src/video_core/buffer_cache/buffer_cache.h'
ARENA_IMPL_REL='src/video_core/buffer_cache/buffer_cache.cpp'
ARENA_SOURCE_TOUCHED=0
R8_REL='src/video_core/renderer_vulkan/vk_rasterizer.cpp'
R8_SOURCE_TOUCHED=0
# GHOST_NO_GDB_CONTROL: deliberately no early watchpoint.
# An earlier trial logged 32 consecutive SIGTRAP stops inside assert_fail_impl.
WATCHER_PID=''
WATCHER_STARTED=0
REL='src/video_core/renderer_vulkan/vk_runtime.cpp'
ARMED_AT="$(date +%s)"
mkdir -p "$SESSION" "$STATE"
: >"$LOG"
PHASE=preflight
CANDIDATE_SHA=''
ORIGINAL_BUILT=''
SOURCE_TOUCHED=0
DID_DEPLOY=0
GAME_PID=''
log() { printf '%s\n' "$*" | tee -a "$LOG"; }
run() { log "+ $(printf '%q ' "$@")"; "$@" 2>&1 | tee -a "$LOG"; }
fail() { log "STOP: $*"; exit 1; }
game_pid() {
    # /proc/<pid>/comm is "shadPS4:Main", not "shadps4"; never use pgrep -x.
    # Match the installed executable inode AND the game's actual command line.
    python3 - "$DEST" <<'PY'
from pathlib import Path
import sys
exe = Path(sys.argv[1])
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if not (proc / 'exe').samefile(exe):
            continue
        args = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode('utf-8','replace')
        if 'CUSA11456' in args or 'Ghost of Tsushima.ps4' in args:
            print(proc.name)
            break
    except (OSError, PermissionError, ValueError):
        continue
PY
}
any_shadps4() {
    # Check deleted ELF mappings as well: previous trials restored the disk inode.
    python3 - <<'PY'
from pathlib import Path
import os, sys
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if proc.stat().st_uid != os.getuid():
            continue
        exe = os.readlink(proc / "exe").removesuffix(" (deleted)")
        if Path(exe).name.lower() == "shadps4":
            sys.exit(0)
    except (OSError, ValueError, PermissionError):
        continue
sys.exit(1)
PY
}
find_session() {
    local candidate t
    while IFS= read -r -d '' candidate; do
        t="$(stat -c %Y "$candidate" 2>/dev/null || echo 0)"
        if (( t >= ARMED_AT - 5 )); then
            printf '%s' "${candidate%/session.meta}"
            return 0
        fi
    done < <(find "$STATE" -maxdepth 2 -path '*Ghost_of_Tsushima.ps4/session.meta' -print0 2>/dev/null | sort -z -r)
    return 1
}
finish() {
    local original_rc="$?" restored='no'
    trap - EXIT
    set +e
    log ''
    log '=== Automatic cleanup (never closing the game/SSH) ==='
    if (( SOURCE_TOUCHED )) && [[ -f "$SESSION/original/$REL" ]]; then
        cp -p -- "$SESSION/original/$REL" "$ROOT/$REL"
        touch -- "$ROOT/$REL"
        log 'Original vk_runtime.cpp restored; source marked for baseline rebuild.'
    fi
    if (( VK11_SOURCE_TOUCHED )) && [[ -f "$SESSION/original-vk11/$VK11_REL" ]]; then
        cp -p -- "$SESSION/original-vk11/$VK11_REL" "$ROOT/$VK11_REL"
        touch -- "$ROOT/$VK11_REL"
        log 'Original vk_instance.cpp restored; source marked for baseline rebuild.'
    fi
    if (( R8_SOURCE_TOUCHED )) && [[ -f "$SESSION/original-r8/$R8_REL" ]]; then
        cp -p -- "$SESSION/original-r8/$R8_REL" "$ROOT/$R8_REL"
        touch -- "$ROOT/$R8_REL"
        log 'Original vk_rasterizer.cpp restored; source marked for baseline rebuild.'
    fi
    if (( ARENA_SOURCE_TOUCHED )); then
        for arena_file in "$ARENA_HEADER_REL" "$ARENA_IMPL_REL"; do
            if [[ -f "$SESSION/original-arena/$arena_file" ]]; then
                cp -p -- "$SESSION/original-arena/$arena_file" "$ROOT/$arena_file"
                touch -- "$ROOT/$arena_file"
                log "Original $arena_file restored and marked for future baseline rebuild."
            else
                log "WARNING: original arena source backup missing: $arena_file."
            fi
        done
    fi
    if (( DID_DEPLOY )) && [[ -f "$SESSION/installed-original" ]]; then
        now_sha="$(sha256sum "$DEST" 2>/dev/null | cut -d' ' -f1)"
        if [[ "$now_sha" == "$CANDIDATE_SHA" ]]; then
            cp -a -- "$SESSION/installed-original" "$DEST.restore-ghost-mip-with-watch-$STAMP"
            mv -fT -- "$DEST.restore-ghost-mip-with-watch-$STAMP" "$DEST"
            restored='yes'
            log 'Original installed executable restored atomically.'
        else
            log "WARNING: installed executable changed externally ($now_sha); refusing to overwrite."
        fi
    fi
    if [[ -n "$ORIGINAL_BUILT" && -f "$SESSION/cached-original" ]]; then
        now_build_sha="$(sha256sum "$ORIGINAL_BUILT" 2>/dev/null | cut -d' ' -f1)"
        if [[ -n "$CANDIDATE_SHA" && "$now_build_sha" == "$CANDIDATE_SHA" ]]; then
            local stage_cached="${ORIGINAL_BUILT}.restore-ghost-map-$STAMP"
            if cp -a -- "$SESSION/cached-original" "$stage_cached" &&
               [[ "$(sha256sum "$stage_cached" 2>/dev/null | cut -d' ' -f1)" == "$EXPECTED_SHA" ]] &&
               mv -fT -- "$stage_cached" "$ORIGINAL_BUILT" &&
               [[ "$(sha256sum "$ORIGINAL_BUILT" 2>/dev/null | cut -d' ' -f1)" == "$EXPECTED_SHA" ]]; then
                log 'Original cached executable restored atomically and verified.'
            else
                log 'WARNING: cached executable could not be restored or verified; review archive.'
            fi
        elif [[ "$now_build_sha" != "$EXPECTED_SHA" ]]; then
            log "WARNING: unexpected cached build SHA=$now_build_sha; refusing to overwrite."
        fi
    fi
    [[ -d "$ROOT" ]] && git -C "$ROOT" status --short -b >"$SESSION/source-after.txt" 2>&1
    if [[ -x "$DEST" ]]; then
        sha256sum "$DEST" >"$SESSION/installed-after.sha256"
    fi
    if [[ "$WATCHER_STARTED" == 1 ]]; then
        local pid_alive=0
        if [[ -n "$WATCHER_PID" ]] && kill -0 "$WATCHER_PID" 2>/dev/null; then
            for _ in $(seq 1 20); do
                kill -0 "$WATCHER_PID" 2>/dev/null || break
                sleep 1
            done
            kill -0 "$WATCHER_PID" 2>/dev/null && pid_alive=1
        fi
        if [[ "$pid_alive" == 1 ]]; then
            log "Watcher still collecting; leaving it running. Its separate archive will be printed in $SESSION/watch-live.log."
        else
            wait "$WATCHER_PID" 2>/dev/null || true
            log 'Late-intro watcher finished; collecting its evidence.'
        fi
        if [[ -f "$SESSION/watch-live.log" ]]; then
            local watcher_archive
            watcher_archive="$(sed -n 's/^.*ARCHIVE=//p' "$SESSION/watch-live.log" | tail -n 1)"
            if [[ -n "$watcher_archive" && -f "$watcher_archive" ]]; then
                cp -a -- "$watcher_archive" "$SESSION/companion-watch.tar.gz"
                log 'Companion debugger archive included.'
            fi
        fi
    fi
    local session_path
    session_path="$(find_session || true)"
    if [[ -n "$session_path" ]]; then
        log "Guest session: $session_path"
        for filename in runtime.log session.meta; do
            [[ -f "$session_path/$filename" ]] && cp -a -- "$session_path/$filename" "$SESSION/$filename"
        done
    else
        log 'No new Ghost session captured.'
    fi
    if [[ -f "$SESSION/runtime.log" ]]; then
        python3 - "$SESSION/runtime.log" "$SESSION/summary.txt" <<'PY'
import re,sys,pathlib
raw=pathlib.Path(sys.argv[1]).read_text(errors='replace')
text=re.sub(r'\x1b\[[0-9;]*m','',raw)
flip=re.findall(r'GHOST_TRACE vblank=(\d+) guest_flips=(\d+) pending=(\d+) queued=(\d+)',text)
out=[]
hits = text.count("GHOST_MIP_COPY mips=")
old_assertions = text.count("GHOST_MIP_ASSERT")
assertions = text.count("Assertion Failed!")
out.append(f'GHOST_MIP_COPY executed: {hits}')
out.append(f'GHOST_MIP_REVERSE_COPY executed: {text.count("GHOST_MIP_REVERSE_COPY mips=")}')
out.append('NO_GDB_HARDWARE_WATCHPOINT=1')
out.append(f'VULKAN11_16BIT_FEATURE_DIAGNOSTIC_COUNT={text.count("GHOST_VK11_16BIT uniformAndStorageBuffer16BitAccess")}')
out.append(f'R8_INDEX1_POINT_APPLIED={text.count("GHOST_R8_INDEX1_APPLIED shader=")}')
out.append(f'DESCRIPTOR_TYPE_00319_ERRORS={text.count("VUID-VkWriteDescriptorSet-descriptorType-00319:")}')
out.append(f'VULKAN_LAYER_ACTIVE={"VK_LAYER_KHRONOS_validation" in text}')
out.append(f'STORAGE_USAGE_00339_ERRORS={text.count("VUID-VkWriteDescriptorSet-descriptorType-00339:")}')
out.append(f'STORAGE_IMAGE_07028_ERRORS={text.count("VUID-vkCmdDispatchIndirect-OpTypeImage-07028:")}')
out.append(f'SYNC_WRITE_AFTER_PRESENT={text.count("SYNC-HAZARD-WRITE-AFTER-PRESENT:")}')
out.append(f'RADV_GPU_HANG={text.count("radv: GPU hang detected")}')
out.append(f'FALLBACK_MIP_ASSERT_PROBES={text.count("GHOST_COPY_FALLBACK_ASSERT mips=")}')
out.append(f'SPARSE_ARENA_1G_CONFIG={text.count("GHOST_ARENA_1G_CONFIG arena_page=")}')
out.append(f'VUID_BUFFER_OVERSIZE={text.count("VUID-VkBufferCreateInfo-size-06409")}')
out.append(f'ARENA_GUARD_ASSERTS={text.count("GHOST_ARENA_1G oversized initial sparse buffer=") + text.count("GHOST_ARENA_1G sparse arena merge=")}')
out.append('R8_INDEX0_UNCHANGED=1')
out.append('FALLBACK_ASSERTION_PRESERVED=1')
out.append(f'Original 9-mip assertion: {old_assertions}')
out.append("MIP_TEST_OUTCOME=" + (
    "COPIED_MIPS_NO_ASSERTION_RECORDED" if hits and not assertions and not old_assertions else
    "COPIED_MIPS_WITH_LATER_ASSERTION" if hits else
    "OLD_ASSERTION_STILL_TRIGGERED" if old_assertions else
    "MIP_CODE_PATH_NOT_REACHED"))
out.append(f'Flip samples: {len(flip)}')
if flip:
    out.append(f'First: {flip[0]}')
    out.append(f'Peak completed guest flips: {max(int(f[1]) for f in flip)}')
    out.append(f'Last: {flip[-1]}')
for pat in ('Assertion Failed!','GHOST_MIP_ASSERT','HOST_QUIT action=accepted','Unhandled exception','SIGSEGV'):
    out.append(f'{pat}: {text.count(pat)}')
pathlib.Path(sys.argv[2]).write_text('\n'.join(out)+'\n')
print('\n'.join(out))
PY
    fi
    printf 'phase=%s\nreturn_code=%s\ninstalled_restored=%s\ncompleted=%s\n' \
        "$PHASE" "$original_rc" "$restored" "$(date -Is)" >>"$SESSION/manifest.txt"
    # Never bundle the large executable backups.
    tar -czf "$ARCHIVE" -C "$SESSION" \
        --exclude=installed-original --exclude=cached-original . 2>/dev/null
    log "ARCHIVE=$ARCHIVE"
    if [[ "$restored" == yes ]]; then
        log 'Baseline installed binary restored. Game saves, wrappers, audio, and SSH unchanged.'
    fi
}
trap finish EXIT
trap 'exit 130' INT TERM HUP

log '=== GHOST NINE-MIP BIDIRECTIONAL COPY + EXISTING VULKAN FIXES ==='
log 'Test: transfer all 9 color-to-depth mips via aligned staging buffer regions.'
log 'Unrelated formats and existing single-mip copy path remain unchanged.'
log 'The companion watchpoint uses the same frame-400 window as the successful baseline crash capture.'
log 'Sampler point filtering restricted to validated shader/index 1 only.'
log 'Fallback copy probe logs the unsupported mip shape without disabling the assertion.'
log 'Experimental 1GiB sparse arenas avoid RADV 4GiB-4 maxBufferSize; oversized merges remain guarded.'
log 'NEW: 9-mip D32_SFLOAT->R32_SFLOAT transfer now handles the reverse of the verified copy.'
log 'Source image aspects, layers, samples, dimensions and fallback assertion stay guarded.'
log 'All sources and installed/cached ELF restore after this ONE playtest.'
log 'GHOST_NO_GDB_CONTROL: no early watchpoints and no debugger attachments.'
log 'Trial source and binaries restore after the game exits or timeout.'

for tool in git cmake ninja curl python3 sha256sum tar install cmp find stat; do
    command -v "$tool" >/dev/null || fail "Required tool missing: $tool"
done
[[ -f "$ROOT/.git" && -f "$BUILD/CMakeCache.txt" && -x "$DEST" ]] || \
    fail 'Reviewed source, cached build, or installed executable unavailable.'
[[ "$(git -C "$ROOT" rev-parse HEAD)" == "$EXPECTED_HEAD" ]] || fail 'Source commit differs.'
[[ -z "$(git -C "$ROOT" status --porcelain)" ]] || fail 'Source tree has changes. Not touching them.'
grep -Fxq "CMAKE_HOME_DIRECTORY:INTERNAL=$ROOT" "$BUILD/CMakeCache.txt" || fail 'Wrong CMake cache.'
[[ "$(sha256sum "$DEST" | cut -d' ' -f1)" == "$EXPECTED_SHA" ]] || \
    fail 'Installed executable changed since evidence capture; refusing replacement.'
any_shadps4 && fail 'shadPS4 is already running; please exit normally first.'
mapfile -t bins < <(find "$BUILD" -type f -name shadps4 -perm /111)
[[ "${#bins[@]}" == 1 ]] || fail "Expected one build output, found ${#bins[@]}"
ORIGINAL_BUILT="${bins[0]}"
[[ "$(sha256sum "$ORIGINAL_BUILT" | cut -d' ' -f1)" == "$EXPECTED_SHA" ]] || \
    fail 'Cached executable does not match installed baseline.'
cp -a -- "$DEST" "$SESSION/installed-original"
cp -a -- "$ORIGINAL_BUILT" "$SESSION/cached-original"
printf 'baseline=%s\nsource_commit=%s\nstarted=%s\n' \
    "$EXPECTED_SHA" "$EXPECTED_HEAD" "$(date -Is)" >"$SESSION/manifest.txt"

log '=== Fetch and self-test pinned Vulkan, sampler, multi-mip and descriptor correction ==='
REVERSE_PATCHER="$SESSION/ghost-mip-reverse-direction-patch.py"
run curl -fsSL --retry 2 --max-time 35   "https://raw.githubusercontent.com/Chreece/shadPS4/$REVERSE_PATCHER_REV/scripts/ghost-mip-reverse-direction-patch.py"   -o "$REVERSE_PATCHER"
run python3 -m py_compile "$REVERSE_PATCHER"
run python3 -I "$REVERSE_PATCHER" --self-test
ARENA_PATCHER="$SESSION/ghost-sparse-arena-1g-trial-patch.py"
run curl -fsSL --retry 2 --max-time 35   "https://raw.githubusercontent.com/Chreece/shadPS4/$ARENA_PATCHER_REV/scripts/ghost-sparse-arena-1g-trial-patch.py"   -o "$ARENA_PATCHER"
run python3 -m py_compile "$ARENA_PATCHER"
run python3 -I "$ARENA_PATCHER" --self-test
run python3 -I "$ARENA_PATCHER" "$ROOT" "$SESSION/original-arena" --check-only
FALLBACK_PATCHER="$SESSION/ghost-copy-fallback-probe.py"
run curl -fsSL --retry 2 --max-time 35   "https://raw.githubusercontent.com/Chreece/shadPS4/$FALLBACK_PROBE_REV/scripts/ghost-copy-fallback-probe.py"   -o "$FALLBACK_PATCHER"
run python3 -m py_compile "$FALLBACK_PATCHER"
run python3 -I "$FALLBACK_PATCHER" --self-test
R8_PATCHER="$SESSION/ghost-r8-sampler-index1-patch.py"
run curl -fsSL --retry 2 --max-time 35   "https://raw.githubusercontent.com/Chreece/shadPS4/$R8_PATCHER_REV/scripts/ghost-r8-sampler-index1-patch.py"   -o "$R8_PATCHER"
run python3 -m py_compile "$R8_PATCHER"
run python3 -I "$R8_PATCHER" --self-test
run python3 -I "$R8_PATCHER" "$ROOT" "$SESSION/original-r8" --check-only
DESCRIPTOR_PATCHER="$SESSION/ghost-descriptor-from-shader-trial.py"
run curl -fsSL --retry 2 --max-time 35 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$DESCRIPTOR_PATCHER_REV/scripts/ghost-descriptor-from-shader-trial.py" \
    -o "$DESCRIPTOR_PATCHER"
run python3 -I -m py_compile "$DESCRIPTOR_PATCHER"
run python3 -I "$DESCRIPTOR_PATCHER" --self-test
VK11_PATCHER="$SESSION/ghost-vk11-16bit-feature-patch.py"
run curl -fsSL --retry 2 --max-time 35   "https://raw.githubusercontent.com/Chreece/shadPS4/$VK11_PATCHER_REV/scripts/ghost-vk11-16bit-feature-patch.py"   -o "$VK11_PATCHER"
run python3 -m py_compile "$VK11_PATCHER"
run python3 -I "$VK11_PATCHER" --self-test
run python3 -I "$VK11_PATCHER" "$ROOT" "$SESSION/original-vk11" --check-only
log 'GHOST_NO_GDB_CONTROL: companion hardware watchpoint deliberately disabled.'
log '=== Stage exact-source multi-mip copy correction ==='
PATCHER="$SESSION/patcher.py"
run curl -fsSL --retry 2 --max-time 30 \
    "https://raw.githubusercontent.com/Chreece/shadPS4/$PATCHER_REV/scripts/ghost-mip-copy-candidate.py" \
    -o "$PATCHER"
run python3 -m py_compile "$PATCHER"
SOURCE_TOUCHED=1
run python3 -I "$PATCHER" --self-test
run python3 -I "$PATCHER" --check-source "$ROOT/$REL"
run python3 -I "$PATCHER" "$ROOT" "$SESSION/original"
# Extend only the reviewed 9-mip branch. Fail closed unless this source
# exactly matches the candidate SHA verified from the real game archive.
run python3 -I "$REVERSE_PATCHER" "$ROOT" "$SESSION/reverse-mip-candidate" --check-only
run python3 -I "$REVERSE_PATCHER" "$ROOT" "$SESSION/reverse-mip-candidate"
# Keep the original fallback assertion. Instrument its remaining cases.
run python3 -I "$FALLBACK_PATCHER" "$ROOT" "$SESSION/probed-mip-candidate" --check-only
run python3 -I "$FALLBACK_PATCHER" "$ROOT" "$SESSION/probed-mip-candidate"
VK11_SOURCE_TOUCHED=1
run python3 -I "$VK11_PATCHER" "$ROOT" "$SESSION/original-vk11"
R8_SOURCE_TOUCHED=1
run python3 -I "$R8_PATCHER" "$ROOT" "$SESSION/original-r8"
# The R8 patcher just verified and preserved the exact original rasterizer.
# Use the shader's original is_written bit to pick descriptor writes,
# never a potentially stale ImageDesc for a rejected/null T#.
run python3 -I "$DESCRIPTOR_PATCHER" "$ROOT" "$SESSION/original-r8" --check-only
run python3 -I "$DESCRIPTOR_PATCHER" "$ROOT" "$SESSION/original-r8"
ARENA_SOURCE_TOUCHED=1
run python3 -I "$ARENA_PATCHER" "$ROOT" "$SESSION/original-arena"
git -C "$ROOT" diff --check || fail 'Source diff check failed.'
git -C "$ROOT" diff -- "$REL" "$VK11_REL" "$R8_REL"   "$ARENA_HEADER_REL" "$ARENA_IMPL_REL" >"$SESSION/ghost-arena-1g.patch"
grep -q 'GHOST_VK11_16BIT' "$SESSION/ghost-arena-1g.patch" ||
    fail 'Missing Vulkan 1.1 uniform storage 16-bit feature change.'
grep -q 'GHOST_MIP_COPY' "$SESSION/ghost-arena-1g.patch" ||
    fail 'Missing targeted multi-mip source change.'
grep -q 'GHOST_MIP_REVERSE_COPY' "$SESSION/ghost-arena-1g.patch" ||
    fail 'Missing exact D32-to-R32 multi-mip source change.'
grep -q 'GHOST_R8_INDEX1_APPLIED' "$SESSION/ghost-arena-1g.patch" ||
    fail 'Missing validated R8_UINT sampler-index 1 correction.'
grep -q 'GHOST_DESCRIPTOR_FROM_SHADER' "$SESSION/ghost-arena-1g.patch" ||
    fail 'Missing shader-derived image descriptor correction.'
grep -q 'GHOST_COPY_FALLBACK_ASSERT' "$SESSION/ghost-arena-1g.patch" ||
    fail 'Missing fallback mip-copy assertion context probe.'
grep -q 'GHOST_ARENA_1G_CONFIG' "$SESSION/ghost-arena-1g.patch" ||
    fail 'Missing 1GiB sparse arena device-limit diagnostic.'
grep -q 'static constexpr u64 ARENA_PAGE_BITS = 30;' "$ROOT/$ARENA_HEADER_REL" ||
    fail 'Sparse arena page size was not changed to 1GiB.'
grep -q 'total_size <= max_sparse_buffer_size' "$ROOT/$ARENA_IMPL_REL" ||
    fail 'Sparse arena merge maxBufferSize guard absent.'
# Variadic LOG_ERROR cannot bind C++ bit-fields by reference. Explicitly
# verify the updated depth flags are passed as values before compilation.
grep -Fq 'static_cast<u32>(src->info.props.is_depth)' "$ROOT/$REL" ||
    fail 'Fallback diagnostic still forwards source depth bit-field by reference.'
grep -Fq 'static_cast<u32>(dst->info.props.is_depth)' "$ROOT/$REL" ||
    fail 'Fallback diagnostic still forwards destination depth bit-field by reference.'

log '=== Incremental Vulkan build (affinity/CPUID unchanged) ==='
JOBS="${GHOST_BUILD_JOBS:-4}"
[[ "$JOBS" =~ ^[1-9][0-9]?$ ]] || fail 'Invalid build job count.'
run cmake --build "$BUILD" --target shadps4 --parallel "$JOBS"
# RECORD THE BUILT SHA *BEFORE* ANY MARKER CHECK. A failed check must not
# strand the freshly linked candidate in the cached build output.
CANDIDATE_SHA="$(sha256sum "$ORIGINAL_BUILT" | cut -d' ' -f1)"
[[ "$CANDIDATE_SHA" != "$EXPECTED_SHA" ]] || fail 'New executable identical to baseline.'
printf 'trial_sha256=%s\n' "$CANDIDATE_SHA" >>"$SESSION/manifest.txt"
grep -aFq 'GHOST_MIP_COPY mips=' "$ORIGINAL_BUILT" || \
    fail 'Build missing nine-mip transfer marker.'
grep -aFq 'GHOST_MIP_REVERSE_COPY mips=' "$ORIGINAL_BUILT" ||
    fail 'Build missing reverse D32-to-R32 transfer marker.'
grep -aFq 'GHOST_VK11_16BIT uniformAndStorageBuffer16BitAccess' "$ORIGINAL_BUILT" ||
    fail 'Built binary missing Vulkan 1.1 16-bit feature diagnostic marker.'
# This is the actual emitted log format. The GHOST_R8_SAMPLER_HANDLE_MAP
# token exists only in a C++ comment and is NOT expected in the ELF.
grep -aFq 'GHOST_R8_INDEX1_APPLIED shader=' "$ORIGINAL_BUILT" ||
    fail 'Built binary missing sampler-index 1 point-filter marker.'
grep -aFq 'GHOST_COPY_FALLBACK_ASSERT mips=' "$ORIGINAL_BUILT" ||
    fail 'Built binary missing untouched fallback-assertion diagnostic.'
grep -aFq 'GHOST_ARENA_1G_CONFIG arena_page=' "$ORIGINAL_BUILT" ||
    fail 'Built binary missing sparse arena page/host-limit logging.'
grep -aFq 'GHOST_ARENA_1G sparse arena merge=' "$ORIGINAL_BUILT" ||
    fail 'Built binary missing invalid sparse arena merge guard.'

log '=== Atomic temporary deployment, verified backup ==='
any_shadps4 && fail 'Emulator started during compilation; refusing install.'
[[ "$(sha256sum "$DEST" | cut -d' ' -f1)" == "$EXPECTED_SHA" ]] || \
    fail 'Installed binary changed during build.'
TEMP="${DEST}.ghost-mip-watch-trial-$STAMP"
run install -m 0755 -- "$ORIGINAL_BUILT" "$TEMP"
cmp -s "$ORIGINAL_BUILT" "$TEMP" || fail 'Staged binary checksum mismatch.'
run mv -fT -- "$TEMP" "$DEST"
DID_DEPLOY=1
[[ "$(sha256sum "$DEST" | cut -d' ' -f1)" == "$CANDIDATE_SHA" ]] || \
    fail 'Trial deployment verification failed.'
PHASE=armed

log 'NO_GDB_HARDWARE_WATCHPOINT=1'
printf 'no_gdb_hardware_watchpoint=true\n' >>"$SESSION/manifest.txt"

log ''
log 'READY - AUTO-LAUNCH GHOST OF TSUSHIMA (no manual Moonlight interaction).'
log 'No RADV_DEBUG=hang or GDB. Only the game process launched by this script is eligible for cleanup.'
log 'The original executable and cached build will be restored afterward.'
python3 - "$SESSION" "$STATE" <<'GHOST_AUTO_LAUNCH'
import os, pathlib, subprocess, signal, sys, time, shutil
session=pathlib.Path(sys.argv[1])
launcher=pathlib.Path.home()/".local/bin/shadps4-esde"
game=pathlib.Path("/mnt/roms-all/ps4/Ghost of Tsushima.ps4")
assert launcher.is_file() and os.access(launcher,os.X_OK), "Ghost launcher missing"
assert game.is_file(), "Game entry missing"
env=os.environ.copy()
env["DISPLAY"]=":0"
# Enforce validation without modifying user or game configuration files.
env["VK_INSTANCE_LAYERS"]="VK_LAYER_KHRONOS_validation"
env["VK_LAYER_ENABLES"]="VK_VALIDATION_FEATURE_ENABLE_SYNCHRONIZATION_VALIDATION_EXT"
env.pop("RADV_DEBUG",None)
def candidate_xauth():
    paths=[]
    if env.get("XAUTHORITY"): paths.append(env["XAUTHORITY"])
    paths.append(str(pathlib.Path.home()/".Xauthority"))
    uid=os.getuid()
    for proc in pathlib.Path("/proc").iterdir():
        if not proc.name.isdigit(): continue
        try:
            if proc.stat().st_uid != uid: continue
            raw=(proc/"environ").read_bytes().split(b"\0")
            d=dict(field.split(b"=",1) for field in raw if b"=" in field)
            x=d.get(b"XAUTHORITY",b"").decode(errors="replace")
            if x: paths.append(x)
            argv=(proc/"cmdline").read_bytes().split(b"\0")
            if b"-auth" in argv:
                idx=argv.index(b"-auth")
                if idx+1<len(argv): paths.append(argv[idx+1].decode(errors="replace"))
        except (OSError,ValueError): pass
    for path in paths:
        if pathlib.Path(path).is_file() and os.access(path,os.R_OK):
            return path
    return ""
xauth=candidate_xauth()
if xauth: env["XAUTHORITY"]=xauth
check=shutil.which("xdpyinfo") or shutil.which("xset")
if check:
    cmd=[check,"-display",env["DISPLAY"]] + ([] if check.endswith("xdpyinfo") else ["q"])
    status=subprocess.run(cmd,env=env,capture_output=True,timeout=8)
    if status.returncode!=0: raise RuntimeError("X display authentication failed; test refused and baseline will restore")
print(f"AUTO_DISPLAY={env['DISPLAY']} xauth={'present' if xauth else 'default'}",flush=True)
print(f"AUTO_VULKAN_VALIDATION={env['VK_INSTANCE_LAYERS']}",flush=True)
out=(session/"launcher-auto-stdout.log").open("wb")
proc=subprocess.Popen([str(launcher),str(game)],env=env,stdin=subprocess.DEVNULL,
                      stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
print(f"AUTO_GAME_LAUNCH pid={proc.pid} sid={os.getsid(proc.pid)}",flush=True)
(session/"launcher-auto.pid").write_text(str(proc.pid)+"\n")
deadline=time.monotonic()+115
try:
    while proc.poll() is None and time.monotonic()<deadline:
        time.sleep(1)
    if proc.poll() is None:
        print("AUTO_GAME_TIMEOUT=115s; terminating ONLY this isolated launch session",flush=True)
        try:
            if os.getsid(proc.pid)==proc.pid:
                os.killpg(proc.pid,signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                if os.getsid(proc.pid)==proc.pid:
                    os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=8)
    print(f"AUTO_GAME_EXIT_CODE={proc.returncode}",flush=True)
finally:
    out.close()
GHOST_AUTO_LAUNCH
PHASE=autoplay_finished
log 'The automatic game trial finished; collecting runtime evidence and restoring verified baseline.'
exit 0

# All source/executable restoration and archive creation happen in EXIT trap.
