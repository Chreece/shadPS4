#!/usr/bin/env bash
# GoT v38: carry the successful GoW 64 KiB read-only-stream policy to the
# 18,020-byte Ghost descriptor table. A single source-level conditional changes.
# Reuse ALL retained v37 fixes and built objects. Never touch GoW, saves or SSH.
set -Eeuo pipefail
umask 077
R="$HOME/projects/shadps4"
P="$R/GhostOfTsushima"
T="$P/tests"
A="$T/got-v31-nptoolkit-mspace-20261013"
B="$T/got-v37-guest-image-safety-20261013"
C="$T/got-v38-64k-readonly-stream-20261013"
L37="$P/scripts/got-v37-descriptor-visual.sh"
L38="$P/scripts/got-v38-64k-visual.sh"
SRC=src/video_core/buffer_cache/buffer_cache.cpp
OBJ="CMakeFiles/shadps4.dir/$SRC.o"
PIN=3b7197f57c2a44048f6365e2568050df7324aa33
V31SHA=1e7264ec822d00060cddf9649fc704217524163e81673d87e5059e64e1375f6f
V37SHA=1a4d7fb9b69a9d2b632298d82472a44b0af81bc912b1058a5fbb3c044c998543
V37VK=02be53c70ac824ca42b1df41af93163f0e4bdfe5b1666f270193f51fd58f155d
V37SRT=5bbdf054553361bda16921a813fc81499994ea62e28340fc90cb5f06034ad1ca
fail() { printf 'STOP: %s\n' "$*" >&2; exit 2; }
sha() { sha256sum "$1" | cut -d ' ' -f1; }
if [[ -f "$L38" && ! -L "$L38" ]]; then
  echo 'V38_ALREADY_STARTED=YES; NOT_REBUILDING_OR_RETESTING'
  bash "$L38" status
  exit 0
fi
[[ ! -e "$L38" && ! -L "$L38" ]] || fail 'unsafe existing v38 launcher'
for x in git ninja python3 sha256sum bash cmp; do
  command -v "$x" >/dev/null || fail "missing $x"
done
for d in "$T" "$P/scripts" "$R/logs" "$A" "$A/build" "$B" "$B/build"; do
  [[ -d "$d" && ! -L "$d" ]] || fail "missing or unsafe directory: $d"
done
for f in "$A/.git" "$B/.git" "$A/build/build.ninja" "$A/build/shadps4" \
         "$B/build/shadps4" "$B/build/link-v37.sh" "$A/$SRC" "$B/$SRC" "$L37"; do
  [[ -f "$f" && ! -L "$f" ]] || fail "missing or unsafe checkpoint: $f"
done
[[ "$(git -C "$A" rev-parse HEAD)" == "$PIN" ]] || fail 'v31 Git head differs'
[[ "$(git -C "$B" rev-parse HEAD)" == "$PIN" ]] || fail 'v37 Git head differs'
[[ "$(sha "$A/build/shadps4")" == "$V31SHA" ]] || fail 'v31 compiled baseline changed'
[[ "$(sha "$B/build/shadps4")" == "$V37SHA" ]] || fail 'v37 compiled baseline changed'
[[ "$(sha "$B/src/video_core/renderer_vulkan/vk_rasterizer.cpp")" == "$V37VK" ]] ||
  fail 'v37 image descriptor fix was modified'
[[ "$(sha "$B/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp")" == "$V37SRT" ]] ||
  fail 'v37 retained SRT fixes were modified'
cmp -s "$A/$SRC" "$B/$SRC" || fail 'base buffer-cache CPP differs from verified v31'
grep -Fq "$V37SHA" "$L37" || fail 'v37 screenshot launcher does not pin the current binary'
[[ "$(df -Pk "$T" | awk 'END{print $4}')" -gt 524288 ]] || fail 'less than 512 MiB free'
# Preserve source parity (v37 rasterizer, v33 SRT, the HLE fix and linked objects)
# in a new standalone worktree. This is not a reset of any existing checkout.
if [[ ! -e "$C" && ! -L "$C" ]]; then
  git -C "$B" worktree add --detach "$C" "$PIN"
  for f in \
    src/video_core/renderer_vulkan/vk_rasterizer.cpp \
    src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp \
    src/shader_recompiler/ir/passes/resource_discover_pass.cpp \
    src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
    src/shader_recompiler/backend/spirv/emit_spirv_image.cpp \
    src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp \
    src/shader_recompiler/resource.h src/shader_recompiler/info.h; do
    cp -p -- "$B/$f" "$C/$f"
  done
  for f in libc_internal_memory.cpp libc_internal_threads.cpp mspace_allocator.h cxa_guard_registry.h; do
    rel="src/core/libraries/libc_internal/$f"
    [[ -f "$B/$rel" ]] || fail "missing historical HLE fix $rel"
    cp -p -- "$B/$rel" "$C/$rel"
  done
