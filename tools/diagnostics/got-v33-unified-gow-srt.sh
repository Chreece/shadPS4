#!/usr/bin/env bash
# GoT v33: v29 game fixes + v31 NP HLE + v32 SRT rollback + GoW offset fixes.
# Does NOT write to existing checkpoints, saves, SSH, Sunshine, ES-DE or GoW.
set -Eeuo pipefail
umask 077
ROOT="$HOME/projects/shadps4"
P="$ROOT/GhostOfTsushima"
T="$P/tests"
LOGS="$ROOT/logs"
A="$T/got-v31-nptoolkit-mspace-20261013"
B="$T/got-v32-srt-jit-stackfix-20261013"
C="$T/got-v33-gow-srt-correctness-20261013"
OLD="$P/scripts/got-v32-srt-visual.sh"
NEW="$P/scripts/got-v33-gow-srt-visual.sh"
SRC="src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
PIN="3b7197f57c2a44048f6365e2568050df7324aa33"
AHASH="1e7264ec822d00060cddf9649fc704217524163e81673d87e5059e64e1375f6f"
fail() { printf 'STOP: %s\n' "$*" >&2; exit 2; }
hash() { sha256sum "$1" | cut -d' ' -f1; }
for cmd in git python3 bash sha256sum df; do command -v "$cmd" >/dev/null || fail "missing $cmd"; done
for f in "$A/.git" "$B/.git" "$A/build/build.ninja" "$B/$SRC" \
         "$B/build/compile-one.sh" "$B/build/link-v32.sh" "$OLD"; do
    [[ -f "$f" && ! -L "$f" ]] || fail "missing/unsafe file $f"
done
[[ -x "$A/build/shadps4" && -x "$B/build/shadps4" ]] || fail "missing v31 or v32 executable"
[[ "$(git -C "$A" rev-parse HEAD)" == "$PIN" ]] || fail "v31 git head differs"
[[ "$(git -C "$B" rev-parse HEAD)" == "$PIN" ]] || fail "v32 git head differs"
[[ "$(hash "$A/build/shadps4")" == "$AHASH" ]] || fail "v31 binary SHA mismatch"
git -C "$A" diff --quiet -- "$SRC" || fail "v31 shader source unexpectedly modified"
[[ ! -e "$C" && ! -L "$C" && ! -e "$NEW" && ! -L "$NEW" ]] || fail "v33 already exists; refusing overwrite"
[[ "$(df -Pk "$T" | awk 'END {print $4}')" -gt 524288 ]] || fail "less than 512MiB free"
for f in libc_internal_memory.cpp libc_internal_threads.cpp mspace_allocator.h cxa_guard_registry.h; do
    [[ -s "$A/src/core/libraries/libc_internal/$f" ]] || fail "v31 HLE file missing $f"
done
mkdir -p "$LOGS"
STAMP="$(date +%Y%m%d-%H%M%S)"
BUILDLOG="$LOGS/got-v33-compile-$STAMP.log"
BHASH="$(hash "$B/build/shadps4")"
BSOURCE="$(hash "$B/$SRC")"
[[ "$BHASH" != "$AHASH" ]] || fail "v32 executable unexpectedly unchanged"
printf 'CUMULATIVE_BASE=v31+v32; v29 fixes retained\n'
git -C "$A" worktree add --detach "$C" "$PIN"
for f in libc_internal_memory.cpp libc_internal_threads.cpp mspace_allocator.h cxa_guard_registry.h; do
    cp -p "$A/src/core/libraries/libc_internal/$f" "$C/src/core/libraries/libc_internal/$f"
done
cp -p "$B/$SRC" "$C/$SRC"
python3 - "$C/$SRC" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text()
ops=[
('shift',
'''        c.shl(reg, inst->Arg(0).U32());
        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.shl(reg, cl);''',
'''        // Preserve the dynamic shift count; discard no value operand.
        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.shl(reg, cl);'''),
('constant-mask',
'''        c.and_(reg, (1U << inst->Arg(2).U32()) - 1);''',
'''        const u32 bits = inst->Arg(2).U32();
        // A full 32-bit extract must not perform an undefined 1u << 32.
        if (bits < 32) {
            c.and_(reg, bits == 0 ? 0u : (1u << bits) - 1u);
        }'''),
('dynamic-mask',
'''        c.mov(ecx, reg);
        c.mov(edx, 1);
        c.shl(edx, cl);
        c.dec(edx);
        c.and_(dword[rsp], edx);
        c.pop(reg.cvt64());''',
'''        c.mov(ecx, reg);
        // x86 masks shift counts mod 32: explicitly handle 32+ as full width.
        Xbyak::Label full_width, mask_ready;
        c.cmp(ecx, 32);
        c.jae(full_width);
        c.mov(edx, 1);
        c.shl(edx, cl);
        c.dec(edx);
        c.jmp(mask_ready);
        c.L(full_width);
        c.mov(edx, 0xFFFFFFFFu);
        c.L(mask_ready);
        c.and_(dword[rsp], edx);
        c.pop(reg.cvt64());''')]
