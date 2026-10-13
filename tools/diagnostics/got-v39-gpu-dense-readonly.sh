#!/usr/bin/env bash
# GoT v39: GPU-to-GPU dense copy of the read-only 18,020-byte descriptor buffer.
# Carries forward v33 SRT + v37 image fixes + v38 buffer-cache build, untouched.
# No full rebuild, shader skip, SSH process manipulation, GoW changes or save writes.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
PROJ="$ROOT/GhostOfTsushima"
TESTS="$PROJ/tests"
V31="$TESTS/got-v31-nptoolkit-mspace-20261013"
V37="$TESTS/got-v37-guest-image-safety-20261013"
V38="$TESTS/got-v38-64k-readonly-stream-20261013"
V39="$TESTS/got-v39-gpu-dense-readonly-20261013"
VK="src/video_core/renderer_vulkan/vk_rasterizer.cpp"
CACHE="src/video_core/buffer_cache/buffer_cache.cpp"
SRT="src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
L38="$PROJ/scripts/got-v38-64k-visual.sh"
L39="$PROJ/scripts/got-v39-dense-gpu-visual.sh"
PIN="3b7197f57c2a44048f6365e2568050df7324aa33"
SHA31="1e7264ec822d00060cddf9649fc704217524163e81673d87e5059e64e1375f6f"
SHA37="1a4d7fb9b69a9d2b632298d82472a44b0af81bc912b1058a5fbb3c044c998543"
SHA_VK_BASE="02be53c70ac824ca42b1df41af93163f0e4bdfe5b1666f270193f51fd58f155d"
SHA_VK_NEW="36d3396badb26d2804218203c5c4f27decba22eb5d544ea42a8a4801c4c7d927"
SHA_PATCH="b2b51c0041bf43f34c550b547c6298f1131dd2fbcb8535b3845c07f3baeadf20"
fail() { printf 'STOP: %s\n' "$*" >&2; exit 2; }
hash() { sha256sum "$1" | cut -d' ' -f1; }

if [[ -f "$L39" && ! -L "$L39" ]]; then
    printf 'PREVIOUS_V39=FOUND; NO_REBUILD_OR_REPEAT_RUN\n'
    bash "$L39" status
    exit 0
fi
[[ ! -e "$L39" && ! -L "$L39" ]] || fail "unsafe existing v39 visual launcher"
for x in bash python3 git ninja sha256sum base64 gzip cmp; do
    command -v "$x" >/dev/null || fail "missing $x"
done
for x in "$TESTS" "$ROOT/logs" "$PROJ/scripts" "$V31" "$V31/build" "$V37" "$V37/build" "$V38" "$V38/build"; do
    [[ -d "$x" && ! -L "$x" ]] || fail "missing or unsafe build directory: $x"
done
for x in "$V31/.git" "$V37/.git" "$V38/.git" \
         "$V31/build/shadps4" "$V31/build/build.ninja" \
         "$V37/build/shadps4" "$V37/build/compile-one.sh" \
         "$V38/build/shadps4" "$V38/build/link-v38.sh" \
         "$V37/$VK" "$V38/$VK" "$V38/$CACHE" "$V38/$SRT" "$L38"; do
    [[ -f "$x" && ! -L "$x" ]] || fail "missing or unsafe preserved input: $x"
done
for x in "$V31" "$V37" "$V38"; do
    [[ "$(git -C "$x" rev-parse HEAD)" == "$PIN" ]] || fail "Git commit differs in $x"
done
[[ "$(hash "$V31/build/shadps4")" == "$SHA31" ]] || fail "v31 binary changed"
[[ "$(hash "$V37/build/shadps4")" == "$SHA37" ]] || fail "v37 binary changed"
[[ "$(hash "$V37/$VK")" == "$SHA_VK_BASE" && "$(hash "$V38/$VK")" == "$SHA_VK_BASE" ]] ||
    fail "v37/v38 Vulkan code differs from pinned source"
[[ "$(hash "$V37/$SRT")" == "$(hash "$V38/$SRT")" ]] ||
    fail "v38 SRT source differs from v37"
grep -Fq 'size <= 64_KB && !IsRegionGpuModified' "$V38/$CACHE" ||
    fail "v38 GoW streaming source missing"
