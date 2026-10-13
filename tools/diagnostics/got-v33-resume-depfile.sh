#!/usr/bin/env bash
# Continue a failed GoT v33 one-object compile after creating -MF depfile parents.
# Does not rebuild or overwrite v31/v32, GoW, saves, Sunshine or SSH.
set -Eeuo pipefail
umask 077
R="$HOME/projects/shadps4"
P="$R/GhostOfTsushima"
A="$P/tests/got-v31-nptoolkit-mspace-20261013"
B="$P/tests/got-v32-srt-jit-stackfix-20261013"
C="$P/tests/got-v33-gow-srt-correctness-20261013"
SRC="src/shader_recompiler/ir/passes/flatten_extended_userdata_pass.cpp"
L32="$P/scripts/got-v32-srt-visual.sh"
L33="$P/scripts/got-v33-gow-srt-visual.sh"
AHASH="1e7264ec822d00060cddf9649fc704217524163e81673d87e5059e64e1375f6f"
PIN="3b7197f57c2a44048f6365e2568050df7324aa33"
fail() { echo "STOP: $*" >&2; exit 2; }
hash() { sha256sum "$1" | cut -d' ' -f1; }
for x in "$A" "$B" "$C" "$C/build"; do
  [[ -d "$x" && ! -L "$x" && -f "$x/.git" ]] || fail "missing or unsafe worktree $x"
done
for x in "$A/build/shadps4" "$B/build/shadps4" "$B/$SRC" "$C/$SRC" \
 "$C/build/compile-one.sh" "$C/build/link-v33.sh" "$B/build/compile-one.sh" \
 "$B/build/link-v32.sh" "$L32"; do
  [[ -f "$x" && ! -L "$x" ]] || fail "missing or unsafe input $x"
done
[[ ! -e "$L33" && ! -L "$L33" ]] || fail "v33 launcher already present; check its status"
[[ ! -e "$C/build/shadps4" && ! -L "$C/build/shadps4" ]] ||
 fail "v33 binary already exists: do not overwrite"
for x in "$A" "$B" "$C"; do
 [[ "$(git -C "$x" rev-parse HEAD)" == "$PIN" ]] || fail "unexpected source HEAD in $x"
done
[[ "$(hash "$A/build/shadps4")" == "$AHASH" ]] || fail "v31 executable hash changed"
git -C "$A" diff --quiet -- "$SRC" || fail "v31 SRT source has changed"
BHASH="$(hash "$B/build/shadps4")"
[[ "$BHASH" != "$AHASH" ]] || fail "v32 binary is not patched"
grep -Fq "$BHASH" "$L32" || fail "v32 screenshot launcher hash mismatch"
BSOURCE="$(hash "$B/$SRC")"
CSOURCE="$(hash "$C/$SRC")"
for f in libc_internal_memory.cpp libc_internal_threads.cpp mspace_allocator.h cxa_guard_registry.h; do
 cmp -s "$A/src/core/libraries/libc_internal/$f" \
         "$C/src/core/libraries/libc_internal/$f" ||
 fail "v31 HLE fix not preserved in v33: $f"