else
  [[ -d "$C" && ! -L "$C" && -f "$C/.git" ]] || fail 'unsafe partial v38 worktree'
  [[ "$(git -C "$C" rev-parse HEAD)" == "$PIN" ]] || fail 'partial v38 git head differs'
fi
[[ "$(sha "$C/src/video_core/renderer_vulkan/vk_rasterizer.cpp")" == "$V37VK" ]] ||
  fail 'v38 lost proven v37 Vulkan fix'
[[ "$(sha "$C/src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp")" == "$V37SRT" ]] ||
  fail 'v38 lost proven v33 SRT fix'

# This is exactly the GoW Stage-72-74 read-only streaming correction, scoped
# to one existing conditional in buffer_cache.cpp; no shader or skip changes.
python3 - "$B/$SRC" "$C/$SRC" <<'PY'
from pathlib import Path
import sys
old=Path(sys.argv[1]).read_text()
p=Path(sys.argv[2])
needle='if (!is_written && size <= STREAM_THRESHOLD && !IsRegionGpuModified(device_addr, size)) {'
replacement='''// GoW-confirmed read-only dense streaming: includes 53 * 340 = 18020-byte
    // GoT descriptor tables without permitting GPU-written buffers on this path.
    if (!is_written && size <= 64_KB && !IsRegionGpuModified(device_addr, size)) {'''
if old.count(needle)!=1: raise SystemExit('STOP: source conditional changed unexpectedly')
expected=old.replace(needle,replacement)
current=p.read_text()
if current==old:
    p.write_text(expected)
elif current!=expected:
    raise SystemExit('STOP: partial v38 source differs; refusing overwrite')
print('V38_CODE=GO-W_PROVEN_READONLY_64K_STREAM; V37_BASE=UNCHANGED')
PY
mkdir -p "$C/build"
# Never compile or link into v31 or v37. Redirect the one changed object
# and final executable, while reusing v37 rasterizer and v33 SRT objects.
python3 - "$A" "$B" "$C" "$SRC" "$OBJ" <<'PY'
from pathlib import Path
import re,sys,subprocess
a,b,c,src,obj=sys.argv[1:]
A,B,C=map(Path,(a,b,c))
base=A/'build'
commands=subprocess.run(['ninja','-C',str(base),'-t','commands','shadps4'],
 capture_output=True,text=True,check=True).stdout.splitlines()
candidates=[x for x in commands if 'buffer_cache.cpp' in x and re.search(r'\s-c\s',x)]
if len(candidates)!=1:
 raise SystemExit(f'STOP: expected one buffer-cache compile command; got {len(candidates)}')
cc=candidates[0]
src_old=str(A/src); src_new=str(C/src)
obj_new=str(C/'build'/obj)
if cc.count(src_old)!=1 or obj not in cc:
 raise SystemExit('STOP: unrecognized buffer-cache compile input/object')
cc=cc.replace(src_old,src_new)
cc=cc.replace(obj+'.d','__DEPENDENCY__').replace(obj,'__OBJECT__')
cc=cc.replace('__DEPENDENCY__',obj_new+'.d').replace('__OBJECT__',obj_new)
if src_old in cc or f'-o {obj}' in cc:
 raise SystemExit('STOP: compile would write/read an unexpected path')
old_link=(B/'build/link-v37.sh').read_text()
v37_bin=str(B/'build/shadps4')
v38_bin=str(C/'build/shadps4')
if old_link.count(obj)!=1 or old_link.count(v37_bin)!=1 or \
   '&&' in old_link or ';' in old_link:
 raise SystemExit('STOP: existing v37 linker is ambiguous/unsafe')