SHA38="$(hash "$V38/build/shadps4")"
[[ "$SHA38" != "$SHA37" ]] || fail "v38 executable unchanged from v37"
grep -Fq "$SHA38" "$L38" || fail "v38 screenshot launcher does not pin its binary"
[[ "$(df -Pk "$TESTS" | awk 'END {print $4}')" -gt 524288 ]] ||
    fail "less than 512 MiB available"
printf 'SOURCE=PINNED; V31_V37_V38=VERIFIED\n'

# Use a new worktree and copy forward the exact local modified source files.
if [[ ! -e "$V39" && ! -L "$V39" ]]; then
    git -C "$V38" worktree add --detach "$V39" "$PIN"
    for p in \
        "$VK" "$CACHE" "$SRT" \
        src/shader_recompiler/ir/passes/resource_discover_pass.cpp \
        src/shader_recompiler/ir/passes/resource_patching_pass.cpp \
        src/shader_recompiler/backend/spirv/emit_spirv_image.cpp \
        src/shader_recompiler/backend/spirv/emit_spirv_context_get_set.cpp \
        src/shader_recompiler/resource.h src/shader_recompiler/info.h; do
        [[ -f "$V38/$p" && ! -L "$V38/$p" ]] || fail "missing carried source $p"
        cp -p -- "$V38/$p" "$V39/$p"
    done
    for f in libc_internal_memory.cpp libc_internal_threads.cpp mspace_allocator.h cxa_guard_registry.h; do
        p="src/core/libraries/libc_internal/$f"
        [[ -s "$V38/$p" ]] || fail "missing prior HLE fix $p"
        cp -p -- "$V38/$p" "$V39/$p"
    done
else
    [[ -d "$V39" && ! -L "$V39" && -f "$V39/.git" ]] ||
        fail "unsafe partial v39 worktree"
    [[ "$(git -C "$V39" rev-parse HEAD)" == "$PIN" ]] ||
        fail "unexpected v39 commit"
fi
for p in "$CACHE" "$SRT" src/shader_recompiler/resource.h src/shader_recompiler/info.h; do
    cmp -s -- "$V38/$p" "$V39/$p" || fail "v39 lost v38 carried source: $p"
done
mkdir -p -- "$V39/build"
PATCH="$V39/build/got39-gpu-dense.patch"
if [[ ! -e "$PATCH" && ! -L "$PATCH" ]]; then
    base64 -d <<'PATCH64' | gzip -dc > "$PATCH"