done
python3 - "$B" "$C" "$SRC" <<'PY'
from pathlib import Path
import sys
B,C,src=sys.argv[1:]
b,c=Path(B),Path(C)
s=(b/src).read_text()
patches=[
('shift',
'''        c.shl(reg, inst->Arg(0).U32());
        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.shl(reg, cl);''',
'''        // Preserve the dynamic shift count; discard no value operand.
        c.mov(ecx, reg);
        c.mov(reg, inst->Arg(0).U32());
        c.shl(reg, cl);'''),
('mask-immediate',
'''        c.and_(reg, (1U << inst->Arg(2).U32()) - 1);''',
'''        const u32 bits = inst->Arg(2).U32();
        // A full 32-bit extract must not perform an undefined 1u << 32.
        if (bits < 32) {
            c.and_(reg, bits == 0 ? 0u : (1u << bits) - 1u);
        }'''),
('mask-dynamic',
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
        c.pop(reg.cvt64());'''),
]
if s.count('const size_t checkpoint = c.getSize()') < 2:
 raise SystemExit('STOP: v32 JIT rollback missing')
for title,old,new in patches:
 if s.count(old)!=1: raise SystemExit('STOP: v32 source mismatch '+title)
 s=s.replace(old,new)
if s!=(c/src).read_text():
 raise SystemExit('STOP: partial v33 source differs from expected cumulative fixes')
for original,relinked in [('compile-one.sh','compile-one.sh'),
                           ('link-v32.sh','link-v33.sh')]:
 cmd=(b/'build'/original).read_text()
 if B not in cmd: raise SystemExit('STOP: v32 source in build command missing')
 if cmd.replace(B,C)!=(c/'build'/relinked).read_text():
  raise SystemExit('STOP: existing compile/link commands not verified')
obj=str(c/'build'/('CMakeFiles/shadps4.dir/'+src+'.o'))
cc=(c/'build/compile-one.sh').read_text()
ld=(c/'build/link-v33.sh').read_text()
if not all(v in cc for v in (str(c/src),obj,obj+'.d')):
 raise SystemExit('STOP: compiler output/depfile not redirected into v33')
if obj not in ld or str(c/'build/shadps4') not in ld:
 raise SystemExit('STOP: linker output not redirected into v33')
print('SOURCE_PATCHES=VERIFIED; COMPILE_AND_LINK_OUTPUTS=ISOLATED')
PY
# GCC/Clang do not create directories containing their generated .d file.
mkdir -p -- "$C/build/CMakeFiles/shadps4.dir/$(dirname "$SRC")"
bash -n "$C/build/compile-one.sh" || fail "invalid compile command"
bash -n "$C/build/link-v33.sh" || fail "invalid link command"
LOG="$R/logs/got-v33-resumed-$(date +%Y%m%d-%H%M%S).log"
printf 'REPAIR=DEPENDENCY_PARENT_CREATED; ACTION=COMPILE_ONE_OBJECT\n'
(cd "$A/build" && bash "$C/build/compile-one.sh") >"$LOG" 2>&1 ||
 { tail -45 "$LOG"; fail "one-object compile failed: $LOG"; }
(cd "$A/build" && bash "$C/build/link-v33.sh") >>"$LOG" 2>&1 ||
 { tail -45 "$LOG"; fail "link failed: $LOG"; }
[[ -x "$C/build/shadps4" && ! -L "$C/build/shadps4" ]] ||
 fail "v33 executable missing"
CHASH="$(hash "$C/build/shadps4")"
[[ "$CHASH" != "$BHASH" ]] || fail "v33 identical to v32"
[[ "$(hash "$A/build/shadps4")" == "$AHASH" &&
 "$(hash "$B/build/shadps4")" == "$BHASH" &&
 "$(hash "$B/$SRC")" == "$BSOURCE" &&
 "$(hash "$C/$SRC")" == "$CSOURCE" ]] ||
 fail "checkpoint changed: gameplay test blocked"
python3 - "$L32" "$L33" "$BHASH" "$CHASH" <<'PY'
from pathlib import Path
import sys
a,b,oldsha,newsha=sys.argv[1:]
s=Path(a).read_text()
for x in ('got-v32-srt-jit-stackfix-20261013','got-v32-srt-visual',oldsha):
 if x not in s: raise SystemExit('STOP: visual launcher source differs')
s=s.replace('got-v32-srt-jit-stackfix-20261013','got-v33-gow-srt-correctness-20261013')
s=s.replace('got-v32-srt-visual','got-v33-gow-srt-visual')
s=s.replace(oldsha,newsha)
p=Path(b)
if p.exists() or p.is_symlink(): raise SystemExit('STOP: launcher already exists')
p.write_text(s)
p.chmod(0o700)
PY
bash -n "$L33" || fail "visual launcher syntax invalid"
printf 'BUILD=PASS; V31_V32_PRESERVED=YES\n'
printf 'STAGE=DETACHED_VISUAL_GAMEPLAY_CHECK\n'
bash "$L33" start
printf 'ARCHIVE_STATUS_COMMAND=bash %s status\n' "$L33"