new_link=old_link.replace(obj,obj_new).replace(v37_bin,v38_bin)
new_link=new_link.replace(str(B/'build/link.d'),str(C/'build/link.d'))
if obj in new_link and obj_new not in new_link:
 raise SystemExit('STOP: object not redirected in v38 link')
if v37_bin in new_link:
 raise SystemExit('STOP: v38 link would overwrite v37')
for filename, body in [('compile-one.sh','#!/usr/bin/env bash\nset -Eeuo pipefail\n'+cc+'\n'),
                       ('link-v38.sh',new_link)]:
 dest=C/'build'/filename
 if dest.exists() and dest.read_text()!=body:
  raise SystemExit('STOP: unknown existing v38 build command '+filename)
 dest.write_text(body);dest.chmod(0o700)
print('REBUILD=BUFFER_CACHE_CPP_ONLY; RENDERER=V37; SRT=V33')
PY
mkdir -p "$C/build/CMakeFiles/shadps4.dir/$(dirname "$SRC")"
bash -n "$C/build/compile-one.sh" || fail 'one-file compiler command syntax error'
bash -n "$C/build/link-v38.sh" || fail 'v38 linker command syntax error'
PROOF="$C/build/.got38-executable-sha256"
if [[ -e "$C/build/shadps4" || -L "$C/build/shadps4" ]]; then
  [[ -f "$PROOF" && ! -L "$PROOF" && -x "$C/build/shadps4" ]] ||
    fail 'unverified existing v38 executable'
  [[ "$(sha "$C/build/shadps4")" == "$(cat "$PROOF")" ]] ||
    fail 'v38 executable and proof differ'
  echo 'REUSE_EXISTING_VERIFIED_V38_BINARY'
else
  LOG="$R/logs/got38-readonly-stream-$(date +%Y%m%d-%H%M%S).log"
  echo 'STAGE=COMPILE_ONE_BUFFER_CACHE_FILE'
  (cd "$A/build" && bash "$C/build/compile-one.sh") >"$LOG" 2>&1 ||
    { tail -65 "$LOG"; fail "compiler error; upload $LOG"; }
  echo 'STAGE=LINK_RETAINED_V37_GRAPHICS_AND_V33_SRT'
  (cd "$A/build" && bash "$C/build/link-v38.sh") >>"$LOG" 2>&1 ||
    { tail -65 "$LOG"; fail "link error; upload $LOG"; }
  [[ -x "$C/build/shadps4" && ! -L "$C/build/shadps4" ]] ||
    fail 'v38 executable not created'
  [[ "$(sha "$C/build/shadps4")" != "$V37SHA" ]] ||
    fail 'v38 executable identical to v37'
  sha "$C/build/shadps4" > "$PROOF"
  printf 'BUILD_LOG=%s\n' "$LOG"
fi
[[ "$(sha "$A/build/shadps4")" == "$V31SHA" &&
   "$(sha "$B/build/shadps4")" == "$V37SHA" ]] ||
   fail 'preserved baseline changed; launch blocked'
NEW_SHA="$(sha "$C/build/shadps4")"
python3 - "$L37" "$L38" "$V37SHA" "$NEW_SHA" <<'PY'
from pathlib import Path
import sys
original,dest,old_sha,new_sha=sys.argv[1:]
s=Path(original).read_text()
for x in ('got-v37-guest-image-safety-20261013','got-v37-descriptor-visual',old_sha):
 if x not in s:raise SystemExit('STOP: screenshot runner differs: '+x)
s=s.replace('got-v37-guest-image-safety-20261013','got-v38-64k-readonly-stream-20261013')
s=s.replace('got-v37-descriptor-visual','got-v38-64k-visual')
s=s.replace(old_sha,new_sha)
p=Path(dest)
if p.exists() or p.is_symlink():raise SystemExit('STOP: refusing to overwrite existing v38 runner')
p.write_text(s);p.chmod(0o700)
PY
bash -n "$L38" || fail 'new visual runner syntax invalid'
printf 'V38_SHA256=%s\nV31_V33_V37_PRESERVED=YES\n' "$NEW_SHA"
echo 'STAGE=ONE_DETACHED_SCREENSHOT_TEST'
bash "$L38" start
printf 'GET_REPORT=bash %s status\n' "$L38"