H4sIAAAAAAAC/7VWbXPiNhD+nl+xx01vyGFD3iah7pFJjqRM5nKBIcl1Op2OR9gCdLElV5IJlOG/d2WZQBJDoNfqA4lk76N9eZ5d
u64LpKZkUBuxkAo/EJLWJOUhlVT6ozR6ILw2evAlUZpK9jeV1SBJdiqVCvS2Nzs7A/fg0DmGCv6ewNnZDrxnPIjSkEJpY6RhaQuz
lGsW061sVDCkYRrZmyobW2kyYHzg99J+Hx8kQkTb3TokeOQPozXOajrWqaR+QNDFGovJgPojRh83tXi2M0amIPWDE6cOlfpB3Tmy
NSla1+2Wf9nttrvlbua7A6VmROKEhqCwKtCXIobpDLQwv30hwSSEwtR7P56VnGLQxRph+DKptqi+RbTyrpOhOhakmgxif0jUcPeX
1zizHfflUSC40kBS9OUPWw4HRL+vqP4TGpAXyOag3dOE8c/ZUXmn8hLpX2CsC69HFPVJGEqq1DzCkKqgypT/KJnWlC8OMIcxwaMQ
o37lWK0GLXEHdJygKGLKtQd6SGG/7uwd7Lm9iaYZjmSJxlJo0oso5pJFEfQYDxUQUAmRihYi2+gMQMIQyAAfH7lf2GcYCqVdpSUl
MR5jGEMRhVX4Fe8ggKehK3g0AcvlQmy0EakMMPCYSWl8Q/RW594dMcWMl8Z3BR8Fnz/5CIxjFQj6w5UJa8QC6haCRyIgEeRKBBKZ
vWaCV+GGjjAilfYwCTrVJhkEL2t27mGQUmRLTGMhJ9VC2K5tIp7XFMnE1hnUhAdDKTjWUGWezsMiPMz2dMzwLuNGEGC5C4G1JMGD
eadHI/EIyAxTGTzlCu9wDSUouKd5Pl2TYIw/MQLkQZGzrA/l55KBRgP2xnV6cnQY1On99TV8+GAViw/268iWXZi+BlrIqIeNDAZJ
6sciZH2Gem8Uv27WM11cqS4dYPZbSfo1ty2vFEIRyc3CaDQL8E/oeUSLmAWf0sODUxgIffizH4nBgIbTvdkKa5OPdy8UtjLeJ8Fb
ojVgPjxMV7Kc6mAyyrvVLv3LkKa8GidzPdP3N9OMm9iLPe9rxrG7SYL/X2Q0vjaERc5EbMCNildlYVGO0YPnWQoaMuJhMpmud6OK
Y7qddS4MybYw5w2LUOkniywZ1c3sLK9s4Ktfna0Jcp7yhdLK8+5rHZnvMkJgD+PTDyYHDuzP1iXPEGGZMtU+1cHQULC8n4PZDuAL
acawpBEZY+uFT1BfS5j5bPztvHtzddPKp6P/LZvrb+RreZVa7Tv0Dvudf3F5c3vpN9ud33PhN7IhmnVu5GAD52tpC+CsoRqjrNHl
WMuC3hYQ+eSjfY5kdpYdBgeZs/TM7J6elbbIxvMe5sxDr6Y889h5i2Ibzd/lHGyBZRnonlp6Wh2fW0zz3bKRUpbXMq9Xoi6rcB3P
Z2/15idF2+0aKFHUAVa8PwMaYcv831T2owp7pa7bL1edzuXFjwqsWFw4p5XgjfyLPiA8Gz10UwH8l+QvIP4q+hRQZ7azYgqlx0c5
P/xsdpmvAmiKOBbc887NyYV45OVcC8/m2xpEEn5P1WJKgfvijgJjw7jc7Cc4gnf4sWOI9A8cRDoQ1w4AAA==
PATCH64
fi
[[ -f "$PATCH" && ! -L "$PATCH" && "$(hash "$PATCH")" == "$SHA_PATCH" ]] ||
    fail "GPU dense patch integrity failed"
if [[ "$(hash "$V39/$VK")" == "$SHA_VK_BASE" ]]; then
    (cd "$V39" && git apply --check "$PATCH" && git apply "$PATCH") ||
        fail "exact source patch failed"
elif [[ "$(hash "$V39/$VK")" == "$SHA_VK_NEW" ]]; then
    printf 'PATCH_ALREADY_APPLIED=VERIFIED\n'
else
    fail "v39 Vulkan source is neither unmodified baseline nor expected patch"
fi
[[ "$(hash "$V39/$VK")" == "$SHA_VK_NEW" ]] ||
    fail "v39 patched source SHA mismatch"

# Reuse v37's already proven one-TU compile and v38's full link line.
# Redirect rasterizer compilation/link ONLY into v39. The v38 buffer-cache
# object and all original SRT/NP/graphics dependencies remain linked unchanged.
python3 - "$V37" "$V38" "$V39" "$VK" <<'PY'
from pathlib import Path
import sys
d,b,c,vk = map(Path,sys.argv[1:])
obj = Path('CMakeFiles/shadps4.dir') / vk
src37 = str(d / vk)
src39 = str(c / vk)
obj37 = str(d / 'build' / obj)
obj39 = str(c / 'build' / obj)
out38 = str(b / 'build' / 'shadps4')
out39 = str(c / 'build' / 'shadps4')
cc = (d / 'build' / 'compile-one.sh').read_text()
if cc.count(src37)!=1 or cc.count(obj37)<1:
    raise SystemExit('STOP: v37 compile source/object cannot be uniquely identified')
cc = cc.replace(str(d),str(c))
if src37 in cc or obj37 in cc or src39 not in cc or obj39 not in cc:
    raise SystemExit('STOP: v39 compile source/object not redirected')
ld = (b / 'build' / 'link-v38.sh').read_text()
if ld.count(obj37)!=1 or ld.count(out38)!=1 or ' && ' in ld or ';' in ld:
    raise SystemExit('STOP: unexpected v38 link structure')
ld = ld.replace(obj37,obj39).replace(out38,out39)
ld = ld.replace(str(b/'build/link.d'),str(c/'build/link.d'))
if obj37 in ld or out38 in ld or obj39 not in ld or out39 not in ld:
    raise SystemExit('STOP: v39 linker still targets an old path')