if s.count('const size_t checkpoint = c.getSize()') < 2:
    raise SystemExit('STOP: verified v32 JIT stack rollback absent')
for name,a,b in ops:
    n=s.count(a)
    if n!=1:raise SystemExit(f'STOP: {name} port not safe; {n} matches')
    s=s.replace(a,b)
p.write_text(s)
print('SRT_PORT=3; V32_ROLLBACK=RETAINED')
PY
mkdir -p "$C/build"
python3 - "$B" "$C" "$SRC" <<'PY'
from pathlib import Path
import sys
b,c,src=map(Path,sys.argv[1:])
for before,after in [('compile-one.sh','compile-one.sh'),('link-v32.sh','link-v33.sh')]:
    s=(b/'build'/before).read_text()
    if str(b) not in s: raise SystemExit('STOP: v32 build command unexpected')
    s=s.replace(str(b),str(c))
    if str(b) in s: raise SystemExit('STOP: previous outputs still referenced')
    expected=str(c/src) if before.startswith('compile') else str(c/'build/shadps4')
    if expected not in s: raise SystemExit('STOP: target not redirected: '+expected)
    f=c/'build'/after;f.write_text(s);f.chmod(0o700)
PY
bash -n "$C/build/compile-one.sh" || fail "compile command syntax"
bash -n "$C/build/link-v33.sh" || fail "link command syntax"
printf 'STAGE=COMPILE_ONE_TU_AND_RELINK_V33\n'
(cd "$A/build" && bash "$C/build/compile-one.sh") >"$BUILDLOG" 2>&1 ||
    { tail -50 "$BUILDLOG"; fail "shader compile failed: $BUILDLOG"; }
(cd "$A/build" && bash "$C/build/link-v33.sh") >>"$BUILDLOG" 2>&1 ||
    { tail -50 "$BUILDLOG"; fail "isolated link failed: $BUILDLOG"; }
[[ -x "$C/build/shadps4" ]] || fail "v33 executable missing"
CHASH="$(hash "$C/build/shadps4")"
[[ "$CHASH" != "$BHASH" ]] || fail "v33 binary unchanged"
[[ "$(hash "$A/build/shadps4")" == "$AHASH" ]] || fail "v31 binary changed"
[[ "$(hash "$B/build/shadps4")" == "$BHASH" ]] || fail "v32 binary changed"
[[ "$(hash "$B/$SRC")" == "$BSOURCE" ]] || fail "v32 source changed"
git -C "$A" diff --quiet -- "$SRC" || fail "v31 shader source changed"
python3 - "$OLD" "$NEW" "$BHASH" "$CHASH" <<'PY'
from pathlib import Path
import sys
before,after,oldsha,newsha=sys.argv[1:]
s=Path(before).read_text()
for x in ('got-v32-srt-jit-stackfix-20261013','got-v32-srt-visual',oldsha):
    if x not in s: raise SystemExit('STOP: v32 screenshot runner differs: '+x)
s=s.replace('got-v32-srt-jit-stackfix-20261013','got-v33-gow-srt-correctness-20261013')
s=s.replace('got-v32-srt-visual','got-v33-gow-srt-visual')
s=s.replace(oldsha,newsha)
f=Path(after);f.write_text(s);f.chmod(0o700)
PY
bash -n "$NEW" || fail "v33 screenshot runner syntax invalid"
printf 'BUILD=PASS\nOLD_BINARIES=PRESERVED\nV33_SHA256=%s\nBUILDLOG=%s\n' "$CHASH" "$BUILDLOG"
printf 'STAGE=ONE_DETACHED_VISUAL_TEST\n'
bash "$NEW" start
printf 'TO_RETRIEVE_SCREENSHOT_ARCHIVE=bash %s status\n' "$NEW"