for name,body in [('compile-one.sh',cc),('link-v39.sh',ld)]:
    dst=c/'build'/name
    if dst.exists() and dst.read_text()!=body:
        raise SystemExit('STOP: previous v39 build command differs: '+name)
    dst.write_text(body)
    dst.chmod(0o700)
print('BUILD_MODE=ONE_RASTERIZER_TU; ORIGINAL_V38_OBJECTS_UNCHANGED')
PY
mkdir -p -- "$V39/build/CMakeFiles/shadps4.dir/$(dirname "$VK")"
bash -n "$V39/build/compile-one.sh" || fail "compiler script syntax invalid"
bash -n "$V39/build/link-v39.sh" || fail "link script syntax invalid"
PROOF="$V39/build/.got39-binary-sha256"
if [[ -e "$V39/build/shadps4" || -L "$V39/build/shadps4" ]]; then
    [[ -f "$PROOF" && ! -L "$PROOF" && -x "$V39/build/shadps4" ]] ||
        fail "unknown pre-existing v39 binary"
    [[ "$(hash "$V39/build/shadps4")" == "$(cat "$PROOF")" ]] ||
        fail "v39 binary/proof mismatch"
    printf 'REUSE_PREVIOUSLY_VERIFIED_V39_BINARY\n'
else
    BUILD_LOG="$ROOT/logs/got39-dense-$(date +%Y%m%d-%H%M%S).log"
    printf 'STAGE=COMPILE_ONE_VULKAN_SOURCE\n'
    (cd "$V31/build" && bash "$V39/build/compile-one.sh") >"$BUILD_LOG" 2>&1 ||
        { tail -60 "$BUILD_LOG"; fail "compile failure; upload $BUILD_LOG"; }
    printf 'STAGE=RELINK_V38_OBJECTS_PLUS_V39_RENDERER\n'
    (cd "$V31/build" && bash "$V39/build/link-v39.sh") >>"$BUILD_LOG" 2>&1 ||
        { tail -60 "$BUILD_LOG"; fail "link failure; upload $BUILD_LOG"; }
    [[ -x "$V39/build/shadps4" && ! -L "$V39/build/shadps4" ]] ||
        fail "v39 executable missing"
    [[ "$(hash "$V39/build/shadps4")" != "$SHA38" ]] ||
        fail "v39 binary unexpectedly identical to v38"
    hash "$V39/build/shadps4" > "$PROOF"
    printf 'BUILD_LOG=%s\n' "$BUILD_LOG"
fi
[[ "$(hash "$V31/build/shadps4")" == "$SHA31" &&
   "$(hash "$V37/build/shadps4")" == "$SHA37" &&
   "$(hash "$V38/build/shadps4")" == "$SHA38" &&
   "$(hash "$V38/$VK")" == "$SHA_VK_BASE" ]] ||
    fail "prior checkpoint mutated; launch blocked"
NEW_SHA="$(hash "$V39/build/shadps4")"
python3 - "$L38" "$L39" "$SHA38" "$NEW_SHA" <<'PY'
from pathlib import Path
import sys
a,b,oldsha,newsha=sys.argv[1:]
s=Path(a).read_text()
for token in ('got-v38-64k-readonly-stream-20261013','got-v38-64k-visual',oldsha):
    if token not in s:
        raise SystemExit('STOP: v38 screenshot launcher changed: '+token)
s=s.replace('got-v38-64k-readonly-stream-20261013',
            'got-v39-gpu-dense-readonly-20261013')
s=s.replace('got-v38-64k-visual','got-v39-dense-gpu-visual')
s=s.replace(oldsha,newsha)
dest=Path(b)
if dest.exists() or dest.is_symlink():
    raise SystemExit('STOP: would overwrite existing v39 launcher')
dest.write_text(s)
dest.chmod(0o700)
PY
bash -n "$L39" || fail "v39 screenshot launcher syntax invalid"
printf 'BUILD=PASS; V31_V33_V37_V38_PRESERVED=YES\nV39_SHA256=%s\n' "$NEW_SHA"
printf 'TARGET=18,020_BYTE_READONLY_GPU_DENSE_COPY; SHADER_UNCHANGED=YES\n'
printf 'STAGE=ONE_DETACHED_VISUAL_TEST\n'
bash "$L39" start
printf 'REPORT_STATUS=bash %s status\n' "$L39"
